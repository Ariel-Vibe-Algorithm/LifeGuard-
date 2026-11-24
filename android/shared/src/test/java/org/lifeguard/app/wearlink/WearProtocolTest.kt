package org.lifeguard.app.wearlink

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import org.lifeguard.app.core.MotionSignal
import org.lifeguard.app.wearlink.WearProtocol.AckStatus
import org.lifeguard.app.wearlink.WearProtocol.PhoneState
import org.lifeguard.app.wearlink.WearProtocol.SosAck
import org.lifeguard.app.wearlink.WearProtocol.SosRequest
import org.lifeguard.app.wearlink.WearProtocol.SosStatus

class WearProtocolTest {
    private fun b(s: String) = s.toByteArray(Charsets.UTF_8)

    @Test fun manual_request_round_trip() {
        val r = SosRequest("req-1", WearProtocol.KIND_MANUAL, 1_700_000_000_000L)
        assertEquals(r, WearProtocol.decodeRequest(WearProtocol.encodeRequest(r)))
    }

    @Test fun fall_request_round_trip_keeps_confidence_signals_and_hebrew() {
        val sig = MotionSignal("fall", 0.85, 1_700_000_000_500L, "נפילה: נפילה חופשית 400 מ\"ש, מכה 3.8g (שעון)")
        val r = SosRequest("req-2", WearProtocol.KIND_WATCH_FALL, 1_700_000_031_000L, WearProtocol.WHY_NO_ANSWER, 0.85,
            listOf(sig, MotionSignal("impact", 0.6, 1_700_000_000_400L, "מכה חזקה 3.8g")))
        val d = WearProtocol.decodeRequest(WearProtocol.encodeRequest(r))
        assertEquals(r, d)
    }

    @Test fun manual_request_ignores_fall_fields() {
        val d = WearProtocol.decodeRequest(b("""{"v":1,"req":"a","kind":"manual","at":5,"confidence":0.1,"why":"no_answer"}"""))!!
        assertEquals(WearProtocol.KIND_MANUAL, d.kind)
        assertNull(d.confidence); assertNull(d.why); assertEquals(emptyList<MotionSignal>(), d.signals)
    }

    @Test fun invalid_requests_decode_to_null() {
        assertNull(WearProtocol.decodeRequest(null))
        assertNull(WearProtocol.decodeRequest(ByteArray(0)))
        assertNull(WearProtocol.decodeRequest(b("not json")))
        assertNull(WearProtocol.decodeRequest(b("""{"req":"a","kind":"manual"}""")))                 // בלי גרסה
        assertNull(WearProtocol.decodeRequest(b("""{"v":1,"kind":"manual"}""")))                      // בלי מזהה
        assertNull(WearProtocol.decodeRequest(b("""{"v":1,"req":"${"x".repeat(65)}","kind":"manual"}""")))
        assertNull(WearProtocol.decodeRequest(b("""{"v":1,"req":"a","kind":"watch_button"}""")))      // סוג לא מוכר
        // נפילה בלי ביטחון / ביטחון מחוץ לטווח / בלי why -> לא תקין (לא "מנחשים" ביטחון)
        assertNull(WearProtocol.decodeRequest(b("""{"v":1,"req":"a","kind":"watch_fall","why":"no_answer"}""")))
        assertNull(WearProtocol.decodeRequest(b("""{"v":1,"req":"a","kind":"watch_fall","why":"no_answer","confidence":1.5}""")))
        assertNull(WearProtocol.decodeRequest(b("""{"v":1,"req":"a","kind":"watch_fall","confidence":0.8}""")))
        assertNull(WearProtocol.decodeRequest(b("""{"v":1,"req":"a","kind":"watch_fall","why":"maybe","confidence":0.8}""")))
        assertNull(WearProtocol.decodeRequest(ByteArray(WearProtocol.MAX_BYTES + 1) { 'a'.code.toByte() }))
    }

    @Test fun future_version_with_same_fields_is_still_read() {
        assertNotNull(WearProtocol.decodeRequest(b("""{"v":2,"req":"a","kind":"manual","at":1,"extra":true}""")))
    }

    @Test fun fall_signals_are_bounded_and_clamped() {
        val many = (1..20).joinToString(",") { """{"source":"fall","confidence":1.7,"at":$it,"reason":"${"r".repeat(500)}"}""" }
        val d = WearProtocol.decodeRequest(b("""{"v":1,"req":"a","kind":"watch_fall","why":"help_requested","confidence":0.7,"at":9,"signals":[$many,{"source":"","confidence":0.5}]}"""))!!
        assertEquals(WearProtocol.MAX_SIGNALS, d.signals.size)
        assertEquals(1.0, d.signals[0].confidence, 0.0)
        assertEquals(WearProtocol.MAX_REASON, d.signals[0].reason.length)
    }

    @Test fun ack_round_trip_and_unknown_status() {
        for (s in AckStatus.entries) {
            val a = SosAck("r", s, if (s == AckStatus.QUEUED) "cid" else null)
            assertEquals(a, WearProtocol.decodeAck(WearProtocol.encodeAck(a)))
        }
        assertNull(WearProtocol.decodeAck(b("""{"v":1,"req":"r","status":"sent"}""")))
    }

    @Test fun status_round_trip_and_validation() {
        for (st in PhoneState.entries) {
            val s = SosStatus("r", "cid", st, if (st == PhoneState.DELIVERED) "unconfirmed" else null, 3, 2)
            assertEquals(s, WearProtocol.decodeStatus(WearProtocol.encodeStatus(s)))
        }
        assertNull(WearProtocol.decodeStatus(b("""{"v":1,"req":"r","state":"delivered"}""")))         // בלי client_id
        assertNull(WearProtocol.decodeStatus(b("""{"v":1,"req":"r","client_id":"c","state":"received_by_contacts"}""")))
    }

    @Test fun phone_source_round_trip() {
        val s = WearProtocol.phoneSource(WearProtocol.KIND_WATCH_FALL, "abc-123")
        assertEquals(WearProtocol.KIND_WATCH_FALL to "abc-123", WearProtocol.parsePhoneSource(s))
        assertNull(WearProtocol.parsePhoneSource("button"))
        assertNull(WearProtocol.parsePhoneSource("wellness_check:motion"))
        assertNull(WearProtocol.parsePhoneSource("watch:manual:"))
        assertNull(WearProtocol.parsePhoneSource(null))
    }
}
