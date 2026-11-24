package org.lifeguard.app.ui

import android.Manifest
import android.content.Intent
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.delay
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.core.PhoneNumbers
import org.lifeguard.app.core.WorshipPref
import org.lifeguard.app.databinding.ActivitySettingsBinding
import org.lifeguard.app.fcm.FcmManager
import org.lifeguard.app.hazard.HazardRuntime
import org.lifeguard.app.service.LifeGuardService
import org.lifeguard.app.service.SmsSender
import org.lifeguard.app.service.PrivacySync
import android.widget.Toast

/** הגדרות: מספרי SMS לחירום בלי אינטרנט (מוצפנים), זיהוי נפילה/עצירה, סירנה, שרת. */
class SettingsActivity : AppCompatActivity() {
    private lateinit var b: ActivitySettingsBinding

    private val smsPerm = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        b.numbersMsg.text = if (ok) getString(R.string.sms_saved, LG.store.emergencyNumbers.size)
        else "ללא הרשאת SMS לא ניתן לשלוח הודעה כשאין אינטרנט"
    }

    private val micPerm = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        // ההרשאה ניתנה מראש -> מפעילים בשרת. סירוב -> נשאר כבוי.
        if (ok) setRecording(true) else { b.recordingInfo.text = "בלי הרשאת מיקרופון לא ניתן להקליט"; renderRecording() }
    }

    private var policyLoaded = false

    /** "התראות סכנה מ-AI" (הזיהוי ברקע מבקש הרשאת מיקרופון משלו - נפרד מהקלטת הראיות) */
    private lateinit var hazardSection: HazardSettingsSection
    private val hazardMicPerm = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        hazardSection.onMicResult(ok)
    }

    private fun setRecording(enabled: Boolean) {
        b.audioSwitch.isEnabled = false
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().setRecordOnEmergency(enabled).also { PrivacySync.savePolicy(it) } } }
            r.exceptionOrNull()?.let { Toast.makeText(this@SettingsActivity, Ui.errorText(this@SettingsActivity, it), Toast.LENGTH_LONG).show() }
            renderRecording(r.exceptionOrNull()?.let { Ui.errorText(this@SettingsActivity, it) })
        }
    }

    private fun loadPolicy() {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { PrivacySync.refresh(force = true) } }
            policyLoaded = r.isSuccess
            renderRecording(r.exceptionOrNull()?.let { Ui.errorText(this@SettingsActivity, it) })
        }
    }

    /** מצב ההקלטה לפי מדיניות השרת (ממטמון אם אין חיבור). */
    private fun renderRecording(error: String? = null) {
        val p = PrivacySync.cachedPolicy
        b.audioSwitch.setOnCheckedChangeListener(null)
        b.audioSwitch.isChecked = p?.recordOnEmergency == true
        b.audioSwitch.isEnabled = policyLoaded           // שינוי רק מול השרת
        b.audioSwitch.setOnCheckedChangeListener(audioListener)
        val lines = ArrayList<String>()
        if (error != null) lines += error +
            (if (p != null) " (מוצג המצב האחרון שנשמר)" else "")
        if (p == null) lines += "מדיניות ההקלטה עוד לא התקבלה מהשרת - עד אז לא מקליטים"
        else {
            if (p.discreet) lines += "פרופיל ילד/ה: הקלטה דיסקרטית פעילה עבור " + p.discreetGuardians.joinToString(", ") +
                " (בחירום מאושר - בלי צורך בלחיצה; סמן המיקרופון של הטלפון מופיע)"
            lines += if (p.upload) "ההקלטות יועלו מוצפנות ל: " + p.recipients.joinToString(", ")
            else "אף איש קשר לא קיבל גישה להקלטות - ההקלטות יישמרו במכשיר בלבד (מגדירים ב\"שיתוף מיקום והרשאות\")"
            lines += "נשמרות בשרת ${p.retentionDays} יום"
            p.notes.forEach { lines += "• $it" }
        }
        val micOk = androidx.core.content.ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
        if (p != null && (p.recordOnEmergency || p.discreet) && !micOk) lines += "⚠ אין הרשאת מיקרופון - לא יוקלט כלום. הפעילו מחדש את המתג כדי לבקש אותה."
        b.recordingInfo.text = lines.joinToString("\n")
    }

    private val audioListener: android.widget.CompoundButton.OnCheckedChangeListener = android.widget.CompoundButton.OnCheckedChangeListener { sw, v ->
        if (!v) { setRecording(false); return@OnCheckedChangeListener }
        sw.setOnCheckedChangeListener(null); sw.isChecked = false; sw.setOnCheckedChangeListener(audioListenerRef())
        // ההרשאה מתבקשת עכשיו, מראש, עם הסבר - בזמן חירום אין זמן לאשר (ובשירות ברקע אי אפשר לבקש)
        androidx.appcompat.app.AlertDialog.Builder(this).setMessage(R.string.audio_evidence_explain)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                if (androidx.core.content.ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
                    android.content.pm.PackageManager.PERMISSION_GRANTED) setRecording(true)
                else micPerm.launch(Manifest.permission.RECORD_AUDIO)
            }
            .setNegativeButton(android.R.string.cancel, null).show()
    }

    private fun audioListenerRef(): android.widget.CompoundButton.OnCheckedChangeListener = audioListener

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(b.root)
        title = getString(R.string.settings_title)

        b.numbers.setText(LG.store.emergencyNumbers.joinToString("\n"))
        b.saveNumbers.setOnClickListener { saveNumbers() }
        b.testSms.setOnClickListener { testSms() }

        b.fallSwitch.isChecked = LG.store.fallDetection
        b.motionSwitch.isChecked = LG.store.motionMonitoring
        b.sirenSwitch.isChecked = LG.store.localSiren
        b.fallSwitch.setOnCheckedChangeListener { _, v -> LG.store.fallDetection = v; LifeGuardService.start(this) }
        b.motionSwitch.setOnCheckedChangeListener { _, v -> LG.store.motionMonitoring = v; LifeGuardService.start(this) }
        b.sirenSwitch.setOnCheckedChangeListener { _, v -> LG.store.localSiren = v }
        // הקלטה בחירום: מדיניות השרת (PUT /me/recording), לא הגדרה מקומית
        renderRecording()
        loadPolicy()
        // מקום תפילה כמפלט אחרון: רק במכשיר, מוצפן - לא נשלח לשרת
        b.worshipExplain.text = WorshipPref.EXPLAIN_HE
        b.worshipBox.addView(WorshipPrefUi.radioGroup(this, LG.store.worshipFallback) { key ->
            LG.store.worshipFallback = key
            b.worshipMsg.text = getString(R.string.worship_saved)
        }, 1)
        // התראות סכנה מ-AI: העדפות מוצפנות + זיהוי ברקע (כבוי כברירת מחדל)
        hazardSection = HazardSettingsSection(this, b.hazardBox) { hazardMicPerm.launch(Manifest.permission.RECORD_AUDIO) }
        hazardSection.build()
        lifecycleScope.launch {
            withContext(Dispatchers.IO) { HazardRuntime.engineUnavailableReason(this@SettingsActivity) }
            repeatOnLifecycle(Lifecycle.State.STARTED) { while (true) { hazardSection.renderListen(); delay(2000) } }
        }
        b.openEvidence.setOnClickListener { startActivity(EvidenceActivity.intent(this)) }
        b.openLocationSharing.setOnClickListener { startActivity(LocationSharingActivity.intent(this)) }
        b.openContactsLocation.setOnClickListener { startActivity(ContactLocationActivity.intent(this)) }
        b.openGuardian.setOnClickListener { startActivity(GuardianActivity.intent(this)) }
        b.openPrivacyHistory.setOnClickListener { startActivity(PrivacyHistoryActivity.intent(this)) }

        b.serverUrl.text = LG.store.serverUrl
        b.changeServer.setOnClickListener {
            lifecycleScope.launch {
                withContext(Dispatchers.IO) {
                    FcmManager.unregisterBlocking()      // DELETE /me/devices/fcm מול השרת הישן
                    runCatching { LG.api().logout() }
                }
                LifeGuardService.stop(this@SettingsActivity)
                FcmManager.onLogout()
                LG.store.clearSession()
                startActivity(Intent(this@SettingsActivity, LoginActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
                finish()
            }
        }
    }

    private fun saveNumbers(): Boolean {
        val (ok, bad) = PhoneNumbers.parseList(b.numbers.text.toString())
        LG.store.emergencyNumbers = ok
        b.numbers.setText(ok.joinToString("\n") + if (bad.isNotEmpty()) "\n" + bad.joinToString("\n") else "")
        b.numbersMsg.text = if (bad.isNotEmpty()) getString(R.string.sms_invalid, bad.joinToString(", "))
        else getString(R.string.sms_saved, ok.size)
        if (ok.isNotEmpty() && !SmsSender.hasPermission(this)) smsPerm.launch(Manifest.permission.SEND_SMS)
        return ok.isNotEmpty()
    }

    private fun testSms() {
        if (!saveNumbers()) return
        if (!SmsSender.hasPermission(this)) { smsPerm.launch(Manifest.permission.SEND_SMS); return }
        val name = LG.store.displayName ?: LG.store.username ?: ""
        val n = SmsSender.send(this, LG.store.emergencyNumbers,
            "LifeGuard: test message from $name (no emergency).", SmsSender.KIND_TEST)
        b.numbersMsg.text = "נשלחה הודעת בדיקה ל-$n מספרים"
    }
}
