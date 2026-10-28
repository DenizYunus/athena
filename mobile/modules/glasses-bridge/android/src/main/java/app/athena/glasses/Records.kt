package app.athena.glasses

import expo.modules.kotlin.records.Field
import expo.modules.kotlin.records.Record

/** Options for connect(); see modules/glasses-bridge/index.ts for what each one means. */
class ConnectOptions : Record {
  @Field val mock: Boolean = false
  @Field val mockModel: String = "rayban_meta"
  @Field val mockFeed: String = "camera:back"
  @Field val mockPhotoUri: String? = null
  @Field val quality: String = "high"
  @Field val frameRate: Int = 15
  @Field val previewWidth: Int = 360
  @Field val previewFps: Int = 8
  @Field val previewQuality: Int = 55
  @Field val yuv: String = "i420"
}

/** One card for the 600x600 display of Meta Ray-Ban Display glasses. */
class CardOptions : Record {
  @Field val kind: String = "step"
  @Field val eyebrow: String = ""
  @Field val title: String = ""
  @Field val body: String = ""
  @Field val hint: String = ""
  @Field val imageUri: String? = null
}
