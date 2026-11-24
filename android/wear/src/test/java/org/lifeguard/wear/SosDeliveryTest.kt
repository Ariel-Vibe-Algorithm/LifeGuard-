package org.lifeguard.wear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.wearlink.WearProtocol
import org.lifeguard.app.wearlink.WearProtocol.AckStatus
import org.lifeguard.app.wearlink.WearProtocol.PhoneState
import org.lifeguard.app.wearlink.WearProtocol.SosAck
import org.lifeguard.app.wearlink.WearProtocol.SosStatus
import org.lifeguard.wear.logic.SosDelivery
import org.lifeguard.wear.logic.SosDelivery.Companion as T

/** השעון מציג רק מה שהוא באמת יודע */
class SosDeliveryTest {
    private fun d() = SosDelivery(ackTimeoutMs = 10_000).apply { begin("r1", WearProtocol.KIND_MANUAL) }
    private val allText get() = listOf(T.T_SENT_TO_PHONE, T.T_DELIVERED)

    @Test fun never_says_sent_before_phone_ack() {
        val d = d()
        assertEquals(T.T_SENDING, d.view().title)
        d.onSent("r1", 0)                                         // MessageClient הצליח - עדיין לא אישור
        val v = d.view()
        assertEquals(T.T_WAITING_ACK, v.title)
        assertTrue(allText.none { v.title.contains(it) || v.detail.contains(it) })
        d.onAck(SosAck("r1", AckStatus.QUEUED, "cid"))
        assertEquals(T.T_SENT_TO_PHONE, d.view().title)
        assertEquals(T.T_PHONE_SENDING, d.view().detail)          // עדיין לא "התקבל בשרת"
    }

    @Test fun server_delivery_only_after_relayed_status() {
        val d = d(); d.onSent("r1", 0); d.onAck(SosAck("r1", AckStatus.QUEUED, "cid"))
        d.onStatus(SosStatus("r1", "cid", PhoneState.RETRYING, attempts = 2))
        assertTrue(d.view().detail.contains("ניסיון 2"))
        assertTrue(d.view().offerCall)
        d.onStatus(SosStatus("r1", "cid", PhoneState.DELIVERED, activation = "manual", attempts = 3))
        assertEquals(T.T_DELIVERED, d.view().detail)
        assertFalse(d.view().offerCall)
        // סטטוס "מנסה שוב" ישן שהגיע באיחור לא מבטל את "התקבל"
        d.onStatus(SosStatus("r1", "cid", PhoneState.RETRYING, attempts = 2))
        assertEquals(T.T_DELIVERED, d.view().detail)
    }

    @Test fun unconfirmed_activation_is_described_honestly() {
        val d = d(); d.onSent("r1", 0); d.onAck(SosAck("r1", AckStatus.QUEUED, "cid"))
        d.onStatus(SosStatus("r1", "cid", PhoneState.DELIVERED, activation = "unconfirmed"))
        assertEquals(T.T_DELIVERED_UNCONFIRMED, d.view().detail)
    }

    @Test fun low_confidence_rejection_shown() {
        val d = SosDelivery().apply { begin("r1", WearProtocol.KIND_WATCH_FALL) }
        d.onSent("r1", 0); d.onAck(SosAck("r1", AckStatus.QUEUED, "cid"))
        d.onStatus(SosStatus("r1", "cid", PhoneState.REJECTED_LOW_CONFIDENCE))
        assertEquals(T.T_REJECTED_TITLE, d.view().title)
    }

    @Test fun sms_is_submitted_not_delivered() {
        val d = d(); d.onSent("r1", 0); d.onAck(SosAck("r1", AckStatus.QUEUED, "cid"))
        d.onStatus(SosStatus("r1", "cid", PhoneState.RETRYING, attempts = 1))
        d.onStatus(SosStatus("r1", "cid", PhoneState.SMS_SUBMITTED, smsCount = 2))
        val v = d.view()
        assertTrue(v.detail.contains("ניסיון 1"))
        assertTrue(v.detail.contains("העביר SMS ישיר לשליחה ל-2"))
        d.onStatus(SosStatus("r1", "cid", PhoneState.SMS_FAILED))
        assertTrue(d.view().detail.contains(T.T_SMS_FAILED))
        assertTrue(d.view().offerCall)
    }

    @Test fun no_phone_offers_call_and_retry() {
        val d = d()
        d.onNoPhone("r1", SosDelivery.NoPhoneReason.NOT_CONNECTED)
        val v = d.view()
        assertEquals(T.T_NO_PHONE, v.title)
        assertEquals(T.T_CALL_FROM_WATCH, v.detail)
        assertTrue(v.offerCall && v.canRetry)
        assertEquals("r1" to WearProtocol.KIND_MANUAL, d.retry())
        assertEquals(T.T_SENDING, d.view().title)
        d.onNoPhone("r1", SosDelivery.NoPhoneReason.APP_MISSING)
        assertEquals(T.T_NO_APP, d.view().title)
    }

    @Test fun ack_timeout_says_so_and_late_ack_still_counts() {
        val d = d(); d.onSent("r1", 1000)
        d.tick(10_999)
        assertEquals(T.T_WAITING_ACK, d.view().title)
        d.tick(11_000)
        val v = d.view()
        assertEquals(T.T_NO_ACK, v.title)
        assertTrue(v.offerCall && v.canRetry)
        d.onAck(SosAck("r1", AckStatus.QUEUED, "cid"))
        assertEquals(T.T_SENT_TO_PHONE, d.view().title)
    }

    @Test fun phone_refusals() {
        val a = d(); a.onSent("r1", 0); a.onAck(SosAck("r1", AckStatus.NOT_LOGGED_IN))
        assertEquals(T.T_NOT_LOGGED_IN, a.view().title); assertTrue(a.view().offerCall)
        val b = d(); b.onSent("r1", 0); b.onAck(SosAck("r1", AckStatus.ALREADY_ACTIVE))
        assertEquals(T.T_ALREADY_ACTIVE, b.view().title); assertFalse(b.view().canRetry)
        val c = d(); c.onSent("r1", 0); c.onAck(SosAck("r1", AckStatus.INVALID))
        assertEquals(T.T_INVALID, c.view().title)
    }

    @Test fun stale_messages_for_other_requests_are_ignored() {
        val d = d(); d.onSent("r1", 0)
        assertFalse(d.onAck(SosAck("old", AckStatus.QUEUED, "x")))
        assertFalse(d.onStatus(SosStatus("old", "x", PhoneState.DELIVERED)))
        assertEquals(T.T_WAITING_ACK, d.view().title)
        d.onSent("other", 0); d.onNoPhone("other", SosDelivery.NoPhoneReason.NOT_CONNECTED)
        assertEquals(T.T_WAITING_ACK, d.view().title)
    }

    @Test fun status_before_ack_is_an_implicit_ack() {
        val d = d(); d.onSent("r1", 0)
        assertTrue(d.onStatus(SosStatus("r1", "cid", PhoneState.DELIVERED, activation = "manual")))
        assertEquals(T.T_SENT_TO_PHONE, d.view().title)
        assertEquals(T.T_DELIVERED, d.view().detail)
        d.onAck(SosAck("r1", AckStatus.QUEUED, "cid"))          // ה-ack המאוחר לא מוחק את הסטטוס
        assertEquals(T.T_DELIVERED, d.view().detail)
    }

    @Test fun retry_not_offered_once_acknowledged() {
        val d = d(); d.onSent("r1", 0); d.onAck(SosAck("r1", AckStatus.QUEUED, "cid"))
        assertNull(d.retry())
    }

    @Test fun wire_round_trip_feeds_delivery() {
        val d = d(); d.onSent("r1", 0)
        d.onAck(WearProtocol.decodeAck(WearProtocol.encodeAck(SosAck("r1", AckStatus.QUEUED, "cid")))!!)
        d.onStatus(WearProtocol.decodeStatus(WearProtocol.encodeStatus(SosStatus("r1", "cid", PhoneState.DELIVERED, "confirmed_high")))!!)
        assertEquals(T.T_DELIVERED, d.view().detail)
    }
}
