package app.athena.glasses

import android.content.Context
import android.content.Intent
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import expo.modules.kotlin.activityresult.AppContextActivityResultContract

/**
 * The toolkit's permission prompt (it opens the Meta AI app) wrapped for Expo's activity-result
 * registry, which registers contracts before the activity starts. Result: "granted", "denied" or
 * "error:<description>".
 */
class DatPermissionContract : AppContextActivityResultContract<Permission, String> {
  private val inner = Wearables.RequestPermissionContract()

  override fun createIntent(context: Context, input: Permission): Intent = inner.createIntent(context, input)

  override fun parseResult(input: Permission, resultCode: Int, intent: Intent?): String =
    inner.parseResult(resultCode, intent).fold(
      { status -> if (status == PermissionStatus.Granted) "granted" else "denied" },
      { error, _ -> "error:${error.description}" },
    )
}
