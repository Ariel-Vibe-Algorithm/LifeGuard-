package org.lifeguard.app.service

import android.content.Context
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.api.OsrmWalk
import org.lifeguard.app.core.SafePlace
import org.lifeguard.app.core.SafePlaceChoice
import org.lifeguard.app.core.SafePlaces

/**
 * מקום בטוח קרוב בזמן אירוע (כמו engine._find_safe_route_for_location ב-Windows):
 * 1. offline - מיידי, בלי רשת: הרשימה הארצית המובנית (assets/safe_places/safe_places_il.json), אומדן ×1.3.
 * 2. refine - רק אם יש רשת, אחרי שהתוצאה האופליין כבר מוצגת: אותה רשימה קצרה מדורגת לפי מרחק הליכה אמיתי
 *    (OSRM table) + מסלול הליכה ליעד (OSRM route). כישלון -> null (נשארים עם האומדן המסומן).
 * אין כאן חיפוש מקוונים של מקומות ציבוריים (Overpass/Nominatim) - באנדרואיד רק הרשימה המובנית.
 * worship = העדפת מקום התפילה (WorshipPref, נשמרת מוצפנת במכשיר בלבד): קובעת אילו מקומות תפילה מותר להציע,
 * ורק כשאין רשמי/ציבורי אחר קרוב (SafePlaces.chooseFrom). ברשימה המובנית אין כרגע מקומות תפילה.
 */
object SafePlaceFinder {
    @Volatile private var cache: List<SafePlace>? = null

    data class Result(
        val origin: GeoPoint,
        val shortlist: SafePlaces.Shortlist,
        val choice: SafePlaceChoice?,
        val routeM: Double? = null,
        val routeS: Double? = null,
        val refined: Boolean = false,
    )

    fun bundled(ctx: Context): List<SafePlace> = cache ?: synchronized(this) {
        cache ?: runCatching {
            ctx.assets.open(SafePlaces.ASSET_PATH).use { SafePlaces.parseBundled(String(it.readBytes(), Charsets.UTF_8)) }
        }.getOrDefault(emptyList()).also { if (it.isNotEmpty()) cache = it }
    }

    fun offline(ctx: Context, origin: GeoPoint, worship: String? = null): Result {
        val short = SafePlaces.shortlist(origin.lat, origin.lon, bundled(ctx), worship = worship)
        return Result(origin, short, SafePlaces.chooseFrom(short))
    }

    fun refine(r: Result, osrm: OsrmWalk = OsrmWalk()): Result? {
        if (r.choice == null) return null
        val o = r.origin
        val choice = SafePlaces.chooseFrom(r.shortlist) { places -> osrm.table(o.lat, o.lon, places.map { it.lat to it.lon }) }
            ?: return null
        val route = osrm.route(o.lat, o.lon, choice.place.lat, choice.place.lon)
        if (!choice.walkMeasured && route == null) return null
        return r.copy(choice = choice, routeM = route?.first, routeS = route?.second, refined = true)
    }
}
