package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.lifeguard.app.api.SetupStatus
import org.lifeguard.app.core.AesGcmSealer
import org.lifeguard.app.core.AuthState
import org.lifeguard.app.core.ChannelState
import org.lifeguard.app.core.CheckStatus
import org.lifeguard.app.core.DiagnosticsEvaluator
import org.lifeguard.app.core.DiagnosticsSnapshot
import org.lifeguard.app.core.FcmConfig
import org.lifeguard.app.core.FcmDiagState
import org.lifeguard.app.core.FcmTokenRegistry
import org.lifeguard.app.core.FcmTokenRegistry.Failure
import org.lifeguard.app.core.FcmTokenRegistry.Status
import org.lifeguard.app.core.FixAction
import org.lifeguard.app.core.Importance
import org.lifeguard.app.core.LocationPerm
import org.lifeguard.app.core.MemoryTextStore
import org.lifeguard.app.core.SealedFileTextStore
import org.lifeguard.app.core.ServerProbe
import org.lifeguard.app.core.SetupProbe
import org.lifeguard.app.core.WakeMessage
import org.lifeguard.app.core.WakePolicy
import java.io.File
import javax.crypto.KeyGenerator

/** FCM כערוץ השכמה: הגדרה, תור רישום הטוקן, פענוח ההשכמה והחלטה, ובדיקת המוכנות. */
class FcmLogicTest {
    @get:Rule val tmp = TemporaryFolder()
    private var now = 1_700_000_000.0
    private val acct = FcmTokenRegistry.accountKey("https://lg.example.org", "dana")
    private val token = "fcm_token_" + "a".repeat(140)

    private fun registry(store: org.lifeguard.app.core.TextStore = MemoryTextStore()) = FcmTokenRegistry(store) { now }

    // ------------------------------------------------------------ הגדרה
    private val good = FcmConfig("AIzaSyA1234567890abcdefghijklmnopqrstu", "1:123456789012:android:0a1b2c3d4e5f",
        "lifeguard-12345", "123456789012")

    @Test fun config_validation() {
        assertNull(good.problem()); assertTrue(good.isValid)
        val empty = FcmConfig("", "", "", "")
        assertTrue(empty.isEmpty); assertEquals(FcmConfig.NOT_CONFIGURED, empty.problem())
        assertTrue(good.copy(projectId = "").problem()!!.contains("fcm.projectId"))
        // ערכים מהקובץ לדוגמה = לא מוגדר
        assertTrue(good.copy(apiKey = "REPLACE_WITH_WEB_API_KEY").problem()!!.contains("לדוגמה"))
        assertTrue(good.copy(senderId = "12ab").problem()!!.contains("senderId"))
        assertTrue(good.copy(appId = "1:123:ios:abc").problem()!!.contains("fcm.appId"))
        // appId של פרויקט אחר
        assertTrue(good.copy(appId = "1:999999999999:android:0a1b2c").problem()!!.contains("פרויקט אחר"))
        assertTrue(good.copy(projectId = "Bad_Project").problem()!!.contains("projectId"))
        assertTrue(good.copy(apiKey = "short").problem()!!.contains("apiKey"))
    }

    // ------------------------------------------------------------ תור הרישום
    @Test fun new_token_is_due_then_registered() {
        val r = registry()
        assertEquals(Status.NO_TOKEN, r.status(acct))
        assertNull(r.due(acct))
        r.onNewToken(token)
        assertEquals(Status.PENDING, r.status(acct))
        assertEquals(token, r.due(acct))
        assertNull("לא מחובר/ת -> לא רושמים", r.due(null))
        r.onRegistered(token, acct, "not_configured")
        assertEquals(Status.REGISTERED, r.status(acct))
        assertNull(r.due(acct))
        assertEquals("not_configured", r.state().serverFcm)
        // אותו טוקן שוב (getToken בכל עלייה) - לא מאפס את הרישום
        r.onNewToken(token)
        assertEquals(Status.REGISTERED, r.status(acct))
        // טוקן מתחלף (onNewToken) -> רישום מחדש
        r.onNewToken(token + "b")
        assertEquals(Status.PENDING, r.status(acct))
        assertEquals(token + "b", r.due(acct))
    }

    @Test fun network_failure_backs_off_and_force_overrides() {
        val r = registry()
        r.onNewToken(token)
        r.onFailure(token, acct, Failure.classify(null, null))
        assertEquals("network", r.state().lastError)
        assertNull("backoff 30 שנ'", r.due(acct))
        assertEquals(token, r.due(acct, force = true))
        now += 31; assertEquals(token, r.due(acct))
        r.onFailure(token, acct, Failure.classify(503, "unavailable"))
        now += 31; assertNull("ניסיון שני: 60 שנ'", r.due(acct))
        now += 30; assertEquals(token, r.due(acct))
        assertEquals(Status.PENDING, r.status(acct))
        // תקרה
        assertEquals(30.0, FcmTokenRegistry.backoff(1), 0.0)
        assertEquals(60.0, FcmTokenRegistry.backoff(2), 0.0)
        assertEquals(FcmTokenRegistry.MAX_BACKOFF_SECONDS, FcmTokenRegistry.backoff(40), 0.0)
    }

    @Test fun failure_classification() {
        assertEquals(Failure.Retry("auth"), Failure.classify(401, "unauthorized"))
        assertEquals(Failure.Retry("http_429"), Failure.classify(429, null))
        assertEquals(Failure.Unsupported, Failure.classify(404, "not_found"))
        assertEquals(Failure.Permanent("invalid_token"), Failure.classify(400, "invalid_token"))
    }

    @Test fun permanent_failure_waits_for_new_token_and_404_retries_slowly() {
        val r = registry()
        r.onNewToken(token)
        r.onFailure(token, acct, Failure.Permanent("invalid_token"))
        assertEquals(Status.FAILED, r.status(acct))
        assertNull(r.due(acct, force = true))
        // חשבון אחר - ננסה שם
        val other = FcmTokenRegistry.accountKey("https://lg.example.org", "noa")
        assertEquals(token, r.due(other))
        r.onNewToken(token + "x")
        assertEquals(Status.PENDING, r.status(acct))

        r.onFailure(token + "x", acct, Failure.Unsupported)
        now += 3600; assertNull(r.due(acct))
        now += FcmTokenRegistry.UNSUPPORTED_RETRY_SECONDS; assertEquals(token + "x", r.due(acct))
    }

    @Test fun stale_results_for_old_token_are_ignored() {
        val r = registry()
        r.onNewToken(token)
        r.onNewToken(token + "2")                       // התחלף בזמן שהרישום של הישן היה בדרך
        r.onRegistered(token, acct, "active")
        assertEquals(Status.PENDING, r.status(acct))
        r.onFailure(token, acct, Failure.Permanent("x"))
        assertEquals(Status.PENDING, r.status(acct))
    }

    @Test fun account_change_requires_reregistration_and_clear_returns_token() {
        val r = registry()
        r.onNewToken(token); r.onRegistered(token, acct, "active")
        val otherServer = FcmTokenRegistry.accountKey("https://other.example.org", "dana")
        assertNotEquals(acct, otherServer)
        assertEquals(Status.PENDING, r.status(otherServer))
        assertEquals(token, r.due(otherServer))
        // השרת לא מכיר מכשירים -> רישום מחדש
        r.markStale()
        assertEquals(Status.PENDING, r.status(acct))
        r.onRegistered(token, acct, "active")
        assertEquals(token, r.clear())
        assertEquals(Status.NO_TOKEN, r.status(acct))
        assertNull(r.clear())
        // שם משתמש לא נשמר גלוי; לא תלוי רישיות
        assertEquals(acct, FcmTokenRegistry.accountKey("https://LG.example.org/", "Dana"))
        assertFalse(acct.contains("dana"))
    }

    @Test fun registry_is_encrypted_at_rest() {
        val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        val f = File(tmp.root, "fcm/registration.bin")
        val r = registry(SealedFileTextStore(f, AesGcmSealer { key }))
        r.onNewToken(token); r.onFailure(token, acct, Failure.Retry("network"))
        val raw = String(f.readBytes(), Charsets.ISO_8859_1)
        assertFalse("הטוקן לא נשמר גלוי", raw.contains("fcm_token_"))
        assertFalse(raw.contains("network"))
        // שורד "אתחול"
        val r2 = registry(SealedFileTextStore(f, AesGcmSealer { key }))
        assertEquals(token, r2.state().token); assertEquals(1, r2.state().attempts)
        // מפתח אחר -> ריק (לא קורס)
        val other = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        assertEquals(Status.NO_TOKEN, registry(SealedFileTextStore(f, AesGcmSealer { other })).status(acct))
        r2.clear(); assertFalse(f.exists())
    }

    // ------------------------------------------------------------ השכמה
    @Test fun wake_message_parsing_is_strict_and_content_free() {
        assertEquals(WakeMessage("incident_new", true), WakeMessage.parse(mapOf("t" to "wake", "k" to "incident_new", "u" to "1")))
        assertEquals(WakeMessage("chat_message", false), WakeMessage.parse(mapOf("t" to "wake", "k" to "chat_message", "u" to "0")))
        assertNull(WakeMessage.parse(emptyMap()))
        assertNull(WakeMessage.parse(mapOf("t" to "notify", "k" to "incident_new")))
        // סוג חשוד / ארוך -> "unknown" (לא נכנס ללוג/לשום מקום כפי שהוא); u חסר = לא דחוף
        assertEquals(WakeMessage("unknown", false), WakeMessage.parse(mapOf("t" to "wake", "k" to "<script>")))
        assertEquals("unknown", WakeMessage.parse(mapOf("t" to "wake", "k" to "a".repeat(33)))!!.kind)
        // שדות נוספים (גם אם שרת עתידי ישלח) לא נכנסים למודל בכלל
        val m = WakeMessage.parse(mapOf("t" to "wake", "k" to "incident_new", "u" to "1", "name" to "דנה", "lat" to "32.1"))!!
        assertEquals(setOf("kind", "urgent"), WakeMessage::class.java.declaredFields
            .filter { !java.lang.reflect.Modifier.isStatic(it.modifiers) }.map { it.name }.toSet())
        assertTrue(m.urgent)
    }

    @Test fun wake_decision() {
        val urgent = WakeMessage("incident_new", true)
        val normal = WakeMessage("chat_message", false)
        assertEquals(WakePolicy.Action.IGNORE, WakePolicy.decide(null, loggedIn = true, serviceRunning = false).action)
        WakePolicy.decide(urgent, loggedIn = false, serviceRunning = false).let {
            assertEquals(WakePolicy.Action.IGNORE, it.action); assertEquals("logged_out", it.reason); assertEquals(0L, it.wakeLockMs)
        }
        WakePolicy.decide(urgent, loggedIn = true, serviceRunning = false).let {
            assertEquals(WakePolicy.Action.START_SERVICE, it.action); assertEquals(WakePolicy.URGENT_WAKELOCK_MS, it.wakeLockMs)
        }
        WakePolicy.decide(normal, loggedIn = true, serviceRunning = true).let {
            assertEquals(WakePolicy.Action.KICK_RUNNING_SERVICE, it.action); assertEquals(WakePolicy.NORMAL_WAKELOCK_MS, it.wakeLockMs)
        }
        // שירות החזית נחסם (Android 12+) -> משיכה חד-פעמית; expedited רק לדחוף
        assertEquals(WakePolicy.Fallback.EXPEDITED_POLL, WakePolicy.fallback(urgent))
        assertEquals(WakePolicy.Fallback.POLL, WakePolicy.fallback(normal))
    }

    // ------------------------------------------------------------ בדיקת מוכנות
    private val fcmOk = FcmDiagState(true, null, null, Status.REGISTERED, null, now - 120)
    private fun setup(fcm: String?, devices: Int?) =
        SetupStatus(2, 0, 0, true, null, null, null, null, null, now, fcmDevices = devices, fcm = fcm)

    private fun snap(fcm: FcmDiagState, setup: SetupProbe) = DiagnosticsSnapshot(
        sdkInt = 34, nowSec = now, locationPerm = LocationPerm.FINE, gpsEnabled = true, networkLocationEnabled = true,
        lastFixAt = now - 30, lastFixAccuracyM = 8.0, backgroundLocation = true, serviceRunning = true,
        batteryExempt = true, powerSaveOn = false, backgroundRestricted = false, networkPresent = true,
        networkValidated = true, server = ServerProbe(true, 120, "ok", null), auth = AuthState.VALID,
        notificationsGranted = true, notificationsEnabled = true,
        channels = listOf(ChannelState("ring_v1", "צלצול", Importance.HIGH)),
        fullScreenAllowed = true, setup = setup, smsNumbers = 2, smsPermission = true, telephony = true,
        hasAccelerometer = true, hasGyroscope = true, motionDetectionEnabled = true, audioEvidenceEnabled = false,
        micPermission = false, fcm = fcm,
    )

    private fun check(fcm: FcmDiagState, setup: SetupProbe) = DiagnosticsEvaluator.evaluate(snap(fcm, setup)).single { it.id == "fcm" }

    @Test fun diagnostics_fcm_unavailable_without_app_config() {
        val none = FcmDiagState(false, FcmConfig.NOT_CONFIGURED, null, Status.NO_TOKEN, null, null)
        val c = check(none, SetupProbe.Ok(setup("active", 1)))
        assertEquals(CheckStatus.UNAVAILABLE, c.status)
        assertEquals("FCM / השכמה", c.title)
        assertTrue(c.detail, c.detail.startsWith("FCM לא מוגדר"))
        assertTrue(c.detail.contains("firebase.properties"))
        assertNull(c.fix)
        // קובץ עם ערך לדוגמה -> עדיין "לא מוגדר" + הסיבה
        val bad = none.copy(configProblem = "fcm.apiKey עדיין ערך לדוגמה - להעתיק מקונסולת Firebase")
        assertTrue(check(bad, SetupProbe.NotChecked).detail.startsWith("FCM לא מוגדר: fcm.apiKey"))
    }

    @Test fun diagnostics_fcm_warn_when_server_lacks_credentials_or_no_devices() {
        var c = check(fcmOk, SetupProbe.Ok(setup("not_configured", 1)))
        assertEquals(CheckStatus.WARN, c.status); assertTrue(c.detail.contains("LIFEGUARD_FCM_SERVICE_ACCOUNT"))
        c = check(fcmOk, SetupProbe.Ok(setup("misconfigured", 1)))
        assertEquals(CheckStatus.WARN, c.status); assertTrue(c.detail.contains("misconfigured"))
        c = check(fcmOk, SetupProbe.Ok(setup("active", 0)))
        assertEquals(CheckStatus.WARN, c.status); assertEquals(FixAction.RETRY, c.fix)
        // שרת ישן בלי שדות FCM
        assertEquals(CheckStatus.WARN, check(fcmOk, SetupProbe.Ok(setup(null, null))).status)
        // המכשיר לא רשום / נדחה / בלי טוקן
        c = check(fcmOk.copy(registration = Status.PENDING, lastError = "network"), SetupProbe.Ok(setup("active", 1)))
        assertEquals(CheckStatus.WARN, c.status); assertTrue(c.detail.contains("network"))
        assertEquals(CheckStatus.WARN, check(fcmOk.copy(registration = Status.FAILED), SetupProbe.Ok(setup("active", 1))).status)
        assertTrue(check(fcmOk.copy(registration = Status.NO_TOKEN), SetupProbe.Ok(setup("active", 1))).detail.contains("Google Play"))
        // אתחול Firebase נכשל
        assertEquals(CheckStatus.WARN, check(fcmOk.copy(initError = "IllegalArgumentException"), SetupProbe.Ok(setup("active", 1))).status)
        // לא נבדק מול השרת
        assertEquals(CheckStatus.UNAVAILABLE, check(fcmOk, SetupProbe.NotChecked).status)
        assertEquals(CheckStatus.UNAVAILABLE, check(fcmOk, SetupProbe.Error("x")).status)
    }

    @Test fun diagnostics_fcm_ok_when_server_active_and_device_registered() {
        val c = check(fcmOk, SetupProbe.Ok(setup("active", 2)))
        assertEquals(CheckStatus.OK, c.status)
        assertTrue(c.detail, c.detail.contains("השכמה אחרונה לפני 2 דק'"))
    }

    @Test fun setup_status_parses_fcm_fields() {
        val s = SetupStatus.parse(org.json.JSONObject("""{"contacts_accepted":1,"fcm_devices":2,"fcm":"active"}"""))
        assertEquals(2, s.fcmDevices); assertEquals("active", s.fcm)
        val old = SetupStatus.parse(org.json.JSONObject("""{"contacts_accepted":1}"""))
        assertNull(old.fcmDevices); assertNull(old.fcm)
        assertEquals("misconfigured", SetupStatus.parse(org.json.JSONObject("""{"fcm":"weird"}""")).fcm)
    }
}
