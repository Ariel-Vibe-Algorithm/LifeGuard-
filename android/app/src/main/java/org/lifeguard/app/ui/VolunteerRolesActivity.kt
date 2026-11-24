package org.lifeguard.app.ui

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.api.VolunteerRoles
import org.lifeguard.app.core.Helpers

/**
 * "איך תרצה/י לעזור? (לא חייבים להיות מד"א)" - תפקידי מתנדב/ת ושפות (features_ui.py VolunteerTrustSection._build_roles).
 * ההגדרה נשמרת רק בשרת (PUT /v1/me/volunteer/roles). ליד כל תפקיד - המצב בפועל מהשרת ("✓ פעיל" / למה עוד לא).
 * עד שההגדרה נטענה מהשרת אי אפשר לשנות (לא מציגים מצב מקומי כאילו נשמר).
 */
class VolunteerRolesActivity : AppCompatActivity() {
    private val roleBoxes = LinkedHashMap<String, CheckBox>()
    private val roleStatus = HashMap<String, TextView>()
    private val langChips = LinkedHashMap<String, Chip>()
    private lateinit var msg: TextView
    private lateinit var availability: TextView
    private var applying = false
    private var loaded = false
    private lateinit var identity: IdentitySection

    override fun onDestroy() {
        if (isFinishing && ::identity.isInitialized) identity.cleanup()     // קבצי צילום זמניים
        super.onDestroy()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.volunteer_roles_title)
        val col = Ui.page(this)
        // אימות זהות - חובה לכל מתנדב/ת (גם מי שכבר רשום/ה); עד האימות לא נשלחות קריאות
        identity = IdentitySection(this)
        col.addView(identity.view)
        identity.load()
        col.addView(Ui.text(this, "איך תרצה/י לעזור? (לא חייבים להיות מד\"א)", 18f, bold = true, topDp = 16))
        for (row in Helpers.ROLE_ROWS) {
            val line = Ui.row(this).apply { layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT).apply { topMargin = Ui.dp(this@VolunteerRolesActivity, 12) } }
            val cb = CheckBox(this).apply {
                text = row.label; textSize = 16f; isChecked = row.role == "on_scene"; isEnabled = false
                setOnCheckedChangeListener { _, _ -> if (!applying) save() }
            }
            val st = Ui.text(this, "", 13f, color = Ui.gray(this), topDp = 0)
            line.addView(cb, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT))
            line.addView(st, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = Ui.dp(this@VolunteerRolesActivity, 8) })
            col.addView(line)
            col.addView(Ui.text(this, row.sub, 13f, color = Ui.gray(this), topDp = 0))
            roleBoxes[row.role] = cb; roleStatus[row.role] = st
        }
        col.addView(Ui.text(this, "שפות לשיחה:", 15f, bold = true, topDp = 16))
        val group = ChipGroup(this)
        for ((code, label) in Helpers.LANG_ROWS) {
            val chip = Chip(this).apply {
                text = label; isCheckable = true; isChecked = code == "he"; isEnabled = false
                setOnCheckedChangeListener { _, _ -> if (!applying) save() }
            }
            group.addView(chip); langChips[code] = chip
        }
        col.addView(group)
        msg = Ui.text(this, "⏳ טוען מהשרת...", 14f, topDp = 12)
        col.addView(msg)
        availability = Ui.text(this, "", 13f, color = Ui.gray(this), topDp = 12)
        col.addView(availability)
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) {
                runCatching { LG.api().volunteerRoles() to runCatching { LG.api().meSetup() }.getOrNull() }
            }
            r.onSuccess { (roles, setup) ->
                apply(roles)
                if (msg.text.startsWith("⏳")) msg.text = ""      // הודעת שגיאה/אזהרה קודמת נשארת
                // התפקידים קובעים איך לעזור; הזמינות (מתנדב/ת פעיל/ה בכלל) היא הגדרה נפרדת בשרת
                availability.text = when (setup?.volunteerAvailable) {
                    true -> "זמינות כמתנדב/ת: פעילה"
                    false, null -> "⚠️ זמינות כמתנדב/ת כבויה בשרת - לא יישלחו אליך פניות עד שתופעל (כרגע מהאפליקציה " +
                        "למחשב / מהאתר; אין עדיין מתג זמינות באפליקציה הזו)"
                }
            }
            r.onFailure { e ->
                msg.text = "❌ " + Ui.errorText(this@VolunteerRolesActivity, e) + " - ההגדרה נשמרת רק בשרת, אי אפשר לשנות כרגע"
                msg.setTextColor(Ui.red(this@VolunteerRolesActivity))
            }
        }
    }

    private fun apply(r: VolunteerRoles) {
        applying = true
        loaded = true
        if (r.roles.isNotEmpty()) for ((role, cb) in roleBoxes) cb.isChecked = role in r.roles
        for ((code, chip) in langChips) chip.isChecked = code in r.languages
        for ((role, st) in roleStatus) {
            val state = r.effective[role]
            st.text = Helpers.roleStatusText(state)
            st.setTextColor(if (state == "active") Ui.green(this) else Ui.gray(this))
        }
        roleBoxes.values.forEach { it.isEnabled = true }
        langChips.values.forEach { it.isEnabled = true }
        applying = false
    }

    private fun save() {
        if (!loaded) return
        val roles = roleBoxes.filter { it.value.isChecked }.keys.toList()
        if (roles.isEmpty()) {
            msg.text = "יש לבחור לפחות דרך אחת לעזור"
            msg.setTextColor(Ui.red(this))
            load()
            return
        }
        val langs = langChips.filter { it.value.isChecked }.keys.toList().ifEmpty { listOf("he") }
        roleBoxes.values.forEach { it.isEnabled = false }
        langChips.values.forEach { it.isEnabled = false }
        msg.text = "⏳ שומר בשרת..."
        msg.setTextColor(Ui.gray(this))
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().setVolunteerRoles(roles, langs) } }
            r.onSuccess {
                apply(it)
                msg.text = "✅ נשמר בשרת"
                msg.setTextColor(Ui.green(this@VolunteerRolesActivity))
            }
            r.onFailure { e ->
                msg.text = "❌ " + Ui.errorText(this@VolunteerRolesActivity, e) + " (לא נשמר)"
                msg.setTextColor(Ui.red(this@VolunteerRolesActivity))
                load()                         // חוזרים למצב שבשרת
            }
        }
    }

    companion object {
        fun intent(ctx: Context): Intent = Intent(ctx, VolunteerRolesActivity::class.java)
    }
}
