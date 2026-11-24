# -*- coding: utf-8 -*-
"""
דיווחים מהתקשורת בשרת (server/news_layer.py): כבוי כברירת מחדל, תמיד "דווח בתקשורת, לא מאומת",
בלי כותרות (שמות מעורבים), רק רחוב/שכונה נכנסים לניקוד (0.1), עיר = הקשר בלבד, חדשות לבדן לא מעלות
מסלול ל"גבוה", דעיכה ו-30 יום, סינון מעקבי מעצר / כותרות-שאלה / אירוע פרטי-משפחתי. נתונים סינתטיים.
"""
import datetime as dt
import json
import threading

import pytest

from server import news_layer as NL
from server.lifeguard_server import LifeGuardService, create_server
from backend_client import BackendClient

NOW = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc).timestamp()
HERZL = (32.8150, 34.9990)
HAIFA = (32.8191, 34.9984)


def _iso(hours_ago):
    return dt.datetime.fromtimestamp(NOW - hours_ago * 3600, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(i, level="street", lat=HERZL[0], lon=HERZL[1], hours=10, conf=0.9, flags=(), cat="shooting"):
    return {"id": f"news-{i}", "type": "news_report", "verified": False, "category": cat, "category_he": "ירי",
            "title": "ירי ברחוב הרצל: משה כהן נפצע", "snippet": "משה כהן בן 30 נפצע", "url": f"https://x.example/{i}",
            "publisher": "וואלה", "publishers": ["וואלה", "חי פה"], "published_at": _iso(hours),
            "sources": [{"url": f"https://x.example/{i}", "publisher": "וואלה"}],
            "location": {"lat": lat, "lon": lon, "level": level, "name": "הרצל, חיפה" if level == "street" else "חיפה",
                         "precision_m": 300 if level == "street" else 4000, "street_text": "הרצל"},
            "confidence": conf, "classification": {"flags": list(flags)}}


def _data(*items):
    return {"summary": {"generated_at": _iso(0)}, "items": list(items)}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEGUARD_NEWS_FILE", raising=False)
    s = LifeGuardService(str(tmp_path / "n.db"))
    s.register("dana", "StrongPass!2026")
    from server import risk_factors_layer as RF, safety_intel as SI
    s._rf_layer, s._safety_layers = RF.RiskFactors(), SI.Layers()
    import context_risk as CR
    monkeypatch.setattr(CR, "_grid", lambda: None)
    yield s
    s.close()


def _route(p, m=400):
    return [[p[0] - m / 2 / 111000, p[1]], [p[0] + m / 2 / 111000, p[1]]]


def test_off_by_default_and_without_opt_in(svc):
    r = svc.route_safety("dana", routes=[_route(HERZL)], at=NOW)
    assert r["news"] is None and "news" not in r["routes"][0]["components"]          # השרת לא הגדיר קובץ
    svc._news_layer = NL.NewsLayer(_data(_item(1)))
    r = svc.route_safety("dana", routes=[_route(HERZL)], at=NOW)                     # בלי include_news
    assert r["news"] is None and r["routes"][0]["risk_points"] == 0
    intel = svc.street_intel("dana", *HERZL, include_news=True)
    assert any(i["layer"] == "news" for i in intel["items"])
    svc._news_layer = NL.NewsLayer()
    assert any(u["layer"] == "news" for u in svc.street_intel("dana", *HERZL, include_news=True)["unavailable"])


def test_street_report_is_weak_unverified_and_never_shows_names(svc):
    svc._news_layer = NL.NewsLayer(_data(_item(1), _item(2), _item(3)))
    r = svc.route_safety("dana", routes=[_route(HERZL)], at=NOW, include_news=True)["routes"][0]
    seg_pts = [sg["risk"] for sg in r["segments"]]
    assert seg_pts and max(seg_pts) <= NL.NEWS_SEGMENT_CAP + 1e-9                    # 3 דיווחים - עדיין עד 0.2
    assert r["level"] == "low" and any("לא מאומת" in " ".join(sg["reasons"]) for sg in r["segments"])
    items = [i for i in svc.street_intel("dana", *HERZL, include_news=True)["items"] if i["layer"] == "news"]
    blob = json.dumps(items, ensure_ascii=False)
    assert "משה כהן" not in blob and "title" not in items[0] and "snippet" not in items[0]
    assert items[0]["verified"] is False and items[0]["label"] == "דווח בתקשורת, לא מאומת"
    assert items[0]["links"] and "וואלה" in items[0]["text"]


def test_news_alone_never_raises_a_route_to_high():
    assert NL.W_NEWS < 0.3 and NL.NEWS_LEVEL_CAP_PER_KM < 8


def test_city_level_is_context_only(svc, monkeypatch):
    # street_intel משתמש בשעון האמיתי (בקשה חיה); הפריטים מתוארכים ביחס ל-NOW - מקבעים את השעון כדי שהבדיקה
    # לא תיכשל אחרי שבוע (חלון ההקשר העירוני הוא 7 ימים)
    monkeypatch.setattr(NL.time, "time", lambda: NOW)
    svc._news_layer = NL.NewsLayer(_data(_item(1, level="city", lat=HAIFA[0], lon=HAIFA[1]),
                                         _item(2, level="city", lat=HAIFA[0], lon=HAIFA[1])))
    res = svc.route_safety("dana", routes=[_route(HERZL)], at=NOW, include_news=True)
    assert res["routes"][0]["risk_points"] == 0                                       # לא נקודה ולא ניקוד
    assert res["news"]["city_context"][0]["count"] == 2 and res["news"]["bias_note"]
    intel = svc.street_intel("dana", *HERZL, include_news=True)
    city = next(i for i in intel["items"] if i["layer"] == "news_city")
    assert "ברמת עיר" in city["text"] and not any(i["layer"] == "news" for i in intel["items"])


@pytest.mark.parametrize("kw", [dict(flags=["followup_arrest_or_investigation"]), dict(flags=["question_headline"]),
                                dict(flags=["private_or_domestic_context"]), dict(conf=0.4), dict(hours=31 * 24),
                                dict(level="none")])
def test_filtered_items_are_never_used(kw):
    layer = NL.NewsLayer(_data(_item(1, **kw)))
    assert layer.near_points(*HERZL, 150, now=NOW) == [] and not layer.city_context(*HERZL, now=NOW)


def test_decay_halves_by_category_and_stops_at_30_days():
    assert NL.decay(0, "shooting") == 1 and NL.decay(10 * 86400, "shooting") == pytest.approx(0.5)
    assert NL.decay(3 * 86400, "riot") == pytest.approx(0.5) and NL.decay(31 * 86400, "homicide") == 0


def test_loaded_from_env_file_over_http(tmp_path, monkeypatch):
    f = tmp_path / "news.json"
    f.write_text(json.dumps(_data(_item(1, hours=1)), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("LIFEGUARD_NEWS_FILE", str(f))
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "h.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = BackendClient(f"http://127.0.0.1:{httpd.server_address[1]}")
        c.login_or_register("dana", "StrongPass!2026")
        assert not any(i["layer"] == "news" for i in c.street_intel_at(*HERZL)["items"])          # ברירת מחדל: כבוי
        assert any(i["layer"] == "news" for i in c.street_intel_at(*HERZL, include_news=True)["items"])
        assert c.route_safety(routes=[_route(HERZL)], include_news=True)["news"]["label"] == NL.LABEL_HE
    finally:
        httpd.shutdown()
        httpd.service.close()


def test_route_text_shows_news_context_with_label():
    from app_screens import route_safety_text
    res = {"phase": "day", "routes": [{"level": "low", "components": {"lighting": {"level": "low", "per_km": 0},
                                                                      "news": {"level": "low", "per_km": 0.2}},
                                       "data": {"reliability": "low"}, "segments": []}],
           "news": {"label": NL.LABEL_HE, "bias_note": NL.BIAS_NOTE,
                    "city_context": [{"city": "חיפה", "count": 2, "categories": ["ירי"]}]}}
    t = route_safety_text(res)
    assert "2 דיווחים בתקשורת בחיפה" in t and "לא מאומת" in t and "היעדר דיווח" in t and "תקשורת" in t
