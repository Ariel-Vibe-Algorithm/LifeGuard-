# -*- coding: utf-8 -*-
"""
risk_factors_layer.py - גורמי סיכון סביבתיים לבחירת מסלול (מחקר: research/risk_factors_report.md).

הרשת models/risk_factors_grid.json.gz נבנית ע"י research_tools/risk_factors.py (לא במאגר - gitignored).
חסר קובץ -> "לא זמין" (לא ניחוש). מודול עצמאי: השרת לא תלוי בקוד המחקר.

רק גורמים שיש להם גם ספרות וגם אות בנתוני המשטרה המקומיים (מעבר לפעילות מסחרית כללית):
- nightlife: ברים/מועדונים (OSM) / ברים ברישוי עסקים ת"א / אזור בילוי עירוני ת"א עם סגירה 02:00+.
  IRR 1.10 [1.01-1.19] ב-4/4 ערים; ספרות: Rossow & Norström 2012, Kypri et al., Twinam 2017.
  בלילה בלבד; משקל מלא בלילות חמישי-שבת 22:00-05:00 (שם האפקט מרוכז בספרות).
- transit: תחנת רכבת/רק"ל. IRR 1.13, 3/3 ערים, בעיקר שוד. ניסיוני - חשד לערבוב עם "מרכז העיר".
לא מנוקדים (אין אות מעבר לפעילות / אין נתונים): כספומטים, חנויות אלכוהול, תחנות אוטובוס, פארקים,
מבנים נטושים, תחנות משטרה, מצלמות (פרטיות), מדד חברתי-כלכלי (אתיקה).

פשיעה אזורית (משטרה, אזור סטטיסטי 2011): **להצגה בלבד**, לא משפיעה על בחירת מסלול. ניסוח: נתון אזורי,
לא מידע על הרחוב; מושפע ממבקרים; אף פעם לא "שכונה מסוכנת".
"""

import datetime as _dt
import gzip
import json
import math
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRID_PATH = os.path.join(HERE, "models", "risk_factors_grid.json.gz")

NIGHTLIFE_MIN_VENUES = 3
NIGHT_ZONE_MIN = 3           # ערך = שעת סגירה + 1; 3 = אזור בילוי עם סגירה מותרת ב-02:00 או מאוחר יותר
W_NIGHTLIFE = 0.75
W_NIGHTLIFE_WEEKEND = 1.0
W_TRANSIT = 0.25
NIGHTLIFE_LEVEL_CAP_PER_KM = 7.5   # בילוי לבדו לא מעלה מסלול ל"גבוה" (סף 8)
AREA_UNSTABLE_FLAGS = {"small_population", "low_case_count", "visitor_traffic_name_heuristic",
                       "non_residential_area", "high_ratio_check_land_use"}
FLAG_HE = {"small_population": "אוכלוסייה קטנה", "low_case_count": "מעט תיקים - הנתון לא יציב",
           "visitor_traffic_name_heuristic": "אזור עם הרבה מבקרים", "non_residential_area": "אזור לא למגורים",
           "high_ratio_check_land_use": "יחס גבוה - כנראה אזור מסחר/בילוי (מבקרים)"}
CITY_HE = {5000: "תל אביב-יפו", 3000: "ירושלים", 4000: "חיפה", 9000: "באר שבע"}
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"


def _cell(lat, lon, deg):
    return int(math.floor(lat / deg)), int(math.floor(lon / deg))


class RiskFactors:
    def __init__(self, data=None):
        d = data or {}
        self.available = bool(d.get("cells"))
        self.deg = d.get("cell_deg", 0.001)
        names = (d.get("fields") or [])[2:]
        self.cells, self.meta = {}, {}
        for k, v in (d.get("cells") or {}).items():
            key = tuple(int(x) for x in k.split(":"))
            self.meta[key] = (v[0], v[1])
            c = {n: x for n, x in zip(names, v[2:]) if x}
            if c:
                self.cells[key] = c
        self.areas = d.get("stat_areas") or {}
        self.coverage = {int(k): v for k, v in (d.get("coverage") or {}).items()}
        self.years = d.get("years") or []
        self.built_at = d.get("built_at")

    @classmethod
    def load(cls, path=GRID_PATH):
        if not os.path.exists(path):
            return cls()
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return cls(json.load(fh))
        except (OSError, ValueError, KeyError, TypeError):
            return cls()

    # ---------------------------------------------------------------- שאילתות
    def city_at(self, lat, lon):
        m = self.meta.get(_cell(lat, lon, self.deg))
        return m[0] if m else None

    def near(self, lat, lon):
        """סכום בתא ובשכנים (3x3, ~150 מ' לכל כיוון). אזור בילוי/פארק: מקסימום."""
        cy, cx = _cell(lat, lon, self.deg)
        out = {}
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for k, v in (self.cells.get((cy + dy, cx + dx)) or {}).items():
                    out[k] = max(out.get(k, 0), v) if k in ("park", "park_tlv", "night_zone_close") else out.get(k, 0) + v
        return out

    def flags_at(self, lat, lon):
        """-> {"nightlife": bool, "transit": bool, "covered": bool, "official": bool} לנקודה."""
        city = self.city_at(lat, lon)
        if city is None:
            return {"nightlife": False, "transit": False, "covered": False, "official": False}
        n = self.near(lat, lon)
        official = bool((self.coverage.get(city) or {}).get("tlv_municipal"))
        osm_venues = n.get("bar", 0) + n.get("nightclub", 0)
        licensed = n.get("bar_licensed_tlv", 0)
        # אזור בילוי עירוני = היכן שמותר לסגור מאוחר (תכנון), לא היכן שיש ברים בפועל - מכסה ~29% מת"א
        # כולל שכונות מגורים (נמצא בבדיקה על רמת אביב). לכן הוא נספר רק כשיש בקרבת מקום גם מקום בילוי אמיתי.
        zone_active = n.get("night_zone_close", 0) >= NIGHT_ZONE_MIN and (licensed + osm_venues) >= 1
        night_official = licensed >= NIGHTLIFE_MIN_VENUES or zone_active
        nightlife = night_official or osm_venues >= NIGHTLIFE_MIN_VENUES
        return {"nightlife": nightlife, "nightlife_official": night_official,
                "transit": n.get("rail_station", 0) >= 1, "covered": True, "official": official}

    def coverage_label(self, city):
        if city is None:
            return "none"
        return "official+osm" if (self.coverage.get(city) or {}).get("tlv_municipal") else "osm_partial"

    def osm_snapshot(self, city):
        return (self.coverage.get(city) or {}).get("osm_base")

    def area_context(self, lat, lon):
        m = self.meta.get(_cell(lat, lon, self.deg))
        if not m:
            return None
        a = self.areas.get(f"{m[0]}:{m[1]}")
        return dict(a, city=m[0], stat11=m[1]) if a else None


# ---------------------------------------------------------------- זמן מקומי (ישראל)
def _last_sunday(year, month):
    d = _dt.date(year, month, 31 if month in (3, 10) else 30)
    return d - _dt.timedelta(days=(d.weekday() + 1) % 7)


def israel_local(at):
    """datetime מקומי בישראל. zoneinfo אם יש tzdata; אחרת חוק שעון הקיץ (מ-2013): מיום שישי שלפני
    יום ראשון האחרון של מרץ 02:00 ועד יום ראשון האחרון של אוקטובר 02:00."""
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.fromtimestamp(at, ZoneInfo("Asia/Jerusalem"))
    except Exception:  # noqa: BLE001 - Windows בלי tzdata
        pass
    utc = _dt.datetime.utcfromtimestamp(at)
    start = _dt.datetime.combine(_last_sunday(utc.year, 3) - _dt.timedelta(days=2), _dt.time(0, 0))  # 02:00 מקומי = 00:00 UTC
    end = _dt.datetime.combine(_last_sunday(utc.year, 10), _dt.time(23, 0)) - _dt.timedelta(days=1)  # 02:00 IDT = 23:00 UTC
    return utc + _dt.timedelta(hours=3 if start <= utc < end else 2)


def weekend_late(at):
    """לילות חמישי, שישי ושבת, 22:00-05:00 שעון ישראל."""
    t = israel_local(at)
    wd = t.weekday()          # שני=0 ... ראשון=6
    if t.hour >= 22:
        return wd in (3, 4, 5)                  # חמישי/שישי/שבת בערב
    if t.hour < 5:
        return wd in (4, 5, 6)                  # המשך הלילה: שישי/שבת/ראשון לפנות בוקר
    return False


def area_item(a, years):
    """פריט street_intel לפשיעה אזורית - נוסח זהיר, להצגה בלבד."""
    flags = [f for f in str(a.get("flags") or "").split(";") if f]
    unstable = bool(set(flags) & AREA_UNSTABLE_FLAGS)
    yrs = f"{years[0]}-{years[-1]}" if years else ""
    ratio = a.get("eb_ratio")
    if ratio is None:
        rel = "אין מספיק נתונים להשוואה"
    elif ratio >= 1.5:
        rel = "נרשמו בו יותר תיקי אלימות ושוד ביחס למספר התושבים מאשר באזור טיפוסי בעיר"
    elif ratio <= 0.67:
        rel = "נרשמו בו פחות תיקי אלימות ושוד ביחס למספר התושבים מאשר באזור טיפוסי בעיר"
    else:
        rel = "מספר התיקים ביחס לתושבים דומה לאזור טיפוסי בעיר"
    text = (f"נתוני משטרה ברמת אזור סטטיסטי ({yrs}): {rel}. זה נתון אזורי, לא מידע על הרחוב הזה; "
            "באזורי מסחר ובילוי המספר מושפע ממבקרים.")
    if flags:
        text += " (" + ", ".join(FLAG_HE.get(f, f) for f in flags) + ")"
    return {"layer": "area_crime", "type": "official", "source": f"משטרת ישראל + הלמ\"ס (אזורים סטטיסטיים 2011), {yrs}",
            "confidence": "low" if unstable else "medium", "verified": True,
            "value": {"eb_ratio": ratio, "flags": flags, "years": years, "city": a.get("city")},
            "affects_route": False, "text": text}
