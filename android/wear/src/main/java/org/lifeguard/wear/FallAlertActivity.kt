package org.lifeguard.wear

import android.app.Activity
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * "הכל בסדר?" אחרי נפילה: רטט חזק + ספירה לאחור 30 שנ' + "אני בסדר" / "צריך/ה עזרה".
 * בלי תשובה -> WatchController שולח לטלפון טריגר אוטומטי (watch_fall + ביטחון הגלאי) - והמסך עובר להציג את מצב המסירה.
 */
class FallAlertActivity : Activity() {
    private lateinit var title: TextView
    private lateinit var reason: TextView
    private lateinit var countdown: TextView
    private lateinit var okBtn: View
    private lateinit var helpBtn: View
    private lateinit var status: StatusPanel
    private val listener: () -> Unit = { render() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WatchController.init(this)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val pad = dp(18f).toInt()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(pad, dp(26f).toInt(), pad, dp(40f).toInt())
        }
        title = TextView(this).apply {
            textSize = 22f; gravity = Gravity.CENTER; setTextColor(WatchColors.LABEL)
            setTypeface(typeface, android.graphics.Typeface.BOLD); text = getString(R.string.fall_title)
        }
        reason = TextView(this).apply { textSize = 13f; gravity = Gravity.CENTER; setTextColor(WatchColors.SOS_TINT_TEXT) }
        countdown = TextView(this).apply { textSize = 14f; gravity = Gravity.CENTER; setTextColor(WatchColors.LABEL) }
        col.addView(title); col.addView(reason); col.addView(countdown)
        okBtn = pillButton(this, getString(R.string.fall_ok), WatchColors.SUCCESS_FILL) { WatchController.answerOk() }
        helpBtn = pillButton(this, getString(R.string.fall_help), WatchColors.SOS_FILL) { WatchController.answerHelp() }
        col.addView(okBtn); col.addView(helpBtn)
        status = StatusPanel(this, col)
        val scroll = ScrollView(this).apply {
            setBackgroundColor(WatchColors.FALL_BG)
            addView(col, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        setContentView(scroll)
        scroll.requestFocus()
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

    private fun render() {
        val c = WatchController
        val asking = c.fall.asking
        val t = c.now()
        okBtn.visibility = if (asking != null) View.VISIBLE else View.GONE
        helpBtn.visibility = if (asking != null) View.VISIBLE else View.GONE
        if (asking != null) {
            title.text = getString(R.string.fall_title)
            reason.text = getString(R.string.fall_detected)
            countdown.text = getString(R.string.fall_countdown, c.fall.secondsLeft(t))
            status.render(org.lifeguard.wear.logic.SosDelivery.View("", "", false, false, org.lifeguard.wear.logic.SosDelivery.Tone.PROGRESS))
            return
        }
        countdown.text = ""
        if (c.delivery.active) {
            title.text = getString(R.string.fall_detected)
            reason.text = ""
            status.render(c.delivery.view())
        } else if (t - c.lastFallDismissedAt < 3_000) {
            title.text = getString(R.string.fall_ok)
            reason.text = getString(R.string.fall_dismissed)
            title.postDelayed({ if (!isFinishing && WatchController.fall.asking == null && !WatchController.delivery.active) finish() }, 2_000)
        } else finish()
    }
}
