package org.lifeguard.app.core

import org.lifeguard.app.api.PrivacyEvent
import org.lifeguard.app.api.str
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * טקסטים ליומן הפרטיות. עיקרון: גישה למיקום מתוארת כ"נמסר לאפליקציה של X" - אף פעם לא "נצפה"/"ראה"
 * (השרת יודע רק שהנתון נמסר לאפליקציה, לא שמישהו הסתכל במסך).
 */
object PrivacyHistoryText {
    enum class Result { COMPLETED, FAILED, PENDING }

    /** סוג (קידומת action בשרת) -> תווית. "" = הכל. */
    val TYPE_FILTERS: List<Pair<String, String>> = listOf(
        "" to "הכל",
        "location_access" to "מסירת מיקום",
        "permission_" to "שינויי הרשאות",
        "recording_" to "הקלטות",
        "emergency_" to "הפעלת חירום",
        "guardian_" to "הורה וילד",
        "sos_" to "SOS בלי קליטה",
        "false_alarm" to "אזעקות שווא",
        "evidence_exported" to "ייצוא תיעוד",
        "privacy_report" to "ייצוא דוח פרטיות",
    )

    fun result(e: PrivacyEvent): Result = when (e.effectiveResult) {
        "failed" -> Result.FAILED
        "pending" -> Result.PENDING
        else -> Result.COMPLETED
    }

    fun resultHe(r: Result) = when (r) {
        Result.COMPLETED -> "הושלם"
        Result.FAILED -> "נכשל / נחסם"
        Result.PENDING -> "ממתין"
    }

    private fun who(e: PrivacyEvent) = e.actorDisplay ?: e.actor ?: "?"
    private fun to(e: PrivacyEvent) = e.recipientDisplay ?: e.recipient ?: "?"

    private val MODE_HE = mapOf("always" to "תמיד", "emergency" to "רק בחירום", "off" to "כבוי")
    private val VIA_HE = mapOf("always" to "שיתוף רציף", "emergency" to "אירוע חירום",
        "volunteer_assigned" to "מתנדב/ת משויך/ת לאירוע", "volunteer_nearby_approx" to "התראה למתנדבים בקרבת מקום (מיקום משוער)")

    /** תיאור קצר בעברית. me = שם המשתמש שלי (כדי לכתוב "את/ה" במקום השם). */
    fun describe(e: PrivacyEvent, me: String?): String {
        val d = e.details
        val iAmActor = e.actor != null && e.actor == me
        val actor = if (iAmActor) "את/ה" else who(e)
        return when (e.action) {
            "location_access" -> if (result(e) == Result.FAILED) "בקשה למיקום שלך מ${who(e)} נחסמה (אין הרשאה)"
            else "המיקום שלך נמסר לאפליקציה של ${to(e)}" + (VIA_HE[e.via]?.let { " · $it" } ?: "")
            "permission_location_mode" -> "שיתוף המיקום עם ${to(e)} שונה מ\"${MODE_HE[d.str("from")] ?: d.str("from") ?: "?"}\" " +
                "ל\"${MODE_HE[d.str("to")] ?: d.str("to") ?: "?"}\""
            "permission_recording_access" -> "גישה להקלטות ל${to(e)}: ${if (d.str("to") == "true") "אופשרה" else "בוטלה"}"
            "permission_sharing_paused" -> "כל שיתוף המיקום הרציף הושהה"
            "permission_sharing_resumed" -> "שיתוף המיקום חודש"
            "permission_recording" -> "הקלטה בחירום ${if (d.str("record_on_emergency") == "true") "הופעלה" else "כובתה"}"
            "permission_discreet_recording" -> "$actor: ${if (d.str("consent") == "true") "הסכמה" else "ביטול הסכמה"} להקלטה דיסקרטית (${partyHe(d.str("party"))})"
            "emergency_activated" -> "הופעל חירום (${triggerHe(d.str("trigger"))}, ${activationHe(d.str("activation"))})"
            "emergency_confirmed" -> "אישרת שזה חירום - נשלחו מתנדבים"
            "emergency_cancelled" -> "החירום בוטל (אזעקת שווא)"
            "emergency_resolved" -> "החירום הסתיים"
            "recording_uploaded" -> "הקלטה הועלתה (${d.str("bytes") ?: "?"} בתים)"
            "recording_recipient_notified" -> "${to(e)} קיבל/ה הודעה על הקלטה" + (e.delivery?.let { " · $it" } ?: "")
            "recording_access" -> if (result(e) == Result.FAILED) "בקשה להקלטה מ${who(e)} נחסמה"
            else "הקלטה נמסרה לאפליקציה של ${to(e)}"
            "recording_deleted" -> "הקלטה נמחקה" + if (d.str("reason") == "retention_expired") " (תום תקופת השמירה)" else ""
            "recording_started" -> "התחילה הקלטה במכשיר"
            "recording_stopped" -> "ההקלטה במכשיר נעצרה"
            "recording_stored_offline" -> "קטע הקלטה נשמר במכשיר (ממתין להעלאה)"
            "recording_upload_failed" -> "העלאת קטע הקלטה נכשלה" + (d.str("reason")?.let { " ($it)" } ?: "")
            "sos_activated_offline" -> "SOS הופעל בלי קליטה (SMS ישיר מהטלפון)"
            "auto_activation_rejected" -> "זיהוי אוטומטי בביטחון נמוך נדחה - לא נפתח חירום"
            "false_alarm_cancelled" -> if (d.str("reason") == "low_confidence") "זיהוי אוטומטי בביטחון נמוך נדחה - לא נפתח חירום"
            else "אזעקת שווא בוטלה מהמכשיר"
            "live_location_paused_offline" -> "השהיית שיתוף מיקום בלי קליטה (נשלחה לשרת כשחזרה הקליטה)"
            "evidence_exported" -> "ייצוא תיעוד מהמכשיר" + (d.str("kind")?.let { " ($it)" } ?: "")
            "privacy_report_exported" -> "יוצא דוח פרטיות חתום"
            "guardian_link_requested" -> if (iAmActor) "ביקשת קישור הורה-ילד עם ${to(e)}" else "${who(e)} ביקש/ה קישור הורה-ילד"
            "guardian_link_approved" -> if (iAmActor) "אישרת קישור הורה-ילד" else "${who(e)} אישר/ה קישור הורה-ילד"
            "guardian_link_revoked" -> if (iAmActor) "ביטלת קישור הורה-ילד" else "${who(e)} ביטל/ה קישור הורה-ילד"
            "guardian_verified" -> "ההורה אומת/ה ע\"י צוות LifeGuard"
            "guardian_unverified" -> "אימות ההורה בוטל"
            else -> e.action
        }
    }

    private fun partyHe(p: String?) = when (p) { "guardian" -> "הורה"; "child" -> "ילד/ה"; else -> p ?: "?" }

    fun triggerHe(t: String?) = when (t) {
        "manual" -> "ידני"
        "wellness_no_response" -> "אין תגובה ל\"הכל בסדר?\""
        "sensor" -> "חיישנים"
        "acoustic" -> "זיהוי קול"
        "auto" -> "אוטומטי"
        "server_timer" -> "טיימר בשרת"
        else -> t ?: "?"
    }

    fun activationHe(a: String?) = when (a) {
        "manual" -> "SOS ידני"
        "unconfirmed" -> "לא מאושר - בלי מתנדבים"
        "confirmed_high" -> "ביטחון גבוה"
        "confirmed_by_user" -> "אושר על ידך"
        "server_timer" -> "טיימר"
        else -> a ?: "?"
    }

    fun clock(epochSec: Double, tz: TimeZone = TimeZone.getDefault()): String =
        SimpleDateFormat("dd/MM/yyyy HH:mm:ss", Locale.US).apply { timeZone = tz }.format(Date((epochSec * 1000).toLong()))

    /** זמן שרת וזמן מכשיר - תמיד בנפרד ובמפורש */
    fun timeLines(e: PrivacyEvent, tz: TimeZone = TimeZone.getDefault()): List<String> = buildList {
        add("זמן שרת: " + (e.serverTs?.let { clock(it, tz) } ?: "לא ידוע"))
        e.deviceTs?.let { add("זמן מכשיר (שעון הטלפון, לא מאומת): " + clock(it, tz)) }
    }

    fun detailLines(e: PrivacyEvent, me: String?, tz: TimeZone = TimeZone.getDefault()): List<String> = buildList {
        add(describe(e, me))
        add("סטטוס: " + resultHe(result(e)) + if (e.resultEffective != null && e.resultEffective != e.result)
            " (נרשם כ-${e.result}, מצב בפועל: ${e.resultEffective})" else "")
        addAll(timeLines(e, tz))
        add("מקור: " + if (e.source == "device") "נרשם במכשיר" else "נרשם בשרת")
        e.accessPeriod?.let { p ->
            val f = p.from?.let { clock(it, tz) } ?: "?"
            val t = p.to?.let { clock(it, tz) } ?: "?"
            add("תקופת מסירה: $f עד $t · ${p.requests ?: 0} בקשות (בקשות חוזרות מאוחדות; זה לא אומר שמישהו הסתכל)")
        }
        e.delivery?.let { add("מסירה: $it") }
        e.via?.let { add("ערוץ: " + (VIA_HE[it] ?: it)) }
        e.permission?.let { add("הרשאה בזמן הגישה: " + (MODE_HE[it] ?: it)) }
        e.incidentId?.let { add("אירוע: $it") }
        add("מזהה: ${e.eventId}")
    }

    enum class RevokeKind { LOCATION, RECORDING_ACCESS, GUARDIAN, RECORDING_SETTING }
    data class Revoke(val kind: RevokeKind, val contact: String?, val label: String)

    /** פעולת ביטול רלוונטית לאירוע (מקשרת למסך ההרשאות / הורה-ילד). null = אין. */
    fun revokeFor(e: PrivacyEvent, me: String?): Revoke? {
        val other = listOf(e.recipient, e.actor).firstOrNull { it != null && it != me && it != "admin" }
        return when {
            e.action == "location_access" || e.action.startsWith("permission_location") || e.action.startsWith("permission_sharing") ->
                Revoke(RevokeKind.LOCATION, other, if (other != null) "לשנות את שיתוף המיקום עם ${e.recipientDisplay ?: e.actorDisplay ?: other}"
                else "לנהל שיתוף מיקום")
            e.action in setOf("recording_access", "recording_recipient_notified", "permission_recording_access") ->
                Revoke(RevokeKind.RECORDING_ACCESS, other, "לנהל גישה להקלטות")
            e.action.startsWith("guardian_") || e.action == "permission_discreet_recording" ->
                Revoke(RevokeKind.GUARDIAN, null, "לנהל קישור הורה-ילד")
            e.action == "permission_recording" || e.action == "recording_uploaded" ->
                Revoke(RevokeKind.RECORDING_SETTING, null, "הגדרות הקלטה")
            else -> null
        }
    }

    /** טווח ימים (כולל) בשעון המקומי -> [from, to] בשניות epoch */
    fun dayRange(from: Triple<Int, Int, Int>?, to: Triple<Int, Int, Int>?, tz: TimeZone = TimeZone.getDefault()): Pair<Double?, Double?> {
        fun at(d: Triple<Int, Int, Int>, end: Boolean): Double = Calendar.getInstance(tz).run {
            clear(); set(d.first, d.second - 1, d.third, 0, 0, 0)
            if (end) add(Calendar.DAY_OF_MONTH, 1)
            timeInMillis / 1000.0 - if (end) 0.001 else 0.0
        }
        return from?.let { at(it, false) } to to?.let { at(it, true) }
    }

    /** כרונולוגי - החדש למעלה (לפי זמן השרת; זמן המכשיר לא מאומת ולא משמש למיון) */
    fun sorted(events: List<PrivacyEvent>): List<PrivacyEvent> = events.sortedByDescending { it.serverTs ?: 0.0 }
}
