package org.lifeguard.app.ui

import android.app.DatePickerDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.api.PrivacyEvent
import org.lifeguard.app.api.PrivacyHistoryQuery
import org.lifeguard.app.core.PrivacyHistoryText
import org.lifeguard.app.core.PrivacyHistoryText.Result
import org.lifeguard.app.core.PrivacyHistoryText.RevokeKind
import org.lifeguard.app.service.PrivacySync
import java.util.Calendar

/**
 * היסטוריית פרטיות (GET /v1/me/privacy/history): רשימה כרונולוגית עם סינון (טווח תאריכים, איש קשר, סוג, טקסט חופשי),
 * פרטי אירוע, הבחנה ברורה בין הושלם / נכשל / ממתין (result_effective כשקיים), זמן שרת מול זמן מכשיר בנפרד,
 * קישור לביטול הרשאה, וייצוא הדוח החתום (באישור נעילת המסך) לקובץ JSON דרך בורר הקבצים.
 */
class PrivacyHistoryActivity : AppCompatActivity() {
    private lateinit var col: LinearLayout
    private lateinit var gate: CredentialGate
    private var events: List<PrivacyEvent>? = null
    private var error: String? = null
    private var from: Triple<Int, Int, Int>? = null
    private var to: Triple<Int, Int, Int>? = null
    private lateinit var contact: EditText
    private lateinit var query: EditText
    private lateinit var type: Spinner
    private var exportJson: String? = null

    private val createJson = registerForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        val data = exportJson; exportJson = null
        if (uri != null && data != null) save(uri, data)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.privacy_history_title)
        gate = CredentialGate(this)
        col = Ui.page(this)
        contact = EditText(this).apply { hint = "איש קשר (שם משתמש)"; inputType = InputType.TYPE_CLASS_TEXT; textDirection = android.view.View.TEXT_DIRECTION_LTR }
        query = EditText(this).apply { hint = "חיפוש חופשי"; inputType = InputType.TYPE_CLASS_TEXT }
        type = Spinner(this).apply {
            adapter = ArrayAdapter(this@PrivacyHistoryActivity, android.R.layout.simple_spinner_dropdown_item,
                PrivacyHistoryText.TYPE_FILTERS.map { it.second })
        }
        render()
        load()
    }

    private fun currentQuery(): PrivacyHistoryQuery {
        val (f, t) = PrivacyHistoryText.dayRange(from, to)
        return PrivacyHistoryQuery(from = f, to = t, contact = contact.text.toString(),
            type = PrivacyHistoryText.TYPE_FILTERS[type.selectedItemPosition.coerceAtLeast(0)].first, q = query.text.toString())
    }

    private fun load() {
        val q = currentQuery()
        events = null; error = null; render()
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().privacyHistory(q) } }
            events = r.getOrNull()?.let { PrivacyHistoryText.sorted(it) }
            error = r.exceptionOrNull()?.let { Ui.errorText(this@PrivacyHistoryActivity, it) }
            render()
        }
    }

    private fun dateLabel(d: Triple<Int, Int, Int>?) = d?.let { "%02d/%02d/%d".format(it.third, it.second, it.first) } ?: "—"

    private fun pickDate(cur: Triple<Int, Int, Int>?, set: (Triple<Int, Int, Int>?) -> Unit) {
        val c = Calendar.getInstance()
        val y = cur?.first ?: c.get(Calendar.YEAR); val m = (cur?.second ?: (c.get(Calendar.MONTH) + 1)) - 1
        val d = cur?.third ?: c.get(Calendar.DAY_OF_MONTH)
        DatePickerDialog(this, { _, yy, mm, dd -> set(Triple(yy, mm + 1, dd)); render() }, y, m, d).show()
    }

    private fun render() {
        col.removeAllViews()
        // ---- סינון ----
        col.addView(Ui.text(this, getString(R.string.filters), 16f, bold = true, topDp = 0))
        val dates = Ui.row(this)
        dates.addView(Ui.button(this, "מתאריך: ${dateLabel(from)}", outlined = true) { pickDate(from) { from = it } })
        dates.addView(Ui.button(this, "עד: ${dateLabel(to)}", outlined = true) { pickDate(to) { to = it } })
        col.addView(dates)
        for (v in listOf(contact, type, query)) { (v.parent as? LinearLayout)?.removeView(v); col.addView(v) }
        val actions = Ui.row(this)
        actions.addView(Ui.button(this, getString(R.string.apply)) { load() })
        actions.addView(Ui.button(this, getString(R.string.clear), outlined = true) {
            from = null; to = null; contact.setText(""); query.setText(""); type.setSelection(0); load()
        })
        actions.addView(Ui.button(this, getString(R.string.export_signed), outlined = true) { export() })
        col.addView(actions)
        col.addView(Ui.text(this, "זמן שרת = מתי השרת רשם (מאומת). זמן מכשיר = שעון הטלפון בזמן הפעולה (לא מאומת). " +
            "\"נמסר לאפליקציה\" = הנתון נשלח לאפליקציה של הצד השני - אין לנו דרך לדעת אם מישהו הסתכל.", 12f, color = Ui.gray(this)))

        // ---- רשימה ----
        val list = events
        when {
            error != null -> col.addView(Ui.text(this, error!!, color = Ui.red(this), topDp = 12))
            list == null -> col.addView(Ui.text(this, "טוען…", topDp = 12))
            list.isEmpty() -> col.addView(Ui.text(this, "אין אירועים בטווח שנבחר", topDp = 12))
            else -> list.forEach { col.addView(eventRow(it)) }
        }
    }

    private fun resultColor(r: Result) = when (r) {
        Result.COMPLETED -> Ui.green(this); Result.FAILED -> Ui.red(this); Result.PENDING -> Ui.amber(this)
    }

    private fun resultMark(r: Result) = when (r) { Result.COMPLETED -> "✓"; Result.FAILED -> "✗"; Result.PENDING -> "⏳" }

    private fun eventRow(e: PrivacyEvent): LinearLayout {
        val me = LG.store.username
        val r = PrivacyHistoryText.result(e)
        val card = Ui.card(this, if (r == Result.FAILED) R.color.card_alert else R.color.card_neutral)
        card.addView(Ui.text(this, "${resultMark(r)} ${PrivacyHistoryText.resultHe(r)}", 12f, bold = true, color = resultColor(r), topDp = 0))
        card.addView(Ui.text(this, PrivacyHistoryText.describe(e, me), 14f))
        PrivacyHistoryText.timeLines(e).forEach { card.addView(Ui.text(this, it, 11f, color = Ui.gray(this), topDp = 0)) }
        card.isClickable = true
        card.setOnClickListener { details(e) }
        return card
    }

    private fun details(e: PrivacyEvent) {
        val me = LG.store.username
        val tv = TextView(this).apply {
            text = PrivacyHistoryText.detailLines(e, me).joinToString("\n")
            setPadding(Ui.dp(this@PrivacyHistoryActivity, 20), Ui.dp(this@PrivacyHistoryActivity, 12), Ui.dp(this@PrivacyHistoryActivity, 20), 0)
            setTextIsSelectable(true)
        }
        val b = AlertDialog.Builder(this).setTitle(PrivacyHistoryText.resultHe(PrivacyHistoryText.result(e))).setView(tv)
            .setNegativeButton(android.R.string.ok, null)
        PrivacyHistoryText.revokeFor(e, me)?.let { rv ->
            b.setPositiveButton(rv.label) { _, _ ->
                startActivity(when (rv.kind) {
                    RevokeKind.LOCATION, RevokeKind.RECORDING_ACCESS -> LocationSharingActivity.intent(this, rv.contact)
                    RevokeKind.GUARDIAN -> GuardianActivity.intent(this)
                    RevokeKind.RECORDING_SETTING -> Intent(this, SettingsActivity::class.java)
                })
            }
        }
        b.show()
    }

    /** ייצוא: אישור נעילת מסך -> GET /me/privacy/export -> שמירה כפי שהתקבל (חתום Ed25519) דרך בורר הקבצים */
    private fun export() {
        gate.confirm(getString(R.string.export_signed), {
            lifecycleScope.launch {
                val r = withContext(Dispatchers.IO) { runCatching { LG.api().privacyExportRaw() } }
                r.onSuccess {
                    exportJson = it         // הטקסט המקורי מהשרת - בלי לשנות בית
                    createJson.launch("lifeguard-privacy-${System.currentTimeMillis() / 1000}.json")
                }.onFailure { toast(Ui.errorText(this@PrivacyHistoryActivity, it)) }
            }
        }, { toast(it) })
    }

    private fun save(uri: Uri, data: String) {
        lifecycleScope.launch {
            val ok = withContext(Dispatchers.IO) {
                runCatching { contentResolver.openOutputStream(uri)!!.use { it.write(data.toByteArray(Charsets.UTF_8)) } }.isSuccess
            }
            PrivacySync.audit("evidence_exported", if (ok) "completed" else "failed",
                JSONObject().put("kind", "privacy_report").put("bytes", data.length))
            toast(if (ok) "הדוח החתום נשמר" else "השמירה נכשלה")
        }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()

    companion object {
        fun intent(ctx: Context) = Intent(ctx, PrivacyHistoryActivity::class.java)
    }
}
