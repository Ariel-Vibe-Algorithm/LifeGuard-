package org.lifeguard.app.store

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import org.lifeguard.app.BuildConfig
import org.lifeguard.app.core.PendingSos
import org.lifeguard.app.core.WorshipPref
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * אחסון מקומי. סודות (טוקן, מספרי SMS לחירום, SOS שממתין לשליחה) מוצפנים ב-AES-GCM עם מפתח
 * ב-Android Keystore (המפתח לא יוצא מהחומרה המאובטחת). השאר - SharedPreferences רגיל.
 */
class SecureStore(context: Context) {
    private val prefs = context.getSharedPreferences("lifeguard", Context.MODE_PRIVATE)
    private val secret = context.getSharedPreferences("lifeguard_secure", Context.MODE_PRIVATE)

    // ---------------- הצפנה ----------------
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        gen.init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build())
        return gen.generateKey()
    }

    @Synchronized
    fun putSecret(name: String, value: String?) {
        if (value == null) { secret.edit().remove(name).apply(); return }
        val c = Cipher.getInstance(TRANSFORMATION)
        c.init(Cipher.ENCRYPT_MODE, key())
        val out = c.iv + c.doFinal(value.toByteArray(Charsets.UTF_8))
        secret.edit().putString(name, Base64.encodeToString(out, Base64.NO_WRAP)).apply()
    }

    @Synchronized
    fun getSecret(name: String): String? {
        val b64 = secret.getString(name, null) ?: return null
        return try {
            val all = Base64.decode(b64, Base64.NO_WRAP)
            val c = Cipher.getInstance(TRANSFORMATION)
            c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, all, 0, 12))
            String(c.doFinal(all, 12, all.size - 12), Charsets.UTF_8)
        } catch (e: Exception) {
            null   // המפתח לא זמין (למשל שחזור גיבוי למכשיר אחר) -> כאילו אין ערך
        }
    }

    // ---------------- חשבון ----------------
    var token: String?
        get() = getSecret("token")
        set(v) = putSecret("token", v)

    var serverUrl: String
        get() = prefs.getString("server_url", null) ?: BuildConfig.DEFAULT_SERVER_URL
        set(v) = prefs.edit().putString("server_url", v).apply()

    var username: String?
        get() = prefs.getString("username", null)
        set(v) = prefs.edit().putString("username", v).apply()

    var displayName: String?
        get() = prefs.getString("display_name", null)
        set(v) = prefs.edit().putString("display_name", v).apply()

    val isLoggedIn get() = username != null && token != null

    /** -1 = עוד לא אותחל (התקנה חדשה/התחברות) -> מתחילים מ-/notifications/cursor */
    var lastNotificationId: Long
        get() = prefs.getLong("last_notification_id", -1L)
        set(v) = prefs.edit().putLong("last_notification_id", v).apply()

    // ---------------- אירוע שלי ----------------
    var activeIncidentId: String?
        get() = prefs.getString("active_incident_id", null)
        set(v) = prefs.edit().putString("active_incident_id", v).apply()

    var activeIncidentMode: String?
        get() = prefs.getString("active_incident_mode", null)
        set(v) = prefs.edit().putString("active_incident_mode", v).apply()

    /** בקשת שיחה פתוחה ("צריך/ה לדבר עם מישהו?" - לא חירום). נפרד מ-activeIncidentId: בלי מיקום, בלי SOS. */
    var supportIncidentId: String?
        get() = prefs.getString("support_incident_id", null)
        set(v) = prefs.edit().putString("support_incident_id", v).apply()

    /** סוגי עזרה שביקשתי באירוע הפעיל ("<incident>|kind,kind") - כדי לומר "אין זמינים" רק אחרי בקשה אמיתית */
    var requestedHelperKinds: String?
        get() = prefs.getString("requested_helper_kinds", null)
        set(v) = prefs.edit().putString("requested_helper_kinds", v).apply()

    /** אירוע שסגרתי ("אני בטוח/ה") בלי רשת - נשלח לשרת כשהקליטה תחזור */
    var pendingResolveId: String?
        get() = prefs.getString("pending_resolve_id", null)
        set(v) = prefs.edit().putString("pending_resolve_id", v).apply()

    var pendingSos: PendingSos?
        get() = getSecret("pending_sos")?.let { runCatching { PendingSos.parse(JSONObject(it)) }.getOrNull() }
        set(v) = putSecret("pending_sos", v?.toJson()?.toString())

    /** סטטוס לסגירה הממתינה: resolved | cancelled (אזעקת שווא) */
    var pendingResolveStatus: String
        get() = prefs.getString("pending_resolve_status", null) ?: "resolved"
        set(v) = prefs.edit().putString("pending_resolve_status", v).apply()

    /** מפתח יומן הראיות של האירוע הנוכחי (client_id או מזהה האירוע) */
    var evidenceKey: String?
        get() = prefs.getString("evidence_key", null)
        set(v) = prefs.edit().putString("evidence_key", v).apply()

    /** נשלח SMS עזרה לאירוע הנוכחי -> "אני בטוח/ה" ישלח גם SMS המשך */
    var sosSmsSent: Boolean
        get() = prefs.getBoolean("sos_sms_sent", false)
        set(v) = prefs.edit().putBoolean("sos_sms_sent", v).apply()

    // ---------------- ליווי ----------------
    var escortId: String?
        get() = prefs.getString("escort_id", null)
        set(v) = prefs.edit().putString("escort_id", v).apply()

    /** שניות epoch (זמן שרת) */
    var escortDeadline: Double
        get() = prefs.getLong("escort_deadline_ms", 0L) / 1000.0
        set(v) = prefs.edit().putLong("escort_deadline_ms", (v * 1000).toLong()).apply()

    /** "ride" לנסיעה בטוחה, אחרת null */
    var escortKind: String?
        get() = prefs.getString("escort_kind", null)
        set(v) = prefs.edit().putString("escort_kind", v).apply()

    /** loud | silent */
    var escortMode: String?
        get() = prefs.getString("escort_mode", null)
        set(v) = prefs.edit().putString("escort_mode", v).apply()

    /** תור לא-מקוון של הנסיעה (מיקומים / checkin) - מוצפן */
    var rideQueue: String?
        get() = getSecret("ride_queue")
        set(v) = putSecret("ride_queue", v)

    // ---------------- SMS לחירום ----------------
    var emergencyNumbers: List<String>
        get() = getSecret("emergency_numbers")?.split('\n')?.filter { it.isNotBlank() } ?: emptyList()
        set(v) = putSecret("emergency_numbers", v.joinToString("\n").ifEmpty { null })

    // ---------------- הגדרות ----------------
    var fallDetection: Boolean
        get() = prefs.getBoolean("fall_detection", true)
        set(v) = prefs.edit().putBoolean("fall_detection", v).apply()

    var motionMonitoring: Boolean
        get() = prefs.getBoolean("motion_monitoring", false)
        set(v) = prefs.edit().putBoolean("motion_monitoring", v).apply()

    var localSiren: Boolean
        get() = prefs.getBoolean("local_siren", true)
        set(v) = prefs.edit().putBoolean("local_siren", v).apply()

    /**
     * ההגדרה המקומית הישנה (גרסה 0.2) - הוחלפה במדיניות השרת (record_on_emergency). אם הייתה מופעלת, היא מועברת
     * פעם אחת לשרת (PUT /me/recording) ומתאפסת. לא משמשת יותר להחלטה אם להקליט.
     */
    var audioEvidence: Boolean
        get() = prefs.getBoolean("audio_evidence", false)
        set(v) = prefs.edit().putBoolean("audio_evidence", v).apply()

    // ---------------- הפעלה לפי ביטחון / פרטיות ----------------
    /** activation של האירוע הפעיל שלי מהשרת (unconfirmed -> לשאול את הבעלים) */
    var activeIncidentActivation: String?
        get() = prefs.getString("active_incident_activation", null)
        set(v) = prefs.edit().putString("active_incident_activation", v).apply()

    /** הבעלים אישר/ה "זה חירום" בלי רשת - נשלח ל-/confirm כשהקליטה חוזרת */
    var pendingConfirm: Boolean
        get() = prefs.getBoolean("pending_confirm", false)
        set(v) = prefs.edit().putBoolean("pending_confirm", v).apply()

    /** מדיניות ההקלטה האחרונה מהשרת (JSON של RecordingPolicy) */
    var recordingPolicyJson: String?
        get() = prefs.getString("recording_policy", null)
        set(v) = prefs.edit().putString("recording_policy", v).apply()

    var policyFetchedAt: Double
        get() = prefs.getLong("policy_fetched_ms", 0L) / 1000.0
        set(v) = prefs.edit().putLong("policy_fetched_ms", (v * 1000).toLong()).apply()

    /** כמה אנשי קשר במצב "always" (מטמון מ-/me/privacy/permissions) */
    var alwaysContacts: Int
        get() = prefs.getInt("always_contacts", 0)
        set(v) = prefs.edit().putInt("always_contacts", v).apply()

    /** השהיית שיתוף מיקום - המצב המקומי קובע מיד (גם בלי קליטה) */
    var sharingPaused: Boolean
        get() = prefs.getBoolean("sharing_paused", false)
        set(v) = prefs.edit().putBoolean("sharing_paused", v).apply()

    /** שינוי השהיה שעוד לא הגיע לשרת: "true"/"false" + זמן המכשיר */
    var pendingPause: String?
        get() = prefs.getString("pending_pause", null)
        set(v) = prefs.edit().putString("pending_pause", v).apply()

    var liveLastUploadAt: Double
        get() = prefs.getLong("live_last_upload_ms", 0L) / 1000.0
        set(v) = prefs.edit().putLong("live_last_upload_ms", (v * 1000).toLong()).apply()

    var liveLastFixAt: Double
        get() = prefs.getLong("live_last_fix_ms", 0L) / 1000.0
        set(v) = prefs.edit().putLong("live_last_fix_ms", (v * 1000).toLong()).apply()

    /** דיוק (מ') של המיקום החי האחרון שנשמר בשרת; -1 = לא ידוע */
    var liveLastAccuracy: Float
        get() = prefs.getFloat("live_last_accuracy", -1f)
        set(v) = prefs.edit().putFloat("live_last_accuracy", v).apply()

    var liveRefusedUntil: Double
        get() = prefs.getLong("live_refused_until_ms", 0L) / 1000.0
        set(v) = prefs.edit().putLong("live_refused_until_ms", (v * 1000).toLong()).apply()

    /**
     * לאיזה מקום תפילה להפנות בסכנה כשאין מקום בטוח אחר קרוב (WorshipPref.OPTIONS). העדפה דתית = מידע רגיש:
     * מוצפנת (AES-GCM, Keystore), רק במכשיר הזה - לא נשלחת לשרת, לא לאנשי הקשר ולא לאבחון/יומנים.
     * לא נקבע / ערך לא מוכר -> ברירת המחדל (בית כנסת, כמו קודם). לא נמחקת בהתנתקות (כמו מספרי ה-SMS).
     */
    var worshipFallback: String
        get() = WorshipPref.normalize(getSecret("worship_fallback"))
        set(v) {
            require(v in WorshipPref.OPTIONS) { "unknown worship preference" }
            putSecret("worship_fallback", v)
        }

    var batteryPromptShown: Boolean
        get() = prefs.getBoolean("battery_prompt_shown", false)
        set(v) = prefs.edit().putBoolean("battery_prompt_shown", v).apply()

    /** התנתקות: מוחק טוקן ומצב אירועים. מספרי ה-SMS וההגדרות נשמרים. */
    fun clearSession() {
        token = null
        pendingSos = null
        prefs.edit().remove("username").remove("display_name").remove("last_notification_id")
            .remove("active_incident_id").remove("active_incident_mode").remove("pending_resolve_id")
            .remove("support_incident_id").remove("requested_helper_kinds")
            .remove("escort_id").remove("escort_deadline_ms").remove("sos_sms_sent").remove("evidence_key")
            .remove("pending_resolve_status").remove("escort_kind").remove("escort_mode")
            .remove("active_incident_activation").remove("pending_confirm").remove("recording_policy")
            .remove("policy_fetched_ms").remove("always_contacts").remove("sharing_paused").remove("pending_pause")
            .remove("live_last_upload_ms").remove("live_last_fix_ms").remove("live_refused_until_ms")
            .remove("live_last_accuracy").apply()
        rideQueue = null
    }

    companion object {
        private const val KEYSTORE = "AndroidKeyStore"
        private const val ALIAS = "lifeguard_secrets_v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}
