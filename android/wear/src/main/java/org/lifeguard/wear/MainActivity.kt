package org.lifeguard.wear

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import org.lifeguard.wear.logic.CancelWindow

/**
 * המסך הראשי בשעון: כפתור SOS גדול בהחזקה (2 שנ') -> חלון ביטול (5 שנ') -> שליחה לטלפון -> מצב אמיתי מהטלפון.
 * למטה: מתג "זיהוי נפילה" + איך הוא דוגם (סוללה).
 */
class MainActivity : Activity() {
    private lateinit var sos: HoldSosView
    private lateinit var countdown: TextView
    private lateinit var cancelBtn: View
    private lateinit var status: StatusPanel
    private lateinit var fallSwitch: Switch
    private lateinit var fallNote: TextView
    private val listener: () -> Unit = { render() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WatchController.init(this)
        val pad = dp(18f).toInt()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            // מסך עגול: שוליים כדי שהתוכן לא ייחתך בפינות
            setPadding(pad, dp(22f).toInt(), pad, dp(40f).toInt())
        }
        val scroll = ScrollView(this).apply {
            setBackgroundColor(WatchColors.BG)
            isVerticalScrollBarEnabled = true
            addView(col, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        val size = (resources.displayMetrics.widthPixels * 0.62f).toInt()
        sos = HoldSosView(this).apply {
            label = getString(R.string.sos_hold)
            contentDescription = getString(R.string.sos_desc)
            onTriggered = { WatchController.startCancelWindow(this@MainActivity) }
            layoutParams = LinearLayout.LayoutParams(size, size).apply { gravity = Gravity.CENTER_HORIZONTAL }
        }
        col.addView(sos)
        countdown = TextView(this).apply {
            textSize = 20f; gravity = Gravity.CENTER; setTextColor(WatchColors.LABEL)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }
        col.addView(countdown)
        cancelBtn = pillButton(this, getString(R.string.cancel), WatchColors.NEUTRAL_FILL) {
            if (WatchController.cancelSos()) sos.resetHold()
        }
        col.addView(cancelBtn)
        status = StatusPanel(this, col)

        fallSwitch = Switch(this).apply {
            text = getString(R.string.fall_setting)
            textSize = 14f
            setTextColor(WatchColors.LABEL)
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = dp(16f).toInt() }
            isChecked = WatchPrefs.fallEnabled(this@MainActivity)
            setOnCheckedChangeListener { _, on ->
                WatchPrefs.setFallEnabled(this@MainActivity, on)
                FallMonitorService.sync(this@MainActivity)
                sos.postDelayed({ render() }, 600)
            }
        }
        col.addView(fallSwitch)
        fallNote = TextView(this).apply { textSize = 12f; gravity = Gravity.CENTER; setTextColor(WatchColors.LABEL_SECONDARY) }
        col.addView(fallNote)
        setContentView(scroll)
        scroll.requestFocus()       // גלילה בכתר/בזל (rotary)

        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        FallMonitorService.sync(this)
    }

    override fun onResume() {
        super.onResume()
        WatchController.visibleScreens++
        WatchController.addListener(listener)
        render()
    }

    override fun onPause() {
        WatchController.removeListener(listener)
        WatchController.visibleScreens--
        super.onPause()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        FallMonitorService.sync(this)
        render()
    }

    private fun render() {
        val c = WatchController
        val counting = c.cancel.state == CancelWindow.State.COUNTING
        val d = c.delivery
        val inFlight = c.sendInFlight
        // אחרי ההחזקה: הכפתור נעול עד שהחלון נגמר/בוטל; בזמן שליחה/המתנה לאישור - נעול (אין SOS כפול מאותו שעון)
        sos.isEnabled = !counting && !inFlight
        if (!counting && !inFlight && sos.hold.triggered) sos.resetHold()
        sos.invalidate()
        countdown.visibility = if (counting || c.cancel.state == CancelWindow.State.CANCELLED) View.VISIBLE else View.GONE
        countdown.text = when (c.cancel.state) {
            CancelWindow.State.COUNTING -> getString(R.string.sending_in, c.cancel.secondsLeft(c.now()))
            CancelWindow.State.CANCELLED -> getString(R.string.cancelled)
            else -> ""
        }
        cancelBtn.visibility = if (counting) View.VISIBLE else View.GONE
        status.render(d.view())

        val enabled = WatchPrefs.fallEnabled(this)
        if (fallSwitch.isChecked != enabled) fallSwitch.isChecked = enabled
        fallNote.text = when {
            !enabled -> getString(R.string.fall_mode_off)
            WatchPrefs.monitorMode(this) == WatchPrefs.MODE_NO_SENSOR -> getString(R.string.fall_no_sensor)
            WatchPrefs.monitorMode(this) == WatchPrefs.MODE_WAKELOCK -> getString(R.string.fall_mode_wakelock)
            WatchPrefs.monitorMode(this) == WatchPrefs.MODE_WAKEUP -> getString(R.string.fall_mode_wakeup)
            else -> ""
        } + if (!WatchNotifier.canPost(this)) "\n" + getString(R.string.notif_permission_note) else ""
    }
}
