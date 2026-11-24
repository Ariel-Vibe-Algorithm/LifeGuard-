package org.lifeguard.app.core

import org.lifeguard.app.api.AppNotification
import org.lifeguard.app.api.IncidentView
import org.lifeguard.app.api.bool
import org.lifeguard.app.api.dbl
import org.lifeguard.app.api.str

/** מה השירות צריך לעשות בעקבות התראות מהשרת. לוגיקה טהורה (נבדקת ב-JVM), בלי Android. */
sealed class Effect {
    /** אישור קבלה לשרת - לכל התראה שהתקבלה בפועל */
    data class Ack(val notificationId: Long) : Effect()
    /** צלצול: סירנה בלולאה + רטט + מסך מלא, עד תגובה או ring_stopped */
    data class StartRing(val incident: IncidentView) : Effect()
    data class StopRing(val incidentId: String, val reason: StopReason) : Effect()
    /** קריאה חדשה בלי צלצול: ring=false (מישהו כבר מטפל) או התראת מתנדב (loud=true -> התראה עם צליל רגיל) */
    data class IncidentAlert(val incident: IncidentView, val loud: Boolean) : Effect()
    data class ChatMessage(val incidentId: String, val from: String, val messageId: Long?, val quiet: Boolean) : Effect()
    /** שינוי אחר באירוע (מיקום/תגובה/נקרא...) - רענון מסכים; text להתראה אם רלוונטי */
    data class IncidentChanged(val incidentId: String, val event: String, val quiet: Boolean, val text: String?) : Effect()
    data class IncidentClosed(val incidentId: String, val reason: String?, val text: String?) : Effect()
    data class EscortOverdue(val escortId: String, val text: String, val silent: Boolean) : Effect()
    /** נסיעה בטוחה: "הכל בסדר?" מהשרת לבעל/ת הנסיעה. respondBy = זמן שרת. silent -> מסך ניטרלי בלי צליל. */
    data class EscortCheck(val escortId: String, val kind: String, val text: String, val respondBy: Double?, val silent: Boolean) : Effect()
    data class Info(val type: String, val title: String, val text: String, val quiet: Boolean) : Effect()
}

enum class StopReason { RING_STOPPED, ANSWERED, CLOSED, STALE }

/**
 * מכונת המצבים של הצלצול והטיפול בהתראות.
 * - incident_new עם ring=true -> צלצול (אלא אם ההתראה ישנה מדי, האירוע נסגר, או כבר הגבתי)
 * - incident_update {event: ring_stopped} / incident_closed / תגובה מקומית (קבלה/סירוב) -> עצירת הצלצול
 * - בעל אירוע שקט: שום צליל/רטט על הודעות (גם אם השרת שכח לסמן quiet)
 * התראות באותה אצווה מתקזזות: צלצול שנעצר כבר באותה אצווה (השלמת פער אחרי ניתוק) לא מצלצל בכלל.
 */
class RingStateMachine(private val ringMaxAgeSeconds: Double = 120.0) {
    private val ringing = LinkedHashSet<String>()
    private val responded = HashSet<String>()
    private val closed = HashSet<String>()
    private var lastSeenId = 0L

    val ringingIncidents: Set<String> @Synchronized get() = ringing.toSet()
    @Synchronized fun isRinging(incidentId: String) = incidentId in ringing

    /** אחרי התקנה/התחברות - מתחילים מהמזהה שהשרת מחזיר ב-cursor. */
    @Synchronized fun resetCursor(lastId: Long) { lastSeenId = lastId }

    /**
     * @param ownSilentIncidents אירועים שלי (כבעלים) במצב שקט
     * @param nowServer זמן שרת משוער (שניות epoch)
     * @param backlog true להתראות שעוד לא אושרו מה-cursor (כבר עברו לפני ה-lastId) - לא מסננים לפי מזהה
     */
    @Synchronized
    fun onNotifications(
        list: List<AppNotification>, myUsername: String, ownSilentIncidents: Set<String>, nowServer: Double,
        backlog: Boolean = false,
    ): List<Effect> {
        val out = ArrayList<Effect>()
        for (n in list.sortedBy { it.id }) {
            if (!backlog && n.id <= lastSeenId) continue   // כפילות (ניסיון חוזר של poll)
            if (!backlog) lastSeenId = n.id
            out += Effect.Ack(n.id)
            out += handle(n, myUsername, ownSilentIncidents, nowServer)
        }
        return coalesce(out)
    }

    /** המשתמש הגיב בטלפון (קבל/סרב/בדרך...) - הצלצול נעצר מיד בלי לחכות לשרת. */
    @Synchronized
    fun onLocalResponse(incidentId: String, action: String): List<Effect> {
        responded += incidentId
        return if (ringing.remove(incidentId)) listOf(Effect.StopRing(incidentId, StopReason.ANSWERED)) else emptyList()
    }

    /** השתקה ידנית (כפתור "השתק") - האירוע עדיין פתוח לתגובה. */
    @Synchronized
    fun silence(incidentId: String): List<Effect> =
        if (ringing.remove(incidentId)) listOf(Effect.StopRing(incidentId, StopReason.RING_STOPPED)) else emptyList()

    @Synchronized fun lastId() = lastSeenId

    private fun handle(n: AppNotification, me: String, ownSilent: Set<String>, now: Double): List<Effect> {
        val p = n.payload
        val iid = n.incidentId ?: p.str("incident_id")
        when (n.type) {
            "incident_new" -> {
                val view = IncidentView.parse(p)
                val id = view.id.ifEmpty { iid ?: return emptyList() }
                val stale = n.createdAt > 0 && now - n.createdAt > ringMaxAgeSeconds
                // מתנדב/ת תמיכה/הזעקה (my_role=helper): צלצול רק להזעקת רשויות; שיחת תמיכה - התראה רגילה עם צליל
                val ringAllowed = !view.isHelper || view.helperKind == Helpers.AUTHORITY_LIAISON
                return when {
                    id in closed || !view.isActive -> emptyList()
                    view.ring == true && ringAllowed && !stale && id !in responded -> {
                        if (ringing.add(id)) listOf(Effect.StartRing(view)) else emptyList()
                    }
                    // ring=false: מישהו אחר כבר מטפל - התראה שקטה. ring חסר: מתנדב - התראה רגילה עם צליל.
                    else -> listOf(Effect.IncidentAlert(view, loud = (view.ring == null || view.isHelper) && !stale))
                }
            }
            "incident_update" -> {
                if (iid == null) return emptyList()
                val event = p.str("event") ?: ""
                val quiet = p.bool("quiet") || iid in ownSilent
                return when (event) {
                    "ring_stopped" -> if (ringing.remove(iid)) listOf(Effect.StopRing(iid, StopReason.RING_STOPPED)) else emptyList()
                    "message" -> listOf(Effect.ChatMessage(iid, p.str("from") ?: "", p.str("message_id")?.toLongOrNull(), quiet))
                    else -> listOf(Effect.IncidentChanged(iid, event, quiet || event in QUIET_EVENTS, describe(event, p)))
                }
            }
            "incident_closed" -> {
                if (iid == null) return emptyList()
                closed += iid
                val r = ArrayList<Effect>()
                if (ringing.remove(iid)) r += Effect.StopRing(iid, StopReason.CLOSED)
                r += Effect.IncidentClosed(iid, p.str("reason"), p.str("text"))
                return r
            }
            "escort_overdue" -> return listOf(Effect.EscortOverdue(p.str("escort_id") ?: "", p.str("text") ?: "",
                p.bool("silent")))
            "escort_check" -> {
                val eid = p.str("escort_id") ?: return emptyList()
                return listOf(Effect.EscortCheck(eid, p.str("kind") ?: "", p.str("text") ?: "הכל בסדר?",
                    p.dbl("respond_by"), p.bool("silent")))
            }
            "escort_update" -> {
                // לעוקבים: חריגה בנסיעה ועדכונים חשובים - התראה רגילה; מיקום/הארכה וכו' - שקט
                val event = p.str("event") ?: ""
                val text = p.str("text") ?: return emptyList()
                return listOf(Effect.Info(n.type, p.str("owner_display") ?: "LifeGuard", text,
                    quiet = event !in LOUD_ESCORT_EVENTS))
            }
            "guardian_update" -> {
                // הזמנה / אישור / הסכמה להקלטה / ביטול - התראה רגילה (הזמנה ודורשת פעולה), השאר שקט
                val event = p.str("event")
                return listOf(Effect.Info(n.type, "LifeGuard", GuardianText.notificationText(event, p.str("from")),
                    quiet = event != "invite" && event != "revoked"))
            }
            else -> {
                val from = p.str("from_display") ?: p.str("owner_display") ?: "LifeGuard"
                val text = p.str("text") ?: TYPE_TEXT[n.type] ?: return emptyList()
                return listOf(Effect.Info(n.type, from, text, quiet = n.type in QUIET_TYPES))
            }
        }
    }

    /** צלצול שהתחיל ונעצר באותה אצווה -> התראה שקטה בלבד (בלי סירנה לשבריר שנייה). */
    private fun coalesce(effects: List<Effect>): List<Effect> {
        val stopped = effects.filterIsInstance<Effect.StopRing>().map { it.incidentId }.toSet()
        val started = effects.filterIsInstance<Effect.StartRing>().map { it.incident.id }.toSet()
        val both = started intersect stopped
        if (both.isEmpty()) return effects
        return effects.mapNotNull { e ->
            when {
                e is Effect.StartRing && e.incident.id in both -> Effect.IncidentAlert(e.incident, loud = false)
                e is Effect.StopRing && e.incidentId in both -> null
                else -> e
            }
        }
    }

    private fun describe(event: String, p: org.json.JSONObject): String? = when (event) {
        "response" -> p.str("by")?.let { by -> "$by: ${RESPONSE_HE[p.str("action")] ?: "הגיב/ה"}" }
        "delivered" -> "ההתראה התקבלה אצל איש/אשת קשר"
        "signal_lost" -> "המיקום לא מתעדכן - ייתכן שאין קליטה"
        "escalated" -> "האירוע הוסלם לאזעקה גלויה"
        "recording" -> "הקלטה חדשה מהאירוע זמינה לך"
        "confirmed" -> "בעל/ת האירוע אישר/ה שזה חירום"
        // מתנדבי תמיכה/הזעקה (server/helpers.py): טקסט ההזעקה מהשרת; הצטרפות - רק אחרי קבלה אמיתית
        "liaison" -> p.str("text")
        "helper" -> if (p.str("action") == "accept") "מתנדב/ת הצטרף/ה (${Helpers.ROLE_HE[p.str("kind")] ?: "עזרה מרחוק"})" else null
        else -> null
    }

    companion object {
        val QUIET_EVENTS = setOf("read", "location", "details", "delivered", "volunteer_notified", "signal_back", "sms")
        val LOUD_ESCORT_EVENTS = setOf("anomaly", "anomaly_cleared", "running_late", "alternative_route",
            "unconfirmed_arrival")
        val QUIET_TYPES = setOf("relationship_update", "relationship_removed", "escort_update")
        val RESPONSE_HE = mapOf("accept" to "קיבל/ה את האירוע", "on_my_way" to "בדרך", "arrived" to "הגיע/ה",
            "handled" to "מטפל/ת", "decline" to "לא זמין/ה")
        val TYPE_TEXT = mapOf("relationship_invite" to "הזמנה חדשה למעגל הביטחון",
            "readiness_check" to "בדיקת מוכנות - האפליקציה אישרה קבלה")
    }
}
