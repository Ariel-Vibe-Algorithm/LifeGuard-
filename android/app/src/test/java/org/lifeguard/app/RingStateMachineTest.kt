package org.lifeguard.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.api.AppNotification
import org.lifeguard.app.core.Effect
import org.lifeguard.app.core.RingStateMachine
import org.lifeguard.app.core.StopReason

class RingStateMachineTest {
    private val now = 1_700_000_000.0
    private val m = RingStateMachine()

    private fun n(id: Long, type: String, payload: String, iid: String? = "inc_1", at: Double = now) =
        AppNotification(id, type, iid, JSONObject(payload), at)

    private fun newInc(id: Long, ring: Boolean?, iid: String = "inc_1", at: Double = now, mode: String = "loud") =
        n(id, "incident_new", """{"id":"$iid","mode":"$mode","status":"active","owner":"dana","owner_display":"דנה"
            ${if (ring != null) ",\"ring\":$ring" else ""}}""", iid, at)

    private fun upd(id: Long, event: String, iid: String = "inc_1", extra: String = "") =
        n(id, "incident_update", """{"incident_id":"$iid","event":"$event"$extra}""", iid)

    private fun run(vararg ns: AppNotification, ownSilent: Set<String> = emptySet(), backlog: Boolean = false) =
        m.onNotifications(ns.toList(), "me", ownSilent, now, backlog)

    @Test fun ring_true_starts_ring_and_every_notification_is_acked() {
        val e = run(newInc(1, true))
        assertEquals(listOf(1L), e.filterIsInstance<Effect.Ack>().map { it.notificationId })
        val start = e.filterIsInstance<Effect.StartRing>().single()
        assertEquals("דנה", start.incident.ownerDisplay)
        assertTrue(m.isRinging("inc_1"))
    }

    @Test fun ring_stopped_stops_ring() {
        run(newInc(1, true))
        val e = run(upd(2, "ring_stopped"))
        assertEquals(Effect.StopRing("inc_1", StopReason.RING_STOPPED), e.filterIsInstance<Effect.StopRing>().single())
        assertFalse(m.isRinging("inc_1"))
        assertEquals(listOf(2L), e.filterIsInstance<Effect.Ack>().map { it.notificationId })
    }

    @Test fun local_accept_or_decline_stops_ring_and_prevents_re_ring() {
        run(newInc(1, true))
        assertEquals(listOf(Effect.StopRing("inc_1", StopReason.ANSWERED)), m.onLocalResponse("inc_1", "accept"))
        assertTrue(m.onLocalResponse("inc_1", "accept").isEmpty())      // כבר שקט
        val again = run(newInc(2, true))                                  // שליחה חוזרת מהשרת - לא מצלצל שוב
        assertTrue(again.none { it is Effect.StartRing })
    }

    @Test fun ring_false_is_quiet_alert_and_missing_ring_is_normal_alert() {
        val quiet = run(newInc(1, false)).filterIsInstance<Effect.IncidentAlert>().single()
        assertFalse(quiet.loud)
        val vol = run(newInc(2, null, iid = "inc_2")).filterIsInstance<Effect.IncidentAlert>().single()
        assertTrue(vol.loud)
        assertTrue(m.ringingIncidents.isEmpty())
    }

    @Test fun incident_closed_stops_ring() {
        run(newInc(1, true))
        val e = run(n(2, "incident_closed", """{"incident_id":"inc_1","reason":"resolved","text":"הסתיים"}"""))
        assertEquals(StopReason.CLOSED, e.filterIsInstance<Effect.StopRing>().single().reason)
        assertEquals("resolved", e.filterIsInstance<Effect.IncidentClosed>().single().reason)
    }

    @Test fun start_and_stop_in_same_batch_never_sounds() {
        val e = run(newInc(1, true), upd(2, "ring_stopped"))
        assertTrue(e.none { it is Effect.StartRing || it is Effect.StopRing })
        assertFalse(e.filterIsInstance<Effect.IncidentAlert>().single().loud)
        assertEquals(listOf(1L, 2L), e.filterIsInstance<Effect.Ack>().map { it.notificationId })
        assertFalse(m.isRinging("inc_1"))
    }

    @Test fun stale_ring_from_backlog_does_not_ring() {
        val e = run(newInc(1, true, at = now - 600), backlog = true)
        assertTrue(e.none { it is Effect.StartRing })
        assertFalse(e.filterIsInstance<Effect.IncidentAlert>().single().loud)
    }

    @Test fun duplicate_ids_are_ignored() {
        run(newInc(5, true))
        val e = run(newInc(5, true), newInc(4, true))
        assertTrue(e.isEmpty())
    }

    @Test fun owner_of_silent_incident_gets_quiet_chat_even_without_flag() {
        val e = run(upd(1, "message", extra = ""","from":"אבי","message_id":9"""), ownSilent = setOf("inc_1"))
        val c = e.filterIsInstance<Effect.ChatMessage>().single()
        assertTrue(c.quiet); assertEquals(9L, c.messageId); assertEquals("אבי", c.from)
    }

    @Test fun quiet_flag_from_server_is_respected_and_responder_chat_is_audible() {
        assertTrue(run(upd(1, "message", extra = ""","quiet":true""")).filterIsInstance<Effect.ChatMessage>().single().quiet)
        assertFalse(run(upd(2, "message", iid = "inc_2", extra = ""","from":"דנה""""))
            .filterIsInstance<Effect.ChatMessage>().single().quiet)
    }

    @Test fun read_receipts_are_quiet_updates() {
        val c = run(upd(1, "read", extra = ""","quiet":true""")).filterIsInstance<Effect.IncidentChanged>().single()
        assertTrue(c.quiet); assertEquals("read", c.event)
        val r = run(upd(2, "response", extra = ""","by":"אבי","action":"on_my_way""""))
            .filterIsInstance<Effect.IncidentChanged>().single()
        assertFalse(r.quiet); assertEquals("אבי: בדרך", r.text)
    }

    @Test fun info_and_escort_overdue() {
        val i = run(n(1, "info", """{"from_display":"דנה","text":"בדיקה"}""", iid = null))
            .filterIsInstance<Effect.Info>().single()
        assertEquals("בדיקה", i.text)
        val o = run(n(2, "escort_overdue", """{"escort_id":"esc_1","text":"x","silent":true}""", iid = null))
            .filterIsInstance<Effect.EscortOverdue>().single()
        assertTrue(o.silent)
    }
}
