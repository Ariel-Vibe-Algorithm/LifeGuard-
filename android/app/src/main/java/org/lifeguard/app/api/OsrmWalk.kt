package org.lifeguard.app.api

import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * מרחק/זמן הליכה אמיתיים - OSRM עם פרופיל הליכה (שרת FOSSGIS של OpenStreetMap Germany, כמו map_module.py:
 * router.project-osrm.org מריץ רק רכב). שיפור בלבד: המקום הבטוח האופליין מוצג קודם ולא מחכה לזה לעולם.
 * כל כישלון -> null (נשארים עם האומדן, שמסומן "אומדן").
 */
class OsrmWalk(
    private val baseUrl: String = DEFAULT_BASE,
    private val http: OkHttpClient = defaultHttp(),
    private val userAgent: String = DEFAULT_UA,
) {
    /** _walk_table: בקשה אחת לכל המועמדים -> [(מטרים, שניות) או null לכל יעד], או null בכישלון. */
    fun table(lat: Double, lon: Double, dests: List<Pair<Double, Double>>): List<Pair<Double, Double>?>? {
        if (dests.isEmpty()) return emptyList()
        val coords = (listOf(lat to lon) + dests).joinToString(";") { (la, lo) -> "${f(lo)},${f(la)}" }
        val body = get("$baseUrl/table/v1/foot/$coords?sources=0&annotations=duration,distance") ?: return null
        return parseTable(body, dests.size)
    }

    /** מסלול הליכה (מרחק ומשך בלבד - המסלול עצמו מוצג באפליקציית המפות). */
    fun route(fromLat: Double, fromLon: Double, toLat: Double, toLon: Double): Pair<Double, Double>? {
        val body = get("$baseUrl/route/v1/foot/${f(fromLon)},${f(fromLat)};${f(toLon)},${f(toLat)}?overview=false")
            ?: return null
        return parseRoute(body)
    }

    private fun get(url: String): String? = try {
        http.newCall(Request.Builder().url(url).header("User-Agent", userAgent).build()).execute().use { r ->
            if (r.isSuccessful) r.body?.string() else null
        }
    } catch (e: Exception) {
        null
    }

    companion object {
        const val DEFAULT_BASE = "https://routing.openstreetmap.de/routed-foot"
        val DEFAULT_UA = "LifeGuard-Android/${org.lifeguard.app.BuildConfig.VERSION_NAME} (personal safety app)"

        private fun f(x: Double) = "%.6f".format(Locale.US, x)

        fun defaultHttp(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(4, TimeUnit.SECONDS).readTimeout(6, TimeUnit.SECONDS).callTimeout(8, TimeUnit.SECONDS).build()

        fun parseTable(body: String, n: Int): List<Pair<Double, Double>?>? = try {
            val o = JSONObject(body)
            if (o.optString("code") != "Ok") null
            else {
                val dist = o.getJSONArray("distances").getJSONArray(0)
                val dur = o.getJSONArray("durations").getJSONArray(0)
                (1..n).map { i ->
                    if (i >= dist.length() || i >= dur.length() || dist.isNull(i) || dur.isNull(i)) null
                    else dist.getDouble(i) to dur.getDouble(i)
                }
            }
        } catch (e: Exception) {
            null
        }

        fun parseRoute(body: String): Pair<Double, Double>? = try {
            val o = JSONObject(body)
            val routes = o.optJSONArray("routes")
            if (o.optString("code") != "Ok" || routes == null || routes.length() == 0) null
            else routes.getJSONObject(0).let { it.getDouble("distance") to it.getDouble("duration") }
        } catch (e: Exception) {
            null
        }
    }
}
