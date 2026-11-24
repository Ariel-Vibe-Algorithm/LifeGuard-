package org.lifeguard.app.core

import java.io.ByteArrayOutputStream

/**
 * אימות זהות חובה לכל מתנדב/ת (server/volunteer_identity.py): מספר ת"ז + צילום התעודה + סלפי, הסכמה מפורשת.
 * כאן רק לוגיקה טהורה (נבדקת ב-JVM): ספרת ביקורת, הכנת התמונות (הקטנה + JPEG מחדש + הסרת מטא-דאטה), טקסטים.
 */
object IsraeliId {
    /**
     * ספרת ביקורת של ת"ז ישראלית - זהה ל-valid_israeli_id בשרת: עד 9 ספרות (משלימים אפסים משמאל), רק ספרות
     * אחרי הסרת רווחים בקצוות, לא 000000000. מחזיר את 9 הספרות או null.
     */
    fun valid(value: String?): String? {
        val t = (value ?: "").trim()
        if (t.isEmpty() || t.length > 9 || !t.all { it in '0'..'9' }) return null
        val s = t.padStart(9, '0')
        if (s == "000000000") return null
        var total = 0
        for ((i, ch) in s.withIndex()) {
            val d = (ch - '0') * (if (i % 2 == 0) 1 else 2)
            total += if (d > 9) d - 9 else d
        }
        return if (total % 10 == 0) s else null
    }
}

/**
 * הכנת צילום לאימות (כמו prepare_photo ב-Windows ו-toJpegB64 ב-PWA): מסובבים לפי EXIF, מקטינים כך שהצלע הארוכה
 * <= 1600 פיקסלים, ומקודדים מחדש ל-JPEG באיכות 88. המקור לא נשלח לעולם: הקידוד מחדש לא מעתיק EXIF (כולל מיקום GPS
 * של הצילום), ובנוסף stripJpegMetadata מסירה כל מקטע APP1-APP15 / COM שנשאר (הגנה כפולה).
 */
object IdentityPhoto {
    const val MAX_SIDE = 1600
    const val JPEG_QUALITY = 88
    const val MIN_BYTES = 10_000          // volunteer_identity.MIN_IMAGE_BYTES
    const val MAX_BYTES = 3_000_000       // volunteer_identity.MAX_IMAGE_BYTES
    /** קובץ מקור גדול מזה לא נקרא לזיכרון (צילום רגיל: 2-12MB) */
    const val MAX_SOURCE_BYTES = 40_000_000

    /** גודל היעד: הצלע הארוכה עד maxSide, יחס נשמר (Math.round כמו ב-PWA). תמונה קטנה - בלי הגדלה. */
    fun scaledSize(w: Int, h: Int, maxSide: Int = MAX_SIDE): Pair<Int, Int> {
        require(w > 0 && h > 0) { "bad size" }
        val m = maxOf(w, h)
        if (m <= maxSide) return w to h
        val k = maxSide.toDouble() / m
        return maxOf(1, Math.round(w * k).toInt()) to maxOf(1, Math.round(h * k).toInt())
    }

    /** inSampleSize (חזקת 2) לפענוח חסכוני בזיכרון: הגדול ביותר שעדיין משאיר צלע ארוכה >= maxSide. */
    fun sampleSize(w: Int, h: Int, maxSide: Int = MAX_SIDE): Int {
        var s = 1
        while (maxOf(w, h) / (s * 2) >= maxSide) s *= 2
        return s
    }

    fun isJpeg(b: ByteArray) = b.size >= 3 && b[0] == 0xFF.toByte() && b[1] == 0xD8.toByte() && b[2] == 0xFF.toByte()

    /** אחרי סיבוב EXIF של 90/270 מעלות (orientation 5-8) הרוחב והגובה מתחלפים */
    fun swapsAxes(orientation: Int) = orientation in 5..8

    /**
     * מסיר מ-JPEG את כל מקטעי המטא-דאטה שלפני תחילת הנתונים (SOS): APP1-APP15 (EXIF, GPS, XMP, ICC...) ו-COM.
     * APP0 נשמר רק אם הוא JFIF. שאר המקטעים (טבלאות, SOF) והנתונים - כפי שהם.
     */
    fun stripJpegMetadata(jpeg: ByteArray): ByteArray {
        require(isJpeg(jpeg)) { "not a JPEG" }
        val out = ByteArrayOutputStream(jpeg.size)
        out.write(jpeg, 0, 2)                                   // SOI
        var i = 2
        while (i < jpeg.size) {
            if (jpeg[i] != 0xFF.toByte()) throw IllegalArgumentException("corrupt JPEG at $i")
            var j = i
            while (j < jpeg.size && jpeg[j] == 0xFF.toByte()) j++  // בתי מילוי FF
            if (j >= jpeg.size) break
            val marker = jpeg[j].toInt() and 0xFF
            if (marker == 0xD9 || marker == 0x01 || marker in 0xD0..0xD7) {   // בלי אורך
                out.write(0xFF); out.write(marker); i = j + 1
                if (marker == 0xD9) break
                continue
            }
            if (j + 2 >= jpeg.size) throw IllegalArgumentException("truncated JPEG")
            val len = ((jpeg[j + 1].toInt() and 0xFF) shl 8) or (jpeg[j + 2].toInt() and 0xFF)
            val end = j + 1 + len
            if (len < 2 || end > jpeg.size) throw IllegalArgumentException("bad segment length")
            if (marker == 0xDA) {                                 // SOS: מכאן נתוני התמונה עד הסוף
                out.write(0xFF); out.write(jpeg, j, jpeg.size - j)
                return out.toByteArray()
            }
            val drop = marker in 0xE1..0xEF || marker == 0xFE ||
                (marker == 0xE0 && !(len >= 7 && String(jpeg, j + 3, 4, Charsets.US_ASCII) == "JFIF"))
            if (!drop) { out.write(0xFF); out.write(jpeg, j, end - j) }
            i = end
        }
        return out.toByteArray()
    }

    /** מקטעי מטא-דאטה שנשארו (לבדיקות ולאימות לפני שליחה): "APP1", "COM"... ריק = נקי. */
    fun metadataSegments(jpeg: ByteArray): List<String> {
        if (!isJpeg(jpeg)) return emptyList()
        val found = ArrayList<String>()
        var i = 2
        while (i + 3 < jpeg.size) {
            if (jpeg[i] != 0xFF.toByte()) break
            var j = i
            while (j < jpeg.size && jpeg[j] == 0xFF.toByte()) j++
            if (j + 2 >= jpeg.size) break
            val marker = jpeg[j].toInt() and 0xFF
            if (marker == 0xDA || marker == 0xD9) break
            if (marker == 0x01 || marker in 0xD0..0xD7) { i = j + 1; continue }
            val len = ((jpeg[j + 1].toInt() and 0xFF) shl 8) or (jpeg[j + 2].toInt() and 0xFF)
            if (marker in 0xE1..0xEF) found += "APP${marker - 0xE0}"
            if (marker == 0xFE) found += "COM"
            if (marker == 0xE0 && !(len >= 7 && String(jpeg, j + 3, 4, Charsets.US_ASCII) == "JFIF")) found += "APP0"
            i = j + 1 + len
        }
        return found
    }

    /** פענוח/קידוד - באנדרואיד Bitmap (IdentityPhotoAndroid), בבדיקות JVM ImageIO. B = סוג התמונה בזיכרון. */
    interface Codec<B> {
        /** רוחב/גובה בלי לפענח (null = לא תמונה) */
        fun bounds(src: ByteArray): Pair<Int, Int>?
        /** EXIF orientation (1-8; 1 = רגיל) */
        fun orientation(src: ByteArray): Int
        fun decode(src: ByteArray, sampleSize: Int): B?
        fun size(img: B): Pair<Int, Int>
        /** מקטין ל-w×h ואז מסובב/משקף לפי orientation */
        fun scaleAndOrient(img: B, w: Int, h: Int, orientation: Int): B
        fun encodeJpeg(img: B, quality: Int): ByteArray
        fun release(img: B) {}
    }

    class PhotoException(message: String) : Exception(message)

    /** הצינור המלא: מקור (כל פורמט שהמערכת יודעת לפענח) -> JPEG נקי, צלע ארוכה <= 1600. */
    fun <B> prepare(src: ByteArray, codec: Codec<B>): ByteArray {
        if (src.size > MAX_SOURCE_BYTES) throw PhotoException("הקובץ גדול מדי")
        val (w0, h0) = codec.bounds(src) ?: throw PhotoException("לא ניתן לקרוא את התמונה")
        val orientation = codec.orientation(src)
        val img = codec.decode(src, sampleSize(w0, h0)) ?: throw PhotoException("לא ניתן לקרוא את התמונה")
        val (w, h) = codec.size(img)
        val (tw, th) = scaledSize(w, h)
        val ready = codec.scaleAndOrient(img, tw, th, orientation)
        val jpeg = try {
            codec.encodeJpeg(ready, JPEG_QUALITY)
        } finally {
            if (ready !== img) codec.release(ready)
            codec.release(img)
        }
        val clean = stripJpegMetadata(jpeg)
        if (clean.size < MIN_BYTES) throw PhotoException("התמונה קטנה מדי - צריך צילום ברור")
        if (clean.size > MAX_BYTES) throw PhotoException("הקובץ גדול מדי (עד 3MB)")
        return clean
    }
}

/** טקסטים ובדיקות הטופס - זהים ל-PWA (identityBlock) ול-Windows (IdentityUploadWindow). */
object IdentityForm {
    const val NOT_VERIFIED_HE = "לא יישלחו אליך קריאות עד שהזהות תאומת"
    const val CONSENT_HE = "אני מאשר/ת שימוש בתמונות ובמספר הת\"ז לאימות זהותי כמתנדב/ת בלבד"
    const val EXPLAIN_HE = "כל מתנדב/ת - גם מי שכבר רשום/ה - צריך/ה לאמת זהות כדי לקבל קריאות: מספר ת\"ז, צילום של " +
        "התעודה וסלפי. מנהל/ת בודק/ת ידנית. התמונות נשמרות בשרת מוצפנות ונמחקות מיד אחרי ההחלטה (או אחרי 30 יום). " +
        "מספר הת\"ז עצמו לא נשמר - רק 4 ספרות אחרונות. התמונות מוקטנות ונשלחות בלי פרטי הצילום (כולל מיקום)."
    const val PENDING_HE = "הבקשה ממתינה לבדיקה ידנית. אחרי ההחלטה התמונות נמחקות מהשרת."
    const val BAD_ID_HE = "מספר תעודת הזהות לא תקין"
    const val MISSING_PHOTOS_HE = "צריך צילום של תעודת הזהות וגם סלפי"
    const val NO_CONSENT_HE = "צריך לאשר את השימוש בתמונות לאימות"

    /** null = אפשר לשלוח; אחרת הודעת שגיאה (באותו סדר בדיקות כמו ב-PWA) */
    fun validate(idNumber: String, hasIdPhoto: Boolean, hasSelfie: Boolean, consent: Boolean): String? = when {
        IsraeliId.valid(idNumber) == null -> BAD_ID_HE
        !hasIdPhoto || !hasSelfie -> MISSING_PHOTOS_HE
        !consent -> NO_CONSENT_HE
        else -> null
    }

    /** השורה האדומה מתחת לסטטוס: עד האימות אין קריאות; בדחייה - גם הסיבה */
    fun warningLine(status: String, note: String?): String? {
        if (status == "verified") return null
        return NOT_VERIFIED_HE + if (status == "rejected" && !note.isNullOrBlank()) " · סיבת הדחייה: $note" else ""
    }
}
