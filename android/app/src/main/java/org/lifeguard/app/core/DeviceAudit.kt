package org.lifeguard.app.core

import org.json.JSONArray
import org.json.JSONObject
import org.lifeguard.app.api.dbl
import org.lifeguard.app.api.obj
import org.lifeguard.app.api.str
import java.io.File
import java.util.UUID

/** אחסון טקסט אחד (תור). במכשיר: קובץ מוצפן AES-GCM במפתח Keystore; בבדיקות: מפתח תוכנה / זיכרון. */
interface TextStore {
    fun read(): String?
    fun write(value: String?)
}

class MemoryTextStore(private var v: String? = null) : TextStore {
    override fun read() = v
    override fun write(value: String?) { v = value }
}

/**
 * קובץ מוצפן (Sealer = AES-256-GCM). כתיבה אטומית (קובץ זמני + rename). קובץ שלא ניתן לפענח (מפתח אבד) = ריק.
 */
class SealedFileTextStore(private val file: File, private val sealer: Sealer) : TextStore {
    @Synchronized
    override fun read(): String? {
        if (!file.exists()) return null
        return runCatching { String(sealer.open(file.readBytes()), Charsets.UTF_8) }.getOrNull()
    }

    @Synchronized
    override fun write(value: String?) {
        if (value == null) { file.delete(); return }
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeBytes(sealer.seal(value.toByteArray(Charsets.UTF_8)))
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
    }
}

/** אירוע ביקורת שנרשם במכשיר (POST /v1/me/audit). device_ts = שעון המכשיר (שניות epoch), לא מאומת. */
data class DeviceAuditEvent(
    val eventId: String,
    val deviceTs: Double,
    val action: String,
    /** completed | failed | pending */
    val result: String,
    val details: JSONObject,
    val incidentId: String?,
) {
    fun toJson(): JSONObject = JSONObject().put("event_id", eventId).put("device_ts", deviceTs).put("action", action)
        .put("result", result).put("details", details).apply { incidentId?.let { put("incident_id", it) } }

    companion object {
        fun parse(o: JSONObject) = DeviceAuditEvent(o.str("event_id") ?: "", o.dbl("device_ts") ?: 0.0,
            o.str("action") ?: "", o.str("result") ?: "completed", o.obj("details") ?: JSONObject(), o.str("incident_id"))
    }
}

/**
 * תור לא-מקוון של אירועי ביקורת מהמכשיר. מוצפן (TextStore), event_id קבוע לכל אירוע -> שליחה חוזרת אחרי ניתוק
 * לא יוצרת כפילות בשרת (השרת אידמפוטנטי לפי event_id). אירוע יוצא מהתור רק אחרי שהשרת אישר את האצווה.
 */
class DeviceAuditQueue(
    private val store: TextStore,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val idGen: () -> String = { "a" + UUID.randomUUID().toString().replace("-", "") },
    val maxEvents: Int = 1000,
) {
    data class FlushResult(val sent: Int, val remaining: Int, val error: Exception?)

    private fun load(): MutableList<DeviceAuditEvent> {
        val s = store.read() ?: return ArrayList()
        return runCatching {
            val a = JSONArray(s)
            (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let(DeviceAuditEvent::parse) }.toMutableList()
        }.getOrDefault(ArrayList())
    }

    private fun save(list: List<DeviceAuditEvent>) =
        store.write(if (list.isEmpty()) null else JSONArray().apply { list.forEach { put(it.toJson()) } }.toString())

    /** מחזיר את האירוע שנוסף. פעולה/מזהה לא חוקיים -> IllegalArgumentException (באג בקוד, לא קלט משתמש). */
    @Synchronized
    fun add(action: String, result: String = "completed", details: JSONObject = JSONObject(), incidentId: String? = null,
            eventId: String? = null, deviceTs: Double? = null): DeviceAuditEvent {
        require(action in ACTIONS) { "unknown audit action $action" }
        require(result in RESULTS) { "bad result $result" }
        val id = eventId ?: idGen()
        require(validId(id)) { "bad event id" }
        // details: עד 10 שדות, ערכים קצרים (השרת חותך ל-200 תווים)
        val d = JSONObject()
        details.keys().asSequence().take(10).forEach { k -> d.put(k, details.opt(k)?.toString()?.take(200)) }
        val e = DeviceAuditEvent(id, deviceTs ?: clock(), action, result, d, incidentId?.take(60))
        val list = load()
        if (list.any { it.eventId == id }) return list.first { it.eventId == id }       // אידמפוטנטי גם מקומית
        list += e
        // תקרה (1000 אירועים בלי קליטה - לא צפוי בפועל): נשארים הכי חדשים
        while (list.size > maxEvents) list.removeAt(0)
        save(list)
        return e
    }

    @Synchronized fun pending(): List<DeviceAuditEvent> = load()

    /**
     * send(events JSON) -> מספר שהשרת קיבל (או זורק IOException/ApiException). אצוות של batchSize.
     * הצלחה -> האצווה יוצאת מהתור (גם אירועים שהשרת דילג עליהם כלא חוקיים - אין טעם לשלוח שוב).
     * isPermanent(e)=true (למשל 400) -> האצווה נזרקת כדי לא לחסום את התור לעד.
     */
    fun flush(batchSize: Int = 50, isPermanent: (Exception) -> Boolean = { false }, send: (JSONArray) -> Int): FlushResult {
        var sent = 0
        while (true) {
            val batch = synchronized(this) { load().take(batchSize) }
            if (batch.isEmpty()) return FlushResult(sent, 0, null)
            val arr = JSONArray().apply { batch.forEach { put(it.toJson()) } }
            try {
                send(arr)
            } catch (e: Exception) {
                if (!isPermanent(e)) return FlushResult(sent, pending().size, e)
            }
            synchronized(this) {
                val ids = batch.map { it.eventId }.toSet()
                save(load().filterNot { it.eventId in ids })
            }
            sent += batch.size
        }
    }

    companion object {
        val ACTIONS = setOf("recording_started", "recording_stopped", "recording_stored_offline", "recording_upload_failed",
            "sos_activated_offline", "false_alarm_cancelled", "live_location_paused_offline", "evidence_exported")
        val RESULTS = setOf("completed", "failed", "pending")
        private val ID_RE = Regex("^[A-Za-z0-9_-]{8,64}$")
        fun validId(id: String) = ID_RE.matches(id)
    }
}
