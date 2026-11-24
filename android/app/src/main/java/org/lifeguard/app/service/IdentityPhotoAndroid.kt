package org.lifeguard.app.service

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import org.lifeguard.app.core.IdentityPhoto
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream

/**
 * הכנת צילום ת"ז / סלפי באנדרואיד: Bitmap לפי IdentityPhoto.prepare (הקטנה ל-1600, סיבוב לפי EXIF, JPEG 88).
 * Bitmap.compress לא כותב EXIF בכלל, ו-IdentityPhoto.stripJpegMetadata מסירה כל מטא-דאטה שנשאר - המקור (עם מיקום
 * הצילום) לא נשלח לשרת. הכול בזיכרון; שום קובץ מוכן לא נכתב לדיסק.
 */
object IdentityPhotoAndroid : IdentityPhoto.Codec<Bitmap> {
    /** קריאה (רקע בלבד) + הכנה. זורק IdentityPhoto.PhotoException עם הודעה בעברית. */
    fun prepare(ctx: Context, uri: Uri): ByteArray {
        val src = ctx.contentResolver.openInputStream(uri)?.use { input ->
            val buf = ByteArrayOutputStream()
            val chunk = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(chunk)
                if (n < 0) break
                buf.write(chunk, 0, n)
                if (buf.size() > IdentityPhoto.MAX_SOURCE_BYTES) throw IdentityPhoto.PhotoException("הקובץ גדול מדי")
            }
            buf.toByteArray()
        } ?: throw IdentityPhoto.PhotoException("לא ניתן לקרוא את התמונה")
        return IdentityPhoto.prepare(src, this)
    }

    override fun bounds(src: ByteArray): Pair<Int, Int>? {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(src, 0, src.size, o)
        return if (o.outWidth > 0 && o.outHeight > 0) o.outWidth to o.outHeight else null
    }

    override fun orientation(src: ByteArray): Int = runCatching {
        ExifInterface(ByteArrayInputStream(src)).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
    }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)

    override fun decode(src: ByteArray, sampleSize: Int): Bitmap? =
        BitmapFactory.decodeByteArray(src, 0, src.size, BitmapFactory.Options().apply {
            inSampleSize = sampleSize
            inPreferredConfig = Bitmap.Config.ARGB_8888
        })

    override fun size(img: Bitmap): Pair<Int, Int> = img.width to img.height

    override fun scaleAndOrient(img: Bitmap, w: Int, h: Int, orientation: Int): Bitmap {
        val m = Matrix()
        m.postScale(w.toFloat() / img.width, h.toFloat() / img.height)
        when (orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> m.postScale(-1f, 1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> m.postRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> m.postScale(1f, -1f)
            ExifInterface.ORIENTATION_TRANSPOSE -> { m.postRotate(90f); m.postScale(-1f, 1f) }
            ExifInterface.ORIENTATION_ROTATE_90 -> m.postRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> { m.postRotate(-90f); m.postScale(-1f, 1f) }
            ExifInterface.ORIENTATION_ROTATE_270 -> m.postRotate(270f)
        }
        if (m.isIdentity) return img
        return Bitmap.createBitmap(img, 0, 0, img.width, img.height, m, true)
    }

    override fun encodeJpeg(img: Bitmap, quality: Int): ByteArray {
        // JPEG בלי ערוץ שקיפות: PNG שקוף מקבל רקע לבן (אחרת שחור)
        val opaque = if (img.hasAlpha()) Bitmap.createBitmap(img.width, img.height, Bitmap.Config.ARGB_8888).also {
            android.graphics.Canvas(it).apply { drawColor(android.graphics.Color.WHITE); drawBitmap(img, 0f, 0f, null) }
        } else img
        return try {
            ByteArrayOutputStream().use { out ->
                if (!opaque.compress(Bitmap.CompressFormat.JPEG, quality, out)) throw IdentityPhoto.PhotoException("קידוד התמונה נכשל")
                out.toByteArray()
            }
        } finally {
            if (opaque !== img) opaque.recycle()
        }
    }

    override fun release(img: Bitmap) { if (!img.isRecycled) img.recycle() }
}
