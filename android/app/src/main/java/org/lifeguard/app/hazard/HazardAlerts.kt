package org.lifeguard.app.hazard

import org.json.JSONObject
import org.lifeguard.app.core.TextStore
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

/**
 * התראות אינטראקטיביות על סכנה שה-AI זיהה - העתק של hazard_alerts.py (Windows): אותם קבועים, אותו ניהול חכם,
 * אותן מחרוזות (עם התאמות כנות לטלפון: חייגן במקום "אין חיוג במחשב", SOS בלי ספירה לאחור).
 *
 * עקרונות בטיחות (זהים ל-Windows):
 * - זיהוי AI לעולם לא מפעיל SOS ולא פונה לאנשי קשר / חירום בעצמו - רק מציע. כל פעולה משמעותית דורשת אישור מפורש.
 * - כיבוי החלונות לא מכבה את הזיהוי: הוא ממשיך ונרשם בלי חלון. התראות קריטיות של האפליקציה לא תלויות בזה.
 * - במצוקה שקטה / SOS פעיל - שום חלון (נרשם בלבד).
 * - "התראת שווא" = משוב משתמש/ת לא מאומת (label_strength=user_reported). לא משנה ספים - לעולם.
 * - לא נשמר אודיו ולא מיקום - רק מטא-דאטה של הזיהוי.
 */
object HazardAlerts {
    const val EVENT_GAP_S = 120.0
    const val COOLDOWN_DISMISS_S = 5 * 60.0
    const val COOLDOWN_FALSE_S = 15 * 60.0
    const val COOLDOWN_ACTION_S = 10 * 60.0
    const val MIN_GAP_ANY_S = 20.0

    val SEVERITY_RANK = mapOf("info" to 0, "low" to 1, "medium" to 2, "high" to 3)
    val MIN_SEVERITY_OPTIONS = linkedMapOf("high" to "רק סכנה גבוהה", "medium" to "בינונית ומעלה (מומלץ)", "low" to "כל זיהוי")
    val MIN_SEVERITY_OPTIONS_EN = linkedMapOf("high" to "High danger only", "medium" to "Medium and above (recommended)",
        "low" to "Every detection")
    val FAMILY = mapOf("gunshot" to "blast", "explosion" to "blast", "fireworks" to "blast", "scream" to "voice",
        "distress_voice" to "voice", "glass_break" to "break", "impact" to "break", "alarm" to "signal", "siren" to "signal")
    val FALSE_REASONS = linkedMapOf("music_tv" to "מוזיקה / טלוויזיה", "fireworks" to "זיקוקים", "horn" to "צופר רכב",
        "construction" to "עבודות / בנייה", "other" to "אחר")
    val REASONS_EN = linkedMapOf("music_tv" to "Music / TV", "fireworks" to "Fireworks", "horn" to "Vehicle horn",
        "construction" to "Construction", "other" to "Other")
    val RESPONSES = listOf("sos", "safe_place", "contacts", "call", "false_alarm", "dismiss")
    val ACTION_RESPONSES = setOf("sos", "safe_place", "contacts", "call")
    val LABELS_EN = mapOf("gunshot" to "Gunshot", "explosion" to "Explosion", "fireworks" to "Fireworks",
        "glass_break" to "Glass breaking", "siren" to "Siren", "alarm" to "Alarm", "scream" to "Scream",
        "impact" to "Heavy impact", "distress_voice" to "Distress voice (scream / shout / crying)")
    val EXPLAIN_EN = mapOf(
        "gunshot" to "A sound similar to a gunshot. Fireworks and drums can sound alike.",
        "explosion" to "A sound similar to an explosion. Fireworks and thunder can sound alike.",
        "alarm" to "An alarm sound. Car horns are sometimes detected as alarms.",
        "scream" to "A sound similar to a scream. Happy shouting and play sound alike.",
        "distress_voice" to "Sounds like screaming, shouting or crying. Happy shouting and play sound alike.",
        "glass_break" to "A glass-breaking sound.", "impact" to "A heavy impact.")

    fun rank(severity: String?) = SEVERITY_RANK[severity] ?: 0
    fun family(category: String) = FAMILY[category] ?: category

    fun severityAllowed(severity: String?, prefs: HazardPrefs) = rank(severity) >= rank(prefs.minSeverity)

    /** טקסט ההודעה לאנשי הקשר (POST /trusted/notify) - אומר במפורש שזה זיהוי AI לא מאומת. בלי מיקום. */
    fun contactsMessage(alert: HazardAlert, lang: String = "he"): String {
        val when_ = SimpleDateFormat("HH:mm", Locale.US).format(Date((alert.firstTs * 1000).toLong()))
        if (lang == "en") return "LifeGuard: a possible danger sound (${alert.category}) was detected near me at $when_. " +
            "This is an unverified AI detection. Please check on me."
        val label = alert.labelHe ?: alert.category
        return "LifeGuard: זוהה אצלי צליל שעשוי להעיד על סכנה ($label) בשעה $when_. " +
            "${HazardText.he.notVerified}. כדאי לבדוק מה שלומי."
    }
}

/** זיהוי בודד מהמוניטור. meta: score / threshold / model / model_version / quality / near_threshold / rule. */
data class Detection(
    val category: String,
    val severity: String,
    val confidence: Double,
    val labelHe: String?,
    val explanation: String?,
    val meta: Map<String, Any?> = emptyMap(),
)

/** אירוע התראה (אחד לכל קבוצת זיהויים). שדות משתנים - AlertManager מעדכן אותם (כמו dict ב-Python). */
class HazardAlert(
    val id: String,
    val family: String,
    var category: String,
    var severity: String,
    var confidence: Double,
    var labelHe: String?,
    var explanation: String?,
    val firstTs: Double,
    var lastTs: Double,
    var count: Int,
    var response: String? = null,
    var reason: String? = null,
    val meta: Map<String, Any?> = emptyMap(),
)

/** מחליט לכל זיהוי: new / update / grouped / cooldown / rate_limited. בלי ממשק - נבדק לבד (AlertManager ב-Python). */
class AlertManager(
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val idGen: () -> String = { "hz-" + UUID.randomUUID().toString().replace("-", "").take(12) },
) {
    val open = LinkedHashMap<String, HazardAlert>()
    /** family -> (עד מתי, דרגת החומרה שנסגרה) */
    val cooldown = HashMap<String, Pair<Double, Int>>()
    var lastPopup = 0.0
        private set

    @Synchronized
    fun decide(det: Detection): Pair<String, HazardAlert?> {
        val now = clock()
        val fam = HazardAlerts.family(det.category)
        val rank = HazardAlerts.rank(det.severity)
        val a = open[fam]
        if (a != null && now - a.lastTs <= HazardAlerts.EVENT_GAP_S) {
            a.count += 1
            a.lastTs = now
            if (rank > HazardAlerts.rank(a.severity)) {
                a.category = det.category; a.severity = det.severity
                a.confidence = maxOf(a.confidence, det.confidence)
                a.labelHe = det.labelHe; a.explanation = det.explanation
                return "update" to a
            }
            a.confidence = maxOf(a.confidence, det.confidence)
            return "grouped" to a
        }
        val cd = cooldown[fam]
        if (cd != null && now < cd.first && rank <= cd.second) return "cooldown" to null
        if (now - lastPopup < HazardAlerts.MIN_GAP_ANY_S && open.values.none { rank > HazardAlerts.rank(it.severity) }) {
            return "rate_limited" to null
        }
        val n = HazardAlert(idGen(), fam, det.category, det.severity, det.confidence, det.labelHe, det.explanation,
            now, now, 1, meta = det.meta)
        open[fam] = n
        lastPopup = now
        return "new" to n
    }

    /** תגובת המשתמש/ת -> סגירת האירוע ושקט לפי סוג התגובה. */
    @Synchronized
    fun close(alert: HazardAlert, response: String) {
        val now = clock()
        val secs = when (response) {
            "false_alarm" -> HazardAlerts.COOLDOWN_FALSE_S
            "dismiss" -> HazardAlerts.COOLDOWN_DISMISS_S
            else -> HazardAlerts.COOLDOWN_ACTION_S
        }
        alert.response = response
        open.remove(alert.family)
        cooldown[alert.family] = (now + secs) to HazardAlerts.rank(alert.severity)
    }

    @Synchronized
    fun find(id: String): HazardAlert? = open.values.firstOrNull { it.id == id }
}

/**
 * העדפות ההתראות. ברירות המחדל זהות ל-Windows (hazard_alerts.DEFAULTS) + background (רק באנדרואיד):
 * זיהוי ברקע מחזיק את המיקרופון פתוח, ולכן כבוי עד שמפעילים במפורש.
 */
data class HazardPrefs(
    val popups: Boolean = true,
    val sound: Boolean = true,
    val vibration: Boolean = false,
    /** high / medium / low */
    val minSeverity: String = "medium",
    /** he / en */
    val language: String = "he",
    /** "זיהוי סכנה ברקע" (מיקרופון פתוח בשירות חזית) */
    val background: Boolean = false,
) {
    fun toJson(): JSONObject = JSONObject().put("popups", popups).put("sound", sound).put("vibration", vibration)
        .put("min_severity", minSeverity).put("language", language).put("background", background)

    fun validate(): HazardPrefs {
        require(minSeverity in HazardAlerts.MIN_SEVERITY_OPTIONS) { "רמת חומרה לא מוכרת" }
        require(language in HazardText.LANGS) { "שפה לא נתמכת" }
        return this
    }

    companion object {
        /** ערכים חסרים / מסוג לא נכון / לא מוכרים -> ברירת המחדל לשדה הזה. */
        fun fromJson(o: JSONObject?): HazardPrefs {
            val d = HazardPrefs()
            if (o == null) return d
            fun b(k: String, def: Boolean) = (o.opt(k) as? Boolean) ?: def
            fun s(k: String, def: String, ok: Collection<String>) = (o.opt(k) as? String)?.takeIf { it in ok } ?: def
            return HazardPrefs(b("popups", d.popups), b("sound", d.sound), b("vibration", d.vibration),
                s("min_severity", d.minSeverity, HazardAlerts.MIN_SEVERITY_OPTIONS.keys), s("language", d.language, HazardText.LANGS),
                b("background", d.background))
        }
    }
}

/**
 * אחסון ההעדפות: JSON מוצפן. במכשיר - SecureStore.putSecret/getSecret (AES-GCM, מפתח ב-Android Keystore), כמו שאר
 * הנתונים הרגישים באפליקציה; בבדיקות - SealedFileTextStore עם מפתח תוכנה.
 */
class HazardPrefsStore(private val store: TextStore) {
    @Volatile private var cache: HazardPrefs? = null

    fun load(): HazardPrefs = cache ?: (runCatching { HazardPrefs.fromJson(store.read()?.let { JSONObject(it) }) }
        .getOrDefault(HazardPrefs())).also { cache = it }

    fun save(p: HazardPrefs): HazardPrefs {
        p.validate()
        store.write(p.toJson().toString())
        cache = p
        return p
    }
}

/** מחרוזות הממשק - עברית (RTL) ואנגלית (LTR), לפי ההעדפה (לא לפי שפת המכשיר), כמו STRINGS ב-Windows. */
class HazardText(
    val title: String, val question: String, val sos: String, val safePlace: String, val contacts: String,
    val call: String, val falseAlarm: String, val dismiss: String, val severity: String, val confidence: String,
    val location: String, val noLocation: String, val updated: String, val sev: Map<String, String>,
    val confirmSos: String, val confirmContacts: String, val confirmCall: String, val why: String,
    val notVerified: String, val rtl: Boolean, val notConnected: String, val sending: String, val approx: String,
    val minAgo: String, val noContacts: String, val sent: String, val delivered: String, val sendFailed: String,
    val dialOpened: String, val noDialer: String, val yes: String, val no: String, val confirmFalse: String,
    val notActive: String, val searchingSafe: String, val navigate: String, val openApp: String,
) {
    fun sevLabel(s: String) = sev[s] ?: s
    fun label(alert: HazardAlert) = if (rtl) alert.labelHe ?: alert.category else HazardAlerts.LABELS_EN[alert.category] ?: alert.category
    fun explanation(alert: HazardAlert) = if (rtl) alert.explanation ?: "" else HazardAlerts.EXPLAIN_EN[alert.category] ?: ""
    fun reasons() = if (rtl) HazardAlerts.FALSE_REASONS else HazardAlerts.REASONS_EN

    companion object {
        val LANGS = setOf("he", "en")

        val he = HazardText(
            title = "LifeGuard זיהה סכנה אפשרית", question = "מה תרצה/י לעשות?", sos = "🚨 הפעלת SOS",
            safePlace = "📍 מקום בטוח", contacts = "👨‍👩‍👧 התראה לאנשי הקשר", call = "📞 חיוג לחירום",
            falseAlarm = "התראת שווא", dismiss = "סגירה", severity = "חומרה", confidence = "ביטחון בזיהוי",
            location = "מיקום", noLocation = "מיקום לא ידוע", updated = "עודכן - זוהה שוב",
            sev = mapOf("high" to "גבוהה", "medium" to "בינונית", "low" to "נמוכה", "info" to "מידע"),
            // באנדרואיד SOS נשלח מיד (אין ספירה לאחור כמו ב-Windows) - הנוסח אומר את זה בכנות
            confirmSos = "להפעיל עכשיו SOS? אנשי הקשר יקבלו התראה מיד, בדיוק כמו בהחזקת כפתור ה-SOS. " +
                "אפשר לבטל אחר כך ב\"אזעקת שווא\".",
            confirmContacts = "לשלוח לאנשי הקשר הודעה שזוהתה סכנה אפשרית? ההודעה תציין שזה זיהוי AI שלא אומת.",
            confirmCall = "לפתוח חיוג ל-{n}?", why = "למה זו התראת שווא? (לא חובה)",
            notVerified = "זיהוי של צליל בלבד - לא אישור לסכנה", rtl = true,
            notConnected = "לא מחובר לשרת", sending = "שולח...", approx = "משוער", minAgo = "לפני {m} דק'",
            noContacts = "אין אנשי קשר מאושרים במעגל הביטחון - ההודעה לא נשלחה לאף אחד",
            sent = "השרת שלח ל-{n} אנשי קשר. ממתין לאישור קבלה באפליקציות...",
            delivered = "{ok} מתוך {n} אישרו קבלה באפליקציה", sendFailed = "❌ ההודעה לא נשלחה: {e}",
            dialOpened = "נפתח החייגן עם {n} - לוחצים \"חיוג\" כדי להתקשר.",
            noDialer = "אין במכשיר הזה אפליקציית חיוג. חייגו {n} מטלפון אחר.",
            yes = "כן", no = "ביטול", confirmFalse = "התראת שווא ✓",
            notActive = "ההתראה הזו כבר לא פעילה (נסגרה או שהאפליקציה הופעלה מחדש).",
            searchingSafe = "⏳ מחפש מקום בטוח קרוב...", navigate = "ניווט", openApp = "פתיחה",
        )

        val en = HazardText(
            title = "LifeGuard detected a potential danger", question = "What would you like to do?",
            sos = "🚨 Activate SOS", safePlace = "📍 Find a safe place", contacts = "👨‍👩‍👧 Alert contacts",
            call = "📞 Call emergency", falseAlarm = "False alarm", dismiss = "Dismiss", severity = "Severity",
            confidence = "Detection confidence", location = "Location", noLocation = "Location unknown",
            updated = "Updated - detected again",
            sev = mapOf("high" to "High", "medium" to "Medium", "low" to "Low", "info" to "Info"),
            confirmSos = "Start SOS now? Your contacts are alerted immediately, exactly like holding the SOS button. " +
                "You can cancel it later with \"False alarm\".",
            confirmContacts = "Send your contacts a message that a potential danger was detected? It will say this " +
                "is an unverified AI detection.",
            confirmCall = "Open the dialer for {n}?", why = "Why is this a false alarm? (optional)",
            notVerified = "Sound-only detection - not a confirmation of danger", rtl = false,
            notConnected = "not connected to server", sending = "Sending...", approx = "approximate",
            minAgo = "{m} min ago", noContacts = "No approved contacts in your safety circle - nothing was sent",
            sent = "The server sent it to {n} contacts. Waiting for their apps to confirm receipt...",
            delivered = "{ok} of {n} confirmed receipt in the app", sendFailed = "❌ Not sent: {e}",
            dialOpened = "The dialer opened with {n} - press call to place the call.",
            noDialer = "This device has no dialer app. Dial {n} from another phone.",
            yes = "Yes", no = "Cancel", confirmFalse = "False alarm ✓",
            notActive = "This alert is no longer active (it was closed or the app restarted).",
            searchingSafe = "⏳ Looking for a nearby safe place...", navigate = "Navigate", openApp = "Open",
        )

        fun of(lang: String?) = if (lang == "en") en else he
    }
}
