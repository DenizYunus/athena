package app.athena.glasses

import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer

/**
 * Turns one decoded 4:2:0 frame from the toolkit into a JPEG. The toolkit configures its HEVC
 * decoder for planar I420 (MediaCodec color format 19) and hands the decoder's buffer through
 * untouched, so i420 is the default; nv12 / nv21 are there for decoders that ignore the request.
 * `factor` subsamples by an integer (2 turns 720x1280 into 360x640) before encoding.
 */
object Yuv {
  class Jpeg(val bytes: ByteArray, val width: Int, val height: Int)

  fun toJpeg(src: ByteBuffer, width: Int, height: Int, layout: String, factor: Int, quality: Int): Jpeg {
    val f = factor.coerceAtLeast(1)
    val buf = src.duplicate()
    val base = buf.position()
    val ySize = width * height
    require(buf.remaining() >= ySize * 3 / 2) { "frame buffer too small for ${width}x$height" }

    val ow = (width / f) and 1.inv()
    val oh = (height / f) and 1.inv()
    val out = ByteArray(ow * oh * 3 / 2)

    // Luma: straight copy when not subsampling, else nearest neighbour.
    if (f == 1 && ow == width && oh == height) {
      val dup = buf.duplicate()
      dup.position(base)
      dup.get(out, 0, ySize)
    } else {
      for (y in 0 until oh) {
        val rowOff = base + (y * f) * width
        val outOff = y * ow
        for (x in 0 until ow) out[outOff + x] = buf.get(rowOff + x * f)
      }
    }

    // Chroma, written interleaved V then U (NV21), which YuvImage can encode.
    val cw = width / 2
    val ch = height / 2
    val ocw = ow / 2
    val och = oh / 2
    val uvBase = base + ySize
    val vPlane = uvBase + cw * ch
    var o = ow * oh
    for (y in 0 until och) {
      val sy = y * f
      for (x in 0 until ocw) {
        val sx = x * f
        val u: Byte
        val v: Byte
        when (layout) {
          "nv12" -> {
            val i = uvBase + (sy * cw + sx) * 2
            u = buf.get(i)
            v = buf.get(i + 1)
          }
          "nv21" -> {
            val i = uvBase + (sy * cw + sx) * 2
            v = buf.get(i)
            u = buf.get(i + 1)
          }
          else -> {
            u = buf.get(uvBase + sy * cw + sx)
            v = buf.get(vPlane + sy * cw + sx)
          }
        }
        out[o++] = v
        out[o++] = u
      }
    }

    val baos = ByteArrayOutputStream(ow * oh / 4)
    YuvImage(out, ImageFormat.NV21, ow, oh, null).compressToJpeg(Rect(0, 0, ow, oh), quality.coerceIn(10, 100), baos)
    return Jpeg(baos.toByteArray(), ow, oh)
  }
}
