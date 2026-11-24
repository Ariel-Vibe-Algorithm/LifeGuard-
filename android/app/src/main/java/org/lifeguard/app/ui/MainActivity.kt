package org.lifeguard.app.ui

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.widget.Button
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
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
import org.lifeguard.app.api.IncidentView
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.core.DiagnosticsEvaluator
import org.lifeguard.app.core.Geo
import org.lifeguard.app.core.Helpers
import org.lifeguard.app.core.SafePlaces
import org.lifeguard.app.core.Tone
import org.lifeguard.app.core.SmsState
import org.lifeguard.app.databinding.ActivityMainBinding
import org.lifeguard.app.fcm.FcmManager
import org.lifeguard.app.service.DiagnosticsCollector
import org.lifeguard.app.service.EscortManager
import org.lifeguard.app.service.IncidentManager
import org.lifeguard.app.service.LifeGuardService
import org.lifeguard.app.service.SafePlaceFinder
import org.lifeguard.app.service.SmsSender
import org.lifeguard.app.service.hasLocationPermission
import java.io.IOException

class MainActivity : AppCompatActivity() {
    private lateinit var b: ActivityMainBinding
    private var inbox: List<IncidentView> = emptyList()
    /** סטטוס מתנדבי תמיכה/הזעקה באירוע הפעיל - רק מתשובת השרת: (incidentId או "" = לפני השרת, טקסט, טון) */
    private var helpersStatus: Triple<String, String, Tone>? = null
    private var helpersBusy = false
    /** מקום בטוח: תוצאה אופליין מיידית, ומשופרת ברשת כשאפשר */
    private var safe: SafePlaceFinder.Result? = null
    private var safeJob: kotlinx.coroutines.Job? = null
    private var safeRefineJob: kotlinx.coroutines.Job? = null

    private val permLauncher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        LifeGuardService.start(this)   // מפעיל מחדש עם סוג location אם ניתנה הרשאה
        render()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!LG.store.isLoggedIn) { openLogin(); return }
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)
        title = LG.store.displayName ?: getString(R.string.app_name)

        b.sosBtn.fillColor = ContextCompat.getColor(this, R.color.lg_fill_sos)
        b.sosBtn.label = getString(R.string.sos_hold)
        b.sosBtn.textSizePx = 26f * resources.displayMetrics.scaledDensity
        b.sosBtn.onTriggered = { IncidentManager.startSos(this, "loud"); render() }
        // מצב שקט: כפתור קטן וניטרלי, גם הוא בהחזקה (בלי צליל, בלי מסך אדום)
        b.silentBtn.fillColor = ContextCompat.getColor(this, R.color.neutral)
        b.silentBtn.showHalo = false
        b.silentBtn.label = getString(R.string.silent_hold)
        b.silentBtn.textSizePx = 12f * resources.displayMetrics.scaledDensity
        b.silentBtn.onTriggered = { IncidentManager.startSos(this, "silent"); render() }

        b.safeBtn.setOnClickListener { IncidentManager.resolve(this); render() }
        b.falseAlarmBtn.setOnClickListener {
            AlertDialog.Builder(this).setMessage(R.string.false_alarm_confirm).setPositiveButton(R.string.false_alarm) { _, _ ->
                val key = IncidentManager.resolve(this, falseAlarm = true)
                render()
                EvidenceActivity.askDeleteFalseAlarm(this, key)        // מחיקת התיעוד המקומי רק באישור
            }.setNegativeButton(android.R.string.cancel, null).show()
        }
        // אירוע "unconfirmed" (זיהוי אוטומטי בביטחון בינוני): "כן, זה חירום" -> /confirm; "אני בסדר" -> ביטול כאזעקת שווא
        b.confirmYes.setOnClickListener {
            b.confirmYes.isEnabled = false
            lifecycleScope.launch {
                val ok = withContext(Dispatchers.IO) { IncidentManager.confirmEmergency() }
                b.confirmYes.isEnabled = true
                toast(getString(if (ok) R.string.confirm_sent else R.string.confirm_pending))
                LifeGuardService.start(this@MainActivity, kick = true)
                render()
            }
        }
        b.confirmOk.setOnClickListener {
            AlertDialog.Builder(this).setMessage(R.string.confirm_ok_question).setPositiveButton(R.string.confirm_ok) { _, _ ->
                val key = IncidentManager.resolve(this, falseAlarm = true)
                render()
                EvidenceActivity.askDeleteFalseAlarm(this, key)
            }.setNegativeButton(android.R.string.cancel, null).show()
        }
        b.muteBtn.setOnClickListener { IncidentManager.stopSiren(); render() }
        b.chatBtn.setOnClickListener { LG.store.activeIncidentId?.let { startActivity(ChatActivity.intent(this, it)) } }
        b.liaisonBtn.setOnClickListener { requestHelper(Helpers.AUTHORITY_LIAISON) }
        b.talkBtn.setOnClickListener { requestHelper(Helpers.REMOTE_SUPPORT) }
        b.safePlaceNav.setOnClickListener { navigateToSafePlace() }
        b.supportBtn.setOnClickListener { startActivity(SupportActivity.intent(this)) }

        b.firstAidBtn.text = Ui.twoLine(this, getString(R.string.home_tile_first_aid), getString(R.string.home_tile_first_aid_sub))
        b.supportBtn.text = Ui.twoLine(this, getString(R.string.home_tile_support), getString(R.string.home_tile_support_sub))
        b.firstAidBtn.setOnClickListener { startActivity(FirstAidActivity.intent(this)) }
        b.firstAidActiveBtn.setOnClickListener { startActivity(FirstAidActivity.intent(this)) }
        b.escortStart.setOnClickListener { startEscort() }
        b.rideBtn.setOnClickListener { startActivity(RideActivity.intent(this)) }
        b.escortArrived.setOnClickListener { finishEscort("arrived") }
        b.escortCancel.setOnClickListener { finishEscort("cancelled") }

        b.permNotif.setOnClickListener {
            if (Build.VERSION.SDK_INT >= 33) permLauncher.launch(arrayOf(Manifest.permission.POST_NOTIFICATIONS))
        }
        b.permLocation.setOnClickListener {
            AlertDialog.Builder(this).setMessage(R.string.location_explain).setPositiveButton(android.R.string.ok) { _, _ ->
                permLauncher.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
            }.setNegativeButton(android.R.string.cancel, null).show()
        }
        b.permBgLocation.setOnClickListener {
            if (Build.VERSION.SDK_INT >= 29) permLauncher.launch(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION))
        }
        b.permFullScreen.setOnClickListener { openFullScreenSettings() }
        b.permBattery.setOnClickListener { askBatteryExemption() }
        b.permSms.setOnClickListener { startActivity(Intent(this, SettingsActivity::class.java)) }
        b.readiness.setOnClickListener { startActivity(DiagnosticsActivity.intent(this)) }

        LifeGuardService.start(this)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch {
                    LG.events.collect { e ->
                        when (e) {
                            UiEvent.LoggedOut -> openLogin()
                            is UiEvent.IncidentUpdated -> {
                                loadInbox()
                                // עדכון מתנדבי תמיכה/הזעקה באירוע שלי (helper / liaison) -> סטטוס מהשרת
                                if (e.incidentId == LG.store.activeIncidentId) refreshHelpers()
                            }
                            is UiEvent.IncidentClosed, is UiEvent.RingChanged -> loadInbox()
                            else -> {}
                        }
                        render()
                    }
                }
                launch { while (true) { render(); delay(5000) } }
            }
        }
        if (!LG.store.batteryPromptShown && !isIgnoringBattery()) {
            LG.store.batteryPromptShown = true
            askBatteryExemption()
        }
        if (Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS)) {
            permLauncher.launch(arrayOf(Manifest.permission.POST_NOTIFICATIONS, Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION))
        }
    }

    override fun onResume() {
        super.onResume()
        if (!LG.store.isLoggedIn) { openLogin(); return }
        render(); loadInbox()
        refreshReadiness()
        refreshHelpers()
    }

    /** בדיקות השרת של שורת המוכנות - לכל היותר פעם ב-5 דקות. */
    private fun refreshReadiness() {
        val last = DiagnosticsCollector.remote?.at ?: 0.0
        if (LG.nowSec() - last < 300) return
        lifecycleScope.launch {
            withContext(Dispatchers.IO) { runCatching { DiagnosticsCollector.refreshRemote() } }
            render()
        }
    }

    private fun granted(p: String) = ContextCompat.checkSelfPermission(this, p) == PackageManager.PERMISSION_GRANTED

    private fun isIgnoringBattery() = getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)

    private fun canFullScreen() = Build.VERSION.SDK_INT < 34 ||
        getSystemService(NotificationManager::class.java).canUseFullScreenIntent()

    @SuppressLint("BatteryLife")
    private fun askBatteryExemption() {
        AlertDialog.Builder(this).setTitle(R.string.perm_battery).setMessage(R.string.battery_explain)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                runCatching {
                    startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
                }
            }.setNegativeButton(android.R.string.cancel, null).show()
    }

    private fun openFullScreenSettings() {
        AlertDialog.Builder(this).setMessage(R.string.fullscreen_explain).setPositiveButton(android.R.string.ok) { _, _ ->
            if (Build.VERSION.SDK_INT >= 34) runCatching {
                startActivity(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:$packageName")))
            }
        }.setNegativeButton(android.R.string.cancel, null).show()
    }

    private fun render() {
        if (!::b.isInitialized) return
        val online = LG.isOnline()
        b.status.text = "${LG.store.displayName ?: ""} · " + getString(if (online) R.string.status_online else R.string.status_offline)
        b.status.setTextColor(ContextCompat.getColor(this, R.color.lg_label_secondary))
        androidx.core.widget.TextViewCompat.setCompoundDrawableTintList(b.status, android.content.res.ColorStateList.valueOf(
            ContextCompat.getColor(this, if (online) R.color.lg_success else R.color.lg_label_tertiary)))

        // ---- האירוע שלי ----
        val pending = LG.store.pendingSos
        val activeId = LG.store.activeIncidentId
        val mode = IncidentManager.activeMode
        val active = pending != null || activeId != null
        val wasActive = b.activeCard.visibility == View.VISIBLE
        b.activeCard.visibility = if (active) View.VISIBLE else View.GONE
        if (active && !wasActive) Ui.reveal(b.activeCard)      // קוסמטי בלבד: ה-SOS כבר נשלח לפני ה-render
        b.sosHero.visibility = if (active) View.GONE else View.VISIBLE
        b.sosBtn.visibility = if (active) View.GONE else View.VISIBLE
        b.silentBtn.visibility = if (active) View.GONE else View.VISIBLE
        if (active) {
            val silent = mode == "silent"
            // מצוקה שקטה: כרטיס ניטרלי ומילים שגרתיות - מי שמסתכל על המסך לא מבין שיש חירום
            Ui.setCardTone(b.activeCard, alert = !silent)
            b.activeTitle.text = getString(if (silent) R.string.active_silent_title else R.string.active_loud_title)
            b.activeTitle.setTextColor(ContextCompat.getColor(this, if (silent) R.color.lg_label else R.color.lg_sos))
            b.safeBtn.text = getString(if (silent) R.string.finish else R.string.im_safe)
            val lines = ArrayList<String>()
            lines += getString(if (pending != null) R.string.sos_pending else R.string.sos_sent)
            if (!silent) when (pending?.smsState) {
                SmsState.SENDING -> lines += getString(R.string.sms_sending)
                SmsState.SENT -> lines += getString(R.string.sms_sent)
                SmsState.FAILED -> lines += getString(R.string.sms_failed)
                else -> if (LG.store.sosSmsSent) lines += getString(R.string.sms_sent)
            }
            if (activeId != null && LG.store.pendingConfirm) lines += getString(R.string.confirm_pending)
            b.activeDetail.text = lines.joinToString("\n")
            b.confirmBox.visibility = if (IncidentManager.awaitingOwnerConfirmation) View.VISIBLE else View.GONE
            b.chatBtn.visibility = if (activeId != null) View.VISIBLE else View.GONE
            b.muteBtn.visibility = if (LG.alarm.mode == org.lifeguard.app.alert.AlarmPlayer.Mode.SIREN) View.VISIBLE else View.GONE
            // במצוקה שקטה לא מציגים כפתור שמסגיר שיש חירום
            b.falseAlarmBtn.visibility = if (silent) View.GONE else View.VISIBLE
            b.firstAidActiveBtn.visibility = if (silent) View.GONE else View.VISIBLE
            renderHelpers(activeId)
            // מקום בטוח: לא במצוקה שקטה (לא מציגים על המסך שום דבר שמסגיר חירום)
            if (silent) b.safePlaceBox.visibility = View.GONE else updateSafePlace()
        } else {
            safeJob?.cancel(); safeRefineJob?.cancel(); safe = null
            helpersStatus = null
        }

        // ---- ליווי ----
        val escort = EscortManager.activeId
        b.escortForm.visibility = if (escort == null) View.VISIBLE else View.GONE
        b.escortActive.visibility = if (escort != null) View.VISIBLE else View.GONE
        if (escort != null) {
            val nowS = runCatching { LG.api().serverNow() }.getOrDefault(LG.nowSec())
            val left = (LG.store.escortDeadline - nowS).toLong().coerceAtLeast(0)
            b.escortStatus.text = getString(R.string.escort_active, "%d:%02d".format(left / 60, left % 60))
        }
        // נסיעה פעילה מנוהלת במסך הנסיעה; ליווי רגיל פעיל - אי אפשר להתחיל נסיעה במקביל
        b.rideBtn.visibility = if (escort == null || EscortManager.isRide) View.VISIBLE else View.GONE

        // ---- הרשאות ----
        val needNotif = Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS)
        val needLoc = !hasLocationPermission(this)
        val needBg = Build.VERSION.SDK_INT >= 29 && !needLoc && !granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
        val needFs = !canFullScreen()
        val needBat = !isIgnoringBattery()
        val needSms = LG.store.emergencyNumbers.isEmpty() || !SmsSender.hasPermission(this)
        fun show(v: Button, need: Boolean) { v.visibility = if (need) View.VISIBLE else View.GONE }
        show(b.permNotif, needNotif); show(b.permLocation, needLoc); show(b.permBgLocation, needBg)
        show(b.permFullScreen, needFs); show(b.permBattery, needBat); show(b.permSms, needSms)
        b.setupCard.visibility = if (needNotif || needLoc || needBg || needFs || needBat || needSms) View.VISIBLE else View.GONE

        val sum = DiagnosticsEvaluator.summarize(DiagnosticsEvaluator.evaluate(DiagnosticsCollector.local(this)))
        b.readiness.text = sum.textHe()
        b.readiness.setTextColor(ContextCompat.getColor(this, when {
            sum.fail > 0 -> R.color.sos_red_dark
            sum.warn > 0 -> R.color.warn_amber
            else -> R.color.ok_green
        }))

        renderInbox()
    }

    // ---------------- מתנדבי תמיכה / הזעקת רשויות (main.py SosWindow._request_helper / helpers_summary) ----------------
    private fun requestHelper(kind: String) {
        val id = LG.store.activeIncidentId
        if (id == null) {
            helpersStatus = Triple("", Helpers.NOT_ON_SERVER_YET, Tone.ERROR); render(); return
        }
        if (helpersBusy) return
        helpersBusy = true
        helpersStatus = Triple(id, "⏳ שולח בקשה...", Tone.MUTED); render()
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { IncidentManager.requestHelper(kind) } }
            helpersBusy = false
            r.onSuccess { v -> showHelpers(v) }
            r.onFailure { e -> helpersStatus = Triple(id, "❌ " + Ui.errorText(this@MainActivity, e), Tone.ERROR) }
            render()
        }
    }

    private fun refreshHelpers() {
        val id = LG.store.activeIncidentId ?: return
        if (helpersBusy) return
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().getIncident(id) } }
            r.onSuccess { v -> if (v.id == LG.store.activeIncidentId && !helpersBusy) { showHelpers(v); render() } }
        }
    }

    private fun showHelpers(v: IncidentView) {
        // מה התבקש: מהשרת (helper_requests, כולל הזעקה אוטומטית); בשרת ישן - מה שביקשתי מהמכשיר הזה
        val requested = (v.helperRequests ?: emptyList()).toSet() + IncidentManager.requestedHelperKinds(v.id)
        val (text, tone) = Helpers.summary(v.helpers, requested)
        helpersStatus = Triple(v.id, text, tone)
    }

    private fun renderHelpers(activeId: String?) {
        // לפני שהאירוע נוצר בשרת אי אפשר לבקש - הלחיצה מסבירה זאת (כמו ב-Windows)
        val st = helpersStatus?.takeIf { it.first == "" || it.first == activeId }
        b.helpersStatus.visibility = if (st == null || st.second.isEmpty()) View.GONE else View.VISIBLE
        if (st != null) {
            b.helpersStatus.text = st.second
            b.helpersStatus.setTextColor(toneColor(st.third))
        }
        b.liaisonBtn.isEnabled = !helpersBusy
        b.talkBtn.isEnabled = !helpersBusy
    }

    private fun toneColor(t: Tone): Int = when (t) {
        Tone.OK -> ContextCompat.getColor(this, R.color.ok_green)
        Tone.ERROR -> ContextCompat.getColor(this, R.color.sos_red_dark)
        Tone.WARN -> ContextCompat.getColor(this, R.color.warn_amber)
        Tone.MUTED -> Ui.gray(this)
    }

    // ---------------- מקום בטוח קרוב (map_module.find_safe_route: אופליין מיד, הרשת רק משפרת) ----------------
    private fun updateSafePlace() {
        b.safePlaceBox.visibility = View.VISIBLE
        val fix = LG.lastFix
        val cur = safe
        val moved = fix != null && (cur == null ||
            Geo.distanceMeters(cur.origin.lat, cur.origin.lon, fix.lat, fix.lon) > 75 ||
            (fix.capturedAt ?: 0.0) - (cur.origin.capturedAt ?: 0.0) > SafePlaces.STALE_ORIGIN_S)
        if (fix != null && moved && safeJob?.isActive != true) {
            safeJob = lifecycleScope.launch {
                // מיידי ובלי רשת: הרשימה המובנית (assets). לא מחכה לשום דבר ברשת.
                // העדפת מקום התפילה (מוצפנת, מקומית בלבד) - רק כשאין מקום בטוח אחר קרוב
                val pref = LG.store.worshipFallback
                val r = withContext(Dispatchers.Default) { SafePlaceFinder.offline(this@MainActivity, fix, pref) }
                safe = r
                renderSafePlace()
                if (r.choice != null && LG.networkAvailable(this@MainActivity)) {
                    safeRefineJob?.cancel()
                    safeRefineJob = lifecycleScope.launch {
                        val better = withContext(Dispatchers.IO) { runCatching { SafePlaceFinder.refine(r) }.getOrNull() }
                        if (better != null && safe === r) { safe = better; renderSafePlace() }
                    }
                }
            }
        }
        renderSafePlace()
    }

    private fun renderSafePlace() {
        val r = safe
        val c = r?.choice
        b.safePlaceText.text = when {
            r == null && LG.lastFix == null -> SafePlaces.NO_LOCATION_HE
            r == null -> "⏳ מחפש מקום בטוח קרוב..."
            c == null -> SafePlaces.NONE_FOUND_HE
            else -> SafePlaces.summaryHe(c, r.routeM, r.routeS)
        }
        val note = r?.let { SafePlaces.originNoteHe(it.origin.capturedAt, it.origin.accuracyM, LG.nowSec()) }
        b.safePlaceNote.visibility = if (note == null) View.GONE else View.VISIBLE
        b.safePlaceNote.text = note ?: ""
        b.safePlaceNav.visibility = if (c != null) View.VISIBLE else View.GONE
    }

    private fun navigateToSafePlace() {
        val r = safe ?: return
        val c = r.choice ?: return
        for (uri in SafePlaces.navigationUris(r.origin.lat, r.origin.lon, c.place)) {
            try {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(uri)))
                return
            } catch (_: android.content.ActivityNotFoundException) {
            }
        }
        toast("לא נמצאה אפליקציית מפות או דפדפן במכשיר")
    }

    private fun loadInbox() {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().inbox() } }
            r.onSuccess { inbox = it; LG.connectivity.onSuccess(LG.nowSec()) }
            r.exceptionOrNull()?.let { if (it is ApiException && it.isAuth) { FcmManager.onLogout(); LG.store.clearSession(); openLogin() } }
            renderInbox()
        }
    }

    private fun renderInbox() {
        val box = b.inbox
        box.removeAllViews()
        if (inbox.isEmpty()) {
            box.addView(Ui.card(this).apply {
                (layoutParams as android.widget.LinearLayout.LayoutParams).topMargin = 0
                addView(Ui.text(this@MainActivity, getString(R.string.inbox_empty), 15f, color = Ui.gray(this@MainActivity), topDp = 0))
            })
            return
        }
        for (v in inbox) {
            val ringing = LG.ring.isRinging(v.id)
            // שורה ברשימה; קריאה שמצלצלת עכשיו - רקע אדום בהיר וטקסט אדום, כדי שתבלוט
            val btn = Ui.listRow(this, "") {}.apply {
                if (ringing) {
                    backgroundTintList = android.content.res.ColorStateList.valueOf(ContextCompat.getColor(this@MainActivity, R.color.lg_sos_tint))
                    setTextColor(ContextCompat.getColor(this@MainActivity, R.color.lg_sos))
                }
            }
            if (v.isHelper) {
                // בקשת תמיכה/הזעקה: בלי שם ובלי מיקום לפני קבלה (השרת לא שולח אותם)
                btn.text = (if (ringing) "🔔 " else "") + Helpers.title(v) + " · " + (v.helperKindLabel ?: "") +
                    (if (v.myState == "accepted") " · אצלך" else "")
                btn.setOnClickListener { startActivity(Notifier.alertIntent(this, v, ringing)) }
                box.addView(btn)
                continue
            }
            val dist = distanceKm(v)?.let { " · " + Geo.formatDistanceHe(it) } ?: ""
            btn.text = (if (ringing) "🔔 " else "") + "${v.ownerDisplay} · ${Geo.emergencyTypeHe(v.emergencyType)}$dist"
            btn.setOnClickListener {
                if (v.fullAccess && !ringing && v.myState == "accepted") startActivity(ChatActivity.intent(this, v.id))
                else startActivity(Intent(this, IncomingAlertActivity::class.java)
                    .putExtra(IncomingAlertActivity.EXTRA_INCIDENT_ID, v.id)
                    .putExtra(IncomingAlertActivity.EXTRA_RINGING, ringing))
            }
            box.addView(btn)
        }
    }

    private fun startEscort() {
        val minutes = b.escortMinutes.text.toString().toIntOrNull()?.coerceIn(1, 1440) ?: 20
        val dest = b.escortDest.text.toString().trim()
        b.escortStart.isEnabled = false
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { EscortManager.start(minutes, dest) } }
            b.escortStart.isEnabled = true
            r.onFailure { e -> toast(if (e is ApiException) e.message ?: "" else getString(R.string.escort_needs_network)) }
            LifeGuardService.start(this@MainActivity)
            render()
        }
    }

    private fun finishEscort(action: String) {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { EscortManager.finish(action) } }
            r.onFailure { e -> toast(if (e is IOException) getString(R.string.network_error) else e.message ?: "") }
            render()
        }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 1, 0, R.string.menu_settings)
        menu.add(0, 2, 1, R.string.refresh)
        menu.add(0, 4, 2, R.string.diag_title)
        menu.add(0, 5, 2, R.string.evidence_title)
        menu.add(0, 6, 2, R.string.national_title)
        menu.add(0, 7, 2, R.string.location_sharing_title)
        menu.add(0, 8, 2, R.string.contacts_location_title)
        menu.add(0, 9, 2, R.string.guardian_title)
        menu.add(0, 10, 2, R.string.privacy_history_title)
        menu.add(0, 11, 2, R.string.volunteer_roles_title)
        menu.add(0, 3, 3, R.string.menu_logout)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            1 -> startActivity(Intent(this, SettingsActivity::class.java))
            2 -> { loadInbox(); render() }
            3 -> logout()
            4 -> startActivity(DiagnosticsActivity.intent(this))
            5 -> startActivity(EvidenceActivity.intent(this))
            6 -> startActivity(NationalActivity.intent(this))
            7 -> startActivity(LocationSharingActivity.intent(this))
            8 -> startActivity(ContactLocationActivity.intent(this))
            9 -> startActivity(GuardianActivity.intent(this))
            10 -> startActivity(PrivacyHistoryActivity.intent(this))
            11 -> startActivity(VolunteerRolesActivity.intent(this))
            else -> return super.onOptionsItemSelected(item)
        }
        return true
    }

    private fun logout() {
        lifecycleScope.launch {
            withContext(Dispatchers.IO) {
                FcmManager.unregisterBlocking()          // DELETE /me/devices/fcm כל עוד ההתחברות תקפה
                runCatching { LG.api().logout() }
            }
            LifeGuardService.stop(this@MainActivity)
            LG.alarm.stop()
            FcmManager.onLogout()
            LG.store.clearSession()
            openLogin()
        }
    }

    private fun openLogin() {
        startActivity(Intent(this, LoginActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finish()
    }

    companion object {
        fun distanceKm(v: IncidentView): Double? {
            v.distanceKm?.let { return it }
            val me = LG.lastFix ?: return null
            val loc = v.location ?: return null
            return Geo.distanceMeters(me.lat, me.lon, loc.lat, loc.lon) / 1000.0
        }
    }
}
