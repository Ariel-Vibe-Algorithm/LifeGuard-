# -*- coding: utf-8 -*-
"""
news_intel.py - שכבת "מודיעין חדשות" (מחקר בלבד, לא חלק מהאפליקציה).

מאתר דיווחים תקשורתיים פומביים על אירועים שרלוונטיים לבטיחות אישית בישראל (אלימות/תקיפה,
דקירה, ירי, שוד, פגיעה/הטרדה מינית, ניסיון חטיפה, מהומות), ממקם אותם ברמת יישוב/שכונה/רחוב
כשהטקסט מאפשר, מאחד כפילויות בין כלי תקשורת, ומוציא JSON עם רמת ודאות לכל פריט.

הפלט מיועד להיות אות **חלש ומסומן**: "דווח בתקשורת, לא מאומת". לעולם לא עובדה.

עקרונות:
- נשמרים רק כותרת, תקציר קצר כפי שהופיע בפיד (עד SNIPPET_MAX תווים), URL, מפרסם וזמן פרסום.
  אין הורדה של דפי כתבות ואין שמירת טקסט מלא (זכויות יוצרים).
- ספקים: RSS של מפרסמים (רשימה ניתנת להגדרה), GDELT DOC 2.0 (בלי מפתח), Google Programmable
  Search רק אם הוגדרו LIFEGUARD_GOOGLE_CSE_KEY + LIFEGUARD_GOOGLE_CSE_ID. **אין גירוד של google.com.**
  תוצאות Google לא נשמרות לדיסק (סעיף 1.4(l) בתנאי Programmable Search).
- רשת: User-Agent כללי, השהיה לכל host, בדיקת robots.txt לפידים, cache מקומי ב-gzip, ומצב offline.
- מסווג שקוף: כללי מילים/ביטויים בעברית + מסנני אי-רלוונטיות (תרגיל, סרט, גזר דין, חו"ל,
  תאונת דרכים, מלחמה, הכחשה, חדשות ישנות, סטטיסטיקה) + ודאות.
- מיקום: מילון יישובים (רשות האוכלוסין, data.gov.il) + קואורדינטות ושכונות מ-OSM (ODbL).
  רמת רחוב רק אם הרחוב גואוקד בפועל (Nominatim, אופציונלי ומוגבל). רמת עיר אף פעם לא מוצגת כרחוב.

שימוש:
  python -m research_tools.news_intel gazetteer [--offline]
  python -m research_tools.news_intel run [--offline] [--providers rss,gdelt,google] [--days 14] [--nominatim]
  python -m research_tools.news_intel label-template [--out FILE]
  python -m research_tools.news_intel eval --labels FILE
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- נתיבים וקבועים

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "research", "samples", "news_intel")   # ב-gitignore (research/samples/)
CACHE_DIR = os.path.join(OUT_DIR, "cache")
STORE_PATH = os.path.join(OUT_DIR, "store.json.gz")
GAZETTEER_PATH = os.path.join(OUT_DIR, "gazetteer.json.gz")

USER_AGENT = "LifeGuard-Research/1.0"
REQUEST_TIMEOUT_S = 60
REQUEST_RETRIES = 3
DEFAULT_MIN_INTERVAL_S = 2.0
HOST_MIN_INTERVAL_S = {
    "api.gdeltproject.org": 6.0,            # GDELT: "Please limit requests to one every 5 seconds"
    "nominatim.openstreetmap.org": 15.0,    # מדיניות OSMF: מקס' 1/שנ'; סקריפט שרץ באופן קבוע: 4 לדקה
    "overpass-api.de": 10.0,
    "www.googleapis.com": 1.0,
}
FEED_TTL_S = 15 * 60          # פיד לא נשלף שוב לפני 15 דק'
STATIC_TTL_S = 30 * 86400     # robots / מילון יישובים / תוצאות Nominatim
SNIPPET_MAX = 300             # תקציר כפי שהופיע בפיד, חתוך. אין טקסט מלא
STORE_RETENTION_DAYS = 90
LABEL_HE = "דווח בתקשורת, לא מאומת"

# פידים ציבוריים שנבדקו (2026-11-07): קיימים, RSS תקין, robots.txt לא חוסם.
# שימוש אישי/מחקרי בלבד; תנאי השימוש של רוב המפרסמים אוסרים יצירת "מאגר" באמצעים אוטומטיים -
# ראו research/news_intel_report.md לפני כל שימוש מעבר למחקר.
DEFAULT_FEEDS: List[dict] = [
    {"url": "https://rcs.mako.co.il/rss/news-law.xml", "publisher": "mako / N12", "section": "פלילים ומשפט"},
    {"url": "https://rcs.mako.co.il/rss/news-israel.xml", "publisher": "mako / N12", "section": "בארץ"},
    {"url": "https://www.ynet.co.il/Integration/StoryRss1854.xml", "publisher": "ynet", "section": "מבזקים"},
    {"url": "https://www.ynet.co.il/Integration/StoryRss2.xml", "publisher": "ynet", "section": "חדשות"},
    {"url": "https://rss.walla.co.il/feed/1", "publisher": "וואלה", "section": "חדשות בארץ"},
    {"url": "https://rss.walla.co.il/feed/22", "publisher": "וואלה", "section": "מבזקים"},
    {"url": "https://www.maariv.co.il/Rss/RssFeedsMivzakiChadashot", "publisher": "מעריב", "section": "מבזקים"},
    {"url": "https://www.haaretz.co.il/srv/htz---all-articles", "publisher": "הארץ", "section": "כל הכתבות"},
    {"url": "https://www.israelhayom.co.il/rss.xml", "publisher": "ישראל היום", "section": "כללי"},
    {"url": "https://www.inn.co.il/Rss.aspx", "publisher": "ערוץ 7", "section": "מבזקים"},
    {"url": "https://www.davar1.co.il/feed/", "publisher": "דבר", "section": "כללי"},
    {"url": "https://www.haipo.co.il/feed/", "publisher": "חי פה", "section": "חיפה (מקומי)"},
]


def log_default(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", "replace").decode("ascii", "replace"))


# ================================================================= רשת + cache

class OfflineMiss(Exception):
    """מצב offline ואין עותק ב-cache."""


class RateLimited(Exception):
    pass


class Http:
    """GET מנומס: השהיה לכל host, ניסיונות חוזרים, gzip, cache מקומי (gzip) עם TTL ומצב offline.

    opener/sleep/clock ניתנים להחלפה לבדיקות (בבדיקות אין רשת בכלל)."""

    def __init__(self, cache_dir: str = CACHE_DIR, offline: bool = False,
                 opener: Optional[Callable] = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, log: Callable[[str], None] = log_default,
                 rate_limit_backoff_s: float = 30.0):
        self.cache_dir = cache_dir
        self.offline = offline
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self.clock = clock
        self.log = log
        self.backoff = rate_limit_backoff_s
        self._last: Dict[str, float] = {}
        self._robots: Dict[str, urllib.robotparser.RobotFileParser] = {}
        self.requests = 0

    def cache_path(self, source: str, url: str) -> str:
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
        return os.path.join(self.cache_dir, source, h + ".json.gz")

    def _read_cache(self, path: str) -> Optional[dict]:
        if not os.path.exists(path):
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _throttle(self, host: str) -> None:
        gap = HOST_MIN_INTERVAL_S.get(host, DEFAULT_MIN_INTERVAL_S)
        last = self._last.get(host)
        if last is not None:
            wait = gap - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def get(self, url: str, source: str, ttl: Optional[float] = FEED_TTL_S, cache: bool = True,
            headers: Optional[dict] = None) -> str:
        """מחזיר טקסט. ttl=None: cache לתמיד. cache=False: לא נקרא ולא נכתב לדיסק (Google)."""
        path = self.cache_path(source, url)
        cached = self._read_cache(path) if cache else None
        if cached is not None:
            fresh = ttl is None or (self.clock() - cached.get("fetched_at", 0)) < ttl
            if self.offline or fresh:
                return cached["body"]
        if self.offline:
            raise OfflineMiss(f"offline: אין cache עבור {url}")
        host = urllib.parse.urlsplit(url).hostname or ""
        last_err: Optional[Exception] = None
        for attempt in range(REQUEST_RETRIES):
            self._throttle(host)
            try:
                hdr = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
                hdr.update(headers or {})
                req = urllib.request.Request(url, headers=hdr)
                self.requests += 1
                with self.opener(req, timeout=REQUEST_TIMEOUT_S) as resp:
                    raw = resp.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                body = raw.decode("utf-8-sig", "replace")
                if host == "api.gdeltproject.org" and body.startswith("Please limit requests"):
                    raise RateLimited(body[:80])
                if cache:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    tmp = path + ".tmp"
                    with gzip.open(tmp, "wt", encoding="utf-8") as f:
                        json.dump({"url": url, "fetched_at": self.clock(), "body": body}, f, ensure_ascii=False)
                    os.replace(tmp, path)
                return body
            except RateLimited as e:
                last_err = e
                self.log(f"  [{source}] rate limited, backoff {self.backoff * (attempt + 1):.0f}s")
                self.sleep(self.backoff * (attempt + 1))
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (400, 401, 403, 404, 410):
                    break
                self.sleep(2.0 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_err = e
                self.sleep(2.0 * (attempt + 1))
        if cached is not None:      # רשת נכשלה - עותק ישן עדיף משום דבר
            self.log(f"  [{source}] נכשל ({last_err}); משתמש בעותק מה-cache")
            return cached["body"]
        raise RuntimeError(f"נכשל: {url}: {last_err}")

    def robots_allowed(self, url: str) -> Tuple[bool, str]:
        """robots.txt של ה-host. robots שלא נגיש (שגיאה/HTML) -> מותר אבל מדווח."""
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(base)
        note = "ok"
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                txt = self.get(base + "/robots.txt", "robots", ttl=STATIC_TTL_S)
                if "<html" in txt[:500].lower():
                    txt, note = "", "robots_unavailable_html"
            except OfflineMiss:
                txt, note = "", "robots_not_cached"
            except RuntimeError:
                txt, note = "", "robots_fetch_failed"
            rp.parse(txt.splitlines())
            self._robots[base] = rp
        return rp.can_fetch(USER_AGENT, url), note


# ================================================================= טקסט עברי

_GERSHAYIM = str.maketrans({"״": '"', "”": '"', "“": '"', "׳": "'", "’": "'", "‘": "'", "`": "'",
                            "־": "-", "–": "-", "—": "-", "‏": "", "‎": "", "\xa0": " "})
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
HEB = "א-ת"


def clean_text(s: Optional[str]) -> str:
    """HTML -> טקסט, איחוד גרשיים/מקפים, רווחים."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = _TAG_RE.sub(" ", s)
    s = s.translate(_GERSHAYIM)
    return _WS_RE.sub(" ", s).strip()


def snippet_of(s: Optional[str], limit: int = SNIPPET_MAX) -> str:
    t = clean_text(s)
    if len(t) <= limit:
        return t
    cut = t[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit * 0.6 else cut) + "…"


def norm_name(s: str) -> str:
    """נרמול שם יישוב להשוואה: בלי גרשיים/מקפים/רווחים כפולים, קריית=קרית, תקווה=תקוה."""
    s = clean_text(s).replace('"', "").replace("'", "'")
    s = re.sub(r"\s*-\s*", " ", s)
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.replace("קריית", "קרית").replace("תקווה", "תקוה")
    return _WS_RE.sub(" ", s).strip()


def parse_date(s: Optional[str]) -> Optional[str]:
    """תאריך RSS/Atom/GDELT -> ISO UTC."""
    if not s:
        return None
    s = s.strip()
    if s.startswith("<![CDATA["):
        s = s[9:-3].strip()
    dt = None
    if not re.search(r" I[DS]T ", s):          # mako: "Sun Aug 04 07:35:00 IDT 2024" - email parser מאבד את ה-IDT
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            dt = None
    if dt is None:
        # (פורמט, הפרש מ-UTC בשעות). Z = UTC; IDT/IST = שעון ישראל קיץ/חורף
        for fmt, off in (("%Y%m%dT%H%M%SZ", 0), ("%Y-%m-%dT%H:%M:%SZ", 0), ("%Y-%m-%dT%H:%M:%S%z", None),
                         ("%a %b %d %H:%M:%S IDT %Y", 3), ("%a %b %d %H:%M:%S IST %Y", 2), ("%Y/%m/%d %H:%M:%S", None)):
            try:
                dt = datetime.strptime(s, fmt)
            except ValueError:
                continue
            if off is not None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=off)))
            break
    if dt is None:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:   # בלי אזור זמן (למשל "2026/11/07 22:53:13"): מניחים שעון ישראל +02:00 (קיץ/חורף לא ידוע)
        dt = dt.replace(tzinfo=timezone(timedelta(hours=2)))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_to_ts(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def item_id(url: str) -> str:
    u = urllib.parse.urlsplit(url.strip())
    canon = urllib.parse.urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), u.query, ""))
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:16]


# ================================================================= ספקים

def parse_feed(xml_text: str) -> List[dict]:
    """RSS 2.0 / Atom -> [{title, description, link, published}]. סובלני לפידים פגומים (fallback ב-regex)."""
    txt = xml_text.lstrip("﻿ \r\n\t")
    out: List[dict] = []
    try:
        root = ET.fromstring(txt.encode("utf-8"))
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag not in ("item", "entry"):
                continue
            rec = {"title": "", "description": "", "link": "", "published": ""}
            for c in el:
                ct = c.tag.rsplit("}", 1)[-1]
                if ct == "title":
                    rec["title"] = c.text or ""
                elif ct in ("description", "summary") and not rec["description"]:
                    rec["description"] = c.text or ""
                elif ct == "link":
                    rec["link"] = (c.text or c.attrib.get("href") or "").strip()
                elif ct in ("pubDate", "published", "updated", "date") and not rec["published"]:
                    rec["published"] = c.text or ""
            out.append(rec)
        return out
    except ET.ParseError:
        pass
    for m in re.finditer(r"<item\b.*?</item>", txt, re.S):
        block = m.group(0)

        def grab(tag):
            g = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", block, re.S)
            v = g.group(1) if g else ""
            v = re.sub(r"^\s*<!\[CDATA\[(.*)\]\]>\s*$", r"\1", v, flags=re.S)
            return v.strip()
        out.append({"title": grab("title"), "description": grab("description"), "link": grab("link"),
                    "published": grab("pubDate")})
    return out


class RssProvider:
    name = "rss"

    def __init__(self, feeds: Optional[Sequence[dict]] = None):
        self.feeds = list(feeds or DEFAULT_FEEDS)

    def fetch(self, http: Http, log=log_default) -> Tuple[List[dict], dict]:
        items, status = [], {"provider": self.name, "feeds": []}
        for f in self.feeds:
            st = {"url": f["url"], "publisher": f["publisher"]}
            allowed, note = http.robots_allowed(f["url"])
            st["robots"] = note if allowed else "disallowed"
            if not allowed:
                status["feeds"].append(st)
                log(f"  [rss] robots.txt אוסר: {f['url']} - דילוג")
                continue
            try:
                body = http.get(f["url"], "rss", ttl=FEED_TTL_S)
            except (OfflineMiss, RuntimeError) as e:
                st["error"] = str(e)[:200]
                status["feeds"].append(st)
                continue
            recs = parse_feed(body)
            st["items"] = len(recs)
            status["feeds"].append(st)
            for r in recs:
                title, link = clean_text(r["title"]), clean_text(r["link"])
                if not title or not link.startswith("http"):
                    continue
                desc = snippet_of(r["description"])
                if desc == title:
                    desc = ""
                items.append({"provider": self.name, "publisher": f["publisher"], "feed": f["url"],
                              "title": title, "snippet": desc, "url": link,
                              "published_at": parse_date(r["published"]), "fetched_at": _now_iso(http.clock())})
        return items, status


class GdeltProvider:
    """GDELT DOC 2.0 (artlist/JSON). נבדק 2026-11-07: מילות מפתח בעברית לא מחזירות תוצאות ('נדקר' על
    חודש שלם = 0), אבל מילות מפתח **באנגלית** מתאימות גם למאמרים בעברית (GDELT מתרגם מכונה ומאנדקס את
    התרגום): 'police sourcelang:hebrew' החזיר כותרות עבריות. לכן השאילתה באנגלית, והסיווג נעשה מקומית
    על הכותרת העברית. GDELT מחזיר כותרת בלבד (בלי תקציר), עם גרשיים שהוסרו ורווחים סביב פיסוק."""

    name = "gdelt"
    API = "https://api.gdeltproject.org/api/v2/doc/doc"

    DEFAULT_QUERY = ("(stabbing OR stabbed OR shooting OR shot OR robbery OR robbed OR assault OR attacked OR "
                     "beaten OR rape OR harassment OR kidnapping OR abduction OR riot OR murdered) sourcelang:hebrew")

    def __init__(self, queries: Optional[Sequence[str]] = None, window_hours: int = 24, max_queries: int = 4,
                 maxrecords: int = 250):
        self.queries = list(queries or [self.DEFAULT_QUERY])
        self.window_hours = window_hours
        self.max_queries = max_queries
        self.maxrecords = maxrecords

    def fetch(self, http: Http, log=log_default, end_ts: Optional[float] = None) -> Tuple[List[dict], dict]:
        end = end_ts or http.clock()
        end -= end % 3600          # חלונות מעוגלים לשעה -> cache יציב
        items, status = [], {"provider": self.name, "queries": []}
        n = 0
        for q in self.queries:
            for w in range(self.max_queries):
                if n >= self.max_queries:
                    break
                t1 = end - w * self.window_hours * 3600
                t0 = t1 - self.window_hours * 3600
                fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%d%H%M%S")
                params = {"query": q, "mode": "artlist", "format": "json", "maxrecords": self.maxrecords,
                          "startdatetime": fmt(t0), "enddatetime": fmt(t1), "sort": "datedesc"}
                url = self.API + "?" + urllib.parse.urlencode(params)
                n += 1
                st = {"query": q, "start": fmt(t0), "end": fmt(t1)}
                try:
                    body = http.get(url, "gdelt", ttl=None if t1 < http.clock() - 3600 else FEED_TTL_S)
                    data = json.loads(body) if body.strip().startswith("{") else {}
                except (OfflineMiss, RuntimeError, ValueError) as e:
                    st["error"] = str(e)[:200]
                    status["queries"].append(st)
                    continue
                arts = data.get("articles", []) or []
                st["articles"] = len(arts)
                status["queries"].append(st)
                for a in arts:
                    title = clean_text(a.get("title"))
                    if not title or not a.get("url"):
                        continue
                    items.append({"provider": self.name, "publisher": a.get("domain") or "",
                                  "feed": "gdelt:" + q, "title": title, "snippet": "", "url": a["url"],
                                  "published_at": parse_date(a.get("seendate")),
                                  "fetched_at": _now_iso(http.clock())})
        status["note"] = "כותרות בלבד; שאילתה באנגלית על תרגום המכונה של GDELT (מילות מפתח בעברית לא עבדו)"
        return items, status


class GoogleCseProvider:
    """Google Programmable Search (Custom Search JSON API) - רק עם מפתחות מהסביבה.

    - סגור ללקוחות חדשים; ללקוחות קיימים נסגר ב-2027-01-01 (developers.google.com/custom-search/v1/overview).
    - תנאי Programmable Search 1.4(l): אסור לאחסן תוצאות באופן לא-זמני -> cache=False, והפריטים
      מסומנים transient ולא נשמרים ב-store/בפלט (רק ספירה בסיכום).
    - לעולם לא נשלפות תוצאות מ-google.com/search (robots.txt: Disallow: /search; תנאי Google)."""

    name = "google"
    API = "https://www.googleapis.com/customsearch/v1"
    DEFAULT_QUERIES = ("נדקר", "נורה ונפצע", "שוד", "תקיפה מינית", "ניסיון חטיפה", "הותקף")

    def __init__(self, queries: Optional[Sequence[str]] = None, env=None):
        env = os.environ if env is None else env
        self.key = env.get("LIFEGUARD_GOOGLE_CSE_KEY") or ""
        self.cx = env.get("LIFEGUARD_GOOGLE_CSE_ID") or ""
        self.queries = list(queries or self.DEFAULT_QUERIES)

    @property
    def configured(self) -> bool:
        return bool(self.key and self.cx)

    def fetch(self, http: Http, log=log_default) -> Tuple[List[dict], dict]:
        if not self.configured:
            return [], {"provider": self.name, "status": "not configured",
                        "note": "חסרים LIFEGUARD_GOOGLE_CSE_KEY / LIFEGUARD_GOOGLE_CSE_ID. אין גירוד של google.com"}
        if http.offline:
            return [], {"provider": self.name, "status": "skipped_offline",
                        "note": "תוצאות Google לא נשמרות ב-cache (תנאי שימוש), ולכן אין מצב offline"}
        items, status = [], {"provider": self.name, "status": "configured", "queries": []}
        for q in self.queries:
            params = {"key": self.key, "cx": self.cx, "q": q, "lr": "lang_iw", "gl": "il",
                      "dateRestrict": "d7", "num": 10}
            try:
                body = http.get(self.API + "?" + urllib.parse.urlencode(params), "google", cache=False)
                data = json.loads(body)
            except (RuntimeError, ValueError) as e:
                status["queries"].append({"q": q, "error": str(e).replace(self.key, "***")[:200]})
                continue
            res = data.get("items", []) or []
            status["queries"].append({"q": q, "results": len(res)})
            for r in res:
                items.append({"provider": self.name, "publisher": r.get("displayLink") or "", "feed": "google:" + q,
                              "title": clean_text(r.get("title")), "snippet": snippet_of(r.get("snippet")),
                              "url": r.get("link") or "", "published_at": None,
                              "fetched_at": _now_iso(http.clock()), "transient": True})
        return items, status


def _now_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ================================================================= מסווג

def _rx(pattern: str) -> "re.Pattern":
    """גבול מילה עברי + קידומות. שם עצם: ו/ב/ל/מ/ש/ה/כ (עד 3). פועל (תבנית שמתחילה ב-"V:"): ו/ש בלבד."""
    if pattern.startswith("V:"):
        return re.compile(rf"(?<![{HEB}\w])(?:ו?ש?)(?:{pattern[2:]})(?![{HEB}\w])")
    return re.compile(rf"(?<![{HEB}\w])(?:[ובלמשהכ]{{0,3}})(?:{pattern})(?![{HEB}\w])")


# (קטגוריה, תבנית, משקל). משקל: 0.85 חזק (אירוע מפורש), 0.6 בינוני, 0.35 חלש (מילה כללית).
# תבנית שמתחילה ב-"V:" היא פועל: מקבלת רק קידומות ו/ש ("ונורה", "שנורה") - "מנורה" אינו מ+"נורה".
INCIDENT_RULES: List[Tuple[str, str, float]] = [
    ("stabbing", r"V:נדקר|נדקרה|נדקרו|דקר|דקרה|דקרו", 0.85),
    ("stabbing", r"דקירה|דקירות|פיגוע דקירה|נפצע מדקירה|נפצעה מדקירה", 0.85),
    ("stabbing", r"סכין|סכינים", 0.35),
    ("shooting", r"V:נורה|נורתה|נורו|ירו לעבר|ירו ב[{0}]+|פתח באש|פתחו באש|חוסל".format(HEB), 0.85),
    ("shooting", r"נפצע מירי|נפצעה מירי|נפצעו מירי|נהרג מירי|נהרגה מירי|נרצח בירי|נרצחה בירי|"
                 r"אירוע ירי|ירי לעבר|חיסול", 0.85),
    ("shooting", r"V:ירה|ירו", 0.4),
    ("shooting", r"ירי|יריות|נשק|אקדח", 0.4),
    ("explosive", r"רימון(?! הלם| עשן)|רימון רסס|מטען נפץ|מטען חבלה|פיצוץ רכב|רכב התפוצץ|הושלך רימון|הטמנת מטען", 0.75),
    ("homicide", r"V:נרצח|נרצחה|נרצחו|רצח|רצחו|רצחה", 0.8),
    ("homicide", r"רצח|ניסיון רצח|ניסיון לרצח|הנרצח|הנרצחת", 0.8),
    ("homicide", r"גופה|גופת|נמצא ללא רוח חיים|נמצאה ללא רוח חיים", 0.35),
    ("assault", r"V:הותקף|הותקפה|הותקפו|תקף|תקפה|תקפו|הוכה|הוכתה|הוכו|היכו|היכה|הכו", 0.8),
    ("assault", r"תקיפה|תקיפת|קטטה|קטטת|תגרה|תגרת|לינץ'|פיגוע דריסה|אלימות קשה|אירוע אלימות|"
                r"פציעה חודרת|פצע דקירה|דריסה מכוונת|דרס בכוונה|נדרס בכוונה|דריסה בכוונה", 0.8),
    ("assault", r"אלימות|אלים|אלימה|נפצע|נפצעה|פצוע|פצועה|חבלה|איומים|איים", 0.3),
    ("robbery", r"V:שדד|שדדו|שדדה|נשדד|נשדדה|נשדדו", 0.85),
    ("robbery", r"שוד|שודד|שודדים|שוד מזוין|חטף את התיק|חטיפת תיק|חטפו את התיק", 0.85),
    ("sexual", r"אונס|אנס|אנסו|נאנס|נאנסה|תקיפה מינית|תקף מינית|תקפה מינית|הטרדה מינית|הטריד מינית|"
               r"מעשה מגונה|מעשים מגונים|מעשה סדום|פגיעה מינית|פגע מינית|עבירות מין|עבירת מין|"
               r"הוטרדה מינית|הוטרד מינית|חשיפה מגונה|מטריד סדרתי", 0.85),
    ("sexual", r"הטרדה|הטריד|הטרידה|הטרידו|מטריד", 0.35),
    ("kidnapping", r"ניסיון חטיפה|ניסה לחטוף|ניסו לחטוף|V:נחטף|נחטפה|נחטפו|"
                   r"הוכנס בכוח לרכב|הוכנסה בכוח לרכב|נגרר לרכב|נגררה לרכב", 0.75),
    ("kidnapping", r"חטיפה|חטיפת|V:חטפו|חטף|חוטף|חוטפים", 0.45),
    ("riot", r"מהומות|מהומה|התפרעות|התפרעויות|התפרעו|מתפרעים|הפרות סדר|הפרת סדר|יידוי אבנים|יידו אבנים|"
             r"השליכו אבנים|בקבוקי תבערה|בקבוק תבערה|הצתת רכבים|עימותים עם שוטרים|עימותים אלימים|"
             r"הפגנה אלימה|קטטה המונית|לינץ'", 0.8),
    ("riot", r"עימותים|עימות", 0.3),
]

# מסננים. (שם, תבנית, סוג) - סוג "hard": פוסל גם אירוע חזק. "soft": פוסל רק כשאין התאמה חזקה.
EXCLUSION_RULES: List[Tuple[str, str, str]] = [
    ("drill_or_fiction", r"תרגיל|תרגול|סימולציה|סרט|הסרט|סדרה|הסדרה|סדרת|פרק|הפרק|עונה|העונה|הצגה|ההצגה|"
                         r"רומן|הספר|ספר|מותחן|טריילר|פודקאסט|ביקורת|ביקורת טלוויזיה|נטפליקס|קולנוע|"
                         r"משחק מחשב|דרמה|קומדיה|שחקן|שחקנית|במאי|במאית|שיר|אלבום", "hard"),
    ("legal_proceeding", r"גזר הדין|גזר דין|נגזר|נגזרה|נגזרו|נגזרו עליו|שנות מאסר|שנות מאסר בפועל|חודשי מאסר|"
                         r"הורשע|הורשעה|הורשעו|הרשעה|הרשעת|כתב אישום|כתבי אישום|כתב האישום|הסדר טיעון|"
                         r"זוכה|זוכתה|זיכוי|ערעור|ערעורו|העליון|בית המשפט העליון|נידון|נידונה|"
                         r"הכרעת דין|נאשם|נאשמת|נאשמים|הנאשם|מואשם|מואשמת|הואשם|הואשמה|הואשמו|יואשם|יואשמו|"
                         r"האישום|לפי האישום|אישום|אישומים|האישומים|מח\"ש|סגרה תיק|נסגר התיק|סגירת התיק|"
                         r"איים ברצח|איים לרצוח|איומים ברצח|מאיים לרצוח|איים להרוג", "hard"),
    ("legal_context", r"עונש|העונש|פרקליטות|הפרקליטות|עדות|העיד|העידה|בית המשפט|בית משפט|השופט|השופטת", "soft"),
    ("followup_arrest", r"נעצר|נעצרה|נעצרו|הארכת מעצר|הוארך מעצרו|הוארך מעצרה|הוארך מעצרם|חשוד|חשודה|חשודים|"
                        r"שוחרר|שוחררה|שוחררו|מעצר בית|נחקר|נחקרה|חקירה|החקירה", "penalty"),
    ("traffic_accident", r"תאונת דרכים|תאונה|התאונה|תאונת|התנגשות|התנגש|התנגשה|התהפך|התהפכה|נדרס|נדרסה|"
                         r"נדרסו|דריסה|פגע וברח|פגעה וברחה|נפגע מרכב|נפגעה מרכב|רוכב אופנוע|קורקינט|"
                         r"אופניים חשמליים|משאית|הולך רגל|הולכת רגל", "soft"),
    ("war_security", r"רקטה|רקטות|טיל|טילים|יירוט|יירוטים|כטב\"ם|כטב\"מים|צבע אדום|אזעקה|אזעקות|"
                     r"חיל האוויר|צה\"ל תקף|צה\"ל|חטופים|החטופים|החטוף|בשבי|השבי|חמאס|חיזבאללה|החות'ים|"
                     r"רצועת עזה|בעזה|ברצועה|לבנון|בלבנון|איראן|באיראן|תימן|סוריה|בסוריה|כוחותינו|"
                     r"לוחמים|לוחם|חייל|חיילים|מחבלים חמושים בעזה|פיקוד העורף", "soft"),
    ("negated_or_denied", r"אין חשד לפלילים|לא מדובר ב[{0}]+|לא היה פיגוע|התברר כי|התברר ש|התגלה כ[{0}]*|"
                          r"שקרי|פייק|פייק ניוז|הכחיש|הכחישה|הכחישו|הוכחש|הפריך|הפריכה|הופרך|שמועות|"
                          r"שמועה|דיווח כוזב|הודעה כוזבת|בדיוני|בדיה|לא נמצא קשר".format(HEB), "hard"),
    ("old_or_anniversary", r"לפני \d+ שנים|לפני כ-?\d+ שנים|לפני עשור|שנים לאחר|יום השנה|אזכרה|האזכרה|"
                           r"שנה לרצח|שנה לפיגוע|שנים לרצח|שנים לפיגוע|לזכרו|לזכרה|הנצחה|לפני שנה", "hard"),
    ("statistics_or_opinion", r"מתחילת השנה|נתוני|הנתונים|דו\"ח|הדו\"ח|סקר|הסקר|עלייה של|ירידה של|"
                              r"בשנה האחרונה|מדד|מחקר|המחקר|טור דעה|דעה|פרשנות|ניתוח|ראיון|מאמר|"
                              r"הצעת חוק|הממשלה|הכנסת|בכנסת|השר|השרה|ועדת|הוועדה|מנכ\"ל|המפכ\"ל|מפכ\"ל|"
                              r"תוכנית לאומית|מאבק בפשיעה|מגפת|ברחבי הארץ|סיכום חדשות|סיכום השבוע|"
                              r"סיכום השבת|סיכום היום|לפי מעקב|מעקב", "soft"),
    ("sports", r"ליגה|הליגה|ליגת|כדורגל|כדורסל|נבחרת|הנבחרת|מאמן|המאמן|אוהדים|האוהדים|שער|שערים|"
               r"אליפות|גביע|משחק|המשחק|דקה ה-?\d+|מכבי|הפועל|בית\"ר|UFC|אגרוף|איגרוף|קרב|קבוצה|הקבוצה|"
               r"קבוצתו|קבוצתה|שוער|השוער|כדורגלן|הכדורגלן|המגרש|על המגרש", "soft"),
    ("metaphor", r"סכין בגב|ירייה ברגל|ירה לעצמו ברגל|שוד לאור יום|שוד הקופה|חטיפת ניצחון|חטף ניצחון|"
                 r"ירי של|אחוזי ירי|ירי לשלוש|רצח אופי|רצח העם|נרצחו בשואה|בשואה|התקף לב|התקף חרדה|"
                 r"התקף פאניקה|התקף אסתמה|התקף זעם|התקף אפילפסיה|תוקפים את|תקף את הממשלה|תקפה את הממשלה|"
                 r"מתקפה|מתקפת|המתקפה|במתקפה|מתקפות", "hard"),
]

# ארצות/ערים בחו"ל (ללא יישוב ישראלי בטקסט -> "בינלאומי")
FOREIGN_PLACES = (r"ארה\"ב|ארצות הברית|אמריקה|ניו יורק|לוס אנג'לס|קליפורניה|טקסס|פלורידה|שיקגו|וושינגטון|"
                  r"קנדה|מקסיקו|ברזיל|ארגנטינה|קולומביה|בריטניה|אנגליה|לונדון|סקוטלנד|אירלנד|צרפת|פריז|"
                  r"גרמניה|ברלין|אוסטריה|וינה|שווייץ|איטליה|רומא|מילאנו|ספרד|מדריד|ברצלונה|פורטוגל|"
                  r"הולנד|אמסטרדם|בלגיה|בריסל|שוודיה|שטוקהולם|נורבגיה|דנמרק|פינלנד|פולין|אוקראינה|"
                  r"קייב|רוסיה|מוסקבה|טורקיה|איסטנבול|יוון|אתונה|קפריסין|מצרים|קהיר|ירדן|עמאן|"
                  r"דובאי|איחוד האמירויות|סעודיה|הודו|סין|יפן|טוקיו|תאילנד|בנגקוק|אוסטרליה|סידני|"
                  r"דרום אפריקה|ניגריה|קניה|אתיופיה|ונצואלה|פרו|צ'ילה|הונגריה|רומניה|בולגריה|צ'כיה|פראג|"
                  r"גיאורגיה|אזרבייג'ן|קזחסטן|פקיסטן|אפגניסטן|עיראק|האו\"ם|באירופה|אירופה|בחו\"ל|בעולם|"
                  r"קרקוב|ורשה|מונטריאול|טורונטו|מנצ'סטר|מלבורן|ליון|מרסיי|קופנהגן|ציריך|ז'נבה|בודפשט|"
                  r"פרנקפורט|מינכן|המבורג|איראן|לבנון|סוריה|תימן|בואנוס איירס|סאו פאולו|ויליאמסבורג|ברוקלין|מנהטן")

# הקשר פרטי/משפחתי/סכסוך: לא משנה ודאות, רק דגל. אירוע כזה כמעט לא מעיד על סיכון לעובר אורח זר -
# השרת צריך להסתיר אותו מהמפה (ראו research/news_intel_report.md, סעיף 6).
DOMESTIC_RX = re.compile(r"(?<![א-ת])(?:ו?ש?(?:ב|ל)?)(?:בן זוגה|בן זוגו|בת זוגו|בת זוגה|גרושתו|גרושה|אשתו|בעלה|"
                         r"בתוך הבית|בביתה|בביתו|על רקע משפחתי|סכסוך|הסכסוך|סכסוך דמים|אחיו|אחיה|אביו|אביה|"
                         r"אמו|אמה|בנו|בתו)(?![א-ת])")

_INCIDENT = [(c, _rx(p), w) for c, p, w in INCIDENT_RULES]
_EXCLUDE = [(n, _rx(p), k) for n, p, k in EXCLUSION_RULES]
_FOREIGN = _rx(FOREIGN_PLACES)
_YEAR = re.compile(r"(?<!\d)(?:ב-?|בשנת |מ-?)?(19[5-9]\d|20[0-4]\d)(?!\d)")

PRECEDENCE = ("stabbing", "shooting", "explosive", "sexual", "kidnapping", "robbery", "homicide", "riot", "assault")
CATEGORY_HE = {"stabbing": "דקירה", "shooting": "ירי", "homicide": "רצח/מוות באלימות", "assault": "אלימות/תקיפה",
               "robbery": "שוד", "sexual": "פגיעה/הטרדה מינית", "kidnapping": "חטיפה/ניסיון חטיפה",
               "riot": "מהומות/הפרות סדר", "explosive": "רימון/מטען/פיצוץ"}
STRONG = 0.6
RELEVANT_THRESHOLD = 0.45


def classify(title: str, snippet: str = "", now_year: Optional[int] = None,
             israeli_place: Optional[bool] = None) -> dict:
    """סיווג שקוף. מחזיר category (קטגוריית אירוע, או שם המסנן כשנפסל), relevant, confidence, matched,
    excluded_by. israeli_place: האם נמצא יישוב ישראלי בטקסט (מהגאוקודר) - לזיהוי "בינלאומי"."""
    title, snippet = clean_text(title), clean_text(snippet)
    text = f"{title} . {snippet}"
    matched: List[dict] = []
    scores: Dict[str, float] = defaultdict(float)
    hits: Dict[str, set] = defaultdict(set)      # ביטויים שונים (אותה מילה משני כללים נספרת פעם אחת)
    for cat, rx, w in _INCIDENT:
        for where, t in (("title", title), ("snippet", snippet)):
            m = rx.search(t)
            if m:
                ww = w if where == "title" else w * 0.85
                matched.append({"category": cat, "text": m.group(0).strip(), "where": where, "weight": round(ww, 2)})
                scores[cat] = max(scores[cat], ww)
                hits[cat].add((where, m.start()))
    excl: List[dict] = []
    for name, rx, kind in _EXCLUDE:
        if name == "legal_proceeding":
            # הליך משפטי בכותרת = הכתבה עוסקת במשפט; בתקציר זה לרוב רקע ("ריצה עונש על...") -> רך
            m = rx.search(title)
            if not m:
                m, kind = rx.search(snippet), "soft"
        else:
            m = rx.search(text)
        if m:
            excl.append({"rule": name, "text": m.group(0).strip(), "kind": kind})
    flags: List[str] = []
    if now_year:
        for where, t, kind in (("title", title, "hard"), ("snippet", snippet, "soft")):
            old = [int(y) for y in _YEAR.findall(t) if int(y) < now_year]
            if old:
                excl.append({"rule": "old_or_anniversary", "text": str(min(old)), "kind": kind})
    fm = _FOREIGN.search(text)
    if fm and (not israeli_place or any(e["rule"] == "war_security" for e in excl)):
        excl.append({"rule": "international", "text": fm.group(0).strip(), "kind": "hard"})
    # "פיגוע דריסה" הוא תקיפה, לא תאונה
    if any(m["text"].endswith(("פיגוע דריסה", "דריסה מכוונת", "דרס בכוונה", "נדרס בכוונה", "דריסה בכוונה"))
           for m in matched):
        excl = [e for e in excl if e["rule"] != "traffic_accident"]

    if not scores:
        cat = excl[0]["rule"] if excl else "none"
        return {"category": cat, "relevant": False, "confidence": 0.0, "matched": matched, "excluded_by": excl,
                "flags": flags}
    best = max(scores.values())
    cands = [c for c in PRECEDENCE if scores.get(c, 0) >= best - 0.15 and scores.get(c, 0) >= 0.3]
    cat = cands[0] if cands else max(scores, key=scores.get)
    # רצח עם ירי/דקירה מפורשים -> קטגוריית הנשק; "ירי" חלש + רצח חזק -> ירי
    if cat == "homicide" and scores.get("shooting", 0) >= 0.4:
        cat = "shooting"
    elif cat == "homicide" and scores.get("stabbing", 0) >= 0.35:
        cat = "stabbing"
    conf = scores[cat]
    if len(hits[cat]) >= 2 or len([c for c in scores if scores[c] >= STRONG]) >= 2:
        conf += 0.07
    strong = scores[cat] >= STRONG
    if not any(m["where"] == "title" and m["category"] == cat for m in matched):
        flags.append("only_in_snippet")
    hard = [e for e in excl if e["kind"] == "hard"]
    soft = [e for e in excl if e["kind"] == "soft"]
    penalty = [e for e in excl if e["kind"] == "penalty"]
    if hard:
        return {"category": hard[0]["rule"], "relevant": False, "confidence": round(min(conf, 0.95), 2),
                "incident_category": cat, "matched": matched, "excluded_by": excl, "flags": flags}
    if soft and (not strong or "only_in_snippet" in flags):
        return {"category": soft[0]["rule"], "relevant": False, "confidence": round(min(conf, 0.95), 2),
                "incident_category": cat, "matched": matched, "excluded_by": excl, "flags": flags}
    for e in soft:
        if e["rule"] in ("war_security", "sports", "traffic_accident"):
            conf -= 0.2
            flags.append("context_" + e["rule"])
        elif e["rule"] == "statistics_or_opinion":
            conf -= 0.25
            flags.append("context_statistics_or_policy")
    if penalty:
        conf -= 0.1
        flags.append("followup_arrest_or_investigation")
    if DOMESTIC_RX.search(text):
        flags.append("private_or_domestic_context")
    if title.startswith('"') and re.search(r"(?<![א-ת])(?:אותי|הייתי|אני|לי|שלי)(?![א-ת])", title):
        conf -= 0.45                         # עדות בגוף ראשון - לרוב ראיון/כתבת עומק, לא אירוע טרי
        flags.append("first_person_testimony")
    if "?" in title:
        conf -= 0.4                          # כותרת-שאלה - לרוב כתבת עומק/פרשנות
        flags.append("question_headline")
    conf = round(max(0.0, min(conf, 0.95)), 2)
    return {"category": cat, "relevant": conf >= RELEVANT_THRESHOLD, "confidence": conf, "matched": matched,
            "excluded_by": excl, "flags": flags}


# ================================================================= מיקום (gazetteer)

# יישובים ששמם הוא גם מילה/שם פרטי נפוץ: דורשים מילת הקשר מפורשת (ביישוב/בקיבוץ/בעיר/תושב...).
AMBIGUOUS_NAMES = {
    "רחובות", "יבנה", "עומר", "אורה", "גבעה", "שדה", "נווה", "מגן", "ברק", "ניר", "אור", "עין", "כרמל",
    "שלומי", "אלון", "תקומה", "מבטחים", "חולית", "גאולה", "רמה", "אמונים", "עדי", "הודיה", "ליבנים", "נחשון",
    "שחר", "אמיר", "עוז", "עידן", "צופר", "פארן", "כליל", "שורש", "מנוחה", "שילה", "תלמים", "מטע", "נועם",
    "אביטל", "יעד", "ברכה", "תפרח", "גילת", "מלכיה", "אושה", "אמנון", "אליכין", "עופר", "שקד", "רימונים",
    "תומר", "צור", "גשר", "דגניה", "עלמה", "חיננית", "מצפה", "כפר", "ערד", "אחוזה", "בית", "רעות", "יהל",
    "לוטם", "קטורה", "נטע", "אלמוג", "אשבול", "גבעות", "שבות", "יונתן", "אביגיל", "מעון", "כרמים", "יקיר",
    "נריה", "אבנת", "חלמיש", "מגדל", "זיו", "שומרה", "דקל", "יתד", "שובה", "שוקדה", "תלם", "עוצם", "נוב",
    "פדויים", "ישע", "מלאה", "גיתית", "שלווה", "שגב", "הררית", "קדימה", "צורן", "אחיטוב", "ארגמן", "שבי",
    "מירב", "שקף", "כוכב", "אלומה", "אמץ", "חמד", "חגור", "אודם", "גבים", "שדמה", "רוויה", "עמקה",
    "ברכיה", "בניה", "יבול", "מסד", "ניצן", "גאליה", "שפר", "סעד", "עזר", "ערוגות", "אחיעזר", "גן",
    "אזור", "רחוב", "צופים", "חוסן", "אייל", "דור", "יקום", "קשת", "מצר", "עלי", "שני", "חבר", "ניין", "בחן",
    "נחלים", "מבוא", "מסלול", "נתיב", "שדות", "גבעולים", "זמר", "כנות", "יכיני", "מעגלים", "פדיה", "שתולים",
    "אמונה", "תושיה", "ישעי", "ניצנים", "משמר", "מגל", "אבטליון", "הדר", "נגבה", "צוקים", "אורים",
}
# כינויים נפוצים בכותרות -> שם רשמי (נורמלי)
ALIASES = {
    'ת"א': "תל אביב יפו", "תל אביב": "תל אביב יפו", "יפו": "תל אביב יפו", 'ב"ש': "באר שבע",
    'פ"ת': "פתח תקוה", 'ראשל"צ': "ראשון לציון", 'ק"ש': "קרית שמונה", "נצרת עילית": "נוף הגליל",
    "מודיעין": "מודיעין מכבים רעות", 'ק"ג': "קרית גת", 'ר"ג': "רמת גן", 'ב"ב': "בני ברק",
    "פתח תקווה": "פתח תקוה", "ראשון": None,
}
STREET_CUE = re.compile(rf"(?:רחוב|רח'|שדרות|שד'|דרך|כביש|צומת|מחלף|כיכר|שכונת|שכ')\s*$")
RESIDENCE_CUE = re.compile(rf"(?:תושב|תושבת|תושבי|יליד|ילידת)\s+$|(?:בן|בת|בני|בנות)\s+\d+\s+$|"
                           rf"(?:מהיישוב|מהישוב|מהכפר|מהעיר|מהקיבוץ|מהמושב)\s+$")
LOCALITY_CUE = re.compile(rf"(?:יישוב|ישוב|קיבוץ|מושב|כפר|עיר|עיירה|מועצה|תושב|תושבת|ליד|סמוך ל|"
                          rf"בקרבת|באזור|ביישוב|בישוב|בקיבוץ|במושב|בכפר|בעיר|בעיירה)\s+$")
NEIGHBORHOOD_CUE = re.compile(r"(?:שכונת|שכ'|רובע|בשכונת|בשכ'|ברובע)\s+$")
STREET_RX = re.compile(rf"(?<![{HEB}])(?:ב|ל|מ)?(?:רחוב|רח'|שדרות|שד')\s+((?:[{HEB}\"'\-]+|\d+)(?:\s+[{HEB}\"'\-]+){{0,2}})")
STREET_STOP = {"ב", "של", "ליד", "סמוך", "בעיר", "בשכונת", "פינת", "שבו", "שבה", "בו", "בה", "אחרי", "לאחר",
               "כאשר", "בזמן", "הלילה", "אמש", "היום", "בבוקר", "בערב", "נדקר", "נדקרה", "נורה", "הותקף"}
PRECISION_M = {"city": 4000, "town": 2500, "village": 1000, "hamlet": 700, "isolated_dwelling": 500,
               "neighborhood": 800, "street": 300, "unknown": 3000}


class Gazetteer:
    """localities: [{name, norm, code, lat, lon, kind, source}], neighborhoods: [{name, norm, city, lat, lon}]."""

    def __init__(self, localities: Sequence[dict], neighborhoods: Sequence[dict] = (), meta: Optional[dict] = None):
        self.localities = [dict(l) for l in localities]
        for l in self.localities:
            l.setdefault("norm", norm_name(l["name"]))
            l.setdefault("kind", "town")
        rank = {"city": 0, "town": 1, "village": 2, "hamlet": 3, "isolated_dwelling": 4, "unknown": 5}
        self.by_norm: Dict[str, dict] = {}
        for l in self.localities:            # "טמרה" מול "טמרה (יזרעאל)": מועדף city/town ושם בלי סוגריים
            cur = self.by_norm.get(l["norm"])
            key = (l.get("lat") is None, rank.get(l.get("kind"), 5), "(" in l["name"])
            if cur is None or key < (cur.get("lat") is None, rank.get(cur.get("kind"), 5), "(" in cur["name"]):
                self.by_norm[l["norm"]] = l
        for alias, target in ALIASES.items():
            if target and target in self.by_norm:
                self.by_norm.setdefault(norm_name(alias) if '"' not in alias else alias, self.by_norm[target])
        self.neighborhoods = [dict(n) for n in neighborhoods]
        for n in self.neighborhoods:
            n.setdefault("norm", norm_name(n["name"]))
        self.nb_by_city: Dict[str, List[dict]] = defaultdict(list)
        for n in self.neighborhoods:
            self.nb_by_city[norm_name(n["city"])].append(n)
        self.meta = meta or {}
        names = sorted(self.by_norm, key=len, reverse=True)
        alt = "|".join(re.escape(n).replace(r"\ ", r"[\s\-]+").replace("קרית", "קרי?ית").replace("תקוה", "תקו?וה")
                       for n in names if len(n) >= 2)
        # קידומות: ו / ש / ב|ל|מ בסדר הזה בלבד (וב, שב, ומ...). לא "ה": "השחורה" אינו ה+ש+"חורה"
        self._rx = re.compile(rf"(?<![{HEB}\w\"'])(?P<pre>ו?ש?[בלמ]?)(?P<name>{alt})(?![{HEB}\w])") if alt else None

    @classmethod
    def load(cls, path: str = GAZETTEER_PATH) -> Optional["Gazetteer"]:
        if not os.path.exists(path):
            return None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        return cls(d["localities"], d.get("neighborhoods", []), d.get("meta"))

    def save(self, path: str = GAZETTEER_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        strip = lambda d: {k: v for k, v in d.items() if k != "norm"}
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump({"meta": self.meta, "localities": [strip(l) for l in self.localities],
                       "neighborhoods": [strip(n) for n in self.neighborhoods]}, f, ensure_ascii=False)

    def find_localities(self, text: str) -> List[dict]:
        """כל אזכורי יישובים בטקסט, עם role (incident/residence) ו-strength."""
        if not self._rx:
            return []
        text = clean_text(text)
        out = []
        for m in self._rx.finditer(text):
            name = m.group("name")
            key = name if name in self.by_norm else norm_name(name)
            loc = self.by_norm.get(key)
            if loc is None:
                loc = self.by_norm.get(key.replace("קריית", "קרית").replace("תקווה", "תקוה"))
            if loc is None or loc.get("lat") is None:
                continue
            before = text[max(0, m.start() - 25):m.start()]
            pre = m.group("pre")
            if STREET_CUE.search(before) or re.search(r"(?:רחוב|רח'|שדרות|שד')\s*$", before + pre):
                continue                         # "ברחוב ירושלים" - שם רחוב, לא היישוב
            role = "residence" if RESIDENCE_CUE.search(before) or (
                pre.endswith("מ") and re.search(r"(?:בן|בת|בני|בנות)\s+\d+\s+$", before)) else "incident"
            cue = bool(LOCALITY_CUE.search(before + pre))
            single = " " not in loc["norm"] and "-" not in name
            if loc["norm"] in AMBIGUOUS_NAMES and not cue and role != "residence":
                continue
            if loc["norm"] == "שדרות" and re.match(r"\s*[\"']", text[m.end():m.end() + 2]):
                continue                         # 'בשדרות "החוקה"' - שם רחוב
            if single and loc.get("kind") in ("village", "hamlet", "isolated_dwelling", "unknown") and not cue \
                    and role != "residence" and not (re.search(r"[במ]$", pre) and len(loc["norm"]) >= 5):
                # כפר בשם חד-מילתי: צריך מילת הקשר, או "ב/מ" + שם של 5 אותיות לפחות
                # ("מניין"/"מבחן"/"מיקום" אינם "ניין"/"בחן"/"יקום")
                continue
            strength = 0.9 if cue or loc.get("kind") in ("city", "town") or not single else 0.6
            out.append({"loc": loc, "matched": m.group(0), "role": role, "start": m.start(), "strength": strength})
        return out

    def find_neighborhood(self, text: str, city_norm: str) -> Optional[dict]:
        text = clean_text(text)
        for nb in sorted(self.nb_by_city.get(city_norm, []), key=lambda n: -len(n["norm"])):
            body = re.escape(nb["norm"]).replace("\\ ", r"[\s\-]+")
            rx = re.compile(rf"(?<![{HEB}\w])(?P<pre>[ובלמשה]{{0,2}})" + body + rf"(?![{HEB}\w])")
            for m in rx.finditer(text):
                before = text[max(0, m.start() - 12):m.start()] + m.group("pre")
                cue = bool(NEIGHBORHOOD_CUE.search(before)) or "שכונת" in before
                if " " in nb["norm"] or cue:
                    return {"nb": nb, "cue": cue, "matched": m.group(0)}
        return None


def find_street(text: str, stop_tokens: Iterable[str] = ()) -> Optional[str]:
    """שם רחוב אחרי "רחוב/רח'/שדרות". stop_tokens: אזכורי יישובים שזוהו (למשל "בחיפה") - שם הרחוב נחתך לפניהם."""
    m = STREET_RX.search(clean_text(text))
    if not m:
        return None
    cand = m.group(1)
    for st in stop_tokens:               # "ז'בוטינסקי בבת ים" -> "ז'בוטינסקי"
        st = st.strip()
        i = cand.find(" " + st) if st else -1
        if i >= 0:
            cand = cand[:i]
    stops = set(STREET_STOP) | {t.strip() for t in stop_tokens}
    toks = []
    for t in cand.split():
        if t in stops or (toks and re.match(r"^[ובלמש]?(?:נדקר|נורה|הותקף|נפצע)", t)):
            break
        toks.append(t.strip(",.:;"))
    return " ".join(toks) or None


class NominatimStreetGeocoder:
    """גאוקוד רחוב+עיר דרך Nominatim הציבורי - כבוי כברירת מחדל. 4 בקשות לדקה (מדיניות OSMF לסקריפטים
    קבועים), cache לתמיד, User-Agent מזהה, עד max_requests לריצה. לשימוש קבוע/ייצור: Nominatim עצמי או
    extract של OSM (Geofabrik) - המדיניות לא מעודדת גאוקוד תכוף בשרת הציבורי.
    מקבל רק תוצאה מסוג highway בטווח 15 ק"מ ממרכז היישוב. התוצאה היא way אחד של הרחוב, לא כל הרחוב."""

    API = "https://nominatim.openstreetmap.org/search"

    def __init__(self, http: Http, max_requests: int = 50):
        self.http = http
        self.max_requests = max_requests
        self.used = 0

    def geocode(self, street: str, city: dict) -> Optional[dict]:
        params = {"street": street, "city": city["name"], "countrycodes": "il", "format": "jsonv2", "limit": 3,
                  "accept-language": "he"}
        email = os.environ.get("LIFEGUARD_CONTACT_EMAIL")
        if email:
            params["email"] = email
        url = self.API + "?" + urllib.parse.urlencode(params)
        cached = os.path.exists(self.http.cache_path("nominatim", url))
        if not cached and (self.http.offline or self.used >= self.max_requests):
            return None
        try:
            body = self.http.get(url, "nominatim", ttl=None)
            if not cached:
                self.used += 1
            res = json.loads(body)
        except (OfflineMiss, RuntimeError, ValueError):
            return None
        for r in res:
            if r.get("category", r.get("class")) != "highway":
                continue
            lat, lon = float(r["lat"]), float(r["lon"])
            if haversine_m(lat, lon, city["lat"], city["lon"]) <= 15000:
                prec = PRECISION_M["street"]
                bb = r.get("boundingbox")
                if bb and len(bb) == 4:      # חצי אלכסון של ה-way שהוחזר (קטע אחד; רחוב שלם יכול להיות ארוך יותר)
                    s_, n_, w_, e_ = map(float, bb)
                    prec = max(prec, round(haversine_m(s_, w_, n_, e_) / 2))
                return {"lat": lat, "lon": lon, "display": r.get("display_name", "")[:120], "precision_m": prec}
        return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(min(1.0, h)))


def geolocate(title: str, snippet: str, gaz: Optional[Gazetteer],
              street_geocoder: Optional[NominatimStreetGeocoder] = None) -> dict:
    """מיקום האירוע. level: street / neighborhood / city / none. רמת עיר לעולם לא מוצגת כרחוב:
    רחוב שזוהה בטקסט אבל לא גואוקד נשמר ב-street_text, ו-level נשאר city/neighborhood."""
    none = {"lat": None, "lon": None, "level": "none", "name": None, "confidence": 0.0, "precision_m": None}
    if gaz is None:
        return dict(none, note="gazetteer not built")
    mentions = gaz.find_localities(title) + [dict(m, start=m["start"] + 10000)
                                             for m in gaz.find_localities(snippet)]
    if not mentions:
        return none
    incident = [m for m in mentions if m["role"] == "incident"]
    pick = (incident or mentions)[0]
    loc = pick["loc"]
    in_title = pick["start"] < 10000
    conf = pick["strength"] * (1.0 if in_title else 0.85)
    role = pick["role"]
    if role == "residence":
        conf *= 0.4                         # רק מקום מגורים של המעורב - לא בהכרח מקום האירוע
    distinct = {m["loc"]["norm"] for m in incident}
    if len(distinct) > 1:
        conf *= 0.75                        # כמה יישובים "אירוע" בטקסט - דו-משמעי
    out = {"lat": loc["lat"], "lon": loc["lon"], "level": "city", "name": loc["name"],
           "confidence": round(conf, 2), "precision_m": PRECISION_M.get(loc.get("kind", "unknown"), 3000),
           "role": role, "matched_text": pick["matched"].strip()}
    if len(distinct) > 1:
        out["other_localities"] = sorted(distinct - {loc["norm"]})
    text = f"{title} . {snippet}"
    nb = gaz.find_neighborhood(text, loc["norm"])
    if nb and role == "incident":
        n = nb["nb"]
        out.update({"lat": n["lat"], "lon": n["lon"], "level": "neighborhood",
                    "name": f"{n['name']}, {loc['name']}", "precision_m": PRECISION_M["neighborhood"],
                    "confidence": round(conf * (0.9 if nb["cue"] else 0.75), 2)})
    street = find_street(text, [m["matched"] for m in mentions])
    if street and role == "incident":
        out["street_text"] = street
        geo = street_geocoder.geocode(street, loc) if street_geocoder else None
        if geo:
            out.update({"lat": geo["lat"], "lon": geo["lon"], "level": "street",
                        "name": f"{street}, {loc['name']}", "precision_m": geo.get("precision_m", PRECISION_M["street"]),
                        "confidence": round(conf * 0.85, 2), "street_geocoder": "nominatim"})
        else:
            out["street_note"] = "רחוב זוהה בטקסט אך לא גואוקד - המיקום הוא ברמת " + (
                "שכונה" if out["level"] == "neighborhood" else "עיר")
    return out


# ---------------------------------------------------------------- בניית ה-gazetteer

PIBA_LOCALITIES = "5c78e9fa-c2e2-4771-93ff-7f400a12f7ba"   # רשות האוכלוסין - רשימת ישובים בישראל (data.gov.il)
CKAN_SEARCH = "https://data.gov.il/api/3/action/datastore_search"
OVERPASS = "https://overpass-api.de/api/interpreter"
OSM_BBOX = (29.45, 34.2, 33.35, 35.95)
OSM_PLACE_QUERY = """[out:json][timeout:180];
node["place"~"^(city|town|village|hamlet|isolated_dwelling|suburb|neighbourhood|quarter)$"]({s},{w},{n},{e});
out body;"""


def build_gazetteer_from(piba_records: Iterable[dict], osm_elements: Iterable[dict],
                         nb_radius_m: float = 7000) -> Gazetteer:
    """join לפי שם מנורמל: שם וסמל מרשות האוכלוסין, קואורדינטות וסוג מ-OSM. שכונות (suburb/neighbourhood/quarter)
    משויכות ליישוב city/town הקרוב (עד nb_radius_m) - היוריסטיקה, נאמר במטא-דאטה."""
    osm_loc: Dict[str, dict] = {}
    osm_nb: List[dict] = []
    for el in osm_elements:
        tags = el.get("tags") or {}
        name = tags.get("name:he") or tags.get("name") or ""
        alts = [tags.get(k) for k in ("alt_name:he", "old_name:he", "official_name:he", "short_name:he")]
        alts = [a for a in alts if a and re.search(rf"[{HEB}]", a)]
        if not re.search(rf"[{HEB}]", name):
            continue
        rec = {"name": clean_text(name), "lat": el.get("lat"), "lon": el.get("lon"), "kind": tags.get("place"),
               "osm_id": el.get("id")}
        if rec["kind"] in ("suburb", "neighbourhood", "quarter"):
            osm_nb.append(rec)
        else:
            rank = ("city", "town", "village", "hamlet", "isolated_dwelling")
            for nm in [name] + [x for a in alts for x in a.split(";")]:
                k = norm_name(nm)
                if k not in osm_loc or rank.index(rec["kind"]) < rank.index(osm_loc[k]["kind"]):
                    osm_loc[k] = rec
    localities, matched = [], 0
    for r in piba_records:
        name = clean_text(str(r.get("שם_ישוב") or r.get("name") or ""))
        if not name:
            continue
        k = norm_name(name)
        o = osm_loc.get(k)
        if o is None:
            first = norm_name(re.split(r"\s*-\s*", name)[0])     # "בנימינה-גבעת עדה" -> "בנימינה"
            for alt in (k.replace("קרית", "קריית"), k.replace(" ", ""), first, k.replace(" אל ", " א"),
                        k.replace("ח'", "כ"), k.replace("'", ""), k.replace("גרביה", "גרבייה"),
                        k.replace("גרבייה", "גרביה")):
                o = osm_loc.get(alt)
                if o:
                    break
        rec = {"name": name, "code": str(r.get("סמל_ישוב") or r.get("code") or "").strip(),
               "lat": o["lat"] if o else None, "lon": o["lon"] if o else None,
               "kind": o["kind"] if o else "unknown", "source": "piba+osm" if o else "piba"}
        matched += bool(o)
        localities.append(rec)
    cities = [l for l in localities if l["lat"] is not None and l["kind"] in ("city", "town")]
    nbs = []
    for n in osm_nb:
        best, bd = None, nb_radius_m
        for c in cities:
            if abs(c["lat"] - n["lat"]) > 0.08 or abs(c["lon"] - n["lon"]) > 0.1:
                continue
            d = haversine_m(n["lat"], n["lon"], c["lat"], c["lon"])
            if d < bd:
                best, bd = c, d
        if best and norm_name(n["name"]) != norm_name(best["name"]):
            nbs.append({"name": n["name"], "city": best["name"], "lat": n["lat"], "lon": n["lon"], "kind": n["kind"]})
    meta = {"localities": len(localities), "with_coords": matched, "neighborhoods": len(nbs),
            "sources": [
                {"id": "piba_localities", "name": "רשות האוכלוסין וההגירה - רשימת ישובים בישראל (data.gov.il "
                                                  "citiesandsettelments)", "license": "Other (Open) - תנאי data.gov.il"},
                {"id": "osm_places", "name": "OpenStreetMap place nodes (Overpass API)",
                 "license": "ODbL 1.0 - '© OpenStreetMap contributors', share-alike למאגר נגזר"}],
            "neighborhood_assignment": f"nearest city/town node within {nb_radius_m:.0f} m (heuristic)"}
    return Gazetteer(localities, nbs, meta)


def build_gazetteer(http: Http, log=log_default) -> Gazetteer:
    q = {"resource_id": PIBA_LOCALITIES, "limit": 5000}
    piba = json.loads(http.get(CKAN_SEARCH + "?" + urllib.parse.urlencode(q), "piba", ttl=STATIC_TTL_S))
    recs = piba["result"]["records"]
    log(f"  רשות האוכלוסין: {len(recs)} יישובים")
    s, w, n, e = OSM_BBOX
    query = OSM_PLACE_QUERY.format(s=s, w=w, n=n, e=e)
    osm = json.loads(http.get(OVERPASS + "?" + urllib.parse.urlencode({"data": query}), "overpass", ttl=STATIC_TTL_S))
    els = osm.get("elements", [])
    log(f"  OSM: {len(els)} place nodes")
    g = build_gazetteer_from(recs, els)
    g.meta["built_at"] = _now_iso(http.clock())
    return g


# ================================================================= איחוד כפילויות + דעיכה

HALF_LIFE_DAYS = {"riot": 3, "assault": 7, "robbery": 10, "stabbing": 10, "shooting": 10, "explosive": 10, "homicide": 14,
                  "sexual": 14, "kidnapping": 14}
MAX_AGE_DAYS = 30
_STOP = {"של", "על", "את", "עם", "לא", "הוא", "היא", "כי", "גם", "אחרי", "לאחר", "בן", "בת", "שנים", "בני",
         "תיעוד", "צפו", "דיווח", "חשד", "המשטרה", "משטרה", "האירוע", "אירוע", "קשה", "באורח", "בינוני", "קל"}


def _tokens(s: str) -> set:
    toks = re.findall(rf"[{HEB}]{{2,}}|\d+", clean_text(s))
    out = set()
    for t in toks:
        t2 = re.sub(rf"^[ובלמשה]{{1,2}}(?=[{HEB}]{{3,}})", "", t) if len(t) > 4 else t
        if t2 not in _STOP:
            out.add(t2)
    return out


def decay_weight(age_s: float, category: str) -> float:
    """משקל דעיכה: חצי-חיים לפי קטגוריה, 0 אחרי MAX_AGE_DAYS. גיל שלילי (שעון) -> 1."""
    age_d = max(0.0, age_s / 86400)
    if age_d > MAX_AGE_DAYS:
        return 0.0
    return round(0.5 ** (age_d / HALF_LIFE_DAYS.get(category, 7)), 3)


VIOLENT_DEATH = {"homicide", "stabbing", "shooting", "explosive"}


def _compatible(a: str, b: str, same_city: bool) -> bool:
    """אותה קטגוריה, או (באותו יישוב) רצח מול דקירה/ירי - כלי תקשורת שונים מתארים אותו אירוע אחרת."""
    return a == b or (same_city and a in VIOLENT_DEATH and b in VIOLENT_DEATH)


def dedup(items: List[dict], window_h: float = 48, min_jaccard: float = 0.25) -> List[dict]:
    """אשכול של אותו אירוע ממקורות שונים: קטגוריה תואמת, עד 48 שעות, אותו יישוב (או שלאחד אין מיקום),
    ודמיון כותרות (Jaccard על מילים) >= 0.25 או מספר מזהה משותף (גיל) + יישוב זהה.
    קטגוריית האשכול: הספציפית ביותר לפי PRECEDENCE."""
    items = sorted(items, key=lambda x: x.get("published_at") or "")
    clusters: List[dict] = []
    for it in items:
        ts = iso_to_ts(it.get("published_at")) or 0
        tok = _tokens(it["title"] + " " + it.get("snippet", ""))
        tok_t = _tokens(it["title"])
        place = (it.get("location") or {}).get("name")
        city = place.split(", ")[-1] if place else None
        best, best_sim = None, 0.0
        for c in clusters:
            if abs(ts - c["_ts"]) > window_h * 3600:
                continue
            if city and c["_city"] and city != c["_city"]:
                continue
            if not _compatible(c["category"], it["category"], bool(city and city == c["_city"])):
                continue
            inter = tok & c["_tok"]
            sim = len(inter) / max(1, len(tok | c["_tok"]))
            sim_t = len(tok_t & c["_tok_t"]) / max(1, min(len(tok_t), len(c["_tok_t"])))
            nums = {t for t in inter if t.isdigit()}
            ok = sim >= min_jaccard or sim_t >= 0.5 or (nums and city and city == c["_city"] and sim >= 0.12)
            if ok and max(sim, sim_t) > best_sim:
                best, best_sim = c, max(sim, sim_t)
        if best is None:
            clusters.append({"category": it["category"], "_ts": ts, "_tok": set(tok), "_tok_t": set(tok_t),
                             "_city": city, "members": [it]})
        else:
            best["members"].append(it)
            best["category"] = min((best["category"], it["category"]),
                                   key=lambda k: PRECEDENCE.index(k) if k in PRECEDENCE else 99)
            best["_tok"] |= tok
            best["_tok_t"] |= tok_t
            best["_city"] = best["_city"] or city
    out = []
    for c in clusters:
        ms = c["members"]
        # המייצג: מיקום ברמה המדויקת ביותר, אחר כך הוודאות הגבוהה ביותר
        lvl = {"street": 3, "neighborhood": 2, "city": 1, "none": 0}
        rep = max(ms, key=lambda m: (lvl[(m.get("location") or {}).get("level", "none")],
                                     (m.get("location") or {}).get("confidence", 0), m["confidence"]))
        pubs = sorted({m["publisher"] for m in ms})
        conf = max(m["confidence"] for m in ms)
        # אישוש: כמה מפרסמים שונים. כלי תקשורת מעתיקים את אותה הודעת משטרה -> תוספת קטנה ומוגבלת
        conf = round(min(0.95, conf + 0.05 * min(3, len(pubs) - 1)), 2)
        first = min((m.get("published_at") for m in ms if m.get("published_at")), default=None)
        out.append({
            "id": "news-" + hashlib.sha1("|".join(sorted(item_id(m["url"]) for m in ms)).encode()).hexdigest()[:12],
            "type": "news_report", "verified": False, "label": LABEL_HE,
            "category": c["category"], "category_he": CATEGORY_HE.get(c["category"], c["category"]),
            "title": rep["title"], "snippet": rep.get("snippet", ""), "url": rep["url"], "publisher": rep["publisher"],
            "published_at": first, "location": rep.get("location"), "confidence": conf,
            "classification": rep.get("classification"), "publishers": pubs,
            "sources": [{"publisher": m["publisher"], "url": m["url"], "title": m["title"],
                         "published_at": m.get("published_at"), "provider": m["provider"]} for m in ms],
        })
    return out


# ================================================================= store + צינור

def load_store(path: str = STORE_PATH) -> Dict[str, dict]:
    if not os.path.exists(path):
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def save_store(store: Dict[str, dict], path: str = STORE_PATH, now: Optional[float] = None) -> None:
    """שומר רק שדות מותרים (כותרת, תקציר קצר, URL, מפרסם, זמנים). פריטי Google (transient) לא נשמרים."""
    now = now or time.time()
    keep = ("provider", "publisher", "feed", "title", "snippet", "url", "published_at", "fetched_at")
    clean = {}
    for k, v in store.items():
        if v.get("transient"):
            continue
        ts = iso_to_ts(v.get("published_at") or v.get("fetched_at")) or now
        if now - ts > STORE_RETENTION_DAYS * 86400:
            continue
        clean[k] = {f: v.get(f) for f in keep}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False)
    os.replace(tmp, path)


def analyze(raw_items: Iterable[dict], gaz: Optional[Gazetteer], now: float, days: float = 14,
            street_geocoder: Optional[NominatimStreetGeocoder] = None) -> Tuple[List[dict], dict]:
    """סיווג + מיקום + סינון לפי חלון זמן + איחוד. מחזיר (clusters, stats)."""
    year = datetime.fromtimestamp(now, timezone.utc).year
    seen, relevant = set(), []
    stats = {"raw": 0, "unique_urls": 0, "in_window": 0, "no_date": 0, "excluded": Counter(),
             "relevant": 0, "by_provider": Counter()}
    for it in raw_items:
        stats["raw"] += 1
        iid = item_id(it["url"])
        if iid in seen:
            continue
        seen.add(iid)
        stats["unique_urls"] += 1
        ts = iso_to_ts(it.get("published_at"))
        if ts is None:
            stats["no_date"] += 1
            if not it.get("transient"):
                continue           # בלי תאריך אי אפשר לדעוך/לסנן חדשות ישנות -> לא נכנס
            ts = iso_to_ts(it.get("fetched_at")) or now
        if now - ts > days * 86400:
            continue
        stats["in_window"] += 1
        stats["by_provider"][it["provider"]] += 1
        loc = geolocate(it["title"], it.get("snippet", ""), gaz, street_geocoder)
        israeli = loc["level"] != "none"
        cl = classify(it["title"], it.get("snippet", ""), now_year=year, israeli_place=israeli)
        if not cl["relevant"]:
            stats["excluded"][cl["category"]] += 1
            continue
        stats["relevant"] += 1
        # ודאות כוללת: סיווג x (מיקום חלקי). פריט בלי מיקום נשאר, אבל לא ייכנס למפה
        conf = cl["confidence"] * (0.6 + 0.4 * loc["confidence"]) if israeli else cl["confidence"] * 0.5
        relevant.append(dict(it, category=cl["category"], confidence=round(conf, 2), location=loc,
                             classification={"confidence": cl["confidence"], "matched": cl["matched"][:4],
                                             "flags": cl["flags"],
                                             "excluded_by": [e["rule"] for e in cl["excluded_by"]]}))
    clusters = dedup(relevant)
    for c in clusters:
        ts = iso_to_ts(c["published_at"]) or now
        c["age_hours"] = round((now - ts) / 3600, 1)
        c["decay_weight"] = decay_weight(now - ts, c["category"])
    clusters.sort(key=lambda c: (-(c["confidence"] * c["decay_weight"]), c["published_at"] or ""))
    stats["excluded"] = dict(stats["excluded"].most_common())
    stats["by_provider"] = dict(stats["by_provider"])
    return clusters, stats


def summarize(clusters: List[dict], stats: dict, provider_status: List[dict], gaz: Optional[Gazetteer],
              now: float, days: float) -> dict:
    lv = Counter((c.get("location") or {}).get("level", "none") for c in clusters)
    return {
        "generated_at": _now_iso(now), "window_days": days,
        "label": LABEL_HE,
        "counts": {"raw_items": stats["raw"], "unique_urls": stats["unique_urls"], "in_window": stats["in_window"],
                   "relevant_items": stats["relevant"], "incident_clusters": len(clusters),
                   "multi_publisher_clusters": sum(1 for c in clusters if len(c["publishers"]) > 1)},
        "by_category": dict(Counter(c["category"] for c in clusters).most_common()),
        "by_location_level": dict(lv.most_common()),
        "excluded_by_reason": stats["excluded"], "items_without_date": stats["no_date"],
        "by_provider_in_window": stats["by_provider"],
        "providers": provider_status,
        "gazetteer": (gaz.meta if gaz else {"status": "not built - run: python -m research_tools.news_intel gazetteer"}),
        "storage_policy": "נשמרים רק כותרת, תקציר קצר מהפיד (עד %d תווים), URL, מפרסם וזמן פרסום. אין טקסט מלא. "
                          "תוצאות Google לא נשמרות." % SNIPPET_MAX,
        "attribution": ["GDELT Project - https://www.gdeltproject.org/",
                        "© OpenStreetMap contributors (ODbL)",
                        "רשות האוכלוסין וההגירה, רשימת ישובים, data.gov.il",
                        "כותרות: כל זכויות היוצרים למפרסמים; כל פריט מקשר למקור"],
    }


def run(providers: Sequence[str] = ("rss", "gdelt", "google"), offline: bool = False, days: float = 14,
        nominatim: bool = False, feeds: Optional[Sequence[dict]] = None, http: Optional[Http] = None,
        gaz: Optional[Gazetteer] = None, out_dir: str = OUT_DIR, store_path: str = STORE_PATH,
        gdelt_queries: int = 4, log=log_default) -> dict:
    http = http or Http(offline=offline, log=log)
    now = http.clock()
    gaz = gaz or Gazetteer.load()
    if gaz is None:
        log("  אזהרה: אין gazetteer (python -m research_tools.news_intel gazetteer). מיקום לא יחושב")
    store = load_store(store_path)
    fetched, status = [], []
    for p in providers:
        prov = {"rss": lambda: RssProvider(feeds), "gdelt": lambda: GdeltProvider(max_queries=gdelt_queries),
                "google": lambda: GoogleCseProvider()}[p]()
        log(f"[{p}]")
        its, st = prov.fetch(http, log=log)
        st["items"] = len(its)
        status.append(st)
        fetched.extend(its)
        log(f"  {len(its)} פריטים")
    for it in fetched:
        k = item_id(it["url"])
        if it.get("transient"):
            continue
        prev = store.get(k)
        if prev is None or (not prev.get("snippet") and it.get("snippet")):
            store[k] = it
    if not offline:
        save_store(store, store_path, now)
    transient = [it for it in fetched if it.get("transient")]
    geo = NominatimStreetGeocoder(http) if nominatim else None
    clusters, stats = analyze(list(store.values()) + transient, gaz, now, days, geo)
    summary = summarize(clusters, stats, status, gaz, now, days)
    if geo:
        summary["nominatim_requests"] = geo.used
    persisted = [c for c in clusters if not all(s["provider"] == "google" for s in c["sources"])]
    summary["google_only_clusters_not_saved"] = len(clusters) - len(persisted)
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H%M")
    path = os.path.join(out_dir, f"news_intel_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "items": persisted}, f, ensure_ascii=False, indent=1)
    log(f"נכתב: {path} ({len(persisted)} אשכולות)")
    return {"path": path, "summary": summary, "items": persisted}


# ================================================================= הערכה מול תיוג ידני

def evaluate(labels: Dict[str, dict], store: Dict[str, dict], gaz: Optional[Gazetteer], now: float) -> dict:
    """labels: {url: {"relevant": bool, "category": str|None, "locality": str|None}}  (תיוג ידני).
    מודד: precision/recall לרלוונטיות, דיוק קטגוריה בין ה-TP, ודיוק יישוב בין ה-TP עם יישוב מתויג."""
    year = datetime.fromtimestamp(now, timezone.utc).year
    by_url = {v["url"]: v for v in store.values()}
    tp = fp = fn = tn = 0
    cat_ok = cat_n = loc_ok = loc_n = loc_found = 0
    errors = []
    lvl = Counter()
    for url, lab in labels.items():
        it = by_url.get(url)
        if it is None:
            continue
        loc = geolocate(it["title"], it.get("snippet", ""), gaz)
        cl = classify(it["title"], it.get("snippet", ""), now_year=year, israeli_place=loc["level"] != "none")
        pred, gold = cl["relevant"], bool(lab.get("relevant"))
        if pred and gold:
            tp += 1
            cat_n += 1
            cat_ok += cl["category"] == lab.get("category")
            if cl["category"] != lab.get("category"):
                errors.append({"type": "category", "title": it["title"][:90], "pred": cl["category"],
                               "gold": lab.get("category")})
            if lab.get("locality"):
                loc_n += 1
                lvl[loc["level"]] += 1
                if loc["level"] != "none":
                    loc_found += 1
                    loc_ok += norm_name(loc["name"].split(", ")[-1]) == norm_name(lab["locality"])
                    if norm_name(loc["name"].split(", ")[-1]) != norm_name(lab["locality"]):
                        errors.append({"type": "location", "title": it["title"][:90], "pred": loc["name"],
                                       "gold": lab["locality"]})
                else:
                    errors.append({"type": "location_missing", "title": it["title"][:90], "gold": lab["locality"]})
        elif pred and not gold:
            fp += 1
            errors.append({"type": "FP", "title": it["title"][:90], "pred": cl["category"]})
        elif gold and not pred:
            fn += 1
            errors.append({"type": "FN", "title": it["title"][:90], "gold": lab.get("category"),
                           "pred": cl["category"]})
        else:
            tn += 1
    n = tp + fp + fn + tn
    return {"n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "category_accuracy_on_tp": round(cat_ok / cat_n, 3) if cat_n else None,
            "locality_labelled_tp": loc_n, "locality_found": loc_found,
            "locality_correct": loc_ok, "location_levels": dict(lvl), "errors": errors}


def label_template(store: Dict[str, dict], out_path: str, now: float, days: float = 14) -> int:
    rows = []
    for v in store.values():
        ts = iso_to_ts(v.get("published_at"))
        if ts is None or now - ts > days * 86400:
            continue
        rows.append(v)
    rows.sort(key=lambda v: v["url"])
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["url", "publisher", "published_at", "title", "snippet", "relevant", "category", "locality"])
        for v in rows:
            w.writerow([v["url"], v["publisher"], v.get("published_at"), v["title"], v.get("snippet", ""), "", "", ""])
    return len(rows)


# ================================================================= CLI

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="LifeGuard news intel (research)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gazetteer")
    g.add_argument("--offline", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--offline", action="store_true")
    r.add_argument("--providers", default="rss,gdelt,google")
    r.add_argument("--days", type=float, default=14)
    r.add_argument("--nominatim", action="store_true", help="גאוקוד רחובות (Nominatim, עד 50 בקשות, 1/שנייה)")
    r.add_argument("--feeds", help="קובץ JSON עם רשימת פידים [{url, publisher, section}]")
    r.add_argument("--gdelt-queries", type=int, default=4)
    t = sub.add_parser("label-template")
    t.add_argument("--out", default=os.path.join(OUT_DIR, "label_template.csv"))
    t.add_argument("--days", type=float, default=14)
    e = sub.add_parser("eval")
    e.add_argument("--labels", required=True)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    if a.cmd == "gazetteer":
        gz = build_gazetteer(Http(offline=a.offline))
        gz.save()
        log_default(json.dumps(gz.meta, ensure_ascii=False))
    elif a.cmd == "run":
        feeds = None
        if a.feeds:
            with open(a.feeds, encoding="utf-8") as f:
                feeds = json.load(f)
        res = run([p.strip() for p in a.providers.split(",") if p.strip()], offline=a.offline, days=a.days,
                  nominatim=a.nominatim, feeds=feeds, gdelt_queries=a.gdelt_queries)
        log_default(json.dumps({k: res["summary"][k] for k in ("counts", "by_category", "by_location_level")},
                               ensure_ascii=False))
    elif a.cmd == "label-template":
        n = label_template(load_store(), a.out, time.time(), a.days)
        log_default(f"{n} שורות -> {a.out}")
    elif a.cmd == "eval":
        with open(a.labels, encoding="utf-8") as f:
            labels = json.load(f)
        store = load_store()
        now = max((iso_to_ts(v.get("fetched_at")) or 0) for v in store.values()) if store else time.time()
        res = evaluate(labels, store, Gazetteer.load(), now)
        log_default(json.dumps({k: v for k, v in res.items() if k != "errors"}, ensure_ascii=False))
        for err in res["errors"]:
            log_default("  " + json.dumps(err, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
