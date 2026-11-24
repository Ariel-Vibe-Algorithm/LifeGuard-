package org.lifeguard.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.core.FcmServerStatus
import org.lifeguard.app.core.FcmTokenRegistry
import java.util.UUID

/**
 * רישום / ביטול טוקן FCM מול השרת האמיתי (רק עם LIFEGUARD_IT_URL). הטוקן מזויף - לא נשלחת שום הודעת FCM אמיתית,
 * ובלי LIFEGUARD_FCM_SERVICE_ACCOUNT השרת עונה "not_configured".
 */
class RealServerFcmIT {
    private val url = System.getProperty("lifeguard.it.url").orEmpty()

    @Test fun registerAndUnregisterFcmTokenAgainstRealServer() {
        assumeTrue("LIFEGUARD_IT_URL not set", url.isNotEmpty())
        val name = "fcm" + UUID.randomUUID().toString().take(8)
        val anon = ApiClient(url, { null })
        anon.register(name, "password123", "שם fcm")
        val t = anon.login(name, "password123", "jvm-it").token
        val api = ApiClient(url, { t })

        val before = api.meSetup()
        assertEquals(0, before.fcmDevices)
        assertTrue(before.fcm in setOf(FcmServerStatus.ACTIVE, FcmServerStatus.NOT_CONFIGURED, FcmServerStatus.MISCONFIGURED))

        val token = "it_fake_fcm_token_" + UUID.randomUUID().toString().replace("-", "")
        val r = api.registerFcm(token, "jvm-it")
        assertTrue(r.ok)
        assertEquals(before.fcm, r.serverFcm)
        api.registerFcm(token, "jvm-it")                        // אידמפוטנטי
        assertEquals(1, api.meSetup().fcmDevices)

        // טוקן לא תקין -> 400 -> כשל קבוע בתור
        try { api.registerFcm("short", "jvm-it"); fail("expected 400") } catch (e: ApiException) {
            assertEquals(400, e.status)
            assertTrue(FcmTokenRegistry.Failure.classify(e.status, e.code) is FcmTokenRegistry.Failure.Permanent)
        }

        api.unregisterFcm(token)
        assertEquals(0, api.meSetup().fcmDevices)
        api.unregisterFcm(token)                                // כבר לא קיים - עדיין ok
    }
}
