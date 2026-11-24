package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.core.ConnectivityTracker
import org.lifeguard.app.core.FallDetector
import org.lifeguard.app.core.NoSignalPolicy
import org.lifeguard.app.core.PendingSos
import org.lifeguard.app.core.SmsComposer
import org.lifeguard.app.core.SmsState
import java.util.TimeZone

class NoSignalPolicyTest {
    private val t0 = 1000.0
    private fun sos(attempts: Int = 0, last: Double = 0.0, sms: SmsState = SmsState.NONE, smsAttempts: Int = 0,
                    lastSms: Double = 0.0) =
        PendingSos("cid", "loud", t0, GeoPoint(32.0, 34.0), attempts = attempts, lastAttemptAt = last, smsState = sms,
            smsAttempts = smsAttempts, lastSmsAt = lastSms)

    @Test fun retry_backoff_is_capped() {
        assertEquals(listOf(0.0, 1.0, 2.0, 4.0, 8.0, 15.0, 15.0), (0..6).map { NoSignalPolicy.retryDelaySeconds(it) })
        assertTrue(NoSignalPolicy.shouldAttemptCreate(sos(), t0))
        assertFalse(NoSignalPolicy.shouldAttemptCreate(sos(attempts = 3, last = t0), t0 + 3))
        assertTrue(NoSignalPolicy.shouldAttemptCreate(sos(attempts = 3, last = t0), t0 + 4))
    }

    @Test fun sms_when_no_network_or_after_failures_or_timeout() {
        assertTrue(NoSignalPolicy.shouldSendSms(sos(), t0, networkAvailable = false, hasNumbers = true, hasPermission = true))
        assertFalse(NoSignalPolicy.shouldSendSms(sos(attempts = 1), t0 + 1, true, true, true))
        assertTrue(NoSignalPolicy.shouldSendSms(sos(attempts = 3), t0 + 5, true, true, true))
        assertTrue(NoSignalPolicy.shouldSendSms(sos(attempts = 1), t0 + 21, true, true, true))
    }

    @Test fun sms_needs_numbers_and_permission_and_is_sent_once() {
        assertFalse(NoSignalPolicy.shouldSendSms(sos(), t0, false, hasNumbers = false, hasPermission = true))
        assertFalse(NoSignalPolicy.shouldSendSms(sos(), t0, false, hasNumbers = true, hasPermission = false))
        assertFalse(NoSignalPolicy.shouldSendSms(sos(sms = SmsState.SENT), t0 + 999, false, true, true))
        assertFalse(NoSignalPolicy.shouldSendSms(sos(sms = SmsState.SENDING), t0 + 999, false, true, true))
        // נכשל (אין קליטה סלולרית) -> ניסיון חוזר כל דקה, עד 5 פעמים
        assertFalse(NoSignalPolicy.shouldSendSms(sos(sms = SmsState.FAILED, smsAttempts = 1, lastSms = t0), t0 + 30, false, true, true))
        assertTrue(NoSignalPolicy.shouldSendSms(sos(sms = SmsState.FAILED, smsAttempts = 1, lastSms = t0), t0 + 61, false, true, true))
        assertFalse(NoSignalPolicy.shouldSendSms(sos(sms = SmsState.FAILED, smsAttempts = 5, lastSms = t0), t0 + 999, false, true, true))
    }

    @Test fun pending_sos_roundtrips_json() {
        val p = sos(attempts = 2, sms = SmsState.FAILED, smsAttempts = 1).copy(escortId = "esc_1", source = "wellness_check")
        assertEquals(p, PendingSos.parse(p.toJson()))
    }

    @Test fun sms_text_contract() {
        val utc = TimeZone.getTimeZone("UTC")
        assertEquals("LifeGuard: דנה needs help. Location: https://maps.google.com/?q=32.085300,34.781800 (00:01)",
            SmsComposer.help("דנה", GeoPoint(32.0853, 34.7818), 60_000L, utc))
        assertEquals("LifeGuard: X needs help. Location unknown (00:00)", SmsComposer.help("X\n", null, 0L, utc))
        assertEquals("LifeGuard: X is safe now (00:00)", SmsComposer.safe("X", 0L, utc))
    }

    @Test fun connectivity_tracker() {
        val c = ConnectivityTracker(staleSeconds = 90.0)
        assertFalse(c.isOnline(0.0))
        c.onSuccess(10.0); assertTrue(c.isOnline(20.0))
        c.onFailure(30.0); assertFalse(c.isOnline(31.0))
        c.onSuccess(40.0); assertTrue(c.isOnline(41.0)); assertFalse(c.isOnline(200.0))
    }
}

class FallDetectorTest {
    private val G = FallDetector.GRAVITY.toFloat()

    /** מזין רצף של (משך במ"ש, תאוצה ב-g) בדגימה כל 20 מ"ש; מחזיר את הזיהוי הראשון */
    private fun feed(d: FallDetector, vararg segments: Pair<Int, Double>): FallDetector.Detection? {
        var t = 0L
        for ((ms, g) in segments) {
            val end = t + ms
            while (t < end) {
                d.onSample(t, 0f, 0f, (g * G).toFloat())?.let { return it }
                t += 20
            }
        }
        return null
    }

    @Test fun free_fall_then_impact_then_still_is_a_fall() {
        val r = feed(FallDetector(), 1000 to 1.0, 300 to 0.1, 60 to 3.2, 3000 to 1.0)
        assertEquals("fall", r!!.signal); assertTrue(r.confidence >= 0.8)
    }

    @Test fun impact_without_free_fall_is_ignored() {
        assertNull(feed(FallDetector(), 1000 to 1.0, 60 to 4.0, 3000 to 1.0))       // טריקה על שולחן
    }

    @Test fun free_fall_without_impact_is_ignored() {
        assertNull(feed(FallDetector(), 300 to 0.1, 3000 to 1.0))
    }

    @Test fun movement_after_impact_is_low_confidence_impact() {
        // אחרי המכה: תנועה חזקה (הרים את הטלפון והמשיך ללכת)
        val segs = arrayListOf(1000 to 1.0, 300 to 0.1, 60 to 3.0)
        repeat(40) { segs += 40 to 1.8; segs += 40 to 0.4 }
        val r = feed(FallDetector(), *segs.toTypedArray())
        assertEquals("impact", r!!.signal); assertTrue(r.confidence < 0.8)
    }

    @Test fun walking_never_triggers() {
        val segs = ArrayList<Pair<Int, Double>>()
        repeat(100) { segs += 100 to 1.3; segs += 100 to 0.7 }
        assertNull(feed(FallDetector(), *segs.toTypedArray()))
    }
}
