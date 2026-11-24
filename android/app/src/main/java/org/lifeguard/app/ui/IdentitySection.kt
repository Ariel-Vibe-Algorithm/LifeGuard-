package org.lifeguard.app.ui

import android.content.ActivityNotFoundException
import android.net.Uri
import android.text.InputFilter
import android.text.InputType
import android.view.View
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.api.VolunteerIdentity
import org.lifeguard.app.core.IdentityForm
import org.lifeguard.app.core.IdentityPhoto
import org.lifeguard.app.service.IdentityPhotoAndroid
import java.io.File

/**
 * אימות זהות חובה למתנדב/ת (server/volunteer_identity.py; כמו identityBlock ב-PWA ו-IdentityUploadWindow ב-Windows):
 * סטטוס, "לא יישלחו אליך קריאות עד שהזהות תאומת", טופס (ת"ז + צילום התעודה + סלפי + הסכמה), ובקשה ממתינה -> ביטול.
 * התמונות מוקטנות ומקודדות מחדש בזיכרון (IdentityPhoto) - בלי EXIF/מיקום; צילום מהמצלמה נשמר זמנית ב-cache ונמחק מיד.
 * יש ליצור את המחלקה ב-onCreate (רישום ה-launchers של בחירה/צילום).
 */
class IdentitySection(private val a: AppCompatActivity) {
    private enum class Slot { DOC, SELFIE }

    private val photos = HashMap<Slot, ByteArray>()
    private val photoLabels = HashMap<Slot, TextView>()
    private var pendingSlot: Slot? = null
    private var captureFile: File? = null
    private var busy = false

    private val pick = a.registerForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        val slot = pendingSlot
        pendingSlot = null
        if (uri != null && slot != null) load(slot, uri, temp = null)
    }

    private val capture = a.registerForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val slot = pendingSlot
        val f = captureFile
        pendingSlot = null; captureFile = null
        if (ok && slot != null && f != null) load(slot, Uri.fromFile(f), temp = f) else f?.delete()
    }

    val view: LinearLayout = Ui.card(a)
    private val chip = TextView(a).apply {
        textSize = 14f
        setPadding(Ui.dp(a, 10), Ui.dp(a, 3), Ui.dp(a, 10), Ui.dp(a, 3))
        setBackgroundResource(R.drawable.bg_pill)
    }
    private val warning = Ui.text(a, "", 14f, color = Ui.red(a))
    private val pendingBox = LinearLayout(a).apply { orientation = LinearLayout.VERTICAL }
    private val formBox = LinearLayout(a).apply { orientation = LinearLayout.VERTICAL }
    private val idInput = TextInputEditText(a).apply {
        inputType = InputType.TYPE_CLASS_NUMBER
        filters = arrayOf(InputFilter.LengthFilter(9))
        textDirection = View.TEXT_DIRECTION_LTR
        importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO
    }
    private val consent = CheckBox(a).apply { text = IdentityForm.CONSENT_HE; textSize = 14f }
    private val msg = Ui.text(a, "", 14f).apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
    private val submit = Ui.button(a, "שליחה לאימות") { submit() }
    private val withdraw = Ui.button(a, "ביטול הבקשה ומחיקת התמונות", outlined = true) { withdraw() }

    init {
        view.addView(Ui.text(a, a.getString(R.string.identity_title), 17f, bold = true, topDp = 0))
        view.addView(Ui.row(a).apply {
            gravity = android.view.Gravity.CENTER_VERTICAL
            addView(Ui.text(a, "אימות זהות:", 15f, topDp = 0).apply {
                layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            })
            addView(chip, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { marginStart = Ui.dp(a, 8) })
            setPadding(0, Ui.dp(a, 8), 0, 0)
        })
        view.addView(warning)

        pendingBox.addView(Ui.text(a, IdentityForm.PENDING_HE, 14f, color = Ui.gray(a)))
        pendingBox.addView(withdraw)
        view.addView(pendingBox)

        formBox.addView(Ui.text(a, IdentityForm.EXPLAIN_HE, 13f, color = Ui.gray(a), topDp = 8))
        formBox.addView(TextInputLayout(a).apply {
            hint = "מספר תעודת זהות (9 ספרות)"
            addView(idInput)
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = Ui.dp(a, 8) }
        })
        photoRow(Slot.DOC, "צילום תעודת הזהות (הצד עם התמונה)")
        photoRow(Slot.SELFIE, "סלפי (פנים גלויות, בלי משקפי שמש)")
        formBox.addView(consent, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = Ui.dp(a, 8) })
        formBox.addView(submit)
        view.addView(formBox)
        view.addView(msg)
        render(null)
    }

    private fun photoRow(slot: Slot, label: String) {
        formBox.addView(Ui.text(a, label, 15f, bold = true, topDp = 12))
        val row = Ui.row(a)
        val lp = { LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f) }
        row.addView(Ui.tonalButton(a, "📷 צילום") { takePhoto(slot) }, lp())
        row.addView(Ui.button(a, "🖼 בחירה מהגלריה", outlined = true) { pickPhoto(slot) },
            lp().apply { marginStart = Ui.dp(a, 8) })
        formBox.addView(row)
        val st = Ui.text(a, "לא נבחרה תמונה", 13f, color = Ui.gray(a), topDp = 2)
        photoLabels[slot] = st
        formBox.addView(st)
    }

    /** מצב מהשרת; null = עוד לא נטען (הטופס מוסתר עד שידוע הסטטוס) */
    fun render(st: VolunteerIdentity?) {
        if (st == null) {
            chip.text = "⏳"
            warning.visibility = View.GONE; pendingBox.visibility = View.GONE; formBox.visibility = View.GONE
            return
        }
        chip.text = st.statusHe + (if (st.status == "verified" && st.idLast4 != null) " · ת\"ז ···${st.idLast4}" else "")
        chip.setTextColor(when (st.status) {
            "verified" -> Ui.green(a)
            "pending" -> Ui.amber(a)
            else -> Ui.red(a)
        })
        val warn = if (st.canReceiveCalls) null else IdentityForm.warningLine(st.status, st.note)
        warning.text = warn ?: ""
        warning.visibility = if (warn == null) View.GONE else View.VISIBLE
        pendingBox.visibility = if (st.status == "pending") View.VISIBLE else View.GONE
        withdraw.isEnabled = true
        val showForm = st.status == "none" || st.status == "rejected"
        formBox.visibility = if (showForm) View.VISIBLE else View.GONE
        if (!showForm) clearPhotos()
    }

    fun load() {
        lifecycle {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().volunteerIdentity() } }
            r.onSuccess { render(it) }
            r.onFailure { e -> setMsg("❌ " + Ui.errorText(a, e) + " - סטטוס אימות הזהות לא נטען", error = true) }
        }
    }

    private fun takePhoto(slot: Slot) {
        if (busy) return
        val dir = File(a.cacheDir, "identity").apply { mkdirs() }
        val f = File.createTempFile("cap_", ".jpg", dir)
        val uri = FileProvider.getUriForFile(a, a.packageName + ".identityphotos", f)
        pendingSlot = slot; captureFile = f
        try {
            capture.launch(uri)
        } catch (e: ActivityNotFoundException) {
            f.delete(); pendingSlot = null; captureFile = null
            setMsg("לא נמצאה אפליקציית מצלמה - אפשר לבחור תמונה מהגלריה", error = true)
        }
    }

    private fun pickPhoto(slot: Slot) {
        if (busy) return
        pendingSlot = slot
        try {
            pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
        } catch (e: ActivityNotFoundException) {
            pendingSlot = null
            setMsg("לא נמצאה גלריה במכשיר", error = true)
        }
    }

    /** מקטין ומקודד מחדש ברקע; המקור (וקובץ הצילום הזמני) לא נשמר */
    private fun load(slot: Slot, uri: Uri, temp: File?) {
        val label = photoLabels.getValue(slot)
        label.text = "⏳ מכין את התמונה..."
        label.setTextColor(Ui.gray(a))
        lifecycle {
            val r = withContext(Dispatchers.Default) {
                try { runCatching { IdentityPhotoAndroid.prepare(a, uri) } } finally { temp?.delete() }
            }
            r.onSuccess { bytes ->
                photos[slot] = bytes
                label.text = "✓ התמונה מוכנה (${bytes.size / 1024}KB, הוקטנה ונוקתה מפרטי הצילום)"
                label.setTextColor(Ui.green(a))
            }
            r.onFailure { e ->
                photos.remove(slot)
                label.text = "❌ " + (if (e is IdentityPhoto.PhotoException) e.message else "לא ניתן לקרוא את התמונה")
                label.setTextColor(Ui.red(a))
            }
        }
    }

    private fun submit() {
        if (busy) return
        val num = idInput.text?.toString()?.trim().orEmpty()
        val doc = photos[Slot.DOC]
        val selfie = photos[Slot.SELFIE]
        IdentityForm.validate(num, doc != null, selfie != null, consent.isChecked)?.let { setMsg(it, error = true); return }
        busy = true; submit.isEnabled = false
        setMsg("⏳ שולח...", error = false)
        lifecycle {
            val r = withContext(Dispatchers.IO) {
                runCatching { LG.api().submitVolunteerIdentity(num, doc!!, selfie!!, consent = true) }
            }
            busy = false; submit.isEnabled = true
            r.onSuccess {
                idInput.setText(""); consent.isChecked = false
                clearPhotos()
                render(it)
                setMsg("✅ נשלח לאימות - תקבל/י הודעה אחרי הבדיקה", error = false, ok = true)
            }
            r.onFailure { e -> setMsg("❌ " + Ui.errorText(a, e), error = true) }
        }
    }

    private fun withdraw() {
        withdraw.isEnabled = false
        lifecycle {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().withdrawVolunteerIdentity() } }
            r.onSuccess { render(it); setMsg("הבקשה בוטלה והתמונות נמחקו מהשרת", error = false, ok = true) }
            r.onFailure { e -> withdraw.isEnabled = true; setMsg("❌ " + Ui.errorText(a, e), error = true) }
        }
    }

    private fun clearPhotos() {
        photos.clear()
        photoLabels.values.forEach { it.text = "לא נבחרה תמונה"; it.setTextColor(Ui.gray(a)) }
    }

    /** מחיקת קבצי צילום זמניים שנשארו (למשל המסך נסגר באמצע) - לקרוא מ-onDestroy כשהמסך נסגר (isFinishing) */
    fun cleanup() {
        photos.clear()
        File(a.cacheDir, "identity").listFiles()?.forEach { it.delete() }
    }

    private fun setMsg(s: String, error: Boolean, ok: Boolean = false) {
        msg.text = s
        msg.setTextColor(when {
            error -> Ui.red(a)
            ok -> Ui.green(a)
            else -> Ui.gray(a)
        })
    }

    private fun lifecycle(block: suspend () -> Unit) { a.lifecycleScope.launch { block() } }
}
