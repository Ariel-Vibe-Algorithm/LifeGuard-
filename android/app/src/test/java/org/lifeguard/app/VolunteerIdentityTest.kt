package org.lifeguard.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.VolunteerIdentity
import org.lifeguard.app.core.IdentityForm
import org.lifeguard.app.core.IdentityPhoto
import org.lifeguard.app.core.IsraeliId
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream

/**
 * אימות זהות מתנדב/ת (server/volunteer_identity.py): ספרת ביקורת זהה לשרת, הכנת התמונות (צלע ארוכה <= 1600,
 * JPEG מחדש, בלי EXIF/GPS), טקסטים ובדיקות הטופס, וחוזה ה-API (נתיב/שיטה/גוף).
 */
class VolunteerIdentityTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun setUp() {
        server = MockWebServer(); server.start()
        api = ApiClient(server.url("/").toString().trimEnd('/'), { "tok" })
    }

    @After fun tearDown() { server.shutdown() }

    // ---------------- ספרת ביקורת ----------------
    /** הפלט של valid_israeli_id בשרת (Python) על אותם קלטים */
    private val pythonVectors = listOf(
        "000000018" to "000000018", "18" to "000000018", "123456782" to "123456782", " 123456782 " to "123456782",
        "123456789" to null, "000000000" to null, "0" to null, "1234567890" to null, "12a456782" to null, "" to null,
        "  " to null, "12345678-2" to null, "+23456782" to null, "039337423" to "039337423", "039337424" to null,
        "987654321" to null, "999999998" to "999999998", "1" to null, "26" to "000000026", "447712782" to null,
        "261973069" to "261973069", "523938499" to null, "798935572" to null, "151847156" to null,
        "177777868" to null, "981836553" to null, "675398922" to null, "201071364" to null,
    )

    @Test fun israeliIdCheckDigitMatchesServer() {
        for ((input, expected) in pythonVectors) assertEquals("valid_israeli_id('$input')", expected, IsraeliId.valid(input))
        assertNull(IsraeliId.valid(null))
    }

    @Test fun israeliIdEveryCheckDigitHasExactlyOneValid() {
        for (base in listOf("12345678", "03933742", "00000001", "99999999")) {
            val ok = (0..9).filter { IsraeliId.valid(base + it) != null }
            assertEquals(base, 1, ok.size)
        }
    }

    // ---------------- טופס וטקסטים ----------------
    @Test fun formValidationOrderAndTexts() {
        assertEquals(IdentityForm.BAD_ID_HE, IdentityForm.validate("123456789", true, true, true))
        assertEquals(IdentityForm.MISSING_PHOTOS_HE, IdentityForm.validate("123456782", true, false, true))
        assertEquals(IdentityForm.MISSING_PHOTOS_HE, IdentityForm.validate("123456782", false, true, true))
        assertEquals(IdentityForm.NO_CONSENT_HE, IdentityForm.validate("123456782", true, true, false))
        assertNull(IdentityForm.validate("123456782", true, true, true))
        assertEquals("אני מאשר/ת שימוש בתמונות ובמספר הת\"ז לאימות זהותי כמתנדב/ת בלבד", IdentityForm.CONSENT_HE)
        // ההסבר: מוצפן, נמחק אחרי ההחלטה / 30 יום, רק 4 ספרות
        assertTrue(IdentityForm.EXPLAIN_HE.contains("מוצפנות"))
        assertTrue(IdentityForm.EXPLAIN_HE.contains("30 יום"))
        assertTrue(IdentityForm.EXPLAIN_HE.contains("4 ספרות אחרונות"))
    }

    @Test fun warningLineByStatus() {
        assertNull(IdentityForm.warningLine("verified", null))
        assertEquals("לא יישלחו אליך קריאות עד שהזהות תאומת", IdentityForm.warningLine("none", null))
        assertEquals("לא יישלחו אליך קריאות עד שהזהות תאומת", IdentityForm.warningLine("pending", null))
        assertEquals("לא יישלחו אליך קריאות עד שהזהות תאומת · סיבת הדחייה: תמונה מטושטשת",
            IdentityForm.warningLine("rejected", "תמונה מטושטשת"))
    }

    @Test fun statusParses() {
        val st = VolunteerIdentity.parse(JSONObject("""{"status":"rejected","status_he":"נדחה","required":true,
            "can_receive_calls":false,"id_last4":"6782","submitted_at":1.7E9,"decided_at":1.7000001E9,"note":"לא ברור"}"""))
        assertEquals("rejected", st.status); assertEquals("נדחה", st.statusHe); assertTrue(st.required)
        assertFalse(st.canReceiveCalls); assertEquals("6782", st.idLast4); assertEquals("לא ברור", st.note)
        // שרת ישן / תשובה חסרה: none, נדרש, בלי קריאות
        val empty = VolunteerIdentity.parse(JSONObject("{}"))
        assertEquals("none", empty.status); assertEquals("לא הועלה", empty.statusHe)
        assertTrue(empty.required); assertFalse(empty.canReceiveCalls)
        assertTrue(VolunteerIdentity.parse(JSONObject("""{"status":"verified","can_receive_calls":true}""")).canReceiveCalls)
    }

    // ---------------- API ----------------
    private fun json(code: Int, body: String) = MockResponse().setResponseCode(code)
        .setHeader("Content-Type", "application/json; charset=utf-8").setBody(body)

    @Test fun identityApiContract() {
        server.enqueue(json(200, """{"status":"none","status_he":"לא הועלה","required":true,"can_receive_calls":false}"""))
        assertEquals("none", api.volunteerIdentity().status)
        var req = server.takeRequest()
        assertEquals("GET", req.method); assertEquals("/v1/me/volunteer/identity", req.path)
        assertEquals("Bearer tok", req.getHeader("Authorization"))

        val doc = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 1, 2, 3)
        val selfie = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 9, 8)
        server.enqueue(json(200, """{"status":"pending","status_he":"ממתין לבדיקה","required":true,"can_receive_calls":false,"id_last4":"6782"}"""))
        val st = api.submitVolunteerIdentity("123456782", doc, selfie, consent = true)
        assertEquals("pending", st.status); assertEquals("6782", st.idLast4)
        req = server.takeRequest()
        assertEquals("POST", req.method); assertEquals("/v1/me/volunteer/identity", req.path)
        val b = JSONObject(req.body.readUtf8())
        assertEquals("123456782", b.getString("id_number"))
        assertEquals(true, b.get("consent"))
        // base64 רגיל, בלי "data:" (השרת מקבל גם וגם)
        assertFalse(b.getString("id_image").startsWith("data:"))
        assertArrayEquals(doc, java.util.Base64.getDecoder().decode(b.getString("id_image")))
        assertArrayEquals(selfie, java.util.Base64.getDecoder().decode(b.getString("selfie")))
        assertEquals(setOf("id_number", "id_image", "selfie", "consent"), b.keySet())

        server.enqueue(json(200, """{"status":"none","status_he":"לא הועלה","required":true,"can_receive_calls":false}"""))
        assertEquals("none", api.withdrawVolunteerIdentity().status)
        req = server.takeRequest()
        assertEquals("DELETE", req.method); assertEquals("/v1/me/volunteer/identity", req.path)
    }

    @Test fun identityServerErrorsKeepCodeAndHebrewMessage() {
        val errors = listOf(400 to "consent_required", 400 to "invalid_id_number", 400 to "bad_image_type",
            400 to "image_too_small", 413 to "image_too_large", 409 to "id_in_use", 409 to "already_verified")
        for ((status, code) in errors) {
            server.enqueue(json(status, """{"error":"$code","message":"הודעה $code"}"""))
            try {
                api.submitVolunteerIdentity("123456782", ByteArray(4), ByteArray(4), consent = true)
                fail("expected $code")
            } catch (e: ApiException) {
                assertEquals(code, e.code); assertEquals(status, e.status); assertEquals("הודעה $code", e.message)
            }
            server.takeRequest()
        }
    }

    // ---------------- הכנת התמונות ----------------
    @Test fun scaledSizeKeepsAspectAndCapsAt1600() {
        assertEquals(1600 to 1200, IdentityPhoto.scaledSize(4000, 3000))
        assertEquals(1200 to 1600, IdentityPhoto.scaledSize(3000, 4000))
        assertEquals(1600 to 900, IdentityPhoto.scaledSize(4032, 2268))
        assertEquals(1000 to 800, IdentityPhoto.scaledSize(1000, 800))          // לא מגדילים
        assertEquals(1600 to 1600, IdentityPhoto.scaledSize(1600, 1600))
        assertEquals(1600 to 1, IdentityPhoto.scaledSize(9000, 2))               // לפחות פיקסל אחד
        assertEquals(1, IdentityPhoto.sampleSize(3000, 2000))
        assertEquals(2, IdentityPhoto.sampleSize(4000, 3000))
        assertEquals(4, IdentityPhoto.sampleSize(8000, 6000))
        assertEquals(1, IdentityPhoto.sampleSize(1000, 800))
    }

    @Test fun exifAndGpsAreStrippedAndImageStillDecodes() {
        val plain = jpeg(gradient(640, 480), 0.88f)
        assertTrue(IdentityPhoto.metadataSegments(plain).isEmpty())
        assertArrayEquals(plain, IdentityPhoto.stripJpegMetadata(plain))          // JPEG נקי - בלי שינוי
        val dirty = withMetadata(plain)
        assertEquals(listOf("APP1", "COM", "APP13"), IdentityPhoto.metadataSegments(dirty))
        assertTrue(contains(dirty, "Exif".toByteArray()) && contains(dirty, "GPSLatitude".toByteArray()))
        val clean = IdentityPhoto.stripJpegMetadata(dirty)
        assertTrue(IdentityPhoto.metadataSegments(clean).isEmpty())
        assertFalse(contains(clean, "Exif".toByteArray()))
        assertFalse(contains(clean, "GPSLatitude".toByteArray()))
        assertFalse(contains(clean, "Photoshop".toByteArray()))
        assertArrayEquals(plain, clean)                                         // נתוני התמונה עצמם לא השתנו
        val img = Jdk.decode(clean)!!
        assertEquals(640, img.w); assertEquals(480, img.h)
        try { IdentityPhoto.stripJpegMetadata(byteArrayOf(0x89.toByte(), 'P'.code.toByte())); fail("PNG is not JPEG") }
        catch (_: IllegalArgumentException) {}
    }

    @Test fun preparePipelineDownscalesRotatesAndStrips() {
        val src = withMetadata(jpeg(gradient(3600, 2700), 0.95f))
        // orientation 6 = הטלפון צילם לאורך: אחרי הסיבוב 2700x3600 -> 1200x1600
        val codec = JvmCodec(orientation = 6)
        val out = IdentityPhoto.prepare(src, codec)
        assertEquals(2, codec.lastSampleSize)                                  // פענוח חסכוני: 1800x1350 ולא 3600x2700
        assertTrue(IdentityPhoto.isJpeg(out))
        assertTrue(IdentityPhoto.metadataSegments(out).isEmpty())
        assertFalse(contains(out, "GPSLatitude".toByteArray()))
        assertFalse(contains(out, "Exif".toByteArray()))
        val img = Jdk.decode(out)!!
        assertEquals(1200, img.w); assertEquals(1600, img.h)
        assertTrue("${out.size} bytes", out.size in IdentityPhoto.MIN_BYTES..IdentityPhoto.MAX_BYTES)

        // בלי סיבוב, תמונה קטנה מ-1600 - לא מוגדלת
        val small = IdentityPhoto.prepare(jpeg(gradient(1200, 900), 0.95f), JvmCodec(orientation = 1))
        Jdk.decode(small)!!.let { assertEquals(1200, it.w); assertEquals(900, it.h) }
    }

    @Test fun prepareRejectsTinyOrUnreadable() {
        try { IdentityPhoto.prepare(jpeg(Raster(40, 30, IntArray(40 * 30)), 0.88f), JvmCodec(1)); fail() }
        catch (e: IdentityPhoto.PhotoException) { assertTrue(e.message!!.contains("קטנה מדי")) }
        try { IdentityPhoto.prepare("not an image".toByteArray(), JvmCodec(1)); fail() }
        catch (e: IdentityPhoto.PhotoException) { assertNotNull(e.message) }
    }

    // ---------------- עזרים ----------------
    /** תמונה בזיכרון: פיקסלים RGB לפי שורות */
    class Raster(val w: Int, val h: Int, val px: IntArray)

    /**
     * Codec ב-JVM: פענוח/קידוד JPEG עם ImageIO של ה-JDK, הקטנה וסיבוב ב-Kotlin. ה-encoder מעתיק בכוונה EXIF+GPS
     * לפלט (כמו encoder ששומר מטא-דאטה) - כדי לוודא שהצינור מסיר אותם בכל מקרה.
     */
    private class JvmCodec(private val orientation: Int) : IdentityPhoto.Codec<Raster> {
        var lastSampleSize = 0
        override fun bounds(src: ByteArray) = Jdk.decode(src)?.let { it.w to it.h }
        override fun orientation(src: ByteArray) = orientation
        override fun decode(src: ByteArray, sampleSize: Int): Raster? {
            lastSampleSize = sampleSize
            val full = Jdk.decode(src) ?: return null
            return if (sampleSize == 1) full else resize(full, full.w / sampleSize, full.h / sampleSize)
        }
        override fun size(img: Raster) = img.w to img.h
        override fun scaleAndOrient(img: Raster, w: Int, h: Int, orientation: Int): Raster {
            val s = resize(img, w, h)
            if (orientation != 6) return s
            // 90° עם כיוון השעון: (x', y') <- (y', h-1-x')
            val out = IntArray(w * h)
            for (y2 in 0 until w) for (x2 in 0 until h) out[y2 * h + x2] = s.px[(h - 1 - x2) * w + y2]
            return Raster(h, w, out)
        }
        override fun encodeJpeg(img: Raster, quality: Int) = withMetadata(jpeg(img, quality / 100f))

        private fun resize(img: Raster, w: Int, h: Int): Raster {
            val out = IntArray(w * h)
            for (y in 0 until h) {
                val sy = (y.toLong() * img.h / h).toInt()
                for (x in 0 until w) out[y * w + x] = img.px[sy * img.w + (x.toLong() * img.w / w).toInt()]
            }
            return Raster(w, h, out)
        }
    }

    /** ImageIO / BufferedImage דרך reflection: ב-classpath של קומפילציית בדיקות אנדרואיד אין java.awt, אבל ב-JVM יש */
    private object Jdk {
        private val int = Int::class.javaPrimitiveType!!
        private val bi = Class.forName("java.awt.image.BufferedImage")
        private val io = Class.forName("javax.imageio.ImageIO")
        private val writerC = Class.forName("javax.imageio.ImageWriter")
        private val paramC = Class.forName("javax.imageio.ImageWriteParam")
        private val metaC = Class.forName("javax.imageio.metadata.IIOMetadata")
        private val iioC = Class.forName("javax.imageio.IIOImage")

        fun decode(bytes: ByteArray): Raster? {
            val img = runCatching { io.getMethod("read", java.io.InputStream::class.java).invoke(null, ByteArrayInputStream(bytes)) }
                .getOrNull() ?: return null
            val w = bi.getMethod("getWidth").invoke(img) as Int
            val h = bi.getMethod("getHeight").invoke(img) as Int
            val px = bi.getMethod("getRGB", int, int, int, int, IntArray::class.java, int, int)
                .invoke(img, 0, 0, w, h, null, 0, w) as IntArray
            return Raster(w, h, px)
        }

        fun encode(r: Raster, quality: Float): ByteArray {
            val img = bi.getConstructor(int, int, int).newInstance(r.w, r.h, 1 /* TYPE_INT_RGB */)
            bi.getMethod("setRGB", int, int, int, int, IntArray::class.java, int, int).invoke(img, 0, 0, r.w, r.h, r.px, 0, r.w)
            val writer = (io.getMethod("getImageWritersByFormatName", String::class.java).invoke(null, "jpeg") as Iterator<*>).next()
            val param = writerC.getMethod("getDefaultWriteParam").invoke(writer)
            paramC.getMethod("setCompressionMode", int).invoke(param, 2 /* MODE_EXPLICIT */)
            paramC.getMethod("setCompressionQuality", Float::class.javaPrimitiveType).invoke(param, quality)
            val out = ByteArrayOutputStream()
            val ios = io.getMethod("createImageOutputStream", Any::class.java).invoke(null, out)
            writerC.getMethod("setOutput", Any::class.java).invoke(writer, ios)
            val iio = iioC.getConstructor(Class.forName("java.awt.image.RenderedImage"), java.util.List::class.java, metaC)
                .newInstance(img, null, null)
            writerC.getMethod("write", metaC, iioC, paramC).invoke(writer, null, iio, param)
            (ios as java.io.Closeable).close()
            writerC.getMethod("dispose").invoke(writer)
            return out.toByteArray()
        }
    }

    companion object {
        /** תמונה עם פרטים (מעבר צבע + רעש דטרמיניסטי קטן) - לא דחיסה אפסית */
        fun gradient(w: Int, h: Int): Raster {
            val px = IntArray(w * h)
            var seed = 12345
            for (y in 0 until h) for (x in 0 until w) {
                seed = seed * 1103515245 + 12345
                val n = (seed ushr 16) and 7
                val r = (x * 255 / w + n) and 0xFF
                val g = (y * 255 / h + n) and 0xFF
                val b = ((x + y) * 255 / (w + h)) and 0xFF
                px[y * w + x] = (r shl 16) or (g shl 8) or b
            }
            return Raster(w, h, px)
        }

        fun jpeg(img: Raster, quality: Float): ByteArray = Jdk.encode(img, quality)

        /** מוסיף אחרי ה-SOI: APP1 Exif עם תגי GPS, הערה (COM) ו-APP13 Photoshop - כמו צילום טלפון אמיתי */
        fun withMetadata(jpeg: ByteArray): ByteArray {
            fun seg(marker: Int, payload: ByteArray): ByteArray {
                val len = payload.size + 2
                return byteArrayOf(0xFF.toByte(), marker.toByte(), (len shr 8).toByte(), (len and 0xFF).toByte()) + payload
            }
            val exif = "Exif".toByteArray() + byteArrayOf(0, 0) + "MM GPSLatitude 32.0853 GPSLongitude 34.7818 Make Pixel".toByteArray()
            val out = ByteArrayOutputStream()
            out.write(jpeg, 0, 2)
            out.write(seg(0xE1, exif))
            out.write(seg(0xFE, "taken at home".toByteArray()))
            out.write(seg(0xED, "Photoshop 3.0".toByteArray() + byteArrayOf(0)))
            out.write(jpeg, 2, jpeg.size - 2)
            return out.toByteArray()
        }

        fun contains(hay: ByteArray, needle: ByteArray): Boolean {
            outer@ for (i in 0..hay.size - needle.size) {
                for (j in needle.indices) if (hay[i + j] != needle[j]) continue@outer
                return true
            }
            return false
        }
    }
}
