package org.lifeguard.app.service

import android.content.Context
import org.json.JSONObject
import org.lifeguard.app.LG
import org.lifeguard.app.UiEvent
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.EscortView
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.IncidentView
import org.lifeguard.app.api.RideRequest
import org.lifeguard.app.core.RideQueue
import java.io.IOException

/**
 * ליווי הביתה ונסיעה בטוחה.
 * - ליווי רגיל: מיקום כל 60 שנ'. נסיעה (kind=ride): מיקום כל 15 שנ' עם accuracy_m ו-speed_mps.
 * - בלי קליטה (נסיעה): מיקומים ו-checkin נשמרים בתור מוצפן (RideQueue) ונשלחים לפי הסדר כשהקליטה חוזרת.
 *   SOS / "צריך/ה עזרה" שלא מגיעים לשרת -> SOS רגיל (outbox + SMS ישיר לאנשי הקשר).
 * בצד המכשיר: אין קשר לשרת 5 דקות בזמן ליווי -> "הכל בסדר?" (WellnessMonitor.onConnectivity).
 */
object EscortManager {
    const val LOCATION_INTERVAL_SECONDS = 60.0
    const val RIDE_LOCATION_INTERVAL_SECONDS = 15.0
    private var lastSent = 0.0
    private var lastFixSent = -1.0

    /** תצוגת השרת האחרונה (לממשק הנסיעה) */
    @Volatile var lastView: EscortView? = null
        private set

    val activeId: String? get() = LG.store.escortId
    val isRide get() = LG.store.escortKind == "ride"
    val isSilent get() = LG.store.escortMode == "silent"

    private val queueLock = Any()
    private fun queue() = RideQueue.parse(LG.store.rideQueue)
    private fun save(q: RideQueue) { LG.store.rideQueue = if (q.size == 0) null else q.toJson() }
    val queuedCount: Int get() = synchronized(queueLock) { queue().size }

    /** קריאת רשת - מ-thread רקע. ride != null -> נסיעה בטוחה. */
    fun start(minutes: Int, destination: String, mode: String = "loud", ride: RideRequest? = null): EscortView {
        val v = LG.api().startEscort(minutes, destination, mode, LG.lastFix, ride)
        LG.connectivity.onSuccess(LG.nowSec())
        LG.store.escortId = v.id
        LG.store.escortDeadline = v.deadline
        LG.store.escortKind = if (ride != null || v.isRide) "ride" else null
        LG.store.escortMode = v.mode
        lastView = v
        lastSent = LG.nowSec()
        LG.emit(UiEvent.StateChanged)
        return v
    }

    private fun clearLocal() {
        val id = LG.store.escortId
        LG.store.escortId = null
        LG.store.escortKind = null
        LG.store.escortMode = null
        lastView = null
        LG.escortCheck = null
        synchronized(queueLock) { val q = queue(); q.clear(id); save(q) }
        LG.emit(UiEvent.StateChanged)
    }

    /** action: arrived | cancelled */
    fun finish(action: String) {
        val id = LG.store.escortId ?: return
        try {
            LG.api().escortAction(id, action)
        } catch (e: ApiException) {
            if (e.isRetryable || e.isAuth) throw e     // 409 (כבר הסתיים) / 404 -> פשוט מנקים
        }
        clearLocal()
    }

    fun extend(minutes: Int): EscortView {
        val id = LG.store.escortId ?: throw IllegalStateException("no escort")
        val v = LG.api().escortAction(id, "extend", minutes)
        LG.store.escortDeadline = v.deadline
        lastView = v
        LG.emit(UiEvent.StateChanged)
        return v
    }

    /** רענון תצוגת הנסיעה מהשרת (thread רקע). */
    fun refresh(): EscortView? {
        val id = LG.store.escortId ?: return null
        return try {
            val v = LG.api().getEscort(id)
            LG.connectivity.onSuccess(LG.nowSec())
            if (!v.isActive) { clearLocal(); return v }
            lastView = v
            LG.store.escortDeadline = v.deadline
            LG.emit(UiEvent.StateChanged)
            v
        } catch (e: ApiException) {
            if (e.status == 404) clearLocal()
            null
        } catch (e: IOException) {
            LG.connectivity.onFailure(LG.nowSec()); null
        }
    }

    /**
     * תשובה ל"הכל בסדר?" של הנסיעה (thread רקע).
     * @return sent | queued (ok בלי קליטה) | sos_fallback (help בלי קליטה -> SOS רגיל עם SMS) | closed | no_escort
     */
    fun checkin(ctx: Context, status: String): String {
        val id = LG.store.escortId ?: LG.escortCheck?.escortId ?: return "no_escort"
        LG.escortCheck = null
        LG.alarm.stopBeepIfAny()
        org.lifeguard.app.alert.Notifier.cancel(ctx, org.lifeguard.app.alert.Notifier.ID_ESCORT_CHECK)
        return try {
            val inc = LG.api().escortCheckin(id, status)
            LG.connectivity.onSuccess(LG.nowSec())
            if (inc != null) adoptIncident(inc, "ride_help") else refresh()
            "sent"
        } catch (e: ApiException) {
            when {
                e.status == 404 || e.status == 409 -> { clearLocal(); "closed" }
                e.isRetryable -> offlineCheckin(ctx, id, status)
                else -> throw e
            }
        } catch (e: IOException) {
            LG.connectivity.onFailure(LG.nowSec())
            offlineCheckin(ctx, id, status)
        }
    }

    private fun offlineCheckin(ctx: Context, id: String, status: String): String {
        if (status == "help") {
            IncidentManager.startSos(ctx, if (isSilent) "silent" else "loud", source = "ride_help_offline")
            return "sos_fallback"
        }
        synchronized(queueLock) { val q = queue(); q.addCheckin(id, status, LG.nowSec()); save(q) }
        return "queued"
    }

    /** SOS מתוך נסיעה (POST /escorts/{id}/sos). לא הגיע לשרת -> SOS רגיל (outbox + SMS) המקושר לנסיעה. */
    fun sos(ctx: Context, mode: String): String {
        val id = LG.store.escortId ?: run { IncidentManager.startSos(ctx, mode, source = "ride_sos"); return "sos_fallback" }
        return try {
            val inc = LG.api().escortSos(id, mode)
            LG.connectivity.onSuccess(LG.nowSec())
            if (inc != null && inc.id.isNotEmpty()) adoptIncident(inc, "ride_sos")
            else IncidentManager.startSos(ctx, mode, source = "ride_sos")
            "sent"
        } catch (e: Exception) {
            if (e is IOException) LG.connectivity.onFailure(LG.nowSec())
            IncidentManager.startSos(ctx, mode, source = "ride_sos_fallback")
            "sos_fallback"
        }
    }

    /** השרת פתח אירוע מקושר לנסיעה -> הוא האירוע הפעיל שלי (מיקום חי, צ'אט, תיעוד). */
    private fun adoptIncident(inc: IncidentView, source: String) {
        if (inc.id.isEmpty()) return
        val escortId = LG.store.escortId
        LG.store.activeIncidentId = inc.id
        LG.store.activeIncidentMode = inc.mode
        Evidence.begin(inc.id, inc.mode, JSONObject().put("source", source).put("incident_id", inc.id)
            .apply { escortId?.let { put("escort_id", it) } })
        Evidence.linkIncident(inc.id)
        Evidence.record("sos_sent", JSONObject().put("incident_id", inc.id).put("via", "escort"))
        if (inc.mode == "loud" && LG.store.localSiren) LG.alarm.startSiren()
        clearLocal()      // השרת סימן את הנסיעה escalated
    }

    /** מהשירות כל שנייה. */
    fun tick() {
        val id = LG.store.escortId ?: return
        val now = LG.nowSec()
        val interval = if (isRide) RIDE_LOCATION_INTERVAL_SECONDS else LOCATION_INTERVAL_SECONDS
        if (now - lastSent < interval) return
        lastSent = now
        flushQueue()
        val fix = LG.lastFix ?: return
        if (isRide && (fix.capturedAt ?: 0.0) == lastFixSent) return       // אין מיקום חדש
        try {
            LG.api().escortLocation(id, fix)
            lastFixSent = fix.capturedAt ?: 0.0
            LG.connectivity.onSuccess(LG.nowSec())
        } catch (e: ApiException) {
            if (e.status == 409 || e.status == 404) clearLocal()
            else if (e.isRetryable && isRide) enqueueLocation(id, fix)
        } catch (e: IOException) {
            LG.connectivity.onFailure(LG.nowSec())
            if (isRide) enqueueLocation(id, fix)
        }
    }

    private fun enqueueLocation(id: String, fix: GeoPoint) {
        val body = fix.toJson().apply { fix.speedMps?.let { put("speed_mps", it) } }
        lastFixSent = fix.capturedAt ?: 0.0
        synchronized(queueLock) { val q = queue(); q.addLocation(id, body, LG.nowSec()); save(q) }
    }

    private fun flushQueue() {
        synchronized(queueLock) {
            val q = queue()
            if (q.size == 0) return
            val r = q.flush { op ->
                when (op.kind) {
                    RideQueue.Op.LOCATION -> {
                        val g = GeoPoint.parse(op.body) ?: return@flush
                        LG.api().escortLocation(op.escortId, g.copy(speedMps = op.body.optDouble("speed_mps").takeIf { !it.isNaN() }))
                    }
                    RideQueue.Op.CHECKIN -> { LG.api().escortCheckin(op.escortId, op.body.getString("status")) }
                }
            }
            save(q)
            if (r == RideQueue.Outcome.SENT) LG.connectivity.onSuccess(LG.nowSec())
        }
    }

    /** בהפעלה: סנכרון מהשרת (מקור האמת). */
    fun sync() {
        try {
            val mine = LG.api().myEscorts().firstOrNull { it.isActive }
            LG.store.escortId = mine?.id
            if (mine != null) {
                LG.store.escortDeadline = mine.deadline
                LG.store.escortKind = if (mine.isRide) "ride" else null
                LG.store.escortMode = mine.mode
            }
            lastView = mine
            LG.emit(UiEvent.StateChanged)
        } catch (_: Exception) {
        }
    }
}
