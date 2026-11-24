package org.lifeguard.app.core

import org.lifeguard.app.api.HelperInfo
import org.lifeguard.app.api.IncidentView

/** קו סיוע רשמי. number יכול להיות "1202 / 1203"; url - כשאין מספר (צ'אט). */
data class Hotline(val name: String, val number: String?, val url: String? = null, val note: String? = null) {
    /** מספרים לחיוג (ACTION_DIAL בלבד - המשתמש/ת לוחץ/ת "חייג") */
    val dialNumbers: List<String> get() = number?.split("/")?.map { it.trim().filter { c -> c.isDigit() || c == '+' } }
        ?.filter { it.isNotEmpty() } ?: emptyList()
}

/** צבע/טון של שורת סטטוס */
enum class Tone { MUTED, OK, WARN, ERROR }

/**
 * מתנדבים שלא חייבים להיות מד"א (server/helpers.py): שיחת תמיכה מרחוק (remote_support - בלי שם ובלי מיקום של הפונה)
 * והזעקת רשויות (authority_liaison - מתנדב/ת מאומת/ת, רואה שם ומיקום רק אחרי קבלה).
 * כל הטקסטים זהים לאפליקציית Windows (main.py helpers_summary, support_ui.py, alert_window.py, features_ui.py).
 * כל סטטוס נגזר רק ממה שהשרת החזיר - "מישהו איתך" רק אחרי state=accepted אמיתי.
 */
object Helpers {
    const val REMOTE_SUPPORT = "remote_support"
    const val AUTHORITY_LIAISON = "authority_liaison"
    val VOL_ROLES = listOf("on_scene", REMOTE_SUPPORT, AUTHORITY_LIAISON)
    val ROLE_HE = mapOf("on_scene" to "הגעה למקום", REMOTE_SUPPORT to "שיחת תמיכה מרחוק", AUTHORITY_LIAISON to "הזעקת רשויות")

    /** features_ui.py VolunteerTrustSection.ROLE_ROWS */
    data class RoleRow(val role: String, val label: String, val sub: String)
    val ROLE_ROWS = listOf(
        RoleRow("on_scene", "🚶 הגעה למקום", "קריאות בקרבתך - כמו היום"),
        RoleRow(REMOTE_SUPPORT, "💬 שיחת תמיכה מרחוק",
            "צ'אט עם מי שצריך/ה להרגיש שמישהו איתו/ה. מכל מקום, בלי לראות שם ומיקום. חשבון בן 3 ימים לפחות."),
        RoleRow(AUTHORITY_LIAISON, "👮 הזעקת רשויות",
            "מתקשר/ת ל-100/101/102 בשביל מי שלא יכול/ה לדבר. רואה שם ומיקום - לכן דורש אימות " +
                "(גם אימות זהות ידני, בלי ארגון)."),
    )
    /** features_ui.py LANG_ROWS (השרת מקבל גם fr/es - לא מוצגות ב-Windows) */
    val LANG_ROWS = listOf("he" to "עברית", "ar" to "ערבית", "en" to "אנגלית", "ru" to "רוסית", "am" to "אמהרית")

    /** server/helpers.py LIAISON_ACTIONS - הסדר לכפתורים (התוויות מהשרת כשנשלחו) */
    val LIAISON_ACTIONS: Map<String, String> = linkedMapOf(
        "called_police" to "הזעיק/ה משטרה (100)", "called_mda" to "הזעיק/ה מד\"א (101)",
        "called_fire" to "הזעיק/ה כבאות (102)", "reported_other" to "דיווח/ה לגורם רשמי אחר",
        "could_not_reach" to "לא הצליח/ה ליצור קשר עם הרשויות - ממשיך/ה לנסות",
    )
    /** alert_window.py respond(): משוב אחרי פעולה מוצלחת בשרת */
    val ACTION_FEEDBACK = mapOf(
        "accept" to "קיבלת את הבקשה", "decline" to "סומנת כלא זמין/ה",
        "called_police" to "עודכן: הזעקת משטרה", "called_mda" to "עודכן: הזעקת מד\"א",
        "called_fire" to "עודכן: הזעקת כבאות", "reported_other" to "עודכן: דיווחת לגורם רשמי",
        "could_not_reach" to "עודכן: עוד לא הצלחת ליצור קשר", "done" to "תודה! הבקשה נסגרה מצדך",
    )

    /** עותק מקומי של server/helpers.py HOTLINES - מוצג גם בלי שרת ובלי אינטרנט. */
    val HOTLINES = listOf(
        Hotline("ער\"ן - עזרה ראשונה נפשית", "1201", note = "24/7, גם בצ'אט באתר"),
        Hotline("סה\"ר - סיוע והקשבה ברשת", null, url = "https://sahar.org.il", note = "צ'אט אנונימי"),
        Hotline("נט\"ל - טראומה על רקע לאומי", "1-800-363-363"),
        Hotline("מרכזי סיוע לנפגעות ולנפגעי תקיפה מינית", "1202 / 1203", note = "נשים / גברים"),
        Hotline("מוקד 105 - הגנה על ילדים ברשת", "105"),
        Hotline("משטרה", "100", note = "בסכנה מיידית"),
    )
    const val HOTLINES_NOTE = "מספרי הקווים יש לאמת לפני הפצה לציבור."
    const val NOT_EMERGENCY_NOTE = "בסכנה מיידית לחצו SOS או התקשרו 100 / 101 - שיחה עם מתנדב/ת אינה שירות חירום."

    // ---------------- בעל/ת האירוע (מסך ה-SOS) ----------------
    const val BTN_LIAISON = "👮 שמישהו יזעיק משטרה בשבילי"
    const val BTN_TALK = "💬 מישהו לדבר איתו עכשיו"
    const val NOT_ON_SERVER_YET = "ההתראה עוד לא אושרה בשרת - נסו שוב בעוד רגע.\nבסכנה מיידית: 100"

    /** main.py SosWindow.helpers_summary - רק לפי מה שהשרת החזיר (לא "מישהו בדרך" בלי אישור אמיתי). */
    fun summary(helpers: List<HelperInfo>, requested: Set<String> = emptySet()): Pair<String, Tone> {
        val lines = ArrayList<String>()
        val kindsSeen = HashSet<String>()
        for (h in helpers) {
            kindsSeen += h.kind
            if (h.state == "accepted") {
                val who = if (h.kind == AUTHORITY_LIAISON) h.displayName else "מתנדב/ת"
                lines += "✅ ${h.kindLabel}: $who איתך" + (h.lastActionLabel?.let { "\n   $it" } ?: "")
            }
        }
        for (kind in listOf(AUTHORITY_LIAISON, REMOTE_SUPPORT)) {
            val mine = helpers.filter { it.kind == kind }
            if (mine.any { it.state == "accepted" }) continue
            when {
                mine.any { it.state == "notified" } -> lines += "⏳ ${mine[0].kindLabel}: נשלח למתנדבים, ממתינים שמישהו יקבל"
                mine.any { it.state == "done" } -> lines += "✔️ ${mine[0].kindLabel}: המתנדב/ת סיים/ה"
                kind in requested && kind !in kindsSeen -> lines += "⚠️ אין כרגע מתנדבים זמינים לזה. בסכנה מיידית: 100 / 101"
            }
        }
        if (lines.isEmpty()) return "" to Tone.MUTED
        val bad = lines.any { it.startsWith("⚠️") }
        return lines.joinToString("\n") to (if (bad) Tone.ERROR else if (lines[0].startsWith("✅")) Tone.OK else Tone.MUTED)
    }

    // ---------------- "צריך/ה לדבר עם מישהו?" (לא חירום) ----------------
    /** support_ui.py support_status */
    fun supportStatus(view: IncidentView?): Pair<String, Tone> {
        if (view == null) return "" to Tone.MUTED
        if (!view.isActive) return "השיחה הסתיימה. אפשר תמיד לפתוח שיחה חדשה או לפנות לקווי הסיוע." to Tone.MUTED
        val hs = view.helpers
        if (hs.any { it.state == "accepted" }) {
            return "✅ מתנדב/ת הצטרף/ה לשיחה - אפשר לכתוב. המתנדב/ת לא רואה את שמך ואת מיקומך." to Tone.OK
        }
        val n = hs.count { it.state == "notified" }
        if (n > 0) return "⏳ הבקשה נשלחה ל-$n מתנדבים. ממתינים שמישהו יקבל - בינתיים אפשר לכתוב כאן." to Tone.MUTED
        if (hs.any { it.state == "done" }) return "המתנדב/ת סיים/ה את השיחה. אפשר לסגור ולפתוח בקשה חדשה." to Tone.MUTED
        return ("⚠️ אין כרגע מתנדבי שיחה זמינים. הבקשה נשארת פתוחה וננסה שוב - " +
            "ובינתיים אפשר לפנות לקווי הסיוע למטה.") to Tone.WARN
    }

    fun hotlineLine(h: Hotline): String =
        "☎️ ${h.name}: ${h.number ?: h.url ?: ""}" + (h.note?.let { " ($it)" } ?: "")

    // ---------------- המתנדב/ת (בקשה נכנסת) - alert_window.py _apply_helper ----------------
    fun isLiaison(v: IncidentView) = v.helperKind == AUTHORITY_LIAISON
    fun isAccepted(v: IncidentView) = v.myState == "accepted"

    fun title(v: IncidentView): String = when {
        !v.isActive -> "✅ הבקשה הסתיימה"
        isLiaison(v) -> "👮 בקשה להזעיק רשויות"
        else -> "💬 מישהו צריך לדבר"
    }

    fun subtitle(v: IncidentView, timeHhmm: String): String =
        "את/ה: ${v.helperKindLabel ?: ""} · " + (if (v.supportRequest) "לא חירום - שיחה בלבד" else "בתוך אירוע חירום") +
            " · נפתח $timeHhmm"

    /** כותרת התראה (main.py: _os_notify למתנדב/ת) */
    fun notificationTitle(v: IncidentView) = if (isLiaison(v)) "👮 מבקשים ממך להזעיק רשויות" else "💬 מישהו צריך לדבר"
    fun notificationText(v: IncidentView) = "${v.helperKindLabel ?: ""} · פתח/י את LifeGuard"

    /**
     * שורת המיקום/פרטיות. שם ומיקום - רק למתנדב/ת הזעקה שקיבל/ה; מיקום ישן מסומן במפורש (לעולם לא כמיקום חי).
     * מחזיר (טקסט, טון).
     */
    fun locationLine(v: IncidentView): Pair<String, Tone> {
        val loc = v.location
        val liaison = isLiaison(v)
        return when {
            liaison && isAccepted(v) && loc != null -> {
                val stale = if (v.locationStale) "\n⚠️ המיקום לא התעדכן מעל 5 דקות - זה המיקום האחרון הידוע, לא מיקום חי" else ""
                "📍 ${"%.5f".format(java.util.Locale.US, loc.lat)}, ${"%.5f".format(java.util.Locale.US, loc.lon)} - למסירה לרשויות$stale" to
                    (if (v.locationStale) Tone.ERROR else Tone.MUTED)
            }
            liaison && isAccepted(v) -> "📍 אין עדיין מיקום מהפונה" to Tone.ERROR
            liaison -> "📍 השם והמיקום יוצגו רק אחרי שתקבל/י את הבקשה" to Tone.MUTED
            else -> "🔒 לשמירה על הפרטיות לא מוצגים שם ומיקום של הפונה" to Tone.MUTED
        }
    }

    /** מיקום להצגה/ניווט/העתקה - רק למתנדב/ת הזעקה שקיבל/ה. */
    fun shareableLocation(v: IncidentView) = if (isLiaison(v) && isAccepted(v)) v.location else null

    /** שם: רק למתנדב/ת הזעקה שקיבל/ה; אחרת קווי סיוע להפניה (לא למתנדב/ת הזעקה). */
    fun profileLine(v: IncidentView): String {
        if (isLiaison(v) && isAccepted(v)) return "שם: ${v.ownerDisplay}"
        val hot = v.hotlines.filter { it.number != null }
        return if (hot.isNotEmpty() && !isLiaison(v)) "קווי סיוע שאפשר להפנות אליהם: " +
            hot.joinToString(" · ") { "${it.name} ${it.number}" } else ""
    }

    /** כפתורי פעולה אחרי קבלה: פעולות הזעקה (למתנדב/ת הזעקה) לפי הסדר, ואחריהן "סיימתי". */
    fun actionButtons(v: IncidentView): List<Pair<String, String>> {
        if (!isAccepted(v) || !v.isActive) return emptyList()
        val out = ArrayList<Pair<String, String>>()
        if (isLiaison(v)) {
            val server = v.liaisonActions
            val keys = LIAISON_ACTIONS.keys.filter { server.isEmpty() || it in server } +
                server.keys.filter { it !in LIAISON_ACTIONS }
            for (k in keys) out += k to (server[k] ?: LIAISON_ACTIONS[k] ?: k)
        }
        out += "done" to "סיימתי"
        return out
    }

    /** צ'אט רק אחרי קבלה (השרת דוחה 403 accept_first לפני כן) */
    fun chatAllowed(v: IncidentView) = isAccepted(v) && v.isActive

    /** צלצול רק להזעקת רשויות (payload ring=true); שיחת תמיכה - התראה רגילה */
    fun shouldRing(v: IncidentView) = v.myRole == "helper" && v.ring == true && isLiaison(v)

    // ---------------- הגדרות מתנדב/ת ----------------
    /** features_ui.py apply_roles: "✓ פעיל" / "⏳ <סיבה מהשרת>" / "" (התפקיד לא נבחר) */
    fun roleStatusText(effective: String?): String = when (effective) {
        null -> ""
        "active" -> "✓ פעיל"
        else -> "⏳ $effective"
    }
}
