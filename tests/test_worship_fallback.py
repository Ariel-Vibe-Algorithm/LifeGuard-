# -*- coding: utf-8 -*-
"""
מקום תפילה כמפלט אחרון בחירום (worship_pref.py + map_module._choose):
- מקום תפילה נבחר **רק** כשאין רשמי קרוב ואין מקום ציבורי אחר קרוב; אז מוצגת הערה שזה מפלט.
- רק סוג המקום שהמשתמש/ת בחר/ה (בית כנסת / מסגד / כנסייה / כל מקום תפילה / אף אחד).
- ברירת מחדל = בית כנסת (כמו לפני השינוי).
- ההעדפה נשמרת מקומית בלבד, מוצפנת, ולא נשלחת לשרת.
"""
import config
import incident_store
import map_module as M
import worship_pref as W

HOME = (32.0800, 34.7800)


def _p(t, dlat, name=None):
    return {"name": name or t, "type": t, "type_label": t, "lat": HOME[0] + dlat, "lon": HOME[1], "source": "test"}


def _choose(cands, pref):
    return M._choose(HOME[0], HOME[1], cands, W.categories_for(pref), 5000, walk=False)


def test_new_worship_categories_marked_and_not_sent_to_google():
    ws = [c for c in config.SAFE_PLACE_CATEGORIES if c.get("worship")]
    assert {c["id"] for c in ws} == {"synagogue", "mosque", "church"}
    assert all(c["google_type"] is None for c in ws)                       # Google לא יודע לסנן לפי דת
    assert {c["tags"]["religion"] for c in ws} == {"jewish", "muslim", "christian"}


def test_worship_only_when_nothing_else_is_close():
    syn = _p("synagogue", 0.002)                                           # ~220 מ'
    pharm = _p("pharmacy", 0.006)                                          # ~670 מ'
    police_far = _p("police", 0.03)                                        # ~3.3 ק"מ
    assert _choose([syn, pharm, police_far], "synagogue")["type"] == "pharmacy"   # ציבורי אחר קרוב קודם
    best = _choose([syn, police_far], "synagogue")
    assert best["type"] == "synagogue" and "מקום תפילה" in best["fallback_note"]
    assert _choose([syn, _p("police", 0.01)], "synagogue")["type"] == "police"    # רשמי עד 2 ק"מ קודם
    assert "fallback_note" not in _choose([pharm], "synagogue")


def test_only_the_preferred_kind_is_offered():
    syn, mosque, church = _p("synagogue", 0.002), _p("mosque", 0.003), _p("church", 0.004)
    police_far = _p("police", 0.03)
    assert _choose([syn, mosque, church, police_far], "mosque")["type"] == "mosque"
    assert _choose([syn, mosque, church, police_far], "church")["type"] == "church"
    assert _choose([syn, mosque, church, police_far], "any")["type"] == "synagogue"   # הקרוב מביניהם
    assert _choose([syn, mosque, church, police_far], "none")["type"] == "police"     # לא מפנים למקום תפילה
    assert _choose([syn, mosque, church, police_far], None)["type"] == "synagogue"    # ברירת מחדל = כמו קודם
    assert _choose([mosque], "synagogue") is None


def test_preference_is_local_encrypted_and_defaults(temp_env):
    assert W.get("dana") == W.DEFAULT == "synagogue"
    W.save("dana", "mosque")
    assert W.get("dana") == "mosque"
    raw = incident_store.get_setting("dana", "worship_fallback")
    assert raw and "mosque" not in raw                                     # מוצפן (DPAPI)
    try:
        W.save("dana", "sector_x")
        raise AssertionError("ערך לא מוכר התקבל")
    except ValueError:
        pass


def test_preference_never_sent_to_server():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("backend_client.py", "alert_gateway.py", "privacy_sync.py"):
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        assert "worship" not in src, rel


def test_engine_passes_preference_to_search(temp_env, monkeypatch):
    import engine as em
    seen = []
    monkeypatch.setattr(em.map_module, "find_safe_route",
                        lambda lat, lon, log_callback=None, online=True, worship=None: seen.append(worship))
    monkeypatch.setattr(em.map_module, "get_walking_route", lambda *a, **k: None)
    W.save("tester", "church")
    eng = object.__new__(em.LifeGuardEngine)
    eng.username, eng.current_event = "tester", None
    eng.log = eng._log_silent = lambda *a, **k: None
    eng.on_route_update = lambda *a, **k: None
    eng._find_safe_route_for_location({"available": True, "lat": 32.08, "lon": 34.78, "is_precise": True,
                                       "source": "GNSS"})
    assert seen and set(seen) == {"church"}
