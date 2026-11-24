package org.lifeguard.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.IncidentView
import org.lifeguard.app.core.ActivationPolicy
import org.lifeguard.app.core.ActivationPolicy.CreateFailure
import org.lifeguard.app.core.Activations
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.core.NoSignalPolicy
import org.lifeguard.app.core.PendingSos
import org.lifeguard.app.core.Triggers

/** משימה 1: הפעלת חירום לפי מקור וביטחון (חוזה השרת). */
class ActivationPolicyTest {
    private val at = 1_700_000_000_000L

    @Test fun manual_sos_is_never_gated_by_confidence() {
        // SOS ידני: אין risk, אין שלב הערכה, הצפי תמיד "manual" (גם בלי ביטחון / ביטחון 0)
        assertEquals(Activations.MANUAL, ActivationPolicy.expectedActivation(Triggers.MANUAL, null))
        assertEquals(Activations.MANUAL, ActivationPolicy.expectedActivation(Triggers.MANUAL, 0.0))
        assertEquals(Activations.MANUAL, ActivationPolicy.expectedActivation(null, null))
        val manual = PendingSos("cid", "loud", 1000.0, null)
        assertEquals(Triggers.MANUAL, manual.trigger)
        assertFalse(manual.isAutomatic)
        // נשלח מיד (attempts=0 -> בלי המתנה) ו-SMS ישיר אחרי 20 שנ' בלי שום תלות בביטחון
        assertTrue(NoSignalPolicy.shouldAttemptCreate(manual, 1000.0))
        assertTrue(NoSignalPolicy.shouldSendSms(manual, 1021.0, true, true, true))
        assertTrue(ActivationPolicy.emergencyConfirmedForRecording(false, null, manual.trigger, null))
    }

    @Test fun wellness_triggers_and_no_response_signal() {
        assertEquals(Triggers.WELLNESS_NO_RESPONSE, ActivationPolicy.triggerForWellness("no_answer"))
        assertEquals(Triggers.SENSOR, ActivationPolicy.triggerForWellness("help_requested"))
        val sig = listOf(MotionSignal("fall", 0.3, at, "נפילה"))
        val noAns = ActivationPolicy.wellnessRisk(sig, "no_answer", at + 45_000)
        assertTrue(noAns.signals.any { it.source == ActivationPolicy.NO_RESPONSE_SOURCE })
        // הביטחון המשולב עולה בזכות "אין תגובה" (noisy-OR): 1-(0.7*0.5)=0.65
        assertEquals(0.65, noAns.confidence, 1e-9)
        assertTrue(noAns.reasons.contains(ActivationPolicy.NO_RESPONSE_REASON))
        assertEquals(1, noAns.reasons.count { it == ActivationPolicy.NO_RESPONSE_REASON })   // בלי כפילות
        val js = noAns.toJson()
        assertEquals(0.65, js.getDouble("confidence"), 1e-9)
        // "צריך/ה עזרה" = אישור אנושי -> ביטחון ≥0.95 -> confirmed_high (לעולם לא נדחה)
        val help = ActivationPolicy.wellnessRisk(sig, "help_requested", at)
        assertTrue(help.confidence >= 0.95)
        assertFalse(help.signals.any { it.source == ActivationPolicy.NO_RESPONSE_SOURCE })
        assertEquals(Activations.CONFIRMED_HIGH, ActivationPolicy.expectedActivation(Triggers.SENSOR, help.confidence))
    }

    @Test fun thresholds_match_server() {
        val t = Triggers.WELLNESS_NO_RESPONSE
        assertNull(ActivationPolicy.expectedActivation(t, null))
        assertNull(ActivationPolicy.expectedActivation(t, 0.39))
        assertEquals(Activations.UNCONFIRMED, ActivationPolicy.expectedActivation(t, 0.4))
        assertEquals(Activations.UNCONFIRMED, ActivationPolicy.expectedActivation(t, 0.69))
        assertEquals(Activations.CONFIRMED_HIGH, ActivationPolicy.expectedActivation(t, 0.7))
        for (a in listOf("sensor", "acoustic", "auto")) assertTrue(Triggers.isAutomatic(a))
    }

    @Test fun create_errors_are_classified() {
        assertEquals(CreateFailure.LowConfidence, ActivationPolicy.classifyCreateError(422, "low_confidence"))
        assertEquals(CreateFailure.Retry, ActivationPolicy.classifyCreateError(null, null))
        assertEquals(CreateFailure.Retry, ActivationPolicy.classifyCreateError(503, "x"))
        assertEquals(CreateFailure.Retry, ActivationPolicy.classifyCreateError(401, "auth"))
        assertEquals(CreateFailure.Rejected(422, "other"), ActivationPolicy.classifyCreateError(422, "other"))
        assertEquals(CreateFailure.Rejected(400, "invalid_client_id"), ActivationPolicy.classifyCreateError(400, "invalid_client_id"))
    }

    @Test fun automatic_pending_sos_keeps_trigger_and_gates_sms_and_recording() {
        fun auto(conf: Double) = PendingSos("c", "loud", 1000.0, GeoPoint(1.0, 2.0), source = "wellness_check:motion",
            riskJson = JSONObject().put("level", "LOW").put("confidence", conf).toString(), trigger = Triggers.WELLNESS_NO_RESPONSE)
        val low = auto(0.2)
        val back = PendingSos.parse(JSONObject(low.toJson().toString()))
        assertEquals(low, back)
        assertEquals(Triggers.WELLNESS_NO_RESPONSE, back.trigger)
        assertEquals(0.2, back.confidence!!, 1e-9)
        // ביטחון נמוך -> גם בלי קליטה לא שולחים SMS (השרת היה דוחה)
        assertFalse(NoSignalPolicy.shouldSendSms(low, 2000.0, false, true, true))
        assertFalse(ActivationPolicy.emergencyConfirmedForRecording(false, null, low.trigger, low.confidence))
        // בינוני -> אנשי הקשר כן מקבלים (גם ב-SMS), אבל לא מקליטים עד אישור
        val mid = auto(0.5)
        assertTrue(NoSignalPolicy.shouldSendSms(mid, 2000.0, false, true, true))
        assertFalse(ActivationPolicy.emergencyConfirmedForRecording(false, null, mid.trigger, mid.confidence))
        assertTrue(ActivationPolicy.emergencyConfirmedForRecording(false, null, mid.trigger, 0.8))
        // שרת: unconfirmed -> לא מאושר; confirmed_by_user / manual / שרת ישן בלי שדה -> מאושר
        assertFalse(ActivationPolicy.emergencyConfirmedForRecording(true, "unconfirmed", null, null))
        assertTrue(ActivationPolicy.emergencyConfirmedForRecording(true, "confirmed_by_user", null, null))
        assertTrue(ActivationPolicy.emergencyConfirmedForRecording(true, null, null, null))
        // PendingSos ישן (לפני השדה) נקרא כידני
        assertEquals(Triggers.MANUAL, PendingSos.parse(JSONObject().put("client_id", "x").put("mode", "loud")).trigger)
    }

    @Test fun incident_view_parses_activation_stale_and_withheld() {
        val v = IncidentView.parse(JSONObject("""{"id":"inc_1","status":"active","activation":"unconfirmed",
            "location":{"lat":1,"lon":2,"stale":true,"received_at":5}}"""))
        assertEquals("unconfirmed", v.activation)
        assertTrue(Activations.needsOwnerConfirmation(v.activation))
        assertTrue(v.locationStale)
        assertFalse(v.locationWithheld)
        val w = IncidentView.parse(JSONObject("""{"id":"inc_2","status":"active","activation":"manual","location_withheld":true}"""))
        assertTrue(w.locationWithheld); assertNull(w.location); assertFalse(Activations.needsOwnerConfirmation(w.activation))
    }
}
