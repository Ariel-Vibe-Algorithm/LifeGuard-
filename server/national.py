# -*- coding: utf-8 -*-
"""
national.py - מצב חירום לאומי: התרעות רשמיות, הנחיות, מקלטים מאומתים, תיאום מתנדבים.

**התרעות פיקוד העורף - מה כן ומה לא:**
- אין לנו הרשאה רשמית/הסכם API עם פיקוד העורף. לכן האינטגרציה **כבויה כברירת מחדל**.
- מנהל/ת שרת שקיבל/ה אישור (או מפעיל/ה מראה מורשית) מגדיר/ה LIFEGUARD_OREF_ALERTS_URL - כתובת
  שמחזירה את פורמט ה-JSON של התרעות פיקוד העורף ({"id","cat","title","data":[אזורים],"desc"}).
  כל התרעה מוצגת עם המקור שלה ועם ההערה "האפליקציה הרשמית של פיקוד העורף היא המקור המחייב".
- בלי הגדרה: "התרעות לא מחוברות בשרת הזה" - ולא שום דבר מדומה.
- LifeGuard **לא** מחליף את אפליקציית פיקוד העורף / צופר / 104. ההתרעה ממנה תמיד מהירה ומחייבת.

**Siren (siren.co.il) - מקור משלים בלבד:** ממשק קהילתי ללא-מטרות-רווח שמעביר את התרעות פיקוד העורף
(נבדק 2026-11-11: api.siren.co.il קיים, GET /active דורש מפתח - 401 "Missing API key" בלעדיו; תנאים:
https://siren.co.il/terms, מרץ 2026). כבוי בלי LIFEGUARD_SIREN_KEY (או LIFEGUARD_SIREN_KEY_FILE).
- התנאים עצמם (סעיף 3) אוסרים להסתמך עליו כמקור יחיד/עיקרי, והוא לא מאושר ע"י פיקוד העורף (סעיף 2).
- לכן: ההתרעה הרשמית מחושבת קודם ולעולם לא משתנה/נמחקת בגלל Siren - גם כש-Siren ריק, שונה, "הסתיים",
  או לא זמין. תוצאת Siren מוצגת בנפרד ("supplementary") עם שם המקור, "לא רשמי", וקישור חובה (סעיף 9).
- רק מפתח פרטי (pr_) בשרת; מפתח ציבורי (pb_) אסור בצד שרת (סעיף 5) - מסורב. תרגילים (...Drill) לא מוצגים.

**מקלטים:** רק ממאגרים עירוניים רשמיים (safety_intel.Layers): ת"א, ב"ש, חיפה. לכל מקלט - המקור.
**מצב לאומי (מנהל/ת):** הפעלה ידנית עם הודעה ואזורים → כרטיס בכל האפליקציות + התראה למתנדבים זמינים.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

from server import data_sources as DS

ALERT_CACHE_S = 5
INSTRUCTIONS = {
    "missiles": ("ירי רקטות וטילים",
                 ["היכנסו מיד למרחב מוגן: ממ\"ד, ממ\"ק, מקלט, או חדר מדרגות פנימי",
                  "בחוץ בלי מרחב מוגן: שכבו על הקרקע והגנו על הראש בידיים",
                  "הישארו במרחב המוגן עד להודעה של פיקוד העורף על סיום האירוע"]),
    "aircraft": ("חדירת כלי טיס עוין",
                 ["היכנסו למרחב מוגן וסגרו דלתות וחלונות",
                  "הישארו בו עד להודעה של פיקוד העורף על סיום האירוע"]),
    "infiltration": ("חדירת מחבלים",
                     ["היכנסו לבית או למבנה סגור ונעלו את הדלת", "התרחקו מחלונות ודלתות",
                      "השאירו את הטלפון על שקט ופעלו לפי הנחיות כוחות הביטחון"]),
    "earthquake": ("רעידת אדמה",
                   ["צאו לשטח פתוח אם אפשר לעשות זאת מהר", "אם לא - היכנסו לממ\"ד או לחדר מדרגות",
                    "אם אין אפשרות - התכופפו מתחת לרהיט כבד והחזיקו בו"]),
    "tsunami": ("צונאמי", ["התרחקו מהחוף מיד", "עלו לקומה גבוהה או לשטח גבוה"]),
    "hazmat": ("אירוע חומרים מסוכנים", ["היכנסו למבנה וסגרו חלונות ודלתות", "כבו מזגנים ומערכות אוורור"]),
    "general": ("התרעה", ["פעלו לפי הנחיות פיקוד העורף באפליקציה הרשמית או בטלפון 104"]),
}
NUMBERS = [{"name": "משטרה", "number": "100"}, {"name": "מד\"א", "number": "101"},
           {"name": "כיבוי והצלה", "number": "102"}, {"name": "מוקד פיקוד העורף", "number": "104"}]
OFFICIAL_NOTE = "המקור המחייב: אפליקציית פיקוד העורף / אתר oref.org.il / 104. LifeGuard הוא כלי עזר בלבד."
OFFICIAL_SOURCE = "פיקוד העורף (דרך שרת LifeGuard)"

SIREN_URL = "https://api.siren.co.il/active"
SIREN_CACHE_S = 5
SIREN_SOURCE = "Siren (siren.co.il) - מקור קהילתי, לא רשמי"
SIREN_LINK = "https://siren.co.il"
SIREN_NOTE = ("מקור משלים בלבד: Siren הוא פרויקט קהילתי שאינו מאושר ע\"י פיקוד העורף. "
              "ההתרעה הרשמית (אפליקציית פיקוד העורף / 104) היא המחייבת, תמיד.")
# סוגי Siren (docs.siren.co.il/api/alerts) -> הסוגים שלנו. Drill -> לא מוצג. לא מוכר -> general (מוצג, מסומן).
SIREN_KINDS = {"missiles": "missiles", "hostileAircraftIntrusion": "aircraft",
               "terroristInfiltration": "infiltration", "earthQuake": "earthquake", "tsunami": "tsunami",
               "hazardousMaterials": "hazmat", "radiologicalEvent": "hazmat", "general": "general",
               "newsFlash": "early_warning", "endAlert": "end"}
SIREN_TEXT = {"early_warning": "התרעה מקדימה (לפי Siren) - היו מוכנים להיכנס למרחב מוגן",
              "end": "לפי Siren האירוע הסתיים - המתינו להודעת פיקוד העורף לפני יציאה מהמרחב המוגן"}

MIGRATION = """
CREATE TABLE national_prefs(username TEXT PRIMARY KEY, areas TEXT NOT NULL DEFAULT '[]', updated_at REAL NOT NULL);
"""


def classify(title, cat=None):
    t = str(title or "")
    if "רקט" in t or "טיל" in t:
        return "missiles"
    if "כלי טיס" in t:
        return "aircraft"
    if "מחבל" in t:
        return "infiltration"
    if "רעידת" in t:
        return "earthquake"
    if "צונאמי" in t:
        return "tsunami"
    if "חומרים מסוכנים" in t:
        return "hazmat"
    return "general"


def area_matches(user_area, alert_area):
    """"תל אביב" תואם ל-"תל אביב - מרכז העיר"; התאמה מדויקת או לפי השם לפני " - "."""
    u, a = str(user_area).strip(), str(alert_area).strip()
    return bool(u) and (u == a or a.split(" - ")[0].strip() == u or u.split(" - ")[0].strip() == a)


def parse_alert(body):
    """פורמט ה-JSON של פיקוד העורף. גוף ריק = אין התרעה."""
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8-sig", "replace")
    body = (body or "").strip()
    if not body:
        return None
    d = json.loads(body)
    if not isinstance(d, dict) or not d.get("data"):
        return None
    areas = [str(a)[:80] for a in d.get("data") or [] if a][:500]
    kind = classify(d.get("title"), d.get("cat"))
    return {"id": str(d.get("id") or "")[:40], "title": str(d.get("title") or "")[:120], "areas": areas,
            "desc": str(d.get("desc") or "")[:300], "kind": kind}


def parse_siren_active(body):
    """GET /active של Siren: {"missiles": ["תל אביב - יפו", ...], ...}; {} = אין התרעות. תרגילים מסוננים."""
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8-sig", "replace")
    d = json.loads((body or "").strip() or "{}")
    if not isinstance(d, dict):
        raise ValueError("פורמט Siren לא צפוי")
    out = []
    for typ, cities in d.items():
        typ = str(typ)[:40]
        if typ.endswith("Drill") or not isinstance(cities, list):
            continue
        areas = [str(c)[:80] for c in cities if c][:500]
        if areas:
            out.append({"type": typ, "kind": SIREN_KINDS.get(typ, "general"), "areas": areas})
    return out


def _ds_check(svc, source_id, ok, detail):
    """מצב אחרון ל-/v1/data-sources. כישלון כאן לעולם לא פוגע בהתרעות."""
    try:
        DS.cache_for(svc).record_check(source_id, ok, detail)
    except Exception:  # noqa: BLE001
        pass


class NationalMixin:
    def _siren_fetch(self):
        key = DS.secret("LIFEGUARD_SIREN_KEY")
        if not key:
            return {"configured": False}
        if not key.startswith("pr_"):
            return {"configured": True, "ok": False,
                    "error": "נדרש מפתח פרטי (pr_) - מפתח ציבורי אסור בצד שרת לפי תנאי Siren"}
        cache = getattr(self, "_siren_cache", None)
        now = time.time()
        if cache and now - cache[0] < SIREN_CACHE_S:
            return cache[1]
        if now < getattr(self, "_siren_backoff_until", 0):
            return {"configured": True, "ok": False, "error": "rate_limited", "fetched_at": now}
        try:
            req = urllib.request.Request(SIREN_URL, headers={"User-Agent": "LifeGuard-Server/1.0",
                                                             "Accept": "application/json", "X-API-Key": key})
            with urllib.request.urlopen(req, timeout=3) as r:
                items = parse_siren_active(r.read(2_000_000))
            res = {"configured": True, "ok": True, "items": items, "fetched_at": time.time()}
            _ds_check(self, "siren", True, "HTTP 200")
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    wait = float(exc.headers.get("Retry-After") or 60)
                except (TypeError, ValueError, AttributeError):
                    wait = 60.0
                self._siren_backoff_until = time.time() + min(max(wait, 5.0), 900.0)
            res = {"configured": True, "ok": False, "error": f"HTTP {exc.code}", "fetched_at": time.time()}
            _ds_check(self, "siren", False, f"HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001 - שגיאה מוצגת, לא "אין התרעות"
            res = {"configured": True, "ok": False, "error": type(exc).__name__, "fetched_at": time.time()}
            _ds_check(self, "siren", False, type(exc).__name__)
        self._siren_cache = (time.time(), res)
        return res

    def _siren_block(self, feed, areas, official_current, official_ok):
        """בלוק משלים נפרד. לא נוגע בהתרעה הרשמית - רק מתאר את עצמו ואת ההתאמה/אי-ההתאמה."""
        rec = DS.record("siren", None, fetched_at=feed.get("fetched_at"))
        sup = {"source_id": "siren", "source": SIREN_SOURCE, "official": False, "attribution_url": SIREN_LINK,
               "note": SIREN_NOTE, "kind": rec["kind"], "kind_label": rec["kind_label"],
               "reliability": rec["reliability"], "configured": feed.get("configured", False)}
        if not feed.get("configured"):
            sup["status"] = "not_configured"
            return sup
        if not feed.get("ok"):
            sup["status"] = "error"
            sup["error"] = feed.get("error")
            return sup
        sup["status"] = "ok"
        sup["fetched_at"] = feed.get("fetched_at")
        cur = []
        for it in feed.get("items") or []:
            mine = [x for x in it["areas"] if any(area_matches(u, x) for u in areas)]
            entry = {"type": it["type"], "kind": it["kind"], "in_my_areas": bool(mine), "my_areas": mine,
                     "areas_count": len(it["areas"]), "source": SIREN_SOURCE, "official": False}
            if it["kind"] in INSTRUCTIONS:
                entry["title"] = INSTRUCTIONS[it["kind"]][0]
                entry["instructions"] = INSTRUCTIONS[it["kind"]][1]
            else:
                entry["title"] = SIREN_TEXT.get(it["kind"], "התרעה")
            cur.append(entry)
        sup["current"] = cur
        siren_mine = any(e["in_my_areas"] and e["kind"] != "end" for e in cur)
        if not official_ok:
            sup["agreement"] = "official_unavailable"
            if siren_mine:
                sup["text"] = ("Siren (מקור לא רשמי) מדווח על התרעה באזור שלך, והמקור הרשמי לא זמין בשרת הזה - "
                               "היכנסו למרחב מוגן ובדקו מיד באפליקציית פיקוד העורף")
        else:
            off_mine = bool(official_current and official_current.get("in_my_areas"))
            sup["agreement"] = {(True, True): "both", (True, False): "official_only",
                                (False, True): "siren_only", (False, False): "none"}[(off_mine, siren_mine)]
            if sup["agreement"] == "siren_only":
                sup["text"] = ("Siren (מקור לא רשמי) מדווח על התרעה באזור שלך שלא התקבלה מהמקור הרשמי - "
                               "בדקו מיד באפליקציית פיקוד העורף")
            elif sup["agreement"] == "official_only":
                sup["text"] = "יש התרעה רשמית באזור שלך. היעדר דיווח ב-Siren לא משנה אותה - פעלו לפי ההתרעה הרשמית"
        return sup

    def _oref_fetch(self):
        url = os.environ.get("LIFEGUARD_OREF_ALERTS_URL", "").strip()
        if not url:
            return {"configured": False}
        cache = getattr(self, "_oref_cache", None)
        if cache and time.time() - cache[0] < ALERT_CACHE_S:
            return cache[1]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LifeGuard-Server/1.0",
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                alert = parse_alert(r.read(2_000_000))
            res = {"configured": True, "ok": True, "alert": alert, "fetched_at": time.time()}
            _ds_check(self, "oref_alerts", True, "OK")
        except Exception as exc:  # noqa: BLE001 - שגיאה מוצגת, לא "אין התרעות"
            res = {"configured": True, "ok": False, "error": str(exc)[:200], "fetched_at": time.time()}
            _ds_check(self, "oref_alerts", False, type(exc).__name__)
        self._oref_cache = (time.time(), res)
        return res

    def set_national_areas(self, user, areas):
        areas = [str(a).strip()[:80] for a in (areas or []) if str(a).strip()][:10]
        self._x("INSERT INTO national_prefs(username, areas, updated_at) VALUES (?,?,?) ON CONFLICT(username) "
                "DO UPDATE SET areas=excluded.areas, updated_at=excluded.updated_at",
                (user, json.dumps(areas, ensure_ascii=False), time.time()))
        return {"areas": areas}

    def _national_areas(self, user):
        r = self._q("SELECT areas FROM national_prefs WHERE username=?", (user,), one=True)
        return json.loads(r["areas"]) if r else []

    def _national_mode(self):
        r = self._q("SELECT value FROM server_meta WHERE key='national_mode'", one=True)
        return json.loads(r["value"]) if r else None

    def admin_national_mode(self, active, title="", message="", areas=None):
        if active:
            mode = {"active": True, "title": str(title or "מצב חירום לאומי")[:120], "message": str(message or "")[:500],
                    "areas": [str(a)[:80] for a in (areas or [])][:50], "since": time.time()}
            self._x("INSERT INTO server_meta(key, value) VALUES ('national_mode', ?) ON CONFLICT(key) DO UPDATE "
                    "SET value=excluded.value", (json.dumps(mode, ensure_ascii=False),))
            # תיאום מתנדבים: כל מתנדב/ת זמין/ה מקבל/ת עדכון (בלי מיקום של אף אחד)
            for v in self._q("SELECT username FROM volunteers WHERE available=1 AND suspended=0"):
                self._notify(v["username"], "national_mode", {"active": True, "title": mode["title"],
                                                              "text": mode["message"]})
            return mode
        self._x("DELETE FROM server_meta WHERE key='national_mode'")
        return {"active": False}

    def national_status(self, user, lat=None, lon=None):
        areas = self._national_areas(user)
        # Siren במקביל (לא מעכב את הרשמי); תוצאתו לא משפיעה על חישוב ההתרעה הרשמית
        siren_box = {}
        th = threading.Thread(target=lambda: siren_box.setdefault("feed", self._siren_fetch()), daemon=True)
        th.start()
        feed = self._oref_fetch()
        alerts = {"configured": feed.get("configured", False), "official_note": OFFICIAL_NOTE,
                  "primary_source": "oref"}
        if not feed.get("configured"):
            alerts["status"] = "not_configured"
            alerts["text"] = "התרעות פיקוד העורף לא מחוברות בשרת הזה - הפעילו את אפליקציית פיקוד העורף"
        elif not feed.get("ok"):
            alerts["status"] = "error"
            alerts["text"] = "לא ניתן לקבל התרעות כרגע - הסתמכו על אפליקציית פיקוד העורף"
        else:
            a = feed.get("alert")
            alerts["status"] = "ok"
            alerts["source"] = OFFICIAL_SOURCE
            alerts["fetched_at"] = feed.get("fetched_at")
            if a:
                mine = [x for x in a["areas"] if any(area_matches(u, x) for u in areas)]
                title, steps = INSTRUCTIONS[a["kind"]]
                alerts["current"] = {"title": a["title"] or title, "kind": a["kind"], "desc": a["desc"],
                                     "in_my_areas": bool(mine), "my_areas": mine, "areas_count": len(a["areas"]),
                                     "instructions": steps, "source": OFFICIAL_SOURCE, "official": True}
            else:
                alerts["current"] = None
        th.join(3.5)
        alerts["supplementary"] = self._siren_block(siren_box.get("feed") or {"configured": True, "ok": False,
                                                                              "error": "timeout"},
                                                    areas, alerts.get("current"), bool(feed.get("ok")))
        out = {"national_mode": self._national_mode(), "alerts": alerts, "my_areas": areas, "numbers": NUMBERS,
               "instructions": {k: {"title": v[0], "steps": v[1]} for k, v in INSTRUCTIONS.items()},
               "shelters": [], "shelters_note": None}
        if lat is not None and lon is not None:
            lat, lon = self._valid_coords(lat, lon)
            layers = self._layers()
            if not layers.available:
                out["shelters_note"] = "מאגר המקלטים לא נטען בשרת הזה"
            else:
                near = layers.shelters_near(lat, lon, n=5, max_m=2000)
                for s in near:
                    src = layers.source(s["source"])
                    s["source_name"] = src.get("name", s["source"])
                    s["license_note"] = src.get("license")
                out["shelters"] = near
                cities = sorted({s.get("city") for s in layers.shelters})
                out["shelters_note"] = (None if near else "לא נמצא מקלט ציבורי במאגרים הרשמיים בטווח 2 ק\"מ. ") or None
                out["shelters_coverage"] = f"מאגרים עירוניים רשמיים: {', '.join(c for c in cities if c)}. " \
                                           "במקום אחר - אין לנו נתוני מקלטים; השתמשו בממ\"ד / חדר מדרגות."
        return out
