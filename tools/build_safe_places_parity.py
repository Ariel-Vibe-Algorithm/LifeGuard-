# -*- coding: utf-8 -*-
"""
build_safe_places_parity.py - קובץ תאימות לבדיקת ה-JVM של אנדרואיד (SafePlacesParityTest).

מריץ את המימוש המקורי (map_module._choose, is_er_hospital) על הרשימה המובנית safe_places/safe_places_il.json -
המסלול האופליין בלבד: בלי רשת (walk=False -> הליכה = קו אווירי × 1.3) ובלי מטמון מקומי (באנדרואיד אין מטמון;
וכך הקובץ לא תלוי בתיקיית data/). התוצאה נכתבת ל-android/app/src/test/resources/safe_places_parity.json.

    python -m tools.build_safe_places_parity

לא פונה לרשת ולא כותב לשום מקום חוץ מקובץ התאימות.
"""

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
import map_module  # noqa: E402

OUT = os.path.join(ROOT, "android", "app", "src", "test", "resources", "safe_places_parity.json")

# ערים ונקודות ידועות (כולל קצוות: מדבר, ים, גבול) - מכסה תחנות כיבוי רשמיות, משטרה ובתי חולים עם/בלי מיון
NAMED_POINTS = [
    ("tel_aviv_center", 32.0809, 34.7806), ("tel_aviv_south", 32.0550, 34.7700), ("jaffa", 32.0500, 34.7550),
    ("jerusalem_center", 31.7800, 35.2200), ("jerusalem_old_city", 31.7767, 35.2345), ("jerusalem_ein_karem", 31.7650, 35.1600),
    ("haifa_downtown", 32.8150, 34.9980), ("haifa_carmel", 32.7940, 34.9890), ("beer_sheva", 31.2520, 34.7910),
    ("eilat", 29.5577, 34.9519), ("netanya", 32.3215, 34.8532), ("herzliya", 32.1663, 34.8436),
    ("rishon_lezion", 31.9730, 34.7925), ("petah_tikva", 32.0870, 34.8870), ("ashdod", 31.8044, 34.6553),
    ("ashkelon", 31.6688, 34.5743), ("holon", 32.0158, 34.7874), ("bat_yam", 32.0171, 34.7454),
    ("ramat_gan_sheba", 32.0460, 34.8420), ("kfar_saba", 32.1750, 34.9070), ("hadera", 32.4340, 34.9196),
    ("afula", 32.6080, 35.2890), ("nazareth", 32.7019, 35.2972), ("tiberias", 32.7922, 35.5312),
    ("safed", 32.9646, 35.4960), ("nahariya", 33.0058, 35.0940), ("kiryat_shmona", 33.2073, 35.5702),
    ("dimona", 31.0700, 35.0330), ("mitzpe_ramon", 30.6100, 34.8010), ("arava_desert", 30.3000, 35.1500),
    ("negev_open", 30.9000, 34.6000), ("mediterranean_sea", 32.3000, 34.3000), ("dead_sea_ein_bokek", 31.2000, 35.3630),
    ("golan_katzrin", 32.9930, 35.6900), ("modiin", 31.8980, 35.0100), ("rehovot", 31.8940, 34.8110),
    ("umm_al_fahm", 32.5190, 35.1530), ("ofakim", 31.3140, 34.6200), ("beit_shemesh", 31.7470, 34.9880),
    ("sderot", 31.5250, 34.5960),
]


def choose_offline(lat, lon, candidates, radius_m=None):
    radius_m = radius_m or config.SAFE_PLACE_SEARCH_RADIUS_M
    return map_module._choose(lat, lon, candidates, config.SAFE_PLACE_CATEGORIES, radius_m, walk=False)


def result_json(r):
    if r is None:
        return None
    out = {"name": r["name"], "type": r["type"], "type_label": r["type_label"], "lat": r["lat"], "lon": r["lon"],
           "source": r["source"], "distance_m": r["distance_m"], "walk_m": r["walk_m"],
           "walk_measured": r["walk_measured"]}
    if "walk_s" in r:
        out["walk_s"] = r["walk_s"]
    return out


def fake_walk_table(lat, lon, places, timeout=6):
    """טבלת הליכה דטרמיניסטית במקום OSRM (בלי רשת) - לבדיקת ענף walk=True. אותה נוסחה בבדיקת ה-Kotlin:
    פריט i: None אם i % 4 == 2, אחרת מטרים = קו אווירי × (2.6 - ((7i) % 5) × 0.4), שניות = מטרים / 1.35."""
    out = []
    for i, p in enumerate(places):
        if i % 4 == 2:
            out.append(None)
            continue
        m = map_module._haversine_m(lat, lon, p["lat"], p["lon"]) * (2.6 - ((7 * i) % 5) * 0.4)
        out.append((m, m / 1.35))
    return out


SYN_BASE = (32.0, 34.8)
SYN_M_LAT = 1 / 111195.0             # ~1 מ' בקו רוחב


def _at(dn, de, **kw):
    """מועמד מלאכותי dn מ' צפונה ו-de מ' מזרחה מנקודת הבסיס."""
    import math
    base_lat, base_lon = SYN_BASE
    p = {"lat": base_lat + dn * SYN_M_LAT, "lon": base_lon + de * SYN_M_LAT / math.cos(math.radians(base_lat)),
         "emergency": None, "source": "synthetic"}
    p.update(kw)
    p.setdefault("type_label", {c["id"]: c["label"] for c in config.SAFE_PLACE_CATEGORIES}.get(p["type"], p["type"]))
    return p


def synthetic_cases():
    """מועמדים מלאכותיים שמפעילים את כל הענפים של _choose (ציבורי קרוב, רשמי רחוק, כפילויות, בי"ח בלי מיון)."""
    base_lat, base_lon = SYN_BASE
    at = _at

    cases = [
        ("official_close_beats_public", [at(1500, 0, name="תחנה", type="police"), at(200, 0, name="סופר", type="supermarket")]),
        ("public_when_official_far", [at(4000, 0, name="תחנה רחוקה", type="police"), at(500, 0, name="בית מרקחת", type="pharmacy")]),
        ("public_too_far_official_far", [at(4000, 0, name="תחנה רחוקה", type="police"), at(1300, 0, name="דלק", type="fuel")]),
        ("only_public_far", [at(1300, 0, name="דלק", type="fuel")]),
        ("only_public_near", [at(900, 0, name="מתנ\"ס", type="community_centre")]),
        ("nothing_in_radius", [at(6000, 0, name="רחוק", type="police")]),
        ("non_er_hospital_skipped", [at(100, 0, name="מרכז לבריאות הנפש", type="hospital"),
                                     at(1800, 0, name="כיבוי", type="fire_station")]),
        ("er_by_tag", [at(300, 0, name="בית חולים כלשהו", type="hospital", emergency="yes"),
                       at(200, 0, name="בית חולים בלי תג", type="hospital")]),
        ("er_tag_no_overrides_name", [at(300, 0, name="שיבא", type="hospital", emergency="no"),
                                      at(2500, 0, name="כיבוי", type="fire_station")]),
        ("er_by_name", [at(700, 0, name="המרכז הרפואי סורוקה", type="hospital"), at(600, 0, name="משטרה", type="police")]),
        ("duplicates_removed", [at(500, 0, name="א", type="police"), at(500.4, 0.3, name="ב", type="police"),
                                at(800, 0, name="ג", type="fire_station")]),
        ("unknown_type_ignored", [at(50, 0, name="?", type="nightclub"), at(1000, 0, name="תחנה", type="police")]),
        ("seven_officials_shortlist", [at(300 + 100 * i, 0, name=f"ת{i}", type="police") for i in range(8)]),
        ("tie_keeps_input_order", [at(0, 1000, name="מזרח", type="police"), at(0, -1000, name="מערב", type="fire_station")]),
        ("official_exactly_2000", [at(2000, 0, name="גבול", type="police"), at(600, 0, name="סופר", type="supermarket")]),
        ("empty", []),
    ]
    out = []
    for cid, cands in cases:
        out.append({"id": cid, "lat": base_lat, "lon": base_lon, "radius_m": config.SAFE_PLACE_SEARCH_RADIUS_M,
                    "candidates": cands, "expected": result_json(choose_offline(base_lat, base_lon, cands))})
    return out


def worship_cases():
    """מקום תפילה כמפלט אחרון (worship_pref + _choose): כל שכבה, כל העדפה, וענף walk=True (טבלה דטרמיניסטית).
    "worship" = ההעדפה שנמסרה (None = ברירת המחדל); categories = worship_pref.categories_for(worship)."""
    import worship_pref
    base_lat, base_lon = SYN_BASE
    at = _at
    syn, mosque, church = (at(220, 0, name="בית כנסת קרוב", type="synagogue"),
                           at(330, 0, name="מסגד קרוב", type="mosque"), at(440, 0, name="כנסייה קרובה", type="church"))
    police_far = at(3300, 0, name="תחנה רחוקה", type="police")
    all3 = [syn, mosque, church, police_far]
    offline = [
        ("worship_only_when_nothing_else", "synagogue", [syn, police_far]),
        ("public_beats_worship", "synagogue", [syn, at(670, 0, name="בית מרקחת", type="pharmacy"), police_far]),
        ("official_beats_worship", "synagogue", [syn, at(1100, 0, name="משטרה", type="police")]),
        ("pref_mosque", "mosque", all3),
        ("pref_church", "church", all3),
        ("pref_any_nearest", "any", all3),
        ("pref_none_official_far", "none", all3),
        ("pref_default", None, all3),
        ("pref_unknown_is_default", "sector_x", all3),
        ("other_kind_only_none", "synagogue", [mosque]),
        ("pref_none_only_worship", "none", [syn, church]),
        ("worship_beyond_public_radius", "any", [at(1300, 0, name="מסגד רחוק", type="mosque"), police_far]),
        ("worship_only_far", "any", [at(1300, 0, name="מסגד רחוק", type="mosque")]),
        ("worship_shortlist_3", "any", [at(300 + 100 * i, 0, name=f"מ{i}", type=("synagogue", "mosque", "church")[i % 3])
                                        for i in range(5)]),
        ("worship_dupes_same_type", "any", [at(500, 0, name="א", type="church"), at(500.4, 0.3, name="ב", type="church"),
                                            at(500, 0, name="ג", type="mosque")]),
        ("non_er_hospital_then_worship", "any", [at(100, 0, name="מרכז לבריאות הנפש", type="hospital"),
                                                 at(500, 0, name="כנסייה", type="church")]),
        ("public_too_far_worship_near", "any", [at(1300, 0, name="דלק", type="fuel"), at(800, 0, name="מסגד", type="mosque"),
                                                police_far]),
        ("worship_tie_keeps_input_order", "any", [at(0, 600, name="מזרח", type="mosque"),
                                                  at(0, -600, name="מערב", type="synagogue")]),
    ]
    walk = [
        ("walk_worship_too_far_else_branch", "any", [at(900, 0, name="מסגד", type="mosque")]),
        ("walk_worship_too_far_official_far", "any", [at(3000, 0, name="משטרה", type="police"),
                                                      at(900, 0, name="מסגד", type="mosque")]),
        ("walk_public_too_far_worship_near", "synagogue", [at(1000, 0, name="בית מרקחת", type="pharmacy"),
                                                           at(300, 0, name="בית כנסת", type="synagogue")]),
        ("walk_worship_unmeasured", "any", [at(1100, 0, name="דלק", type="fuel"), at(500, 0, name="כנסייה", type="church"),
                                            at(2500, 0, name="משטרה", type="police")]),
        ("walk_public_and_worship_too_far", "any", [at(1000, 0, name="דלק", type="fuel"),
                                                    at(0, 1000, name="מסגד", type="mosque")]),
    ]
    out = []
    for is_walk, group in ((False, offline), (True, walk)):
        for cid, pref, cands in group:
            r = map_module._choose(base_lat, base_lon, cands, worship_pref.categories_for(pref),
                                   config.SAFE_PLACE_SEARCH_RADIUS_M, walk=is_walk)
            exp = result_json(r)
            if exp is not None:
                exp["fallback_note"] = r.get("fallback_note")
            out.append({"id": cid, "worship": pref, "walk": is_walk, "lat": base_lat, "lon": base_lon,
                        "radius_m": config.SAFE_PLACE_SEARCH_RADIUS_M, "candidates": cands, "expected": exp})
    return out


def main():
    map_module._cache_load = lambda: []          # רק הרשימה המובנית - כמו באנדרואיד
    places = map_module._bundled_places()
    if not places:
        print("safe_places/safe_places_il.json not found or empty", file=sys.stderr)
        return 1
    points = list(NAMED_POINTS)
    # ליד כל עשירי מהמקומות ברשימה (בהיסט קבוע) - בודק שכנים קרובים וסדר בין סוגים
    for i, p in enumerate(places[::10]):
        points.append((f"near_{i}_{p['type']}", round(p["lat"] + 0.004, 6), round(p["lon"] - 0.003, 6)))
    rnd = random.Random(20261108)
    for i in range(15):                           # נקודות אקראיות (זרע קבוע) בתוך תיבת ישראל
        points.append((f"random_{i}", round(rnd.uniform(29.5, 33.3), 6), round(rnd.uniform(34.25, 35.9), 6)))
    cases = [{"id": pid, "lat": lat, "lon": lon, "expected": result_json(choose_offline(lat, lon, places))}
             for pid, lat, lon in points]
    map_module._walk_table = fake_walk_table
    walk_cases = [{"id": pid, "lat": lat, "lon": lon, "expected": result_json(map_module._choose(
        lat, lon, places, config.SAFE_PLACE_CATEGORIES, config.SAFE_PLACE_SEARCH_RADIUS_M, walk=True))}
        for pid, lat, lon in NAMED_POINTS]
    names = sorted({p["name"] for p in places if p["type"] == "hospital"}) + [
        "מרכז לבריאות הנפש", "בית חולים גריאטרי", "Eye Hospital", "שיבא", "מיון", "קופת חולים מכבי", "", None]
    er = [{"name": n, "emergency": e, "expected": map_module.is_er_hospital(n, e)}
          for n in names for e in (None, "yes", "no")]
    import worship_pref
    out = {
        "generated_by": "tools/build_safe_places_parity.py (map_module._choose, offline path: walk=False, no cache)",
        "radius_m": config.SAFE_PLACE_SEARCH_RADIUS_M,
        "categories": [{"id": c["id"], "label": c["label"]} for c in config.SAFE_PLACE_CATEGORIES],
        "worship_ids": sorted(worship_pref.worship_ids()),
        "worship_options": [{"id": k, "label": v} for k, v in worship_pref.OPTIONS.items()],
        "worship_default": worship_pref.DEFAULT,
        "worship_categories": {k: [c["id"] for c in worship_pref.categories_for(k)] for k in worship_pref.OPTIONS},
        "official_ids": list(map_module.OFFICIAL_IDS),
        "official_preferred_m": map_module.OFFICIAL_PREFERRED_M, "public_radius_m": map_module.PUBLIC_RADIUS_M,
        "walk_factor": map_module.WALK_FACTOR,
        "bundled_usable": len(places),
        "cases": cases,
        "synthetic": synthetic_cases(),
        "worship_synthetic": worship_cases(),
        "walk_cases": walk_cases,
        "is_er_hospital": er,
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    found = sum(1 for c in cases if c["expected"])
    print(f"wrote {OUT}: {len(cases)} points ({found} with a result), {len(out['synthetic'])} synthetic, "
          f"{len(out['worship_synthetic'])} worship, {len(er)} is_er_hospital checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
