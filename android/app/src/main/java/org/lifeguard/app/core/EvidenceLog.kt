package org.lifeguard.app.core

import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** הצפנה/פענוח של בלוק. במכשיר: מפתח ב-Android Keystore; בבדיקות JVM: מפתח תוכנה. */
interface Sealer {
    fun seal(plain: ByteArray): ByteArray
    fun open(sealed: ByteArray): ByteArray
}

/**
 * AES-256-GCM: פלט = IV (12 בתים) + ciphertext + tag. ה-IV נוצר ע"י ה-Cipher (חובה עם מפתחות Keystore).
 * keyProvider נקרא בכל פעולה (מפתח Keystore לא יוצא מהחומרה - מחזירים רק handle).
 */
class AesGcmSealer(private val keyProvider: () -> SecretKey) : Sealer {
    override fun seal(plain: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, keyProvider())
        return c.iv + c.doFinal(plain)
    }

    override fun open(sealed: ByteArray): ByteArray {
        require(sealed.size > 12 + 16) { "too short" }
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, keyProvider(), GCMParameterSpec(128, sealed, 0, 12))
        return c.doFinal(sealed, 12, sealed.size - 12)
    }
}

object Hashes {
    fun sha256(b: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }
    fun sha256(s: String): String = sha256(s.toByteArray(Charsets.UTF_8))
    val GENESIS = "0".repeat(64)
}

/**
 * רשומה ביומן. data נשמר כמחרוזת JSON כפי שנכתבה (לא מסודר מחדש) - כך ה-hash ניתן לשחזור בכל מקום:
 * hash = sha256( "seq|at|type|data|prev" ) ב-UTF-8, prev = hash הרשומה הקודמת (בראשונה: 64 אפסים).
 */
data class EvidenceEntry(val seq: Long, val atMs: Long, val type: String, val data: String, val prev: String, val hash: String) {
    fun toJson(): JSONObject = JSONObject().put("seq", seq).put("at", atMs).put("type", type).put("data", data)
        .put("prev", prev).put("hash", hash)

    fun dataJson(): JSONObject = runCatching { JSONObject(data) }.getOrDefault(JSONObject())

    fun computeHash(): String = Companion.computeHash(seq, atMs, type, data, prev)

    companion object {
        fun computeHash(seq: Long, at: Long, type: String, data: String, prev: String) =
            Hashes.sha256("$seq|$at|$type|$data|$prev")

        fun parse(o: JSONObject) = EvidenceEntry(o.getLong("seq"), o.getLong("at"), o.getString("type"),
            o.getString("data"), o.getString("prev"), o.getString("hash"))
    }
}

data class EvidenceMeta(
    val key: String,
    val createdAtMs: Long,
    val incidentId: String?,
    val mode: String?,
    val count: Long,
    val head: String,
    /** null = פתוח; resolved | cancelled | false_alarm */
    val closedAs: String?,
    val closedAtMs: Long?,
) {
    fun toJson(): JSONObject = JSONObject().put("key", key).put("created_at", createdAtMs).put("count", count)
        .put("head", head).apply {
            incidentId?.let { put("incident_id", it) }; mode?.let { put("mode", it) }
            closedAs?.let { put("closed_as", it) }; closedAtMs?.let { put("closed_at", it) }
        }

    companion object {
        fun parse(o: JSONObject) = EvidenceMeta(
            key = o.getString("key"), createdAtMs = o.optLong("created_at"),
            incidentId = if (o.has("incident_id")) o.getString("incident_id") else null,
            mode = if (o.has("mode")) o.getString("mode") else null,
            count = o.optLong("count"), head = o.optString("head", Hashes.GENESIS),
            closedAs = if (o.has("closed_as")) o.getString("closed_as") else null,
            closedAtMs = if (o.has("closed_at")) o.getLong("closed_at") else null,
        )
    }
}

data class VerifyResult(val ok: Boolean, val count: Int, val head: String, val problems: List<String>)

/**
 * יומן ראיות מקומי לכל אירוע: append-only, שרשרת hash, מוצפן במנוחה.
 *
 * מבנה: root/<key>/log  - שורה לכל רשומה = base64( seal( JSON של הרשומה ) )
 *       root/<key>/meta - base64( seal( EvidenceMeta ) ): מונה + hash אחרון (לזיהוי קטיעה של סוף היומן)
 *       root/<key>/<name>.bin - קובץ מוצפן (קטע שמע); ה-SHA-256 של התוכן המקורי חתום ברשומה ביומן.
 *
 * מגבלה: מי שיכול לכתוב לאחסון הפרטי של האפליקציה (root) ולהחליף גם log וגם meta לגרסה קודמת עקבית - לא יתגלה מקומית.
 * שינוי/מחיקה/הוספה של רשומה, החלפת קובץ שמע, או קטיעה בלי עדכון meta - מתגלים ב-verify().
 */
class EvidenceLog(private val root: File, private val sealer: Sealer, private val clock: () -> Long = System::currentTimeMillis) {
    private val lock = Any()
    private val b64e = Base64.getEncoder()
    private val b64d = Base64.getDecoder()

    private fun dir(key: String): File {
        require(key.isNotEmpty() && key.length <= 80 && key.all { it.isLetterOrDigit() || it == '-' || it == '_' }) { "bad key" }
        return File(root, key)
    }

    fun exists(key: String) = File(dir(key), "log").exists()

    /** פותח יומן לאירוע (אם לא קיים) עם רשומת פתיחה. */
    fun start(key: String, mode: String?, data: JSONObject = JSONObject()): EvidenceEntry? = synchronized(lock) {
        if (exists(key)) return null
        dir(key).mkdirs()
        writeMeta(EvidenceMeta(key, clock(), null, mode, 0, Hashes.GENESIS, null, null))
        append(key, "incident_start", data)
    }

    fun append(key: String, type: String, data: JSONObject = JSONObject()): EvidenceEntry = synchronized(lock) {
        val d = dir(key)
        if (!File(d, "log").exists()) {
            d.mkdirs()
            if (!File(d, "meta").exists()) writeMeta(EvidenceMeta(key, clock(), null, null, 0, Hashes.GENESIS, null, null))
        }
        val (lastSeq, lastHash) = tail(key)
        val seq = lastSeq + 1
        val at = clock()
        val ds = data.toString()
        val e = EvidenceEntry(seq, at, type, ds, lastHash, EvidenceEntry.computeHash(seq, at, type, ds, lastHash))
        val line = b64e.encodeToString(sealer.seal(e.toJson().toString().toByteArray(Charsets.UTF_8))) + "\n"
        FileOutputStream(File(d, "log"), true).use { out ->
            out.write(line.toByteArray(Charsets.US_ASCII)); out.flush(); runCatching { out.fd.sync() }
        }
        val m = meta(key) ?: EvidenceMeta(key, at, null, null, 0, Hashes.GENESIS, null, null)
        writeMeta(m.copy(count = seq + 1, head = e.hash))
        e
    }

    /** הרשומה האחרונה (seq, hash) - מתוך הקובץ עצמו (המקור), לא מה-meta. */
    private fun tail(key: String): Pair<Long, String> {
        val f = File(dir(key), "log")
        if (!f.exists()) return -1L to Hashes.GENESIS
        val last = f.readLines(Charsets.US_ASCII).lastOrNull { it.isNotBlank() } ?: return -1L to Hashes.GENESIS
        val e = EvidenceEntry.parse(JSONObject(String(sealer.open(b64d.decode(last)), Charsets.UTF_8)))
        return e.seq to e.hash
    }

    fun setIncidentId(key: String, incidentId: String) {
        synchronized(lock) { meta(key)?.let { if (it.incidentId != incidentId) writeMeta(it.copy(incidentId = incidentId)) } }
    }

    /** סגירה: נרשמת ביומן וב-meta. closedAs: resolved | cancelled | false_alarm */
    fun close(key: String, closedAs: String, data: JSONObject = JSONObject()) {
        synchronized(lock) {
            if (!exists(key)) return
            append(key, "incident_closed_local", data.put("closed_as", closedAs))
            meta(key)?.let { writeMeta(it.copy(closedAs = closedAs, closedAtMs = clock())) }
        }
    }

    fun meta(key: String): EvidenceMeta? {
        val f = File(dir(key), "meta")
        if (!f.exists()) return null
        return runCatching {
            EvidenceMeta.parse(JSONObject(String(sealer.open(b64d.decode(f.readText(Charsets.US_ASCII).trim())), Charsets.UTF_8)))
        }.getOrNull()
    }

    private fun writeMeta(m: EvidenceMeta) {
        val d = dir(m.key)
        val tmp = File(d, "meta.tmp")
        tmp.writeText(b64e.encodeToString(sealer.seal(m.toJson().toString().toByteArray(Charsets.UTF_8))), Charsets.US_ASCII)
        if (!tmp.renameTo(File(d, "meta"))) { File(d, "meta").delete(); tmp.renameTo(File(d, "meta")) }
    }

    /** כל היומנים (לפי meta; בלי לפענח רשומות ובלי רישום גישה). */
    fun list(): List<EvidenceMeta> =
        (root.listFiles()?.filter { it.isDirectory && File(it, "log").exists() } ?: emptyList())
            .mapNotNull { runCatching { meta(it.name) }.getOrNull() ?: EvidenceMeta(it.name, it.lastModified(), null, null, -1,
                Hashes.GENESIS, null, null) }
            .sortedByDescending { it.createdAtMs }

    /**
     * קריאת הרשומות לצפייה/ייצוא. כל קריאה נרשמת קודם ביומן עצמו (רשומת access) - גם היא חלק מהשרשרת.
     * purpose: view | export
     */
    fun open(key: String, purpose: String, extra: JSONObject = JSONObject()): List<EvidenceEntry> = synchronized(lock) {
        append(key, "access", extra.put("action", purpose))
        entries(key).mapNotNull { it.getOrNull() }
    }

    /** פענוח כל השורות (בלי רישום גישה - לשימוש פנימי/אימות). */
    private fun entries(key: String): List<Result<EvidenceEntry>> {
        val f = File(dir(key), "log")
        if (!f.exists()) return emptyList()
        return f.readLines(Charsets.US_ASCII).filter { it.isNotBlank() }.map { line ->
            runCatching { EvidenceEntry.parse(JSONObject(String(sealer.open(b64d.decode(line.trim())), Charsets.UTF_8))) }
        }
    }

    fun verify(key: String): VerifyResult = synchronized(lock) {
        val problems = ArrayList<String>()
        val rows = entries(key)
        var prev = Hashes.GENESIS
        var expectSeq = 0L
        var head = Hashes.GENESIS
        val good = ArrayList<EvidenceEntry>()
        rows.forEachIndexed { i, r ->
            val e = r.getOrNull()
            if (e == null) { problems += "שורה ${i + 1}: פענוח נכשל - השורה שונתה או נפגמה"; prev = "?"; expectSeq++; return@forEachIndexed }
            if (e.seq != expectSeq) problems += "שורה ${i + 1}: מספר רץ ${e.seq} במקום $expectSeq (רשומה נמחקה/נוספה)"
            if (prev != "?" && e.prev != prev) problems += "שורה ${i + 1}: השרשור לרשומה הקודמת שבור"
            if (e.computeHash() != e.hash) problems += "שורה ${i + 1}: ה-hash לא תואם לתוכן (התוכן שונה)"
            prev = e.hash; head = e.hash; expectSeq = e.seq + 1
            good += e
        }
        val m = meta(key)
        if (m == null) problems += "קובץ ה-meta חסר או לא ניתן לפענוח"
        else if (m.count >= 0) {
            when {
                m.count > rows.size -> problems += "חסרות ${m.count - rows.size} רשומות בסוף היומן (קטיעה)"
                m.count > 0 && good.getOrNull((m.count - 1).toInt())?.hash != m.head ->
                    problems += "ה-hash האחרון שנשמר לא תואם ליומן"
            }
            // m.count < rows.size: קריסה בין כתיבת השורה לעדכון ה-meta - לא שינוי זדוני
        }
        // קבצים מצורפים: ה-SHA-256 של התוכן המקורי חייב להתאים לרשומה
        for (e in good.filter { it.type == "audio_segment" }) {
            val d = e.dataJson()
            val name = d.optString("file"); val sha = d.optString("sha256")
            val bytes = runCatching { readBlob(key, name) }.getOrNull()
            if (bytes == null) problems += "קובץ $name חסר או לא ניתן לפענוח"
            else if (Hashes.sha256(bytes) != sha) problems += "קובץ $name שונה (SHA-256 לא תואם)"
        }
        VerifyResult(problems.isEmpty(), rows.size, head, problems)
    }

    /** מצפין קובץ (קטע שמע) לתוך היומן, מוחק את המקור, ורושם את ה-SHA-256 שלו בשרשרת. */
    fun sealFile(key: String, plain: File, kind: String, extra: JSONObject = JSONObject()): EvidenceEntry = synchronized(lock) {
        val bytes = plain.readBytes()
        val sha = Hashes.sha256(bytes)
        val d = dir(key).also { it.mkdirs() }
        val n = (d.listFiles()?.count { it.name.endsWith(".bin") } ?: 0) + 1
        val name = "${kind}_%03d.bin".format(n)
        File(d, name).writeBytes(sealer.seal(bytes))
        plain.delete()
        append(key, "${kind}_segment", extra.put("file", name).put("sha256", sha).put("bytes", bytes.size))
    }

    fun readBlob(key: String, name: String): ByteArray {
        require(name.matches(Regex("[A-Za-z0-9_]+\\.bin"))) { "bad name" }
        return sealer.open(File(dir(key), name).readBytes())
    }

    fun delete(key: String): Boolean = synchronized(lock) { dir(key).deleteRecursively() }
}
