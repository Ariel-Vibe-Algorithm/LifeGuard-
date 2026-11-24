package org.lifeguard.app.core.firstaid

import java.io.File
import java.io.FileNotFoundException
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

/**
 * מקור קבצי התוכן, בנתיבים יחסיים לשורש first_aid/ (למשל "content/first_aid_guides_he.json", "synonyms.json").
 * מקומי בלבד: באפליקציה - assets שנארזו בזמן הבנייה; בבדיקות JVM - התיקייה ../first_aid במאגר.
 * read() זורק FileNotFoundException כשהקובץ חסר, ושגיאה אחרת כשאי אפשר לקרוא / לפענח UTF-8 (כמו open(..., encoding="utf-8")).
 */
interface ContentSource {
    fun read(path: String): String

    companion object {
        /** UTF-8 קפדני (בית לא תקין -> שגיאה, כמו Python) */
        fun decodeUtf8(bytes: ByteArray): String = Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes)).toString()

        fun readStream(s: InputStream): String = s.use { decodeUtf8(it.readBytes()) }
    }
}

class FileContentSource(private val root: File) : ContentSource {
    override fun read(path: String): String {
        val f = File(root, path)
        if (!f.isFile) throw FileNotFoundException(path)
        return ContentSource.decodeUtf8(f.readBytes())
    }
}
