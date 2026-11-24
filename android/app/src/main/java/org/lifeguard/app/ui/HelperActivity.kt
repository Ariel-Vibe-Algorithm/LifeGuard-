package org.lifeguard.app.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.UiEvent
import org.lifeguard.app.alert.AlarmPlayer
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.IncidentView
import org.lifeguard.app.core.Effect
import org.lifeguard.app.core.Helpers
import org.lifeguard.app.core.Tone
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * בקשה נכנסת למתנדב/ת תמיכה מרחוק / הזעקת רשויות (my_role=helper, server/helpers.py; alert_window.py _apply_helper).
 * - שיחת תמיכה: לא מוצגים שם ומיקום של הפונה - לעולם.
 * - הזעקת רשויות: שם ומיקום רק אחרי קבלה (השרת שולח אותם רק אז); מיקום ישן מסומן במפורש - לא כמיקום חי.
 * - צ'אט רק אחרי קבלה. צלצול רק להזעקת רשויות (ring=true בהתראה).
 * - כל מצב מוצג לפי תשובת השרת בלבד ("הבקשה אצלך" רק אחרי accept מוצלח).
 */
class HelperActivity : AppCompatActivity() {
    private lateinit var incidentId: String
    private var view: IncidentView? = null
    private var closed = false
    private var busy = false

    private lateinit var title: TextView
    private lateinit var sub: TextView
    private lateinit var guidance: TextView
    private lateinit var location: TextView
    private lateinit var profile: TextView
    private lateinit var status: TextView
    private lateinit var acceptBtn: Button
    private lateinit var declineBtn: Button
    private lateinit var silenceBtn: Button
    private lateinit var chatBtn: Button
    private lateinit var navBtn: Button
    private lateinit var copyBtn: Button
    private lateinit var actions: LinearLayout
    private var actionsFor: List<Pair<String, String>>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // צלצול (הזעקת רשויות) - גם על מסך נעול, כמו קריאה נכנסת
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }
        else @Suppress("DEPRECATION") window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
            WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        incidentId = intent.getStringExtra(IncomingAlertActivity.EXTRA_INCIDENT_ID) ?: run { finish(); return }
        build()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch {
                    LG.events.collect { e ->
                        when {
                            e is UiEvent.RingChanged && e.incidentId == incidentId -> renderRing()
                            e is UiEvent.IncidentClosed && e.incidentId == incidentId -> load()
                            e is UiEvent.IncidentUpdated && e.incidentId == incidentId -> load()
                        }
                    }
                }
                // גיבוי לעדכונים (מיקום מתעדכן אצל מתנדב/ת הזעקה שקיבל/ה)
                launch { while (true) { delay(15_000); if (!closed) load() } }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.getStringExtra(IncomingAlertActivity.EXTRA_INCIDENT_ID)?.let { incidentId = it; closed = false; load() }
    }

    override fun onResume() {
        super.onResume()
        load(); renderRing()
    }

    private fun build() {
        val col = Ui.page(this)
        title = Ui.text(this, "…", 22f, bold = true)
        sub = Ui.text(this, "", 13f, color = Ui.gray(this))
        silenceBtn = Ui.button(this, getString(R.string.alert_silence)) { silence() }.also { it.visibility = View.GONE }
        val gCard = Ui.card(this)
        gCard.addView(Ui.text(this, "מה עושים:", 15f, bold = true, topDp = 0))
        guidance = Ui.text(this, "", 14f)
        gCard.addView(guidance)
        location = Ui.text(this, "", 14f, topDp = 12)
        profile = Ui.text(this, "", 14f, color = Ui.gray(this))
        navBtn = Ui.button(this, "🧭 פתיחת המיקום במפה", outlined = true) { openMap() }
        copyBtn = Ui.button(this, "📋 העתקת המיקום (למסירה לרשויות)", outlined = true) { copyLocation() }
        status = Ui.text(this, "", 14f, bold = true, topDp = 12)
        val row = Ui.row(this)
        acceptBtn = Ui.button(this, "✋ אני לוקח/ת את הבקשה") { respond("accept") }
        declineBtn = Ui.button(this, "לא זמין/ה כרגע", outlined = true) { respond("decline") }
        row.addView(acceptBtn, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        row.addView(declineBtn, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            .apply { marginStart = Ui.dp(this@HelperActivity, 8) })
        actions = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        chatBtn = Ui.button(this, "💬 צ'אט") { startActivity(ChatActivity.intent(this, incidentId)) }
        for (v in listOf(title, sub, silenceBtn, gCard, location, navBtn, copyBtn, profile, status, row, actions, chatBtn)) col.addView(v)
        navBtn.visibility = View.GONE; copyBtn.visibility = View.GONE; chatBtn.visibility = View.GONE
    }

    private fun load() {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().getIncident(incidentId) } }
            r.onSuccess { v ->
                if (!v.isHelper) {
                    // לא בקשת מתנדב/ת תמיכה/הזעקה (למשל קריאה רגילה) -> מסך הקריאה הרגיל
                    startActivity(Intent(this@HelperActivity, IncomingAlertActivity::class.java).putExtras(intent))
                    finish(); return@onSuccess
                }
                view = v; render(v)
            }
            r.onFailure { e ->
                // 404: הבקשה כבר לא שלך (נלקחה ע"י מתנדב/ת אחר/ת, פג תוקפה, שוחררת, או שהאירוע הסתיים)
                if (e is ApiException && e.status == 404) showClosed("הבקשה כבר לא פעילה אצלך (נלקחה, פג תוקפה או הסתיימה)")
                else if (view == null) setStatus(Ui.errorText(this@HelperActivity, e), Tone.ERROR)
            }
        }
    }

    private fun render(v: IncidentView) {
        title.text = Helpers.title(v)
        sub.text = Helpers.subtitle(v, SimpleDateFormat("HH:mm", Locale.US).format(Date((v.createdAt * 1000).toLong())))
        guidance.text = v.guidance.joinToString("\n") { "• $it" }
        val (locText, locTone) = Helpers.locationLine(v)
        location.text = locText
        location.setTextColor(color(locTone))
        val shareable = Helpers.shareableLocation(v)
        navBtn.visibility = if (shareable != null && v.isActive) View.VISIBLE else View.GONE
        copyBtn.visibility = navBtn.visibility
        navBtn.text = if (v.locationStale) "🧭 פתיחת המיקום האחרון הידוע (לא חי) במפה" else "🧭 פתיחת המיקום במפה"
        profile.text = Helpers.profileLine(v) + (v.lastActionLabel?.let { "\nדיווחת לאחרונה: $it" } ?: "")
        profile.setTextColor(if (Helpers.isLiaison(v) && Helpers.isAccepted(v)) Ui.label(this) else Ui.gray(this))
        val accepted = Helpers.isAccepted(v)
        if (!v.isActive) { showClosed("✅ הבקשה הסתיימה"); return }
        acceptBtn.isEnabled = !accepted && !busy
        acceptBtn.text = if (accepted) "✅ הבקשה אצלך" else "✋ אני לוקח/ת את הבקשה"
        declineBtn.isEnabled = !busy
        declineBtn.visibility = if (accepted) View.GONE else View.VISIBLE
        val btns = Helpers.actionButtons(v)
        if (btns != actionsFor) {
            actionsFor = btns
            actions.removeAllViews()
            for ((code, label) in btns) {
                actions.addView(Ui.button(this, label, outlined = !code.startsWith("called")) { respond(code) })
            }
        }
        for (i in 0 until actions.childCount) actions.getChildAt(i).isEnabled = !busy
        chatBtn.visibility = if (Helpers.chatAllowed(v)) View.VISIBLE else View.GONE
        if (accepted && LG.ring.isRinging(incidentId)) stopRingLocally("accept")
    }

    private fun showClosed(text: String) {
        closed = true
        title.text = "✅ הבקשה הסתיימה"
        setStatus(text, Tone.MUTED)
        acceptBtn.isEnabled = false; declineBtn.isEnabled = false
        actions.removeAllViews(); actionsFor = null
        chatBtn.visibility = View.GONE; navBtn.visibility = View.GONE; copyBtn.visibility = View.GONE
        if (LG.ring.isRinging(incidentId)) stopRingLocally("decline")
    }

    private fun setStatus(text: String, tone: Tone) {
        status.text = text
        status.setTextColor(color(tone))
    }

    private fun color(t: Tone) = when (t) {
        Tone.OK -> Ui.green(this)
        Tone.ERROR -> Ui.red(this)
        Tone.WARN -> Ui.amber(this)
        Tone.MUTED -> Ui.gray(this)
    }

    private fun respond(action: String) {
        if (busy || closed) return
        if (action == "accept" || action == "decline") stopRingLocally(action)   // הצלצול נעצר מיד, גם ברשת איטית
        busy = true
        view?.let { render(it) }
        setStatus("⏳ שולח לשרת...", Tone.MUTED)
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().helperRespond(incidentId, action) } }
            busy = false
            r.onSuccess { v ->
                // מאושר רק אחרי תשובת השרת
                setStatus("✅ " + (Helpers.ACTION_FEEDBACK[action] ?: "עודכן"), Tone.OK)
                if (action == "decline" || action == "done") {
                    closed = true
                    Toast.makeText(this@HelperActivity, Helpers.ACTION_FEEDBACK[action], Toast.LENGTH_LONG).show()
                    finish()
                } else { view = v; render(v) }
            }
            r.onFailure { e ->
                setStatus("❌ " + Ui.errorText(this@HelperActivity, e), Tone.ERROR)
                // 409 taken: מישהו/י אחר/ת כבר לקח/ה; 404: הבקשה כבר לא פעילה
                if (e is ApiException && (e.status == 409 || e.status == 404)) {
                    closed = true
                    acceptBtn.isEnabled = false; declineBtn.isEnabled = false
                    actions.removeAllViews(); actionsFor = null; chatBtn.visibility = View.GONE
                } else view?.let { render(it) }
            }
        }
    }

    private fun renderRing() {
        silenceBtn.visibility = if (LG.ring.isRinging(incidentId)) View.VISIBLE else View.GONE
        if (LG.ring.isRinging(incidentId)) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    private fun stopRingLocally(action: String) {
        val effects = LG.ring.onLocalResponse(incidentId, action)
        if (effects.any { it is Effect.StopRing }) afterRingStopped()
    }

    private fun silence() {
        LG.ring.silence(incidentId)
        afterRingStopped()
    }

    private fun afterRingStopped() {
        if (LG.ring.ringingIncidents.isEmpty() && LG.alarm.mode == AlarmPlayer.Mode.RING) LG.alarm.stop()
        Notifier.cancel(this, Notifier.incidentNotifId(incidentId))
        LG.emit(UiEvent.RingChanged(incidentId, false))
        renderRing()
    }

    private fun openMap() {
        val v = view ?: return
        val loc = Helpers.shareableLocation(v) ?: return
        val geo = Uri.parse("geo:${loc.lat},${loc.lon}?q=${loc.lat},${loc.lon}(${Uri.encode(v.ownerDisplay)})")
        try {
            startActivity(Intent(Intent.ACTION_VIEW, geo))
        } catch (e: Exception) {
            runCatching {
                startActivity(Intent(Intent.ACTION_VIEW,
                    Uri.parse("https://www.openstreetmap.org/?mlat=${loc.lat}&mlon=${loc.lon}#map=17/${loc.lat}/${loc.lon}")))
            }
        }
    }

    private fun copyLocation() {
        val v = view ?: return
        val loc = Helpers.shareableLocation(v) ?: return
        val text = "%.5f, %.5f".format(Locale.US, loc.lat, loc.lon) + (if (v.locationStale) " (מיקום אחרון ידוע - לא עדכני)" else "")
        getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("location", text))
        Toast.makeText(this, "הועתק: $text", Toast.LENGTH_SHORT).show()
    }

    companion object {
        fun intent(ctx: Context, incidentId: String, ringing: Boolean = false): Intent =
            Intent(ctx, HelperActivity::class.java).putExtra(IncomingAlertActivity.EXTRA_INCIDENT_ID, incidentId)
                .putExtra(IncomingAlertActivity.EXTRA_RINGING, ringing)
    }
}
