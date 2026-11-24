# -*- coding: utf-8 -*-
"""
weather.py - מזג אוויר נוכחי + השעות הקרובות לנקודה (Open-Meteo), כ*הקשר* למסלול ולמודיעין רחוב.

מקור: https://api.open-meteo.com/v1/forecast - ללא מפתח וללא הרשמה.
תנאים (נבדקו 2026-11-11, https://open-meteo.com/en/terms + https://open-meteo.com/en/licence):
- ה-API החינמי לשימוש לא-מסחרי בלבד; פחות מ-10,000 קריאות ביום, 5,000 בשעה, 600 בדקה (לכל IP/אפליקציה).
- הנתונים ברישיון CC BY 4.0; חובה להציג קישור ליד כל מקום שמוצגים בו: "Weather data by Open-Meteo.com".
  -> לכל תוצאה יש שדות source / attribution / fetched_at / valid_at, והממשק מציג אותם.

החלטה: **הקשר בלבד (display-only), בלי נקודות סיכון.** הסיבות:
1. כל החלופות בין אותן שתי נקודות נמצאות באותו תא מזג אוויר (~1 ק"מ, וגם רשת המודל עצמה גסה יותר) -
   תוספת נקודות הייתה זהה לכל החלופות, כך שלא הייתה משנה אף בחירת מסלול (מהיר/בטוח/מאוזן), רק מנפחת
   את רמת הסיכון המוצגת.
2. אין לנו כיול מקומי שמתרגם "גשם X מ"מ/שעה" לסיכון אישי (תקיפה/הטרדה) או לסיכון תאונה של הולך רגל
   לכל ק"מ. משקל היה המצאה - ובפרויקט הזה לא ממציאים השפעות.
3. מה שכן ידוע ובטוח לומר: גשם כבד -> משטחים חלקים; ערפל/ראות < 1 ק"מ -> ראות נמוכה; משבים -> קשה ללכת.
   זה מוצג כטקסט מתויג "הקשר - לא נכלל בניקוד".

ספים (תקניים, לא מומצאים):
- גשם כבד: קוד WMO 65/82, או ≥ 7.6 מ"מ/שעה (הגדרת NWS/AMS ל-heavy rain: 0.30 אינץ'/שעה).
  גשם בינוני: קוד 63/81 או ≥ 2.5 מ"מ/שעה (AMS: moderate 2.6-7.6). קל: קודים 51-61/80 או > 0.
- סופת רעמים: קודים 95/96/99. שלג/קרח/גשם קופא: 56/57/66/67/71-77/85/86.
- ערפל: קודים 45/48, או ראות < 1,000 מ' (הגדרת WMO לערפל). הראות של Open-Meteo היא אומדן מודל.
- משבים: ≥ 62 קמ"ש (בופור 8, gale), ≥ 50 קמ"ש (בופור 7).
- חום: ≥ 35°C (מוצג כהקשר בלבד).

חסינות: timeout קצר, מטמון 12 דק' לכל תא ~1 ק"מ, מטמון שלילי 60 שנ' אחרי כשל, ו-get_nowait() שמחכה לכל
היותר WAIT_S ואז ממשיך בלי מזג אוויר (ההבאה נמשכת ברקע וממלאת את המטמון לבקשה הבאה). כשל = "לא זמין",
לעולם לא חוסם את בדיקת המסלול.
"""

import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request

URL = "https://api.open-meteo.com/v1/forecast"
TERMS_URL = "https://open-meteo.com/en/terms"
TERMS_CHECKED = "2026-11-11"
SOURCE = "Open-Meteo"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0) - https://open-meteo.com/"
LICENSE = "CC BY 4.0"
USER_AGENT = "LifeGuard-Server/1.0 (non-commercial personal-safety app)"
CELL_DEG = 0.01               # ~1.1 ק"מ צפון-דרום, ~0.95 ק"מ מזרח-מערב בקו רוחב 32
TTL_S = 12 * 60
FAIL_TTL_S = 60
TIMEOUT_S = 3.0
WAIT_S = 1.5                  # כמה route_safety מוכן לחכות לפני שהוא ממשיך בלי מזג אוויר
FORECAST_HOURS = 12
WINDOW_BEFORE_S = 3600        # "עכשיו" = עד שעה אחורה
MAX_CELLS = 512
AFFECTS_SCORE = False         # ראה ההחלטה בראש הקובץ
LABEL_HE = "הקשר בלבד - לא נכלל בניקוד הסיכון"

CURRENT_VARS = ("temperature_2m", "precipitation", "rain", "showers", "snowfall", "weather_code",
                "wind_gusts_10m", "is_day", "visibility")
HOURLY_VARS = ("temperature_2m", "precipitation", "precipitation_probability", "weather_code", "visibility",
               "wind_gusts_10m", "is_day")

HEAVY_RAIN_MM_H = 7.6
MODERATE_RAIN_MM_H = 2.5
FOG_VISIBILITY_M = 1000
GALE_GUST_KMH = 62
STRONG_GUST_KMH = 50
HEAT_C = 35

WMO_HE = {0: "בהיר", 1: "בהיר בעיקר", 2: "מעונן חלקית", 3: "מעונן", 45: "ערפל", 48: "ערפל עם כפור",
          51: "טפטוף קל", 53: "טפטוף", 55: "טפטוף חזק", 56: "טפטוף קופא", 57: "טפטוף קופא חזק",
          61: "גשם קל", 63: "גשם", 65: "גשם כבד", 66: "גשם קופא", 67: "גשם קופא כבד",
          71: "שלג קל", 73: "שלג", 75: "שלג כבד", 77: "גרגרי שלג", 80: "ממטרים קלים", 81: "ממטרים",
          82: "ממטרים עזים", 85: "ממטרי שלג", 86: "ממטרי שלג כבדים", 95: "סופת רעמים",
          96: "סופת רעמים עם ברד", 99: "סופת רעמים עם ברד כבד"}


def enabled_from_env():
    return os.environ.get("LIFEGUARD_WEATHER", "1").strip().lower() not in ("0", "off", "false", "no")


def cell_of(lat, lon):
    return int(math.floor(lat / CELL_DEG)), int(math.floor(lon / CELL_DEG))


def _http_fetch(lat, lon, timeout):
    q = {"latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}", "current": ",".join(CURRENT_VARS),
         "hourly": ",".join(HOURLY_VARS), "forecast_hours": FORECAST_HOURS, "timezone": "GMT",
         "timeformat": "unixtime", "wind_speed_unit": "kmh"}
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(500_000).decode("utf-8"))


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def hazards(c):
    """תנאים -> [{kind, severity, text}] לפי הספים התקניים בראש הקובץ. בלי ספים = בלי טקסט."""
    out = []
    code = c.get("weather_code")
    code = int(code) if isinstance(code, (int, float)) else None
    rain = _num(c.get("precipitation"))
    vis = _num(c.get("visibility"))
    gust = _num(c.get("wind_gusts_10m"))
    temp = _num(c.get("temperature_2m"))
    rain_txt = f" ({rain:g} מ\"מ/שעה)" if rain else ""
    if code in (95, 96, 99):
        out.append({"kind": "thunderstorm", "severity": "high",
                    "text": "סופת רעמים - כדאי להתעכב במקום מקורה" + rain_txt})
    if code in (65, 82) or (rain is not None and rain >= HEAVY_RAIN_MM_H):
        out.append({"kind": "heavy_rain", "severity": "high", "text": "גשם כבד" + rain_txt + " - משטחים חלקים"})
    elif code in (63, 81) or (rain is not None and rain >= MODERATE_RAIN_MM_H):
        out.append({"kind": "rain", "severity": "medium", "text": "גשם" + rain_txt + " - משטחים רטובים"})
    elif (code is not None and 51 <= code <= 61) or code == 80 or (rain is not None and rain > 0):
        out.append({"kind": "light_rain", "severity": "low", "text": "גשם קל / טפטוף" + rain_txt})
    if code in (56, 57, 66, 67, 71, 73, 75, 77, 85, 86):
        out.append({"kind": "ice_snow", "severity": "high", "text": WMO_HE.get(code, "שלג/קרח") + " - סכנת החלקה"})
    if code in (45, 48) or (vis is not None and vis < FOG_VISIBILITY_M):
        out.append({"kind": "low_visibility", "severity": "medium",
                    "text": "ראות נמוכה" + (f" (~{round(vis / 100) * 100:.0f} מ', אומדן מודל)" if vis is not None
                                           else " (ערפל)")})
    if gust is not None and gust >= GALE_GUST_KMH:
        out.append({"kind": "gale", "severity": "high", "text": f"משבי רוח חזקים מאוד ({gust:.0f} קמ\"ש)"})
    elif gust is not None and gust >= STRONG_GUST_KMH:
        out.append({"kind": "strong_wind", "severity": "medium", "text": f"משבי רוח חזקים ({gust:.0f} קמ\"ש)"})
    if temp is not None and temp >= HEAT_C:
        out.append({"kind": "heat", "severity": "medium", "text": f"חום כבד ({temp:.0f}°C)"})
    return out


def _slot(data, at):
    """התנאים שתקפים ב-at: current אם at "עכשיו", אחרת השעה המתאימה מהתחזית. None = מחוץ לטווח."""
    cur = data.get("current") or {}
    t_cur = _num(cur.get("time"))
    if t_cur is not None and abs(at - t_cur) <= max(WINDOW_BEFORE_S, (_num(cur.get("interval")) or 900)):
        return dict(cur, _kind="current", _time=t_cur)
    h = data.get("hourly") or {}
    times = h.get("time") or []
    for i, t in enumerate(times):
        if isinstance(t, (int, float)) and t <= at < t + 3600:
            return dict({k: (h.get(k) or [None] * len(times))[i] for k in HOURLY_VARS}, _kind="hourly", _time=t)
    return None


class Weather:
    """מטמון לפי תא ~1 ק"מ, single-flight לכל תא, מטמון שלילי אחרי כשל. fetch(lat, lon, timeout) ניתן להחלפה."""

    def __init__(self, fetch=None, enabled=True, ttl=TTL_S, timeout=TIMEOUT_S, clock=time.time):
        self.fetch = fetch or _http_fetch
        self.enabled = enabled
        self.ttl = ttl
        self.timeout = timeout
        self.clock = clock
        self._cache = {}              # cell -> (expires_at, payload or None, fetched_at, error)
        self._inflight = {}           # cell -> threading.Event
        self._lock = threading.Lock()
        self.calls = 0

    @classmethod
    def from_env(cls):
        return cls(enabled=enabled_from_env())

    def applicable(self, at, now=None):
        now = self.clock() if now is None else now
        return now - WINDOW_BEFORE_S <= at <= now + (FORECAST_HOURS - 1) * 3600

    def _cached(self, cell):
        hit = self._cache.get(cell)
        if hit and hit[0] > self.clock():
            return hit
        return None

    def _do_fetch(self, cell, lat, lon, ev):
        payload, err = None, None
        try:
            self.calls += 1
            payload = self.fetch(lat, lon, self.timeout)
            if not isinstance(payload, dict) or not (payload.get("current") or payload.get("hourly")):
                payload, err = None, "unexpected_response"
        except Exception as exc:  # noqa: BLE001 - מזג אוויר הוא הקשר, לא תנאי
            err = type(exc).__name__ + ": " + str(exc)[:120]
        now = self.clock()
        with self._lock:
            self._cache[cell] = (now + (self.ttl if payload else FAIL_TTL_S), payload, now, err)
            if len(self._cache) > MAX_CELLS:
                for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:len(self._cache) - MAX_CELLS]:
                    self._cache.pop(k, None)
            self._inflight.pop(cell, None)
        ev.set()

    def get(self, lat, lon, at=None, wait=WAIT_S):
        """-> dict עם status: ok / unavailable / pending / disabled / not_applicable. לעולם לא זורק."""
        try:
            return self._get(lat, lon, at, wait)
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "reason": str(exc)[:120], "source": SOURCE, "attribution": ATTRIBUTION}

    def _get(self, lat, lon, at, wait):
        base = {"source": SOURCE, "attribution": ATTRIBUTION, "license": LICENSE, "terms_checked": TERMS_CHECKED,
                "affects_score": AFFECTS_SCORE, "label": LABEL_HE}
        if not self.enabled:
            return dict(base, status="disabled", reason="שכבת מזג האוויר לא מופעלת בשרת הזה")
        now = self.clock()
        at = float(at) if at else now
        if not self.applicable(at, now):
            return dict(base, status="not_applicable",
                        reason="מזג האוויר רלוונטי רק למסלול עכשיו או ב-11 השעות הקרובות")
        cell = cell_of(lat, lon)
        with self._lock:
            hit = self._cached(cell)
            ev = None
            if hit is None:
                ev = self._inflight.get(cell)
                if ev is None:
                    ev = self._inflight[cell] = threading.Event()
                    clat, clon = (cell[0] + .5) * CELL_DEG, (cell[1] + .5) * CELL_DEG
                    threading.Thread(target=self._do_fetch, args=(cell, clat, clon, ev), daemon=True).start()
        if hit is None:
            if not ev.wait(max(0.0, wait)):
                return dict(base, status="pending", reason="שירות מזג האוויר לא ענה מספיק מהר - ייתכן בבדיקה הבאה")
            with self._lock:
                hit = self._cache.get(cell)
        if not hit or hit[1] is None:
            return dict(base, status="unavailable", reason="שירות מזג האוויר לא זמין כרגע",
                        error=hit[3] if hit else None)
        _, data, fetched_at, _ = hit
        slot = _slot(data, at)
        if slot is None:
            return dict(base, status="not_applicable", reason="אין תחזית לשעה הזו")
        cond = {k: slot.get(k) for k in CURRENT_VARS + ("precipitation_probability",) if slot.get(k) is not None}
        hz = hazards(cond)
        code = cond.get("weather_code")
        summary = WMO_HE.get(int(code), f"קוד {code}") if isinstance(code, (int, float)) else None
        if hz:
            text = " · ".join(h["text"] for h in hz)
        else:
            text = (summary or "אין תנאים חריגים") + (f", {cond['temperature_2m']:.0f}°C"
                                                      if _num(cond.get("temperature_2m")) is not None else "")
        when = "עכשיו" if slot["_kind"] == "current" else "בשעה הרלוונטית (תחזית)"
        next_h = []
        h = data.get("hourly") or {}
        for i, t in enumerate(h.get("time") or []):
            if isinstance(t, (int, float)) and t >= now - 3600 and len(next_h) < 6:
                row = {k: (h.get(k) or [None] * (i + 1))[i] for k in HOURLY_VARS}
                row["time"] = t
                row["hazards"] = [x["kind"] for x in hazards(row)]
                next_h.append(row)
        return dict(base, status="ok", fetched_at=fetched_at, valid_at=slot["_time"], kind=slot["_kind"],
                    model_point={"lat": data.get("latitude"), "lon": data.get("longitude")},
                    conditions=cond, summary=summary, hazards=hz, next_hours=next_h,
                    text=f"{when}: {text}")
