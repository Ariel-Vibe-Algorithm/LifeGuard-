package org.lifeguard.app.ui

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.Dispatchers
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
import org.lifeguard.app.core.Geo
import org.lifeguard.app.databinding.ActivityAlertBinding
import java.io.IOException
import java.util.Locale

/**
 * קריאה נכנסת (מסך מלא, גם על מסך נעול): מי, מה, מרחק; קבלה / סירוב / ניווט / צ'אט.
 * הצלצול נעצר בתגובה, ב-ring_stopped מהשרת, או בכפתור "השתק" (האירוע עדיין פתוח לתגובה).
 */
class IncomingAlertActivity : AppCompatActivity() {
    private lateinit var b: ActivityAlertBinding
    private lateinit var incidentId: String
    private var view: IncidentView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }
        else @Suppress("DEPRECATION") window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
            WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        b = ActivityAlertBinding.inflate(layoutInflater)
        setContentView(b.root)
        if (!readIntent(intent)) { finish(); return }

        b.acceptBtn.setOnClickListener { respond("accept") }
        b.declineBtn.setOnClickListener { respond("decline") }
        b.silenceBtn.setOnClickListener { silence() }
        b.navigateBtn.setOnClickListener { navigate() }
        b.chatBtn.setOnClickListener { startActivity(ChatActivity.intent(this, incidentId)) }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                LG.events.collect { e ->
                    when {
                        e is UiEvent.RingChanged && e.incidentId == incidentId -> renderRing()
                        e is UiEvent.IncidentClosed && e.incidentId == incidentId -> {
                            b.ringState.text = getString(R.string.alert_closed); setButtons(false)
                        }
                        e is UiEvent.IncidentUpdated && e.incidentId == incidentId -> load()
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (readIntent(intent)) load()
    }

    override fun onResume() {
        super.onResume()
        load(); renderRing()
    }

    private fun readIntent(i: Intent): Boolean {
        incidentId = i.getStringExtra(EXTRA_INCIDENT_ID) ?: return false
        b.title.text = getString(R.string.alert_title, "…")
        return true
    }

    private fun load() {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().getIncident(incidentId) } }
            r.onSuccess {
                if (it.isHelper) {
                    // בקשת מתנדב/ת תמיכה/הזעקה - מסך נפרד (בלי שם ומיקום לפני קבלה, פעולות הזעקה)
                    startActivity(Intent(this@IncomingAlertActivity, HelperActivity::class.java).putExtras(intent))
                    finish(); return@onSuccess
                }
                view = it; render(it)
            }
            r.onFailure { e ->
                if (e is ApiException && e.status == 404) { b.ringState.text = getString(R.string.alert_closed); setButtons(false) }
            }
        }
    }

    private fun render(v: IncidentView) {
        b.title.text = getString(R.string.alert_title, v.ownerDisplay)
        b.type.text = Geo.emergencyTypeHe(v.emergencyType) + (v.riskBandLabel?.let { " · $it" } ?: "")
        val d = MainActivity.distanceKm(v)
        b.distance.text = when {
            d != null -> getString(R.string.alert_distance, Geo.formatDistanceHe(d)) + (v.etaMinutes?.let { " · ~$it דק'" } ?: "")
            v.locationWithheld -> "המיקום לא משותף איתך (בחירת בעל/ת האירוע) - קיבלת התראה בלבד"
            v.location == null -> "מיקום לא ידוע"
            else -> ""
        }
        val notes = ArrayList<String>()
        if (v.isSilent) notes += getString(R.string.alert_silent_mode)
        if (v.location != null && v.locationStale) notes += "המיקום ישן (לא התעדכן 5 דקות ומעלה) - לא מיקום חי"
        if (v.activation == "unconfirmed") notes += "זיהוי אוטומטי שעוד לא אושר ע\"י ${v.ownerDisplay} - כדאי לבדוק מה שלומו/ה"
        v.signalLostSince?.let {
            notes += "המיקום לא מתעדכן מאז " + java.text.SimpleDateFormat("HH:mm", Locale.US).format(java.util.Date((it * 1000).toLong())) +
                " - ייתכן שאין קליטה"
        }
        val handling = v.responders.filter { it.state == "accepted" }.map { it.displayName }
        if (handling.isNotEmpty()) notes += "מטפלים: " + handling.joinToString(", ")
        b.note.text = notes.joinToString("\n")
        val accepted = v.myState == "accepted"
        b.acceptBtn.visibility = if (accepted) View.GONE else View.VISIBLE
        b.declineBtn.visibility = if (accepted) View.GONE else View.VISIBLE
        b.chatBtn.isEnabled = v.fullAccess
        b.navigateBtn.isEnabled = v.location != null
        if (!v.isActive) { b.ringState.text = getString(R.string.alert_closed); setButtons(false) }
    }

    private fun renderRing() {
        val ringing = LG.ring.isRinging(incidentId)
        b.silenceBtn.visibility = if (ringing) View.VISIBLE else View.GONE
        if (!ringing && intent.getBooleanExtra(EXTRA_RINGING, false) && view?.isActive != false) {
            b.ringState.text = getString(R.string.alert_ring_stopped)
        }
    }

    private fun setButtons(enabled: Boolean) {
        b.acceptBtn.isEnabled = enabled; b.declineBtn.isEnabled = enabled
    }

    private fun stopRingLocally(action: String) {
        val effects = LG.ring.onLocalResponse(incidentId, action)
        if (effects.any { it is Effect.StopRing }) {
            if (LG.ring.ringingIncidents.isEmpty() && LG.alarm.mode == AlarmPlayer.Mode.RING) LG.alarm.stop()
            Notifier.cancel(this, Notifier.incidentNotifId(incidentId))
            LG.emit(UiEvent.RingChanged(incidentId, false))
        }
    }

    private fun silence() {
        LG.ring.silence(incidentId)
        if (LG.ring.ringingIncidents.isEmpty() && LG.alarm.mode == AlarmPlayer.Mode.RING) LG.alarm.stop()
        Notifier.cancel(this, Notifier.incidentNotifId(incidentId))
        LG.emit(UiEvent.RingChanged(incidentId, false))
        b.ringState.text = getString(R.string.alert_ring_stopped)
    }

    private fun respond(action: String) {
        stopRingLocally(action)          // הצפצוף נעצר מיד, גם אם הרשת איטית
        setButtons(false)
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().respond(incidentId, action) } }
            r.onSuccess {
                if (action == "decline") finish()
                else startActivity(ChatActivity.intent(this@IncomingAlertActivity, incidentId)).also { finish() }
            }
            r.onFailure { e ->
                setButtons(true)
                Toast.makeText(this@IncomingAlertActivity,
                    if (e is IOException) getString(R.string.network_error) else e.message, Toast.LENGTH_LONG).show()
                if (e is ApiException && (e.status == 409 || e.status == 404)) { setButtons(false); b.ringState.text = getString(R.string.alert_closed) }
            }
        }
    }

    private fun navigate() {
        val loc = view?.location ?: return
        val label = Uri.encode(view?.ownerDisplay ?: "SOS")
        val geo = Uri.parse("geo:${loc.lat},${loc.lon}?q=${loc.lat},${loc.lon}($label)")
        try {
            startActivity(Intent(Intent.ACTION_VIEW, geo))
        } catch (e: Exception) {
            startActivity(Intent(Intent.ACTION_VIEW,
                Uri.parse("https://www.openstreetmap.org/?mlat=${loc.lat}&mlon=${loc.lon}#map=17/${loc.lat}/${loc.lon}")))
        }
    }

    companion object {
        const val EXTRA_INCIDENT_ID = "incident_id"
        const val EXTRA_RINGING = "ringing"
    }
}
