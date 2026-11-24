package org.lifeguard.app.core

import org.lifeguard.app.api.ContactLocation
import org.lifeguard.app.api.GeoPoint
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.math.roundToInt

object LocationModes {
    const val ALWAYS = "always"
    const val EMERGENCY = "emergency"
    const val OFF = "off"
    val ALL = listOf(ALWAYS, EMERGENCY, OFF)

    val LABEL_HE = mapOf(ALWAYS to "תמיד (מיקום חי)", EMERGENCY to "רק בחירום", OFF to "כבוי")
    val EXPLAIN_HE = mapOf(
        ALWAYS to "מקבל/ת את המיקום החי שלך כל הזמן (כשהשיתוף לא מושהה)",
        EMERGENCY to "מקבל/ת מיקום רק בזמן אירוע חירום פעיל",
        OFF to "מקבל/ת התראת חירום, אבל בלי מיקום",
        "paused" to "תמיד - מושהה כרגע (לא מקבל/ת מיקום)",
    )
}

/**
 * העלאת מיקום חי (PUT /v1/me/live-location) למצב "always". חוסך סוללה:
 * - רק כשיש ≥1 איש קשר במצב always ולא מושהה (מקומית או בשרת), ולא אחרי שהשרת סירב לאחרונה.
 * - מרווח 60 שנ'; 5 דק' בחיסכון סוללה / סוללה חלשה.
 * - **לעולם לא מציגים מיקום ישן כחי**: השרת מחשב "stale" לפי זמן הקבלה, ולכן אסור לשלוח שוב את אותו fix (היה מרענן
 *   את received_at) ואסור לשלוח fix ישן מ-2 דק'. captured_at נשלח תמיד כפי שנמדד.
 */
object LiveLocationPolicy {
    const val INTERVAL_SECONDS = 60.0
    const val SAVER_INTERVAL_SECONDS = 300.0
    const val MAX_FIX_AGE_SECONDS = 120.0
    /** אחרי שהשרת ענה stored=false - לא לנסות שוב לפני רענון ההרשאות / 15 דק' */
    const val REFUSAL_BACKOFF_SECONDS = 900.0
    const val LOW_BATTERY_PERCENT = 20

    data class Input(
        val now: Double,
        val alwaysContacts: Int,
        val paused: Boolean,
        val fix: GeoPoint?,
        val lastUploadAt: Double,
        val lastUploadedFixAt: Double?,
        val powerSave: Boolean,
        val batteryPercent: Int?,
        val charging: Boolean,
        val refusedUntil: Double = 0.0,
    )

    data class Decision(val upload: Boolean, val reason: String)

    fun saver(i: Input) = i.powerSave || (!i.charging && i.batteryPercent != null && i.batteryPercent <= LOW_BATTERY_PERCENT)

    fun interval(i: Input) = if (saver(i)) SAVER_INTERVAL_SECONDS else INTERVAL_SECONDS

    fun decide(i: Input): Decision {
        if (i.paused) return Decision(false, "paused")
        if (i.alwaysContacts <= 0) return Decision(false, "no_always_contacts")
        if (i.now < i.refusedUntil) return Decision(false, "refused_recently")
        if (i.now - i.lastUploadAt < interval(i)) return Decision(false, "interval")
        val fix = i.fix ?: return Decision(false, "no_fix")
        val at = fix.capturedAt ?: return Decision(false, "no_fix_time")
        if (i.now - at > MAX_FIX_AGE_SECONDS) return Decision(false, "fix_too_old")
        if (i.lastUploadedFixAt != null && at <= i.lastUploadedFixAt) return Decision(false, "same_fix")
        return Decision(true, "ok")
    }
}

/** תצוגת מיקום של איש קשר - עם סימון מפורש של מיקום ישן. */
object ContactLocationText {
    const val STALE_SECONDS = 300.0

    data class View(val live: Boolean, val headline: String, val lines: List<String>)

    /**
     * fetchedAtDevice/nowDevice: מתי התשובה התקבלה ומה השעה עכשיו (שעון המכשיר) - הגיל ממשיך לגדול גם אם המסך לא רוענן.
     */
    fun describe(c: ContactLocation, name: String, fetchedAtDevice: Double, nowDevice: Double,
                 tz: TimeZone = TimeZone.getDefault()): View {
        if (!c.available || c.lat == null || c.lon == null) {
            return View(false, "אין מיקום זמין מ$name", listOfNotNull(c.reason ?: "אין עדיין מיקום משותף מהמכשיר"))
        }
        val age = (c.ageSeconds ?: Double.MAX_VALUE / 2) + (nowDevice - fetchedAtDevice).coerceAtLeast(0.0)
        val live = !c.stale && age <= STALE_SECONDS
        val headline = if (live) "מיקום חי · עודכן ${ageHe(age)}"
        else "מיקום ישן - לא חי (התקבל ${ageHe(age)})"
        val lines = ArrayList<String>()
        lines += if (c.accuracyM != null) "דיוק: כ-${c.accuracyM.roundToInt()} מ'" + (if (c.isPrecise) "" else " (משוער)")
        else if (c.isPrecise) "מיקום מדויק" else "מיקום משוער"
        c.receivedAt?.let { lines += "התקבל בשרת: ${clock(it, tz)}" }
        c.capturedAt?.let { lines += "נמדד במכשיר: ${clock(it, tz)} (שעון המכשיר של $name)" }
        if (!live) lines += "ייתכן שאין קליטה / הטלפון כבוי / השיתוף נעצר - המיקום הזה עלול לא לשקף איפה $name עכשיו"
        return View(live, headline, lines)
    }

    fun ageHe(seconds: Double): String {
        val s = seconds.coerceAtLeast(0.0)
        return when {
            s < 60 -> "לפני ${s.toInt()} שנ'"
            s < 3600 -> "לפני ${(s / 60).toInt()} דק'"
            s < 86400 -> "לפני ${(s / 3600).toInt()} שע'"
            else -> "לפני ${(s / 86400).toInt()} ימים"
        }
    }

    fun clock(epochSec: Double, tz: TimeZone = TimeZone.getDefault()): String =
        SimpleDateFormat("dd/MM HH:mm:ss", Locale.US).apply { timeZone = tz }.format(Date((epochSec * 1000).toLong()))
}
