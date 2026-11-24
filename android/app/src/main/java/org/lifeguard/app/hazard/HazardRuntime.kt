package org.lifeguard.app.hazard

import android.content.Context
import android.media.AudioManager
import android.media.RingtoneManager
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import org.lifeguard.app.LG
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.api.TrustedNotifyResult
import org.lifeguard.app.core.AesGcmSealer
import org.lifeguard.app.core.SealedFileTextStore
import org.lifeguard.app.core.TextStore
import org.lifeguard.app.service.EscortManager
import org.lifeguard.app.service.IncidentManager
import org.lifeguard.app.store.QueueKey
import org.lifeguard.app.store.SecureStore
import java.io.File
import kotlin.math.abs
import kotlin.math.sqrt

/** סינון אנרגיה לפני YAMNet (חיסכון בסוללה): חלון כמעט שקט לא מנותח. */
object EnergyGate {
    /** ‎-50dBFS - הרבה מתחת לדיבור / צעקה / ירי במרחק; מעל רעש רקע של חדר שקט */
    const val MIN_RMS = 0.00316
    /** צליל קצר וחד (שבירה, ירייה רחוקה) עם RMS נמוך בחלון של 1.5 שנ' - עדיין עובר */
    const val MIN_PEAK = 0.05

    fun passes(x: FloatArray): Boolean {
        if (x.isEmpty()) return false
        var sq = 0.0
        var peak = 0f
        for (v in x) { sq += (v * v).toDouble(); val a = abs(v); if (a > peak) peak = a }
        return sqrt(sq / x.size) >= MIN_RMS || peak >= MIN_PEAK
    }

    fun isDigitalSilence(x: FloatArray) = x.all { it == 0f }
}

/** TextStore מעל SecureStore (AES-GCM, מפתח ב-Android Keystore) - כמו מספרי ה-SMS והעדפת מקום התפילה. */
class SecretTextStore(private val store: SecureStore, private val name: String) : TextStore {
    override fun read(): String? = store.getSecret(name)
    override fun write(value: String?) = store.putSecret(name, value)
}

/**
 * הרכבה במכשיר: מודלים מה-assets, YAMNet (TFLite), מוניטור, יומן, פעולות, התראות. השירות (LifeGuardService) מזין חלונות
 * מהמיקרופון רק כשהמשתמש/ת הפעיל/ה "זיהוי סכנה ברקע". שום דבר כאן לא מפעיל SOS או פונה לאיש בלי לחיצה + אישור.
 */
object HazardRuntime {
    private const val PREFS_KEY = "hazard_alert_prefs"
    private const val MODELS_JSON = "hazard/hazard_models.json"
    private const val MODELS_BIN = "hazard/hazard_models.bin"

    val prefsStore: HazardPrefsStore by lazy { HazardPrefsStore(SecretTextStore(LG.store, PREFS_KEY)) }
    val log: HazardLog by lazy {
        HazardLog(SealedFileTextStore(File(LG.app.noBackupFilesDir, "hazard/alerts.bin"), AesGcmSealer { QueueKey.get() }))
    }
    val manager = AlertManager()

    private val _changes = MutableSharedFlow<String>(extraBufferCapacity = 16)
    /** מזהה התראה שעודכנה/נסגרה - לחלון הפתוח */
    val changes: SharedFlow<String> = _changes

    /** מצב הזיהוי ברקע לתצוגה בהגדרות (נקבע ע"י השירות) */
    @Volatile var listenStatus: String = STATUS_OFF

    @Volatile private var models: HazardModels? = null
    @Volatile private var yam: YamnetRunner? = null
    @Volatile private var engineError: String? = null
    @Volatile private var loaded = false

    fun prefs(): HazardPrefs = prefsStore.load()

    /** null = המנוע זמין; אחרת הסיבה (בעברית). טוען פעם אחת. */
    @Synchronized
    fun engineUnavailableReason(ctx: Context = LG.app): String? {
        if (!loaded) {
            loaded = true
            try {
                val m = HazardModels.parse(ctx.assets.open(MODELS_JSON).use { String(it.readBytes(), Charsets.UTF_8) },
                    ctx.assets.open(MODELS_BIN).use { it.readBytes() })
                models = m
                val present = runCatching { ctx.assets.openFd(m.yamnetAsset).close(); true }.getOrDefault(false)
                if (!present) engineError = "מודל YAMNet לא נכלל בגרסה הזו של האפליקציה - הזיהוי הקולי לא זמין"
                else yam = YamnetTflite(ctx, m.yamnetAsset)
            } catch (e: Throwable) {
                engineError = "מנוע הזיהוי לא נטען (${e.javaClass.simpleName})"
            }
        }
        return engineError
    }

    /** כמו engineUnavailableReason, בלי לטעון (לממשק - הטעינה עצמה ב-thread רקע) */
    fun engineReasonIfLoaded(): String? = if (loaded) engineError else null

    val monitor: HazardMonitor by lazy {
        engineUnavailableReason()
        val m = models
        val intel = m?.let { AudioIntel(it) }
        HazardMonitor(
            analyze = { w -> intel?.analyze(w, yam) },
            canShow = ::canShowNow,
            onAlert = ::onAlert,
            record = { a, shown, why -> log.recordAlert(a, shown, why) },
            prefs = ::prefs,
            manager = manager,
            sequenceRule = m?.shoutRule,
        )
    }

    val actions: HazardActions by lazy {
        HazardActions(manager, log,
            connected = { LG.store.isLoggedIn && LG.isOnline() && LG.networkAvailable() },
            notifier = {
                val api = LG.api()
                object : TrustedNotifier {
                    override fun notifyTrusted(text: String): TrustedNotifyResult = api.notifyTrusted(text)
                    override fun notificationStatus(ids: Map<String, Long>) = api.notificationStatus(ids)
                }
            },
            // אחרי אישור מפורש בלבד: אותו מסלול כמו החזקת כפתור ה-SOS (trigger=manual)
            startSos = { IncidentManager.startSos(LG.app, "loud", source = "hazard_alert") })
    }

    /** מצב האפליקציה: אין חלון במצוקה שקטה / ליווי שקט / SOS פעיל. */
    fun canShowNow(): Pair<Boolean, String?> = HazardGate.canShow(IncidentManager.isActive, IncidentManager.activeMode,
        EscortManager.activeId != null, LG.store.escortMode)

    fun find(id: String): HazardAlert? = manager.find(id)

    fun changed(id: String) { _changes.tryEmit(id) }

    private fun onAlert(kind: String, alert: HazardAlert) {
        val p = prefs()
        Notifier.showHazard(LG.app, alert, p, updated = kind == "update")
        if (kind == "new") playEffects(LG.app, p)          // כמו ב-Windows: צליל רק בחלון חדש, לא בעדכון
        changed(alert.id)
    }

    /** צליל / רטט לפי ההעדפות. ערוץ ההתראה עצמו שקט, כדי שההעדפות האלה יקבעו (ומצב שקט של הטלפון מכובד). */
    private fun playEffects(ctx: Context, p: HazardPrefs) {
        val am = ctx.getSystemService(AudioManager::class.java)
        val ringer = am?.ringerMode ?: AudioManager.RINGER_MODE_NORMAL
        if (p.sound && ringer == AudioManager.RINGER_MODE_NORMAL) runCatching {
            RingtoneManager.getRingtone(ctx, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION))?.play()
        }
        if (p.vibration && ringer != AudioManager.RINGER_MODE_SILENT) runCatching {
            val v = if (Build.VERSION.SDK_INT >= 31) ctx.getSystemService(VibratorManager::class.java)?.defaultVibrator
            else @Suppress("DEPRECATION") ctx.getSystemService(Vibrator::class.java)
            v?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 400, 200, 400), -1))
        }
    }

    const val STATUS_OFF = "הזיהוי ברקע כבוי (ברירת המחדל באנדרואיד: הוא מחזיק את המיקרופון פתוח)"
    const val STATUS_ACTIVE = "הזיהוי ברקע פעיל (מקומי במכשיר, בלי שמירה או שליחה של אודיו)"
    const val STATUS_NO_PERMISSION = "הזיהוי ברקע לא פעיל: אין הרשאת מיקרופון"
    const val STATUS_NOT_LOGGED_IN = "הזיהוי ברקע לא פעיל: צריך להתחבר לחשבון (הוא רץ בשירות LifeGuard)"
    const val STATUS_PAUSED_SOS = "הזיהוי ברקע מושהה בזמן SOS פעיל (המיקרופון משמש לתיעוד, אם הופעל)"
    const val STATUS_SILENT_MIC = "המיקרופון מחזיר שקט מוחלט - כנראה חסום (מתג הפרטיות של אנדרואיד) או בשימוש אפליקציה אחרת"
}
