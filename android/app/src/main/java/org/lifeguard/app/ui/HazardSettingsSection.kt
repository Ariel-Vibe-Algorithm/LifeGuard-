package org.lifeguard.app.ui

import android.Manifest
import android.content.pm.PackageManager
import android.view.View
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.materialswitch.MaterialSwitch
import org.lifeguard.app.R
import org.lifeguard.app.hazard.HazardAlerts
import org.lifeguard.app.hazard.HazardPrefs
import org.lifeguard.app.hazard.HazardRuntime
import org.lifeguard.app.service.LifeGuardService

/** הטקסטים של קטע ההגדרות (settings_ui._build_hazard_section ב-Windows + מה שייחודי לטלפון). */
object HazardSettingsText {
    const val EXPLAIN = "כשה-AI מזהה צליל שעשוי להעיד על סכנה (ירי, פיצוץ, שבירה, צרחה...) מופיעה התראה עם אפשרויות: " +
        "SOS, מקום בטוח, התראה לאנשי הקשר, חיוג לחירום, התראת שווא. ה-AI לעולם לא מפעיל SOS לבד, ולא פונה לאיש בלי לחיצה ואישור."
    const val POPUPS = "חלונות קופצים על סכנה שזוהתה"
    const val SOUND = "צליל התראה"
    const val VIBRATION = "רטט"
    const val MIN_SEVERITY = "להציג מ:"
    const val LANGUAGE = "שפת החלון:"
    const val BACKGROUND = "זיהוי סכנה ברקע"
    const val BACKGROUND_EXPLAIN = "כבוי כברירת מחדל, כי כשהוא פועל LifeGuard מחזיק את המיקרופון פתוח ברקע: סמל המיקרופון של " +
        "אנדרואיד יופיע וצריכת הסוללה תעלה מעט. הקול מנותח רק במכשיר, בחלונות של 1.5 שניות; קטעים שקטים מדולגים; שום " +
        "אודיו לא נשמר ולא נשלח. בזמן SOS עם תיעוד שמע הזיהוי מושהה. אפשר לכבות בכל רגע."
    const val BACKGROUND_CONFIRM = "להפעיל זיהוי סכנה ברקע?\n\nLifeGuard יחזיק את המיקרופון פתוח כל עוד השירות פועל (גם " +
        "כשהאפליקציה סגורה) וינתח את הקול במכשיר בלבד. שום אודיו לא נשמר ולא נשלח, והזיהוי לעולם לא מפעיל SOS לבד.\n\n" +
        "אנדרואיד יבקש הרשאת מיקרופון אם עוד לא ניתנה."
    const val ALWAYS_ON = "כשהחלונות כבויים: הזיהוי ממשיך לרוץ ונרשם (בלי חלון). נשארות פעילות תמיד, בלי קשר להגדרות האלה: " +
        "ה-SOS וההתראות שלו, \"הכל בסדר?\" עם הספירה לאחור אחרי נפילה, קריאות מאנשי הקשר והתראות מהשרת."
    const val SAVED = "נשמר (מוצפן, במכשיר הזה)"
    const val NO_MIC = "בלי הרשאת מיקרופון הזיהוי ברקע לא יכול לפעול - נשאר כבוי"
    val LANGS = linkedMapOf("he" to "עברית", "en" to "English")
}

/**
 * קטע "התראות סכנה מ-AI" בהגדרות: חלונות (ברירת מחדל פעיל), צליל, רטט, חומרה מינימלית (בינונית), שפה,
 * וזיהוי ברקע (ברירת מחדל כבוי). כל שינוי נשמר מיד, מוצפן (SecureStore / Keystore), ושורד הפעלה מחדש.
 */
class HazardSettingsSection(
    private val a: AppCompatActivity,
    private val box: LinearLayout,
    /** בקשת RECORD_AUDIO (ActivityResultLauncher של המסך) */
    private val requestMic: () -> Unit,
) {
    private lateinit var status: android.widget.TextView
    private lateinit var listen: android.widget.TextView
    private lateinit var bgSwitch: MaterialSwitch

    private fun prefs() = HazardRuntime.prefs()

    private fun save(p: HazardPrefs, restartService: Boolean = false) {
        runCatching { HazardRuntime.prefsStore.save(p) }
            .onSuccess { status.text = HazardSettingsText.SAVED }
            .onFailure { status.text = "לא נשמר: ${it.message}" }
        if (restartService) LifeGuardService.start(a)
        renderListen()
    }

    private fun switch(text: String, checked: Boolean, onChange: (Boolean) -> Unit) = MaterialSwitch(a).apply {
        this.text = text
        isChecked = checked
        setOnCheckedChangeListener { _, v -> onChange(v) }
    }

    private fun separator() = View(a).apply {
        setBackgroundColor(Ui.color(a, R.color.lg_separator))
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, Ui.dp(a, 1))
    }

    private fun radios(options: Map<String, String>, selected: String, onPick: (String) -> Unit) = RadioGroup(a).apply {
        orientation = RadioGroup.VERTICAL
        for ((key, label) in options) {
            val rb = RadioButton(a).apply { id = View.generateViewId(); tag = key; text = label; textSize = 15f }
            addView(rb)
            if (key == selected) check(rb.id)
        }
        setOnCheckedChangeListener { g, id -> (g.findViewById<RadioButton>(id)?.tag as? String)?.let(onPick) }
    }

    fun build() {
        val p = prefs()
        box.addView(Ui.text(a, HazardSettingsText.EXPLAIN, 14f, color = Ui.gray(a), topDp = 0))
        box.addView(switch(HazardSettingsText.POPUPS, p.popups) { save(prefs().copy(popups = it)) })
        box.addView(separator())
        box.addView(switch(HazardSettingsText.SOUND, p.sound) { save(prefs().copy(sound = it)) })
        box.addView(separator())
        box.addView(switch(HazardSettingsText.VIBRATION, p.vibration) { save(prefs().copy(vibration = it)) })
        box.addView(Ui.text(a, HazardSettingsText.MIN_SEVERITY, 14f, bold = true, topDp = 12))
        box.addView(radios(HazardAlerts.MIN_SEVERITY_OPTIONS, p.minSeverity) { save(prefs().copy(minSeverity = it)) })
        box.addView(Ui.text(a, HazardSettingsText.LANGUAGE, 14f, bold = true, topDp = 8))
        box.addView(radios(HazardSettingsText.LANGS, p.language) { save(prefs().copy(language = it)) })
        box.addView(separator().apply { (layoutParams as LinearLayout.LayoutParams).topMargin = Ui.dp(a, 8) })
        bgSwitch = switch(HazardSettingsText.BACKGROUND, p.background) { onBackgroundToggle(it) }
        box.addView(bgSwitch)
        box.addView(Ui.text(a, HazardSettingsText.BACKGROUND_EXPLAIN, 13f, color = Ui.gray(a), topDp = 0))
        listen = Ui.text(a, "", 13f, topDp = 6).apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
        box.addView(listen)
        box.addView(Ui.text(a, HazardSettingsText.ALWAYS_ON, 13f, color = Ui.gray(a), topDp = 8))
        status = Ui.text(a, "", 13f, color = Ui.gray(a), topDp = 4).apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
        box.addView(status)
        renderListen()
    }

    private fun micGranted() = ContextCompat.checkSelfPermission(a, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun setSwitchSilently(v: Boolean) {
        bgSwitch.setOnCheckedChangeListener(null)
        bgSwitch.isChecked = v
        bgSwitch.setOnCheckedChangeListener { _, x -> onBackgroundToggle(x) }
    }

    private fun onBackgroundToggle(on: Boolean) {
        if (!on) { save(prefs().copy(background = false), restartService = true); return }
        setSwitchSilently(false)                          // נדלק רק אחרי אישור + הרשאה
        AlertDialog.Builder(a).setTitle(HazardSettingsText.BACKGROUND).setMessage(HazardSettingsText.BACKGROUND_CONFIRM)
            .setPositiveButton(android.R.string.ok) { _, _ -> if (micGranted()) enableBackground() else requestMic() }
            .setNegativeButton(android.R.string.cancel, null).show()
    }

    /** תוצאת בקשת הרשאת המיקרופון */
    fun onMicResult(granted: Boolean) {
        if (granted) enableBackground() else { setSwitchSilently(false); status.text = HazardSettingsText.NO_MIC }
    }

    private fun enableBackground() {
        setSwitchSilently(true)
        save(prefs().copy(background = true), restartService = true)
    }

    /** מצב אמיתי מהשירות (פעיל / ממתין / חסום / מודל חסר) - לא "פעיל" כשבפועל לא */
    fun renderListen() {
        if (!::listen.isInitialized) return
        val p = prefs()
        val engine = HazardRuntime.engineReasonIfLoaded()
        listen.text = when {
            engine != null -> "⚠ $engine"
            !p.background -> HazardRuntime.STATUS_OFF
            !micGranted() -> HazardRuntime.STATUS_NO_PERMISSION
            HazardRuntime.listenStatus == HazardRuntime.STATUS_OFF -> "⏳ מפעיל את הזיהוי ברקע..."
            else -> HazardRuntime.listenStatus
        }
    }
}
