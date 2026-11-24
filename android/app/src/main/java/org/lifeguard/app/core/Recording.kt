package org.lifeguard.app.core

import org.json.JSONArray
import org.json.JSONObject
import org.lifeguard.app.api.RecordingPolicy
import org.lifeguard.app.api.dbl
import org.lifeguard.app.api.lng
import org.lifeguard.app.api.str

/**
 * מתי מקליטים. אין שום מסלול אחר להפעלת המיקרופון: רק אירוע חירום *שלי* שפעיל במכשיר, ורק כשהוא SOS ידני או חירום
 * מאושר, ורק אם מדיניות השרת מאפשרת (record_on_emergency, או discreet בפרופיל ילד), ורק עם הרשאת RECORD_AUDIO שכבר
 * ניתנה. אין הקלטה ברקע מחוץ לחירום ואין הפעלה מרחוק (אין לזה API).
 */
object RecordingGate {
    enum class Decision { RECORD, NO_INCIDENT, NOT_CONFIRMED, POLICY_OFF, POLICY_UNKNOWN, NO_PERMISSION }

    fun decide(incidentActive: Boolean, emergencyConfirmed: Boolean, policy: RecordingPolicy?, micPermission: Boolean): Decision = when {
        !incidentActive -> Decision.NO_INCIDENT
        !emergencyConfirmed -> Decision.NOT_CONFIRMED
        policy == null -> Decision.POLICY_UNKNOWN
        !(policy.recordOnEmergency || policy.discreet) -> Decision.POLICY_OFF
        !micPermission -> Decision.NO_PERMISSION
        else -> Decision.RECORD
    }

    fun reasonHe(d: Decision): String? = when (d) {
        Decision.RECORD, Decision.NO_INCIDENT -> null
        Decision.NOT_CONFIRMED -> "לא מקליטים: זיהוי אוטומטי שעוד לא אושר כחירום"
        Decision.POLICY_OFF -> "הקלטה בחירום כבויה בהגדרות"
        Decision.POLICY_UNKNOWN -> "לא מקליטים: מדיניות ההקלטה עוד לא התקבלה מהשרת"
        Decision.NO_PERMISSION -> "אין הרשאת מיקרופון"
    }
}

/** קטע הקלטה שממתין להעלאה. הקובץ עצמו נשמר מוצפן ביומן הראיות (evidenceKey/blob) - כאן רק מטא-דאטה. */
data class RecordingUpload(
    /** מזהה קבוע לקטע (אידמפוטנטיות בשרת) */
    val clientId: String,
    val evidenceKey: String,
    val blob: String,
    val sha256: String,
    val bytes: Long,
    val mime: String = "audio/mp4",
    val startedAt: Double,
    val endedAt: Double,
    val incidentId: String? = null,
    val attempts: Int = 0,
    val nextAt: Double = 0.0,
    val offlineReported: Boolean = false,
    val lastError: String? = null,
) {
    fun toJson(): JSONObject = JSONObject().put("client_id", clientId).put("evidence_key", evidenceKey).put("blob", blob)
        .put("sha256", sha256).put("bytes", bytes).put("mime", mime).put("started_at", startedAt).put("ended_at", endedAt)
        .put("attempts", attempts).put("next_at", nextAt).put("offline_reported", offlineReported)
        .apply { incidentId?.let { put("incident_id", it) }; lastError?.let { put("last_error", it) } }

    companion object {
        fun parse(o: JSONObject) = RecordingUpload(
            clientId = o.str("client_id") ?: "", evidenceKey = o.str("evidence_key") ?: "", blob = o.str("blob") ?: "",
            sha256 = o.str("sha256") ?: "", bytes = o.lng("bytes") ?: 0, mime = o.str("mime") ?: "audio/mp4",
            startedAt = o.dbl("started_at") ?: 0.0, endedAt = o.dbl("ended_at") ?: 0.0, incidentId = o.str("incident_id"),
            attempts = o.dbl("attempts")?.toInt() ?: 0, nextAt = o.dbl("next_at") ?: 0.0,
            offlineReported = o.optBoolean("offline_reported", false), lastError = o.str("last_error"),
        )

        /** client_id יציב לקטע: נגזר מהיומן ומשם הקובץ (4-80 תווים) */
        fun clientIdFor(evidenceKey: String, blob: String): String =
            "seg-" + Hashes.sha256("$evidenceKey/$blob").take(32)
    }
}

object RecordingUploadPolicy {
    sealed class Outcome {
        object Uploaded : Outcome()
        /** לנסות שוב אחרי backoff (רשת / שרת / עוד לא מאושר) */
        data class Retry(val reason: String) : Outcome()
        /** לא יצליח לעולם: יוצא מהתור + recording_upload_failed */
        data class Drop(val reason: String) : Outcome()
    }

    const val LATE_UPLOAD_SECONDS = 48 * 3600.0

    /** status=null -> שגיאת רשת */
    fun classify(status: Int?, code: String?): Outcome = when {
        status == null -> Outcome.Retry("network")
        status in 200..299 -> Outcome.Uploaded
        status == 409 && code == "not_confirmed" -> Outcome.Retry("not_confirmed")   // אולי הבעלים יאשר/תאשר
        status == 409 && code == "too_late" -> Outcome.Drop("too_late")
        status == 403 && code == "recording_disabled" -> Outcome.Drop("recording_disabled")
        status == 413 -> Outcome.Drop("too_large")
        status == 400 -> Outcome.Drop(code ?: "invalid")
        status == 403 || status == 404 -> Outcome.Drop(code ?: "http_$status")
        else -> Outcome.Retry(code ?: "http_$status")       // 5xx / 429 / 401 (התחברות מחדש)
    }

    /** 30, 60, 120 ... עד 15 דק'; not_confirmed - כל 2 דק' */
    fun backoffSeconds(attempts: Int, reason: String): Double =
        if (reason == "not_confirmed") 120.0 else minOf(900.0, 30.0 * (1 shl (attempts - 1).coerceIn(0, 5)))

    /** קטע ישן מדי (48 שעות אחרי סוף הקטע) - השרת יסרב ממילא */
    fun expired(item: RecordingUpload, now: Double) = now - item.endedAt > LATE_UPLOAD_SECONDS
}

/** תור העלאת הקלטות - מוצפן (TextStore). */
class RecordingUploadQueue(private val store: TextStore) {
    private fun load(): MutableList<RecordingUpload> = runCatching {
        val a = JSONArray(store.read() ?: "[]")
        (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let(RecordingUpload::parse) }.toMutableList()
    }.getOrDefault(ArrayList())

    private fun save(l: List<RecordingUpload>) =
        store.write(if (l.isEmpty()) null else JSONArray().apply { l.forEach { put(it.toJson()) } }.toString())

    @Synchronized fun add(item: RecordingUpload): Boolean {
        val l = load()
        if (l.any { it.clientId == item.clientId }) return false
        l += item; save(l); return true
    }

    @Synchronized fun items(): List<RecordingUpload> = load()

    @Synchronized fun due(now: Double): List<RecordingUpload> = load().filter { it.nextAt <= now }

    @Synchronized fun update(clientId: String, fn: (RecordingUpload) -> RecordingUpload?) {
        val l = load()
        val i = l.indexOfFirst { it.clientId == clientId }
        if (i < 0) return
        val n = fn(l[i])
        if (n == null) l.removeAt(i) else l[i] = n
        save(l)
    }

    /** אחרי ניסיון: Uploaded/Drop -> יוצא; Retry -> attempts+1 ו-nextAt. */
    fun record(clientId: String, outcome: RecordingUploadPolicy.Outcome, now: Double) = update(clientId) { it ->
        when (outcome) {
            is RecordingUploadPolicy.Outcome.Uploaded, is RecordingUploadPolicy.Outcome.Drop -> null
            is RecordingUploadPolicy.Outcome.Retry -> it.copy(attempts = it.attempts + 1, lastError = outcome.reason,
                nextAt = now + RecordingUploadPolicy.backoffSeconds(it.attempts + 1, outcome.reason))
        }
    }
}
