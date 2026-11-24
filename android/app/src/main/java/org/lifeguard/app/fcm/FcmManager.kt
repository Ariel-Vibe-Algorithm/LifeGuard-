package org.lifeguard.app.fcm

import android.app.Application
import android.content.Context
import android.os.Build
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import org.lifeguard.app.BuildConfig
import org.lifeguard.app.LG
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.SetupStatus
import org.lifeguard.app.core.AesGcmSealer
import org.lifeguard.app.core.FcmConfig
import org.lifeguard.app.core.FcmDiagState
import org.lifeguard.app.core.FcmServerStatus
import org.lifeguard.app.core.FcmTokenRegistry
import org.lifeguard.app.core.SealedFileTextStore
import org.lifeguard.app.core.UrlPolicy
import org.lifeguard.app.store.QueueKey
import java.io.File
import java.io.IOException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * שכבת Android דקה ל-FCM (הלוגיקה ב-core/Fcm.kt).
 * - אין firebase.properties בבנייה (BuildConfig.FCM_CONFIGURED=false) -> לא נוגעים ב-Firebase בכלל; "FCM לא מוגדר".
 * - יש -> FirebaseApp.initializeApp ידני (בלי google-services ובלי FirebaseInitProvider), טוקן רק כשמחוברים,
 *   רישום בשרת עם תור מוצפן וניסיונות חוזרים, ביטול + deleteToken בהתנתקות.
 * כל כשל נבלע ונרשם בלוג - FCM הוא ערוץ השכמה נוסף, אף פעם לא תנאי לקבלת קריאות (ה-long-poll ממשיך כרגיל).
 */
object FcmManager {
    private const val TAG = "LifeGuardFcm"
    private const val TOKEN_TIMEOUT_S = 30L
    private const val TOKEN_REFETCH_SECONDS = 10 * 60.0

    val config = FcmConfig(BuildConfig.FCM_API_KEY, BuildConfig.FCM_APP_ID, BuildConfig.FCM_PROJECT_ID,
        BuildConfig.FCM_SENDER_ID)

    /** הוגדר בבנייה (firebase.properties) וההגדרה תקינה */
    val appConfigured: Boolean get() = BuildConfig.FCM_CONFIGURED && config.isValid

    @Volatile var initialized = false
        private set
    @Volatile var initError: String? = null
        private set
    @Volatile private var tokenError: String? = null
    @Volatile private var lastTokenFetch = 0.0
    /** מטמון לבדיקת המוכנות (בלי פענוח הקובץ ב-thread הראשי) */
    @Volatile private var cachedStatus = FcmTokenRegistry.Status.NO_TOKEN
    @Volatile private var cachedError: String? = null

    private lateinit var app: Context
    /** thread אחד - סדר הפעולות נשמר (deleteToken של התנתקות לפני getToken של התחברות חדשה) */
    private val exec = Executors.newSingleThreadExecutor { r -> Thread(r, "lifeguard-fcm").apply { isDaemon = true } }

    private val registry: FcmTokenRegistry by lazy {
        FcmTokenRegistry(SealedFileTextStore(File(app.noBackupFilesDir, "fcm/registration.bin"), AesGcmSealer { QueueKey.get() }))
    }

    fun init(application: Application) {
        app = application
        if (!BuildConfig.FCM_CONFIGURED) {
            Log.i(TAG, "${FcmConfig.NOT_CONFIGURED} (אין firebase.properties) - קבלת קריאות דרך long-poll בלבד")
            return
        }
        config.problem()?.let {
            initError = it
            Log.w(TAG, "הגדרת FCM לא תקינה: $it")
            return
        }
        try {
            if (FirebaseApp.getApps(application).isEmpty()) {
                FirebaseApp.initializeApp(application, FirebaseOptions.Builder()
                    .setApiKey(config.apiKey)
                    .setApplicationId(config.appId)
                    .setProjectId(config.projectId)
                    .setGcmSenderId(config.senderId)
                    .build())
            }
            initialized = true
        } catch (e: Exception) {
            initError = e.javaClass.simpleName + (e.message?.let { ": ${it.take(120)}" } ?: "")
            Log.w(TAG, "FirebaseApp.initializeApp failed: $initError")
            return
        }
        exec.execute { refreshCache() }
        if (LG.store.isLoggedIn) onLogin()
    }

    /** מזהה החשבון הנוכחי (hash של שרת + משתמש), null כשלא מחובר/ת */
    private fun account(): String? {
        val u = LG.store.username ?: return null
        if (LG.store.token == null) return null
        return FcmTokenRegistry.accountKey(LG.store.serverUrl, u)
    }

    private fun device() = "Android ${Build.VERSION.RELEASE}"

    private fun refreshCache() {
        runCatching {
            cachedStatus = registry.status(account())
            cachedError = registry.state().lastError
        }
    }

    /** התחברות (או עליית האפליקציה כשכבר מחוברים): מפעילים auto-init, מבקשים טוקן ורושמים בשרת. */
    fun onLogin() {
        if (!initialized) return
        exec.execute {
            fetchToken()
            flushNow(force = true)
        }
    }

    private fun fetchToken() {
        lastTokenFetch = LG.nowSec()
        try {
            val fm = FirebaseMessaging.getInstance()
            fm.isAutoInitEnabled = true
            val t = Tasks.await(fm.token, TOKEN_TIMEOUT_S, TimeUnit.SECONDS)
            if (!t.isNullOrBlank()) registry.onNewToken(t)
            tokenError = null
        } catch (e: Exception) {
            // בלי Google Play Services / בלי רשת / SERVICE_NOT_AVAILABLE - ננסה שוב מה-tick של השירות
            tokenError = (e.cause ?: e).javaClass.simpleName
            Log.w(TAG, "getToken failed: $tokenError")
        }
        refreshCache()
    }

    /** מ-FirebaseMessagingService.onNewToken (thread של Firebase). */
    fun onNewToken(token: String) {
        if (!initialized) return
        runCatching { registry.onNewToken(token) }
        exec.execute { flushNow(force = true) }
    }

    /** רישום בשרת אם צריך. thread רקע בלבד. force = בלי לחכות ל-backoff (אירוע חדש: טוקן / התחברות / "בדוק שוב"). */
    fun flushNow(force: Boolean = false) {
        if (!initialized) return
        val acct = account() ?: return
        val token = registry.due(acct, force) ?: return refreshCache()
        try {
            val r = LG.api().registerFcm(token, device())
            registry.onRegistered(token, acct, r.serverFcm)
            if (r.serverFcm != FcmServerStatus.ACTIVE) Log.i(TAG, "registered; server fcm=${r.serverFcm} (השרת לא ישלח השכמות)")
        } catch (e: ApiException) {
            registry.onFailure(token, acct, FcmTokenRegistry.Failure.classify(e.status, e.code))
            Log.w(TAG, "register failed: ${e.status} ${e.code}")
        } catch (e: IOException) {
            registry.onFailure(token, acct, FcmTokenRegistry.Failure.classify(null, null))
        } catch (e: IllegalStateException) {
            // כתובת שרת לא מותרת - אין מה לעשות עד התחברות מחדש
        }
        refreshCache()
    }

    /** מהשירות כל 10 שנ' (thread רקע): ניסיון רישום חוזר לפי backoff; טוקן חסר -> בקשה חוזרת כל 10 דק'. */
    fun tick(online: Boolean) {
        if (!initialized || !online) return
        exec.execute {
            if (cachedStatus == FcmTokenRegistry.Status.NO_TOKEN && account() != null &&
                LG.nowSec() - lastTokenFetch > TOKEN_REFETCH_SECONDS) fetchToken()
            flushNow()
        }
    }

    /**
     * תוצאת /v1/me/setup (מבדיקת המוכנות): השרת לא מכיר אף מכשיר שלי (למשל מחק טוקן שפג) למרות שהמכשיר חושב שהוא
     * רשום -> רישום מחדש.
     */
    fun onSetup(setup: SetupStatus) {
        if (!initialized) return
        if (setup.fcmDevices == 0 && runCatching { registry.status(account()) }.getOrNull() == FcmTokenRegistry.Status.REGISTERED) {
            registry.markStale()
            exec.execute { flushNow(force = true) }
        }
    }

    /**
     * לפני התנתקות / החלפת שרת, כשהחיבור לשרת עוד תקף (thread רקע, לפני POST /auth/logout):
     * DELETE /v1/me/devices/fcm - best effort.
     */
    fun unregisterBlocking() {
        if (!initialized) return
        val token = runCatching { registry.state().let { it.registeredToken ?: it.token } }.getOrNull() ?: return
        runCatching { LG.api().unregisterFcm(token) }
            .onFailure { Log.i(TAG, "unregister (best effort) failed: ${it.javaClass.simpleName}") }
    }

    /**
     * התנתקות (כל מסלול - כולל 401). מוחק את מצב הרישום ואת הטוקן ב-Firebase (deleteToken) - גם אם ה-DELETE לשרת
     * לא הגיע, השרת יקבל UNREGISTERED בשליחה הבאה וימחק את הטוקן. כל thread.
     */
    fun onLogout() {
        if (!initialized) return
        val url = (UrlPolicy.normalize(LG.store.serverUrl) as? UrlPolicy.Result.Ok)?.baseUrl
        val auth = LG.store.token
        val token = runCatching { registry.clear() }.getOrNull()
        cachedStatus = FcmTokenRegistry.Status.NO_TOKEN
        cachedError = null
        exec.execute {
            // אם unregisterBlocking לא נקרא (למשל 401 / "התחבר/י מחדש") - ניסיון אחרון עם הטוקן שנלכד לפני הניקוי
            if (token != null && url != null && auth != null) runCatching { ApiClient(url, { auth }).unregisterFcm(token) }
            try {
                val fm = FirebaseMessaging.getInstance()
                fm.isAutoInitEnabled = false
                Tasks.await(fm.deleteToken(), TOKEN_TIMEOUT_S, TimeUnit.SECONDS)
            } catch (e: Exception) {
                Log.w(TAG, "deleteToken failed: ${(e.cause ?: e).javaClass.simpleName}")
            }
        }
    }

    // ------------------------------------------------------------ השכמה אחרונה (לבדיקת המוכנות)
    private val prefs by lazy { app.getSharedPreferences("lifeguard_fcm", Context.MODE_PRIVATE) }

    fun recordWake() {
        runCatching { prefs.edit().putLong("last_wake_ms", System.currentTimeMillis()).apply() }
    }

    private val lastWakeAt: Double?
        get() = runCatching { prefs.getLong("last_wake_ms", 0L) }.getOrDefault(0L).takeIf { it > 0 }?.let { it / 1000.0 }

    fun diagState(): FcmDiagState = FcmDiagState(
        appConfigured = appConfigured,
        configProblem = if (!BuildConfig.FCM_CONFIGURED) FcmConfig.NOT_CONFIGURED else config.problem(),
        initError = if (appConfigured) initError else null,
        registration = if (initialized) cachedStatus else FcmTokenRegistry.Status.NO_TOKEN,
        lastError = cachedError ?: tokenError,
        lastWakeAt = if (initialized) lastWakeAt else null,
    )
}
