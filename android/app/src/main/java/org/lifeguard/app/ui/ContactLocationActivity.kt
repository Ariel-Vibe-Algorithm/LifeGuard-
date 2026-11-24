package org.lifeguard.app.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.ContactLocation
import org.lifeguard.app.api.arr
import org.lifeguard.app.api.objects
import org.lifeguard.app.api.str
import org.lifeguard.app.core.ContactLocationText
import java.util.Locale

/**
 * מיקום חי של אנשי קשר שמשתפים איתי "תמיד" (GET /v1/contacts/{owner}/location). מיקום ישן מסומן במפורש "לא חי".
 * אין בשרת רשימה של מי משתף איתי "תמיד" - מוצגים כל מי שאני איש הקשר שלו/ה, ו-403 מוצג כ"לא משתף/ת איתך מיקום רציף".
 * כל טעינה נרשמת אצל בעל/ת המיקום כ"נמסר לאפליקציה שלך".
 */
class ContactLocationActivity : AppCompatActivity() {
    private lateinit var col: LinearLayout
    private var owners: List<Pair<String, String>>? = null
    private var error: String? = null
    private var selected: Pair<String, String>? = null
    private var loc: ContactLocation? = null
    private var locError: String? = null
    private var fetchedAt = 0.0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.contacts_location_title)
        col = Ui.page(this)
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().circle() } }
            // השרת מסמן מי משתף איתי מיקום חי ("תמיד", לא מושהה); שרת ישן בלי השדה - מציגים את כולם
            owners = r.getOrNull()?.arr("i_am_contact_for")?.objects()?.filter {
                !it.has("shares_location_live") || it.optBoolean("shares_location_live")
            }?.mapNotNull {
                val u = it.str("username") ?: return@mapNotNull null
                u to (it.str("display_name") ?: u)
            }
            error = r.exceptionOrNull()?.let { Ui.errorText(this@ContactLocationActivity, it) }
            render()
        }
        // רענון כל דקה בזמן שהמסך פתוח (לא ברקע)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) { delay(60_000); if (selected != null) fetch() else render() }
            }
        }
        render()
    }

    private fun fetch() {
        val (u, _) = selected ?: return
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().contactLocation(u) } }
            loc = r.getOrNull()
            fetchedAt = LG.nowSec()
            locError = r.exceptionOrNull()?.let {
                if (it is ApiException && it.status == 403) "${selected?.second} לא משתף/ת איתך מיקום רציף כרגע (מצב \"תמיד\" כבוי או מושהה)"
                else Ui.errorText(this@ContactLocationActivity, it)
            }
            render()
        }
    }

    private fun render() {
        col.removeAllViews()
        col.addView(Ui.text(this, "מיקום רציף מוצג רק ממי שבחר/ה לשתף איתך \"תמיד\". מיקום שלא התעדכן 5 דקות מסומן כישן.", 12f,
            color = Ui.gray(this)))
        val list = owners
        if (list == null) {
            col.addView(Ui.text(this, error ?: "טוען…", color = if (error != null) Ui.red(this) else null))
            return
        }
        if (list.isEmpty()) col.addView(Ui.text(this, "אף אחד לא הוסיף אותך כאיש קשר", topDp = 12))
        for (o in list) col.addView(Ui.button(this, o.second, outlined = o != selected) {
            selected = o; loc = null; locError = null; render(); fetch()
        })
        val s = selected ?: return
        val card = Ui.card(this)
        col.addView(card)
        locError?.let { card.addView(Ui.text(this, it, color = Ui.red(this))); return }
        val l = loc ?: run { card.addView(Ui.text(this, "טוען…")); return }
        val v = ContactLocationText.describe(l, s.second, fetchedAt, LG.nowSec())
        Ui.setCardTone(card, alert = !v.live)
        card.addView(Ui.text(this, v.headline, 16f, bold = true, color = if (v.live) Ui.green(this) else Ui.red(this)))
        v.lines.forEach { card.addView(Ui.text(this, it, 13f)) }
        if (l.available && l.lat != null && l.lon != null) {
            card.addView(Ui.button(this, getString(R.string.alert_navigate), outlined = true) {
                val q = String.format(Locale.US, "%.6f,%.6f", l.lat, l.lon)
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("geo:$q?q=$q"))) }.onFailure {
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://www.openstreetmap.org/?mlat=${l.lat}&mlon=${l.lon}#map=17/${l.lat}/${l.lon}"))) }
                }
            })
        }
        card.addView(Ui.button(this, getString(R.string.refresh), outlined = true) { fetch() })
    }

    companion object {
        fun intent(ctx: Context) = Intent(ctx, ContactLocationActivity::class.java)
    }
}
