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
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import org.lifeguard.app.BuildConfig
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.core.EvidenceExport
import org.lifeguard.app.core.EvidenceMeta
import org.lifeguard.app.service.Evidence
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * תיעוד מקומי (ראיות): רשימת האירועים, צפייה ואימות השרשרת, ייצוא zip (דרך בורר הקבצים של המערכת) ומחיקה.
 * צפייה וייצוא דורשים את נעילת המסך של המכשיר, ונרשמים ביומן עצמו.
 */
class EvidenceActivity : AppCompatActivity() {
    private lateinit var list: LinearLayout
    private lateinit var gate: CredentialGate
    private var exportKey: String? = null
    private val fmt = SimpleDateFormat("dd/MM/yyyy HH:mm", Locale.US)

    private val createZip = registerForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        val key = exportKey; exportKey = null
        if (uri != null && key != null) export(key, uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        gate = CredentialGate(this)
        title = getString(R.string.evidence_title)
        val pad = dp(16)
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad) }
        setContentView(ScrollView(this).apply { setBackgroundColor(Ui.color(this@EvidenceActivity, R.color.lg_bg)); isFillViewport = true; addView(list) })
    }

    override fun onResume() { super.onResume(); render() }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun render() {
        list.removeAllViews()
        list.addView(TextView(this).apply { text = getString(R.string.evidence_explain) })
        val items = runCatching { Evidence.log.list() }.getOrDefault(emptyList())
        if (items.isEmpty()) { list.addView(TextView(this).apply { text = getString(R.string.evidence_empty); setPadding(0, dp(16), 0, 0); setTextColor(Ui.gray(this@EvidenceActivity)) }); return }
        for (m in items) list.addView(row(m))
    }

    private fun row(m: EvidenceMeta): LinearLayout {
        val box = Ui.card(this)
        val status = when (m.closedAs) {
            null -> if (m.key == Evidence.currentKey) "פעיל" else "פתוח"
            "false_alarm" -> "אזעקת שווא"
            "closed_remote" -> "נסגר בשרת"
            else -> "הסתיים"
        }
        box.addView(TextView(this).apply {
            text = "${fmt.format(Date(m.createdAtMs))} · ${if (m.mode == "silent") "שקט" else "גלוי"} · $status"
            setTypeface(typeface, Typeface.BOLD)
        })
        box.addView(TextView(this).apply {
            text = (if (m.count >= 0) "${m.count} רשומות" else "") + (m.incidentId?.let { " · $it" } ?: "")
        })
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        fun btn(label: Int, f: () -> Unit) = com.google.android.material.button.MaterialButton(this, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
            text = getString(label); setOnClickListener { f() }
        }
        buttons.addView(btn(R.string.evidence_view) { gate.confirm(getString(R.string.evidence_view), { view(m.key) }, ::toast) })
        buttons.addView(btn(R.string.evidence_export) {
            gate.confirm(getString(R.string.evidence_export), {
                exportKey = m.key
                createZip.launch("lifeguard_evidence_${SimpleDateFormat("yyyyMMdd_HHmm", Locale.US).format(Date(m.createdAtMs))}.zip")
            }, ::toast)
        })
        buttons.addView(btn(R.string.evidence_delete) { confirmDelete(m) })
        box.addView(buttons)
        return box
    }

    private fun view(key: String) {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) {
                runCatching {
                    val entries = Evidence.log.open(key, "view")
                    entries to Evidence.log.verify(key)
                }
            }
            r.onFailure { toast(getString(R.string.evidence_error, it.javaClass.simpleName)) }
            r.onSuccess { (entries, v) ->
                val t = SimpleDateFormat("HH:mm:ss", Locale.US)
                val head = if (v.ok) "✓ השרשרת תקינה (${v.count} רשומות)" else "✗ נמצאו בעיות:\n" + v.problems.joinToString("\n")
                val body = entries.joinToString("\n") { e -> "${e.seq}. ${t.format(Date(e.atMs))} ${e.type} ${e.data.take(160)}" }
                val tv = TextView(this@EvidenceActivity).apply {
                    text = "$head\n\n$body"; setTextIsSelectable(true); setPadding(dp(16), dp(8), dp(16), dp(8)); textSize = 12f
                }
                AlertDialog.Builder(this@EvidenceActivity).setTitle(R.string.evidence_view)
                    .setView(ScrollView(this@EvidenceActivity).apply { addView(tv) })
                    .setPositiveButton(android.R.string.ok, null).show()
            }
        }
    }

    private fun export(key: String, uri: Uri) {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) {
                runCatching {
                    val meta = Evidence.log.meta(key)
                    // הרשומה החתומה של השרת - רק אם יש רשת ומזהה (אירוע או client_id)
                    var note: String? = null
                    val server: JSONObject? = try {
                        when {
                            meta?.incidentId != null -> LG.api().incidentEvidence(meta.incidentId)
                            else -> LG.api().evidenceByClient(key)
                        }
                    } catch (e: ApiException) { note = "השרת החזיר ${e.status}: ${e.message}"; null }
                    catch (e: IOException) { note = "אין חיבור לשרת - הרשומה החתומה לא נכללה"; null }
                    catch (e: Exception) { note = "לא נכלל: ${e.javaClass.simpleName}"; null }
                    val entries = Evidence.log.open(key, "export", JSONObject().put("server_record", server != null))
                    val res = contentResolver.openOutputStream(uri)?.use { out ->
                        EvidenceExport.writeZip(out, Evidence.log, key, entries, server, note, BuildConfig.VERSION_NAME,
                            System.currentTimeMillis())
                    } ?: throw IOException("cannot open output")
                    Evidence.log.append(key, "export_completed", JSONObject().put("files", JSONObject(res.files))
                        .put("verify_ok", res.verify.ok))
                    res to note
                }
            }
            org.lifeguard.app.service.PrivacySync.audit("evidence_exported", if (r.isSuccess) "completed" else "failed",
                JSONObject().put("kind", "local_evidence_zip"), runCatching { Evidence.log.meta(key)?.incidentId }.getOrNull())
            r.onSuccess { (res, note) ->
                toast(getString(R.string.evidence_exported) + (if (res.verify.ok) "" else " · ${getString(R.string.evidence_verify_problems)}") +
                    (note?.let { "\n$it" } ?: ""))
                render()
            }
            r.onFailure { toast(getString(R.string.evidence_error, it.message ?: it.javaClass.simpleName)) }
        }
    }

    private fun confirmDelete(m: EvidenceMeta) {
        if (m.key == Evidence.currentKey) { toast(getString(R.string.evidence_active_no_delete)); return }
        AlertDialog.Builder(this).setMessage(R.string.evidence_delete_confirm)
            .setPositiveButton(R.string.evidence_delete) { _, _ -> Evidence.log.delete(m.key); render() }
            .setNegativeButton(android.R.string.cancel, null).show()
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()

    companion object {
        fun intent(ctx: Context) = Intent(ctx, EvidenceActivity::class.java)

        /** אחרי "אזעקת שווא": לשאול אם למחוק את התיעוד המקומי (רק באישור). */
        fun askDeleteFalseAlarm(ctx: Context, key: String?) {
            if (key == null || !runCatching { Evidence.log.exists(key) }.getOrDefault(false)) return
            AlertDialog.Builder(ctx).setTitle(R.string.false_alarm).setMessage(R.string.evidence_false_alarm_delete)
                .setPositiveButton(R.string.evidence_delete) { _, _ -> Evidence.log.delete(key) }
                .setNegativeButton(R.string.evidence_keep, null).show()
        }
    }
}
