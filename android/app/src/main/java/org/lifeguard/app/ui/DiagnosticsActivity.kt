package org.lifeguard.app.ui

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.core.CheckResult
import org.lifeguard.app.core.CheckStatus
import org.lifeguard.app.core.DiagnosticsEvaluator
import org.lifeguard.app.core.FixAction
import org.lifeguard.app.databinding.ActivityDiagnosticsBinding
import org.lifeguard.app.fcm.FcmManager
import org.lifeguard.app.service.DiagnosticsCollector
import org.lifeguard.app.service.LifeGuardService
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** בדיקת מוכנות: כל בדיקה עם סטטוס, הסבר קצר ופעולת "תקן". */
class DiagnosticsActivity : AppCompatActivity() {
    private lateinit var b: ActivityDiagnosticsBinding
    private var checking = false

    private val permLauncher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        LifeGuardService.start(this)
        render()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityDiagnosticsBinding.inflate(layoutInflater)
        setContentView(b.root)
        title = getString(R.string.diag_title)
        b.refresh.setOnClickListener { refresh() }
    }

    override fun onResume() {
        super.onResume()
        render()
        refresh()   // חוזרים מהגדרות מערכת -> בדיקה מחדש
    }

    private fun refresh() {
        if (checking) return
        checking = true
        b.refresh.isEnabled = false
        b.checkedAt.text = getString(R.string.diag_checking)
        lifecycleScope.launch {
            withContext(Dispatchers.IO) { runCatching { DiagnosticsCollector.refreshRemote() } }
            checking = false
            b.refresh.isEnabled = true
            render()
        }
    }

    private fun render() {
        val results = DiagnosticsEvaluator.evaluate(DiagnosticsCollector.local(this))
        val sum = DiagnosticsEvaluator.summarize(results)
        b.summary.text = sum.textHe().removeSuffix(" - לחץ/י לפרטים")
        b.summary.setTextColor(color(if (sum.fail > 0) CheckStatus.FAIL else if (sum.warn > 0) CheckStatus.WARN else CheckStatus.OK))
        if (!checking) b.checkedAt.text = DiagnosticsCollector.remote?.let {
            getString(R.string.diag_checked_at, SimpleDateFormat("HH:mm:ss", Locale.US).format(Date((it.at * 1000).toLong())))
        } ?: ""
        b.list.removeAllViews()
        // תקלות קודם, אחר כך אזהרות
        val order = listOf(CheckStatus.FAIL, CheckStatus.WARN, CheckStatus.OK, CheckStatus.UNAVAILABLE)
        results.sortedBy { order.indexOf(it.status) }.forEachIndexed { i, r -> b.list.addView(row(r, i == 0)) }
    }

    private fun row(r: CheckResult, first: Boolean): View {
        val pad = (12 * resources.displayMetrics.density).toInt()
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad / 2)
        }
        // קו מפריד בין שורות (לא לפני הראשונה), כמו ברשימה מקובצת
        if (!first) box.addView(View(this).apply {
            setBackgroundColor(ContextCompat.getColor(this@DiagnosticsActivity, R.color.lg_separator))
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 1).apply { bottomMargin = pad }
        })
        box.addView(TextView(this).apply {
            text = "${symbol(r.status)}  ${r.title} · ${statusHe(r.status)}"
            setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL))
            setTextColor(color(r.status))
            textSize = 16f
        })
        box.addView(TextView(this).apply {
            text = r.detail
            textSize = 14f
            setLineSpacing(0f, 1.12f)
            setTextColor(ContextCompat.getColor(this@DiagnosticsActivity, R.color.lg_label_secondary))
            setPadding(0, pad / 3, 0, 0)
        })
        val fix = r.fix
        if (fix != null && r.status != CheckStatus.OK) {
            box.addView(com.google.android.material.button.MaterialButton(this, null, R.attr.lgTonalButtonStyle).apply {
                text = r.fixLabel ?: getString(R.string.diag_fix)
                setOnClickListener { runFix(fix, r.fixArg) }
                layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            })
        }
        return box
    }

    private fun symbol(s: CheckStatus) = when (s) {
        CheckStatus.OK -> "✓"; CheckStatus.WARN -> "!"; CheckStatus.FAIL -> "✗"; CheckStatus.UNAVAILABLE -> "–"
    }

    private fun statusHe(s: CheckStatus) = when (s) {
        CheckStatus.OK -> "תקין"; CheckStatus.WARN -> "אזהרה"; CheckStatus.FAIL -> "תקלה"; CheckStatus.UNAVAILABLE -> "לא זמין"
    }

    private fun color(s: CheckStatus) = when (s) {
        CheckStatus.OK -> ContextCompat.getColor(this, R.color.ok_green)
        CheckStatus.WARN -> ContextCompat.getColor(this, R.color.warn_amber)
        CheckStatus.FAIL -> ContextCompat.getColor(this, R.color.sos_red_dark)
        CheckStatus.UNAVAILABLE -> ContextCompat.getColor(this, R.color.lg_label_tertiary)
    }

    @SuppressLint("BatteryLife")
    private fun runFix(fix: FixAction, arg: String?) {
        val pkg = Uri.parse("package:$packageName")
        when (fix) {
            FixAction.LOCATION_PERMISSION -> explain(R.string.location_explain) {
                permLauncher.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
            }
            FixAction.BACKGROUND_LOCATION -> if (Build.VERSION.SDK_INT >= 29) explain(R.string.bg_location_explain) {
                permLauncher.launch(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION))
            }
            FixAction.LOCATION_SETTINGS -> open(Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS))
            FixAction.START_SERVICE -> { LifeGuardService.start(this); b.root.postDelayed({ render() }, 800) }
            FixAction.BATTERY_EXEMPTION -> explain(R.string.battery_explain) {
                open(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, pkg))
            }
            FixAction.BATTERY_SAVER_SETTINGS -> open(Intent(Settings.ACTION_BATTERY_SAVER_SETTINGS))
            FixAction.APP_DETAILS -> open(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkg))
            FixAction.NETWORK_SETTINGS -> open(Intent(Settings.ACTION_WIRELESS_SETTINGS))
            FixAction.SERVER_SETTINGS, FixAction.SMS_SETTINGS -> startActivity(Intent(this, SettingsActivity::class.java))
            FixAction.RELOGIN -> AlertDialog.Builder(this).setMessage(R.string.diag_relogin_confirm)
                .setPositiveButton(android.R.string.ok) { _, _ ->
                    LifeGuardService.stop(this)
                    FcmManager.onLogout()
                    LG.store.clearSession()
                    startActivity(Intent(this, LoginActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
                    finish()
                }.setNegativeButton(android.R.string.cancel, null).show()
            FixAction.NOTIFICATION_PERMISSION -> if (Build.VERSION.SDK_INT >= 33)
                permLauncher.launch(arrayOf(Manifest.permission.POST_NOTIFICATIONS))
            FixAction.APP_NOTIFICATION_SETTINGS -> open(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
            FixAction.CHANNEL_SETTINGS -> open(Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, packageName).putExtra(Settings.EXTRA_CHANNEL_ID, arg))
            FixAction.FULL_SCREEN_SETTINGS -> if (Build.VERSION.SDK_INT >= 34) explain(R.string.fullscreen_explain) {
                open(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, pkg))
            }
            FixAction.SMS_PERMISSION -> permLauncher.launch(arrayOf(Manifest.permission.SEND_SMS))
            FixAction.MIC_PERMISSION -> explain(R.string.audio_evidence_explain) {
                permLauncher.launch(arrayOf(Manifest.permission.RECORD_AUDIO))
            }
            FixAction.RETRY -> refresh()
        }
    }

    private fun explain(msg: Int, then: () -> Unit) {
        AlertDialog.Builder(this).setMessage(msg).setPositiveButton(android.R.string.ok) { _, _ -> then() }
            .setNegativeButton(android.R.string.cancel, null).show()
    }

    private fun open(i: Intent) {
        // הגדרות יצרן חסרות: חוזרים למסך פרטי האפליקציה
        runCatching { startActivity(i) }.onFailure {
            runCatching { startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName"))) }
        }
    }

    companion object {
        fun intent(ctx: Context) = Intent(ctx, DiagnosticsActivity::class.java)
    }
}
