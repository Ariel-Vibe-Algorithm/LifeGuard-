package org.lifeguard.app.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.chip.Chip
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.UiEvent
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.ChatMessage
import org.lifeguard.app.api.IncidentView
import org.lifeguard.app.core.Geo
import org.lifeguard.app.core.Helpers
import org.lifeguard.app.core.QuickReplies
import org.lifeguard.app.databinding.ActivityChatBinding
import org.lifeguard.app.databinding.ItemMessageBinding
import org.lifeguard.app.service.Evidence
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** צ'אט אירוע - לבעל/ת האירוע ולמגיבים. הודעות מהירות בלחיצה אחת, אישורי קריאה. */
class ChatActivity : AppCompatActivity() {
    private lateinit var b: ActivityChatBinding
    private lateinit var incidentId: String
    private var view: IncidentView? = null
    private var lastMarkedRead = 0L
    private val adapter = MessagesAdapter()
    private var chipsFor: Map<String, String>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        incidentId = intent.getStringExtra(EXTRA_ID) ?: run { finish(); return }
        b = ActivityChatBinding.inflate(layoutInflater)
        setContentView(b.root)
        title = getString(R.string.chat_title)
        b.list.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = true }
        b.list.adapter = adapter
        b.sendBtn.setOnClickListener {
            val t = b.input.text.toString().trim()
            if (t.isNotEmpty()) send(text = t)
        }
        b.onMyWayBtn.setOnClickListener { respond("on_my_way") }
        b.arrivedBtn.setOnClickListener { respond("arrived") }
        b.navBtn.setOnClickListener { navigate() }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch {
                    LG.events.collect { e ->
                        if ((e is UiEvent.IncidentUpdated && e.incidentId == incidentId) ||
                            (e is UiEvent.IncidentClosed && e.incidentId == incidentId)) load()
                    }
                }
                launch { while (true) { load(); delay(15_000) } }   // גיבוי אם התראה פוספסה
            }
        }
    }

    override fun onResume() {
        super.onResume()
        LG.visibleChatIncident = incidentId
        Notifier.cancel(this, Notifier.chatNotifId(incidentId))
    }

    override fun onPause() {
        if (LG.visibleChatIncident == incidentId) LG.visibleChatIncident = null
        super.onPause()
    }

    private fun load() {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().getIncident(incidentId) } }
            r.onSuccess { render(it); markRead(it) }
            r.onFailure { e ->
                if (e is ApiException && e.status == 404) { b.header.text = getString(R.string.alert_closed); setInputEnabled(false) }
            }
        }
    }

    private fun render(v: IncidentView) {
        view = v
        val isOwner = v.myRole == "owner"
        b.header.text = when {
            !v.isActive -> getString(R.string.alert_closed)
            // בקשת שיחה (לא חירום): סטטוס רק לפי השרת (support_ui.py support_status)
            isOwner && v.isSupport -> Helpers.supportStatus(v).first
            isOwner -> {
                val handling = v.responders.filter { it.state == "accepted" }.joinToString(", ") { it.displayName }
                val base = if (handling.isNotEmpty()) "מטפלים: $handling" else "ממתין לתגובה מאנשי הקשר…"
                val helpers = Helpers.summary(v.helpers).first
                if (helpers.isNotEmpty()) "$base\n$helpers" else base
            }
            // מתנדב/ת תמיכה: owner_display מהשרת הוא כללי ("מישהו צריך לדבר") - בלי שם; הזעקה - שם אחרי קבלה
            v.isHelper -> "${Helpers.title(v)} · ${v.helperKindLabel ?: ""}" +
                (if (Helpers.isLiaison(v) && Helpers.isAccepted(v)) "\nשם: ${v.ownerDisplay}" else "") +
                (if (!Helpers.chatAllowed(v)) "\nהצ'אט נפתח אחרי שתקבל/י את הבקשה" else "")
            else -> "${v.ownerDisplay} · ${Geo.emergencyTypeHe(v.emergencyType)}" +
                (MainActivity.distanceKm(v)?.let { " · " + Geo.formatDistanceHe(it) } ?: "") +
                (if (v.isSilent) "\n" + getString(R.string.alert_silent_mode) else "")
        }
        // "בדרך"/"הגעתי" וניווט - רק למגיבים שמגיעים פיזית (לא למתנדבי תמיכה/הזעקה מרחוק)
        b.responderActions.visibility = if (!isOwner && !v.isHelper && v.isActive) View.VISIBLE else View.GONE
        // מתנדב/ת תמיכה לא רואה מי הפונה - גם לא את התפקיד "במצוקה" (השרת כבר מחליף את השם ב"הפונה")
        adapter.hideOwnerRole = v.isHelper && !Helpers.isLiaison(v)
        adapter.submit(v.messages, LG.store.username)
        if (adapter.itemCount > 0) b.list.scrollToPosition(adapter.itemCount - 1)
        // הודעות מהירות של אירוע חירום ("בואו מהר", "אני בדרך") לא מתאימות לשיחת תמיכה / למתנדב/ת מרחוק
        val quick = if (v.isHelper || v.isSupport) emptyMap() else QuickReplies.resolve(v.quickReplies, v.myRole)
        if (quick != chipsFor) {
            chipsFor = quick
            b.chips.removeAllViews()
            for ((code, text) in quick) {
                b.chips.addView(Chip(this).apply {
                    this.text = QuickReplies.chipLabel(code, text)
                    contentDescription = text
                    setOnClickListener { send(quick = code) }
                })
            }
        }
        setInputEnabled(v.isActive && (!v.isHelper || Helpers.chatAllowed(v)))
    }

    private fun setInputEnabled(on: Boolean) {
        b.sendBtn.isEnabled = on; b.input.isEnabled = on
        for (i in 0 until b.chips.childCount) b.chips.getChildAt(i).isEnabled = on
    }

    private fun markRead(v: IncidentView) {
        val me = LG.store.username
        val maxOther = v.messages.filter { it.sender != me }.maxOfOrNull { it.id } ?: return
        if (maxOther <= lastMarkedRead) return
        lastMarkedRead = maxOther
        lifecycleScope.launch(Dispatchers.IO) { runCatching { LG.api().markRead(incidentId, maxOther) } }
    }

    private fun send(text: String? = null, quick: String? = null) {
        b.sendBtn.isEnabled = false
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) {
                runCatching {
                    LG.api().sendMessage(incidentId, text = text, quick = quick)
                    // הודעה מהירה "בדרך" של מגיב/ה מעדכנת גם את הסטטוס באירוע
                    val action = quick?.let { QuickReplies.respondActionFor(it) }
                    if (action != null && view?.myRole != "owner") runCatching { LG.api().respond(incidentId, action) }
                }
            }
            b.sendBtn.isEnabled = true
            r.onSuccess {
                if (text != null) b.input.setText("")
                // הודעה שלי באירוע שלי -> יומן הראיות
                Evidence.recordForIncident(incidentId, "chat_sent", org.json.JSONObject().apply {
                    if (text != null) put("text", text) else put("quick", quick)
                })
                load()
            }
            r.onFailure { e -> toast(if (e is IOException) getString(R.string.network_error) else e.message ?: "") }
        }
    }

    private fun respond(action: String) {
        lifecycleScope.launch {
            val r = withContext(Dispatchers.IO) { runCatching { LG.api().respond(incidentId, action) } }
            r.onSuccess { toast(getString(if (action == "arrived") R.string.action_arrived else R.string.action_on_my_way)); load() }
            r.onFailure { e -> toast(if (e is IOException) getString(R.string.network_error) else e.message ?: "") }
        }
    }

    private fun navigate() {
        val loc = view?.location ?: return
        runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("geo:${loc.lat},${loc.lon}?q=${loc.lat},${loc.lon}"))) }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    private class MessagesAdapter : RecyclerView.Adapter<MessagesAdapter.VH>() {
        private var items: List<ChatMessage> = emptyList()
        private var me: String? = null
        var hideOwnerRole = false
        private val fmt = SimpleDateFormat("HH:mm", Locale.US)

        fun submit(list: List<ChatMessage>, myUsername: String?) {
            if (list == items && me == myUsername) return
            items = list; me = myUsername
            @Suppress("NotifyDataSetChanged") notifyDataSetChanged()
        }

        class VH(val b: ItemMessageBinding) : RecyclerView.ViewHolder(b.root)

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            VH(ItemMessageBinding.inflate(LayoutInflater.from(parent.context), parent, false))

        override fun getItemCount() = items.size

        override fun onBindViewHolder(h: VH, pos: Int) {
            val m = items[pos]
            val mine = m.sender == me
            val ctx = h.b.root.context
            // בועה מעוגלת: שלי בכחול (טקסט on_accent), של אחרים על משטח עם קו מתאר
            h.b.bubbleBody.setBackgroundResource(if (mine) R.drawable.bg_bubble_me else R.drawable.bg_bubble_other)
            val fg = ContextCompat.getColor(ctx, if (mine) R.color.lg_on_accent else R.color.lg_label)
            val fg2 = ContextCompat.getColor(ctx, if (mine) R.color.lg_on_accent else R.color.lg_label_secondary)
            h.b.text.setTextColor(fg); h.b.meta.setTextColor(fg2); h.b.readBy.setTextColor(fg2)
            // הבועה לא רחבה מ-~3/4 מהמסך, כמו בהודעות
            h.b.text.maxWidth = (ctx.resources.displayMetrics.widthPixels * 0.72f).toInt()
            // ב-RTL: start = ימין. ההודעות שלי בצד אחד והאחרות בצד השני
            h.b.bubble.gravity = if (mine) Gravity.START else Gravity.END
            val role = if (hideOwnerRole && m.role == "owner") "" else Geo.roleHe(m.role)
            h.b.meta.text = (if (mine) "אני" else m.senderDisplay) + (if (role.isNotEmpty()) " · $role" else "") +
                " · " + fmt.format(Date((m.createdAt * 1000).toLong()))
            h.b.text.text = m.text
            if (mine && m.readBy.isNotEmpty()) {
                h.b.readBy.visibility = View.VISIBLE
                h.b.readBy.text = ctx.getString(R.string.chat_read_by, m.readBy.joinToString(", "))
            } else {
                h.b.readBy.visibility = View.GONE
            }
        }
    }

    companion object {
        const val EXTRA_ID = "incident_id"
        fun intent(ctx: Context, incidentId: String): Intent =
            Intent(ctx, ChatActivity::class.java).putExtra(EXTRA_ID, incidentId)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
    }
}
