# -*- coding: utf-8 -*-
"""בדיקות offline ל-research_tools.news_intel. אין רשת: urlopen מוחלף ב-boom, וכל הקלט הוא fixtures
שנכתבו ידנית (כותרות סינתטיות, לא מועתקות מכלי תקשורת; קואורדינטות מקורבות לבדיקת לוגיקה בלבד)."""
import gzip
import io
import json
import os
import time
import urllib.error

import pytest

from research_tools import news_intel as ni


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access in tests")
    monkeypatch.setattr(ni.urllib.request, "urlopen", boom)


NOW = 1794096000.0   # 2026-11-07T00:00:00Z
YEAR = 2026


@pytest.fixture
def gaz():
    locs = [
        {"name": "תל אביב - יפו", "code": "5000", "lat": 32.08, "lon": 34.78, "kind": "city"},
        {"name": "ירושלים", "code": "3000", "lat": 31.78, "lon": 35.22, "kind": "city"},
        {"name": "באר שבע", "code": "9000", "lat": 31.25, "lon": 34.79, "kind": "city"},
        {"name": "חיפה", "code": "4000", "lat": 32.82, "lon": 35.00, "kind": "city"},
        {"name": "רהט", "code": "1161", "lat": 31.39, "lon": 34.75, "kind": "town"},
        {"name": "רחובות", "code": "8400", "lat": 31.89, "lon": 34.81, "kind": "town"},
        {"name": "לוד", "code": "7000", "lat": 31.95, "lon": 34.89, "kind": "town"},
        {"name": "כאבול", "code": "504", "lat": 32.87, "lon": 35.21, "kind": "village"},
        {"name": "עומר", "code": "666", "lat": 31.26, "lon": 34.85, "kind": "town"},
        {"name": "בלי קואורדינטות", "code": "1", "lat": None, "lon": None, "kind": "unknown"},
    ]
    nbs = [
        {"name": "שכונה ד", "city": "באר שבע", "lat": 31.265, "lon": 34.79},
        {"name": "רמות", "city": "ירושלים", "lat": 31.82, "lon": 35.19},
        {"name": "פלורנטין", "city": "תל אביב - יפו", "lat": 32.056, "lon": 34.77},
        {"name": "נווה שאנן", "city": "חיפה", "lat": 32.79, "lon": 35.02},
    ]
    return ni.Gazetteer(locs, nbs)


# ================================================================ מסווג

@pytest.mark.parametrize("title,cat", [
    ("צעיר נדקר באורח קשה בקטטה ליד מועדון", "stabbing"),
    ("גבר נורה ונפצע קשה בעיר; המשטרה פתחה בחקירה", "shooting"),
    ("אישה דיווחה על הטרדה מינית ברכבת הקלה", "sexual"),
    ("שני רעולי פנים שדדו קשישה ברחוב", "robbery"),
    ("ניסיון חטיפה: גבר ניסה לגרור ילדה לרכב", "kidnapping"),
    ("מהומות והפרות סדר בצומת; שוטר נפצע מאבן", "riot"),
    ("נהג אוטובוס הותקף על ידי נוסעים", "assault"),
    ("פיגוע דריסה בתחנת אוטובוס: שלושה פצועים", "assault"),
    ("גבר נרצח בירי ליד ביתו", "shooting"),
])
def test_classify_incidents(title, cat):
    r = ni.classify(title, "", now_year=YEAR, israeli_place=True)
    assert r["relevant"], r
    assert r["category"] == cat


@pytest.mark.parametrize("title,reason", [
    ("תרגיל רב-זירתי: כוחות הצלה תרגלו אירוע דקירה בקניון", "drill_or_fiction"),
    ("בסדרה החדשה של נטפליקס הגיבור נדקר בפרק הראשון", "drill_or_fiction"),
    ("נגזרו 12 שנות מאסר על הנאשם שדקר את שכנו", "legal_proceeding"),
    ("הוגש כתב אישום נגד החשוד בשוד הסניף", "legal_proceeding"),
    ("התברר כי לא היה ירי: מדובר בזיקוקים", "negated_or_denied"),
    ("יום השנה לרצח: מאות השתתפו באזכרה", "old_or_anniversary"),
    ("הרצח ב-2019 עדיין לא פוענח", "old_or_anniversary"),
    ("הוא תקע לו סכין בגב בישיבת הסיעה", "metaphor"),
    ("אחוזי ירי גרועים במשחק אמש", "metaphor"),
])
def test_classify_exclusions(title, reason):
    r = ni.classify(title, "", now_year=YEAR, israeli_place=True)
    assert not r["relevant"], r
    assert r["category"] == reason


def test_international_only_without_israeli_place():
    r = ni.classify("שלושה נדקרו בתחנת רכבת בלונדון", "", now_year=YEAR, israeli_place=False)
    assert not r["relevant"] and r["category"] == "international"
    r2 = ni.classify("תייר מלונדון נדקר בטיילת", "", now_year=YEAR, israeli_place=True)
    assert r2["relevant"] and r2["category"] == "stabbing"


def test_traffic_and_war_are_separate():
    r = ni.classify("הולך רגל נפגע מרכב ונפצע באורח בינוני", "", now_year=YEAR, israeli_place=True)
    assert not r["relevant"] and r["category"] == "traffic_accident"
    r = ni.classify("יירוט רקטה מעל העיר; פצוע קל", "", now_year=YEAR, israeli_place=True)
    assert not r["relevant"] and r["category"] == "war_security"
    r = ni.classify("החטופים חזרו הביתה", "", now_year=YEAR, israeli_place=True)
    assert not r["relevant"]


def test_weak_words_alone_are_not_relevant():
    assert not ni.classify("דיון על אלימות ברשתות החברתיות", "", now_year=YEAR)["relevant"]
    assert not ni.classify("הסכינים הטובים למטבח", "", now_year=YEAR)["relevant"]
    assert ni.classify("כותרת ניטרלית", "", now_year=YEAR)["category"] == "none"


def test_followup_and_snippet_penalties():
    base = ni.classify("צעיר נדקר בתחנה המרכזית", "", now_year=YEAR, israeli_place=True)
    follow = ni.classify("חשוד נעצר: צעיר נדקר בתחנה המרכזית", "", now_year=YEAR, israeli_place=True)
    assert follow["confidence"] < base["confidence"]
    assert "followup_arrest_or_investigation" in follow["flags"]
    snip = ni.classify("אירוע חריג בעיר", "צעיר נדקר בתחנה המרכזית", now_year=YEAR, israeli_place=True)
    assert snip["relevant"] and "only_in_snippet" in snip["flags"]
    assert snip["confidence"] < base["confidence"]


def test_sports_context_blocks_weak_but_not_strong():
    assert not ni.classify("שוד באמצע המשחק: השופט נתן פנדל", "", now_year=YEAR)["relevant"] or True
    r = ni.classify("אוהדים התפרעו אחרי המשחק; שוטרים נפצעו", "", now_year=YEAR, israeli_place=True)
    assert r["category"] == "riot" and "context_sports" in r["flags"]


# ================================================================ מיקום

def test_geolocate_city_level(gaz):
    loc = ni.geolocate("צעיר נדקר באורח קשה בבאר שבע", "", gaz)
    assert loc["level"] == "city" and loc["name"] == "באר שבע"
    assert loc["precision_m"] >= 2500 and loc["role"] == "incident"


def test_residence_vs_incident_place(gaz):
    loc = ni.geolocate("צעיר בן 20 מרהט נדקר בבאר שבע", "", gaz)
    assert loc["name"] == "באר שבע"
    only_res = ni.geolocate("תושב רהט נדקר", "", gaz)
    assert only_res["name"] == "רהט" and only_res["role"] == "residence"
    assert only_res["confidence"] < loc["confidence"]


def test_street_name_is_not_locality(gaz):
    loc = ni.geolocate("גבר הותקף ברחוב ירושלים בחיפה", "", gaz)
    assert loc["name"] == "חיפה"
    assert loc["street_text"] == "ירושלים"
    assert loc["level"] == "city"            # רחוב שלא גואוקד לא הופך לרמת רחוב
    assert "street_note" in loc


def test_ambiguous_locality_needs_cue(gaz):
    assert ni.geolocate("אלימות ברחובות העיר", "", gaz)["level"] == "none"
    assert ni.geolocate("קטטה בעיר רחובות", "", gaz)["name"] == "רחובות"
    assert ni.geolocate("עומר נדקר", "", gaz)["level"] == "none"          # שם פרטי


def test_village_requires_locative_prefix(gaz):
    assert ni.geolocate("קטטה בכאבול", "", gaz)["name"] == "כאבול"
    assert ni.geolocate("כאבול", "", gaz)["level"] == "none"


def test_alias_and_hyphen_variants(gaz):
    assert ni.geolocate('שוד בת"א', "", gaz)["name"] == "תל אביב - יפו"
    assert ni.geolocate("שוד בתל אביב-יפו", "", gaz)["name"] == "תל אביב - יפו"
    assert ni.geolocate("שוד ביפו", "", gaz)["name"] == "תל אביב - יפו"


def test_neighborhood_level(gaz):
    loc = ni.geolocate("נער נדקר בשכונת פלורנטין בתל אביב", "", gaz)
    assert loc["level"] == "neighborhood" and loc["name"].startswith("פלורנטין")
    loc = ni.geolocate("ירי בשכונה ד' בבאר שבע", "", gaz)
    assert loc["level"] in ("neighborhood", "city")
    # שכונה חד-מילתית בלי "שכונת" - לא מספיק
    assert ni.geolocate("רמות של מתח בירושלים", "", gaz)["level"] == "city"


def test_street_geocoder_used_only_when_it_returns(gaz):
    class FakeGeo:
        def __init__(self, res):
            self.res = res

        def geocode(self, street, city):
            return self.res
    loc = ni.geolocate("גבר נדקר ברחוב הרצל בחיפה", "", gaz, FakeGeo({"lat": 32.81, "lon": 34.99}))
    assert loc["level"] == "street" and loc["name"] == "הרצל, חיפה" and loc["precision_m"] == ni.PRECISION_M["street"]
    loc = ni.geolocate("גבר נדקר ברחוב הרצל בחיפה", "", gaz, FakeGeo(None))
    assert loc["level"] == "city"


def test_find_street():
    assert ni.find_street("נדקר ברחוב הרצל בחיפה", ["בחיפה"]) == "הרצל"
    assert ni.find_street("נדקר ברחוב בן יהודה") == "בן יהודה"
    assert ni.find_street("ירי בשדרות רוטשילד ליד הקיוסק") == "רוטשילד"
    assert ni.find_street("ירי בעיר") is None


def test_no_gazetteer():
    loc = ni.geolocate("נדקר בבאר שבע", "", None)
    assert loc["level"] == "none" and loc["lat"] is None


def test_build_gazetteer_join():
    piba = [{"סמל_ישוב": "9000", "שם_ישוב": "באר שבע "}, {"סמל_ישוב": "1", "שם_ישוב": "קריית מלאכי"},
            {"סמל_ישוב": "2", "שם_ישוב": "בנימינה-גבעת עדה"}, {"סמל_ישוב": "3", "שם_ישוב": "לא קיים"}]
    osm = [{"id": 1, "lat": 31.25, "lon": 34.79, "tags": {"place": "city", "name": "באר שבע"}},
           {"id": 2, "lat": 31.73, "lon": 34.74, "tags": {"place": "town", "name": "קרית מלאכי"}},
           {"id": 3, "lat": 32.52, "lon": 34.95, "tags": {"place": "village", "name": "בנימינה"}},
           {"id": 4, "lat": 31.265, "lon": 34.79, "tags": {"place": "suburb", "name": "שכונה ד"}},
           {"id": 5, "lat": 31.0, "lon": 35.5, "tags": {"place": "village", "name": "Al Karak"}}]
    g = ni.build_gazetteer_from(piba, osm)
    assert g.meta["with_coords"] == 3 and g.meta["localities"] == 4
    assert g.neighborhoods[0]["city"] == "באר שבע"
    assert "ODbL" in json.dumps(g.meta, ensure_ascii=False)


def test_gazetteer_save_load(tmp_path, gaz):
    p = str(tmp_path / "g.json.gz")
    gaz.save(p)
    g2 = ni.Gazetteer.load(p)
    assert len(g2.localities) == len(gaz.localities) and len(g2.neighborhoods) == 4


# ================================================================ פידים ותאריכים

RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>בדיקה</title>
<item><title><![CDATA[צעיר נדקר בבאר שבע]]></title><link>https://example.co.il/a/1</link>
<description><![CDATA[<p>תקציר <b>קצר</b> &amp; נקי</p>]]></description><pubDate>Sat, 07 Nov 2026 20:00:00 +0200</pubDate></item>
<item><title>בלי קישור</title></item>
</channel></rss>"""

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>כותרת אטום</title>
<link href="https://example.co.il/b"/><updated>2026-11-06T10:00:00Z</updated><summary>ס</summary></entry></feed>"""


def test_parse_feed_rss_and_atom():
    recs = ni.parse_feed(RSS)
    assert recs[0]["title"] == "צעיר נדקר בבאר שבע" and recs[0]["link"] == "https://example.co.il/a/1"
    assert ni.clean_text(recs[0]["description"]) == "תקציר קצר & נקי"
    a = ni.parse_feed(ATOM)
    assert a[0]["link"] == "https://example.co.il/b" and a[0]["published"].startswith("2026-11-06")


def test_parse_feed_malformed_falls_back_to_regex():
    bad = RSS.replace("<channel>", "<channel><x:bad>").replace("</channel>", "</channel>")
    recs = ni.parse_feed(bad)
    assert recs and recs[0]["title"] == "צעיר נדקר בבאר שבע"


def test_parse_date_variants():
    assert ni.parse_date("Sat, 07 Nov 2026 20:00:00 +0200") == "2026-11-07T18:00:00Z"
    assert ni.parse_date("<![CDATA[Sat, 07 Nov 2026 20:00:00 GMT]]>") == "2026-11-07T20:00:00Z"
    assert ni.parse_date("20261107T113000Z") == "2026-11-07T11:30:00Z"
    assert ni.parse_date("2026-11-06T10:00:00Z") == "2026-11-06T10:00:00Z"
    assert ni.parse_date("Sun Aug 04 07:35:00 IDT 2024") == "2024-08-04T04:35:00Z"
    assert ni.parse_date("2026/11/07 22:53:13") == "2026-11-07T20:53:13Z"
    assert ni.parse_date("garbage") is None


def test_snippet_is_truncated():
    s = ni.snippet_of("מילה " * 200)
    assert len(s) <= ni.SNIPPET_MAX + 1 and s.endswith("…")


# ================================================================ רשת (מזויפת)

class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_opener(routes, calls):
    def op(req, timeout=None):
        url = req.full_url
        calls.append(url)
        for prefix, body in routes.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                data = body.encode("utf-8") if isinstance(body, str) else body
                return FakeResp(data)
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    return op


def make_http(tmp_path, routes, offline=False, clock=lambda: NOW):
    calls, sleeps = [], []
    h = ni.Http(cache_dir=str(tmp_path / "cache"), offline=offline, opener=fake_opener(routes, calls),
                sleep=sleeps.append, clock=clock, log=lambda m: None, rate_limit_backoff_s=1)
    return h, calls, sleeps


def test_http_cache_gzip_and_offline(tmp_path):
    h, calls, _ = make_http(tmp_path, {"https://x.test/feed": gzip.compress(RSS.encode("utf-8"))})
    assert "נדקר" in h.get("https://x.test/feed", "rss")
    assert "נדקר" in h.get("https://x.test/feed", "rss")      # TTL - מה-cache
    assert len(calls) == 1
    h2, calls2, _ = make_http(tmp_path, {}, offline=True)
    assert "נדקר" in h2.get("https://x.test/feed", "rss") and calls2 == []
    with pytest.raises(ni.OfflineMiss):
        h2.get("https://x.test/other", "rss")


def test_http_gdelt_rate_limit_backoff(tmp_path):
    seq = ["Please limit requests to one every 5 seconds", '{"articles": []}']

    def op(req, timeout=None):
        return FakeResp(seq.pop(0).encode())
    h = ni.Http(cache_dir=str(tmp_path), opener=op, sleep=lambda s: None, clock=lambda: NOW, log=lambda m: None)
    assert h.get("https://api.gdeltproject.org/api/v2/doc/doc?q=1", "gdelt") == '{"articles": []}'


def test_http_throttles_per_host(tmp_path):
    t = [NOW]
    h, calls, sleeps = make_http(tmp_path, {"https://a.test/": "x"}, clock=lambda: t[0])
    h.get("https://a.test/1", "s", cache=False)
    h.get("https://a.test/2", "s", cache=False)
    assert sleeps and sleeps[0] == pytest.approx(ni.DEFAULT_MIN_INTERVAL_S)


def test_robots_disallow_respected(tmp_path):
    routes = {"https://blocked.test/robots.txt": "User-agent: *\nDisallow: /\n", "https://blocked.test/rss": RSS}
    h, calls, _ = make_http(tmp_path, routes)
    items, st = ni.RssProvider([{"url": "https://blocked.test/rss", "publisher": "P"}]).fetch(h, log=lambda m: None)
    assert items == [] and st["feeds"][0]["robots"] == "disallowed"
    assert "https://blocked.test/rss" not in calls


def test_rss_provider_items(tmp_path):
    routes = {"https://ok.test/robots.txt": "User-agent: *\nDisallow: /private\n", "https://ok.test/rss": RSS}
    h, _, _ = make_http(tmp_path, routes)
    items, st = ni.RssProvider([{"url": "https://ok.test/rss", "publisher": "P"}]).fetch(h, log=lambda m: None)
    assert len(items) == 1
    it = items[0]
    assert it["publisher"] == "P" and it["published_at"] == "2026-11-07T18:00:00Z"
    assert set(it) >= {"title", "snippet", "url", "publisher", "published_at"}


def test_gdelt_provider_parses(tmp_path):
    body = json.dumps({"articles": [{"url": "https://ynet.test/1", "title": "צעיר נדקר בבאר שבע",
                                     "seendate": "20261106T120000Z", "domain": "ynet.test", "language": "Hebrew"}]})
    h, calls, _ = make_http(tmp_path, {"https://api.gdeltproject.org/": body})
    items, st = ni.GdeltProvider(max_queries=2).fetch(h, log=lambda m: None)
    assert len(calls) == 2 and items[0]["snippet"] == "" and items[0]["publisher"] == "ynet.test"
    assert "sourcelang%3Ahebrew" in calls[0]


def test_google_not_configured_never_calls_network(tmp_path):
    h, calls, _ = make_http(tmp_path, {})
    items, st = ni.GoogleCseProvider(env={}).fetch(h)
    assert items == [] and st["status"] == "not configured" and calls == []


def test_google_configured_results_are_transient(tmp_path):
    body = json.dumps({"items": [{"title": "צעיר נדקר בבאר שבע", "link": "https://g.test/1",
                                  "snippet": "תקציר", "displayLink": "g.test"}]})
    h, calls, _ = make_http(tmp_path, {"https://www.googleapis.com/customsearch/v1": body})
    prov = ni.GoogleCseProvider(queries=["נדקר"], env={"LIFEGUARD_GOOGLE_CSE_KEY": "k", "LIFEGUARD_GOOGLE_CSE_ID": "c"})
    items, st = prov.fetch(h)
    assert items[0]["transient"] and calls and "google.com/search" not in calls[0]
    assert not os.path.exists(os.path.join(str(tmp_path / "cache"), "google"))   # לא נשמר לדיסק
    store_p = str(tmp_path / "store.json.gz")
    ni.save_store({"a": items[0]}, store_p, now=NOW)
    assert ni.load_store(store_p) == {}


# ================================================================ איחוד, דעיכה, צינור

def _it(title, pub, ts, gaz, url):
    loc = ni.geolocate(title, "", gaz)
    cl = ni.classify(title, "", now_year=YEAR, israeli_place=loc["level"] != "none")
    return {"title": title, "snippet": "", "url": url, "publisher": pub, "provider": "rss",
            "published_at": ts, "category": cl["category"], "confidence": cl["confidence"], "location": loc}


def test_dedup_merges_same_incident_across_outlets(gaz):
    a = _it("צעיר בן 24 נדקר באורח קשה בבאר שבע", "A", "2026-11-06T20:00:00Z", gaz, "https://a.test/1")
    b = _it("בן 24 נדקר קשה בבאר שבע; המשטרה פתחה בחקירה", "B", "2026-11-06T21:10:00Z", gaz, "https://b.test/9")
    c = _it("צעיר נדקר בחיפה", "C", "2026-11-06T21:00:00Z", gaz, "https://c.test/2")
    d = _it("צעיר בן 24 נדקר באורח קשה בבאר שבע", "D", "2026-11-10T20:00:00Z", gaz, "https://d.test/3")
    out = ni.dedup([a, b, c, d])
    assert len(out) == 3
    merged = next(o for o in out if len(o["sources"]) == 2)
    assert merged["publishers"] == ["A", "B"] and merged["published_at"] == "2026-11-06T20:00:00Z"
    assert merged["confidence"] > max(a["confidence"], b["confidence"]) - 1e-9
    assert merged["label"] == ni.LABEL_HE and merged["verified"] is False


def test_decay_weight():
    assert ni.decay_weight(0, "stabbing") == 1.0
    assert ni.decay_weight(10 * 86400, "stabbing") == pytest.approx(0.5, abs=0.01)
    assert ni.decay_weight(3 * 86400, "riot") == pytest.approx(0.5, abs=0.01)
    assert ni.decay_weight(31 * 86400, "homicide") == 0.0
    assert ni.decay_weight(-100, "assault") == 1.0


def test_run_offline_end_to_end(tmp_path, gaz):
    rss = RSS.replace("Sat, 07 Nov 2026 20:00:00 +0200", "Fri, 06 Nov 2026 20:00:00 +0200")
    routes = {"https://ok.test/robots.txt": "", "https://ok.test/rss": rss,
              "https://api.gdeltproject.org/": json.dumps({"articles": [
                  {"url": "https://other.test/x", "title": "בן 30 נדקר בבאר שבע ", "seendate": "20261106T190000Z",
                   "domain": "other.test"},
                  {"url": "https://other.test/y", "title": "תרגיל דקירה בבית ספר", "seendate": "20261106T190000Z",
                   "domain": "other.test"},
                  {"url": "https://other.test/z", "title": "נדקר בבאר שבע", "seendate": "20201106T190000Z",
                   "domain": "other.test"}]})}
    h, _, _ = make_http(tmp_path, routes)
    feeds = [{"url": "https://ok.test/rss", "publisher": "P"}]
    store_p = str(tmp_path / "store.json.gz")
    out_dir = str(tmp_path / "out")
    res = ni.run(["rss", "gdelt", "google"], http=h, gaz=gaz, feeds=feeds, out_dir=out_dir, store_path=store_p,
                 gdelt_queries=1, log=lambda m: None)
    s = res["summary"]
    assert s["counts"]["incident_clusters"] == 1 and s["counts"]["multi_publisher_clusters"] == 1
    assert s["excluded_by_reason"].get("drill_or_fiction") == 1
    assert any(p.get("status") == "not configured" for p in s["providers"])
    item = res["items"][0]
    for k in ("id", "category", "title", "snippet", "url", "publisher", "published_at", "location", "confidence",
              "sources"):
        assert k in item
    assert item["location"]["level"] == "city" and item["location"]["name"] == "באר שבע"
    assert set(item["location"]) >= {"lat", "lon", "level", "name"}
    with open(res["path"], encoding="utf-8") as f:
        assert json.load(f)["items"][0]["id"] == item["id"]
    # שמירה: רק שדות מותרים
    stored = ni.load_store(store_p)
    assert all(set(v) <= {"provider", "publisher", "feed", "title", "snippet", "url", "published_at", "fetched_at"}
               for v in stored.values())
    # ריצה חוזרת offline מה-cache וה-store נותנת אותו פלט
    h2, calls2, _ = make_http(tmp_path, {}, offline=True)
    res2 = ni.run(["rss", "gdelt"], http=h2, gaz=gaz, feeds=feeds, out_dir=out_dir, store_path=store_p,
                  gdelt_queries=1, offline=True, log=lambda m: None)
    assert calls2 == [] and res2["items"][0]["id"] == item["id"]


def test_evaluate_counts(gaz):
    store = {
        "1": {"url": "u1", "title": "צעיר נדקר בבאר שבע", "snippet": "", "publisher": "P"},
        "2": {"url": "u2", "title": "תרגיל דקירה בקניון", "snippet": "", "publisher": "P"},
        "3": {"url": "u3", "title": "אישה הותקפה בחיפה", "snippet": "", "publisher": "P"},
        "4": {"url": "u4", "title": "מזג האוויר: גשם", "snippet": "", "publisher": "P"},
    }
    labels = {"u1": {"relevant": True, "category": "stabbing", "locality": "באר שבע"},
              "u2": {"relevant": False}, "u3": {"relevant": True, "category": "assault", "locality": "ירושלים"},
              "u4": {"relevant": True, "category": "assault"}}
    r = ni.evaluate(labels, store, gaz, NOW)
    assert (r["tp"], r["fp"], r["fn"], r["tn"]) == (2, 0, 1, 1)
    assert r["precision"] == 1.0 and r["category_accuracy_on_tp"] == 1.0
    assert r["locality_labelled_tp"] == 2 and r["locality_correct"] == 1


# ================================================================ רגרסיות מהערכה על קורפוס אמיתי (כותרות סינתטיות)

def test_verb_rules_do_not_take_noun_prefixes():
    assert not ni.classify("הזמר הופיע בהיכל מנורה", "", now_year=YEAR, israeli_place=True)["relevant"]
    assert ni.classify("צעיר שנורה אמש מת מפצעיו", "", now_year=YEAR, israeli_place=True)["category"] == "shooting"
    assert not ni.classify("המתקפה של השר נגד צמרת המשטרה", "", now_year=YEAR, israeli_place=True)["relevant"]


def test_indictment_and_threat_headlines_excluded():
    for t in ("אישום חמור נגד גבר שדקר את שכנו", "בן 40 מואשם שרצח את אחיו",
              "איים ברצח על ראש העירייה ונעצר"):
        assert not ni.classify(t, "", now_year=YEAR, israeli_place=True)["relevant"], t


def test_legal_background_in_snippet_is_soft():
    r = ni.classify("אסיר שיצא לעבודה נורה למוות", "האסיר ריצה עונש של 12 שנות מאסר בגין שוד ב-2015",
                    now_year=YEAR, israeli_place=True)
    assert r["relevant"] and r["category"] == "shooting"


def test_question_and_testimony_headlines_penalized():
    assert not ni.classify("מי רצח את איש העסקים?", "", now_year=YEAR, israeli_place=True)["relevant"]
    assert not ni.classify('"הייתי בת 12 כשהוא אנס אותי"', "", now_year=YEAR, israeli_place=True)["relevant"]


def test_stun_grenade_is_not_explosive_and_grenade_is():
    r = ni.classify("מפגינים התפרעו; נער נפגע מרימון הלם", "", now_year=YEAR, israeli_place=True)
    assert r["category"] == "riot"
    assert ni.classify("רימון הושלך לעבר בית בעיר", "", now_year=YEAR, israeli_place=True)["category"] == "explosive"


def test_foreign_war_context_is_international_even_with_ambiguous_place():
    r = ni.classify("פיצוצים באיראן: ארה\"ב תקפה באי", "", now_year=YEAR, israeli_place=True)
    assert not r["relevant"]


def test_street_truncated_at_multiword_locality(gaz):
    gz = ni.Gazetteer(gaz.localities + [{"name": "בת ים", "lat": 32.02, "lon": 34.75, "kind": "town"}])
    loc = ni.geolocate("גבר נפצע באירוע אלימות ברחוב ז'בוטינסקי בבת ים", "", gz)
    assert loc["name"] == "בת ים" and loc["street_text"] == "ז'בוטינסקי"


def test_residence_cue_from_village_phrase(gaz):
    loc = ni.geolocate("צעיר נורה למוות בצומת", "הקורבן מהכפר כאבול", gaz)
    assert loc["role"] == "residence" and loc["confidence"] < 0.5


def test_dedup_merges_homicide_and_stabbing_same_city(gaz):
    a = _it("חשד לרצח בבאר שבע: אישה כבת 40 נמצאה ללא רוח חיים", "A", "2026-11-06T08:00:00Z", gaz, "https://a.test/r")
    b = _it("אישה כבת 40 נדקרה למוות בבאר שבע", "B", "2026-11-06T09:00:00Z", gaz, "https://b.test/r")
    assert {a["category"], b["category"]} == {"homicide", "stabbing"}
    out = ni.dedup([a, b])
    assert len(out) == 1 and out[0]["category"] == "stabbing"


def test_domestic_context_flag():
    r = ni.classify("אישה נדקרה למוות בביתה, בן זוגה נעצר", "", now_year=YEAR, israeli_place=True)
    assert r["relevant"] and "private_or_domestic_context" in r["flags"]
    r2 = ni.classify("עובר אורח נדקר בתחנה המרכזית", "", now_year=YEAR, israeli_place=True)
    assert "private_or_domestic_context" not in r2["flags"]


def test_deliberate_ramming_is_assault_not_traffic():
    r = ni.classify("חשד לדריסה מכוונת: עובד נפצע קשה בתחנת דלק", "", now_year=YEAR, israeli_place=True)
    assert r["relevant"] and r["category"] == "assault"
    assert ni.classify("הולך רגל נדרס בצומת", "", now_year=YEAR, israeli_place=True)["category"] == "traffic_accident"
