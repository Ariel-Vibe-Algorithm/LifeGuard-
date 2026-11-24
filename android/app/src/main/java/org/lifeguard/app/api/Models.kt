package org.lifeguard.app.api

import org.json.JSONArray
import org.json.JSONObject

// ---------- עזרי JSON: אחידים גם ב-Android וגם ב-org.json של ה-JVM (התנהגות optString שונה ביניהם ל-null) ----------
fun JSONObject.str(key: String): String? = if (!has(key) || isNull(key)) null else get(key).toString()
fun JSONObject.dbl(key: String): Double? = if (!has(key) || isNull(key)) null else when (val v = get(key)) {
    is Number -> v.toDouble()
    is String -> v.toDoubleOrNull()
    else -> null
}
fun JSONObject.lng(key: String): Long? = dbl(key)?.toLong()
/** השרת מחזיר בוליאני לפעמים כ-true/false ולפעמים כ-0/1 (שורות SQLite). */
fun JSONObject.bool(key: String): Boolean = if (!has(key) || isNull(key)) false else when (val v = get(key)) {
    is Boolean -> v
    is Number -> v.toInt() != 0
    is String -> v == "true" || v == "1"
    else -> false
}
fun JSONObject.obj(key: String): JSONObject? = if (!has(key) || isNull(key)) null else optJSONObject(key)
fun JSONObject.arr(key: String): JSONArray? = if (!has(key) || isNull(key)) null else optJSONArray(key)
fun JSONArray.objects(): List<JSONObject> = (0 until length()).mapNotNull { optJSONObject(it) }
fun JSONArray.strings(): List<String> = (0 until length()).mapNotNull { if (isNull(it)) null else get(it).toString() }

data class LoginResult(val token: String, val username: String, val displayName: String)

/** תשובת POST /trusted/notify: למי השרת שלח (אנשי קשר מאושרים) + מזהה ההודעה לכל אחד (לבדיקת אישור קבלה). */
data class TrustedNotifyResult(val sentTo: List<String>, val notificationIds: Map<String, Long>)

data class GeoPoint(
    val lat: Double,
    val lon: Double,
    val accuracyM: Double? = null,
    val isPrecise: Boolean = false,
    val source: String = "android",
    /** שניות epoch (שעון המכשיר) */
    val capturedAt: Double? = null,
    /** מהירות GPS (מ'/שנ') אם ידועה. לא נכלל ב-toJson (נשלח רק במיקום ליווי/נסיעה - speed_mps). */
    val speedMps: Double? = null,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("lat", lat); put("lon", lon)
        accuracyM?.let { put("accuracy_m", it) }
        put("is_precise", isPrecise)
        put("source", source)
        capturedAt?.let { put("captured_at", it) }
    }

    companion object {
        fun parse(o: JSONObject?): GeoPoint? {
            if (o == null) return null
            val lat = o.dbl("lat") ?: return null
            val lon = o.dbl("lon") ?: return null
            return GeoPoint(lat, lon, o.dbl("accuracy_m"), o.bool("is_precise"), o.str("source") ?: "",
                o.dbl("captured_at") ?: o.dbl("received_at") ?: o.dbl("updated_at"))
        }
    }
}

data class AppNotification(
    val id: Long,
    val type: String,
    val incidentId: String?,
    val payload: JSONObject,
    /** זמן שרת (שניות epoch) */
    val createdAt: Double,
) {
    companion object {
        fun parse(o: JSONObject): AppNotification = AppNotification(
            id = o.lng("id") ?: 0L,
            type = o.str("type") ?: "",
            incidentId = o.str("incident_id") ?: o.obj("payload")?.str("incident_id"),
            payload = when (val p = o.opt("payload")) {
                is JSONObject -> p
                is String -> runCatching { JSONObject(p) }.getOrDefault(JSONObject())
                else -> JSONObject()
            },
            createdAt = o.dbl("created_at") ?: 0.0,
        )
    }
}

data class CursorResult(val lastId: Long, val unreceived: List<AppNotification>)

data class ChatMessage(
    val id: Long,
    val sender: String,
    val senderDisplay: String,
    /** owner | trusted | volunteer */
    val role: String,
    val text: String,
    val quick: String?,
    val createdAt: Double,
    val readBy: List<String>,
) {
    companion object {
        fun parse(o: JSONObject) = ChatMessage(
            id = o.lng("id") ?: 0L,
            sender = o.str("sender") ?: "",
            senderDisplay = o.str("sender_display") ?: o.str("sender") ?: "",
            role = o.str("role") ?: "volunteer",
            text = o.str("text") ?: "",
            quick = o.str("quick"),
            createdAt = o.dbl("created_at") ?: 0.0,
            readBy = o.arr("read_by")?.strings() ?: emptyList(),
        )
    }
}

data class Responder(val displayName: String, val role: String, val state: String, val response: String?)

/** בעל/ת האירוע: מתנדב/ת תמיכה / הזעקה (server/helpers.py _helpers_for_owner). state: notified | accepted | done */
data class HelperInfo(
    val username: String,
    val displayName: String,
    /** remote_support | authority_liaison */
    val kind: String,
    val kindLabel: String,
    val state: String,
    val lastAction: String?,
    val lastActionLabel: String?,
) {
    companion object {
        fun parse(o: JSONObject) = HelperInfo(
            username = o.str("username") ?: "",
            displayName = o.str("display_name") ?: o.str("username") ?: "",
            kind = o.str("kind") ?: "",
            kindLabel = o.str("kind_label") ?: org.lifeguard.app.core.Helpers.ROLE_HE[o.str("kind")] ?: "",
            state = o.str("state") ?: "",
            lastAction = o.str("last_action"),
            lastActionLabel = o.str("last_action_label"),
        )
    }
}

/** GET/PUT /v1/me/volunteer/roles. effective: תפקיד -> "active" או סיבה בעברית למה עוד לא פעיל. */
data class VolunteerRoles(
    val roles: List<String>,
    val languages: List<String>,
    val effective: Map<String, String>,
    val labels: Map<String, String>,
) {
    companion object {
        fun parse(o: JSONObject): VolunteerRoles {
            fun map(k: String): Map<String, String> = LinkedHashMap<String, String>().apply {
                o.obj(k)?.let { m -> m.keys().forEach { key -> m.str(key)?.let { put(key, it) } } }
            }
            return VolunteerRoles(
                roles = o.arr("roles")?.strings() ?: emptyList(),
                languages = o.arr("languages")?.strings()?.ifEmpty { null } ?: listOf("he"),
                effective = map("effective"),
                labels = map("labels"),
            )
        }
    }
}

/**
 * GET/POST/DELETE /v1/me/volunteer/identity (server/volunteer_identity.py). אימות זהות חובה כדי לקבל קריאות.
 * status: none | pending | verified | rejected. note - רק בדחייה. idLast4 - 4 ספרות אחרונות בלבד (המספר המלא לא נשמר).
 */
data class VolunteerIdentity(
    val status: String,
    val statusHe: String,
    val required: Boolean,
    val canReceiveCalls: Boolean,
    val idLast4: String? = null,
    val submittedAt: Double? = null,
    val decidedAt: Double? = null,
    val note: String? = null,
) {
    companion object {
        val STATUS_HE = mapOf("none" to "לא הועלה", "pending" to "ממתין לבדיקה", "verified" to "מאומת/ת", "rejected" to "נדחה")

        fun parse(o: JSONObject): VolunteerIdentity {
            val st = o.str("status")?.takeIf { it in STATUS_HE } ?: "none"
            return VolunteerIdentity(
                status = st,
                statusHe = o.str("status_he") ?: STATUS_HE.getValue(st),
                // שרת ישן בלי השדה -> נדרש (כמו ID_REQUIRED); קריאות רק כשהשרת אומר במפורש שמותר
                required = if (o.has("required") && !o.isNull("required")) o.bool("required") else true,
                canReceiveCalls = o.bool("can_receive_calls"),
                idLast4 = o.str("id_last4"),
                submittedAt = o.dbl("submitted_at"),
                decidedAt = o.dbl("decided_at"),
                note = o.str("note"),
            )
        }
    }
}

data class IncidentView(
    val id: String,
    val mode: String,
    val emergencyType: String,
    val severity: String,
    val status: String,
    val owner: String?,
    val ownerDisplay: String,
    /** owner | trusted | volunteer | null */
    val myRole: String?,
    val myState: String?,
    val fullAccess: Boolean,
    val location: GeoPoint?,
    val distanceKm: Double?,
    val etaMinutes: Int?,
    val messages: List<ChatMessage>,
    val quickReplies: Map<String, String>,
    val signalLostSince: Double?,
    val riskBandLabel: String?,
    val responders: List<Responder>,
    /** רק בהתראת incident_new: true = מצלצלים אליי עכשיו, false = עדכון שקט, null = לא צוין (מתנדב) */
    val ring: Boolean?,
    val createdAt: Double,
    /** manual | unconfirmed | confirmed_high | confirmed_by_user | server_timer; null = שרת ישן */
    val activation: String? = null,
    /** הבעלים כיבה/תה את המיקום לאיש קשר זה (מקבל/ת התראה בלבד) */
    val locationWithheld: Boolean = false,
    /** המיקום האחרון ישן (>5 דק') - לא להציג כחי */
    val locationStale: Boolean = false,
    // ---- מתנדבים שלא חייבים להיות מד"א (server/helpers.py) ----
    /** my_role == "helper": remote_support | authority_liaison */
    val helperKind: String? = null,
    val helperKindLabel: String? = null,
    /** בקשת שיחה שאינה חירום (activation=support) - בתצוגת המתנדב/ת */
    val supportRequest: Boolean = false,
    val guidance: List<String> = emptyList(),
    val hotlines: List<org.lifeguard.app.core.Hotline> = emptyList(),
    val hotlinesNote: String? = null,
    /** רק למתנדב/ת הזעקה שקיבל/ה: קוד -> תווית */
    val liaisonActions: Map<String, String> = emptyMap(),
    /** בעל/ת האירוע: מתנדבי תמיכה/הזעקה (notified | accepted | done) */
    val helpers: List<HelperInfo> = emptyList(),
    /** create_support_request החזיר בקשה פתוחה קיימת */
    val resumed: Boolean = false,
    /** בעלים: סוגי העזרה שהתבקשו באירוע (בשרת, כולל בקשה אוטומטית). null = שרת ישן בלי השדה */
    val helperRequests: List<String>? = null,
    /** מתנדב/ת הזעקה: הפעולה האחרונה שדווחה (גם אחרי טעינה מחדש) */
    val lastAction: String? = null,
    val lastActionLabel: String? = null,
) {
    val isHelper get() = myRole == "helper"
    /** בקשת שיחה (לא חירום) - מצד הבעלים (activation) או המתנדב/ת (support_request) */
    val isSupport get() = activation == "support" || supportRequest

    val isActive get() = status == "active"
    val isSilent get() = mode == "silent"

    companion object {
        fun parse(o: JSONObject): IncidentView {
            val qr = LinkedHashMap<String, String>()
            o.obj("quick_replies")?.let { q -> q.keys().forEach { k -> q.str(k)?.let { qr[k] = it } } }
            // בעלים מקבל "recipients", מגיבים מקבלים "responders"
            val resp = (o.arr("responders") ?: o.arr("recipients"))?.objects()?.map {
                Responder(it.str("display_name") ?: it.str("username") ?: "", it.str("role") ?: "",
                    it.str("state") ?: "", it.str("response"))
            } ?: emptyList()
            return IncidentView(
                id = o.str("id") ?: "",
                mode = o.str("mode") ?: "loud",
                emergencyType = o.str("emergency_type") ?: "unknown",
                severity = o.str("severity") ?: "unknown",
                status = o.str("status") ?: "active",
                owner = o.str("owner"),
                ownerDisplay = o.str("owner_display") ?: o.str("owner") ?: "",
                myRole = o.str("my_role"),
                myState = o.str("my_state"),
                fullAccess = o.bool("full_access"),
                location = GeoPoint.parse(o.obj("location")),
                distanceKm = o.dbl("distance_km"),
                etaMinutes = o.dbl("eta_minutes")?.toInt(),
                messages = o.arr("messages")?.objects()?.map { ChatMessage.parse(it) } ?: emptyList(),
                quickReplies = qr,
                signalLostSince = o.dbl("signal_lost_since"),
                riskBandLabel = o.obj("risk")?.str("band_label"),
                responders = resp,
                ring = if (o.has("ring") && !o.isNull("ring")) o.bool("ring") else null,
                createdAt = o.dbl("created_at") ?: 0.0,
                activation = o.str("activation"),
                locationWithheld = o.bool("location_withheld"),
                locationStale = o.obj("location")?.bool("stale") ?: false,
                helperKind = o.str("helper_kind"),
                helperKindLabel = o.str("helper_kind_label"),
                supportRequest = o.bool("support_request"),
                guidance = o.arr("guidance")?.strings() ?: emptyList(),
                hotlines = o.arr("hotlines")?.objects()?.map {
                    org.lifeguard.app.core.Hotline(it.str("name") ?: "", it.str("number"), it.str("url"), it.str("note"))
                } ?: emptyList(),
                hotlinesNote = o.str("hotlines_note"),
                liaisonActions = LinkedHashMap<String, String>().apply {
                    o.obj("liaison_actions")?.let { m -> m.keys().forEach { k -> m.str(k)?.let { put(k, it) } } }
                },
                helpers = o.arr("helpers")?.objects()?.map { HelperInfo.parse(it) } ?: emptyList(),
                resumed = o.bool("resumed"),
                helperRequests = o.arr("helper_requests")?.strings(),
                lastAction = o.str("last_action"),
                lastActionLabel = o.str("last_action_label"),
            )
        }
    }
}

data class Vehicle(val plate: String? = null, val description: String? = null, val driver: String? = null) {
    val isEmpty get() = plate.isNullOrBlank() && description.isNullOrBlank() && driver.isNullOrBlank()

    /** כל שדה עד 60 תווים (חוזה השרת); שדות ריקים לא נשלחים. */
    fun toJson(): JSONObject = JSONObject().apply {
        plate?.trim()?.take(MAX)?.takeIf { it.isNotEmpty() }?.let { put("plate", it) }
        description?.trim()?.take(MAX)?.takeIf { it.isNotEmpty() }?.let { put("description", it) }
        driver?.trim()?.take(MAX)?.takeIf { it.isNotEmpty() }?.let { put("driver", it) }
    }

    companion object {
        const val MAX = 60
        fun parse(o: JSONObject?): Vehicle? = o?.let { Vehicle(it.str("plate"), it.str("description"), it.str("driver")) }
    }
}

/** פרטי נסיעה בטוחה ל-POST /v1/escorts. rideType: taxi | rideshare | bus | train | private */
data class RideRequest(val rideType: String, val destinationPoint: GeoPoint? = null, val vehicle: Vehicle? = null)

data class RideProgress(val remainingM: Double?, val etaMinutes: Double?)

/** חריגה בנסיעה/ליווי לפי השרת: deviation | stopped | late | near_destination */
data class EscortAnomaly(val kind: String, val since: Double?, val text: String, val respondBy: Double?)

data class EscortView(
    val id: String,
    val status: String,
    val mode: String,
    val destination: String,
    val deadline: Double,
    val remainingSeconds: Double,
    val overdue: Boolean,
    val signalLostSince: Double?,
    val incidentId: String?,
    /** "ride" לנסיעה בטוחה; null/"escort" לליווי רגיל */
    val kind: String? = null,
    val rideType: String? = null,
    /** תווית בעברית מהשרת (ride_label) */
    val rideLabel: String? = null,
    val vehicle: Vehicle? = null,
    val destinationPoint: GeoPoint? = null,
    /** "osrm" | "straight_line" | null */
    val routeSource: String? = null,
    val progress: RideProgress? = null,
    val anomaly: EscortAnomaly? = null,
    val watchers: List<String> = emptyList(),
) {
    val isActive get() = status == "active"
    val isRide get() = kind == "ride"
    val isSilent get() = mode == "silent"

    companion object {
        fun parse(o: JSONObject) = EscortView(
            id = o.str("id") ?: "",
            status = o.str("status") ?: "",
            mode = o.str("mode") ?: "loud",
            destination = o.str("destination") ?: "",
            deadline = o.dbl("deadline") ?: 0.0,
            remainingSeconds = o.dbl("remaining_seconds") ?: 0.0,
            overdue = o.bool("overdue"),
            signalLostSince = o.dbl("signal_lost_since"),
            incidentId = o.str("incident_id"),
            kind = o.str("kind"),
            rideType = o.str("ride_type"),
            rideLabel = o.str("ride_label"),
            vehicle = Vehicle.parse(o.obj("vehicle")),
            destinationPoint = GeoPoint.parse(o.obj("destination_point")),
            routeSource = o.str("route_source"),
            progress = o.obj("progress")?.let { RideProgress(it.dbl("remaining_m"), it.dbl("eta_minutes")) },
            anomaly = o.obj("anomaly")?.let {
                EscortAnomaly(it.str("kind") ?: "", it.dbl("since"), it.str("text") ?: "", it.dbl("respond_by"))
            },
            watchers = o.arr("watchers")?.objects()?.map { it.str("display_name") ?: it.str("username") ?: "" }
                ?: emptyList(),
        )
    }
}

/** GET /v1/me/setup - מוכנות מצד השרת (בלי פרטים מזהים של אנשי הקשר). */
data class SetupStatus(
    val contactsAccepted: Int,
    val contactsPending: Int,
    val pushDevices: Int,
    val phoneVerified: Boolean,
    val volunteerAvailable: Boolean?,
    val volunteerVerification: String?,
    val lastReadinessAt: Double?,
    val lastReadinessConfirmed: Int?,
    val lastReadinessTotal: Int?,
    val serverTime: Double?,
    /** כמה טוקני FCM רשומים לי בשרת. null = שרת בלי FCM */
    val fcmDevices: Int? = null,
    /** active | not_configured | misconfigured - האם לשרת יש credentials ל-FCM. null = שרת בלי FCM */
    val fcm: String? = null,
) {
    companion object {
        fun parse(o: JSONObject): SetupStatus {
            val v = o.obj("volunteer")
            val r = o.obj("last_readiness")
            return SetupStatus(
                contactsAccepted = o.dbl("contacts_accepted")?.toInt() ?: 0,
                contactsPending = o.dbl("contacts_pending")?.toInt() ?: 0,
                pushDevices = o.dbl("push_devices")?.toInt() ?: 0,
                phoneVerified = o.bool("phone_verified"),
                volunteerAvailable = v?.let { it.bool("available") },
                volunteerVerification = v?.str("verification"),
                lastReadinessAt = r?.dbl("created_at"),
                lastReadinessConfirmed = r?.dbl("confirmed")?.toInt(),
                lastReadinessTotal = r?.dbl("total")?.toInt(),
                serverTime = o.dbl("server_time"),
                fcmDevices = o.dbl("fcm_devices")?.toInt(),
                fcm = org.lifeguard.app.core.FcmServerStatus.normalize(o.str("fcm")),
            )
        }
    }
}

/** GET /v1/national/status - מצב התרעות ומקלטים (קריאה בלבד). */
data class NationalStatus(
    /** ok | not_configured | error */
    val alertsStatus: String,
    val alertsText: String?,
    val officialNote: String?,
    val current: NationalAlert?,
    val shelters: List<Shelter>,
    val sheltersNote: String?,
    val sheltersCoverage: String?,
    val numbers: List<Pair<String, String>>,
) {
    data class NationalAlert(val title: String, val kind: String?, val inMyAreas: Boolean, val instructions: List<String>)
    data class Shelter(val name: String, val address: String?, val distanceM: Double?, val source: String?, val lat: Double?, val lon: Double?)

    companion object {
        fun parse(o: JSONObject): NationalStatus {
            val a = o.obj("alerts") ?: JSONObject()
            val cur = a.obj("current")
            return NationalStatus(
                alertsStatus = a.str("status") ?: "error",
                alertsText = a.str("text"),
                officialNote = a.str("official_note"),
                current = cur?.let {
                    NationalAlert(it.str("title") ?: "", it.str("kind"), it.bool("in_my_areas"),
                        it.arr("instructions")?.strings() ?: emptyList())
                },
                shelters = o.arr("shelters")?.objects()?.map {
                    Shelter(it.str("name") ?: "מקלט", it.str("address"), it.dbl("distance_m"),
                        it.str("source_name") ?: it.str("source"), it.dbl("lat"), it.dbl("lon"))
                } ?: emptyList(),
                sheltersNote = o.str("shelters_note"),
                sheltersCoverage = o.str("shelters_coverage"),
                numbers = o.arr("numbers")?.objects()?.mapNotNull { n ->
                    val num = n.str("number") ?: return@mapNotNull null
                    (n.str("name") ?: num) to num
                } ?: emptyList(),
            )
        }
    }
}

/** POST /v1/me/devices/fcm -> {"ok":true,"fcm":"active|not_configured|misconfigured"} ("fcm" = מצב השרת, לא המכשיר) */
data class FcmRegisterResult(val ok: Boolean, val serverFcm: String?)
