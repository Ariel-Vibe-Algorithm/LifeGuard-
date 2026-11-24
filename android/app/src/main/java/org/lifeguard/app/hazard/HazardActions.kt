package org.lifeguard.app.hazard

import org.lifeguard.app.api.TrustedNotifyResult
import org.lifeguard.app.core.Helpers
import java.io.IOException

/** השרת: הודעה לאנשי הקשר + בדיקת אישור קבלה (ApiClient.notifyTrusted / notificationStatus). */
interface TrustedNotifier {
    fun notifyTrusted(text: String): TrustedNotifyResult
    fun notificationStatus(ids: Map<String, Long>): Map<String, Boolean>
}

/** אין חיבור לשרת - ההודעה לא נשלחה (בלי "נשלח" מדומה). */
class HazardNotConnected(message: String) : IOException(message)

/**
 * הפעולות מחלון ההתראה - המקבילות ל-engine.hazard_* ב-Windows. אף אחת לא רצה בלי אישור מפורש של המשתמש/ת
 * (confirmed=true מגיע רק מכפתור "כן" בדיאלוג), ואף אחת לא "מצליחה" בלי תוצאה אמיתית.
 */
class HazardActions(
    private val manager: AlertManager,
    private val log: HazardLog,
    private val connected: () -> Boolean,
    private val notifier: () -> TrustedNotifier,
    /** אותו מסלול כמו כפתור ה-SOS (IncidentManager.startSos, trigger=manual, source=hazard_alert) */
    private val startSos: () -> Unit,
) {
    /** רישום תגובה וסגירת האירוע (שקט לפי סוג התגובה). לא מבצע את הפעולה עצמה. */
    fun respond(alert: HazardAlert, response: String, reason: String? = null) {
        log.recordResponse(alert, response, reason)
        manager.close(alert, response)
    }

    /** SOS - רק אחרי אישור. מחזיר false (ולא נוגע בכלום) בלי אישור. */
    fun activateSos(alert: HazardAlert, confirmed: Boolean): Boolean {
        if (!confirmed) return false
        respond(alert, "sos")
        startSos()
        return true
    }

    /**
     * הודעה לאנשי הקשר - רק אחרי אישור. מחזיר את תשובת השרת (sent_to + מזהי הודעות) או זורק: HazardNotConnected
     * כשאין חיבור, ApiException / IOException משגיאת שרת/רשת. התגובה נרשמת רק אחרי שהשרת קיבל.
     */
    fun notifyContacts(alert: HazardAlert, lang: String, confirmed: Boolean): TrustedNotifyResult {
        check(confirmed) { "contacts alert requires explicit confirmation" }
        if (!connected()) throw HazardNotConnected(
            if (lang == "en") "not connected to the LifeGuard server - the message was not sent"
            else "לא מחובר לשרת LifeGuard - ההודעה לא נשלחה")
        val res = notifier().notifyTrusted(HazardAlerts.contactsMessage(alert, lang))
        respond(alert, "contacts")
        return res
    }

    /** האם אפליקציות אנשי הקשר אישרו קבלה (לא רק "נשלח"). בלי חיבור / בלי מזהים -> ריק. */
    fun deliveryStatus(ids: Map<String, Long>): Map<String, Boolean> =
        if (!connected() || ids.isEmpty()) emptyMap() else notifier().notificationStatus(ids)

    /**
     * חיוג לחירום - רק אחרי אישור. dial(digits) פותח את החייגן (ACTION_DIAL - המשתמש/ת לוחץ/ת "חייג"); מחזיר
     * true אם נפתח. null = לא אושר (לא נרשם כלום).
     */
    fun callEmergency(alert: HazardAlert, confirmed: Boolean, number: String, dial: (String) -> Boolean): Boolean? {
        if (!confirmed) return null
        val opened = runCatching { dial(number.filter { it.isDigit() }) }.getOrDefault(false)
        respond(alert, "call")
        return opened
    }

    companion object {
        /**
         * המספר לחיוג: כמו emergency_numbers_module.format_primary_number ב-Windows (general, אחרת police). באנדרואיד
         * יש רק את הרשימה הישראלית המובנית (Helpers.HOTLINES) - משטרה 100.
         */
        fun emergencyNumber(): String =
            Helpers.HOTLINES.firstOrNull { it.name == "משטרה" }?.dialNumbers?.firstOrNull() ?: "100"
    }
}

/** מתי מותר להקפיץ חלון (engine._hazard_can_show ב-Windows). */
object HazardGate {
    /**
     * - מצוקה שקטה (SOS שקט פעיל / ממתין) או ליווי שקט -> לעולם לא (חלון היה חושף את המעקב): silent_tracking
     * - SOS פעיל -> event_active
     * - אחרת - מותר.
     */
    fun canShow(incidentActive: Boolean, incidentMode: String?, escortActive: Boolean, escortMode: String?): Pair<Boolean, String?> =
        when {
            incidentActive && incidentMode == "silent" -> false to "silent_tracking"
            escortActive && escortMode == "silent" -> false to "silent_tracking"
            incidentActive -> false to "event_active"
            else -> true to null
        }
}
