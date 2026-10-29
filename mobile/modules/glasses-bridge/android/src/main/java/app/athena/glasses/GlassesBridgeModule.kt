package app.athena.glasses

import expo.modules.kotlin.functions.Coroutine
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/**
 * Expo module "GlassesBridge": Ray-Ban Meta glasses as a frame source for a session, through the
 * Meta Wearables Device Access Toolkit. All the toolkit work lives in GlassesController; this class
 * only maps it onto the Expo Modules API (functions, events, activity contracts, intents).
 */
class GlassesBridgeModule : Module() {
  private val controller: GlassesController by lazy {
    GlassesController(appContext) { name, body -> sendEvent(name, body) }
  }

  override fun definition() = ModuleDefinition {
    Name("GlassesBridge")

    Events("connection", "frame")

    Constant("sdkVersion") { GlassesController.SDK_VERSION }

    Function("getState") { controller.snapshot() }

    AsyncFunction("connect") Coroutine { options: ConnectOptions -> controller.connect(options) }

    AsyncFunction("disconnect") Coroutine { -> controller.disconnect() }

    AsyncFunction("capturePhoto") Coroutine { -> controller.capturePhoto() }

    AsyncFunction("showCard") Coroutine { card: CardOptions -> controller.showCard(card) }

    AsyncFunction("clearCard") Coroutine { -> controller.clearCard() }

    RegisterActivityContracts {
      controller.permissionLauncher = registerForActivityResult(DatPermissionContract())
    }

    // The Meta AI app returns to this activity through the athena:// scheme after registration;
    // the toolkit also uses intents when Meta AI starts the registration itself.
    OnNewIntent { intent -> controller.handleIntent(intent) }

    OnDestroy { controller.shutdown() }
  }
}
