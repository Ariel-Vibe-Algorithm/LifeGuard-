# -*- coding: utf-8 -*-
"""אימות סיווג תאורה מול עמודי תאורה: ספירה ברדיוס, סינון לילה/אזורים בלי נתונים, מתאם ו-bootstrap לפי הליכה."""
import os

import numpy as np
import pytest

from research_tools import lighting_validation as L

NIGHT = 1_700_000_000 - (1_700_000_000 % 86400) + 20 * 3600     # 22:00 שעון ישראל (UTC+2)
DAY = NIGHT - 10 * 3600


def test_count_within_radius_and_coverage():
    base = (32.08, 34.78)
    pts = [(base[0] + i * 0.0001, base[1]) for i in range(10)]   # עמוד כל ~11 מ' צפונה
    idx = L.LightIndex(pts)
    assert idx.count_within(*base, radius_m=30) == 3             # 0, 11, 22 מ'
    assert idx.count_within(*base, radius_m=5) == 1
    assert idx.covers(*base) and not idx.covers(31.0, 35.5)


def test_night_filter_and_spearman():
    assert L.is_night(NIGHT) and not L.is_night(DAY) and not L.is_night(None)
    assert L.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert L.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert L.spearman([1, 1, 1], [1, 2, 3]) is None


def test_validation_recovers_true_relationship_and_skips_uninformative_points():
    rng = np.random.default_rng(0)
    lights, obs = [], []
    for w in range(6):                                            # 6 הליכות, כל אחת לאורך רחוב אחר
        lat0 = 32.08 + w * 0.01
        for j in range(30):
            lat = lat0 + j * 0.0004
            dense = j % 3 != 0                                    # שליש מהנקודות בלי עמודים
            if dense:
                lights += [(lat + 0.00005, 34.78), (lat - 0.00005, 34.78)]
            obs.append({"lat": lat, "lon": 34.78, "observed_at": NIGHT + j, "walk_id": f"w{w}",
                        "luminance": (0.35 if dense else 0.08) + rng.normal(0, 0.03),
                        "attributes": {"lighting": float(rng.random())}})       # "מודל" שלא יודע כלום
    obs.append({"lat": 32.08, "lon": 34.78, "observed_at": DAY, "walk_id": "w0", "luminance": 0.9})
    obs.append({"lat": 30.0, "lon": 35.0, "observed_at": NIGHT, "walk_id": "w9", "luminance": 0.1})
    res = L.validate(obs, L.LightIndex(lights))
    assert res["n"] == 180 and res["walks"] == 6
    assert res["skipped"] == {"not_night": 1, "no_location": 0, "no_light_data": 1}
    # בהירות נמדדת - עובדת. (מספר העמודים כאן כמעט בינארי 0/2, ולכן גם הפרדה מושלמת נותנת ספירמן ~0.8)
    assert res["lum"]["spearman"] > 0.5 and res["lum"]["ci95_by_walk"][0] > 0.3
    assert abs(res["clip"]["spearman"]) < 0.25                                       # "מודל" אקראי - נחשף
    assert res["lum"]["mean_no_pole"] < res["lum"]["mean_2plus_poles"]


@pytest.mark.skipif(not os.path.exists(os.path.join(L.DERIVED, L.LIGHT_FILES[0])), reason="נתוני עמודי תאורה לא נבנו")
def test_real_street_light_index_loads():
    idx = L.LightIndex.from_csv()
    assert idx.n > 80000
    assert idx.covers(32.0853, 34.7818)                           # מרכז תל אביב
