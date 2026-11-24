# -*- coding: utf-8 -*-
"""
dataset_discovery.py - איתור אוטומטי של מאגרי קול פתוחים ב-Zenodo (API ציבורי, בלי הרשמה) לפי מילות מפתח של
סכנה ושל "דומים לא מסוכנים", עם סינון רישוי.

- רק רשומות בגישה פתוחה מסוג dataset. לכל רשומה: רישיון, גודל, קבצים, האם מותר לאימון מסחרי (CC0 / CC BY),
  או הערכה בלבד (NC / SA / לא ידוע).
- לא מוריד כלום - רק רשימת מועמדים: research/dataset_candidates.md + research_data/manifests/dataset_candidates.json.
- User-Agent גנרי; בלי פרטים מזהים.

שימוש: python -I research_tools/dataset_discovery.py
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_JSON = os.path.join(ROOT, "research_data", "manifests", "dataset_candidates.json")
OUT_MD = os.path.join(ROOT, "research", "dataset_candidates.md")
UA = "LifeGuard-Research/1.0"
QUERIES = {
    "scream": "scream OR screaming OR shout OR shouting", "distress_speech": "\"emotional speech\" OR \"speech emotion\"",
    "gunshot": "gunshot OR gunfire OR firearm", "explosion_fireworks": "explosion OR fireworks OR firecracker",
    "glass": "\"glass break\" OR \"glass breaking\"", "siren_alarm": "siren OR \"car alarm\" OR \"smoke alarm\"",
    "horn_traffic": "\"car horn\" OR \"traffic noise\" OR \"urban sound\"", "sound_events": "\"sound event detection\"",
    "domestic": "\"domestic sounds\" OR \"household sounds\" OR \"indoor sound\"", "crying": "\"baby cry\" OR \"infant cry\" OR crying",
    "violence": "\"violence detection\" audio", "surveillance": "\"audio surveillance\" OR \"acoustic surveillance\"",
    "nonverbal": "\"nonverbal vocalization\" OR \"non-speech\" OR \"vocal sounds\"", "noise": "\"environmental sound\" OR \"acoustic scene\"",
    "music_drums": "drums OR percussion dataset", "construction": "\"construction noise\" OR jackhammer",
}
COMMERCIAL = ("cc0-1.0", "cc-by-4.0", "cc-by-3.0", "cc-by-1.0", "cc-by-2.0", "mit", "apache-2.0", "cc-zero")
AUDIO_EXT = (".wav", ".flac", ".mp3", ".ogg", ".zip", ".tar", ".gz", ".7z", ".tgz", ".h5", ".hdf5")
MIN_MB, MAX_MB = 5, 60_000


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def search(q, pages=4):
    out = []
    for page in range(1, pages + 1):
        url = "https://zenodo.org/api/records?" + urllib.parse.urlencode(
            {"q": f"({q}) AND (audio OR sound OR acoustic)", "type": "dataset", "access_right": "open",
             "size": 25, "page": page, "sort": "mostviewed"})
        try:
            hits = _get(url).get("hits", {}).get("hits", [])
        except Exception as exc:  # noqa: BLE001
            print(f"  {q[:30]} עמוד {page}: {exc}", flush=True)
            break
        out += hits
        if len(hits) < 25:
            break
        time.sleep(1.2)                       # מגבלת קצב של Zenodo לגישה אנונימית
    return out


def run():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    known = {7004819, 6836032, 3966543, 2590741, 3819968, 6967442, 1203745, 4060432}
    seen = {}
    for tag, q in QUERIES.items():
        hits = search(q)
        print(f"{tag}: {len(hits)}", flush=True)
        for h in hits:
            rid = h["id"]
            m = h.get("metadata", {})
            files = h.get("files") or []
            size = sum(f.get("size", 0) for f in files) / 1e6
            if not any(str(f.get("key", "")).lower().endswith(AUDIO_EXT) for f in files):
                continue
            if not MIN_MB <= size <= MAX_MB:
                continue
            lic = ((m.get("license") or {}).get("id") or "unknown").lower()
            e = seen.setdefault(rid, {"id": rid, "title": m.get("title", "")[:140], "license": lic,
                                      "commercial_ok": lic in COMMERCIAL, "size_mb": round(size),
                                      "files": len(files), "doi": h.get("doi"), "url": f"https://zenodo.org/records/{rid}",
                                      "tags": [], "views": (h.get("stats") or {}).get("unique_views"),
                                      "already_used": rid in known, "date": m.get("publication_date")})
            e["tags"].append(tag)
    rows = sorted(seen.values(), key=lambda e: (-len(e["tags"]), -(e["views"] or 0)))
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    ok = [r for r in rows if r["commercial_ok"]]
    L = ["# מועמדים למאגרי קול נוספים (Zenodo, גישה פתוחה)", "",
         f"נוצר ע\"י `research_tools/dataset_discovery.py` ({time.strftime('%Y-%m-%d')}). {len(rows)} רשומות עם קובצי אודיו "
         f"({MIN_MB} MB–{MAX_MB // 1000} GB); {len(ok)} ברישיון שמתיר אימון מסחרי (CC0 / CC BY). "
         "**הרישיון כאן הוא מה שרשום ב-Zenodo - צריך לאמת מול ה-README של כל מאגר לפני שימוש.**", "",
         "| מאגר | רישיון | אימון מסחרי | GB | נושאים | צפיות | קישור |", "|---|---|---|---|---|---|---|"]
    for r in rows[:150]:
        L.append(f"| {r['title'].replace('|', '/')}{' (בשימוש)' if r['already_used'] else ''} | {r['license']} | "
                 f"{'כן' if r['commercial_ok'] else 'לא'} | {r['size_mb'] / 1000:.1f} | {', '.join(r['tags'])} | "
                 f"{r['views'] or ''} | {r['url']} |")
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"סה\"כ {len(rows)} מועמדים, {len(ok)} מותרים לאימון מסחרי; {sum(r['size_mb'] for r in ok) / 1000:.0f} GB", flush=True)


if __name__ == "__main__":
    run()
