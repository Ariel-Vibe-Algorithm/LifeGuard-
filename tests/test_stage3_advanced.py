# -*- coding: utf-8 -*-
"""
שלב 3: שיבוץ מתנדבים מתקדם, מודיעין רחובות (מקור/ביטחון/עדכון; אומדן ≠ עובדה), בטיחות מסלול,
ומצב חירום לאומי (התרעות רק כשמוגדר מקור; מקלטים רק ממאגרים רשמיים).
"""
import json
import threading
import time

import pytest

import context_risk as CR
from server import national as N
from server import safety_intel as SI
from server import volunteer_match as VM
from server.lifeguard_server import LifeGuardService, create_server
from backend_client import BackendClient

PW = "StrongPass!2026"
INC = (32.0800, 34.7800)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEGUARD_OSRM_URL", raising=False)
    monkeypatch.delenv("LIFEGUARD_OREF_ALERTS_URL", raising=False)
    s = LifeGuardService(str(tmp_path / "s3.db"))
    yield s
    s.close()


def _users(s, *names, age_days=30):
    for n in names:
        s.register(n, PW)
    s._x("UPDATE users SET created_at=?", (time.time() - age_days * 86400,))


def _vol(s, name, meters_north, quals=(), skills=()):
    s.set_volunteer(name, True, 5, list(skills))
    s.update_presence(name, INC[0] + meters_north / 111000, INC[1], is_precise=True)
    for q in quals:
        s.request_qualification(name, q, "מד\"א", "12345")
        s.admin_decide_qualification(name, q, True)


# ---------------------------------------------------------------- מתנדבים
def test_match_tier_and_reliability_rules():
    assert VM.match_tier("medical", {"emt"}, []) == 0
    assert VM.match_tier("medical", set(), ["first_aid"]) == 1
    assert VM.match_tier("medical", {"firefighter"}, []) == 2
    assert VM.match_tier("unknown", {"emt"}, ["first_aid"]) == 2
    assert VM.reliability({"offers": 2})[0] == VM.UNKNOWN                     # חדש/ה - לא נענש/ת
    assert VM.reliability({"offers": 10, "accepted": 1, "declined": 1})[0] == VM.POOR
    assert VM.reliability({"offers": 10, "accepted": 6, "declined": 2})[0] == VM.RELIABLE
    assert VM.reliability({"offers": 10, "accepted": 6, "reports": 2})[0] == VM.POOR


def test_verified_qualification_wins_within_same_eta_band_but_not_across(svc):
    _users(svc, "dana", "near_plain", "mid_emt", "far_emt")
    _vol(svc, "near_plain", 100)
    _vol(svc, "mid_emt", 300, quals=["emt"])               # אותה רצועת 10 דק' הליכה
    inc = svc.create_incident("dana", "m1", emergency_type="medical", location={"lat": INC[0], "lon": INC[1],
                                                                                "is_precise": True})
    vols = [r["username"] for r in svc._q("SELECT username FROM recipients WHERE incident_id=? AND role='volunteer'",
                                          (inc["id"],))]
    assert vols[0] == "mid_emt"
    svc2_inc = svc.create_incident("dana", "m2", emergency_type="unknown",
                                   location={"lat": INC[0], "lon": INC[1], "is_precise": True})
    v2 = svc._q("SELECT username FROM recipients WHERE incident_id=? AND role='volunteer'", (svc2_inc["id"],))
    assert v2 and v2[0]["username"] in ("near_plain", "mid_emt")


def test_speed_beats_qualification_across_bands(svc):
    _users(svc, "dana", "near_plain", "far_emt")
    _vol(svc, "near_plain", 100)
    _vol(svc, "far_emt", 1400, quals=["emt"])               # ~20 דק' הליכה - רצועה אחרת
    inc = svc.create_incident("dana", "m1", emergency_type="medical", location={"lat": INC[0], "lon": INC[1],
                                                                                "is_precise": True})
    first = svc._q("SELECT username FROM recipients WHERE incident_id=? AND role='volunteer' ORDER BY notified_at",
                   (inc["id"],))[0]["username"]
    assert first == "near_plain"


def test_qualification_flow_and_history(svc):
    _users(svc, "vol", "dana")
    svc.set_volunteer("vol", True, 5)
    with pytest.raises(Exception):
        svc.request_qualification("vol", "astronaut", "x", "y")
    with pytest.raises(Exception):
        svc.request_qualification("vol", "emt", "", "")
    q = svc.request_qualification("vol", "emt", "מד\"א", "A-1")
    assert q[0]["status"] == "pending"
    assert svc.admin_qualifications("pending")[0]["username"] == "vol"
    svc.admin_decide_qualification("vol", "emt", False, "תעודה לא נמצאה")
    assert svc.my_qualifications("vol")[0]["status"] == "rejected"
    svc.request_qualification("vol", "emt", "מד\"א", "A-2")
    svc.admin_decide_qualification("vol", "emt", True)
    with pytest.raises(Exception):
        svc.request_qualification("vol", "emt", "מד\"א", "A-3")              # כבר מאומת
    svc.update_presence("vol", INC[0], INC[1], is_precise=True)
    inc = svc.create_incident("dana", "h1", location={"lat": INC[0], "lon": INC[1], "is_precise": True})
    svc.respond("vol", inc["id"], "accept")
    h = svc.volunteer_history("vol")
    assert h["assignments"][0]["response"] == "accept" and h["assignments"][0]["response_seconds"] is not None
    assert h["stats"]["offers"] == 1 and h["reliability"]["bucket"] == "unknown"
    assert h["qualifications"][0]["status"] == "verified"


# ---------------------------------------------------------------- שכבות סינתטיות
def _layers():
    acc = {}
    for i in range(10):                                      # מוקד תאונות בנקודה אחת
        acc[f"{int(32.0800 / 0.001)}:{int(34.7800 / 0.001)}"] = [10, 3, 4]
    for j in range(200):                                     # רקע: תא אחד בכל מקום אחר
        acc[f"{int(32.2 / 0.001) + j}:{int(34.9 / 0.001)}"] = [1, 0, 0]
    return SI.Layers({"built_at": 1_790_000_000, "accidents": {"cell_deg": 0.001, "cells": acc, "years": [2020, 2024]},
                      "shelters": [{"lat": 32.0805, "lon": 34.7805, "city": "תל אביב-יפו", "name": "מקלט ציבורי",
                                    "address": "רחוב 1", "usable": "כשיר לשימוש", "source": "tlv_gis_592"}],
                      "sources": [{"id": "tlv_gis_592", "name": "עיריית ת\"א GIS 592", "license": "TLV Open Data"}]})


def _grid():
    # רחוב מואר לאורך lon=34.7900, חשוך לאורך lon=34.7800 (אזור מכוסה)
    pts = [(32.07 + i * 0.0003, 34.7900) for i in range(80)] + [(32.07 + i * 0.0003, 34.7700) for i in range(80)]
    return CR.LightGrid.build(pts)


@pytest.fixture
def layered(svc, monkeypatch):
    svc._safety_layers = _layers()
    from server import risk_factors_layer as RF
    svc._rf_layer = RF.RiskFactors()      # הרמטי: בלי הרשת המקומית (gitignored) - נבדקת ב-test_route_risk_factors
    monkeypatch.setattr(CR, "_grid", _grid)
    return svc


def test_street_intel_labels_every_item_and_never_calls_estimates_verified(layered):
    s = layered
    _users(s, "dana")
    s._x("""INSERT INTO street_observations(id, kind, submitted_by, lat, lon, observed_at, received_at, attributes)
            VALUES ('o1','user_video','dana',32.0801,34.7801,?,?,?)""",
         (time.time(), time.time(), json.dumps({"model": "clip-test", "model_scores": {"lighting": 0.2}})))
    r = s.street_intel("dana", 32.0800, 34.7800, 150)
    by = {i["layer"]: i for i in r["items"]}
    for it in r["items"]:
        assert {"type", "source", "confidence", "last_update", "verified"} <= set(it)
    assert by["lighting"]["type"] == "official" and "רישום" in by["lighting"]["text"]
    assert by["pedestrian_accidents"]["value"]["total"] >= 10 and "לא פשיעה" in by["pedestrian_accidents"]["text"]
    assert by["shelter"]["value"]["name"] == "מקלט ציבורי"
    assert by["street_vision"]["type"] == "estimate" and by["street_vision"]["verified"] is False
    assert any(u["layer"] == "crime" for u in r["unavailable"])


def test_reports_corroboration_verification_expiry_and_privacy(layered):
    s = layered
    _users(s, "usr_a", "usr_b", "usr_c", "usr_d")
    s.register("newbie", PW)                                  # חשבון חדש - לא נספר לאישוש
    for u in ("usr_a", "usr_b", "newbie"):
        s.add_street_report(u, "dark_street", 32.08001, 34.78001, "אין אור")
    st = [i for i in s.street_intel("usr_a", 32.08, 34.78)["items"] if i["layer"] == "report"]
    assert st and all(i["status"] == "unverified" for i in st)
    s.add_street_report("usr_c", "dark_street", 32.08002, 34.78, "")
    st = [i for i in s.street_intel("usr_a", 32.08, 34.78)["items"] if i["layer"] == "report"]
    assert all(i["status"] == "corroborated" and i["reporters"] == 3 and not i["verified"] for i in st)
    assert "usr_a" not in json.dumps(st) and "usr_b" not in json.dumps(st)                          # בלי זהות מדווחים
    rid = s.add_street_report("usr_d", "harassment", 32.0801, 34.7801)["id"]
    s.admin_decide_report(rid, True)
    v = [i for i in s.street_intel("usr_a", 32.08, 34.78)["items"] if i.get("value", {}).get("kind") == "harassment"]
    assert v[0]["status"] == "verified" and v[0]["verified"] is True
    s._x("UPDATE street_reports SET expires_at=? WHERE id=?", (time.time() - 1, rid))
    assert not [i for i in s.street_intel("usr_a", 32.08, 34.78)["items"] if i.get("value", {}).get("kind") == "harassment"]
    with pytest.raises(Exception):
        s.add_street_report("usr_a", "ufo", 32.08, 34.78)
    for _ in range(SI.REPORTS_PER_DAY - 1):
        s.add_street_report("usr_a", "broken_light", 32.08, 34.78)
    with pytest.raises(Exception):
        s.add_street_report("usr_a", "broken_light", 32.08, 34.78)   # מגבלה יומית
    mine = s.my_street_reports("usr_a")
    with pytest.raises(Exception):
        s.delete_street_report("usr_b", mine[0]["id"])
    assert s.delete_street_report("usr_a", mine[0]["id"])["ok"]


def test_route_safety_prefers_lit_route_at_night_with_reliability(layered):
    s = layered
    _users(s, "dana")
    dark = [[32.0705, 34.7800], [32.0900, 34.7800]]                  # ~2.2 ק"מ לאורך הרחוב החשוך
    lit = [[32.0705, 34.7800], [32.0705, 34.7900], [32.0900, 34.7900], [32.0900, 34.7800]]   # +~1.9 ק"מ
    lit_short = [[32.0705, 34.7800], [32.0706, 34.7895], [32.0900, 34.7895], [32.0900, 34.7800]]
    night = CR.solar_elevation  # noqa: F841
    t_night = 1797800000          # 2026-12 לילה בישראל (נבדק ב-test_context_risk)
    r = s.route_safety("dana", routes=[dark, lit], at=t_night)
    assert r["phase"] == "night"
    d, l = r["routes"]
    assert d["components"]["lighting"]["per_km"] > l["components"]["lighting"]["per_km"]
    assert d["data"]["reliability"] in ("high", "partial") and d["data"]["crime"] == "לא זמין ברמת רחוב"
    assert any("חשוך" in " ".join(sg["reasons"]) for sg in d["segments"])
    assert r["recommendation"] is None                             # הבטוח ארוך ביותר מ-30% - לא ממליצים
    day = s.route_safety("dana", routes=[dark, lit], at=1781000000)   # יוני, צהריים
    assert day["phase"] == "day" and day["routes"][0]["components"]["lighting"]["per_km"] == 0
    with pytest.raises(Exception):
        s.route_safety("dana", routes=[[[32.07, 34.78]]])
    with pytest.raises(Exception) as e:
        s.route_safety("dana", origin={"lat": 32.07, "lon": 34.78}, dest={"lat": 32.09, "lon": 34.78})
    assert "OSRM" in str(getattr(e.value, "message", e.value))      # בלי ניתוב - אומרים, לא ממציאים
    # דיווחים מאומתים על המסלול מעלים סיכון
    _users(s, "usr_x", "usr_y", "usr_z")
    for u in ("usr_x", "usr_y", "usr_z"):
        s.add_street_report(u, "harassment", 32.0800, 34.7800)
    r2 = s.route_safety("dana", routes=[dark], at=1781000000)
    assert r2["routes"][0]["components"]["reports"]["per_km"] > 0


def test_sample_route_spacing():
    pts, length = SI.sample_route([(32.0, 34.0), (32.001, 34.0), (32.002, 34.0)])
    assert 215 < length < 230 and len(pts) >= 9 and pts[-1][2] == pytest.approx(length)


# ---------------------------------------------------------------- חירום לאומי
def test_alert_parsing_and_area_matching():
    raw = '﻿{"id":"1","cat":"1","title":"ירי רקטות וטילים","data":["תל אביב - מרכז העיר","רמת גן - מערב"],"desc":"היכנסו למרחב המוגן"}'
    a = N.parse_alert(raw.encode("utf-8"))
    assert a["kind"] == "missiles" and len(a["areas"]) == 2
    assert N.parse_alert(b"") is None and N.parse_alert(b"  \r\n") is None
    assert N.area_matches("תל אביב", "תל אביב - מרכז העיר")
    assert not N.area_matches("תל", "תל אביב - מרכז העיר")
    assert N.classify("חדירת מחבלים") == "infiltration" and N.classify("משהו") == "general"


def test_national_status_without_feed_is_honest_and_lists_official_shelters(layered):
    s = layered
    _users(s, "dana")
    st = s.national_status("dana", 32.0800, 34.7800)
    assert st["alerts"]["status"] == "not_configured" and "פיקוד העורף" in st["alerts"]["text"]
    assert st["shelters"][0]["source_name"].startswith("עיריית") and st["shelters"][0]["distance_m"] < 100
    assert any(n["number"] == "104" for n in st["numbers"])
    assert s.national_status("dana", 31.0, 35.0)["shelters"] == []                  # אין נתונים - לא ממציאים


def test_national_feed_alert_in_my_area_and_errors(layered, monkeypatch):
    s = layered
    _users(s, "dana")
    s.set_national_areas("dana", ["תל אביב"])
    monkeypatch.setenv("LIFEGUARD_OREF_ALERTS_URL", "http://mirror.invalid/alerts.json")
    body = json.dumps({"id": "9", "cat": "1", "title": "ירי רקטות וטילים", "data": ["תל אביב - מרכז העיר"],
                       "desc": ""}).encode()

    class Resp:
        def read(self, n):
            return body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(N.urllib.request, "urlopen", lambda req, timeout: Resp())
    cur = s.national_status("dana")["alerts"]["current"]
    assert cur["in_my_areas"] and cur["kind"] == "missiles" and "מרחב מוגן" in cur["instructions"][0]
    s._oref_cache = None

    def fail(req, timeout):
        raise OSError("geo-blocked")
    monkeypatch.setattr(N.urllib.request, "urlopen", fail)
    st = s.national_status("dana")["alerts"]
    assert st["status"] == "error" and "current" not in st                          # שגיאה ≠ "אין התרעות"


def test_admin_national_mode_notifies_available_volunteers(svc):
    _users(svc, "vol", "off", "dana")
    svc.set_volunteer("vol", True, 5)
    svc.set_volunteer("off", False, 5)
    m = svc.admin_national_mode(True, "מצב חירום", "מתנדבים - היו זמינים", ["חיפה"])
    assert m["active"] and svc.national_status("dana")["national_mode"]["title"] == "מצב חירום"
    notes = {r["username"] for r in svc._q("SELECT username FROM notifications WHERE type='national_mode'")}
    assert notes == {"vol"}
    svc.admin_national_mode(False)
    assert svc.national_status("dana")["national_mode"] is None


def test_stage3_http_routes(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEGUARD_OREF_ALERTS_URL", raising=False)
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "h.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = BackendClient(f"http://127.0.0.1:{httpd.server_address[1]}")
        c.login_or_register("dana", PW)
        assert c.street_report("broken_light", 32.08, 34.78)["status"] == "unverified"
        assert "items" in c.street_intel_at(32.08, 34.78)
        assert c.route_safety(routes=[[[32.07, 34.78], [32.08, 34.78]]])["routes"][0]["length_m"] > 1000
        assert c.set_national_areas(["חיפה"])["areas"] == ["חיפה"]
        assert c.national_status(32.08, 34.78)["my_areas"] == ["חיפה"]
        c.set_volunteer(True)
        assert c.request_qualification("first_aid", "מד\"א", "123")[0]["status"] == "pending"
        assert c.volunteer_history()["reliability"]["bucket"] == "unknown"
    finally:
        httpd.shutdown()
        httpd.service.close()


def test_desktop_texts_and_national_card_render():
    import tkinter as tk
    import app_screens
    import features_ui
    txt = app_screens.route_safety_text({"phase": "night", "routes": [{
        "level": "medium", "components": {"lighting": {"level": "high"}, "traffic": {"level": "low"},
                                          "reports": {"level": "low"}},
        "data": {"reliability": "partial"}, "segments": [{"from_m": 300, "reasons": ["רחוב חשוך"]}]}]})
    assert "בינוני" in txt and "(לילה)" in txt and "חלקית" in txt and "אחרי 300 מ'" in txt and "פשיעה" in txt
    lines = features_ui.ride_lines({"kind": "ride", "ride_label": "מונית", "vehicle": {"plate": "12"},
                                    "progress": {"remaining_m": 2300, "eta_minutes": 6},
                                    "anomaly": {"text": "סוטה"}, "route_source": "straight_line"})
    assert any("12" in l for l, _ in lines) and any("2.3" in l for l, _ in lines) and lines[-1][1] == "YELLOW"
    assert features_ui.ride_lines({"kind": "walk"}) == []
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        class App:
            client = None
            def __init__(self):
                self.root = root
        card = features_ui.NationalCard(tk.Frame(root), App())
        card.refresh()
        assert "פיקוד העורף" in card.alert.cget("text")                    # לא מחובר - מפנה למקור הרשמי
        card._apply({"national_mode": {"active": True, "title": "מצב חירום", "message": "היו זמינים"},
                     "my_areas": ["תל אביב"],
                     "alerts": {"status": "ok", "official_note": "המקור המחייב",
                                "current": {"title": "ירי רקטות", "in_my_areas": True, "instructions": ["היכנסו"],
                                            "areas_count": 3}},
                     "shelters": [{"name": "מקלט", "distance_m": 120, "lat": 32.0, "lon": 34.0, "address": "א"}]})
        assert "באזור שלך" in card.alert.cget("text") and card.banner.winfo_manager() == "pack"
        assert card.areas_var.get() == "תל אביב"
    finally:
        root.destroy()
