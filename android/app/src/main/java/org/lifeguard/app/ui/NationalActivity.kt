package org.lifeguard.app.ui

import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.api.NationalStatus
import org.lifeguard.app.core.RideText
import java.io.IOException

/**
 * כרטיס מצב ארצי (קריאה בלבד): מצב התרעות פיקוד העורף כפי שהשרת מדווח (כולל "לא מחובר"), מקלטים ציבוריים קרובים
 * מהמאגרים הרשמיים עם מקור ומרחק, הנחיות ומספרי חירום. המקור המחייב - אפליקציית פיקוד העורף.
 */
class NationalActivity : AppCompatActivity() {
    private lateinit var list: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.national_title)
        val pad = (16 * resources.displayMetrics.density).toInt()
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad) }
        setContentView(ScrollView(this).apply { setBackgroundColor(Ui.color(this@NationalActivity, R.color.lg_bg)); isFillViewport = true; addView(list) })
    }

    override fun onResume() { super.onResume(); load() }

    private fun text(s: String, bold: Boolean = false, size: Float = 15f, color: Int? = null) = TextView(this).apply {
        text = s; textSize = size; if (bold) setTypeface(typeface, Typeface.BOLD); color?.let { setTextColor(it) }
        setPadding(0, 6, 0, 6)
    }

    private fun load() {
        list.removeAllViews(); list.addView(text(getString(R.string.national_loading)))
        val fix = LG.lastFix
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().nationalStatus(fix?.lat, fix?.lon) } }
            list.removeAllViews()
            r.onFailure {
                list.addView(text(if (it is IOException) getString(R.string.national_offline) else (it.message ?: ""), bold = true))
                list.addView(text(getString(R.string.national_official)))
            }
            r.onSuccess { render(it, fix != null) }
        }
    }

    private fun render(s: NationalStatus, hadLocation: Boolean) {
        list.addView(text(getString(R.string.national_alerts), bold = true, size = 17f))
        when (s.alertsStatus) {
            "not_configured" -> list.addView(text("לא זמין: " + (s.alertsText ?: "התרעות לא מחוברות בשרת"), color = Ui.gray(this)))
            "error" -> list.addView(text("לא זמין כרגע: " + (s.alertsText ?: ""), color = Ui.gray(this)))
            else -> {
                val c = s.current
                if (c == null) list.addView(text(getString(R.string.national_no_alert), color = ContextCompat.getColor(this, R.color.ok_green)))
                else {
                    list.addView(text(c.title + if (c.inMyAreas) " · באזור שלך" else "", bold = true,
                        color = ContextCompat.getColor(this, R.color.sos_red_dark)))
                    c.instructions.forEach { list.addView(text("• $it")) }
                }
            }
        }
        s.officialNote?.let { list.addView(text(it, size = 12f)) }

        list.addView(text(getString(R.string.national_shelters), bold = true, size = 17f))
        if (!hadLocation) list.addView(text(getString(R.string.national_no_location)))
        for (sh in s.shelters) {
            val line = listOfNotNull(sh.name, sh.address, sh.distanceM?.let { RideText.distance(it) }).joinToString(" · ")
            list.addView(text(line))
            sh.source?.let { list.addView(text("מקור: $it", size = 12f)) }
            if (sh.lat != null && sh.lon != null) list.addView(com.google.android.material.button.MaterialButton(this, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
                text = getString(R.string.alert_navigate)
                setOnClickListener { runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("geo:${sh.lat},${sh.lon}?q=${sh.lat},${sh.lon}"))) } }
            })
        }
        s.sheltersNote?.let { list.addView(text(it)) }
        s.sheltersCoverage?.let { list.addView(text(it, size = 12f)) }

        if (s.numbers.isNotEmpty()) {
            list.addView(text(getString(R.string.national_numbers), bold = true, size = 17f))
            for ((name, num) in s.numbers) list.addView(com.google.android.material.button.MaterialButton(this, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
                text = "$name · $num"
                // חיוג ידני בלבד (ACTION_DIAL - בלי הרשאת חיוג)
                setOnClickListener { runCatching { startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$num"))) } }
            })
        }
    }

    companion object { fun intent(ctx: Context) = Intent(ctx, NationalActivity::class.java) }
}
