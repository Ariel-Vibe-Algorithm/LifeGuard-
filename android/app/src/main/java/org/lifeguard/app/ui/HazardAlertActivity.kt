package org.lifeguard.app.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.core.SafePlaces
import org.lifeguard.app.hazard.AudioIntel
import org.lifeguard.app.hazard.HazardActions
import org.lifeguard.app.hazard.HazardAlert
import org.lifeguard.app.hazard.HazardNotConnected
import org.lifeguard.app.hazard.HazardPrefs
import org.lifeguard.app.hazard.HazardRuntime
import org.lifeguard.app.hazard.HazardText
import org.lifeguard.app.service.LocationTracker
import org.lifeguard.app.service.SafePlaceFinder
import java.util.Locale

/**
 * "LifeGuard זיהה סכנה אפשרית" (hazard_alert_ui.py ב-Windows): כחול-כהה / לבן / כחול של האפליקציה.
 * סוג, חומרה, ביטחון בזיהוי, הסבר קצר, מיקום אחרון ידוע, "זיהוי של צליל בלבד - לא אישור לסכנה".
 * שש פעולות: SOS (אחרי אישור), מקום בטוח, התראה לאנשי הקשר (אחרי אישור; תשובת שרת אמיתית), חיוג לחירום (אחרי
 * אישור; ACTION_DIAL), התראת שווא (סיבה רשות), סגירה. עברית RTL / אנגלית LTR לפי ההעדפה.
 */
class HazardAlertActivity : AppCompatActivity() {
    private lateinit var alertId: String
    private var alert: HazardAlert? = null
    private lateinit var prefs: HazardPrefs
    private lateinit var s: HazardText
    private var closed = false
    private var safe: SafePlaceFinder.Result? = null

    private lateinit var sevBar: View
    private lateinit var kind: TextView
    private lateinit var updated: TextView
    private lateinit var sevChip: TextView
    private lateinit var confChip: TextView
    private lateinit var explain: TextView
    private lateinit var loc: TextView
    private lateinit var status: TextView
    private lateinit var reasons: LinearLayout
    private lateinit var safeBox: LinearLayout
    private lateinit var safeText: TextView
    private lateinit var safeNote: TextView
    private lateinit var safeNav: Button
    private val buttons = LinkedHashMap<String, Button>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }
        else @Suppress("DEPRECATION") window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
            WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        prefs = HazardRuntime.prefs()
        s = HazardText.of(prefs.language)
        alertId = intent.getStringExtra(EXTRA_ALERT_ID) ?: ""
        val a = HazardRuntime.find(alertId)
        if (a == null) { showNotActive(); return }
        alert = a
        build()
        refresh(a)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = dismiss()
        })
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { HazardRuntime.changes.collect { id -> if (id == alertId && !closed) alert?.let { refresh(it) } } }
                launch { while (true) { delay(30_000); if (!closed) loc.text = locationText() } }
            }
        }
    }

    /** התראה אחרת (משפחה אחרת) כשהמסך כבר פתוח - מציגים אותה. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.getStringExtra(EXTRA_ALERT_ID) != alertId) { setIntent(intent); recreate() }
    }

    // ------------------------------------------------------------------ בנייה
    private fun dp(v: Int) = Ui.dp(this, v)
    private fun col(res: Int) = Ui.color(this, res)

    private fun tv(text: CharSequence, sp: Float, color: Int, bold: Boolean = false, top: Int = 0) = TextView(this).apply {
        this.text = text
        textSize = sp
        setTextColor(color)
        if (bold) setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL), Typeface.NORMAL)
        textAlignment = View.TEXT_ALIGNMENT_VIEW_START
        setLineSpacing(0f, 1.1f)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(top) }
    }

    private fun chip(bg: Int, fg: Int) = TextView(this).apply {
        textSize = 14f
        setTextColor(fg)
        setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL), Typeface.NORMAL)
        setPadding(dp(12), dp(6), dp(12), dp(6))
        background = GradientDrawable().apply { cornerRadius = dp(999).toFloat(); setColor(bg) }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { marginEnd = dp(8) }
    }

    private fun action(key: String, text: String, bg: Int, onClick: () -> Unit): Button =
        Ui.button(this, text) { onClick() }.apply {
            backgroundTintList = ColorStateList.valueOf(col(bg))
            setTextColor(col(R.color.lg_on_fill))
            minHeight = dp(64)
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = dp(4); marginEnd = dp(4) }
            buttons[key] = this
        }

    private fun row(vararg views: View) = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(4) }
        views.forEach { addView(it) }
    }

    private fun build() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(col(R.color.lg_bg))
            layoutDirection = if (s.rtl) View.LAYOUT_DIRECTION_RTL else View.LAYOUT_DIRECTION_LTR
            textDirection = if (s.rtl) View.TEXT_DIRECTION_RTL else View.TEXT_DIRECTION_LTR
        }
        // ---- כותרת נייבי עם פס חומרה ----
        val head = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(col(R.color.lg_fill_navy))
        }
        sevBar = View(this).apply { layoutParams = LinearLayout.LayoutParams(dp(8), ViewGroup.LayoutParams.MATCH_PARENT) }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val onNavy = col(R.color.lg_on_fill)
        box.addView(tv("⚠️ " + s.title, 15f, onNavy, bold = true).also { ViewCompat.setAccessibilityHeading(it, true) })
        kind = tv("", 24f, onNavy, bold = true, top = 4)
        box.addView(kind)
        updated = tv("", 13f, col(R.color.lg_on_fill_translucent))
        box.addView(updated)
        head.addView(sevBar); head.addView(box)
        root.addView(head)

        // ---- גוף ----
        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(24))
        }
        sevChip = chip(col(R.color.lg_fill_sos), onNavy)
        confChip = chip(col(R.color.lg_surface_2), col(R.color.lg_label))
        body.addView(row(sevChip, confChip))
        explain = tv("", 16f, col(R.color.lg_label), top = 12)
        body.addView(explain)
        body.addView(tv(s.notVerified, 13f, col(R.color.lg_label_secondary), top = 4))
        loc = tv("", 13f, col(R.color.lg_label_secondary), top = 4)
        body.addView(loc)
        body.addView(tv(s.question, 17f, col(R.color.lg_label), bold = true, top = 16))

        val connected = connected()
        body.addView(row(action("sos", s.sos, R.color.lg_fill_sos) { activateSos() },
            action("safe_place", s.safePlace, R.color.lg_fill_accent) { safePlace() }))
        body.addView(row(action("contacts", s.contacts + if (connected) "" else "\n(${s.notConnected})", R.color.lg_fill_accent) {
            alertContacts()
        }, action("call", s.call, R.color.lg_fill_navy) { callEmergency() }))
        if (!connected) buttons.getValue("contacts").apply {
            isEnabled = false
            backgroundTintList = ColorStateList.valueOf(col(R.color.lg_fill))
            setTextColor(col(R.color.lg_label_tertiary))
        }
        val fa = Ui.tonalButton(this, s.falseAlarm) { showReasons() }.apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(4); marginEnd = dp(4) }
        }
        val dm = Ui.button(this, s.dismiss, outlined = true) { dismiss() }.apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(4); marginEnd = dp(4) }
        }
        buttons["false_alarm"] = fa
        buttons["dismiss"] = dm
        body.addView(row(fa, dm).apply { (layoutParams as LinearLayout.LayoutParams).topMargin = dp(10) })
        status = tv("", 14f, col(R.color.lg_label_secondary), top = 10).apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
        body.addView(status)

        reasons = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; visibility = View.GONE }
        body.addView(reasons)
        safeBox = Ui.card(this).apply { visibility = View.GONE }
        safeText = tv("", 15f, col(R.color.lg_label))
        safeNote = tv("", 13f, col(R.color.lg_label_secondary), top = 4)
        safeNav = Ui.button(this, s.navigate) { navigateToSafePlace() }.apply { visibility = View.GONE }
        safeBox.addView(tv(s.safePlace, 15f, col(R.color.lg_label), bold = true))
        safeBox.addView(safeText); safeBox.addView(safeNote); safeBox.addView(safeNav)
        body.addView(safeBox)

        root.addView(ScrollView(this).apply {
            isFillViewport = true
            addView(body)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f)
        })
        setContentView(root)
        window.statusBarColor = col(R.color.lg_fill_navy)
    }

    private fun showNotActive() {
        val col = Ui.page(this)
        col.addView(Ui.text(this, s.notActive, 16f))
        col.addView(Ui.button(this, s.dismiss) { finish() })
    }

    private fun sevColor(sev: String) = col(when (sev) {
        "high" -> R.color.lg_fill_sos; "medium" -> R.color.lg_fill_warning; "low" -> R.color.lg_fill_accent
        else -> R.color.lg_fill_neutral
    })

    private fun refresh(a: HazardAlert) {
        val c = sevColor(a.severity)
        sevBar.setBackgroundColor(c)
        (sevChip.background as GradientDrawable).setColor(c)
        sevChip.text = "${s.severity}: ${s.sevLabel(a.severity)}"
        confChip.text = "${s.confidence}: ${AudioIntel.fmt(a.confidence * 100, 0)}%"
        kind.text = s.label(a)
        explain.text = s.explanation(a)
        updated.text = if (a.count > 1) "${s.updated} (×${a.count})"
        else java.text.SimpleDateFormat("HH:mm:ss", Locale.US).format(java.util.Date((a.firstTs * 1000).toLong()))
        loc.text = locationText()
    }

    private fun locationText(): String {
        val f = LG.lastFix ?: return "📍 ${s.noLocation}"
        val age = LG.nowSec() - (f.capturedAt ?: LG.nowSec())
        val approx = if (f.isPrecise) "" else " (${s.approx})"
        val whenTxt = if (age > 120) " · " + s.minAgo.replace("{m}", (age / 60).toInt().toString()) else ""
        return "📍 ${s.location}: ${"%.4f".format(Locale.US, f.lat)}, ${"%.4f".format(Locale.US, f.lon)}$approx$whenTxt"
    }

    private fun connected() = LG.store.isLoggedIn && LG.isOnline() && LG.networkAvailable(this)

    private fun confirm(title: String, text: String, onYes: () -> Unit) {
        AlertDialog.Builder(this).setTitle(title).setMessage(text)
            .setPositiveButton(s.yes) { _, _ -> onYes() }
            .setNegativeButton(s.no, null).show()
    }

    private fun setStatus(text: String, colorRes: Int = R.color.lg_label_secondary) {
        status.text = text
        status.setTextColor(col(colorRes))
    }

    // ------------------------------------------------------------------ פעולות
    private fun close() {
        closed = true
        alert?.let { Notifier.cancelHazard(this, it.family) }
        HazardRuntime.changed(alertId)
        finish()
    }

    private fun activateSos() {
        val a = alert ?: return
        confirm("SOS", s.confirmSos) {
            if (HazardRuntime.actions.activateSos(a, confirmed = true)) {
                startActivity(Intent(this, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
                close()
            }
        }
    }

    /** מקום בטוח: אותו מסלול כמו במסך ה-SOS (SafePlaceFinder: רשימה מובנית אופליין מיד, OSRM משפר כשיש רשת). */
    private fun safePlace() {
        val a = alert ?: return
        if (a.response == null) HazardRuntime.actions.respond(a, "safe_place")
        alert?.let { Notifier.cancelHazard(this, it.family) }
        reasons.visibility = View.GONE
        safeBox.visibility = View.VISIBLE
        safeText.text = s.searchingSafe
        val fix = LG.lastFix
        if (fix != null) findSafe(fix) else LocationTracker.currentOnce(this) { f ->
            runOnUiThread { if (f != null) findSafe(f) else { safeText.text = SafePlaces.NO_LOCATION_HE } }
        }
    }

    private fun findSafe(fix: org.lifeguard.app.api.GeoPoint) {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.Default) { SafePlaceFinder.offline(this@HazardAlertActivity, fix, LG.store.worshipFallback) }
            safe = r
            renderSafe()
            if (r.choice != null && LG.networkAvailable(this@HazardAlertActivity)) {
                val better = withContext(Dispatchers.IO) { runCatching { SafePlaceFinder.refine(r) }.getOrNull() }
                if (better != null && safe === r) { safe = better; renderSafe() }
            }
        }
    }

    private fun renderSafe() {
        val r = safe ?: return
        val c = r.choice
        safeText.text = if (c == null) SafePlaces.NONE_FOUND_HE else SafePlaces.summaryHe(c, r.routeM, r.routeS)
        val note = SafePlaces.originNoteHe(r.origin.capturedAt, r.origin.accuracyM, LG.nowSec())
        safeNote.visibility = if (note == null) View.GONE else View.VISIBLE
        safeNote.text = note ?: ""
        safeNav.visibility = if (c != null) View.VISIBLE else View.GONE
    }

    private fun navigateToSafePlace() {
        val r = safe ?: return
        val c = r.choice ?: return
        for (uri in SafePlaces.navigationUris(r.origin.lat, r.origin.lon, c.place)) {
            try { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(uri))); return } catch (_: ActivityNotFoundException) { }
        }
        setStatus("לא נמצאה אפליקציית מפות או דפדפן במכשיר", R.color.lg_sos)
    }

    private fun alertContacts() {
        val a = alert ?: return
        confirm(s.contacts, s.confirmContacts) {
            val btn = buttons.getValue("contacts")
            btn.isEnabled = false
            setStatus(s.sending)
            lifecycleScope.launch {
                val r = withContext(Dispatchers.IO) { runCatching { HazardRuntime.actions.notifyContacts(a, prefs.language, confirmed = true) } }
                r.onSuccess { res ->
                    Notifier.cancelHazard(this@HazardAlertActivity, a.family)
                    if (res.sentTo.isEmpty()) { setStatus(s.noContacts, R.color.lg_sos); return@onSuccess }
                    setStatus(s.sent.replace("{n}", res.sentTo.size.toString()), R.color.lg_label)
                    delay(6000)
                    val st = withContext(Dispatchers.IO) { runCatching { HazardRuntime.actions.deliveryStatus(res.notificationIds) }.getOrDefault(emptyMap()) }
                    val ok = st.values.count { it }
                    setStatus(s.delivered.replace("{ok}", ok.toString()).replace("{n}", res.sentTo.size.toString()),
                        if (ok > 0) R.color.lg_success else R.color.lg_warning)
                }
                r.onFailure { e ->
                    btn.isEnabled = true
                    val msg = if (e is HazardNotConnected) e.message ?: "" else Ui.errorText(this@HazardAlertActivity, e)
                    setStatus(s.sendFailed.replace("{e}", msg), R.color.lg_sos)
                }
            }
        }
    }

    private fun callEmergency() {
        val a = alert ?: return
        val number = HazardActions.emergencyNumber()
        confirm(s.call, s.confirmCall.replace("{n}", number)) {
            val opened = HazardRuntime.actions.callEmergency(a, confirmed = true, number = number) { digits ->
                try { startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$digits"))); true }
                catch (_: ActivityNotFoundException) { false }
            }
            Notifier.cancelHazard(this, a.family)
            if (opened == true) setStatus(s.dialOpened.replace("{n}", number), R.color.lg_label)
            else setStatus(s.noDialer.replace("{n}", number), R.color.lg_sos)
        }
    }

    private fun showReasons() {
        reasons.removeAllViews()
        reasons.addView(tv(s.why, 14f, col(R.color.lg_label), top = 12))
        val group = RadioGroup(this).apply { orientation = RadioGroup.VERTICAL }
        val keys = ArrayList<String>()
        for ((key, label) in s.reasons()) {
            keys += key
            group.addView(RadioButton(this).apply {
                id = View.generateViewId(); tag = key; text = label; textSize = 15f
                minHeight = dp(48); gravity = Gravity.CENTER_VERTICAL or Gravity.START
            })
        }
        reasons.addView(group)
        reasons.addView(Ui.button(this, s.confirmFalse) {
            val chosen = group.findViewById<RadioButton>(group.checkedRadioButtonId)?.tag as? String
            falseAlarm(chosen)
        })
        reasons.visibility = View.VISIBLE
    }

    private fun falseAlarm(reason: String?) {
        val a = alert ?: return
        HazardRuntime.actions.respond(a, "false_alarm", reason)
        close()
    }

    private fun dismiss() {
        if (closed) return
        val a = alert
        if (a != null && a.response == null) HazardRuntime.actions.respond(a, "dismiss")
        close()
    }

    companion object {
        const val EXTRA_ALERT_ID = "hazard_alert_id"

        fun intent(ctx: Context, alertId: String): Intent = Intent(ctx, HazardAlertActivity::class.java)
            .putExtra(EXTRA_ALERT_ID, alertId)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
    }
}
