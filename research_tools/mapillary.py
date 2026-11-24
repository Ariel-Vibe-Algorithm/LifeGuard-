# -*- coding: utf-8 -*-
"""
mapillary.py - תמונות רחוב מ-Mapillary לסיווג רחובות (במקום Google Street View).

למה לא Google Street View: תנאי Google Maps Platform אוסרים במפורש להוריד תמונות Street View בכמות,
לשמור אותן, ו"להשתמש בתוכן Google Maps כדי לשפר מודלי למידת מכונה, כולל לאמן, לבדוק, לתקף או
לכוונן אותם" (ToS §3.2.3, נבדק 2026-11-06). אין חריג אקדמי. לכן אין כאן שום גישה ל-Google.

Mapillary: תמונות משתמשים ברישיון CC BY-SA 4.0, פנים ולוחיות כבר מטושטשות ע"י Mapillary, והתנאים
מתירים במפורש אימון ופיתוח אלגוריתמים - בתנאי שהגישה דרך ה-API הרשמי (עם token), בלי scraping, בלי
ניסיון לבטל טשטוש/לזהות אנשים, ועם ייחוס.

מה המודול עושה:
- שאילתה רשמית ל-Graph API v4 לפי תיבת גבול (bbox), עם token מ-MAPILLARY_TOKEN (לעולם לא בקוד/במאגר).
- הורדת התמונה הממוזערת לזיכרון בלבד -> אותו ניתוח כמו בסרטון ההליכה -> תצפיות מספריות.
- התמונות עצמן לא נשמרות. נשמר קובץ ייחוס (מזהה תמונה, יוצר, רישיון) לכל תצפית.
- קצב מתון ומגבלת כמות. (מגבלות Mapillary: 10,000 חיפושים/דקה לאפליקציה - אנחנו הרבה מתחת.)
הערה: נתונים נגזרים מתמונות CC BY-SA עשויים לחייב שיתוף באותו רישיון אם מפיצים אותם.
"""

import json
import os
import time
import urllib.parse
import urllib.request

import numpy as np

API = "https://graph.mapillary.com"
LICENSE = "CC BY-SA 4.0"
FIELDS = "id,captured_at,computed_geometry,thumb_1024_url,creator,is_pano"
UA = "LifeGuard-Research/1.0"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


IMAGE_HOST_SUFFIXES = (".fbcdn.net", ".mapillary.com")   # שרתי התמונות של Mapillary (CDN של Meta)


class MapillaryError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None   # הפניה -> שגיאה (HTTPError), לא בקשה חדשה


def image_host_allowed(url, extra_hosts=()):
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    if (u.scheme, host) in {("http", h) for h in extra_hosts}:
        return True                       # שרת בדיקה מקומי (מוגדר במפורש)
    return u.scheme == "https" and any(host.endswith(sfx) for sfx in IMAGE_HOST_SUFFIXES)


class Mapillary:
    def __init__(self, token=None, base_url=API, delay_s=0.2, timeout=30, image_hosts=()):
        self.token = token or os.environ.get("MAPILLARY_TOKEN")
        if not self.token:
            raise MapillaryError("חסר MAPILLARY_TOKEN. מקבלים token חינמי ברישום אפליקציה ב-"
                                 "https://www.mapillary.com/dashboard/developers")
        self.base, self.delay, self.timeout = base_url.rstrip("/"), delay_s, timeout
        self.image_hosts = tuple(image_hosts)

    def _get(self, url, binary=False, auth=True):
        """auth=False לתמונות: ה-URL כבר חתום. ה-token לא נשלח לשרתי התמונות, ואין מעקב אחרי הפניות
        (redirect) - אחרת ה-token דולף ליעד ההפניה (נמצא בסקירת אבטחה)."""
        headers = {"User-Agent": UA}
        if auth:
            headers["Authorization"] = f"OAuth {self.token}"
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(urllib.request.Request(url, headers=headers), timeout=self.timeout) as r:
            data = r.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise MapillaryError("תגובה גדולה מדי")
        time.sleep(self.delay)
        return data if binary else json.loads(data.decode("utf-8"))

    def search(self, bbox, limit=100):
        """bbox = (min_lon, min_lat, max_lon, max_lat). מחזיר רשומות תמונה (בלי פנורמות)."""
        min_lon, min_lat, max_lon, max_lat = bbox
        if not (min_lon < max_lon and min_lat < max_lat) or (max_lon - min_lon) * (max_lat - min_lat) > 0.01:
            raise MapillaryError("bbox לא תקין או גדול מדי (עד ~0.1°x0.1°)")
        q = urllib.parse.urlencode({"fields": FIELDS, "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
                                    "limit": int(min(limit, 2000))})
        rows = self._get(f"{self.base}/images?{q}").get("data", [])
        return [r for r in rows if not r.get("is_pano") and r.get("thumb_1024_url") and r.get("computed_geometry")]

    def image(self, row):
        """התמונה לזיכרון (BGR). לא נכתבת לדיסק."""
        import cv2
        url = row["thumb_1024_url"]
        if not image_host_allowed(url, self.image_hosts):
            raise MapillaryError("כתובת תמונה לא צפויה")
        buf = np.frombuffer(self._get(url, binary=True, auth=False), dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            raise MapillaryError("תמונה לא תקינה")
        return img


def collect(bbox, analyzer, client=None, limit=100, log=print):
    """bbox -> (תצפיות מספריות, ייחוס לכל תמונה). שום תמונה לא נשמרת."""
    client = client or Mapillary()
    rows = client.search(bbox, limit)
    log(f"נמצאו {len(rows)} תמונות ב-Mapillary")
    obs, attribution = [], []
    for r in rows:
        try:
            img = client.image(r)
        except Exception as exc:  # noqa: BLE001 - תמונה אחת שנכשלה לא עוצרת את כל האיסוף
            log(f"דילוג על {r.get('id')}: {exc}")
            continue
        emb, zs, lum, faces = analyzer.analyze_frames([img])
        lon, lat = r["computed_geometry"]["coordinates"][:2]
        creator = (r.get("creator") or {}).get("username")
        cap = r.get("captured_at")
        obs.append({"walk_id": "mapillary", "source": "mapillary", "source_ref": str(r["id"]),
                    "observed_at": cap / 1000.0 if isinstance(cap, (int, float)) else None,
                    "lat": float(lat), "lon": float(lon),
                    "attributes": {k: round(float(v[0]), 4) for k, v in zs.items()},
                    "luminance": round(float(lum[0][0]), 4), "dark_fraction": round(float(lum[0][1]), 4),
                    "faces_blurred": int(faces[0]), "embedding": emb[0].astype(np.float16)})
        attribution.append({"image_id": str(r["id"]), "creator": creator, "license": LICENSE,
                            "url": f"https://www.mapillary.com/app/?pKey={r['id']}"})
    return obs, attribution


def main(argv=None):
    import argparse
    from research_tools import street_vision as S
    ap = argparse.ArgumentParser(description="Mapillary street imagery -> LifeGuard street observations")
    ap.add_argument("--bbox", required=True, help="min_lon,min_lat,max_lon,max_lat")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--out", default=os.path.join("research", "samples", "mapillary", "observations.jsonl"))
    a = ap.parse_args(argv)
    bbox = tuple(float(x) for x in a.bbox.split(","))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    obs, attr = collect(bbox, S.StreetAnalyzer(), limit=a.limit)
    S.save_observations(obs, a.out)
    with open(os.path.splitext(a.out)[0] + "_attribution.json", "w", encoding="utf-8") as fh:
        json.dump({"license": LICENSE, "source": "Mapillary", "images": attr}, fh, ensure_ascii=False, indent=1)
    print(f"{len(obs)} תצפיות נשמרו ב-{a.out} (ללא תמונות), ייחוס ב-_attribution.json")


if __name__ == "__main__":
    main()
