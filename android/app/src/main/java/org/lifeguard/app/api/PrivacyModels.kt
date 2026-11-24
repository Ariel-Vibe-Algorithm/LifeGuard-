package org.lifeguard.app.api

import org.json.JSONObject

// מודלים לחוזה הפרטיות / הקלטות / הורה-ילד (server/privacy.py). parse סלחני: שדה חסר -> ברירת מחדל שמרנית.

/** GET /v1/me/recording-policy */
data class RecordingPolicy(
    val recordOnEmergency: Boolean,
    /** פרופיל ילד/ה עם קישור הורה פעיל + שתי הסכמות + הורה מאומת (השרת מחליט) */
    val discreet: Boolean,
    val discreetGuardians: List<String>,
    /** יש נמענים מורשים להקלטות (אחרת ההקלטה נשארת במכשיר בלבד) */
    val upload: Boolean,
    val recipients: List<String>,
    val maxUploadBytes: Long,
    val retentionDays: Int,
    val notes: List<String>,
) {
    fun toJson(): JSONObject = JSONObject().put("record_on_emergency", recordOnEmergency).put("discreet", discreet)
        .put("discreet_guardians", org.json.JSONArray(discreetGuardians)).put("upload", upload)
        .put("recipients", org.json.JSONArray(recipients)).put("max_upload_bytes", maxUploadBytes)
        .put("retention_days", retentionDays).put("notes", org.json.JSONArray(notes))

    companion object {
        const val DEFAULT_MAX_BYTES = 1_200_000L
        fun parse(o: JSONObject) = RecordingPolicy(
            recordOnEmergency = o.bool("record_on_emergency"),
            discreet = o.bool("discreet"),
            discreetGuardians = o.arr("discreet_guardians")?.strings() ?: emptyList(),
            upload = o.bool("upload"),
            recipients = o.arr("recipients")?.strings() ?: emptyList(),
            maxUploadBytes = o.lng("max_upload_bytes") ?: DEFAULT_MAX_BYTES,
            retentionDays = o.dbl("retention_days")?.toInt() ?: 30,
            notes = o.arr("notes")?.strings() ?: emptyList(),
        )
    }
}

/** POST /v1/incidents/{id}/recordings -> {"id","sha256","recipients"} או {"id","duplicate":true} */
data class RecordingUploadResult(val id: String, val sha256: String?, val recipients: List<String>, val duplicate: Boolean) {
    companion object {
        fun parse(o: JSONObject) = RecordingUploadResult(o.str("id") ?: "", o.str("sha256"),
            o.arr("recipients")?.strings() ?: emptyList(), o.bool("duplicate"))
    }
}

data class RecordingInfo(val id: String, val size: Long, val sha256: String?, val mime: String?, val startedAt: Double?,
                         val endedAt: Double?, val uploadedAt: Double?) {
    companion object {
        fun parse(o: JSONObject) = RecordingInfo(o.str("id") ?: "", o.lng("size") ?: 0, o.str("sha256"), o.str("mime"),
            o.dbl("started_at"), o.dbl("ended_at"), o.dbl("uploaded_at"))
    }
}

/** PUT /v1/me/live-location -> {"stored":bool,"reason"?: "paused"|"no_always_contacts"} */
data class LiveLocationResult(val stored: Boolean, val reason: String?) {
    companion object {
        fun parse(o: JSONObject) = LiveLocationResult(o.bool("stored"), o.str("reason"))
    }
}

/** GET /v1/contacts/{owner}/location */
data class ContactLocation(
    val available: Boolean,
    val lat: Double?,
    val lon: Double?,
    val accuracyM: Double?,
    val isPrecise: Boolean,
    /** שעון המכשיר של בעל/ת המיקום (לא מאומת) */
    val capturedAt: Double?,
    /** זמן שרת שבו המיקום התקבל */
    val receivedAt: Double?,
    val ageSeconds: Double?,
    val stale: Boolean,
    val label: String?,
    val reason: String?,
) {
    companion object {
        fun parse(o: JSONObject) = ContactLocation(
            available = o.bool("available"), lat = o.dbl("lat"), lon = o.dbl("lon"), accuracyM = o.dbl("accuracy_m"),
            isPrecise = o.bool("is_precise"), capturedAt = o.dbl("captured_at"), receivedAt = o.dbl("received_at"),
            ageSeconds = o.dbl("age_seconds"),
            // בלי השדה מהשרת -> לא מניחים שזה חי
            stale = if (o.has("stale")) o.bool("stale") else true,
            label = o.str("label"), reason = o.str("reason"),
        )
    }
}

data class PermissionChange(val serverTs: Double?, val actor: String?)

/** איש קשר במעגל שלי (אני הבעלים): מה הוא/היא מקבל/ת ממני */
data class ContactPermission(
    val username: String,
    val displayName: String,
    /** always | emergency | off */
    val locationMode: String,
    val recordingAccess: Boolean,
    /** always | emergency | off | paused */
    val effectiveNow: String,
    val lastChange: PermissionChange?,
) {
    companion object {
        fun parse(o: JSONObject) = ContactPermission(
            username = o.str("username") ?: "", displayName = o.str("display_name") ?: o.str("username") ?: "",
            locationMode = o.str("location_mode") ?: "emergency", recordingAccess = o.bool("recording_access"),
            effectiveNow = o.str("effective_now") ?: o.str("location_mode") ?: "emergency",
            lastChange = o.obj("last_change")?.let { PermissionChange(it.dbl("server_ts"), it.str("actor")) },
        )
    }
}

data class VolunteerAccess(val displayName: String, val username: String?, val incidentId: String?)

/** GET /v1/me/privacy/permissions */
data class PrivacyPermissions(
    val sharingPaused: Boolean,
    val recordOnEmergency: Boolean,
    val contacts: List<ContactPermission>,
    val whoCanSeeLocationNow: List<String>,
    val volunteersWithAccess: List<VolunteerAccess>,
    val guardianLinks: List<GuardianLink>,
    val recordingPolicy: RecordingPolicy?,
    /** captured_at / received_at של המיקום החי האחרון שלי בשרת (null = אין) */
    val myLiveCapturedAt: Double?,
    val myLiveReceivedAt: Double?,
    val hasMyLiveLocation: Boolean,
) {
    val alwaysContacts get() = contacts.count { it.locationMode == "always" }

    companion object {
        fun parse(o: JSONObject): PrivacyPermissions {
            val live = o.obj("my_live_location")
            return PrivacyPermissions(
                sharingPaused = o.bool("sharing_paused"),
                recordOnEmergency = o.bool("record_on_emergency"),
                contacts = o.arr("contacts")?.objects()?.map { ContactPermission.parse(it) } ?: emptyList(),
                whoCanSeeLocationNow = o.arr("who_can_see_location_now")?.strings() ?: emptyList(),
                volunteersWithAccess = o.arr("volunteers_with_access")?.objects()?.map {
                    VolunteerAccess(it.str("display_name") ?: it.str("username") ?: "", it.str("username"), it.str("incident_id"))
                } ?: emptyList(),
                guardianLinks = o.arr("guardian_links")?.objects()?.map { GuardianLink.parse(it) } ?: emptyList(),
                recordingPolicy = o.obj("recording_policy")?.let { RecordingPolicy.parse(it) },
                myLiveCapturedAt = live?.dbl("captured_at"),
                myLiveReceivedAt = live?.dbl("received_at"),
                hasMyLiveLocation = live != null,
            )
        }
    }
}

/** קישור הורה-ילד (GET /v1/me/guardian) */
data class GuardianLink(
    val id: String,
    val guardian: String,
    val guardianDisplay: String,
    val child: String,
    val childDisplay: String,
    /** guardian | child */
    val myRole: String,
    /** pending | active */
    val status: String,
    val childAge: Int?,
    val guardianApproved: Boolean,
    val childApproved: Boolean,
    val guardianVerified: Boolean,
    val consentGuardian: Boolean,
    val consentChild: Boolean,
    val discreetRecordingActive: Boolean,
) {
    val iAmGuardian get() = myRole == "guardian"
    val isActive get() = status == "active"
    val iApproved get() = if (iAmGuardian) guardianApproved else childApproved
    val myConsent get() = if (iAmGuardian) consentGuardian else consentChild
    val otherDisplay get() = if (iAmGuardian) childDisplay else guardianDisplay

    companion object {
        fun parse(o: JSONObject): GuardianLink {
            val rc = o.obj("recording_consent")
            return GuardianLink(
                id = o.str("id") ?: "", guardian = o.str("guardian") ?: "",
                guardianDisplay = o.str("guardian_display") ?: o.str("guardian") ?: "",
                child = o.str("child") ?: "", childDisplay = o.str("child_display") ?: o.str("child") ?: "",
                myRole = o.str("my_role") ?: "child", status = o.str("status") ?: "pending",
                childAge = o.dbl("child_age")?.toInt(),
                guardianApproved = o.bool("guardian_approved"), childApproved = o.bool("child_approved"),
                guardianVerified = o.bool("guardian_verified"),
                consentGuardian = rc?.bool("guardian") ?: false, consentChild = rc?.bool("child") ?: false,
                discreetRecordingActive = o.bool("discreet_recording_active"),
            )
        }
    }
}

data class AccessPeriod(val from: Double?, val to: Double?, val requests: Int?, val seconds: Double?)

/** אירוע ביומן הפרטיות (GET /v1/me/privacy/history) */
data class PrivacyEvent(
    val eventId: String,
    /** זמן שרת (מאומת) */
    val serverTs: Double?,
    /** שעון המכשיר (לא מאומת) - רק באירועים שנרשמו במכשיר */
    val deviceTs: Double?,
    val actor: String?,
    val actorDisplay: String?,
    val subject: String?,
    val recipient: String?,
    val recipientDisplay: String?,
    val action: String,
    val result: String,
    val resultEffective: String?,
    val via: String?,
    val permission: String?,
    val incidentId: String?,
    /** server | device */
    val source: String,
    val details: JSONObject,
    val accessPeriod: AccessPeriod?,
    val delivery: String?,
) {
    /** result_effective (מצב המסירה בפועל) גובר על result כשקיים */
    val effectiveResult get() = resultEffective ?: result

    companion object {
        fun parse(o: JSONObject) = PrivacyEvent(
            eventId = o.str("event_id") ?: "", serverTs = o.dbl("server_ts"), deviceTs = o.dbl("device_ts"),
            actor = o.str("actor"), actorDisplay = o.str("actor_display"), subject = o.str("subject"),
            recipient = o.str("recipient"), recipientDisplay = o.str("recipient_display"),
            action = o.str("action") ?: "", result = o.str("result") ?: "completed", resultEffective = o.str("result_effective"),
            via = o.str("via"), permission = o.str("permission"), incidentId = o.str("incident_id"),
            source = o.str("source") ?: "server", details = o.obj("details") ?: JSONObject(),
            accessPeriod = o.obj("access_period")?.let {
                AccessPeriod(it.dbl("from"), it.dbl("to"), it.dbl("requests")?.toInt(), it.dbl("seconds"))
            },
            delivery = o.str("delivery"),
        )
    }
}

/** פילטרים ל-GET /v1/me/privacy/history. from/to = שניות epoch לפי זמן השרת. type = קידומת של action. */
data class PrivacyHistoryQuery(
    val from: Double? = null,
    val to: Double? = null,
    val contact: String? = null,
    val type: String? = null,
    val q: String? = null,
    val limit: Int = 500,
) {
    fun toParams(): Map<String, String> = LinkedHashMap<String, String>().apply {
        from?.let { put("from", "%.3f".format(java.util.Locale.US, it)) }
        to?.let { put("to", "%.3f".format(java.util.Locale.US, it)) }
        contact?.trim()?.takeIf { it.isNotEmpty() }?.let { put("contact", it.lowercase()) }
        type?.trim()?.takeIf { it.isNotEmpty() }?.let { put("type", it) }
        q?.trim()?.takeIf { it.isNotEmpty() }?.let { put("q", it) }
        put("limit", limit.coerceIn(1, 2000).toString())
    }
}
