package org.lifeguard.app.ui

import android.content.Context
import android.content.Intent
import android.location.Geocoder
import android.os.Bundle
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.widget.doAfterTextChanged
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
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.RideRequest
import org.lifeguard.app.api.Vehicle
import org.lifeguard.app.core.GeoInput
import org.lifeguard.app.core.RideText
import org.lifeguard.app.core.RideTypes
import org.lifeguard.app.databinding.ActivityRideBinding
import org.lifeguard.app.service.EscortManager
import org.lifeguard.app.service.LifeGuardService
import java.io.IOException
import java.util.Locale

/**
 * נסיעה בטוחה: סוג נסיעה, יעד (Geocoder אם קיים; אחרת קישור מפה / lat,lon), זמן משוער, פרטי רכב, מצב שקט.
 * בזמן נסיעה: ספירה לאחור, מרחק/זמן הגעה מהשרת, עוקבים, באנר חריגה ("אני בסדר" / "צריך/ה עזרה"),
 * SOS דיסקרטי בהחזקה, "הגעתי בשלום", הארכה.
 */
class RideActivity : AppCompatActivity() {
    private lateinit var b: ActivityRideBinding
    private var destPoint: GeoPoint? = null
    private val codes = RideTypes.ALL.keys.toList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityRideBinding.inflate(layoutInflater)
        setContentView(b.root)
        title = getString(R.string.ride_title)

        b.rideType.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, RideTypes.ALL.values.toList())
        // Geocoder: לא בכל מכשיר (בלי שירותי Google אין ספק) - אומרים את זה בפירוש
        if (!Geocoder.isPresent()) {
            b.geocodeBtn.isEnabled = false
            b.destStatus.text = getString(R.string.ride_geocoder_unavailable)
        }
        b.geocodeBtn.setOnClickListener { geocode() }
        b.destPoint.doAfterTextChanged { t ->
            val p = GeoInput.parse(t?.toString())
            if (p != null) { destPoint = p; showDest(p, getString(R.string.ride_point_from_link)) }
            else if (!t.isNullOrBlank()) { destPoint = null; b.destStatus.text = getString(R.string.ride_point_invalid) }
        }
        b.startBtn.setOnClickListener { start() }

        b.anomalyOk.setOnClickListener { checkin("ok") }
        b.anomalyHelp.setOnClickListener { checkin("help") }
        b.arrivedBtn.setOnClickListener { finishRide("arrived") }
        b.cancelBtn.setOnClickListener {
            AlertDialog.Builder(this).setMessage(R.string.ride_cancel_confirm)
                .setPositiveButton(R.string.ride_cancel) { _, _ -> finishRide("cancelled") }
                .setNegativeButton(android.R.string.cancel, null).show()
        }
        b.extendBtn.setOnClickListener { extend() }
        b.discreetSos.fillColor = ContextCompat.getColor(this, R.color.neutral)
        b.discreetSos.showHalo = false
        b.discreetSos.label = getString(R.string.ride_discreet_sos)
        b.discreetSos.textSizePx = 11f * resources.displayMetrics.scaledDensity
        b.discreetSos.onTriggered = { sos() }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { LG.events.collect { if (it is UiEvent.StateChanged) render() } }
                launch { while (true) { render(); delay(1000) } }
                launch {
                    while (true) {
                        if (EscortManager.activeId != null) withContext(Dispatchers.IO) { EscortManager.refresh() }
                        render(); delay(15_000)
                    }
                }
            }
        }
    }

    private fun showDest(p: GeoPoint, how: String) {
        b.destStatus.text = "$how: %.5f, %.5f".format(Locale.US, p.lat, p.lon)
    }

    private fun geocode() {
        val q = b.destLabel.text.toString().trim()
        if (q.isEmpty()) { toast(getString(R.string.ride_enter_address)); return }
        b.geocodeBtn.isEnabled = false
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) {
                runCatching {
                    @Suppress("DEPRECATION")
                    Geocoder(this@RideActivity, Locale("he", "IL")).getFromLocationName(q, 1)?.firstOrNull()
                }
            }
            b.geocodeBtn.isEnabled = true
            val a = r.getOrNull()
            when {
                a != null -> {
                    destPoint = GeoPoint(a.latitude, a.longitude, isPrecise = true, source = "geocoder")
                    showDest(destPoint!!, getString(R.string.ride_point_found))
                }
                r.exceptionOrNull() is IOException -> b.destStatus.text = getString(R.string.ride_geocoder_failed)
                else -> b.destStatus.text = getString(R.string.ride_geocoder_not_found)
            }
        }
    }

    private fun start() {
        val minutes = b.minutes.text.toString().toIntOrNull()?.coerceIn(1, 1440) ?: 30
        val label = b.destLabel.text.toString().trim().take(80)
        val type = codes.getOrElse(b.rideType.selectedItemPosition) { "taxi" }
        val vehicle = Vehicle(b.plate.text.toString(), b.vehicleDesc.text.toString(), b.driver.text.toString())
        val mode = if (b.silentSwitch.isChecked) "silent" else "loud"
        b.startBtn.isEnabled = false
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) {
                runCatching { EscortManager.start(minutes, label, mode, RideRequest(type, destPoint, vehicle)) }
            }
            b.startBtn.isEnabled = true
            r.onFailure { e -> toast(if (e is ApiException) e.message ?: "" else getString(R.string.escort_needs_network)) }
            if (r.isSuccess && destPoint == null) toast(getString(R.string.ride_no_point_note))
            LifeGuardService.start(this@RideActivity)
            render()
        }
    }

    private fun render() {
        val id = EscortManager.activeId
        val isRide = EscortManager.isRide
        b.form.visibility = if (id == null) View.VISIBLE else View.GONE
        b.active.visibility = if (id != null) View.VISIBLE else View.GONE
        if (id == null) return
        val v = EscortManager.lastView
        val silent = EscortManager.isSilent
        b.rideTitle.text = if (!isRide) getString(R.string.escort_title)
        else (v?.rideLabel ?: RideTypes.label(v?.rideType)).let { lbl ->
            getString(R.string.ride_active_title, lbl) + (v?.destination?.takeIf { it.isNotBlank() }?.let { " · $it" } ?: "")
        }
        val nowS = runCatching { LG.api().serverNow() }.getOrDefault(LG.nowSec())
        b.countdown.text = RideText.countdown(LG.store.escortDeadline - nowS)
        b.rideStatus.text = v?.let { RideText.status(it, nowS).drop(1).joinToString("\n") } ?: getString(R.string.ride_loading)
        val q = EscortManager.queuedCount
        b.queueStatus.text = if (q > 0) getString(R.string.ride_queued, q) else ""

        val a = v?.anomaly
        b.anomalyCard.visibility = if (a != null) View.VISIBLE else View.GONE
        if (a != null) {
            // נסיעה שקטה: הבאנר ניטרלי (השרת שולח טקסט ניטרלי לבעלים)
            Ui.setCardTone(b.anomalyCard, alert = !silent)
            val left = a.respondBy?.let { (it - nowS).toLong().coerceAtLeast(0) }
            b.anomalyText.text = a.text.ifBlank { RideText.anomalyKind(a.kind) } + (left?.let { " (${it} שנ')" } ?: "")
            b.anomalyOk.text = getString(if (silent) R.string.ride_check_ok_neutral else R.string.wellness_ok)
            b.anomalyHelp.text = getString(if (silent) R.string.ride_check_help_neutral else R.string.ride_need_help)
        }
    }

    private fun checkin(status: String) {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { EscortManager.checkin(this@RideActivity, status) } }
            when (r.getOrNull()) {
                "queued" -> toast(getString(R.string.ride_checkin_queued))
                "sos_fallback" -> toast(getString(R.string.ride_sos_fallback))
                "sent" -> if (status == "help") toast(getString(R.string.ride_help_sent))
                null -> toast(r.exceptionOrNull()?.message ?: getString(R.string.network_error))
            }
            LifeGuardService.start(this@RideActivity, kick = true)
            render()
        }
    }

    private fun sos() {
        val mode = if (EscortManager.isSilent) "silent" else "loud"
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { EscortManager.sos(this@RideActivity, mode) } }
            if (r.getOrNull() == "sos_fallback") toast(getString(R.string.ride_sos_fallback))
            LifeGuardService.start(this@RideActivity, kick = true)
            startActivity(Intent(this@RideActivity, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
            finish()
        }
    }

    private fun extend() {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { EscortManager.extend(10) } }
            r.onFailure { e -> toast(if (e is IOException) getString(R.string.network_error) else e.message ?: "") }
            render()
        }
    }

    private fun finishRide(action: String) {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { EscortManager.finish(action) } }
            r.onFailure { e -> toast(if (e is IOException) getString(R.string.network_error) else e.message ?: "") }
            r.onSuccess { if (action == "arrived") toast(getString(R.string.ride_arrived_ok)) }
            render()
        }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()

    companion object {
        fun intent(ctx: Context) = Intent(ctx, RideActivity::class.java)
    }
}
