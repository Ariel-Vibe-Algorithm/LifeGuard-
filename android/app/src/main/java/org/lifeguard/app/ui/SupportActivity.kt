package org.lifeguard.app.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.UiEvent
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.IncidentView
import org.lifeguard.app.core.Helpers
import org.lifeguard.app.core.Tone

/**
 * "צריך/ה לדבר עם מישהו?" - לא חירום (support_ui.py; server/helpers.py create_support_request, activation='support').
 * - המתנדב/ת לא רואה שם ולא מיקום. אין אנשי קשר, אין מתנדבים בשטח, אין הקלטות, לא נשלח מיקום.
 * - קווי הסיוע הרשמיים מוצגים תמיד - גם בלי שרת ובלי אינטרנט (עותק מקומי, Helpers.HOTLINES).
 * - בקשה פתוחה אחת: השרת מחזיר את הקיימת (resumed) - חוזרים אליה.
 * - סטטוס רק לפי השרת: "מתנדב/ת הצטרף/ה" רק אחרי קבלה אמיתית.
 */
class SupportActivity : AppCompatActivity() {
    private var view: IncidentView? = null
    private lateinit var note: EditText
    private lateinit var startBtn: Button
    private lateinit var status: TextView
    private lateinit var chatBtn: Button
    private lateinit var endBtn: Button
    private var busy = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = "💬 מישהו לדבר איתו"
        build()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch {
                    LG.events.collect { e ->
                        val id = LG.store.supportIncidentId
                        if (id != null && ((e is UiEvent.IncidentUpdated && e.incidentId == id) ||
                                (e is UiEvent.IncidentClosed && e.incidentId == id))) refresh()
                    }
                }
                // גיבוי לעדכוני push (כמו ב-Windows: כל 8 שניות כשהבקשה פתוחה)
                launch { while (true) { delay(8_000); if (view?.isActive == true) refresh() } }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun build() {
        val col = Ui.page(this)
        col.addView(Ui.text(this, "לא חירום · אנונימי למתנדב/ת · אפשר לסגור בכל רגע", 14f, color = Ui.gray(this), topDp = 0))

        val c1 = Ui.card(this)
        c1.addView(Ui.text(this, "מה עובר עליך? (לא חובה - אפשר גם להתחיל בלי לכתוב)", 15f, topDp = 0))
        note = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            minLines = 2; maxLines = 5
        }
        c1.addView(note)
        startBtn = Ui.button(this, "🔎 חפש/י מתנדב/ת לשיחה") { start() }
        c1.addView(startBtn)
        status = Ui.text(this, "", 14f)
        c1.addView(status)
        chatBtn = Ui.button(this, "💬 פתיחת הצ'אט") { view?.let { startActivity(ChatActivity.intent(this, it.id)) } }
        endBtn = Ui.button(this, "סיום השיחה", outlined = true) { end() }
        c1.addView(chatBtn); c1.addView(endBtn)
        chatBtn.visibility = View.GONE; endBtn.visibility = View.GONE
        col.addView(c1)

        val c2 = Ui.card(this)
        c2.addView(Ui.text(this, "קווי סיוע (זמינים תמיד, גם בלי האפליקציה)", 16f, bold = true, topDp = 0))
        for (h in Helpers.HOTLINES) {
            c2.addView(Ui.text(this, Helpers.hotlineLine(h), 14f, topDp = 8))
            val row = Ui.row(this)
            for (n in h.dialNumbers) row.addView(Ui.button(this, "📞 $n", outlined = true) { dial(n) })
            h.url?.let { u -> row.addView(Ui.button(this, "🌐 " + u.removePrefix("https://"), outlined = true) { open(u) }) }
            if (row.childCount > 0) c2.addView(row)
        }
        c2.addView(Ui.text(this, Helpers.HOTLINES_NOTE, 12f, color = Ui.gray(this), topDp = 8))
        c2.addView(Ui.text(this, Helpers.NOT_EMERGENCY_NOTE, 14f, color = Ui.red(this), topDp = 8))
        col.addView(c2)

        if (!LG.store.isLoggedIn) {
            startBtn.isEnabled = false
            setStatus("לא מחובר לשרת LifeGuard - השיחה עם מתנדבים לא זמינה כרגע. קווי הסיוע למטה זמינים תמיד.", Tone.WARN)
        }
    }

    /** בקשה פתוחה שנשמרה במכשיר -> מצבה מהשרת */
    private fun refresh() {
        val id = LG.store.supportIncidentId ?: return
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().getIncident(id) } }
            r.onSuccess { apply(it) }
            r.onFailure { e ->
                if (e is ApiException && e.status == 404) { LG.store.supportIncidentId = null; view = null; renderIdle() }
                else if (view == null) setStatus(Ui.errorText(this@SupportActivity, e) +
                    " - קווי הסיוע למטה זמינים תמיד.", Tone.WARN)
            }
        }
    }

    private fun start() {
        if (busy) return
        busy = true
        startBtn.isEnabled = false
        setStatus("⏳ שולח בקשה לשרת...", Tone.MUTED)
        val text = note.text.toString().trim()
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().createSupportRequest(text, "he") } }
            busy = false
            r.onSuccess { v ->
                apply(v)
                if (v.resumed) {
                    // השרת מחזיר את הבקשה הפתוחה הקיימת (ההערה החדשה לא נשמרה) - חוזרים אליה
                    status.text = "חזרת לבקשה הפתוחה שלך.\n" + status.text
                }
            }
            r.onFailure { e ->
                startBtn.isEnabled = true
                setStatus("❌ " + Ui.errorText(this@SupportActivity, e) + "\nקווי הסיוע למטה זמינים תמיד.", Tone.ERROR)
            }
        }
    }

    private fun apply(v: IncidentView) {
        view = v
        val (text, tone) = Helpers.supportStatus(v)
        setStatus(text, tone)
        if (v.isActive) {
            LG.store.supportIncidentId = v.id
            startBtn.isEnabled = false
            note.isEnabled = false
            chatBtn.visibility = View.VISIBLE
            endBtn.visibility = View.VISIBLE
        } else {
            if (LG.store.supportIncidentId == v.id) LG.store.supportIncidentId = null
            renderIdle(keepStatus = true)
        }
    }

    private fun renderIdle(keepStatus: Boolean = false) {
        startBtn.isEnabled = LG.store.isLoggedIn && !busy
        note.isEnabled = true
        chatBtn.visibility = View.GONE
        endBtn.visibility = View.GONE
        if (!keepStatus) setStatus("", Tone.MUTED)
    }

    private fun end() {
        val v = view ?: return
        endBtn.isEnabled = false
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().setIncidentStatus(v.id, "resolved") } }
            endBtn.isEnabled = true
            r.onSuccess { apply(it) }
            r.onFailure { e -> setStatus("❌ " + Ui.errorText(this@SupportActivity, e), Tone.ERROR) }
        }
    }

    private fun setStatus(text: String, tone: Tone) {
        status.text = text
        status.setTextColor(when (tone) {
            Tone.OK -> Ui.green(this)
            Tone.ERROR -> Ui.red(this)
            Tone.WARN -> Ui.amber(this)
            Tone.MUTED -> Ui.gray(this)
        })
    }

    /** חייגן בלבד (ACTION_DIAL) - המשתמש/ת לוחץ/ת "חייג"; לא מתקשרים אוטומטית */
    private fun dial(number: String) = open("tel:$number")

    private fun open(uri: String) {
        try {
            startActivity(Intent(if (uri.startsWith("tel:")) Intent.ACTION_DIAL else Intent.ACTION_VIEW, Uri.parse(uri)))
        } catch (_: ActivityNotFoundException) {
            setStatus("אין במכשיר אפליקציה שיכולה לפתוח את זה: $uri", Tone.WARN)
        }
    }

    companion object {
        fun intent(ctx: Context): Intent = Intent(ctx, SupportActivity::class.java)
    }
}
