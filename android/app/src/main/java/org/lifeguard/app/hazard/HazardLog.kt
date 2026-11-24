package org.lifeguard.app.hazard

import org.json.JSONArray
import org.json.JSONObject
import org.lifeguard.app.core.TextStore

/**
 * יומן התראות הסכנה במכשיר (המקביל לטבלת hazard_alerts ב-Windows): רשומה לכל אירוע (לא לכל זיהוי).
 * - מוצפן (TextStore = קובץ AES-GCM במפתח Keystore). בלי אודיו ובלי מיקום - רק מטא-דאטה של הזיהוי.
 * - "התראת שווא" נרשמת עם label_strength=user_reported: משוב לא מאומת, לחקירת טעויות ולאימון עתידי בפיקוח.
 *   היומן לא משנה ספים - אין שום מסלול בקוד מהיומן למודל.
 * - שמירה מוגבלת: לכל היותר MAX_RECORDS רשומות ו-RETENTION_DAYS ימים.
 */
class HazardLog(
    private val store: TextStore,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val maxRecords: Int = MAX_RECORDS,
    private val retentionDays: Double = RETENTION_DAYS,
) {
    @Synchronized
    fun list(): List<JSONObject> = load().filter { clock() - it.optDouble("ts", 0.0) <= retentionDays * 86400 }
        .sortedByDescending { it.optDouble("ts", 0.0) }

    /** INSERT OR REPLACE (לפי מזהה האירוע) - גם עדכון חומרה של אותו אירוע מחליף את הרשומה. */
    @Synchronized
    fun recordAlert(alert: HazardAlert, shown: Boolean, notShownReason: String?) {
        val meta = JSONObject()
        for ((k, v) in alert.meta) if (k in META_KEYS) meta.put(k, v ?: JSONObject.NULL)
        val rec = JSONObject()
            .put("id", alert.id).put("ts", alert.firstTs).put("category", alert.category).put("severity", alert.severity)
            .put("confidence", alert.confidence).put("count", alert.count).put("shown", shown)
            .put("not_shown_reason", notShownReason ?: JSONObject.NULL)
            .put("model_version", alert.meta["model_version"] ?: JSONObject.NULL)
            .put("quality", alert.meta["quality"] ?: JSONObject.NULL)
            .put("meta", meta)
            .put("response", alert.response ?: JSONObject.NULL)
            .put("reason", alert.reason ?: JSONObject.NULL)
            .put("label_strength", if (alert.response == "false_alarm") "user_reported" else JSONObject.NULL)
        val all = load().filter { it.optString("id") != alert.id }.toMutableList()
        all += rec
        save(all)
    }

    /** תגובת המשתמש/ת. רק ערכים מוכרים (כמו ב-Python: ValueError -> IllegalArgumentException). */
    @Synchronized
    fun recordResponse(alert: HazardAlert, response: String, reason: String? = null) {
        require(response in HazardAlerts.RESPONSES) { "unknown response: $response" }
        require(reason == null || reason in HazardAlerts.FALSE_REASONS) { "unknown reason: $reason" }
        alert.response = response
        alert.reason = reason
        val all = load()
        val rec = all.firstOrNull { it.optString("id") == alert.id } ?: return
        rec.put("response", response).put("reason", reason ?: JSONObject.NULL)
            .put("label_strength", if (response == "false_alarm") "user_reported" else JSONObject.NULL)
            .put("responded_at", clock())
        save(all)
    }

    /** לחוקר הטעויות: התראות שווא לפי קטגוריה וסיבה (משוב משתמש/ת לא מאומת). */
    fun feedbackSummary(): Map<String, Summary> {
        val by = LinkedHashMap<String, Summary>()
        for (r in list()) {
            val c = by.getOrPut(r.optString("category")) { Summary() }
            c.alerts++
            if (r.optBoolean("shown")) c.shown++
            when (val resp = r.opt("response").takeIf { it != JSONObject.NULL } as? String) {
                "false_alarm" -> {
                    c.falseAlarm++
                    val why = (r.opt("reason") as? String) ?: "unspecified"
                    c.reasons[why] = (c.reasons[why] ?: 0) + 1
                }
                in HazardAlerts.ACTION_RESPONSES -> c.actions++
                else -> {}
            }
        }
        return by
    }

    data class Summary(var alerts: Int = 0, var shown: Int = 0, var falseAlarm: Int = 0, var actions: Int = 0,
                       val reasons: LinkedHashMap<String, Int> = LinkedHashMap())

    private fun load(): MutableList<JSONObject> = runCatching {
        val a = JSONArray(store.read() ?: "[]")
        (0 until a.length()).mapNotNull { a.optJSONObject(it) }.toMutableList()
    }.getOrDefault(ArrayList())

    private fun save(all: List<JSONObject>) {
        val now = clock()
        val kept = all.filter { now - it.optDouble("ts", 0.0) <= retentionDays * 86400 }
            .sortedBy { it.optDouble("ts", 0.0) }.takeLast(maxRecords)
        store.write(if (kept.isEmpty()) null else JSONArray(kept).toString())
    }

    companion object {
        const val MAX_RECORDS = 500
        const val RETENTION_DAYS = 90.0
        /** מה שנשמר מה-meta של הזיהוי (בלי אודיו, בלי מיקום, בלי embeddings) */
        val META_KEYS = setOf("score", "threshold", "model", "model_version", "quality", "near_threshold", "rule")
    }
}
