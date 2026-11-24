package org.lifeguard.app.api

import okhttp3.Dispatcher
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** שגיאה שהשרת החזיר ({"error": code, "message": טקסט בעברית}). שגיאות רשת נשארות IOException. */
class ApiException(val status: Int, val code: String, message: String) : Exception(message) {
    val isAuth get() = status == 401
    /** שווה לנסות שוב (שרת עמוס/נפל) - לעומת בקשה שגויה שלא תצליח לעולם */
    val isRetryable get() = status >= 500 || status == 429 || status == 408
}

/**
 * לקוח ה-API של השרת (/v1). כל הקריאות סינכרוניות - לקרוא רק מ-thread רקע.
 * @param baseUrl כתובת שכבר עברה UrlPolicy (בלי /v1 ובלי / בסוף)
 */
class ApiClient(
    baseUrl: String,
    private val tokenProvider: () -> String?,
    private val http: OkHttpClient = defaultHttp(),
) {
    private val base: HttpUrl = baseUrl.trimEnd('/').toHttpUrl()
    /** ל-long-poll יש Dispatcher נפרד - כדי ש-cancelPoll() (השכמת FCM) יבטל רק אותו ולא SOS / מיקום שבדרך. */
    private val pollHttp: OkHttpClient = http.newBuilder().dispatcher(Dispatcher())
        .readTimeout(POLL_WAIT_SECONDS + 20L, TimeUnit.SECONDS).build()
    /** העלאת תמונות אימות זהות (עד ~6MB) ברשת סלולרית איטית */
    private val uploadHttp: OkHttpClient by lazy {
        http.newBuilder().writeTimeout(90, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build()
    }

    /** הפרש שעון: זמן שרת פחות זמן מכשיר (שניות), לפי כותרת Date של התשובה האחרונה. */
    @Volatile var serverSkewSeconds: Double = 0.0
        private set

    fun serverNow(deviceNowMillis: Long = System.currentTimeMillis()): Double = deviceNowMillis / 1000.0 + serverSkewSeconds

    // ---------------- auth ----------------
    fun login(username: String, password: String, device: String): LoginResult {
        val o = call("POST", "auth/login", JSONObject().put("username", username).put("password", password)
            .put("device", device), auth = false)
        return LoginResult(o.str("token") ?: throw ApiException(500, "bad_response", "תשובה לא תקינה מהשרת"),
            o.str("username") ?: username, o.str("display_name") ?: username)
    }

    fun register(username: String, password: String, displayName: String): JSONObject =
        call("POST", "auth/register", JSONObject().put("username", username).put("password", password)
            .put("display_name", displayName), auth = false)

    fun logout() { call("POST", "auth/logout") }

    fun me(): JSONObject = call("GET", "me")

    /** בלי אימות. {"status":"ok"|"degraded", ...} */
    fun health(): JSONObject = call("GET", "health", auth = false)

    /** מוכנות מצד השרת: אנשי קשר, מכשירים, אימות טלפון, בדיקת מוכנות אחרונה. */
    fun meSetup(): SetupStatus = SetupStatus.parse(call("GET", "me/setup"))

    /** הרשומה החתומה של השרת על האירוע (בעלים בלבד): {record, sha256, signature, public_key, algorithm} */
    fun incidentEvidence(incidentId: String): JSONObject = call("GET", "incidents/${seg(incidentId)}/evidence")

    /** אותה רשומה לפי client_id של ה-SOS (כשהמכשיר לא קיבל את מזהה האירוע - למשל SOS שנשלח בלי קליטה). */
    fun evidenceByClient(clientId: String): JSONObject = call("GET", "evidence/by-client/${seg(clientId)}")

    // ---------------- FCM (השכמה בלבד) ----------------
    /** רישום טוקן FCM של המכשיר. התשובה: האם לשרת יש credentials (active | not_configured | misconfigured). */
    fun registerFcm(token: String, device: String): FcmRegisterResult {
        val o = call("POST", "me/devices/fcm", JSONObject().put("token", token).put("device", device.take(80)))
        return FcmRegisterResult(o.bool("ok"), org.lifeguard.app.core.FcmServerStatus.normalize(o.str("fcm")))
    }

    /** ביטול רישום (התנתקות / החלפת שרת). DELETE עם גוף JSON. */
    fun unregisterFcm(token: String) { call("DELETE", "me/devices/fcm", JSONObject().put("token", token)) }

    // ---------------- notifications ----------------
    fun cursor(): CursorResult {
        val o = call("GET", "notifications/cursor")
        return CursorResult(o.lng("last_id") ?: 0L,
            o.arr("unreceived")?.objects()?.map { AppNotification.parse(it) } ?: emptyList())
    }

    /** long-poll: חוזר מיד כשיש התראות, אחרת אחרי עד wait שניות עם רשימה ריקה. */
    fun poll(after: Long, waitSeconds: Int = POLL_WAIT_SECONDS): List<AppNotification> {
        val o = call("GET", "notifications", query = mapOf("after" to after.toString(), "wait" to waitSeconds.toString()),
            client = if (waitSeconds > 0) pollHttp else http)
        return o.arr("notifications")?.objects()?.map { AppNotification.parse(it) } ?: emptyList()
    }

    /**
     * מבטל long-poll שבדרך (למשל חיבור TCP מת אחרי Doze). ה-poll זורק IOException והלולאה מושכת מחדש מיד.
     * בקשות אחרות (SOS, מיקום, poll עם wait=0) לא מושפעות.
     */
    fun cancelPoll() = pollHttp.dispatcher.cancelAll()

    /** אישור קבלה - השרת סופר "נמסר" רק אחרי זה. */
    fun ack(notificationId: Long) { call("POST", "notifications/$notificationId/received") }

    // ---------------- incidents ----------------
    fun createIncident(
        clientId: String, mode: String, emergencyType: String = "unknown", severity: String = "high",
        location: GeoPoint? = null, risk: JSONObject? = null, escortId: String? = null,
        /** manual | wellness_no_response | sensor | acoustic | auto. null = לא נשלח (השרת: manual) */
        trigger: String? = null,
    ): IncidentView {
        val b = JSONObject().put("client_id", clientId).put("mode", mode).put("emergency_type", emergencyType)
            .put("severity", severity)
        location?.let { b.put("location", it.toJson()) }
        risk?.let { b.put("risk", it) }
        escortId?.let { b.put("escort_id", it) }
        trigger?.let { b.put("trigger", it) }
        return IncidentView.parse(call("POST", "incidents", b))
    }

    /** בעלים: מאשר/ת שאירוע "unconfirmed" הוא חירום -> confirmed_by_user + מתנדבים. */
    fun confirmIncident(incidentId: String): IncidentView =
        IncidentView.parse(call("POST", "incidents/${seg(incidentId)}/confirm"))

    // ---------------- הרשאות מיקום / פרטיות ----------------
    /** mode: always | emergency | off */
    fun setLocationMode(contact: String, mode: String): PrivacyPermissions {
        require(mode in setOf("always", "emergency", "off")) { "bad mode" }
        return PrivacyPermissions.parse(call("PUT", "relationships/${user(contact)}/location-mode", JSONObject().put("mode", mode)))
    }

    fun setRecordingAccess(contact: String, allowed: Boolean): PrivacyPermissions =
        PrivacyPermissions.parse(call("PUT", "relationships/${user(contact)}/recording-access", JSONObject().put("allowed", allowed)))

    /** השהיה מוחקת מיד את המיקום החי שלי מהשרת */
    fun pauseLocationSharing(paused: Boolean): PrivacyPermissions =
        PrivacyPermissions.parse(call("POST", "me/location-sharing/pause", JSONObject().put("paused", paused)))

    fun putLiveLocation(loc: GeoPoint): LiveLocationResult {
        val b = JSONObject().put("lat", loc.lat).put("lon", loc.lon).put("is_precise", loc.isPrecise)
        loc.accuracyM?.let { b.put("accuracy_m", it) }
        loc.capturedAt?.let { b.put("captured_at", it) }
        return LiveLocationResult.parse(call("PUT", "me/live-location", b))
    }

    /** 403 not_permitted אם הבעלים לא משתף/ת "always" איתי או השהה/תה. */
    fun contactLocation(owner: String): ContactLocation = ContactLocation.parse(call("GET", "contacts/${user(owner)}/location"))

    fun privacyPermissions(): PrivacyPermissions = PrivacyPermissions.parse(call("GET", "me/privacy/permissions"))

    fun privacyHistory(q: PrivacyHistoryQuery = PrivacyHistoryQuery()): List<PrivacyEvent> =
        call("GET", "me/privacy/history", query = q.toParams()).arr("events")?.objects()?.map { PrivacyEvent.parse(it) }
            ?: emptyList()

    /** רשומה חתומה {record, sha256, signature, public_key, algorithm} - נשמרת כפי שהיא */
    fun privacyExport(): JSONObject = call("GET", "me/privacy/export")

    /** אותה רשומה - הטקסט המקורי מהשרת, בלי סריאליזציה מחדש (כדי שאימות החתימה החיצוני לא יושפע) */
    fun privacyExportRaw(): String = callWithText("GET", "me/privacy/export").second

    /** אירועי ביקורת מהמכשיר (אידמפוטנטי לפי event_id). מחזיר כמה התקבלו. */
    fun deviceAudit(events: JSONArray): Int = call("POST", "me/audit", JSONObject().put("events", events)).lng("accepted")?.toInt() ?: 0

    // ---------------- הקלטות ----------------
    fun recordingPolicy(): RecordingPolicy = RecordingPolicy.parse(call("GET", "me/recording-policy"))

    fun setRecordOnEmergency(enabled: Boolean): RecordingPolicy =
        RecordingPolicy.parse(call("PUT", "me/recording", JSONObject().put("record_on_emergency", enabled)))

    fun uploadRecording(incidentId: String, clientId: String, data: ByteArray, sha256: String, mime: String,
                        startedAt: Double, endedAt: Double): RecordingUploadResult {
        val b = JSONObject().put("client_id", clientId).put("data_b64", java.util.Base64.getEncoder().encodeToString(data))
            .put("sha256", sha256).put("mime", mime).put("started_at", startedAt).put("ended_at", endedAt)
        return RecordingUploadResult.parse(call("POST", "incidents/${seg(incidentId)}/recordings", b))
    }

    fun listRecordings(incidentId: String): List<RecordingInfo> =
        call("GET", "incidents/${seg(incidentId)}/recordings").arr("recordings")?.objects()?.map { RecordingInfo.parse(it) }
            ?: emptyList()

    /** {"id","mime","sha256","data_b64"} - הגישה נרשמת ביומן הפרטיות של הבעלים */
    fun getRecording(incidentId: String, recordingId: String): JSONObject =
        call("GET", "incidents/${seg(incidentId)}/recordings/${seg(recordingId)}")

    fun deleteRecording(recordingId: String) { call("DELETE", "me/recordings/${seg(recordingId)}") }

    // ---------------- הורה-ילד ----------------
    fun guardianLinks(): List<GuardianLink> = call("GET", "me/guardian").arr("links")?.objects()?.map { GuardianLink.parse(it) }
        ?: emptyList()

    /** iAm: guardian | child */
    fun guardianInvite(other: String, iAm: String, childAge: Int?): GuardianLink {
        require(iAm == "guardian" || iAm == "child")
        val b = JSONObject().put("other", other).put("i_am", iAm)
        childAge?.let { b.put("child_age", it) }
        return GuardianLink.parse(call("POST", "guardian/links", b))
    }

    fun guardianApprove(linkId: String): GuardianLink = GuardianLink.parse(call("POST", "guardian/links/${seg(linkId)}/approve"))

    fun guardianRecordingConsent(linkId: String, consent: Boolean): GuardianLink =
        GuardianLink.parse(call("POST", "guardian/links/${seg(linkId)}/recording-consent", JSONObject().put("consent", consent)))

    fun guardianRevoke(linkId: String) { call("POST", "guardian/links/${seg(linkId)}/revoke") }

    fun sendIncidentLocation(incidentId: String, loc: GeoPoint) {
        call("POST", "incidents/${seg(incidentId)}/location", loc.toJson())
    }

    fun setIncidentStatus(incidentId: String, status: String): IncidentView =
        IncidentView.parse(call("PATCH", "incidents/${seg(incidentId)}", JSONObject().put("status", status)))

    fun getIncident(incidentId: String): IncidentView = IncidentView.parse(call("GET", "incidents/${seg(incidentId)}"))

    fun inbox(): List<IncidentView> = call("GET", "incidents/inbox").arr("incidents")?.objects()
        ?.map { IncidentView.parse(it) } ?: emptyList()

    /** action: accept | decline | on_my_way | arrived | handled */
    fun respond(incidentId: String, action: String): JSONObject =
        call("POST", "incidents/${seg(incidentId)}/respond", JSONObject().put("action", action))

    /** בדיוק אחד מ-text / quick. מחזיר את מזהה ההודעה. */
    fun sendMessage(incidentId: String, text: String? = null, quick: String? = null): Long {
        require((text == null) != (quick == null)) { "text xor quick" }
        val b = JSONObject()
        if (quick != null) b.put("quick", quick) else b.put("text", text)
        return call("POST", "incidents/${seg(incidentId)}/messages", b).lng("id") ?: 0L
    }

    fun markRead(incidentId: String, upToId: Long) {
        call("POST", "incidents/${seg(incidentId)}/messages/read", JSONObject().put("up_to_id", upToId))
    }

    // ---------------- מתנדבים שלא חייבים להיות מד"א (server/helpers.py) ----------------
    fun volunteerRoles(): VolunteerRoles = VolunteerRoles.parse(call("GET", "me/volunteer/roles"))

    /** roles: on_scene | remote_support | authority_liaison (לפחות אחד - אחרת 400 invalid_roles) */
    fun setVolunteerRoles(roles: List<String>, languages: List<String>): VolunteerRoles = VolunteerRoles.parse(
        call("PUT", "me/volunteer/roles", JSONObject().put("roles", JSONArray(roles))
            .put("languages", JSONArray(languages.ifEmpty { listOf("he") }))))

    // ---------------- אימות זהות מתנדב/ת (server/volunteer_identity.py) ----------------
    fun volunteerIdentity(): VolunteerIdentity = VolunteerIdentity.parse(call("GET", "me/volunteer/identity"))

    /**
     * צילום ת"ז + סלפי (JPEG שכבר הוקטן וקודד מחדש - IdentityPhoto, בלי EXIF/מיקום) + מספר ת"ז + הסכמה מפורשת.
     * שגיאות השרת: consent_required, invalid_id_number, bad_image_type, image_too_small, image_too_large, id_in_use,
     * already_verified (ApiException עם הודעה בעברית). העלאה גדולה (עד ~6MB ב-base64) - timeout ארוך יותר.
     */
    fun submitVolunteerIdentity(idNumber: String, idImage: ByteArray, selfie: ByteArray, consent: Boolean): VolunteerIdentity {
        val enc = java.util.Base64.getEncoder()
        val b = JSONObject().put("id_number", idNumber).put("id_image", enc.encodeToString(idImage))
            .put("selfie", enc.encodeToString(selfie)).put("consent", consent)
        return VolunteerIdentity.parse(call("POST", "me/volunteer/identity", b, client = uploadHttp))
    }

    /** ביטול בקשה ממתינה - התמונות נמחקות מהשרת מיד (409 nothing_pending אם אין). */
    fun withdrawVolunteerIdentity(): VolunteerIdentity = VolunteerIdentity.parse(call("DELETE", "me/volunteer/identity"))

    /** בעלים: kind = remote_support | authority_liaison. מחזיר את תצוגת הבעלים (כולל "helpers"). */
    fun requestHelper(incidentId: String, kind: String, reason: String = ""): IncidentView {
        require(kind == "remote_support" || kind == "authority_liaison") { "bad kind" }
        return IncidentView.parse(call("POST", "incidents/${seg(incidentId)}/request-helper",
            JSONObject().put("kind", kind).put("reason", reason)))
    }

    /** מתנדב/ת: accept | decline | done | called_police | called_mda | called_fire | reported_other | could_not_reach */
    fun helperRespond(incidentId: String, action: String): IncidentView =
        IncidentView.parse(call("POST", "incidents/${seg(incidentId)}/helper-respond", JSONObject().put("action", action)))

    /** בעלים: שחרור מתנדב/ת תמיכה/הזעקה מהאירוע */
    fun releaseHelper(incidentId: String, helper: String): IncidentView =
        IncidentView.parse(call("POST", "incidents/${seg(incidentId)}/helpers/${user(helper)}/release", JSONObject()))

    /** "צריך/ה לדבר עם מישהו" (לא חירום). אם כבר יש בקשה פתוחה - השרת מחזיר אותה (resumed=true). */
    fun createSupportRequest(note: String = "", language: String = "he"): IncidentView =
        IncidentView.parse(call("POST", "support-requests", JSONObject().put("note", note).put("language", language)))

    // ---------------- presence / circle ----------------
    fun putPresence(loc: GeoPoint) {
        call("PUT", "me/presence", JSONObject().put("lat", loc.lat).put("lon", loc.lon).put("is_precise", loc.isPrecise)
            .apply { loc.accuracyM?.let { put("accuracy_m", it) } }.put("source", loc.source))
    }

    fun circle(): JSONObject = call("GET", "circle")

    /**
     * הודעה לאנשי הקשר המאושרים (POST /trusted/notify, כמו backend_client.notify_trusted ב-Windows). מחזיר למי השרת שלח
     * ואת מזהי ההודעות - לבדיקת אישור קבלה אמיתי (notificationStatus), לא רק "נשלח".
     */
    fun notifyTrusted(text: String, test: Boolean = false): TrustedNotifyResult {
        val o = call("POST", "trusted/notify", JSONObject().put("text", text).put("type", if (test) "test" else "info"))
        val ids = o.obj("notification_ids")
        return TrustedNotifyResult(o.arr("sent_to")?.strings() ?: emptyList(),
            ids?.keys()?.asSequence()?.mapNotNull { k -> ids.lng(k)?.let { k to it } }?.toMap() ?: emptyMap())
    }

    /** האם אפליקציית כל נמען אישרה קבלה (POST /notifications/status) - רק להודעות שאני שלחתי. */
    fun notificationStatus(ids: Map<String, Long>): Map<String, Boolean> {
        val o = call("POST", "notifications/status", JSONObject().put("ids", JSONObject(ids)))
        return o.keys().asSequence().associateWith { o.bool(it) }
    }

    // ---------------- escorts (ליווי הביתה) ----------------
    /**
     * ליווי רגיל, או נסיעה בטוחה (ride != null): kind="ride", ride_type, destination_point, vehicle.
     */
    fun startEscort(minutes: Int, destination: String, mode: String, loc: GeoPoint?, ride: RideRequest? = null): EscortView {
        val b = JSONObject().put("minutes", minutes).put("destination", destination).put("mode", mode)
        loc?.let { b.put("location", it.toJson()) }
        if (ride != null) {
            b.put("kind", "ride").put("ride_type", ride.rideType)
            ride.destinationPoint?.let { b.put("destination_point", JSONObject().put("lat", it.lat).put("lon", it.lon)) }
            ride.vehicle?.takeIf { !it.isEmpty }?.let { b.put("vehicle", it.toJson()) }
        }
        return EscortView.parse(call("POST", "escorts", b))
    }

    /** מיקום ליווי; accuracy_m כבר בתוך GeoPoint, speed_mps נוסף כשידוע. */
    fun escortLocation(escortId: String, loc: GeoPoint) {
        val b = loc.toJson()
        loc.speedMps?.let { b.put("speed_mps", it) }
        call("POST", "escorts/${seg(escortId)}/location", b)
    }

    fun getEscort(escortId: String): EscortView = EscortView.parse(call("GET", "escorts/${seg(escortId)}"))

    /** status: ok | help. ב-help השרת פותח אירוע מקושר ומחזיר {"incident": {...}}. */
    fun escortCheckin(escortId: String, status: String): IncidentView? {
        require(status == "ok" || status == "help")
        return call("POST", "escorts/${seg(escortId)}/checkin", JSONObject().put("status", status))
            .obj("incident")?.let { IncidentView.parse(it) }
    }

    /** SOS מתוך נסיעה. mode: silent | loud. */
    fun escortSos(escortId: String, mode: String): IncidentView? =
        call("POST", "escorts/${seg(escortId)}/sos", JSONObject().put("mode", mode))
            .obj("incident")?.let { IncidentView.parse(it) }

    /** action: arrived | cancelled | extend */
    fun escortAction(escortId: String, action: String, minutes: Int? = null): EscortView =
        EscortView.parse(call("POST", "escorts/${seg(escortId)}/$action",
            JSONObject().apply { minutes?.let { put("minutes", it) } }))

    fun myEscorts(): List<EscortView> = call("GET", "escorts").arr("mine")?.objects()?.map { EscortView.parse(it) }
        ?: emptyList()

    // ---------------- מצב ארצי (קריאה בלבד) ----------------
    fun nationalStatus(lat: Double? = null, lon: Double? = null): NationalStatus = NationalStatus.parse(
        call("GET", "national/status", query = if (lat != null && lon != null)
            mapOf("lat" to "%.5f".format(java.util.Locale.US, lat), "lon" to "%.5f".format(java.util.Locale.US, lon)) else emptyMap()))

    // ---------------- HTTP ----------------
    private fun seg(s: String): String {
        require(s.isNotEmpty() && s.all { it.isLetterOrDigit() || it == '_' || it == '-' }) { "bad id" }
        return s
    }

    /** שם משתמש כקטע נתיב (כמו USERNAME_RE בשרת: אותיות/ספרות/עברית/./-, 3-32). */
    private fun user(s: String): String {
        require(USERNAME_RE.matches(s) && s.any { it != '.' }) { "bad username" }
        return s.lowercase()
    }

    private fun call(
        method: String, path: String, body: JSONObject? = null, auth: Boolean = true,
        query: Map<String, String> = emptyMap(), client: OkHttpClient = http,
    ): JSONObject = callWithText(method, path, body, auth, query, client).first

    /** כמו call, ומחזיר גם את גוף התשובה המקורי (לשמירה כפי שהוא - למשל רשומה חתומה). */
    private fun callWithText(
        method: String, path: String, body: JSONObject? = null, auth: Boolean = true,
        query: Map<String, String> = emptyMap(), client: OkHttpClient = http,
    ): Pair<JSONObject, String> {
        val url = base.newBuilder().addPathSegment("v1").addPathSegments(path).apply {
            query.forEach { (k, v) -> addQueryParameter(k, v) }
        }.build()
        val rb = when {
            body != null -> body.toString().toRequestBody(JSON)
            method == "GET" -> null
            // בלי גוף בכלל (Content-Length: 0): נתיבים בשרת שלא קוראים את הגוף משאירים אותו בחיבור keep-alive
            // והבקשה הבאה באותו חיבור נשברת (501). ראו הערה בדוח לשרת.
            else -> ByteArray(0).toRequestBody(null)
        }
        val req = Request.Builder().url(url).method(method, rb).header("Accept", "application/json")
        if (auth) {
            val t = tokenProvider() ?: throw ApiException(401, "no_token", "נדרשת התחברות")
            req.header("Authorization", "Bearer $t")
        }
        client.newCall(req.build()).execute().use { resp -> return handle(resp) }
    }

    private fun handle(resp: Response): Pair<JSONObject, String> {
        resp.headers.getDate("Date")?.let { serverSkewSeconds = (it.time - System.currentTimeMillis()) / 1000.0 }
        val text = resp.body?.string().orEmpty()
        val json = try {
            if (text.isBlank()) JSONObject() else JSONObject(text)
        } catch (e: Exception) {
            if (resp.isSuccessful) throw IOException("invalid JSON from server") else JSONObject()
        }
        if (!resp.isSuccessful) {
            throw ApiException(resp.code, json.str("error") ?: "http_${resp.code}",
                json.str("message") ?: "שגיאת שרת (${resp.code})")
        }
        return json to text
    }

    companion object {
        const val POLL_WAIT_SECONDS = 25
        private val USERNAME_RE = Regex("^[\\w\\u0590-\\u05FF.\\-]{3,32}$")
        private val JSON = "application/json; charset=utf-8".toMediaType()

        fun defaultHttp(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .writeTimeout(20, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }
}
