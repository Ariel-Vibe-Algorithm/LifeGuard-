package org.lifeguard.app.ui

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.materialswitch.MaterialSwitch
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.api.ContactPermission
import org.lifeguard.app.api.PrivacyPermissions
import org.lifeguard.app.core.ContactLocationText
import org.lifeguard.app.core.LocationModes
import org.lifeguard.app.service.LifeGuardService
import org.lifeguard.app.service.PrivacySync

/**
 * שיתוף מיקום לכל איש קשר (תמיד / רק בחירום / כבוי) + גישה להקלטות, השהיית כל השיתוף, מי מקבל/ת את המיקום עכשיו,
 * והמיקום החי האחרון ששותף. הנתונים מהשרת (GET /v1/me/privacy/permissions); בלי חיבור - "לא זמין" (חוץ מהשהיה,
 * שעובדת מקומית מיד ונשלחת כשהקליטה חוזרת).
 */
class LocationSharingActivity : AppCompatActivity() {
    private lateinit var col: LinearLayout
    private var perms: PrivacyPermissions? = null
    private var error: String? = null
    private var busy = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.location_sharing_title)
        col = Ui.page(this)
        render()
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().privacyPermissions().also { PrivacySync.savePermissions(it) } } }
            perms = r.getOrNull() ?: perms
            error = r.exceptionOrNull()?.let { Ui.errorText(this@LocationSharingActivity, it) }
            render()
        }
    }

    /** קריאת שרת שמחזירה הרשאות מעודכנות; כשל -> הודעה ורענון (מחזיר את המתג למצב האמיתי) */
    private fun change(f: () -> PrivacyPermissions?) {
        if (busy) return
        busy = true
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { f() } }
            busy = false
            r.getOrNull()?.let { perms = it; PrivacySync.savePermissions(it); error = null }
            r.exceptionOrNull()?.let { Toast.makeText(this@LocationSharingActivity, Ui.errorText(this@LocationSharingActivity, it), Toast.LENGTH_LONG).show() }
            LifeGuardService.start(this@LocationSharingActivity, kick = true)
            render()
        }
    }

    private fun render() {
        col.removeAllViews()
        intent.getStringExtra(EXTRA_CONTACT)?.let { c ->
            col.addView(Ui.text(this, "שינוי הרשאות עבור: $c", bold = true, color = Ui.amber(this)))
        }

        // ---- השהיה (עובדת גם בלי קליטה) ----
        val pauseCard = Ui.card(this, if (LG.store.sharingPaused) R.color.card_alert else R.color.card_neutral)
        val sw = MaterialSwitch(this).apply {
            text = getString(R.string.pause_all); textSize = 18f
            isChecked = LG.store.sharingPaused
            setOnCheckedChangeListener { _, v -> change { PrivacySync.setPaused(v) } }
        }
        pauseCard.addView(sw)
        pauseCard.addView(Ui.text(this, getString(R.string.pause_explain), 12f))
        if (LG.store.pendingPause != null) pauseCard.addView(Ui.text(this, getString(R.string.paused_offline), color = Ui.amber(this)))
        col.addView(pauseCard)

        val p = perms
        if (p == null) {
            col.addView(Ui.text(this, error ?: "טוען…", color = if (error != null) Ui.red(this) else null, topDp = 16))
            if (error != null) col.addView(Ui.button(this, getString(R.string.refresh), outlined = true) { load() })
            return
        }
        error?.let { col.addView(Ui.text(this, "$it (מוצגים הנתונים האחרונים שנטענו)", color = Ui.red(this))) }

        // ---- מי מקבל/ת את המיקום שלי עכשיו ----
        col.addView(Ui.header(this, "מי מקבל/ת את המיקום שלך עכשיו"))
        val now = p.whoCanSeeLocationNow
        col.addView(Ui.text(this, if (now.isEmpty()) "אף אחד לא מקבל/ת מיקום חי כרגע"
            else "מיקום חי נמסר לאפליקציה של: " + now.joinToString(", ")))
        if (p.volunteersWithAccess.isNotEmpty()) col.addView(Ui.text(this, "מתנדבים באירוע פעיל: " +
            p.volunteersWithAccess.joinToString(", ") { it.displayName }))
        col.addView(Ui.text(this, "אנשי קשר במצב \"רק בחירום\" מקבלים מיקום רק בזמן אירוע חירום פעיל.", 12f, color = Ui.gray(this)))

        // ---- המיקום החי האחרון ששותף ----
        col.addView(Ui.header(this, "המיקום החי האחרון ששותף"))
        if (!p.hasMyLiveLocation) col.addView(Ui.text(this, "אין מיקום חי שמור בשרת"))
        else {
            val rec = p.myLiveReceivedAt
            val age = rec?.let { LG.api().serverNow() - it }
            col.addView(Ui.text(this, buildString {
                append("התקבל בשרת: ").append(rec?.let { ContactLocationText.clock(it) } ?: "?")
                age?.let { append(" (").append(ContactLocationText.ageHe(it)).append(")") }
                p.myLiveCapturedAt?.let { append("\nנמדד בטלפון: ").append(ContactLocationText.clock(it)) }
                val acc = LG.store.liveLastAccuracy
                if (acc >= 0) append("\nדיוק: כ-${acc.toInt()} מ'")
                if (age != null && age > ContactLocationText.STALE_SECONDS) append("\nישן - אנשי הקשר רואים אותו מסומן \"לא חי\"")
            }))
        }

        // ---- אנשי קשר ----
        col.addView(Ui.header(this, "אנשי הקשר שלך"))
        if (p.contacts.isEmpty()) col.addView(Ui.text(this, "אין אנשי קשר במעגל (מוסיפים מהאפליקציה השולחנית / PWA)"))
        for (c in p.contacts) col.addView(contactCard(c))
    }

    private fun contactCard(c: ContactPermission): LinearLayout {
        val card = Ui.card(this)
        card.addView(Ui.text(this, c.displayName, 16f, bold = true, topDp = 0))
        card.addView(Ui.text(this, "עכשיו: " + (LocationModes.EXPLAIN_HE[c.effectiveNow] ?: c.effectiveNow), 12f, color = Ui.gray(this)))
        val group = RadioGroup(this).apply { orientation = RadioGroup.VERTICAL }
        for (m in LocationModes.ALL) {
            group.addView(RadioButton(this).apply {
                id = View_ID_BASE + LocationModes.ALL.indexOf(m)
                text = LocationModes.LABEL_HE[m]
                isChecked = c.locationMode == m
            })
        }
        group.setOnCheckedChangeListener { _, checkedId ->
            val mode = LocationModes.ALL[checkedId - View_ID_BASE]
            if (mode != c.locationMode) change { LG.api().setLocationMode(c.username, mode) }
        }
        card.addView(group)
        card.addView(MaterialSwitch(this).apply {
            text = getString(R.string.recording_access)
            isChecked = c.recordingAccess
            setOnCheckedChangeListener { _, v -> change { LG.api().setRecordingAccess(c.username, v) } }
        })
        c.lastChange?.serverTs?.let { card.addView(Ui.text(this, "שינוי אחרון: ${ContactLocationText.clock(it)} (זמן שרת)", 12f, color = Ui.gray(this))) }
        return card
    }

    companion object {
        private const val View_ID_BASE = 0x4c00
        const val EXTRA_CONTACT = "contact"
        fun intent(ctx: Context, contact: String? = null) = Intent(ctx, LocationSharingActivity::class.java)
            .apply { contact?.let { putExtra(EXTRA_CONTACT, it) } }
    }
}
