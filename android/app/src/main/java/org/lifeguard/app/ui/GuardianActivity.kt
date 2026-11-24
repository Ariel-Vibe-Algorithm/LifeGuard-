package org.lifeguard.app.ui

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.materialswitch.MaterialSwitch
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.UiEvent
import org.lifeguard.app.api.GuardianLink
import org.lifeguard.app.core.GuardianText
import org.lifeguard.app.service.PrivacySync

/**
 * פרופיל הורה-ילד: הזמנה (שם משתמש, תפקיד, גיל), אישור, הסכמה *נפרדת* להקלטה (טקסט מותאם גיל לילד/ה), סטטוס
 * (כולל "ההורה עוד לא אומת") וביטול לשני הצדדים. השרת מחליט אם הקלטה דיסקרטית פעילה.
 */
class GuardianActivity : AppCompatActivity() {
    private lateinit var col: LinearLayout
    private var links: List<GuardianLink>? = null
    private var error: String? = null
    private var busy = false
    // טופס ההזמנה נשמר בין רינדורים
    private lateinit var inviteUser: EditText
    private lateinit var inviteAge: EditText
    private lateinit var inviteRole: RadioGroup

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.guardian_title)
        col = Ui.page(this)
        inviteUser = EditText(this).apply { hint = "שם המשתמש של הצד השני"; inputType = InputType.TYPE_CLASS_TEXT; textDirection = android.view.View.TEXT_DIRECTION_LTR }
        inviteAge = EditText(this).apply { hint = "גיל הילד/ה (0-17)"; inputType = InputType.TYPE_CLASS_NUMBER }
        inviteRole = RadioGroup(this).apply {
            addView(RadioButton(this@GuardianActivity).apply { id = ID_GUARDIAN; text = "אני ההורה" })
            addView(RadioButton(this@GuardianActivity).apply { id = ID_CHILD; text = "אני הילד/ה" })
        }
        render()
        load()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                LG.events.collect { if (it == UiEvent.StateChanged && !busy) load() }   // guardian_update מהשרת
            }
        }
    }

    private fun load() {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().guardianLinks() } }
            links = r.getOrNull() ?: links
            error = r.exceptionOrNull()?.let { Ui.errorText(this@GuardianActivity, it) }
            render()
        }
    }

    private fun act(f: () -> Unit) {
        if (busy) return
        busy = true
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { f(); runCatching { PrivacySync.refresh(force = true) } } }
            busy = false
            r.exceptionOrNull()?.let { Toast.makeText(this@GuardianActivity, Ui.errorText(this@GuardianActivity, it), Toast.LENGTH_LONG).show() }
            load()
        }
    }

    private fun render() {
        col.removeAllViews()
        col.addView(Ui.text(this, "קישור הורה-ילד דורש אישור של שני הצדדים. הקלטה דיסקרטית בחירום פעילה רק אם שניכם הסכמתם " +
            "בנפרד להקלטה וההורה אומת/ה ע\"י צוות LifeGuard. אין האזנה חיה ואין הפעלה מרחוק של המיקרופון.", 12f, color = Ui.gray(this)))

        val l = links
        col.addView(Ui.header(this, "קישורים"))
        when {
            l == null -> col.addView(Ui.text(this, error ?: "טוען…", color = if (error != null) Ui.red(this) else null))
            l.isEmpty() -> col.addView(Ui.text(this, "אין קישורים"))
            else -> { error?.let { col.addView(Ui.text(this, it, color = Ui.red(this))) }; l.forEach { col.addView(linkCard(it)) } }
        }

        col.addView(Ui.header(this, "הזמנה חדשה"))
        for (v in listOf(inviteUser, inviteRole, inviteAge)) { (v.parent as? LinearLayout)?.removeView(v); col.addView(v) }
        col.addView(Ui.button(this, "שליחת הזמנה") { invite() })
    }

    private fun invite() {
        val role = when (inviteRole.checkedRadioButtonId) { ID_GUARDIAN -> "guardian"; ID_CHILD -> "child"; else -> "" }
        val (inv, err) = GuardianText.validateInvite(inviteUser.text.toString(), role, inviteAge.text.toString(), LG.store.username)
        if (inv == null) { Toast.makeText(this, err, Toast.LENGTH_LONG).show(); return }
        act {
            LG.api().guardianInvite(inv.other, inv.iAm, inv.childAge)
            runOnUiThread { inviteUser.setText(""); inviteAge.setText("") }
        }
    }

    private fun linkCard(g: GuardianLink): LinearLayout {
        val card = Ui.card(this, if (g.isActive) R.color.card_neutral else R.color.card_alert)
        card.addView(Ui.text(this, GuardianText.roleHe(g), 16f, bold = true, topDp = 0))
        for (line in GuardianText.statusLines(g)) {
            val warn = line.startsWith("ההורה עוד לא אומת") || line.startsWith("ממתין")
            card.addView(Ui.text(this, line, 13f, color = if (warn) Ui.amber(this) else null))
        }
        if (GuardianText.canApprove(g)) card.addView(Ui.button(this, "אישור הקישור") { act { LG.api().guardianApprove(g.id) } })
        if (GuardianText.canConsent(g)) {
            card.addView(MaterialSwitch(this).apply {
                text = if (g.iAmGuardian) "אני מסכים/ה להקלטה דיסקרטית בחירום" else "אני מסכים/ה שהטלפון שלי יקליט בחירום"
                isChecked = g.myConsent
                setOnCheckedChangeListener { sw, v ->
                    if (!v) { act { LG.api().guardianRecordingConsent(g.id, false) }; return@setOnCheckedChangeListener }
                    // רק אחרי קריאת הטקסט ואישור מפורש (מחזירים את המתג בלי להפעיל שוב את המאזין)
                    sw.setOnCheckedChangeListener(null); sw.isChecked = false
                    AlertDialog.Builder(this@GuardianActivity).setTitle("הסכמה להקלטה")
                        .setMessage(GuardianText.consentText(g, PrivacySync.cachedPolicy?.retentionDays ?: 30))
                        .setPositiveButton("אני מסכים/ה") { _, _ -> act { LG.api().guardianRecordingConsent(g.id, true) } }
                        .setNegativeButton("לא עכשיו") { _, _ -> render() }
                        .setOnCancelListener { render() }.show()
                }
            })
        }
        card.addView(Ui.button(this, "ביטול הקישור", outlined = true) {
            AlertDialog.Builder(this).setMessage("לבטל את הקישור עם ${g.otherDisplay}? גם ההסכמות להקלטה יבוטלו. הצד השני יקבל הודעה.")
                .setPositiveButton("ביטול הקישור") { _, _ -> act { LG.api().guardianRevoke(g.id) } }
                .setNegativeButton(android.R.string.cancel, null).show()
        })
        return card
    }

    companion object {
        private const val ID_GUARDIAN = 0x4d01
        private const val ID_CHILD = 0x4d02
        fun intent(ctx: Context) = Intent(ctx, GuardianActivity::class.java)
    }
}
