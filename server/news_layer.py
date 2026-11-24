# -*- coding: utf-8 -*-
"""
news_layer.py - דיווחים מהתקשורת כאות חלש ומסומן "דווח בתקשורת, לא מאומת" (research/news_intel_report.md §6).

מקור: קובץ פלט של research_tools/news_intel.py (summary + items). נטען רק אם LIFEGUARD_NEWS_FILE מוגדר
בשרת - **כבוי כברירת מחדל** - ובנוסף כל משתמש/ת צריך/ה לבקש אותו (include_news). אין גישה לגוגל:
Custom Search סגור ללקוחות חדשים ואוסר שמירה/סינון, ו-Google News RSS חסום ב-robots.txt.
לפני ייצור: אישור בכתב ממפרסמי RSS, או GDELT בלבד (עם ייחוס).

כללים:
- רק פריט עם תאריך, עד 30 יום, ביטחון >= 0.5, בלי מעקב מעצר/חקירה / כותרת-שאלה / אירוע פרטי-משפחתי.
- מוצג: קטגוריה, יישוב, תאריך, מפרסם וקישור - **בלי הכותרת** (כותרות מכילות שמות של מעורבים).
- רק מיקום ברמת רחוב/שכונה נכנס לניקוד מסלול, במשקל 0.1 (פחות מדיווח משתמש לא מאומת, 0.3).
  מיקום ברמת עיר = הקשר עירוני בלבד ("N דיווחים בעיר"), לא נקודה על המפה.
- בלי heatmap / ציון שכונה מחדשות (סטיגמה); חדשות לבדן לא מעלות מסלול מעל "בינוני".
"""

import json
import math
import os
import time

LABEL_HE = "דווח בתקשורת, לא מאומת"
MAX_AGE_DAYS = 30
MIN_CONFIDENCE = 0.5
HIDE_FLAGS = {"followup_arrest_or_investigation", "question_headline", "private_or_domestic_context"}
POINT_LEVELS = {"street", "neighborhood"}
W_NEWS = 0.1
NEWS_SEGMENT_CAP = 0.2            # לכל מקטע 100 מ', גם אם יש כמה דיווחים
NEWS_LEVEL_CAP_PER_KM = 7.5       # חדשות לבדן לא מעלות מסלול ל"גבוה" (סף 8)
POINT_RADIUS_MAX_M = 300
HALF_LIFE_DAYS = {"riot": 3, "assault": 7, "robbery": 10, "stabbing": 10, "shooting": 10, "explosive": 10,
                  "homicide": 14, "sexual": 14, "kidnapping": 14}
ATTRIBUTION = ["GDELT Project - https://www.gdeltproject.org/", "© OpenStreetMap contributors (ODbL)",
               "כותרות וכתבות: כל הזכויות למפרסמים; כל פריט מקשר למקור"]
BIAS_NOTE = ("הכיסוי התקשורתי חלקי ומוטה: אירועים במקומות מסוימים מדווחים יותר מאחרים. "
             "היעדר דיווח אינו אומר שהמקום בטוח.")


def _ts(iso):
    if not iso:
        return None
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(min(1.0, h)))


def decay(age_s, category):
    if age_s < 0 or age_s > MAX_AGE_DAYS * 86400:
        return 0.0
    return 0.5 ** (age_s / 86400 / HALF_LIFE_DAYS.get(category, 7))


def _flags(it):
    return set((it.get("classification") or {}).get("flags") or []) | set(it.get("flags") or [])


class NewsLayer:
    def __init__(self, data=None, path=None):
        d = data or {}
        self.path = path
        self.generated_at = (d.get("summary") or {}).get("generated_at")
        self.items = [it for it in d.get("items") or [] if self._usable(it)]
        self.available = data is not None

    @staticmethod
    def _usable(it):
        loc = it.get("location") or {}
        return (_ts(it.get("published_at")) is not None and (it.get("confidence") or 0) >= MIN_CONFIDENCE
                and not (_flags(it) & HIDE_FLAGS) and loc.get("lat") is not None and loc.get("level") != "none")

    @classmethod
    def from_env(cls):
        path = os.environ.get("LIFEGUARD_NEWS_FILE", "").strip()
        if not path or not os.path.exists(path):
            return cls()
        try:
            with open(path, encoding="utf-8") as fh:
                return cls(json.load(fh), path)
        except (OSError, ValueError):
            return cls()

    # ---------------------------------------------------------------- שאילתות
    def _live(self, now):
        for it in self.items:
            w = decay(now - _ts(it["published_at"]), it.get("category"))
            if w > 0:
                yield it, w

    @staticmethod
    def public(it, w=None):
        """בלי כותרת ובלי תקציר (שמות מעורבים); קטגוריה, יישוב, תאריך, מפרסמים, קישורים."""
        loc = it["location"]
        return {"layer": "news", "type": "news_report", "verified": False, "confidence": "low",
                "label": LABEL_HE, "category": it.get("category"), "category_he": it.get("category_he"),
                "place": loc.get("name"), "street": loc.get("street_text") if loc.get("level") == "street" else None,
                "level": loc.get("level"), "published_at": it["published_at"],
                "publishers": it.get("publishers") or [it.get("publisher")],
                "links": [s["url"] for s in (it.get("sources") or [])][:5] or [it.get("url")],
                "weight": round(w, 3) if w is not None else None,
                "source": "דיווחים בתקשורת (GDELT / RSS מפרסמים)", "last_update": _ts(it["published_at"]),
                "text": f"{it.get('category_he') or ''} · {loc.get('name') or ''}"
                        + (f", רחוב {loc['street_text']}" if loc.get("level") == "street" and loc.get("street_text") else "")
                        + f" · {LABEL_HE} ({', '.join((it.get('publishers') or [it.get('publisher')])[:3])})"}

    def near_points(self, lat, lon, radius_m, now=None):
        """דיווחים ברמת רחוב/שכונה בטווח - רק אלה מוצגים כנקודה."""
        now = now or time.time()
        out = []
        for it, w in self._live(now):
            loc = it["location"]
            if loc.get("level") not in POINT_LEVELS:
                continue
            reach = radius_m + min(loc.get("precision_m") or POINT_RADIUS_MAX_M, POINT_RADIUS_MAX_M)
            if haversine_m((lat, lon), (loc["lat"], loc["lon"])) <= reach:
                out.append((it, w))
        return out

    def city_context(self, lat, lon, now=None, days=7):
        """דיווחים ברמת עיר שהנקודה בתוך תחום הדיוק שלהם -> ספירה לפי עיר (הקשר בלבד)."""
        now = now or time.time()
        by_city = {}
        for it, _w in self._live(now):
            loc = it["location"]
            if loc.get("level") in POINT_LEVELS or now - _ts(it["published_at"]) > days * 86400:
                continue
            if haversine_m((lat, lon), (loc["lat"], loc["lon"])) <= (loc.get("precision_m") or 4000):
                by_city.setdefault(loc.get("name") or "?", []).append(it)
        return by_city

    def segment_weight(self, lat, lon, now):
        w = sum(W_NEWS * x for _it, x in self.near_points(lat, lon, 0, now))
        return min(w, NEWS_SEGMENT_CAP)
