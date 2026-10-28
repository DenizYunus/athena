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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
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
    private const val STREAM_TIMEOUT_MS = 20_000L
    private const val REGISTRATION_TIMEOUT_MS = 180_000L
    private const val PHOTO_MAX_SIDE = 1600
  }

  var permissionLauncher: AppContextActivityResultLauncher<Permission, String>? = null

  private val context: Context
    get() = (appContext.reactContext ?: throw Exceptions.ReactContextLost()).applicationContext

  private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
  private val frameDispatcher = Dispatchers.Default.limitedParallelism(1)
  private val mutex = Mutex()

  @Volatile private var state = "idle"
  @Volatile private var message: String? = null
  @Volatile private var options = ConnectOptions()
  private var mock = false
  private var deviceName: String? = null
  private var displayCapable = false
  private var initialized = false

  private var session: DeviceSession? = null
  private var camera: Camera? = null
  private var stream: Stream? = null
  private var display: Display? = null
  private var mockKit: MockDeviceKitInterface? = null
  private var mockGlasses: MockGlasses? = null
  private val jobs = mutableListOf<Job>()

  @Volatile private var latest: VideoFrame? = null
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

  private fun setState(next: String, note: String? = null) {
    Log.i(TAG, "state $state -> $next${note?.let { " ($it)" } ?: ""}")
    state = next
    message = note
    emit("connection", snapshot())
  }

  private fun <T, E : DatError> DatResult<T, E>.orThrow(what: String): T =
    fold({ it }, { error, cause -> throw GlassesException("$what: ${error.description}", cause) })

  // ------------------------------------------------------------------ connect / disconnect

  suspend fun connect(opts: ConnectOptions): Map<String, Any?> = mutex.withLock {
    if (state == "streaming" || state == "connected" || state == "paused") return snapshot()
    options = opts
    mock = opts.mock
    setState("connecting")
    try {
      ensureInitialized()
      ensureRegistered()
      val s = openSession()
      ensureCameraPermission()
      startStream(s)
      snapshot()
    } catch (e: CancellationException) {
      throw e
    } catch (e: Exception) {
      Log.e(TAG, "connect failed", e)
      teardown()
      val note = e.message ?: e.toString()
      setState("error", note)
      if (e is GlassesException) throw e else throw GlassesException(note, e)
    }
  }

  suspend fun disconnect() = mutex.withLock {
    teardown()
    setState("idle")
  }

  fun shutdown() {
    teardown()
    scope.cancel()
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
      else -> throw GlassesException("Registration failed: $outcome")
    }
  }

  private suspend fun openSession(): DeviceSession {
    val s = Wearables.createSession(AutoDeviceSelector()).orThrow("No glasses to connect to")
    session = s
    val started = CompletableDeferred<Unit>()
    jobs += scope.launch {
      s.errors.collect { error ->
        Log.w(TAG, "session error: ${error.description}")
        message = error.description
        if (!started.isCompleted) started.completeExceptionally(GlassesException("Session: ${error.description}"))
        else emit("connection", snapshot())
      }
    }
    jobs += scope.launch {
      s.state.collect { st ->
        when (st) {
          DeviceSessionState.STARTED -> {
            val info = s.deviceInfo.value
            deviceName = info.name
            displayCapable = info.isDisplayCapable()
            if (!started.isCompleted) started.complete(Unit)
            if (state == "connecting") setState("connected")
          }
          DeviceSessionState.PAUSED -> if (state == "streaming" || state == "connected") setState("paused")
          DeviceSessionState.STOPPED -> {
            if (!started.isCompleted) started.completeExceptionally(GlassesException("The glasses session stopped before it started"))
            else onSessionStopped()
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
    val result = withContext(Dispatchers.Main) { launcher.launch(Permission.CAMERA) }
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
    camera = cam
    val st = cam.stream
    stream = st
    val streaming = CompletableDeferred<Unit>()
    jobs += scope.launch(frameDispatcher) { st.videoStream.collect { onFrame(it) } }
    jobs += scope.launch {
      st.errorStream.collect { error ->
        Log.w(TAG, "stream error: ${error.description}")
        message = error.description
        val fatal = error == StreamError.CRITICAL_STREAM_ERROR || error == StreamError.PERMISSIONS_DENIED
        if (!streaming.isCompleted && fatal) streaming.completeExceptionally(GlassesException("Stream: ${error.description}"))
        else emit("connection", snapshot())
      }
    }
    jobs += scope.launch {
      st.state.collect { sst ->
        when (sst) {
          StreamState.STREAMING -> {
            if (!streaming.isCompleted) streaming.complete(Unit)
            setState("streaming")
          }
          StreamState.PAUSED -> setState("paused", "paused from the glasses (tap the touchpad to resume)")
          StreamState.STOPPED, StreamState.CLOSED ->
            if (streaming.isCompleted && (state == "streaming" || state == "paused")) setState("connected", "stream stopped")
          else -> Unit
        }
      }
    }
    st.start().orThrow("Stream start")
    withTimeoutOrNull(STREAM_TIMEOUT_MS) { streaming.await() }
      ?: throw GlassesException("The glasses camera did not start streaming")
  }

  private fun onSessionStopped() {
    if (state == "idle") return
    val note = message
    teardown()
    setState("disconnected", note)
  }

  private fun teardown() {
    jobs.forEach { it.cancel() }
    jobs.clear()
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
    var failure: String? = null
    var bitmap: Bitmap? = null
    if (st != null && (state == "streaming" || state == "paused")) {
      bitmap = st.capturePhoto().fold(
        { data ->
          when (data) {
            is PhotoData.Bitmap -> data.bitmap
            is PhotoData.HEIC -> decodeWithOrientation(data.data)
            else -> null
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
    val frame = latest ?: throw GlassesException(failure?.let { "No photo: $it" } ?: "No photo: the glasses are not streaming")
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
  suspend fun showCard(card: CardOptions): Boolean {
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
    }.fold({ it }, { error, _ ->
      Log.w(TAG, "sendContent: ${error.description}")
      false
    })
  }

  suspend fun clearCard(): Boolean {
    val d = display ?: return false
    return d.clearDisplay().fold({ it }, { _, _ -> false })
  }

  private suspend fun attachDisplay(s: DeviceSession): Display? {
    val d = s.addDisplay().fold({ it }, { error, _ ->
      Log.w(TAG, "addDisplay: ${error.description}")
      null
    }) ?: return null
    display = d
    if (withTimeoutOrNull(10_000) { d.state.first { it == DisplayState.STARTED } } == null) {
      Log.w(TAG, "display did not reach STARTED")
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
