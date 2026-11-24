package org.lifeguard.app.wear

import org.lifeguard.app.core.ActivationPolicy
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.core.RiskAssessment
import org.lifeguard.app.core.RiskScorer
import org.lifeguard.app.core.Triggers
import org.lifeguard.app.wearlink.WearProtocol
import org.lifeguard.app.wearlink.WearProtocol.AckStatus
import org.lifeguard.app.wearlink.WearProtocol.SosRequest

/**
 * בקשה מהשעון -> מה הטלפון עושה. טהור (בלי Android) - נבדק ב-JVM.
 *
 * - kind=manual ("SOS" בשעון): בדיוק כמו כפתור ה-SOS בטלפון - IncidentManager.startSos(mode=loud, trigger=manual,
 *   בלי risk). ידני = תמיד מלא (השרת לא בודק ביטחון).
 * - kind=watch_fall (נפילה בשעון בלי תשובה / "צריך/ה עזרה"): כמו "הכל בסדר?" בטלפון - trigger אוטומטי
 *   (ActivationPolicy.triggerForWellness: no_answer -> wellness_no_response, help_requested -> sensor) + risk מהאותות
 *   של השעון (ActivationPolicy.wellnessRisk). **לא** manual: ספי ההפעלה של השרת חלים (<0.4 נדחה, 0.4..0.7 unconfirmed).
 *
 * חשוב (חוזה השרת, server/privacy.py activation_for): trigger לא מוכר הופך בשרת ל-"manual" = הפעלה מלאה.
 * לכן "watch_fall" נשאר רק במקור המקומי (PendingSos.source) ולעולם לא נשלח כ-trigger; וגם לא כ-source של אות
 * (dispatch.SIGNAL_SOURCES לא מכיר אותו ומשמיט בשקט) - האות נשלח כ-"fall" וההסבר מציין "שעון".
 */
object WearSosMapper {
    const val MODE = "loud"
    const val WATCH_REASON_PREFIX = "שעון: "

    /** מקורות אותות שהשעון רשאי לשלוח (אותם שמות כמו MotionFusion / SIGNAL_SOURCES בשרת). no_response מתווסף כאן. */
    val WATCH_SIGNAL_SOURCES = setOf("fall", "impact", "struggle")

    sealed class Decision {
        data class Start(val mode: String, val source: String, val trigger: String, val risk: RiskAssessment?) : Decision()
        data class Reply(val status: AckStatus) : Decision()
    }

    fun decide(req: SosRequest?, loggedIn: Boolean, incidentActive: Boolean, nowMs: Long): Decision = when {
        req == null -> Decision.Reply(AckStatus.INVALID)
        !loggedIn -> Decision.Reply(AckStatus.NOT_LOGGED_IN)
        incidentActive -> Decision.Reply(AckStatus.ALREADY_ACTIVE)
        else -> toStart(req, nowMs)
    }

    fun toStart(req: SosRequest, nowMs: Long): Decision = when (req.kind) {
        WearProtocol.KIND_MANUAL ->
            Decision.Start(MODE, WearProtocol.phoneSource(req.kind, req.requestId), Triggers.MANUAL, null)
        WearProtocol.KIND_WATCH_FALL -> {
            val why = req.why
            val conf = req.confidence
            if ((why != WearProtocol.WHY_NO_ANSWER && why != WearProtocol.WHY_HELP_REQUESTED) || conf == null || conf.isNaN())
                Decision.Reply(AckStatus.INVALID)
            else Decision.Start(MODE, WearProtocol.phoneSource(req.kind, req.requestId),
                ActivationPolicy.triggerForWellness(why), ActivationPolicy.wellnessRisk(watchSignals(req), why, nowMs))
        }
        else -> Decision.Reply(AckStatus.INVALID)
    }

    /** האותות מהשעון (רק מקורות מוכרים), עם "שעון:" בהסבר. תמיד כולל אות fall בביטחון הגלאי. */
    fun watchSignals(req: SosRequest): List<MotionSignal> {
        val out = req.signals.filter { it.source in WATCH_SIGNAL_SOURCES }.map {
            it.copy(confidence = it.confidence.coerceIn(0.0, 1.0),
                reason = WATCH_REASON_PREFIX + it.reason.ifEmpty { RiskScorer.SOURCE_HE[it.source] ?: it.source })
        }
        return if (out.any { it.source == "fall" }) out
        else out + MotionSignal("fall", (req.confidence ?: 0.0).coerceIn(0.0, 1.0), req.atMs, WATCH_REASON_PREFIX + "זוהתה נפילה")
    }

    /**
     * בקשות שכבר טופלו (השעון שולח שוב את אותו requestId אם לא קיבל אישור) -> אותה תשובה, בלי SOS נוסף.
     * בזיכרון בלבד; אחרי הפעלה מחדש של התהליך אירוע פעיל ממילא מחזיר already_active.
     */
    class RequestLog(private val max: Int = 32) {
        private val acks = LinkedHashMap<String, WearProtocol.SosAck>()

        @Synchronized fun get(requestId: String): WearProtocol.SosAck? = acks[requestId]

        @Synchronized fun put(ack: WearProtocol.SosAck) {
            acks.remove(ack.requestId)
            acks[ack.requestId] = ack
            while (acks.size > max) acks.remove(acks.keys.first())
        }
    }
}
