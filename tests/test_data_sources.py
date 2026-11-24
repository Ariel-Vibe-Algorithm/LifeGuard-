# -*- coding: utf-8 -*-
"""
שכבת המקורות החיצוניים (server/data_sources.py), Mapillary בצד השרת, ו-Siren כמקור משלים בלבד.
הכל offline: HTTP מדומה (שרת מקומי / urlopen מדומה). אף בקשה לא יוצאת לאינטרנט.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from server import data_sources as DS
from server import mapillary_client as MC
from server import national as N
from server.lifeguard_server import LifeGuardService, create_server

PW = "StrongPass!2026"
SECRET_SIREN = "pr_TOPSECRET_siren_value_123"
SECRET_MLY = "MLY|999|TOPSECRET_mapillary"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("MAPILLARY_TOKEN", "MAPILLARY_TOKEN_FILE", "LIFEGUARD_SIREN_KEY", "LIFEGUARD_SIREN_KEY_FILE",
              "LIFEGUARD_OREF_ALERTS_URL", "LIFEGUARD_DATA_CACHE", "LIFEGUARD_OSRM_URL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def no_network(monkeypatch):
    """כל ניסיון urlopen נרשם ונכשל - מוודא ש'לא מוגדר' באמת לא שולח כלום."""
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("network call attempted")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    return calls


@pytest.fixture
def svc(tmp_path):
    s = LifeGuardService(str(tmp_path / "ds.db"))
    yield s
    s.close()


# ---------------------------------------------------------------- registry
def test_registry_complete_and_well_formed():
    ids = {s["id"] for s in DS.SOURCES}
    assert len(ids) == len(DS.SOURCES)                                   # מזהים ייחודיים
    for must in ("overpass", "osrm_fossgis_foot", "osrm_demo", "nominatim", "mapillary", "siren", "open_meteo",
                 "cbs_ped_accidents", "streetlights_gis", "police_crime_statarea", "osm_safe_places",
                 "gdelt_news", "oref_alerts", "official_shelters"):
        assert must in ids, must
    for s in DS.SOURCES:
        for f in DS.REQUIRED:
            assert f in s, (s["id"], f)
        assert s["kind"] in DS.KINDS
        assert s.get("reliability", "low") in DS.RELIABILITY
        assert s["commercial_ok"] in (True, False, "unknown")
        assert s["terms_checked_at"] and s["terms_url"] and s["attribution"]
        assert s["key_env"] is None or s["key_env"].isupper()           # שם משתנה, לא ערך
    # "חינם" ≠ "מותר מסחרית": Open-Meteo החינמי הוא לא-מסחרי; שרת הדמו של OSRM לא שמיש להליכה
    assert DS.source("open_meteo")["commercial_ok"] is False
    assert DS.source("osrm_demo")["usable_in_israel"] is False
    assert DS.source("gdelt_news")["kind"] == "news_unverified"
    assert DS.source("mapillary")["kind"] == "estimate"
    assert DS.source("oref_alerts")["kind"] == "verified_official"
    assert DS.source("siren")["kind"] != "verified_official"             # קהילתי, לא רשמי


def test_no_key_hardcoded_in_client_code():
    """אף מפתח/token לא בקוד לקוח (PWA / Windows): לא שם משתנה Mapillary/Siren ולא תבנית מפתח."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    web = os.path.join(root, "server", "web")
    hits = []
    for base, _, files in os.walk(web):
        for f in files:
            if f.endswith((".js", ".html", ".json", ".css")):
                txt = open(os.path.join(base, f), encoding="utf-8", errors="ignore").read()
                if "MLY|" in txt or "MAPILLARY_TOKEN" in txt or "SIREN_KEY" in txt or "x-api-key" in txt.lower():
                    hits.append(f)
    assert not hits


# ---------------------------------------------------------------- record wrapper
def test_record_wrapper_shape_and_validation():
    r = DS.record("cbs_ped_accidents", {"n": 3}, fetched_at=100.0, source_updated_at=50.0, precision_m=10)
    assert set(r) >= {"source_id", "fetched_at", "source_updated_at", "precision_m", "reliability", "kind", "data"}
    assert r["kind"] == "verified_official" and r["reliability"] == "high" and r["fetched_at"] == 100.0
    assert DS.record("user_reports", 1)["kind_label"].startswith("דיווח משתמשים")
    with pytest.raises(ValueError):
        DS.record("mapillary", 1, kind="truth")
    with pytest.raises(ValueError):
        DS.record("mapillary", 1, reliability="certain")
    with pytest.raises(KeyError):
        DS.record("made_up_source", 1)


# ---------------------------------------------------------------- cache
def test_cache_ttl_stale_and_forbidden_sources(tmp_path):
    now = [1000.0]
    c = DS.DataCache(str(tmp_path / "sub" / "c.sqlite"), clock=lambda: now[0])
    assert c.put("open_meteo", "tlv", {"t": 21.5}, source_updated_at=990.0)
    assert c.get("open_meteo", "tlv")["value"] == {"t": 21.5}
    now[0] += DS.source("open_meteo")["cache_ttl_s"] + 1
    assert c.get("open_meteo", "tlv") is None                             # פג תוקף
    stale = c.get("open_meteo", "tlv", allow_stale=True)                 # offline: מוחזר ומסומן ישן
    assert stale["stale"] and stale["value"]["t"] == 21.5 and stale["source_updated_at"] == 990.0
    assert c.purge_expired() == 1 and c.get("open_meteo", "tlv", allow_stale=True) is None
    # התרעות בזמן אמת לא נשמרות לעולם (TTL=0)
    assert not c.put("siren", "active", {"missiles": ["x"]})
    assert not c.put("oref_alerts", "active", {"x": 1})
    assert c.get("siren", "active", allow_stale=True) is None
    # ללא תפוגה (None) - שכבות מקומיות
    c.put("user_reports", "k", 1)
    now[0] += 10 ** 9
    assert c.get("user_reports", "k")["value"] == 1
    c.close()
    # קבוע בין הפעלות
    c2 = DS.DataCache(str(tmp_path / "sub" / "c.sqlite"), clock=lambda: now[0])
    assert c2.get("user_reports", "k")["value"] == 1
    c2.close()


def test_cache_lives_next_to_server_db_not_repo_data(tmp_path, monkeypatch):
    p = DS.default_cache_path(str(tmp_path / "x" / "lifeguard_server.db"))
    assert p == os.path.join(str(tmp_path / "x"), "data_cache.sqlite")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.abspath(p).startswith(os.path.join(root, "data") + os.sep)
    monkeypatch.setenv("LIFEGUARD_DATA_CACHE", str(tmp_path / "custom.sqlite"))
    assert DS.default_cache_path("whatever.db") == str(tmp_path / "custom.sqlite")


# ---------------------------------------------------------------- status / endpoint
def test_status_not_configured_without_keys_and_no_network(svc, no_network):
    rep = DS.report(DS.cache_for(svc))
    by = {s["id"]: s for s in rep["sources"]}
    assert by["mapillary"]["status"]["state"] == "not_configured"
    assert by["siren"]["status"]["state"] == "not_configured"
    assert by["oref_alerts"]["status"]["state"] == "not_configured"
    assert by["open_meteo"]["status"]["state"] == "configured"           # בלי מפתח; עדיין לא נבדק
    assert by["mapillary"]["status"]["key_present"] is False
    res = DS.check_all(DS.cache_for(svc), only={"mapillary", "siren"})
    assert res == {"mapillary": {"skipped": "not_configured"}, "siren": {"skipped": "not_configured"}}
    assert no_network == []


def test_check_all_records_reachable_and_unreachable(svc, monkeypatch):
    class R:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        if "open-meteo" in req.full_url:
            return R()
        raise urllib.error.URLError("down")
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    cache = DS.cache_for(svc)
    res = DS.check_all(cache, timeout=1, only={"open_meteo", "overpass"})
    assert res["open_meteo"]["ok"] and not res["overpass"]["ok"]
    by = {s["id"]: s for s in DS.report(cache)["sources"]}
    assert by["open_meteo"]["status"]["state"] == "reachable"
    assert by["overpass"]["status"]["state"] == "unreachable"
    assert by["overpass"]["status"]["last_check"]["checked_at"] > 0


def test_http_endpoint_public_and_never_serializes_keys(tmp_path, monkeypatch, no_network):
    monkeypatch.setenv("LIFEGUARD_SIREN_KEY", SECRET_SIREN)
    keyfile = tmp_path / "mly.secret"
    keyfile.write_text(SECRET_MLY, encoding="utf-8")
    monkeypatch.setenv("MAPILLARY_TOKEN_FILE", str(keyfile))           # קובץ סוד של הפריסה
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "h.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = httpd.server_address[1]
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/v1/data-sources")                         # ציבורי - בלי token
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        assert resp.status == 200
        assert SECRET_SIREN not in raw and "TOPSECRET" not in raw and str(keyfile) not in raw
        body = json.loads(raw)
        by = {s["id"]: s for s in body["sources"]}
        assert by["siren"]["key_env"] == "LIFEGUARD_SIREN_KEY" and by["siren"]["status"]["key_present"] is True
        assert by["mapillary"]["status"]["state"] == "configured"
        assert "local_file" not in by["official_shelters"]               # בלי נתיבי שרת פנימיים
        # נתיב הבדיקה החיה - מנהל/ת בלבד
        conn.request("POST", "/v1/admin/data-sources/check", body=b"{}", headers={"Content-Type": "application/json",
                                                                                 "Content-Length": "2"})
        r2 = conn.getresponse()
        r2.read()
        assert r2.status == 404
        # Mapillary - משתמש/ת מחובר/ת בלבד
        conn.request("GET", "/v1/data/mapillary?lat=32.08&lon=34.78")
        r3 = conn.getresponse()
        r3.read()
        assert r3.status == 401
        conn.close()
    finally:
        httpd.shutdown()
        httpd.service.close()
    assert no_network == []                                               # ה-GET לא פונה לרשת


# ---------------------------------------------------------------- Mapillary (server side)
@pytest.fixture
def fake_mapillary():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            seen.append((u.path, parse_qs(u.query), self.headers.get("Authorization")))
            if self.headers.get("Authorization") != f"OAuth {SECRET_MLY}":
                body = json.dumps({"error": {"message": "Invalid OAuth 2.0 Access Token", "code": 190}}).encode()
                self.send_response(500)
            elif u.path == "/images":
                body = json.dumps({"data": [
                    {"id": 111, "captured_at": 1700000000000, "is_pano": False, "creator": {"username": "alice"},
                     "computed_geometry": {"type": "Point", "coordinates": [34.7818, 32.0854]},
                     "thumb_1024_url": "https://scontent.example.fbcdn.net/x.jpg"},
                    {"id": 222, "is_pano": True, "computed_geometry": {"coordinates": [34.7818, 32.0853]}},
                    {"id": 333, "captured_at": 1600000000000, "is_pano": False, "creator": {"username": "bob"},
                     "computed_geometry": {"coordinates": [34.7830, 32.0860]}},
                    {"id": 444, "captured_at": 1650000000000, "is_pano": False,
                     "computed_geometry": {"coordinates": [34.7900, 32.0900]}}]}).encode()   # מחוץ לרדיוס
                self.send_response(200)
            elif u.path == "/map_features":
                body = json.dumps({"data": [
                    {"id": 9001, "object_value": "object--street-light", "last_seen_at": 1700000000000,
                     "geometry": {"type": "Point", "coordinates": [34.7819, 32.0853]}},
                    {"id": 9002, "object_value": "object--street-light", "last_seen_at": 1690000000000,
                     "geometry": {"type": "Point", "coordinates": [34.7825, 32.0857]}},
                    {"id": 9003, "object_value": "object--bench",
                     "geometry": {"type": "Point", "coordinates": [34.7819, 32.0853]}}]}).encode()
                self.send_response(200)
            else:
                body = b"{}"
                self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()


def test_mapillary_not_configured_sends_nothing(no_network):
    c = MC.MapillaryClient()
    assert not c.configured
    out = c.observe(32.0853, 34.7818)
    assert out["status"] == "not_configured" and no_network == []


def test_mapillary_records_shape_attribution_and_cache(fake_mapillary, tmp_path):
    base, seen = fake_mapillary
    cache = DS.DataCache(str(tmp_path / "c.sqlite"))
    c = MC.MapillaryClient(cache=cache, token=SECRET_MLY, base_url=base)
    assert SECRET_MLY not in repr(c)
    out = c.observe(32.0853, 34.7818, 150)
    assert out["status"] == "ok"
    cov, lights = out["coverage"], out["street_lights"]
    for rec in (cov, lights):
        assert rec["source_id"] == "mapillary" and rec["kind"] == "estimate" and rec["reliability"] == "low"
        assert rec["fetched_at"] > 0 and rec["precision_m"] and "CC BY-SA 4.0" in rec["attribution"]["text"]
        assert rec["attribution"]["logo_required"] is True
    assert cov["data"]["image_count"] == 2                                  # פנורמה + רחוקה סוננו
    imgs = {a["image_id"]: a for a in cov["attribution"]["images"]}
    assert imgs["111"]["creator"] == "alice" and imgs["111"]["license"] == "CC BY-SA 4.0"
    assert "pKey=111" in imgs["111"]["url"]
    assert cov["source_updated_at"] == 1700000000.0
    assert lights["data"]["detected_street_lights"] == 2                   # ספסל לא נספר
    assert "היעדר זיהוי" in lights["data"]["note"]
    # שום כתובת תמונה (thumb) לא נשמרת/מוחזרת, ושום תמונה לא הורדה
    blob = json.dumps(out, ensure_ascii=False)
    assert "fbcdn" not in blob and "thumb" not in blob and SECRET_MLY not in blob
    assert {p for p, _, _ in seen} == {"/images", "/map_features"}
    assert all(auth == f"OAuth {SECRET_MLY}" for _, _, auth in seen)
    q = seen[0][1]
    b = [float(x) for x in q["bbox"][0].split(",")]
    assert (b[2] - b[0]) * (b[3] - b[1]) < 0.01                             # מגבלת bbox של Mapillary
    assert "thumb_1024_url" not in q["fields"][0]
    # מטמון: בקשה חוזרת לא יוצאת לרשת
    n = len(seen)
    again = c.observe(32.0853, 34.7818, 150)
    assert len(seen) == n and again["coverage"].get("cached") is True
    assert cache.last_check("mapillary")["ok"] is True


def test_mapillary_bad_token_is_unreachable_not_fake_data(fake_mapillary, tmp_path):
    base, _ = fake_mapillary
    cache = DS.DataCache(str(tmp_path / "c.sqlite"))
    out = MC.MapillaryClient(cache=cache, token="MLY|wrong", base_url=base).observe(32.0853, 34.7818)
    assert out["status"] == "unreachable" and "coverage" not in out
    assert "wrong" not in json.dumps(out)
    assert cache.last_check("mapillary")["ok"] is False


def test_mapillary_server_client_never_downloads_images():
    src = open(MC.__file__, encoding="utf-8").read()
    assert "thumb_1024_url" not in src.split('"""', 2)[2]                 # (רק בתיעוד, לא בקוד)
    assert "cv2" not in src and "imdecode" not in src


# ---------------------------------------------------------------- Siren: supplementary only
class _Resp:
    def __init__(self, body):
        self.body = body.encode() if isinstance(body, str) else body

    def read(self, n=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _route_http(monkeypatch, oref=None, siren=None, oref_exc=None, siren_exc=None, seen=None):
    def fake(req, timeout):
        url = req.full_url
        if seen is not None:
            seen.append((url, dict(req.header_items())))
        if "siren.co.il" in url:
            if siren_exc:
                raise siren_exc
            return _Resp(json.dumps(siren, ensure_ascii=False))
        if oref_exc:
            raise oref_exc
        return _Resp(json.dumps(oref, ensure_ascii=False) if oref is not None else "")
    monkeypatch.setattr(N.urllib.request, "urlopen", fake)


OREF_TLV = {"id": "9", "cat": "1", "title": "ירי רקטות וטילים", "data": ["תל אביב - מרכז העיר"], "desc": ""}


def _official(st):
    return {k: st["alerts"].get(k) for k in ("status", "current", "source", "configured")}


@pytest.mark.parametrize("siren", [{}, {"endAlert": ["תל אביב - יפו"]}, {"missiles": ["חיפה"]},
                                   {"missilesDrill": ["תל אביב - יפו"]}, "ERROR", "429"])
def test_siren_never_suppresses_or_alters_official_alert(svc, monkeypatch, siren):
    svc.register("dana", PW)
    svc.set_national_areas("dana", ["תל אביב"])
    monkeypatch.setenv("LIFEGUARD_OREF_ALERTS_URL", "http://mirror.invalid/alerts.json")
    _route_http(monkeypatch, oref=OREF_TLV, siren={})
    baseline = _official(svc.national_status("dana"))                    # בלי Siren מוגדר
    assert baseline["current"]["in_my_areas"] and baseline["current"]["official"] is True
    svc._oref_cache = None
    monkeypatch.setenv("LIFEGUARD_SIREN_KEY", SECRET_SIREN)
    exc = None
    if siren == "ERROR":
        exc = OSError("down")
    elif siren == "429":
        exc = urllib.error.HTTPError(N.SIREN_URL, 429, "Too Many", {"Retry-After": "60"}, None)
    _route_http(monkeypatch, oref=OREF_TLV, siren=siren if isinstance(siren, dict) else None, siren_exc=exc)
    st = svc.national_status("dana")
    off = _official(st)
    assert off["current"] == baseline["current"]                         # זהה לחלוטין
    assert st["alerts"]["primary_source"] == "oref"
    sup = st["alerts"]["supplementary"]
    assert sup["official"] is False and sup["attribution_url"] == "https://siren.co.il" and "Siren" in sup["source"]
    if isinstance(siren, dict):
        assert sup["status"] == "ok" and sup["agreement"] == "official_only"
        assert all(not e["type"].endswith("Drill") for e in sup["current"])
    else:
        assert sup["status"] == "error"
    assert SECRET_SIREN not in json.dumps(st, ensure_ascii=False)


def test_siren_only_alert_is_shown_labelled_and_official_absence_kept(svc, monkeypatch):
    svc.register("dana", PW)
    svc.set_national_areas("dana", ["תל אביב"])
    monkeypatch.setenv("LIFEGUARD_SIREN_KEY", SECRET_SIREN)
    seen = []
    # 1) המקור הרשמי לא מוגדר; Siren מדווח -> מוצג כמשלים, הרשמי נשאר "לא מוגדר"
    _route_http(monkeypatch, siren={"missiles": ["תל אביב - יפו"], "newsFlash": ["חיפה"]}, seen=seen)
    st = svc.national_status("dana")
    assert st["alerts"]["status"] == "not_configured" and "current" not in st["alerts"]
    sup = st["alerts"]["supplementary"]
    assert sup["agreement"] == "official_unavailable" and "פיקוד העורף" in sup["text"]
    m = [e for e in sup["current"] if e["kind"] == "missiles"][0]
    assert m["in_my_areas"] and m["source"].startswith("Siren") and "מרחב מוגן" in m["instructions"][0]
    assert [e for e in sup["current"] if e["kind"] == "early_warning"][0]["in_my_areas"] is False
    url, headers = [x for x in seen if "siren" in x[0]][0]
    assert url == "https://api.siren.co.il/active"
    assert {k.lower(): v for k, v in headers.items()}["x-api-key"] == SECRET_SIREN   # בכותרת, לא ב-URL
    assert SECRET_SIREN not in json.dumps(st, ensure_ascii=False)
    # 2) המקור הרשמי מוגדר ואין התרעה; Siren מדווח -> "siren_only", הרשמי נשאר None
    monkeypatch.setenv("LIFEGUARD_OREF_ALERTS_URL", "http://mirror.invalid/alerts.json")
    svc._siren_cache = None
    _route_http(monkeypatch, oref=None, siren={"missiles": ["תל אביב - יפו"]})
    st = svc.national_status("dana")
    assert st["alerts"]["status"] == "ok" and st["alerts"]["current"] is None
    assert st["alerts"]["supplementary"]["agreement"] == "siren_only"


def test_siren_public_key_refused_server_side(svc, monkeypatch, no_network):
    svc.register("dana", PW)
    monkeypatch.setenv("LIFEGUARD_SIREN_KEY", "pb_public_browser_key")
    sup = svc.national_status("dana")["alerts"]["supplementary"]
    assert sup["status"] == "error" and "pr_" in sup["error"] and no_network == []


def test_siren_not_configured_sends_nothing(svc, no_network):
    svc.register("dana", PW)
    st = svc.national_status("dana")
    assert st["alerts"]["supplementary"]["status"] == "not_configured" and no_network == []


def test_siren_429_backs_off(svc, monkeypatch):
    monkeypatch.setenv("LIFEGUARD_SIREN_KEY", SECRET_SIREN)
    calls = []

    def fake(req, timeout):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(N.SIREN_URL, 429, "Too Many", {"Retry-After": "120"}, None)
    monkeypatch.setattr(N.urllib.request, "urlopen", fake)
    assert svc._siren_fetch()["error"] == "HTTP 429"
    svc._siren_cache = None
    assert svc._siren_fetch()["error"] == "rate_limited" and len(calls) == 1
    assert svc._siren_backoff_until - time.time() > 100
    by = {s["id"]: s for s in DS.report(DS.cache_for(svc))["sources"]}
    assert by["siren"]["status"]["state"] == "unreachable"


def test_parse_siren_active():
    items = N.parse_siren_active(json.dumps({"missiles": ["א", ""], "earthQuakeDrill": ["ב"],
                                             "hostileAircraftIntrusion": ["ג"], "somethingNew": ["ד"],
                                             "general": []}))
    kinds = {i["type"]: i["kind"] for i in items}
    assert kinds == {"missiles": "missiles", "hostileAircraftIntrusion": "aircraft", "somethingNew": "general"}
    assert N.parse_siren_active(b"{}") == [] and N.parse_siren_active("") == []
    with pytest.raises(ValueError):
        N.parse_siren_active("[1,2]")
