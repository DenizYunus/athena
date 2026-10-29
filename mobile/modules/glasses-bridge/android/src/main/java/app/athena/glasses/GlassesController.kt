package app.athena.glasses

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.net.Uri
import android.os.SystemClock
import android.util.Base64
import android.util.Log
import androidx.exifinterface.media.ExifInterface
import com.meta.wearable.dat.camera.Camera
import com.meta.wearable.dat.camera.Stream
import com.meta.wearable.dat.camera.addCamera
import com.meta.wearable.dat.camera.types.PhotoData
import com.meta.wearable.dat.camera.types.StreamConfiguration
import com.meta.wearable.dat.camera.types.StreamError
import com.meta.wearable.dat.camera.types.StreamState
import com.meta.wearable.dat.camera.types.VideoFrame
import com.meta.wearable.dat.camera.types.VideoQuality
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.core.types.DatError
import com.meta.wearable.dat.core.types.DatResult
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import com.meta.wearable.dat.core.types.RegistrationState
import com.meta.wearable.dat.display.Display
import com.meta.wearable.dat.display.addDisplay
import com.meta.wearable.dat.display.types.DisplayState
import com.meta.wearable.dat.display.views.CornerRadius
import com.meta.wearable.dat.display.views.FlexBoxBackground
import com.meta.wearable.dat.display.views.ImageSize
import com.meta.wearable.dat.display.views.TextColor
import com.meta.wearable.dat.display.views.TextStyle
import com.meta.wearable.dat.mockdevice.MockDeviceKit
import com.meta.wearable.dat.mockdevice.api.GlassesModel
import com.meta.wearable.dat.mockdevice.api.MockDeviceKitConfig
import com.meta.wearable.dat.mockdevice.api.MockDeviceKitInterface
import com.meta.wearable.dat.mockdevice.api.MockGlasses
import com.meta.wearable.dat.mockdevice.api.camera.CameraFacing
import expo.modules.kotlin.AppContext
import expo.modules.kotlin.activityresult.AppContextActivityResultLauncher
import expo.modules.kotlin.exception.Exceptions
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelChildren
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * The Device Access Toolkit session for one pair of glasses (real or from the Mock Device Kit):
 * register with Meta AI, open a DeviceSession, add the camera and stream 720x1280 frames, take
 * photos, and push cards to a display when the glasses have one.
 *
 * States reported to JS: idle, connecting, registering, permission, connected, streaming, paused,
 * disconnected, error.
 */
class GlassesController(
  private val appContext: AppContext,
  private val emit: (name: String, body: Map<String, Any?>) -> Unit,
) {
  companion object {
    const val SDK_VERSION = "1.0.1"
    private const val TAG = "AthenaGlasses"
    private const val CONNECT_TIMEOUT_MS = 30_000L
    /** How long to wait for a linked pair of glasses (or a freshly paired mock) before giving up. */
    private const val DEVICE_TIMEOUT_MS = 15_000L
    private const val STREAM_TIMEOUT_MS = 20_000L
    private const val REGISTRATION_TIMEOUT_MS = 180_000L
    private const val PERMISSION_TIMEOUT_MS = 120_000L
    /** A stream frame older than this is not used as a photo. */
    private const val FRESH_FRAME_MS = 3_000L
    private const val PHOTO_MAX_SIDE = 1600
  }

  var permissionLauncher: AppContextActivityResultLauncher<Permission, String>? = null

  private val context: Context
    get() = (appContext.reactContext ?: throw Exceptions.ReactContextLost()).applicationContext

  private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
  private val frameDispatcher = Dispatchers.Default.limitedParallelism(1)
  /** Serializes connect() / disconnect() / a session ending on its own. */
  private val mutex = Mutex()
  /** Lens cards go out one at a time, in the order they were asked for (the first one attaches the display). */
  private val cardMutex = Mutex()
  /** Guards teardown() and the session fields; teardown() can run from the modules queue, a collector, or OnDestroy. */
  private val lock = Any()
  /** Bumped by every teardown(); collectors of an older session stop reporting. */
  private val generation = AtomicInteger(0)

  @Volatile private var state = "idle"
  @Volatile private var message: String? = null
  @Volatile private var options = ConnectOptions()
  @Volatile private var mock = false
  @Volatile private var deviceName: String? = null
  @Volatile private var displayCapable = false
  private var initialized = false

  @Volatile private var session: DeviceSession? = null
  @Volatile private var camera: Camera? = null
  @Volatile private var stream: Stream? = null
  @Volatile private var display: Display? = null
  private var mockKit: MockDeviceKitInterface? = null
  private var mockGlasses: MockGlasses? = null
  // One selector for the controller's life: each AutoDeviceSelector keeps a collector on the device
  // list for the life of the process, and the Mock Device Kit only attaches a fake link manager to
  // the same DeviceManager, so the same selector serves the mock and real glasses.
  private var selector: AutoDeviceSelector? = null
  /** Parent of every collector of the current session, cancelled as one in teardown(). */
  @Volatile private var sessionJob: Job? = null
  /** Parent of every connect() in flight: disconnect() cancels all of them instead of queueing behind one. */
  private val connectParent = SupervisorJob(scope.coroutineContext[Job])
  private val connectScope = CoroutineScope(scope.coroutineContext + connectParent)
  // connect()'s waits, failed early when the session ends on its own.
  @Volatile private var pendingStarted: CompletableDeferred<Unit>? = null
  @Volatile private var pendingStreaming: CompletableDeferred<Unit>? = null

  @Volatile private var latest: VideoFrame? = null
  @Volatile private var lastFrameAt = 0L
  @Volatile private var lastPreviewAt = 0L
  @Volatile private var frameCount = 0L

  // ------------------------------------------------------------------ state

  fun snapshot(): Map<String, Any?> = mapOf(
    "state" to state,
    "message" to message,
    "mock" to mock,
    "device" to deviceName,
    "displayCapable" to displayCapable,
    "registration" to registrationName(),
    "frames" to frameCount,
  )

  private fun registrationName(): String =
    if (!initialized) "uninitialized" else runCatching { Wearables.registrationState.value.name.lowercase() }.getOrDefault("unknown")

  // State changes and the events reporting them happen under one lock, so JS receives them in the
  // order they happened. emit() only hands the event to the JS runtime and never calls back in.
  private fun setState(next: String, note: String? = null) {
    synchronized(lock) {
      Log.i(TAG, "state $state -> $next${note?.let { " ($it)" } ?: ""}")
      state = next
      message = note
      emit("connection", snapshot())
    }
  }

  /**
   * A state change reported by a collector of session [gen]. Checked and written under the same lock
   * teardown() bumps the generation under, so a collector of a torn-down session can never overwrite
   * the state that disconnect() or the next connect() set. [onlyIf] sees the current state.
   */
  private fun setStateFor(gen: Int, next: String, note: String? = null, onlyIf: (String) -> Boolean = { true }): Boolean {
    synchronized(lock) {
      if (gen != generation.get() || !onlyIf(state)) return false
      Log.i(TAG, "state $state -> $next${note?.let { " ($it)" } ?: ""}")
      state = next
      message = note
      emit("connection", snapshot())
    }
    return true
  }

  /** A non-fatal note from a collector of session [gen] (a session or stream error), reported as is. */
  private fun noteFor(gen: Int, note: String) {
    synchronized(lock) {
      if (gen != generation.get()) return
      message = note
      emit("connection", snapshot())
    }
  }

  private fun <T, E : DatError> DatResult<T, E>.orThrow(what: String): T =
    fold({ it }, { error, cause -> throw GlassesException("$what: ${error.description}", cause) })

  // ------------------------------------------------------------------ connect / disconnect

  suspend fun connect(opts: ConnectOptions): Map<String, Any?> {
    // Runs outside the caller so disconnect() can cancel it. A Deferred that is cancelled before it
    // even starts still completes, so the JS promise always settles.
    val attempt = connectScope.async { mutex.withLock { doConnect(opts) } }
    try {
      return attempt.await()
    } catch (e: CancellationException) {
      currentCoroutineContext().ensureActive() // the caller itself was cancelled: let that propagate
      throw GlassesException("Glasses connection cancelled")
    }
  }

  private suspend fun doConnect(opts: ConnectOptions): Map<String, Any?> {
    // Already connected: only when a session is really alive, so a stale state cannot turn this
    // connect into a silent no-op. The event replaces the "connecting" the JS side shows meanwhile.
    if (session != null && (state == "streaming" || state == "connected" || state == "paused")) {
      synchronized(lock) { emit("connection", snapshot()) }
      return snapshot()
    }
    options = opts
    mock = opts.mock
    setState("connecting")
    try {
      ensureInitialized()
      ensureRegistered()
      if (state == "registering") setState("connecting") // approved in Meta AI; now waiting for the glasses
      val s = openSession()
      ensureCameraPermission()
      if (state == "permission") setState("connected") // camera allowed; starting the stream
      startStream(s)
      return snapshot()
    } catch (e: CancellationException) {
      teardown()
      setState("idle")
      throw e
    } catch (e: Exception) {
      Log.e(TAG, "connect failed", e)
      teardown()
      val note = e.message ?: e.toString()
      setState("error", note)
      if (e is GlassesException) throw e else throw GlassesException(note, e)
    }
  }

  suspend fun disconnect() {
    connectParent.cancelChildren()
    mutex.withLock {
      try {
        teardown()
      } finally {
        setState("idle")
      }
    }
  }

  fun shutdown() {
    connectParent.cancelChildren()
    scope.cancel()
    teardown()
  }

  private fun ensureInitialized() {
    if (mock) {
      val kit = mockKit ?: MockDeviceKit.getInstance(context).also { mockKit = it }
      if (!kit.isEnabled) {
        kit.enable(MockDeviceKitConfig(initiallyRegistered = true, initialPermissionsGranted = true))
      }
      initialized = true
      val glasses = mockGlasses ?: run {
        val existing = kit.pairedDevices.firstOrNull { it is MockGlasses } as? MockGlasses
        existing ?: kit.pairGlasses(mockModel()).orThrow("mock pairing")
      }
      mockGlasses = glasses
      glasses.powerOn()
      glasses.unfold()
      glasses.don()
      val feed = options.mockFeed.trim()
      when {
        feed.startsWith("camera:") ->
          glasses.services.camera.setCameraFeed(if (feed.endsWith("front")) CameraFacing.FRONT else CameraFacing.BACK)
        feed.isNotEmpty() -> glasses.services.camera.setCameraFeed(Uri.parse(feed))
      }
      options.mockPhotoUri?.takeIf { it.isNotBlank() }?.let { glasses.services.camera.setCapturedImage(Uri.parse(it)) }
      return
    }
    mockKit?.let { kit ->
      if (kit.isEnabled) {
        kit.disable()
        mockGlasses = null
        initialized = false
      }
    }
    if (!initialized) {
      // Fails harmlessly when the SDK was already initialized in this process.
      Wearables.initialize(context).fold({}, { error, _ -> Log.i(TAG, "initialize: ${error.description}") })
      initialized = true
    }
  }

  private fun mockModel(): GlassesModel = when (options.mockModel) {
    "meta_rayban_display" -> GlassesModel.META_RAYBAN_DISPLAY
    "oakley_meta_hstn" -> GlassesModel.OAKLEY_META_HSTN
    "oakley_meta_vanguard" -> GlassesModel.OAKLEY_META_VANGUARD
    else -> GlassesModel.RAYBAN_META
  }

  private suspend fun ensureRegistered() {
    if (Wearables.registrationState.value == RegistrationState.REGISTERED) return
    setState("registering")
    val activity = appContext.currentActivity ?: throw Exceptions.MissingActivity()
    withContext(Dispatchers.Main) { Wearables.startRegistration(activity) }
    val outcome = withTimeoutOrNull(REGISTRATION_TIMEOUT_MS) {
      merge(
        Wearables.registrationState.filter { it == RegistrationState.REGISTERED }.map { null },
        Wearables.registrationErrorStream.map { it.toString() },
      ).first()
    }
    when {
      outcome == null && Wearables.registrationState.value == RegistrationState.REGISTERED -> return
      outcome == null -> throw GlassesException("Registration with the Meta AI app did not complete. Install Meta AI (V292 or newer), turn on Developer Mode under Settings > App Info, and try again.")
      outcome.contains("META_AI_NOT_INSTALLED") ->
        throw GlassesException("The Meta AI app is not installed. Install Meta AI (V292 or newer), pair the glasses there and turn on Developer Mode, then try again.")
      else -> throw GlassesException("Registration failed: $outcome")
    }
  }

  private suspend fun openSession(): DeviceSession {
    // A device shows up a moment after pairing (the mock) or after the Bluetooth link comes up
    // (real glasses); creating the session before that fails with "No eligible device".
    val sel = this.selector ?: AutoDeviceSelector().also { this.selector = it }
    if (sel.activeDevice() == null) {
      withTimeoutOrNull(DEVICE_TIMEOUT_MS) { sel.activeDeviceFlow().first { it != null } }
        ?: throw GlassesException(
          if (mock) "The mock glasses did not come up"
          else "No glasses connected. Check that they are on, unfolded, worn, and paired in the Meta AI app",
        )
    }
    val s = Wearables.createSession(sel).orThrow("No glasses to connect to")
    val parent = SupervisorJob(scope.coroutineContext[Job])
    val started = CompletableDeferred<Unit>()
    val gen = synchronized(lock) {
      session = s
      sessionJob = parent
      pendingStarted = started
      generation.get()
    }
    val sessionScope = CoroutineScope(scope.coroutineContext + parent)
    // UNDISPATCHED: subscribed before s.start(), which reports preflight failures synchronously
    // on a SharedFlow without replay.
    sessionScope.launch(start = CoroutineStart.UNDISPATCHED) {
      s.errors.collect { error ->
        Log.w(TAG, "session error: ${error.description}")
        if (gen != generation.get()) return@collect
        if (!started.isCompleted) started.completeExceptionally(GlassesException("Session: ${error.description}"))
        else noteFor(gen, error.description)
      }
    }
    sessionScope.launch(start = CoroutineStart.UNDISPATCHED) {
      s.state.collect { st ->
        if (gen != generation.get()) return@collect
        when (st) {
          DeviceSessionState.STARTED -> {
            val info = s.deviceInfo.value
            deviceName = info.name
            displayCapable = info.isDisplayCapable()
            if (!started.isCompleted) started.complete(Unit)
            setStateFor(gen, "connected") { it == "connecting" }
          }
          DeviceSessionState.PAUSED -> setStateFor(gen, "paused") { it == "streaming" || it == "connected" }
          DeviceSessionState.STOPPED -> {
            val note = message
            // Fail the waits of a connect() in flight right away, then tear down under the mutex
            // (connect() may still hold it; by the time this runs it has cleaned up itself).
            started.completeExceptionally(GlassesException("The glasses session ended: ${note ?: "disconnected"}"))
            pendingStreaming?.completeExceptionally(GlassesException("The glasses session ended: ${note ?: "disconnected"}"))
            scope.launch {
              mutex.withLock {
                if (session === s) {
                  teardown()
                  setState("disconnected", note)
                }
              }
            }
          }
          else -> Unit
        }
      }
    }
    s.start()
    withTimeoutOrNull(CONNECT_TIMEOUT_MS) { started.await() }
      ?: throw GlassesException("The glasses did not connect in time (are they on, unfolded and worn?)")
    return s
  }

  private suspend fun ensureCameraPermission() {
    val status = Wearables.checkPermissionStatus(Permission.CAMERA).orThrow("Permission check")
    if (status == PermissionStatus.Granted) return
    val launcher = permissionLauncher ?: throw GlassesException("Permission launcher not registered")
    setState("permission")
    val result = withTimeoutOrNull(PERMISSION_TIMEOUT_MS) { withContext(Dispatchers.Main) { launcher.launch(Permission.CAMERA) } }
      ?: throw GlassesException("No answer from the Meta AI app to the camera permission request")
    if (result != "granted") throw GlassesException("Camera access was not granted in the Meta AI app ($result)")
  }

  private suspend fun startStream(s: DeviceSession) {
    val quality = when (options.quality.lowercase()) {
      "low" -> VideoQuality.LOW
      "medium" -> VideoQuality.MEDIUM
      else -> VideoQuality.HIGH
    }
    val fps = listOf(2, 7, 15, 24, 30).minByOrNull { kotlin.math.abs(it - options.frameRate) } ?: 15
    val cam = s.addCamera(StreamConfiguration(videoQuality = quality, frameRate = fps, compressVideo = false)).orThrow("Camera")
    val st = cam.stream
    val streaming = CompletableDeferred<Unit>()
    val (parent, gen) = synchronized(lock) {
      if (session !== s) throw GlassesException("The glasses session ended")
      camera = cam
      stream = st
      pendingStreaming = streaming
      (sessionJob ?: throw GlassesException("The glasses session ended")) to generation.get()
    }
    val streamScope = CoroutineScope(scope.coroutineContext + parent)
    streamScope.launch(frameDispatcher) { st.videoStream.collect { if (gen == generation.get()) onFrame(it) } }
    // UNDISPATCHED for the same reason: st.start() can emit CRITICAL_STREAM_ERROR before returning.
    streamScope.launch(start = CoroutineStart.UNDISPATCHED) {
      st.errorStream.collect { error ->
        Log.w(TAG, "stream error: ${error.description}")
        if (gen != generation.get()) return@collect
        val fatal = error == StreamError.CRITICAL_STREAM_ERROR || error == StreamError.PERMISSIONS_DENIED
        if (!streaming.isCompleted && fatal) streaming.completeExceptionally(GlassesException("Stream: ${error.description}"))
        else noteFor(gen, error.description)
      }
    }
    streamScope.launch(start = CoroutineStart.UNDISPATCHED) {
      st.state.collect { sst ->
        if (gen != generation.get()) return@collect
        when (sst) {
          StreamState.STREAMING -> {
            if (!streaming.isCompleted) streaming.complete(Unit)
            setStateFor(gen, "streaming")
          }
          StreamState.PAUSED -> setStateFor(gen, "paused", "paused from the glasses (tap the touchpad to resume)")
          StreamState.STOPPED, StreamState.CLOSED ->
            if (streaming.isCompleted && setStateFor(gen, "connected", "stream stopped", onlyIf = { it == "streaming" || it == "paused" })) {
              latest = null
            }
          else -> Unit
        }
      }
    }
    st.start().orThrow("Stream start")
    withTimeoutOrNull(STREAM_TIMEOUT_MS) { streaming.await() }
      ?: throw GlassesException("The glasses camera did not start streaming")
  }

  /** Stops everything of the current session. Safe from any thread; later calls are no-ops. */
  private fun teardown() {
    synchronized(lock) {
      generation.incrementAndGet()
      sessionJob?.cancel()
      sessionJob = null
      pendingStarted?.completeExceptionally(GlassesException("Glasses disconnected"))
      pendingStarted = null
      pendingStreaming?.completeExceptionally(GlassesException("Glasses disconnected"))
      pendingStreaming = null
      runCatching { display?.stop() }
      display = null
      runCatching { camera?.stop() } // cascades to the stream
      camera = null
      stream = null
      latest = null
      runCatching { session?.stop() }
      session = null
      deviceName = null
      displayCapable = false
    }
  }

  fun handleIntent(intent: Intent) {
    if (!initialized || mock) return
    val activity = appContext.currentActivity ?: return
    Wearables.handleIntent(intent) { request ->
      request.continueRegistration(activity).fold({}, { error, _ -> Log.w(TAG, "continueRegistration: ${error.description}") })
    }
  }

  // ------------------------------------------------------------------ frames

  private fun onFrame(frame: VideoFrame) {
    if (frame.isCompressed || frame.isCodecConfig) return
    val w = frame.width
    val h = frame.height
    if (w <= 0 || h <= 0 || frame.buffer.remaining() < w * h * 3 / 2) return
    latest = frame
    lastFrameAt = SystemClock.elapsedRealtime()
    if (frameCount == 0L) Log.i(TAG, "first decoded frame ${w}x$h (${frame.buffer.remaining()} bytes, layout ${options.yuv})")
    frameCount++
    val now = SystemClock.elapsedRealtime()
    val interval = 1000L / options.previewFps.coerceIn(1, 30)
    if (now - lastPreviewAt < interval) return
    lastPreviewAt = now
    try {
      val factor = (w / options.previewWidth.coerceAtLeast(120)).coerceAtLeast(1)
      val jpeg = Yuv.toJpeg(frame.buffer, w, h, options.yuv, factor, options.previewQuality)
      val b64 = Base64.encodeToString(jpeg.bytes, Base64.NO_WRAP)
      emit(
        "frame",
        mapOf(
          "uri" to "data:image/jpeg;base64,$b64",
          "width" to jpeg.width,
          "height" to jpeg.height,
          "ts" to frame.presentationTimeUs / 1000,
          "n" to frameCount,
        ),
      )
    } catch (e: Exception) {
      Log.w(TAG, "preview encode failed", e)
    }
  }

  // ------------------------------------------------------------------ photos

  suspend fun capturePhoto(): Map<String, Any?> {
    val st = stream
    if (st == null || !(state == "streaming" || state == "paused")) throw GlassesException("No photo: the glasses are not streaming")
    var failure: String? = null
    var bitmap: Bitmap? = null
    run {
      bitmap = st.capturePhoto().fold(
        { data ->
          when (data) {
            is PhotoData.Bitmap -> data.bitmap
            is PhotoData.HEIC -> decodeWithOrientation(data.data)
          }
        },
        { error, _ ->
          failure = error.description
          null
        },
      )
    }
    val file = newPhotoFile()
    if (bitmap != null) {
      val scaled = downscale(bitmap, PHOTO_MAX_SIDE)
      withContext(Dispatchers.IO) { FileOutputStream(file).use { scaled.compress(Bitmap.CompressFormat.JPEG, 90, it) } }
      return mapOf("uri" to Uri.fromFile(file).toString(), "width" to scaled.width, "height" to scaled.height, "source" to "glasses", "note" to null)
    }
    // Fall back to the latest stream frame, but only a live one: a frame from before a pause or
    // stop must not be passed off as a fresh photo.
    val frame = latest
    val fresh = frame != null && state == "streaming" && SystemClock.elapsedRealtime() - lastFrameAt < FRESH_FRAME_MS
    if (frame == null || !fresh) throw GlassesException(failure?.let { "No photo: $it" } ?: "No photo: no recent frame from the glasses")
    val jpeg = withContext(frameDispatcher) { Yuv.toJpeg(frame.buffer, frame.width, frame.height, options.yuv, 1, 90) }
    withContext(Dispatchers.IO) { file.writeBytes(jpeg.bytes) }
    return mapOf("uri" to Uri.fromFile(file).toString(), "width" to jpeg.width, "height" to jpeg.height, "source" to "frame", "note" to failure)
  }

  private fun newPhotoFile(): File {
    val dir = File(context.cacheDir, "glasses").apply { mkdirs() }
    dir.listFiles()?.sortedByDescending { it.lastModified() }?.drop(6)?.forEach { it.delete() }
    return File(dir, "photo_${System.currentTimeMillis()}.jpg")
  }

  private fun downscale(bitmap: Bitmap, maxSide: Int): Bitmap {
    val longest = maxOf(bitmap.width, bitmap.height)
    if (longest <= maxSide) return bitmap
    val scale = maxSide.toFloat() / longest
    return Bitmap.createScaledBitmap(bitmap, (bitmap.width * scale).toInt().coerceAtLeast(1), (bitmap.height * scale).toInt().coerceAtLeast(1), true)
  }

  /** The glasses store orientation in EXIF, which BitmapFactory does not apply for HEIC. */
  private fun decodeWithOrientation(data: ByteBuffer): Bitmap? {
    val buffer = data.duplicate().apply { rewind() }
    val bytes = ByteArray(buffer.remaining())
    buffer.get(bytes)
    val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null
    val orientation = runCatching {
      ByteArrayInputStream(bytes).use { ExifInterface(it).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL) }
    }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)
    val matrix = Matrix()
    when (orientation) {
      ExifInterface.ORIENTATION_ROTATE_90 -> matrix.postRotate(90f)
      ExifInterface.ORIENTATION_ROTATE_180 -> matrix.postRotate(180f)
      ExifInterface.ORIENTATION_ROTATE_270 -> matrix.postRotate(270f)
      ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.postScale(-1f, 1f)
      ExifInterface.ORIENTATION_FLIP_VERTICAL -> matrix.postScale(1f, -1f)
      ExifInterface.ORIENTATION_TRANSPOSE -> { matrix.postRotate(90f); matrix.postScale(-1f, 1f) }
      ExifInterface.ORIENTATION_TRANSVERSE -> { matrix.postRotate(270f); matrix.postScale(-1f, 1f) }
    }
    if (matrix.isIdentity) return bitmap
    return runCatching { Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true) }.getOrDefault(bitmap)
  }

  // ------------------------------------------------------------------ display cards

  /** Sends one card to the glasses display; false when the glasses have no display or nothing is connected. */
  suspend fun showCard(card: CardOptions): Boolean = cardMutex.withLock { sendCard(card) }

  private suspend fun sendCard(card: CardOptions): Boolean {
    val s = session ?: return false
    if (!displayCapable) return false
    val d = display ?: attachDisplay(s) ?: return false
    val bitmap = card.imageUri?.takeIf { it.isNotBlank() }?.let { loadBitmap(it) }
    return d.sendContent {
      flexBox(gap = 8, padding = 24, background = FlexBoxBackground.CARD) {
        if (card.eyebrow.isNotBlank()) text(card.eyebrow, style = TextStyle.META, color = TextColor.SECONDARY)
        text(card.title, style = TextStyle.HEADING)
        if (card.body.isNotBlank()) text(card.body, style = TextStyle.BODY)
        if (bitmap != null) image(uri = null, bitmap = bitmap, sizePreset = ImageSize.FILL, cornerRadius = CornerRadius.MEDIUM)
        if (card.hint.isNotBlank()) text(card.hint, style = TextStyle.META, color = TextColor.SECONDARY)
      }
    }.fold({ sent ->
      Log.i(TAG, "card \"${card.title.take(40)}\" sent=$sent")
      sent
    }, { error, _ ->
      Log.w(TAG, "sendContent: ${error.description}")
      false
    })
  }

  suspend fun clearCard(): Boolean = cardMutex.withLock {
    val d = display ?: return@withLock false
    d.clearDisplay().fold({ it }, { _, _ -> false })
  }

  private suspend fun attachDisplay(s: DeviceSession): Display? {
    val d = s.addDisplay().fold({ it }, { error, _ ->
      Log.w(TAG, "addDisplay: ${error.description}")
      null
    }) ?: return null
    // Publish it only for the session it belongs to; teardown() may have ended that session meanwhile.
    val current = synchronized(lock) { if (session === s) { display = d; true } else false }
    if (!current) {
      runCatching { d.stop() }
      return null
    }
    // A new Display starts out STOPPED and moves to STARTING later on the toolkit's IO thread, so
    // STOPPED means "failed" only after it has left that initial state; CLOSED is final either way.
    var left = false
    val reached = withTimeoutOrNull(10_000) {
      d.state.first { st ->
        if (st != DisplayState.STOPPED) left = true
        st == DisplayState.STARTED || st == DisplayState.CLOSED || (left && st == DisplayState.STOPPED)
      }
    }
    if (reached != DisplayState.STARTED) {
      Log.w(TAG, "display did not start ($reached)")
      synchronized(lock) { if (display === d) display = null }
      runCatching { d.stop() }
      return null
    }
    return d
  }

  private suspend fun loadBitmap(uri: String): Bitmap? = withContext(Dispatchers.IO) {
    runCatching {
      val parsed = Uri.parse(uri)
      val bytes = when (parsed.scheme) {
        "file", null -> File(parsed.path ?: uri).readBytes()
        "content" -> context.contentResolver.openInputStream(parsed)?.use { it.readBytes() }
        else -> java.net.URL(uri).openStream().use { it.readBytes() }
      } ?: return@runCatching null
      BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.let { downscale(it, 600) }
    }.getOrNull()
  }
}
