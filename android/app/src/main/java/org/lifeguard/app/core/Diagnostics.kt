package org.lifeguard.app.core

import org.lifeguard.app.api.SetupStatus
import kotlin.math.roundToLong

/**
 * בדיקת מוכנות: תמונת מצב (DiagnosticsSnapshot, נאספת בשכבת ה-Android) -> רשימת תוצאות עם סטטוס, הסבר קצר
 * בעברית ופעולת תיקון. לוגיקה טהורה - נבדקת ב-JVM.
 */
enum class CheckStatus { OK, WARN, FAIL, UNAVAILABLE }

/** פעולת "תקן" - שכבת ה-Android ממפה כל ערך ל-Intent/בקשת הרשאה מתאימה. */
enum class FixAction {
    LOCATION_PERMISSION, LOCATION_SETTINGS, BACKGROUND_LOCATION, START_SERVICE, BATTERY_EXEMPTION,
    BATTERY_SAVER_SETTINGS, APP_DETAILS, NETWORK_SETTINGS, SERVER_SETTINGS, RELOGIN, NOTIFICATION_PERMISSION,
    APP_NOTIFICATION_SETTINGS, CHANNEL_SETTINGS, FULL_SCREEN_SETTINGS, SMS_SETTINGS, SMS_PERMISSION, MIC_PERMISSION,
    RETRY,
}

data class CheckResult(
    val id: String,
    val title: String,
    val status: CheckStatus,
    val detail: String,
    val fix: FixAction? = null,
    val fixLabel: String? = null,
    /** פרמטר לפעולה (למשל מזהה ערוץ התראות) */
    val fixArg: String? = null,
)

enum class LocationPerm { NONE, COARSE, FINE }

enum class AuthState { VALID, INVALID, NO_TOKEN, UNKNOWN }

/** תוצאת GET /v1/health. null בתמונת המצב = עוד לא נבדק. */
data class ServerProbe(val reachable: Boolean, val latencyMs: Long?, val status: String?, val error: String?)

sealed class SetupProbe {
    data class Ok(val setup: SetupStatus) : SetupProbe()
    /** השרת החזיר 404 - גרסת שרת בלי /v1/me/setup */
    object NotSupported : SetupProbe()
    data class Error(val message: String) : SetupProbe()
    object NotChecked : SetupProbe()
}

/** ערכי חשיבות של NotificationManager (מועתקים כדי שהמחלקה תישאר טהורה). */
object Importance {
    const val NONE = 0
    const val MIN = 1
    const val LOW = 2
    const val DEFAULT = 3
    const val HIGH = 4
}

data class ChannelState(val id: String, val label: String, val importance: Int?)

data class DiagnosticsSnapshot(
    val sdkInt: Int,
    val nowSec: Double,
    val locationPerm: LocationPerm,
    val gpsEnabled: Boolean,
    val networkLocationEnabled: Boolean,
    val lastFixAt: Double?,
    val lastFixAccuracyM: Double?,
    val backgroundLocation: Boolean,
    val serviceRunning: Boolean,
    val batteryExempt: Boolean,
    val powerSaveOn: Boolean,
    /** null = API < 28 (אין isBackgroundRestricted) */
    val backgroundRestricted: Boolean?,
    val networkPresent: Boolean,
    val networkValidated: Boolean,
    val server: ServerProbe?,
    val auth: AuthState,
    val notificationsGranted: Boolean,
    val notificationsEnabled: Boolean,
    val channels: List<ChannelState>,
    /** API < 34: תמיד true */
    val fullScreenAllowed: Boolean,
    val setup: SetupProbe,
    val smsNumbers: Int,
    val smsPermission: Boolean,
    val telephony: Boolean,
    val hasAccelerometer: Boolean,
    val hasGyroscope: Boolean,
    val motionDetectionEnabled: Boolean,
    val audioEvidenceEnabled: Boolean,
    val micPermission: Boolean,
    /** השכמה דרך FCM (מצב המכשיר; מצב השרת מגיע ב-setup) */
    val fcm: FcmDiagState,
)

data class DiagnosticsSummary(val fail: Int, val warn: Int, val ok: Int, val unavailable: Int) {
    val allGood get() = fail == 0 && warn == 0
    fun textHe(): String = when {
        fail == 0 && warn == 0 -> "מוכנות: הכל תקין"
        else -> "מוכנות: " + listOfNotNull(
            if (fail > 0) (if (fail == 1) "תקלה אחת" else "$fail תקלות") else null,
            if (warn > 0) (if (warn == 1) "אזהרה אחת" else "$warn אזהרות") else null,
        ).joinToString(" · ") + " - לחץ/י לפרטים"
    }
}

object DiagnosticsEvaluator {
    const val FIX_AGE_WARN_SECONDS = 10 * 60.0
    const val SLOW_SERVER_MS = 2000L

    fun evaluate(s: DiagnosticsSnapshot): List<CheckResult> {
        val r = ArrayList<CheckResult>()
        r += locationPermission(s)
        r += locationProvider(s)
        r += lastFix(s)
        r += backgroundLocation(s)
        r += service(s)
        r += battery(s)
        r += powerSaver(s)
        r += backgroundRestriction(s)
        r += internet(s)
        r += server(s)
        r += auth(s)
        r += fcm(s)
        r += notifications(s)
        r += channels(s)
        r += fullScreen(s)
        r += contacts(s)
        r += sms(s)
        r += sensors(s)
        microphone(s)?.let { r += it }
        r += CheckResult("camera", "מצלמה", CheckStatus.UNAVAILABLE, "לא בשימוש בגרסת אנדרואיד")
        return r
    }

    fun summarize(results: List<CheckResult>) = DiagnosticsSummary(
        fail = results.count { it.status == CheckStatus.FAIL },
        warn = results.count { it.status == CheckStatus.WARN },
        ok = results.count { it.status == CheckStatus.OK },
        unavailable = results.count { it.status == CheckStatus.UNAVAILABLE },
    )

    // ------------------------------------------------------------ מיקום
    private fun locationPermission(s: DiagnosticsSnapshot) = when (s.locationPerm) {
        LocationPerm.FINE -> CheckResult("location_perm", "הרשאת מיקום", CheckStatus.OK, "מיקום מדויק מאושר")
        LocationPerm.COARSE -> CheckResult("location_perm", "הרשאת מיקום", CheckStatus.WARN,
            "רק מיקום משוער - אנשי הקשר יקבלו מיקום לא מדויק ב-SOS", FixAction.LOCATION_PERMISSION, "אפשר מיקום מדויק")
        LocationPerm.NONE -> CheckResult("location_perm", "הרשאת מיקום", CheckStatus.FAIL,
            "בלי מיקום, קריאת חירום תישלח בלי מיקום", FixAction.LOCATION_PERMISSION, "אפשר מיקום")
    }

    private fun locationProvider(s: DiagnosticsSnapshot): CheckResult {
        val t = "שירותי מיקום"
        return when {
            s.gpsEnabled -> CheckResult("location_provider", t, CheckStatus.OK, "GPS פעיל")
            s.networkLocationEnabled -> CheckResult("location_provider", t, CheckStatus.WARN,
                "GPS כבוי - רק מיקום לפי רשת (פחות מדויק)", FixAction.LOCATION_SETTINGS, "פתח הגדרות מיקום")
            else -> CheckResult("location_provider", t, CheckStatus.FAIL, "המיקום כבוי במכשיר",
                FixAction.LOCATION_SETTINGS, "הפעל מיקום")
        }
    }

    private fun lastFix(s: DiagnosticsSnapshot): CheckResult {
        val t = "מיקום אחרון"
        if (s.locationPerm == LocationPerm.NONE) return CheckResult("last_fix", t, CheckStatus.UNAVAILABLE, "אין הרשאת מיקום")
        val at = s.lastFixAt ?: return CheckResult("last_fix", t, CheckStatus.WARN,
            "עוד לא התקבל מיקום. בחוץ, עם GPS פעיל, זה לוקח עד דקה")
        val age = (s.nowSec - at).coerceAtLeast(0.0)
        val acc = s.lastFixAccuracyM?.let { " · דיוק ~${it.roundToLong()} מ'" } ?: ""
        return if (age <= FIX_AGE_WARN_SECONDS) CheckResult("last_fix", t, CheckStatus.OK, "לפני ${ageHe(age)}$acc")
        else CheckResult("last_fix", t, CheckStatus.WARN, "המיקום האחרון ישן (לפני ${ageHe(age)})$acc")
    }

    private fun backgroundLocation(s: DiagnosticsSnapshot): CheckResult {
        val t = "מיקום ברקע"
        return when {
            s.sdkInt < 29 -> CheckResult("bg_location", t, CheckStatus.OK, "נכלל בהרשאת המיקום בגרסה זו")
            s.locationPerm == LocationPerm.NONE -> CheckResult("bg_location", t, CheckStatus.UNAVAILABLE, "קודם צריך הרשאת מיקום")
            s.backgroundLocation -> CheckResult("bg_location", t, CheckStatus.OK, "מאושר")
            else -> CheckResult("bg_location", t, CheckStatus.WARN,
                "רשות: בלי זה, אחרי הפעלה מחדש של הטלפון המיקום לא יישלח עד שתפתח/י את האפליקציה",
                FixAction.BACKGROUND_LOCATION, "אפשר מיקום ברקע")
        }
    }

    // ------------------------------------------------------------ רקע וסוללה
    private fun service(s: DiagnosticsSnapshot) =
        if (s.serviceRunning) CheckResult("service", "שירות \"LifeGuard מחובר\"", CheckStatus.OK, "פועל ברקע")
        else CheckResult("service", "שירות \"LifeGuard מחובר\"", CheckStatus.FAIL,
            "השירות לא פועל - קריאות חירום לא יתקבלו כשהאפליקציה סגורה", FixAction.START_SERVICE, "הפעל שירות")

    private fun battery(s: DiagnosticsSnapshot) =
        if (s.batteryExempt) CheckResult("battery_opt", "החרגה מחיסכון סוללה", CheckStatus.OK, "מוחרג - החיבור לא ינותק")
        else CheckResult("battery_opt", "החרגה מחיסכון סוללה", CheckStatus.WARN,
            "המערכת עלולה לנתק את החיבור כשהמסך כבוי", FixAction.BATTERY_EXEMPTION, "בקש החרגה")

    private fun powerSaver(s: DiagnosticsSnapshot) =
        if (!s.powerSaveOn) CheckResult("power_saver", "מצב חיסכון בסוללה", CheckStatus.OK, "כבוי")
        else CheckResult("power_saver", "מצב חיסכון בסוללה", CheckStatus.WARN,
            "מופעל - עלול לעכב התראות ומיקום", FixAction.BATTERY_SAVER_SETTINGS, "הגדרות סוללה")

    private fun backgroundRestriction(s: DiagnosticsSnapshot) = when (s.backgroundRestricted) {
        null -> CheckResult("bg_restricted", "הגבלת רקע", CheckStatus.OK, "לא קיים בגרסת אנדרואיד זו")
        false -> CheckResult("bg_restricted", "הגבלת רקע", CheckStatus.OK, "האפליקציה לא מוגבלת")
        true -> CheckResult("bg_restricted", "הגבלת רקע", CheckStatus.FAIL,
            "האפליקציה מוגבלת ברקע - השירות עלול להיעצר", FixAction.APP_DETAILS, "פתח הגדרות אפליקציה")
    }

    // ------------------------------------------------------------ רשת ושרת
    private fun internet(s: DiagnosticsSnapshot) = when {
        !s.networkPresent -> CheckResult("internet", "אינטרנט", CheckStatus.FAIL,
            "אין רשת. SOS יעבור ל-SMS ישיר (אם הוגדרו מספרים)", FixAction.NETWORK_SETTINGS, "הגדרות רשת")
        !s.networkValidated -> CheckResult("internet", "אינטרנט", CheckStatus.WARN,
            "יש רשת אבל בלי גישה מאומתת לאינטרנט (למשל Wi-Fi עם דף כניסה)", FixAction.NETWORK_SETTINGS, "הגדרות רשת")
        else -> CheckResult("internet", "אינטרנט", CheckStatus.OK, "מחובר")
    }

    private fun server(s: DiagnosticsSnapshot): CheckResult {
        val t = "שרת LifeGuard"
        val p = s.server ?: return CheckResult("server", t, CheckStatus.UNAVAILABLE,
            if (s.networkPresent) "עוד לא נבדק" else "לא נבדק - אין רשת", FixAction.RETRY, "בדוק שוב")
        if (!p.reachable) return CheckResult("server", t, CheckStatus.FAIL,
            "השרת לא עונה" + (p.error?.let { ": $it" } ?: ""), FixAction.SERVER_SETTINGS, "בדוק כתובת שרת")
        val ms = p.latencyMs?.let { " · $it ms" } ?: ""
        return when {
            p.status != null && p.status != "ok" -> CheckResult("server", t, CheckStatus.WARN,
                "השרת עונה אבל מדווח על תקלה (${p.status})$ms")
            (p.latencyMs ?: 0) > SLOW_SERVER_MS -> CheckResult("server", t, CheckStatus.WARN, "השרת איטי$ms", FixAction.RETRY, "בדוק שוב")
            else -> CheckResult("server", t, CheckStatus.OK, "זמין$ms")
        }
    }

    private fun auth(s: DiagnosticsSnapshot) = when (s.auth) {
        AuthState.VALID -> CheckResult("auth", "התחברות", CheckStatus.OK, "החיבור לחשבון תקף")
        AuthState.INVALID -> CheckResult("auth", "התחברות", CheckStatus.FAIL,
            "פג תוקף ההתחברות - לא יתקבלו התראות", FixAction.RELOGIN, "התחבר/י מחדש")
        AuthState.NO_TOKEN -> CheckResult("auth", "התחברות", CheckStatus.FAIL, "לא מחובר/ת", FixAction.RELOGIN, "התחבר/י")
        AuthState.UNKNOWN -> CheckResult("auth", "התחברות", CheckStatus.UNAVAILABLE, "לא ניתן לבדוק בלי חיבור לשרת")
    }

    // ------------------------------------------------------------ השכמה (FCM)
    /**
     * UNAVAILABLE - לאפליקציה אין הגדרת Firebase (אין firebase.properties בבנייה).
     * WARN - האפליקציה מוגדרת אבל השרת בלי credentials (not_configured / misconfigured), או שהמכשיר לא רשום, או fcm_devices=0.
     * OK - השרת active + המכשיר הזה רשום בחשבון הנוכחי + השרת מכיר ≥1 מכשיר.
     */
    private fun fcm(s: DiagnosticsSnapshot): CheckResult {
        val t = "FCM / השכמה"
        val id = "fcm"
        val f = s.fcm
        if (!f.appConfigured) return CheckResult(id, t, CheckStatus.UNAVAILABLE,
            (f.configProblem?.takeIf { it != FcmConfig.NOT_CONFIGURED }?.let { "${FcmConfig.NOT_CONFIGURED}: $it" }
                ?: "${FcmConfig.NOT_CONFIGURED} באפליקציה (אין firebase.properties בבנייה)") +
                ". קריאות מגיעות רק דרך החיבור הפתוח של השירות - בטלפונים שהורגים אפליקציות ברקע קריאה עלולה להתעכב")
        if (f.initError != null) return CheckResult(id, t, CheckStatus.WARN,
            "הגדרת Firebase באפליקציה לא נטענה: ${f.initError}. קריאות מגיעות רק דרך החיבור הפתוח של השירות")
        val setup = when (val p = s.setup) {
            is SetupProbe.Ok -> p.setup
            SetupProbe.NotSupported -> return CheckResult(id, t, CheckStatus.WARN,
                "השרת לא מדווח על השכמה (גרסת שרת ישנה)")
            is SetupProbe.Error -> return CheckResult(id, t, CheckStatus.UNAVAILABLE, "הבדיקה מול השרת נכשלה: ${p.message}",
                FixAction.RETRY, "בדוק שוב")
            SetupProbe.NotChecked -> return CheckResult(id, t, CheckStatus.UNAVAILABLE, "לא נבדק (נדרש חיבור לשרת)",
                FixAction.RETRY, "בדוק שוב")
        }
        val wake = f.lastWakeAt?.let { " · השכמה אחרונה לפני ${ageHe((s.nowSec - it).coerceAtLeast(0.0))}" } ?: ""
        return when {
            setup.fcm == null -> CheckResult(id, t, CheckStatus.WARN, "השרת לא מדווח על FCM (גרסת שרת ישנה) - אין השכמה")
            setup.fcm == FcmServerStatus.NOT_CONFIGURED -> CheckResult(id, t, CheckStatus.WARN,
                "האפליקציה מוגדרת, אבל לשרת אין הרשאת שליחה ל-FCM (LIFEGUARD_FCM_SERVICE_ACCOUNT) - אין השכמה")
            setup.fcm != FcmServerStatus.ACTIVE -> CheckResult(id, t, CheckStatus.WARN,
                "הגדרת FCM בשרת שגויה (${setup.fcm}) - אין השכמה")
            f.registration != FcmTokenRegistry.Status.REGISTERED -> CheckResult(id, t, CheckStatus.WARN,
                when (f.registration) {
                    FcmTokenRegistry.Status.NO_TOKEN -> "עוד לא התקבל טוקן מ-Firebase (צריך Google Play Services ואינטרנט)"
                    FcmTokenRegistry.Status.FAILED -> "השרת דחה את רישום המכשיר" + (f.lastError?.let { " ($it)" } ?: "")
                    else -> "המכשיר עוד לא נרשם בשרת להשכמה" + (f.lastError?.let { " (ניסיון אחרון: $it)" } ?: "")
                }, FixAction.RETRY, "נסה שוב")
            (setup.fcmDevices ?: 0) == 0 -> CheckResult(id, t, CheckStatus.WARN,
                "השרת לא מכיר אף מכשיר שלך להשכמה - נרשם מחדש", FixAction.RETRY, "בדוק שוב")
            else -> CheckResult(id, t, CheckStatus.OK, "פעיל - השרת יכול להעיר את האפליקציה$wake")
        }
    }

    // ------------------------------------------------------------ התראות
    private fun notifications(s: DiagnosticsSnapshot) = when {
        s.sdkInt >= 33 && !s.notificationsGranted -> CheckResult("notifications", "התראות", CheckStatus.FAIL,
            "אין הרשאת התראות - קריאות חירום לא יוצגו", FixAction.NOTIFICATION_PERMISSION, "אפשר התראות")
        !s.notificationsEnabled -> CheckResult("notifications", "התראות", CheckStatus.FAIL,
            "ההתראות של האפליקציה חסומות בהגדרות", FixAction.APP_NOTIFICATION_SETTINGS, "פתח הגדרות התראות")
        else -> CheckResult("notifications", "התראות", CheckStatus.OK, "מאושרות")
    }

    private fun channels(s: DiagnosticsSnapshot): List<CheckResult> = s.channels.map { c ->
        val id = "channel_${c.id}"
        val t = "ערוץ \"${c.label}\""
        when {
            c.importance == null -> CheckResult(id, t, CheckStatus.WARN, "הערוץ חסר - ייווצר מחדש בהפעלה הבאה")
            c.importance == Importance.NONE -> CheckResult(id, t, CheckStatus.FAIL, "הערוץ חסום",
                FixAction.CHANNEL_SETTINGS, "פתח הגדרות ערוץ", c.id)
            c.importance < Importance.HIGH -> CheckResult(id, t, CheckStatus.WARN,
                "החשיבות הונמכה - ההתראה לא תקפוץ על המסך", FixAction.CHANNEL_SETTINGS, "פתח הגדרות ערוץ", c.id)
            else -> CheckResult(id, t, CheckStatus.OK, "חשיבות גבוהה")
        }
    }

    private fun fullScreen(s: DiagnosticsSnapshot) = when {
        s.sdkInt < 34 -> CheckResult("full_screen", "התראה במסך מלא", CheckStatus.OK, "מאושר אוטומטית בגרסה זו")
        s.fullScreenAllowed -> CheckResult("full_screen", "התראה במסך מלא", CheckStatus.OK, "מאושר")
        else -> CheckResult("full_screen", "התראה במסך מלא", CheckStatus.FAIL,
            "קריאה נכנסת ו\"הכל בסדר?\" לא יופיעו על מסך נעול", FixAction.FULL_SCREEN_SETTINGS, "אשר מסך מלא")
    }

    // ------------------------------------------------------------ אנשי קשר ו-SMS
    private fun contacts(s: DiagnosticsSnapshot): CheckResult {
        val t = "אנשי קשר לחירום"
        return when (val p = s.setup) {
            SetupProbe.NotChecked -> CheckResult("contacts", t, CheckStatus.UNAVAILABLE, "לא נבדק (נדרש חיבור לשרת)",
                FixAction.RETRY, "בדוק שוב")
            SetupProbe.NotSupported -> CheckResult("contacts", t, CheckStatus.UNAVAILABLE,
                "השרת לא תומך בבדיקה זו (אין /v1/me/setup - גרסת שרת ישנה)")
            is SetupProbe.Error -> CheckResult("contacts", t, CheckStatus.UNAVAILABLE, "הבדיקה נכשלה: ${p.message}",
                FixAction.RETRY, "בדוק שוב")
            is SetupProbe.Ok -> {
                val st = p.setup
                val extra = ArrayList<String>()
                if (st.contactsPending > 0) extra += "${st.contactsPending} הזמנות ממתינות לאישור"
                if (st.lastReadinessTotal != null && st.lastReadinessConfirmed != null)
                    extra += "בדיקת מוכנות אחרונה: ${st.lastReadinessConfirmed}/${st.lastReadinessTotal} אישרו קבלה"
                val tail = if (extra.isEmpty()) "" else " · " + extra.joinToString(" · ")
                val mgmt = " (ניהול אנשי קשר - באפליקציה השולחנית / PWA)"
                when {
                    st.contactsAccepted > 0 -> CheckResult("contacts", t,
                        if (st.lastReadinessTotal != null && st.lastReadinessTotal > 0 && st.lastReadinessConfirmed == 0)
                            CheckStatus.WARN else CheckStatus.OK,
                        "${st.contactsAccepted} אנשי קשר אישרו$tail")
                    st.contactsPending > 0 -> CheckResult("contacts", t, CheckStatus.WARN,
                        "אף איש קשר עוד לא אישר$tail$mgmt")
                    else -> CheckResult("contacts", t, CheckStatus.FAIL,
                        "אין אנשי קשר - קריאת חירום תגיע רק למתנדבים, אם יש$mgmt")
                }
            }
        }
    }

    private fun sms(s: DiagnosticsSnapshot) = when {
        !s.telephony -> CheckResult("sms", "SMS ללא אינטרנט", CheckStatus.UNAVAILABLE, "למכשיר אין יכולת SMS")
        s.smsNumbers == 0 -> CheckResult("sms", "SMS ללא אינטרנט", CheckStatus.WARN,
            "לא הוגדרו מספרים - בלי אינטרנט ה-SOS לא יגיע לאף אחד", FixAction.SMS_SETTINGS, "הגדר מספרים")
        !s.smsPermission -> CheckResult("sms", "SMS ללא אינטרנט", CheckStatus.FAIL,
            "הוגדרו ${s.smsNumbers} מספרים אבל אין הרשאת SMS", FixAction.SMS_PERMISSION, "אפשר SMS")
        else -> CheckResult("sms", "SMS ללא אינטרנט", CheckStatus.OK, "${s.smsNumbers} מספרים מוכנים")
    }

    // ------------------------------------------------------------ חיישנים
    private fun sensors(s: DiagnosticsSnapshot): CheckResult {
        val t = "חיישני תנועה"
        val off = if (s.motionDetectionEnabled) "" else " · זיהוי תנועה כבוי בהגדרות"
        return when {
            !s.hasAccelerometer -> CheckResult("sensors", t, CheckStatus.UNAVAILABLE,
                "אין חיישן תאוצה - זיהוי נפילה/מכה/מאבק לא זמין")
            !s.hasGyroscope -> CheckResult("sensors", t, CheckStatus.WARN,
                "אין ג'ירוסקופ - הזיהוי עובד בביטחון נמוך יותר$off")
            else -> CheckResult("sensors", t, CheckStatus.OK, "חיישן תאוצה וג'ירוסקופ$off")
        }
    }

    /** רק כשהקלטת שמע בזמן SOS מופעלת בהגדרות. */
    private fun microphone(s: DiagnosticsSnapshot): CheckResult? {
        if (!s.audioEvidenceEnabled) return null
        return if (s.micPermission) CheckResult("microphone", "מיקרופון (תיעוד שמע)", CheckStatus.OK, "מאושר")
        else CheckResult("microphone", "מיקרופון (תיעוד שמע)", CheckStatus.FAIL,
            "הקלטת שמע ב-SOS מופעלת אבל אין הרשאת מיקרופון", FixAction.MIC_PERMISSION, "אפשר מיקרופון")
    }

    fun ageHe(seconds: Double): String {
        val s = seconds.roundToLong()
        return when {
            s < 60 -> "$s שנ'"
            s < 3600 -> "${s / 60} דק'"
            s < 86400 -> "${s / 3600} שע'"
            else -> "${s / 86400} ימים"
        }
    }
}
