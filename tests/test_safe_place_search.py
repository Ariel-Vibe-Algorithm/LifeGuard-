# -*- coding: utf-8 -*-
"""
חיפוש מקום בטוח (map_module) - באג 2026-11-08: Overpass נפל/נתקע (שאילתה כבדה, שרתים אחד-אחרי-השני)
ולא נמצא כלום. עכשיו: שאילתה קלה, Overpass ו-Nominatim במקביל, מטמון מקומי בלי אינטרנט, בחירה שמעדיפה
רשמי קרוב ואז מקום ציבורי קרוב, וניסיון חוזר במנוע בזמן SOS. בלי רשת אמיתית.
+ הכתבה קולית לצ'אט (voice_input) עם מקליט/מזהה מדומים.
"""
import threading
import time

import pytest

import config
import map_module as M
import ui_theme as T
from test_ui_navigation import shared_root  # noqa: F401

HOME = (31.2518, 34.7913)


def _p(kind, dn_m, name=None, src="osm_nominatim"):
    label = next(c["label"] for c in config.SAFE_PLACE_CATEGORIES if c["id"] == kind)
    return {"name": name or label, "lat": HOME[0] + dn_m / 111000, "lon": HOME[1], "type": kind,
            "type_label": label, "source": src}


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "")
    monkeypatch.setattr(M, "_bundled_places", lambda: [])          # בודקים את הרשת/המטמון בנפרד
    monkeypatch.setattr(M, "_walk_table", lambda *a, **k: None)     # בלי רשת אמיתית
    return tmp_path


def test_light_query_limits_public_places_radius():
    q = M._build_overpass_query(config.SAFE_PLACE_CATEGORIES, *HOME, 5000)
    assert '["amenity"="police"](around:5000' in q and '["amenity"="fuel"](around:1200' in q
    assert "out center 80" in q and "timeout:9" in q


def test_choice_prefers_close_official_then_close_public():
    cats, r = config.SAFE_PLACE_CATEGORIES, 5000
    assert M._choose(*HOME, [_p("police", 1500), _p("fuel", 100)], cats, r)["type"] == "police"
    best = M._choose(*HOME, [_p("police", 4500), _p("fuel", 100)], cats, r)
    assert best["type"] == "fuel" and best["distance_m"] < 150          # לא משטרה במרחק 4.5 ק"מ
    assert M._choose(*HOME, [_p("police", 4500), _p("fuel", 3000)], cats, r)["type"] == "police"
    assert M._choose(*HOME, [_p("police", 9000)], cats, r) is None


def test_overpass_down_nominatim_answers(cache_dir, monkeypatch):
    monkeypatch.setattr(M, "_overpass_candidates", lambda *a, **k: (time.sleep(5), None)[1])   # תקוע
    monkeypatch.setattr(M, "_nominatim_candidates", lambda *a, **k: [_p("hospital", 850, "בית חולים סורוקה")])
    t = time.time()
    best = M.find_nearest_safe_place(*HOME)
    assert best["name"] == "בית חולים סורוקה" and best["source"] == "osm_nominatim"
    assert time.time() - t < 2                                          # לא מחכים ל-Overpass התקוע


def test_no_network_uses_cached_places_and_says_so(cache_dir, monkeypatch):
    monkeypatch.setattr(M, "_overpass_candidates",
                        lambda *a, **k: [_p("police", 700, "תחנת באר שבע", src="osm_overpass")])
    monkeypatch.setattr(M, "_nominatim_candidates", lambda *a, **k: None)
    assert M.find_nearest_safe_place(*HOME)["source"] == "osm_overpass"
    assert (cache_dir / "cache" / "safe_places_cache.json").exists()
    monkeypatch.setattr(M, "_overpass_candidates", lambda *a, **k: None)
    logs = []
    best = M.find_nearest_safe_place(*HOME, log_callback=logs.append)
    assert best["name"] == "תחנת באר שבע" and best["source"] == "cache"
    assert any("שנשמר" in x for x in logs)                             # אומרים שזה ממטמון


def test_nothing_anywhere_is_honest(cache_dir, monkeypatch):
    monkeypatch.setattr(M, "_overpass_candidates", lambda *a, **k: None)
    monkeypatch.setattr(M, "_nominatim_candidates", lambda *a, **k: None)
    logs = []
    assert M.find_nearest_safe_place(*HOME, log_callback=logs.append) is None
    assert any("לא זמינים" in x for x in logs)


def test_parallel_overpass_first_answer_wins(monkeypatch):
    calls = []

    class R:
        status_code = 200

        def __init__(self, url):
            self.url = url

        def json(self):
            return {"elements": [{"type": "node", "lat": 1, "lon": 2, "tags": {"amenity": "police"}}], "u": self.url}

    def post(url, **k):
        calls.append(url)
        if "overpass-api.de" in url:
            time.sleep(3)
            raise TimeoutError()
        return R(url)
    monkeypatch.setattr(M.requests, "post", post)
    t = time.time()
    data = M._query_overpass("q")
    assert data and data["elements"] and time.time() - t < 2 and len(calls) == len(M.OVERPASS_URLS)


def test_engine_retries_route_while_sos_active(temp_env, user, monkeypatch):
    import engine as em
    from engine import LifeGuardEngine
    results = [None, {"place": {"name": "משטרה", "type_label": "משטרה", "lat": 1, "lon": 2, "distance_m": 300},
                      "route": None}]
    calls = []
    def fsr(lat, lon, log_callback=None, online=True, **k):
        if not online:
            return None                                           # שלב מיידי: אין מקום ברשימה המובנית
        calls.append((lat, lon))
        return results[len(calls) - 1]
    monkeypatch.setattr(em.map_module, "find_safe_route", fsr)
    monkeypatch.setattr(em.map_module, "get_walking_route", lambda *a, **k: None)
    monkeypatch.setattr(LifeGuardEngine, "ROUTE_RETRY_DELAYS_S", (0.05, 0.05))
    eng = LifeGuardEngine(username=user)
    try:
        eng.current_event = {"event_id": "lg-r", "kind": "SOS", "locations": []}
        routes = []
        eng.on_route_update = routes.append
        gps = {"available": True, "lat": 31.25, "lon": 34.79, "is_precise": True, "source": "GNSS"}
        eng._find_safe_route_for_location(gps)
        assert routes[0]["result"] is None and "מנסה שוב" in routes[0]["reason"]
        end = time.time() + 3
        while time.time() < end and len(routes) < 2:
            time.sleep(0.02)
        assert len(calls) == 2 and routes[-1]["result"]["place"]["name"] == "משטרה"
    finally:
        eng.shutdown()


# ---------------------------------------------------------------- הכתבה קולית
def test_dictation_records_until_stop_and_transcribes_locally():
    import voice_input
    got, stopped = {}, threading.Event()

    def rec():
        stopped.wait(2)
        return [0.1] * 16000
    d = voice_input.Dictation(lambda t, e: got.update(text=t, err=e), recorder=rec,
                              transcriber=lambda audio: " אני ליד הקיוסק ")
    d.start()
    assert d.active
    stopped.set()
    d.stop()
    end = time.time() + 2
    while "text" not in got and time.time() < end:
        time.sleep(0.01)
    assert got == {"text": "אני ליד הקיוסק", "err": None} and not d.active


def test_dictation_mic_error_is_reported_not_raised():
    import voice_input
    got = {}

    def boom():
        raise OSError("המיקרופון תפוס")
    d = voice_input.Dictation(lambda t, e: got.update(text=t, err=e), recorder=boom)
    d.start()
    end = time.time() + 2
    while "text" not in got and time.time() < end:
        time.sleep(0.01)
    assert got["text"] == "" and "תפוס" in got["err"]


def test_chat_dictation_fills_entry_for_review_not_auto_send(monkeypatch, shared_root):  # noqa: F811
    import tkinter as tk
    import types
    import voice_input
    from chat_panel import ChatPanel
    monkeypatch.setattr(voice_input, "available", lambda: True)

    class FakeDict:
        def __init__(self, on_done, **k):
            self.on_done, self.active, self._stop = on_done, False, threading.Event()

        def start(self):
            self.active = True

        def stop(self):
            self.active = False
            self._stop.set()
            self.on_done("אני מסתתרת במחסן", None)

        def elapsed(self):
            return 1
    monkeypatch.setattr(voice_input, "Dictation", FakeDict)
    root = shared_root
    sent = []
    app = types.SimpleNamespace(client=types.SimpleNamespace(send_message=lambda *a, **k: sent.append(a)))
    frame = tk.Frame(root)
    try:
        cp = ChatPanel(frame, app, lambda: "inc_1", "dana")
        cp.pack()
        assert cp.mic_btn is not None
        cp.toggle_dictation()            # התחלה
        cp.toggle_dictation()            # סיום -> זיהוי
        end = time.time() + 2
        while "אני מסתתרת" not in cp.var.get() and time.time() < end:
            root.update()
            time.sleep(0.02)
        assert T.entry_text(cp.var.get()) == "אני מסתתרת במחסן" and sent == []            # לבדיקה, לא נשלח לבד
        assert "בדקו" in cp.voice_status.cget("text")
    finally:
        frame.destroy()


def test_hebrew_entry_keeps_typed_spaces_in_the_right_place(shared_root):  # noqa: F811
    """באג: במסך ה-SOS "אי אפשר לכתוב רווח" - Tk הציג רווח אחרי מילה עברית בצד הלא נכון. RLM בסוף."""
    import tkinter as tk
    var = tk.StringVar(master=shared_root)        # אותו interpreter כמו השורה (בלי master נקשר ל-root אחר)
    e = tk.Entry(shared_root, textvariable=var, justify="right")
    T.rtl_entry(e, var)
    try:
        for ch in "הרצל 5 באר שבע":
            e.insert("insert", ch)                       # כמו הקלדה: תו במקום הסמן
        assert T.entry_text(var.get()) == "הרצל 5 באר שבע"
        assert var.get().endswith(T.RLM) and var.get().count(T.RLM) == 1
        assert e.index("insert") == len("הרצל 5 באר שבע")    # הסמן לפני הסימן הבלתי נראה
        e.icursor("end")
        e.insert("insert", "!")                          # גם אם הסמן אחרי הסימן - מתוקן
        assert T.entry_text(var.get()) == "הרצל 5 באר שבע!" and var.get().endswith(T.RLM)
        var.set("hertzl 5 ")                             # בלי עברית - בלי סימן
        assert var.get() == "hertzl 5 "
        var.set("")
        assert var.get() == ""
    finally:
        e.destroy()


# ---------------------------------------------------------------- רשימה מובנית / דיוק
def test_bundled_list_is_nationwide_and_only_er_hospitals():
    import json
    import os
    d = json.load(open(os.path.join(os.path.dirname(M.__file__), "safe_places", "safe_places_il.json"),
                       encoding="utf-8"))
    types = {p["type"] for p in d["places"]}
    assert {"police", "hospital", "fire_station"} <= types and d["count"] >= 250
    assert any(s["license"] for s in d["sources"]) and d["coverage"].startswith("nationwide")
    hospitals = [p for p in d["places"] if p["type"] == "hospital"]
    assert all(M.is_er_hospital(p["name"], p.get("emergency")) for p in hospitals)
    assert any("סורוקה" in p["name"] for p in hospitals)
    fire = [p for p in d["places"] if p["src"] == "fire_gov_2026"]
    assert len(fire) >= 100 and all(29.4 < p["lat"] < 33.4 for p in fire)


@pytest.mark.parametrize("name,em,ok", [("בית חולים סורוקה", None, True), ("מרכז רפואי רבין – קמפוס ביילינסון", None, True),
                                        ("מרכז לבריאות הנפש גהה", None, False), ("גן ילדים", "yes", False),
                                        ("מרכז גריאטרי שוהם", None, False), ("בית חולים", "yes", True),
                                        ("שערי צדק", "no", False), ("Smile Design Center", "yes", False)])
def test_er_hospital_rule(name, em, ok):
    assert M.is_er_hospital(name, em) is ok


def test_offline_mode_is_instant_and_uses_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no network in offline mode")
    monkeypatch.setattr(M.requests, "get", boom)
    monkeypatch.setattr(M.requests, "post", boom)
    t = time.time()
    best = M.find_nearest_safe_place(*HOME, online=False)              # באר שבע - מהרשימה המובנית
    assert best and best["source"].startswith("bundled") and best["type"] in M.OFFICIAL_IDS
    assert time.time() - t < 0.5 and best["walk_m"] >= best["distance_m"]


def test_walking_distance_beats_straight_line(monkeypatch):
    """קו אווירי קצר דרך כביש מהיר/ואדי ≠ קרוב ברגל: הדירוג לפי מרחק הליכה אמיתי (OSRM)."""
    cats = config.SAFE_PLACE_CATEGORIES
    near_air, far_air = _p("police", 600, "מעבר לכביש"), _p("hospital", 900, "בית חולים סורוקה")
    monkeypatch.setattr(M, "_walk_table", lambda lat, lon, places, **k:
                        [(3200, 2400) if p["name"] == "מעבר לכביש" else (1000, 750) for p in places])
    best = M._choose(*HOME, [near_air, far_air], cats, 5000, walk=True)
    assert best["name"] == "בית חולים סורוקה" and best["walk_m"] == 1000 and best["walk_measured"]
    assert M._choose(*HOME, [near_air, far_air], cats, 5000)["name"] == "מעבר לכביש"    # בלי OSRM - קו אווירי


def test_engine_shows_instant_result_then_refines(temp_env, user, monkeypatch):
    import engine as em
    from engine import LifeGuardEngine
    quick = {"place": {"name": "תחנת כיבוי", "type_label": "כיבוי", "lat": 1, "lon": 2, "distance_m": 500}, "route": None}
    full = {"place": {"name": "משטרה", "type_label": "משטרה", "lat": 3, "lon": 4, "distance_m": 300},
            "route": {"points": [(0, 0), (3, 4)], "distance_m": 400, "duration_s": 300}}
    monkeypatch.setattr(em.map_module, "find_safe_route", lambda lat, lon, log_callback=None, online=True, **k:
                        full if online else dict(quick))
    monkeypatch.setattr(em.map_module, "get_walking_route", lambda *a, **k: {"points": [(0, 0), (1, 2)],
                                                                           "distance_m": 700, "duration_s": 500})
    eng = LifeGuardEngine(username=user)
    try:
        eng.current_event = {"event_id": "lg-q", "kind": "SOS", "locations": []}
        routes = []
        eng.on_route_update = routes.append
        eng._find_safe_route_for_location({"available": True, "lat": 31.25, "lon": 34.79, "is_precise": True,
                                           "source": "GNSS"})
        assert routes[0]["refining"] and routes[0]["result"]["place"]["name"] == "תחנת כיבוי"
        assert routes[0]["result"]["route"]["distance_m"] == 700                      # מסלול כבר בשלב המיידי
        assert routes[-1]["result"]["place"]["name"] == "משטרה" and not routes[-1].get("refining")
        assert eng.current_event["safe_route"]["place"]["name"] == "משטרה"
    finally:
        eng.shutdown()
