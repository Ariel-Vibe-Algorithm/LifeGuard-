package org.lifeguard.app

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.core.FcmTokenRegistry
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/** חוזה ה-HTTP של רישום/ביטול טוקן ה-FCM, שדות ה-FCM ב-/me/setup, וביטול ה-long-poll בהשכמה - מול MockWebServer. */
class FcmApiContractTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient

    @Before fun setUp() {
        server = MockWebServer(); server.start()
        api = ApiClient(server.url("/").toString().trimEnd('/'), { "tok" })
    }

    @After fun tearDown() { server.shutdown() }

    private fun ok(body: String) = MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(body)
    private fun err(code: Int, e: String) = MockResponse().setResponseCode(code).setBody("""{"error":"$e","message":"x"}""")

    @Test fun register_posts_token_and_device_and_reads_server_status() {
        for (status in listOf("active", "not_configured", "misconfigured")) {
            server.enqueue(ok("""{"ok":true,"fcm":"$status"}"""))
            val r = api.registerFcm("tok_" + "x".repeat(40), "Android 15")
            assertTrue(r.ok); assertEquals(status, r.serverFcm)
            val req = server.takeRequest()
            assertEquals("POST", req.method)
            assertEquals("/v1/me/devices/fcm", req.path)
            assertEquals("Bearer tok", req.getHeader("Authorization"))
            val b = JSONObject(req.body.readUtf8())
            assertEquals(setOf("token", "device"), b.keys().asSequence().toSet())
            assertEquals("tok_" + "x".repeat(40), b.getString("token"))
            assertEquals("Android 15", b.getString("device"))
        }
        // שרת בלי שדה fcm
        server.enqueue(ok("""{"ok":true}"""))
        assertNull(api.registerFcm("t".repeat(30), "d").serverFcm)
        server.takeRequest()
    }

    @Test fun register_errors_map_to_queue_failures() {
        server.enqueue(err(400, "invalid_token"))
        try { api.registerFcm("short", "d"); fail() } catch (e: ApiException) {
            assertEquals(FcmTokenRegistry.Failure.Permanent("invalid_token"), FcmTokenRegistry.Failure.classify(e.status, e.code))
        }
        server.enqueue(err(404, "not_found"))
        try { api.registerFcm("t".repeat(30), "d"); fail() } catch (e: ApiException) {
            assertEquals(FcmTokenRegistry.Failure.Unsupported, FcmTokenRegistry.Failure.classify(e.status, e.code))
        }
        server.enqueue(err(401, "unauthorized"))
        try { api.registerFcm("t".repeat(30), "d"); fail() } catch (e: ApiException) {
            assertTrue(FcmTokenRegistry.Failure.classify(e.status, e.code) is FcmTokenRegistry.Failure.Retry)
        }
    }

    @Test fun unregister_sends_delete_with_json_body() {
        server.enqueue(ok("""{"ok":true}"""))
        api.unregisterFcm("t".repeat(30))
        val req = server.takeRequest()
        assertEquals("DELETE", req.method)
        assertEquals("/v1/me/devices/fcm", req.path)
        assertEquals("Bearer tok", req.getHeader("Authorization"))
        assertEquals("t".repeat(30), JSONObject(req.body.readUtf8()).getString("token"))
    }

    @Test fun me_setup_contains_fcm_fields() {
        server.enqueue(ok("""{"contacts_accepted":2,"contacts_pending":0,"push_devices":0,"fcm_devices":1,"fcm":"not_configured",
            "phone_verified":false,"volunteer":null,"last_readiness":null}"""))
        val s = api.meSetup()
        assertEquals(1, s.fcmDevices); assertEquals("not_configured", s.fcm)
        assertEquals("/v1/me/setup", server.takeRequest().path)
    }

    /** השכמה: ביטול long-poll תקוע מחזיר IOException מיד, ולא נוגע בבקשות אחרות (למשל SOS) שבדרך. */
    @Test fun cancel_poll_aborts_only_the_long_poll() {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))      // long-poll "תקוע" (חיבור מת)
        val pollError = AtomicReference<Throwable?>()
        val done = CountDownLatch(1)
        val t = Thread {
            try { api.poll(0) } catch (e: Throwable) { pollError.set(e) } finally { done.countDown() }
        }
        t.start()
        server.takeRequest(5, TimeUnit.SECONDS)                       // ה-poll הגיע לשרת
        Thread.sleep(100)
        val t0 = System.nanoTime()
        api.cancelPoll()
        assertTrue("ה-poll לא בוטל", done.await(5, TimeUnit.SECONDS))
        assertTrue(pollError.get() is IOException)
        assertTrue((System.nanoTime() - t0) / 1_000_000 < 5000)
        // בקשה רגילה אחרי הביטול עובדת (dispatcher נפרד)
        server.enqueue(ok("""{"ok":true,"fcm":"active"}"""))
        assertEquals("active", api.registerFcm("t".repeat(30), "d").serverFcm)
        // ו-poll חדש אחרי הביטול עובד
        server.enqueue(ok("""{"notifications":[]}"""))
        assertTrue(api.poll(0, waitSeconds = 0).isEmpty())
        assertFalse(t.isAlive)
    }
}
