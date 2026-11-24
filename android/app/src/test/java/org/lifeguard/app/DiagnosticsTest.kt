package org.lifeguard.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.SetupStatus
import org.lifeguard.app.core.AuthState
import org.lifeguard.app.core.ChannelState
import org.lifeguard.app.core.CheckStatus
import org.lifeguard.app.core.DiagnosticsEvaluator
import org.lifeguard.app.core.DiagnosticsSnapshot
import org.lifeguard.app.core.FcmDiagState
import org.lifeguard.app.core.FcmTokenRegistry
import org.lifeguard.app.core.FixAction
import org.lifeguard.app.core.Importance
import org.lifeguard.app.core.LocationPerm
import org.lifeguard.app.core.ServerProbe
import org.lifeguard.app.core.SetupProbe

class DiagnosticsEvaluatorTest {
    private val now = 1_700_000_000.0
    private val setupOk = SetupStatus(2, 0, 1, true, null, null, now - 3600, 2, 2, now, fcmDevices = 1, fcm = "active")

    private fun good() = DiagnosticsSnapshot(
        sdkInt = 34, nowSec = now, locationPerm = LocationPerm.FINE, gpsEnabled = true, networkLocationEnabled = true,
        lastFixAt = now - 30, lastFixAccuracyM = 8.0, backgroundLocation = true, serviceRunning = true,
        batteryExempt = true, powerSaveOn = false, backgroundRestricted = false, networkPresent = true,
        networkValidated = true, server = ServerProbe(true, 120, "ok", null), auth = AuthState.VALID,
        notificationsGranted = true, notificationsEnabled = true,
        channels = listOf(ChannelState("ring_v1", "צלצול", Importance.HIGH), ChannelState("alerts", "התראות", Importance.HIGH)),
        fullScreenAllowed = true, setup = SetupProbe.Ok(setupOk), smsNumbers = 2, smsPermission = true, telephony = true,
        hasAccelerometer = true, hasGyroscope = true, motionDetectionEnabled = true, audioEvidenceEnabled = false,
        micPermission = false,
        fcm = FcmDiagState(true, null, null, FcmTokenRegistry.Status.REGISTERED, null, null),
    )

    private fun eval(s: DiagnosticsSnapshot) = DiagnosticsEvaluator.evaluate(s).associateBy { it.id }

    @Test fun everything_ok_except_camera_unavailable() {
        val r = DiagnosticsEvaluator.evaluate(good())
        val notOk = r.filter { it.status != CheckStatus.OK }
        assertEquals(listOf("camera"), notOk.map { it.id })
        assertEquals(CheckStatus.UNAVAILABLE, notOk[0].status)
        assertEquals("לא בשימוש בגרסת אנדרואיד", notOk[0].detail)
        // מיקרופון לא נבדק כשהקלטת שמע כבויה
        assertFalse(r.any { it.id == "microphone" })
        val sum = DiagnosticsEvaluator.summarize(r)
        assertTrue(sum.allGood); assertEquals("מוכנות: הכל תקין", sum.textHe())
    }

    @Test fun location_checks() {
        var r = eval(good().copy(locationPerm = LocationPerm.NONE))
        assertEquals(CheckStatus.FAIL, r["location_perm"]!!.status)
        assertEquals(FixAction.LOCATION_PERMISSION, r["location_perm"]!!.fix)
        assertEquals(CheckStatus.UNAVAILABLE, r["last_fix"]!!.status)
        assertEquals(CheckStatus.UNAVAILABLE, r["bg_location"]!!.status)

        r = eval(good().copy(locationPerm = LocationPerm.COARSE))
        assertEquals(CheckStatus.WARN, r["location_perm"]!!.status)

        r = eval(good().copy(gpsEnabled = false))
        assertEquals(CheckStatus.WARN, r["location_provider"]!!.status)
        r = eval(good().copy(gpsEnabled = false, networkLocationEnabled = false))
        assertEquals(CheckStatus.FAIL, r["location_provider"]!!.status)
        assertEquals(FixAction.LOCATION_SETTINGS, r["location_provider"]!!.fix)

        r = eval(good().copy(lastFixAt = now - 3 * 3600))
        assertEquals(CheckStatus.WARN, r["last_fix"]!!.status)
        assertTrue(r["last_fix"]!!.detail.contains("3 שע'"))
        assertEquals(CheckStatus.WARN, eval(good().copy(lastFixAt = null))["last_fix"]!!.status)

        r = eval(good().copy(backgroundLocation = false))
        assertEquals(CheckStatus.WARN, r["bg_location"]!!.status)
        assertEquals(FixAction.BACKGROUND_LOCATION, r["bg_location"]!!.fix)
        // אנדרואיד 9: אין הרשאה נפרדת לרקע
        assertEquals(CheckStatus.OK, eval(good().copy(sdkInt = 28, backgroundLocation = false))["bg_location"]!!.status)
    }

    @Test fun background_and_battery() {
        val r = eval(good().copy(serviceRunning = false, batteryExempt = false, powerSaveOn = true, backgroundRestricted = true))
        assertEquals(CheckStatus.FAIL, r["service"]!!.status); assertEquals(FixAction.START_SERVICE, r["service"]!!.fix)
        assertEquals(CheckStatus.WARN, r["battery_opt"]!!.status); assertEquals(FixAction.BATTERY_EXEMPTION, r["battery_opt"]!!.fix)
        assertEquals(CheckStatus.WARN, r["power_saver"]!!.status)
        assertEquals(CheckStatus.FAIL, r["bg_restricted"]!!.status); assertEquals(FixAction.APP_DETAILS, r["bg_restricted"]!!.fix)
        assertEquals(CheckStatus.OK, eval(good().copy(backgroundRestricted = null))["bg_restricted"]!!.status)
    }

    @Test fun network_server_auth() {
        var r = eval(good().copy(networkPresent = false, networkValidated = false, server = null, auth = AuthState.UNKNOWN,
            setup = SetupProbe.NotChecked))
        assertEquals(CheckStatus.FAIL, r["internet"]!!.status)
        assertEquals(CheckStatus.UNAVAILABLE, r["server"]!!.status); assertTrue(r["server"]!!.detail.contains("אין רשת"))
        assertEquals(CheckStatus.UNAVAILABLE, r["auth"]!!.status)
        assertEquals(CheckStatus.UNAVAILABLE, r["contacts"]!!.status)

        assertEquals(CheckStatus.WARN, eval(good().copy(networkValidated = false))["internet"]!!.status)

        r = eval(good().copy(server = ServerProbe(false, null, null, "timeout")))
        assertEquals(CheckStatus.FAIL, r["server"]!!.status); assertTrue(r["server"]!!.detail.contains("timeout"))
        r = eval(good().copy(server = ServerProbe(true, 3500, "ok", null)))
        assertEquals(CheckStatus.WARN, r["server"]!!.status); assertTrue(r["server"]!!.detail.contains("3500 ms"))
        assertEquals(CheckStatus.WARN, eval(good().copy(server = ServerProbe(true, 50, "degraded", null)))["server"]!!.status)
        assertTrue(eval(good())["server"]!!.detail.contains("120 ms"))

        r = eval(good().copy(auth = AuthState.INVALID))
        assertEquals(CheckStatus.FAIL, r["auth"]!!.status); assertEquals(FixAction.RELOGIN, r["auth"]!!.fix)
    }

    @Test fun notifications_channels_fullscreen() {
        var r = eval(good().copy(notificationsGranted = false))
        assertEquals(CheckStatus.FAIL, r["notifications"]!!.status)
        assertEquals(FixAction.NOTIFICATION_PERMISSION, r["notifications"]!!.fix)
        // לפני אנדרואיד 13 אין הרשאה בזמן ריצה - רק חסימה בהגדרות
        r = eval(good().copy(sdkInt = 32, notificationsGranted = false, notificationsEnabled = false))
        assertEquals(FixAction.APP_NOTIFICATION_SETTINGS, r["notifications"]!!.fix)

        r = eval(good().copy(channels = listOf(ChannelState("ring_v1", "צלצול", Importance.DEFAULT),
            ChannelState("alerts", "התראות", Importance.NONE))))
        assertEquals(CheckStatus.WARN, r["channel_ring_v1"]!!.status)
        assertEquals("ring_v1", r["channel_ring_v1"]!!.fixArg)
        assertEquals(CheckStatus.FAIL, r["channel_alerts"]!!.status)

        r = eval(good().copy(fullScreenAllowed = false))
        assertEquals(CheckStatus.FAIL, r["full_screen"]!!.status)
        assertEquals(CheckStatus.OK, eval(good().copy(sdkInt = 33, fullScreenAllowed = false))["full_screen"]!!.status)
    }

    @Test fun contacts_from_setup_endpoint() {
        fun c(s: SetupStatus) = eval(good().copy(setup = SetupProbe.Ok(s)))["contacts"]!!
        assertEquals(CheckStatus.OK, c(setupOk).status)
        assertTrue(c(setupOk).detail.contains("2/2"))
        assertEquals(CheckStatus.FAIL, c(setupOk.copy(contactsAccepted = 0, contactsPending = 0)).status)
        val pending = c(setupOk.copy(contactsAccepted = 0, contactsPending = 3))
        assertEquals(CheckStatus.WARN, pending.status); assertTrue(pending.detail.contains("3 הזמנות"))
        // בדיקת המוכנות האחרונה: אף אחד לא אישר קבלה
        assertEquals(CheckStatus.WARN, c(setupOk.copy(lastReadinessConfirmed = 0)).status)
        assertEquals(CheckStatus.UNAVAILABLE, eval(good().copy(setup = SetupProbe.NotSupported))["contacts"]!!.status)
    }

    @Test fun sms_sensors_microphone() {
        assertEquals(CheckStatus.WARN, eval(good().copy(smsNumbers = 0))["sms"]!!.status)
        assertEquals(CheckStatus.FAIL, eval(good().copy(smsPermission = false))["sms"]!!.status)
        assertEquals(CheckStatus.UNAVAILABLE, eval(good().copy(telephony = false))["sms"]!!.status)

        assertEquals(CheckStatus.WARN, eval(good().copy(hasGyroscope = false))["sensors"]!!.status)
        assertEquals(CheckStatus.UNAVAILABLE, eval(good().copy(hasAccelerometer = false))["sensors"]!!.status)

        val mic = eval(good().copy(audioEvidenceEnabled = true, micPermission = false))["microphone"]!!
        assertEquals(CheckStatus.FAIL, mic.status); assertEquals(FixAction.MIC_PERMISSION, mic.fix)
        assertEquals(CheckStatus.OK, eval(good().copy(audioEvidenceEnabled = true, micPermission = true))["microphone"]!!.status)
    }

    @Test fun summary_text() {
        val r = DiagnosticsEvaluator.evaluate(good().copy(serviceRunning = false, batteryExempt = false, powerSaveOn = true))
        val s = DiagnosticsEvaluator.summarize(r)
        assertEquals(1, s.fail); assertEquals(2, s.warn)
        assertEquals("מוכנות: תקלה אחת · 2 אזהרות - לחץ/י לפרטים", s.textHe())
    }
}

class DiagnosticsApiTest {
    @Test fun health_is_unauthenticated_and_setup_parses_contract() {
        val server = MockWebServer(); server.start()
        try {
            val api = ApiClient(server.url("/").toString().trimEnd('/'), { "tok" })
            server.enqueue(MockResponse().setBody("""{"status":"ok","time":1}"""))
            assertEquals("ok", api.health().getString("status"))
            val h = server.takeRequest()
            assertEquals("/v1/health", h.path); assertNull(h.getHeader("Authorization"))

            server.enqueue(MockResponse().setBody("""{"contacts_accepted":2,"contacts_pending":1,"push_devices":0,
                "phone_verified":true,"volunteer":{"available":true,"verification":"verified"},
                "last_readiness":{"created_at":1700000000.5,"confirmed":1,"total":2},"server_time":1700000100.0}"""))
            val s = api.meSetup()
            assertEquals(2, s.contactsAccepted); assertEquals(1, s.contactsPending); assertTrue(s.phoneVerified)
            assertEquals(true, s.volunteerAvailable); assertEquals("verified", s.volunteerVerification)
            assertEquals(1, s.lastReadinessConfirmed); assertEquals(2, s.lastReadinessTotal)
            val r = server.takeRequest()
            assertEquals("/v1/me/setup", r.path); assertEquals("Bearer tok", r.getHeader("Authorization"))

            server.enqueue(MockResponse().setBody("""{"contacts_accepted":0,"contacts_pending":0,"push_devices":0,
                "phone_verified":false,"volunteer":null,"last_readiness":null,"server_time":1}"""))
            val n = api.meSetup()
            assertNull(n.volunteerAvailable); assertNull(n.lastReadinessTotal)
        } finally { server.shutdown() }
    }
}
