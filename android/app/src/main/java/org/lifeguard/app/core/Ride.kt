package org.lifeguard.app.core

import org.json.JSONArray
import org.json.JSONObject
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.api.EscortView
import org.lifeguard.app.api.GeoPoint
import java.io.IOException
import java.net.URLDecoder
import java.util.Locale
import kotlin.math.roundToInt

/** נסיעה בטוחה: סוגי נסיעה, פענוח יעד, טקסטים לתצוגה. לוגיקה טהורה. */
object RideTypes {
    /** קוד לשרת -> תווית */
    val ALL = linkedMapOf("taxi" to "מונית", "rideshare" to "נסיעה שיתופית", "bus" to "אוטובוס", "train" to "רכבת",
        "private" to "רכב פרטי")

    fun label(code: String?) = ALL[code] ?: code ?: ""
}

/**
 * יעד בלי Geocoder: הדבקת "lat,lon", קישור geo:, Google Maps (?q= / @lat,lon / ?ll=), Waze (?ll=), OpenStreetMap
 * (#map=z/lat/lon או ?mlat=&mlon=). מחזיר null אם לא נמצאו קואורדינטות תקינות.
 */
object GeoInput {
    private const val NUM = "(-?\\d{1,3}(?:\\.\\d+)?)"
    private val PAIR = Regex("$NUM\\s*,\\s*$NUM")
    private val AT = Regex("@$NUM,$NUM")
    private val OSM_HASH = Regex("map=\\d+(?:\\.\\d+)?/$NUM/$NUM")
    private val MLAT = Regex("[?&]mlat=$NUM")
    private val MLON = Regex("[?&]mlon=$NUM")

    fun parse(raw: String?): GeoPoint? {
        val s = runCatching { URLDecoder.decode((raw ?: "").trim(), "UTF-8") }.getOrDefault(raw ?: "").trim()
        if (s.isEmpty()) return null
        val candidates = ArrayList<Pair<String, String>>()
        val lower = s.lowercase(Locale.ROOT)
        if (lower.startsWith("geo:")) {
            // geo:lat,lon או geo:0,0?q=lat,lon(label)
            val q = Regex("[?&]q=([^&]*)").find(s)?.groupValues?.get(1)
            q?.let { PAIR.find(it) }?.let { candidates += it.groupValues[1] to it.groupValues[2] }
            PAIR.find(s.substring(4))?.let { candidates += it.groupValues[1] to it.groupValues[2] }
        } else {
            for (k in listOf("q", "query", "ll", "destination", "daddr", "center")) {
                Regex("[?&]$k=([^&#]*)").find(s)?.groupValues?.get(1)?.let { PAIR.find(it) }
                    ?.let { candidates += it.groupValues[1] to it.groupValues[2] }
            }
            AT.find(s)?.let { candidates += it.groupValues[1] to it.groupValues[2] }
            // סמן (mlat/mlon) קודם למרכז התצוגה (#map=)
            val ml = MLAT.find(s); val mo = MLON.find(s)
            if (ml != null && mo != null) candidates += ml.groupValues[1] to mo.groupValues[1]
            OSM_HASH.find(s)?.let { candidates += it.groupValues[1] to it.groupValues[2] }
            if (!s.contains("://")) PAIR.matchEntire(s)?.let { candidates += it.groupValues[1] to it.groupValues[2] }
        }
        for ((a, b) in candidates) {
            val lat = a.toDoubleOrNull() ?: continue
            val lon = b.toDoubleOrNull() ?: continue
            if (lat in -90.0..90.0 && lon in -180.0..180.0 && !(lat == 0.0 && lon == 0.0)) {
                return GeoPoint(lat, lon, isPrecise = true, source = "user_input")
            }
        }
        return null
    }
}

object RideText {
    fun countdown(secondsLeft: Double): String {
        val s = secondsLeft.coerceAtLeast(0.0).toLong()
        return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, (s % 3600) / 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
    }

    fun distance(m: Double): String = if (m < 1000) "${((m / 10).roundToInt() * 10)} מ'"
    else "%.1f ק\"מ".format(Locale.US, m / 1000)

    fun anomalyKind(kind: String?) = when (kind) {
        "deviation" -> "סטייה מהמסלול"
        "stopped" -> "עצירה ממושכת"
        "late" -> "איחור משמעותי"
        "near_destination" -> "קרוב/ה ליעד"
        else -> kind ?: ""
    }

    /** שורת מצב לנסיעה פעילה. nowServer = זמן שרת (שניות). */
    fun status(v: EscortView, nowServer: Double): List<String> {
        val lines = ArrayList<String>()
        val left = v.deadline - nowServer
        lines += if (left >= 0) "זמן שנותר: ${countdown(left)}" else "הזמן עבר לפני ${countdown(-left)}"
        v.progress?.let { p ->
            val parts = ArrayList<String>()
            p.remainingM?.let { parts += "נותרו ${distance(it)}" }
            p.etaMinutes?.let { parts += "הגעה משוערת בעוד ${it.roundToInt()} דק'" }
            if (parts.isNotEmpty()) lines += parts.joinToString(" · ")
        }
        when (v.routeSource) {
            "osrm" -> lines += "מסלול: לפי מפת כבישים"
            "straight_line" -> lines += "מסלול: קו ישר (מסלול כבישים לא זמין בשרת)"
        }
        lines += if (v.watchers.isEmpty()) "אין אנשי קשר שעוקבים (אין אנשי קשר מאושרים)"
        else "עוקבים: " + v.watchers.joinToString(", ")
        if (v.signalLostSince != null) lines += "השרת לא מקבל מיקום מהטלפון"
        return lines
    }
}

/**
 * תור לא-מקוון לנסיעה: עדכוני מיקום ו-checkin שלא הגיעו לשרת נשלחים לפי הסדר כשהקליטה חוזרת.
 * מיקומים: נשמרים רק האחרונים (MAX_LOCATIONS) - השרת צריך מסלול עדכני, לא כל נקודה.
 * נשמר מוצפן (SecureStore) כי הוא מכיל מיקומים.
 */
class RideQueue(items: List<Op> = emptyList()) {
    data class Op(val kind: String, val escortId: String, val body: JSONObject, val queuedAt: Double) {
        fun toJson(): JSONObject = JSONObject().put("kind", kind).put("escort_id", escortId).put("body", body)
            .put("queued_at", queuedAt)

        companion object {
            const val LOCATION = "location"
            const val CHECKIN = "checkin"
            fun parse(o: JSONObject) = Op(o.getString("kind"), o.getString("escort_id"), o.getJSONObject("body"),
                o.optDouble("queued_at", 0.0))
        }
    }

    enum class Outcome { SENT, OFFLINE, IDLE }

    private val ops = ArrayList(items)
    val items: List<Op> get() = ops.toList()
    val size get() = ops.size

    fun addLocation(escortId: String, body: JSONObject, now: Double) {
        ops += Op(Op.LOCATION, escortId, body, now)
        val locs = ops.filter { it.kind == Op.LOCATION && it.escortId == escortId }
        if (locs.size > MAX_LOCATIONS) ops.removeAll(locs.take(locs.size - MAX_LOCATIONS).toSet())
    }

    fun addCheckin(escortId: String, status: String, now: Double) {
        ops.removeAll { it.kind == Op.CHECKIN && it.escortId == escortId }     // רק התשובה האחרונה רלוונטית
        ops += Op(Op.CHECKIN, escortId, JSONObject().put("status", status), now)
    }

    fun clear(escortId: String? = null) { if (escortId == null) ops.clear() else ops.removeAll { it.escortId == escortId } }

    /**
     * שולח לפי הסדר. send זורק IOException (אין רשת -> עוצרים ושומרים), ApiException ניתנת לניסיון חוזר (עוצרים),
     * 404/409 (הנסיעה הסתיימה) -> זורקים את כל הפעולות של הנסיעה הזו, שגיאה אחרת -> זורקים את הפעולה.
     */
    fun flush(send: (Op) -> Unit): Outcome {
        if (ops.isEmpty()) return Outcome.IDLE
        while (ops.isNotEmpty()) {
            val op = ops.first()
            try {
                send(op); ops.removeAt(0)
            } catch (e: IOException) {
                return Outcome.OFFLINE
            } catch (e: ApiException) {
                when {
                    e.isAuth || e.isRetryable -> return Outcome.OFFLINE
                    e.status == 404 || e.status == 409 -> ops.removeAll { it.escortId == op.escortId }
                    else -> ops.removeAt(0)
                }
            }
        }
        return Outcome.SENT
    }

    fun toJson(): String = JSONArray().apply { ops.forEach { put(it.toJson()) } }.toString()

    companion object {
        const val MAX_LOCATIONS = 40
        fun parse(s: String?): RideQueue = RideQueue(runCatching {
            val a = JSONArray(s ?: "[]"); (0 until a.length()).map { Op.parse(a.getJSONObject(it)) }
        }.getOrDefault(emptyList()))
    }
}
