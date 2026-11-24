package org.lifeguard.app.ui

import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import android.widget.Toast
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
import org.lifeguard.app.core.WellnessMonitor
import org.lifeguard.app.databinding.ActivityWellnessBinding
import org.lifeguard.app.service.EscortManager
import org.lifeguard.app.service.LifeGuardService
import kotlin.math.ceil

/**
 * "הכל בסדר?" במסך מלא עם ספירה לאחור.
 * - מצב רגיל (WellnessMonitor): 45 שנ' וצפצוף. אין תשובה -> SOS גלוי (WellnessMonitor.tick בשירות).
 * - מצב נסיעה (escort_check מהשרת): הטקסט וה-respond_by מהשרת; התשובה נשלחת ל-POST /escorts/{id}/checkin.
 *   אין תשובה -> השרת מסלים. בנסיעה שקטה המסך ניטרלי: בלי אדום, בלי צליל, תוויות שגרתיות.
 */
class WellnessCheckActivity : AppCompatActivity() {
    private lateinit var b: ActivityWellnessBinding
    private var answered = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }
        else @Suppress("DEPRECATION") window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
            WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        b = ActivityWellnessBinding.inflate(layoutInflater)
        setContentView(b.root)
        if (intent.getBooleanExtra(EXTRA_ESCORT, false)) escortMode() else wellnessMode()
    }

    private fun wellnessMode() {
        b.okBtn.setOnClickListener { LG.wellness.answer(true); finish() }
        b.helpBtn.setOnClickListener { LG.wellness.answer(false); finish() }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    val p = LG.wellness.pending
                    if (p == null) { finish(); break }
                    b.reason.text = p.text ?: WellnessMonitor.REASONS_HE[p.reason] ?: p.reason
                    val left = ceil(p.deadline - LG.nowSec()).toInt().coerceAtLeast(0)
                    b.countdown.text = getString(R.string.wellness_countdown, left)
                    if (left == 0) LifeGuardService.start(this@WellnessCheckActivity, kick = true)
                    delay(500)
                }
            }
        }
    }

    private fun escortMode() {
        val c = LG.escortCheck ?: run { finish(); return }
        if (c.silent) {
            // ניטרלי: נראה כמו תזכורת רגילה של אפליקציה
            b.root.setBackgroundColor(ContextCompat.getColor(this, R.color.lg_bg))
            window.statusBarColor = ContextCompat.getColor(this, R.color.lg_bg)
            window.navigationBarColor = window.statusBarColor
            b.title.text = "LifeGuard"
            b.title.textSize = 24f
            b.countdown.setTextColor(ContextCompat.getColor(this, R.color.lg_label_secondary))
            val gray = ColorStateList.valueOf(ContextCompat.getColor(this, R.color.neutral))
            b.okBtn.backgroundTintList = gray; b.helpBtn.backgroundTintList = gray
            b.okBtn.text = getString(R.string.ride_check_ok_neutral)
            b.helpBtn.text = getString(R.string.ride_check_help_neutral)
        } else {
            b.title.text = getString(R.string.ride_check_title)
        }
        b.reason.text = c.text
        b.okBtn.setOnClickListener { answer("ok") }
        b.helpBtn.setOnClickListener { answer("help") }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (!answered) {
                    val cur = LG.escortCheck
                    if (cur == null) { finish(); break }
                    val by = cur.respondBy
                    if (by == null) b.countdown.text = ""
                    else {
                        val left = ceil(by - LG.api().serverNow()).toInt().coerceAtLeast(0)
                        b.countdown.text = getString(if (cur.silent) R.string.ride_check_countdown_neutral
                        else R.string.ride_check_countdown, left)
                    }
                    delay(500)
                }
            }
        }
    }

    private fun answer(status: String) {
        if (answered) return
        answered = true
        b.okBtn.isEnabled = false; b.helpBtn.isEnabled = false
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { EscortManager.checkin(this@WellnessCheckActivity, status) } }
            val msg = when (r.getOrNull()) {
                "queued" -> getString(R.string.ride_checkin_queued)
                "sos_fallback" -> getString(R.string.ride_sos_fallback)
                null -> r.exceptionOrNull()?.message
                else -> null
            }
            msg?.let { Toast.makeText(this@WellnessCheckActivity, it, Toast.LENGTH_LONG).show() }
            LifeGuardService.start(this@WellnessCheckActivity, kick = true)
            finish()
        }
    }

    companion object {
        const val EXTRA_ESCORT = "escort_check"
        fun escortIntent(ctx: Context) = Intent(ctx, WellnessCheckActivity::class.java).putExtra(EXTRA_ESCORT, true)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
    }
}
