package org.lifeguard.app.core

import org.json.JSONObject
import org.lifeguard.app.api.dbl
import org.lifeguard.app.api.str

/**
 * FCM (Firebase Cloud Messaging) כערוץ **השכמה בלבד** - לוגיקה טהורה (נבדקת ב-JVM):
 * - FcmConfig: הגדרת Firebase של האפליקציה (מ-firebase.properties דרך BuildConfig) + אימות
 * - FcmTokenRegistry: מכונת המצבים / התור של רישום הטוקן בשרת (POST /v1/me/devices/fcm), מוצפן במנוחה (TextStore)
 * - WakeMessage / WakePolicy: פענוח הודעת ההשכמה והחלטה מה לעשות איתה
 *
 * פרטיות: ההודעה מהשרת היא {"t":"wake","k":<סוג>,"u":"1"|"0"} - בלי שם, מיקום או תוכן. **שום דבר מההודעה לא מוצג** -
 * היא רק מעירה את האפליקציה, שמושכת את ההתראות מהשרת שלנו (/v1/notifications) בחיבור המוצפן.
 */
data class FcmConfig(val apiKey: String, val appId: String, val projectId: String, val senderId: String) {
    /** כל השדות ריקים = אין קובץ firebase.properties (המצב הרגיל עד שנוצר פרויקט Firebase) */
    val isEmpty get() = listOf(apiKey, appId, projectId, senderId).all { it.isBlank() }

    /** null = תקין. אחרת הסבר בעברית למה ההגדרה לא שמישה. */
    fun problem(): String? {
        if (isEmpty) return NOT_CONFIGURED
        val all = listOf("fcm.apiKey" to apiKey, "fcm.appId" to appId, "fcm.projectId" to projectId, "fcm.senderId" to senderId)
        all.firstOrNull { it.second.isBlank() }?.let { return "חסר ${it.first} ב-firebase.properties" }
        all.firstOrNull { looksLikePlaceholder(it.second) }?.let { return "${it.first} עדיין ערך לדוגמה - להעתיק מקונסולת Firebase" }
        if (apiKey.length < 20 || apiKey.any { it.isWhitespace() }) return "fcm.apiKey לא תקין"
        if (!senderId.all { it.isDigit() }) return "fcm.senderId צריך להיות מספר (Project number)"
        val m = APP_ID_RE.matchEntire(appId) ?: return "fcm.appId לא בפורמט 1:<מספר>:android:<מזהה>"
        if (m.groupValues[1] != senderId) return "fcm.appId לא שייך ל-fcm.senderId (פרויקט אחר?)"
        if (!PROJECT_ID_RE.matches(projectId)) return "fcm.projectId לא תקין"
        return null
    }

    val isValid get() = problem() == null

    companion object {
        const val NOT_CONFIGURED = "FCM לא מוגדר"
        private val APP_ID_RE = Regex("^1:(\\d+):android:[0-9a-f]+$")
        private val PROJECT_ID_RE = Regex("^[a-z][a-z0-9-]{3,29}$")
        fun looksLikePlaceholder(v: String) = v.contains("REPLACE", ignoreCase = true) || v.contains('<') || v.contains("xxxx")
    }
}

/** תשובת POST /v1/me/devices/fcm: האם **השרת** מחזיק credentials לשליחת FCM. */
object FcmServerStatus {
    const val ACTIVE = "active"
    const val NOT_CONFIGURED = "not_configured"
    const val MISCONFIGURED = "misconfigured"
    fun normalize(s: String?): String? = when (s) {
        ACTIVE, NOT_CONFIGURED, MISCONFIGURED -> s
        null, "" -> null
        else -> MISCONFIGURED
    }
}

/**
 * רישום טוקן ה-FCM בשרת - תור של פריט אחד עם ניסיונות חוזרים. נשמר מוצפן (TextStore = קובץ AES-GCM במפתח Keystore).
 * הרישום שייך ל"חשבון" (hash של כתובת השרת + שם המשתמש): התחברות לחשבון/שרת אחר -> רישום מחדש של אותו טוקן.
 * - טוקן חדש (onNewToken / getToken) -> ממתין לרישום
 * - רשת / 5xx / 401 -> ניסיון חוזר (30 שנ' .. 30 דק')
 * - 404 (שרת בלי הנתיב) -> ניסיון חוזר כל 6 שעות
 * - 400 (טוקן לא תקין) / 4xx אחר -> נכשל לצמיתות עד טוקן חדש
 * התנתקות -> clear() (הטוקן עצמו נמחק ב-Firebase, ולכן גם אם ה-DELETE לשרת לא הגיע, השרת יקבל UNREGISTERED וימחק).
 */
class FcmTokenRegistry(
    private val store: TextStore,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
) {
    enum class Status { NO_TOKEN, PENDING, REGISTERED, FAILED }

    data class State(
        val token: String? = null,
        val registeredToken: String? = null,
        val registeredAccount: String? = null,
        val registeredAt: Double? = null,
        /** "fcm" מהתשובה האחרונה של השרת (active / not_configured / misconfigured) */
        val serverFcm: String? = null,
        val attempts: Int = 0,
        val nextAttemptAt: Double = 0.0,
        val lastError: String? = null,
        /** החשבון שבו הרישום נכשל לצמיתות (null = לא נכשל) */
        val failedAccount: String? = null,
    ) {
        fun toJson(): JSONObject = JSONObject().apply {
            token?.let { put("token", it) }
            registeredToken?.let { put("registered_token", it) }
            registeredAccount?.let { put("registered_account", it) }
            registeredAt?.let { put("registered_at", it) }
            serverFcm?.let { put("server_fcm", it) }
            put("attempts", attempts)
            put("next_attempt_at", nextAttemptAt)
            lastError?.let { put("last_error", it) }
            failedAccount?.let { put("failed_account", it) }
        }

        companion object {
            fun parse(o: JSONObject) = State(o.str("token"), o.str("registered_token"), o.str("registered_account"),
                o.dbl("registered_at"), o.str("server_fcm"), o.optInt("attempts", 0), o.dbl("next_attempt_at") ?: 0.0,
                o.str("last_error"), o.str("failed_account"))
        }
    }

    sealed class Failure {
        /** רשת / 5xx / 429 / 408 / 401 */
        data class Retry(val reason: String) : Failure()
        /** 404 - השרת לא מכיר את הנתיב (גרסה ישנה) */
        object Unsupported : Failure()
        /** 400 וכו' - לא ינסה שוב עד טוקן חדש */
        data class Permanent(val reason: String) : Failure()

        companion object {
            /** status=null -> שגיאת רשת */
            fun classify(status: Int?, code: String?): Failure = when {
                status == null -> Retry("network")
                status == 401 -> Retry("auth")
                status >= 500 || status == 429 || status == 408 -> Retry("http_$status")
                status == 404 -> Unsupported
                else -> Permanent(code ?: "http_$status")
            }
        }
    }

    @Synchronized fun state(): State =
        store.read()?.let { runCatching { State.parse(JSONObject(it)) }.getOrNull() } ?: State()

    private fun save(s: State) = store.write(if (s == State()) null else s.toJson().toString())

    @Synchronized
    fun status(account: String?): Status {
        val s = state()
        return when {
            s.token == null -> Status.NO_TOKEN
            account != null && s.registeredToken == s.token && s.registeredAccount == account -> Status.REGISTERED
            account != null && s.failedAccount == account -> Status.FAILED
            else -> Status.PENDING
        }
    }

    /** טוקן חדש מ-Firebase. אותו טוקן שכבר נרשם -> בלי שינוי. */
    @Synchronized
    fun onNewToken(token: String) {
        val t = token.trim()
        if (t.isEmpty()) return
        val s = state()
        if (s.token == t) return
        save(s.copy(token = t, attempts = 0, nextAttemptAt = 0.0, lastError = null, failedAccount = null))
    }

    /** הטוקן שצריך לרשום עכשיו בחשבון הזה, או null (אין / כבר רשום / נכשל לצמיתות / עוד לא הגיע זמן הניסיון). */
    @Synchronized
    fun due(account: String?, force: Boolean = false): String? {
        if (account == null) return null
        val s = state()
        val t = s.token ?: return null
        if (s.registeredToken == t && s.registeredAccount == account) return null
        if (s.failedAccount == account) return null
        if (!force && clock() < s.nextAttemptAt) return null
        return t
    }

    @Synchronized
    fun onRegistered(token: String, account: String, serverFcm: String?) {
        val s = state()
        if (s.token != token) return            // הטוקן התחלף בינתיים - הרישום החדש עוד ממתין
        save(s.copy(registeredToken = token, registeredAccount = account, registeredAt = clock(),
            serverFcm = FcmServerStatus.normalize(serverFcm), attempts = 0, nextAttemptAt = 0.0, lastError = null,
            failedAccount = null))
    }

    @Synchronized
    fun onFailure(token: String, account: String, f: Failure) {
        val s = state()
        if (s.token != token) return
        val now = clock()
        val attempts = s.attempts + 1
        save(when (f) {
            is Failure.Retry -> s.copy(attempts = attempts, nextAttemptAt = now + backoff(attempts), lastError = f.reason)
            Failure.Unsupported -> s.copy(attempts = attempts, nextAttemptAt = now + UNSUPPORTED_RETRY_SECONDS,
                lastError = "server_unsupported")
            is Failure.Permanent -> s.copy(attempts = attempts, lastError = f.reason, failedAccount = account)
        })
    }

    /** השרת מדווח שאין לי מכשירים רשומים (למשל מחק טוקן שפג) -> לרשום שוב בהקדם. */
    @Synchronized
    fun markStale() {
        val s = state()
        if (s.registeredToken == null) return
        save(s.copy(registeredToken = null, registeredAccount = null, registeredAt = null, nextAttemptAt = 0.0))
    }

    /** התנתקות / החלפת שרת: מחזיר את הטוקן הרשום (לביטול best effort בשרת) ומוחק הכל. */
    @Synchronized
    fun clear(): String? {
        val t = state().let { it.registeredToken ?: it.token }
        store.write(null)
        return t
    }

    companion object {
        const val MIN_BACKOFF_SECONDS = 30.0
        const val MAX_BACKOFF_SECONDS = 30 * 60.0
        const val UNSUPPORTED_RETRY_SECONDS = 6 * 3600.0

        fun backoff(attempts: Int): Double {
            val exp = (attempts - 1).coerceIn(0, 16)
            return (MIN_BACKOFF_SECONDS * (1L shl exp)).coerceAtMost(MAX_BACKOFF_SECONDS)
        }

        /** מזהה חשבון לא-קריא: hash של כתובת השרת + שם המשתמש (לא שומרים שם משתמש גלוי בקובץ). */
        fun accountKey(serverUrl: String, username: String): String =
            Hashes.sha256(serverUrl.trimEnd('/').lowercase() + "|" + username.lowercase()).take(24)
    }
}

/** הודעת ההשכמה מהשרת. כל שדה אחר בהודעה מתעלמים ממנו - ושום דבר לא מוצג. */
data class WakeMessage(val kind: String, val urgent: Boolean) {
    companion object {
        private val KIND_RE = Regex("^[a-z_]{1,32}$")

        /** null = לא הודעת השכמה של LifeGuard (מתעלמים). */
        fun parse(data: Map<String, String>): WakeMessage? {
            if (data["t"] != "wake") return null
            val k = data["k"]?.takeIf { KIND_RE.matches(it) } ?: "unknown"
            return WakeMessage(k, data["u"] == "1")
        }
    }
}

/** מה לעשות עם הודעת השכמה. */
object WakePolicy {
    enum class Action {
        /** לא מחובר / לא הודעת השכמה */
        IGNORE,
        /** השירות כבר רץ בתהליך: לבטל את ה-long-poll התקוע ולמשוך מיד */
        KICK_RUNNING_SERVICE,
        /** השירות לא רץ: להפעיל שירות חזית (הודעה בעדיפות גבוהה מעניקה חריגה קצרה) - הוא מושך מיד כשהוא עולה */
        START_SERVICE,
    }

    data class Decision(val action: Action, val reason: String, val wakeLockMs: Long)

    enum class Fallback { EXPEDITED_POLL, POLL }

    const val URGENT_WAKELOCK_MS = 30_000L
    const val NORMAL_WAKELOCK_MS = 10_000L

    fun decide(msg: WakeMessage?, loggedIn: Boolean, serviceRunning: Boolean): Decision = when {
        msg == null -> Decision(Action.IGNORE, "not_wake", 0)
        !loggedIn -> Decision(Action.IGNORE, "logged_out", 0)
        serviceRunning -> Decision(Action.KICK_RUNNING_SERVICE, "service_running",
            if (msg.urgent) URGENT_WAKELOCK_MS else NORMAL_WAKELOCK_MS)
        else -> Decision(Action.START_SERVICE, "service_stopped", if (msg.urgent) URGENT_WAKELOCK_MS else NORMAL_WAKELOCK_MS)
    }

    /**
     * אנדרואיד סירב להפעיל את שירות החזית (ForegroundServiceStartNotAllowedException ב-12+, למשל כשההודעה ירדה לעדיפות
     * רגילה כי חרגנו ממכסת ה-high priority): משיכה חד-פעמית דרך WorkManager - expedited אם דחוף.
     */
    fun fallback(msg: WakeMessage): Fallback = if (msg.urgent) Fallback.EXPEDITED_POLL else Fallback.POLL
}

/** מצב FCM מצד המכשיר לבדיקת המוכנות. */
data class FcmDiagState(
    /** BuildConfig.FCM_CONFIGURED + הגדרה תקינה */
    val appConfigured: Boolean,
    /** הסבר כשלא מוגדר / לא תקין */
    val configProblem: String?,
    /** FirebaseApp.initializeApp נכשל */
    val initError: String?,
    val registration: FcmTokenRegistry.Status,
    val lastError: String?,
    val lastWakeAt: Double?,
)
