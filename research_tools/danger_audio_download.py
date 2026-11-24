# -*- coding: utf-8 -*-
"""
danger_audio_download.py - הורדה של הקלטות "סכנה" ורקע אמיתי ממאגרי מחקר פתוחים (אבן דרך 4).

עקרונות:
- רק מאגרים בגישה פתוחה, בלי הרשמה ובלי אישור תנאים בשם המשתמש/ת (Zenodo API, GitHub).
- כל קובץ מאומת מול ה-checksum שהמאגר מפרסם (md5 של Zenodo / sha256 שנרשם בהורדה הראשונה).
- הרישיון, הייחוס וגרסת הרשומה נרשמים ב-research_data/audio/MANIFEST.json. הנתונים לא נכנסים ל-git.
- הורדה ניתנת לחידוש (Range), User-Agent גנרי.
- שימוש: מחקר והערכה. מאגרים ב-NC (TAU, ESC-50) - הערכה בלבד, לא אימון של מודל שנשלח במוצר.

שימוש: python -I research_tools/danger_audio_download.py [שם ...]   (בלי שם = הכל)
"""
import hashlib
import json
import os
import sys
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = os.path.join(ROOT, "research_data", "audio")
MANIFEST = os.path.join(BASE, "MANIFEST.json")
UA = "LifeGuard-Research/1.0"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # -I מתעלם מ-PYTHONIOENCODING

SOURCES = {
    "gunshot_fit": {
        "zenodo": 7004819, "files": ["edge-collected-gunshot-audio.zip", "README.md"], "dir": "gunshot_fit",
        "name": "Gunshot/Gunfire Audio Dataset - multi-firearm, multi-orientation (Florida Tech CADAS / AFRL)",
        "attribution": "Kabealo et al., Data in Brief 2023 - A Multi-Firearm, Multi-Orientation Audio Dataset of Gunshots",
        "role": "חיובי: ירי אמיתי במטווח, מוקלט במכשירי קצה (כולל טלפונים); לא מ-Freesound"},
    "nonspeech7k": {
        "zenodo": 6967442, "files": ["test.zip", "metadata of test set.csv"], "dir": "nonspeech7k",
        "name": "Nonspeech7k (test split)",
        "attribution": "Rashid, Li, Du, IET Signal Processing 2023, doi:10.1049/sil2.12233",
        "role": "צרחה / בכי (חיובי) מול צחוק / שיעול / עיטוש (דומים קשים)"},
    "sonyc_ust": {
        "zenodo": 3966543, "files": ["annotations.csv", "README.md", "dcase-ust-taxonomy.yaml",
                                     "audio-0.tar.gz", "audio-1.tar.gz"], "dir": "sonyc_ust",
        "name": "SONYC Urban Sound Tagging v2.3 (subset: audio-0..1)",
        "attribution": "Cartwright et al., DCASE 2020 - SONYC-UST-V2",
        "role": "רחוב אמיתי (חיישני NYC): צעקות, סירנות, אזעקות רכב, ורקע יומיומי. לא מ-Freesound. בלי תמלול דיבור"},
    "tau2020_mobile": {
        "zenodo": 3819968, "files": ["TAU-urban-acoustic-scenes-2020-mobile-development.meta.zip",
                                     "TAU-urban-acoustic-scenes-2020-mobile-development.doc.zip",
                                     "TAU-urban-acoustic-scenes-2020-mobile-development.audio.1.zip"],
        "dir": "tau2020_mobile", "name": "TAU Urban Acoustic Scenes 2020 Mobile, Development (subset: audio.1)",
        "attribution": "Heittola, Mesaros, Virtanen, DCASE 2020 - Tampere University",
        "role": "רקע יומיומי בטלפונים אמיתיים (רחוב, מטרו, אוטובוס, פארק...) - מדידת התראות שווא לשעה. NC: הערכה בלבד"},
    # ארכיוני TAU נוספים לפי סצנה (הקבצים ממוינים לפי סצנה; נבדק מול meta.csv): אוטובוס, מטרו, פארק + כיכר,
    # רחוב הולכי רגל, רחוב תנועה
    **{f"tau_z{z}": {"zenodo": 3819968, "dir": "tau2020_mobile", "name": f"TAU Urban Acoustic Scenes 2020 Mobile - audio.{z} ({sc})",
                    "files": [f"TAU-urban-acoustic-scenes-2020-mobile-development.audio.{z}.zip"],
                    "attribution": "Heittola, Mesaros, Virtanen, DCASE 2020 - Tampere University",
                    "role": f"רקע יומיומי בטלפונים אמיתיים - {sc}. NC: הערכה בלבד"}
       for z, sc in ((3, "bus"), (4, "metro"), (8, "park + public_square"), (12, "street_pedestrian"),
                     (13, "street_traffic"))},
    # שאר ארכיוני SONYC (CC BY - מותר לאימון): כל הצעקות ברחוב (~360) ושליליים קשים מהרחוב
    **{f"sonyc_a{i}": {"zenodo": 3966543, "dir": "sonyc_ust", "files": [f"audio-{i}.tar.gz"],
                      "name": f"SONYC Urban Sound Tagging v2.3 - audio-{i}",
                      "attribution": "Cartwright et al., DCASE 2020 - SONYC-UST-V2",
                      "role": "רחוב אמיתי (CC BY) - אימון/אימות/מבחן לפי split של המאגר"}
       for i in range(2, 19)},
    # הרחבה (research_tools/dataset_discovery.py): מאגרים ב-CC0 / CC BY לפי Zenodo. files=None -> כל קובצי הרשומה.
    # הרישיון נרשם מה-API; לפני אימון חובה לאמת מול ה-README ולהוסיף מיפוי תוויות ב-hv2_data.py.
    **{name: {"zenodo": rid, "files": None, "dir": name, "name": title, "attribution": f"Zenodo record {rid}",
              "role": role}
       for name, rid, title, role in (
           ("ufrb_esd", 17517988, "UFRB-ESD (Emergency Sound Dataset)", "צרחה / ירי / פיצוץ / סירנה"),
           ("sesa", 3519845, "Sound Events for Surveillance Applications", "ירי / פיצוץ / סירנה מול רגיל"),
           ("gunshot_ipod", 3997406, "Gunshots recorded in an open field using iPod Touch devices", "ירי אמיתי במכשיר נייד"),
           ("c3gd", 22286299, "Certus Caliber Classification Gunshot Dataset (C3GD)", "ירי אמיתי, קליברים"),
           ("gunshot_pam", 13893977, "Gunshot sound files from passive acoustic monitoring", "ירי רחוק ביער"),
           ("mavd_traffic", 4741232, "MAVD-traffic", "רחוב: צופרים, מנועים (שליליים קשים)"),
           ("isolated_urban", 1213793, "Isolated urban sound database", "צלילי רחוב מבודדים"),
           ("bdnoise10", 18639588, "BdNoise-10 urban environmental noise", "רעש עירוני: צופר, בנייה, סירנה"),
           ("csibe", 1243714, "Common Sounds in Bedrooms (CSIBE)", "צלילי בית (שליליים בפנים)"),
           ("acudist", 21695754, "AcuDist (Acoustic Distress Dataset)", "מצוקה מול רקע (צעקות / קריאות)"),
           ("foleyset", 20735877, "FoleySet: human-annotated Foley sounds", "פעולות אנושיות: מכות, חבטות, נפילות"),
           ("knock_emotion", 3668503, "Knocking Sound Effects With Emotional Intentions", "דפיקות (כעס/פחד/רגיל)"),
           ("emo_scream", 18356449, "EMO vocal fry scream", "צרחות שירה (דומה קשה, לא סכנה)"))},
    "esc50": {
        "url": "https://github.com/karolpiczak/ESC-50/archive/refs/heads/master.zip", "dir": "esc50",
        "file": "ESC-50-master.zip", "license": "CC BY-NC 3.0 (ESC-10 subset CC BY)",
        "name": "ESC-50", "attribution": "Piczak, ACM MM 2015 - ESC: Dataset for Environmental Sound Classification",
        "role": "שבירת זכוכית, סירנה, בכי תינוק, זיקוקים. מקור Freesound - מסננים חפיפה עם FSD50K לפי מזהה"},
}


def log(m):
    print(m, flush=True)


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _hash(path, algo):
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url, dest, size=None):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    for attempt in range(6):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if size is not None and have == size:
            break
        req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Range": f"bytes={have}-"} if have else {})})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                mode = "ab" if have and r.status == 206 else "wb"
                done, t0 = (have if mode == "ab" else 0), time.time()
                with open(part, mode) as fh:
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        fh.write(b)
                        done += len(b)
                        if done % (200 << 20) < (1 << 20):
                            log(f"  {os.path.basename(dest)}: {done / 1e6:.0f} MB ({(done - have) / 1e6 / max(1, time.time() - t0):.1f} MB/s)")
            if size is None or os.path.getsize(part) == size:
                break
        except Exception as exc:  # noqa: BLE001 - מנסים שוב (חידוש מאותה נקודה)
            log(f"  ניסיון {attempt + 1} נכשל: {exc}")
            time.sleep(5 * (attempt + 1))
    os.replace(part, dest)


def fetch(name):
    s = SOURCES[name]
    d = os.path.join(BASE, s["dir"])
    entry = {"name": s["name"], "attribution": s["attribution"], "role": s["role"], "gated": False,
             "downloaded": time.strftime("%Y-%m-%d"), "files": {}}
    if "zenodo" in s:
        rec = _get_json(f"https://zenodo.org/api/records/{s['zenodo']}")
        meta = rec["metadata"]
        entry.update(url=f"https://zenodo.org/records/{rec['id']}", record_id=rec["id"], doi=rec.get("doi"),
                     version=meta.get("version"), license=(meta.get("license") or {}).get("id"),
                     access=meta.get("access_right"))
        files = {f["key"]: f for f in rec["files"]}
        for fn in (s["files"] if s["files"] is not None else sorted(files)):
            f = files[fn]
            dest = os.path.join(d, fn)
            algo, want = f["checksum"].split(":", 1)
            if not (os.path.exists(dest) and os.path.getsize(dest) == f["size"] and _hash(dest, algo) == want):
                log(f"{name}: מוריד {fn} ({f['size'] / 1e6:.0f} MB)")
                _download(f["links"]["self"], dest, f["size"])
            got = _hash(dest, algo)
            if got != want:
                raise RuntimeError(f"{fn}: checksum לא תואם ({got} != {want})")
            entry["files"][fn] = {"bytes": f["size"], algo: want, "verified_against": "Zenodo API checksum"}
    else:
        dest = os.path.join(d, s["file"])
        if not os.path.exists(dest):
            log(f"{name}: מוריד {s['file']}")
            _download(s["url"], dest)
        entry.update(url=s["url"], license=s["license"])
        entry["files"][s["file"]] = {"bytes": os.path.getsize(dest), "sha256": _hash(dest, "sha256"),
                                     "verified_against": "sha256 נרשם בהורדה (GitHub לא מפרסם checksum)"}
    _record(name, entry)
    log(f"{name}: ✓ ({entry.get('license')})")
    return entry


def _record(name, entry):
    lock = MANIFEST + ".lock"
    for _ in range(600):                      # כמה תהליכי הורדה במקביל - נעילה פשוטה על הקובץ
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(0.5)
    else:
        raise RuntimeError("MANIFEST נעול")
    try:
        _record_locked(name, entry)
    finally:
        os.close(fd)
        os.remove(lock)


def _record_locked(name, entry):
    m = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as fh:
            m = json.load(fh)
    m.setdefault("danger_sources", {})[name] = entry
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)


def extract(name):
    """פריסה לתיקיית extracted/ - zip בלבד (tar.gz נקרא ישירות בלי פריסה)."""
    d = os.path.join(BASE, SOURCES[name]["dir"])
    out = os.path.join(d, "extracted")
    for fn in os.listdir(d):
        if fn.endswith(".zip"):
            marker = os.path.join(out, f".done_{fn}")
            if os.path.exists(marker):
                continue
            with zipfile.ZipFile(os.path.join(d, fn)) as z:
                for m in z.infolist():                       # הגנה מ-zip slip
                    target = os.path.realpath(os.path.join(out, m.filename))
                    if not target.startswith(os.path.realpath(out) + os.sep):
                        raise RuntimeError(f"נתיב חשוד בארכיון: {m.filename}")
                z.extractall(out)
            open(marker, "w").close()
            log(f"{name}: נפרס {fn}")


if __name__ == "__main__":
    names = sys.argv[1:] or list(SOURCES)
    for n in names:
        fetch(n)
        extract(n)
