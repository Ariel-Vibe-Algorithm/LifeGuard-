# -*- coding: utf-8 -*-
"""
freesound_collect.py - איסוף קטעי אודיו מ-Freesound (API v2) לאימון ובדיקה של מנוע הסכנה הקולי.

מה נאסף: לכל מחלקה בטקסונומיה של hv2_data (יעדים + שליליים קשים) - חיפוש לפי תגיות, רק רישיונות שמותרים
לשימוש מסחרי (CC0 / CC BY), באורך 0.5-30 ש'. מורידים את ה-preview (mp3 באיכות גבוהה) - לא את הקובץ המקורי
(זה דורש OAuth). ה-preview הוא דחוס, כמו אודיו בטלפון; זה בסדר לאימון ראש מעל YAMNet.

תיוג: לפי התגיות של המעלה (תיוג חלש, ברמת קטע). קטע מקבל את כל המחלקות שהתגיות שלו מתאימות להן, לא רק את
המחלקה שבה נמצא. שלוש שכבות ניקוי (v2 של הכללים - ראו CHANGES.md):
1. AMBIGUOUS / AMBIGUOUS_SUB - הקטע כולו מוחרג (צרחה של חיה, סינתיסייזר, "מפלצת"...). AMBIGUOUS_SUB נבדק
   כתת-מחרוזת, כדי לתפוס "diy-synth", "synthesized", "cartoony" וכו'.
2. VETO - תגית שמבטלת **תווית יעד מסוימת** (סירנת דאב/רגאיי איננה סירנה; רינגטון איננו אזעקה; בעיטת תוף
   איננה חבטה; קרב/רימון איננו זיקוקים; צעקות משחק ושמחה אינן מצוקה). הקטע מוחרג מהקטגוריה הזו בלבד
   (לא חיובי ולא שלילי) ונשאר שלילי רגיל לשאר.
3. NEG_EXCL - קטע בלי תווית היעד אבל עם מילה נרדפת לה ("shriek", "klaxon", "impact", "hit"...) - מוחרג
   מהקטגוריה הזו (לא נלמד כשלילי קשה, שהוא כנראה בעצם חיובי).
ההחרגות נשמרות בשדה "excl" (רשימת קטגוריות hv2) ומתורגמות ב-hv3_freesound לתגיות "excl:<cat>".

אין דליפה:
- קטע שנמצא ב-FSD50K (dev או eval) מוחרג - המנוע הקיים אומן על FSD50K dev, והבדיקה חייבת להיות על קטעים
  שאף אחד מהמודלים לא ראה.
- הפיצול train / val / test הוא לפי **המעלה** (hash של שם המשתמש), כך שאותו אדם ואותו מיקרופון לא מופיעים
  גם באימון וגם בבדיקה. "חבילות" (packs) ב-Freesound שייכות תמיד למעלה אחד - כך שגם הן לא נחצות.

המפתח נקרא ממשתנה הסביבה FREESOUND_API_KEY (ב-Windows גם מהרישום של המשתמש). הוא לא נכתב לאף קובץ.

שימוש:
  python -I research_tools/freesound_collect.py search      # -> research_data/freesound/catalog.jsonl
  python -I research_tools/freesound_collect.py download    # -> research_data/freesound/audio/<id>.mp3
  python -I research_tools/freesound_collect.py summary     # -> research/freesound_dataset_summary.md
  python -I research_tools/freesound_collect.py audit       # כמה קטעים כל כלל ניקוי תפס (בלי רשת)
"""
import collections
import concurrent.futures as cf
import csv
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "research_data", "freesound")
CATALOG = os.path.join(OUT, "catalog.jsonl")
AUDIO = os.path.join(OUT, "audio")
FSD_GT = os.path.join(OUT, "FSD50K.ground_truth")
SUMMARY = os.path.join(ROOT, "research", "freesound_dataset_summary.md")
API = "https://freesound.org/apiv2/search/text/"
LICENSE_FILTER = 'license:("Creative Commons 0" OR "Attribution")'
DURATION_FILTER = "duration:[0.5 TO 30]"
RULES_VERSION = "labels_v2"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# מחלקה -> (תגיות שמגדירות אותה, כמה קטעים לבקש לכל היותר)
TARGETS = {
    "scream": ({"scream", "screaming", "screams"}, 900),
    "shout": ({"shout", "shouting", "yell", "yelling", "shouts"}, 700),
    "crying": ({"crying", "sobbing", "sob", "weeping", "cry", "baby-crying", "baby-cry", "baby-cries"}, 700),
    "siren": ({"siren", "sirens", "police-siren", "ambulance-siren", "air-raid-siren"}, 900),
    "alarm": ({"alarm", "car-alarm", "smoke-alarm", "fire-alarm", "smoke-detector", "burglar-alarm", "alarm-bell",
                "alarms", "buzzer"}, 900),
    "impact": ({"slam", "door-slam", "thud", "thump", "slamming"}, 900),
    "fireworks": ({"fireworks", "firecracker", "firecrackers", "petard"}, 700),
    # יעדים שכבר עברו ל-v2 - נאספים כדי לשמש שליליים נכונים לאחרים (ירי אינו זיקוקים וכו')
    "gunshot": ({"gunshot", "gun-shot", "gunfire", "gunshots"}, 300),
    "explosion": ({"explosion", "explosions"}, 300),
    "glass_break": ({"glass-break", "glass-breaking", "shatter", "breaking-glass"}, 300),
}
HARD_NEG = {
    "horn": ({"car-horn", "horn-honk", "honk", "honking"}, 400),
    "drums_music": ({"drums", "drum", "snare", "percussion"}, 400),
    "music": ({"music", "song", "guitar", "piano"}, 400),
    "crowd_cheer": ({"cheering", "applause", "crowd", "stadium"}, 400),
    "children": ({"children", "kids", "playground"}, 400),
    "laughter": ({"laughter", "laugh", "laughing", "giggle"}, 400),
    "construction": ({"construction", "drill", "hammer", "jackhammer"}, 400),
    "traffic": ({"traffic", "street", "car-passing"}, 400),
    "speech": ({"speech", "talking", "conversation", "voice"}, 500),
    "tv_like": ({"television", "tv", "radio"}, 300),
    "door_knock": ({"knock", "knocking", "door"}, 400),
    "thunder": ({"thunder", "storm"}, 300),
    "singing": ({"singing", "choir", "sing"}, 400),
    "beep": ({"beep", "ringtone", "phone-ring", "notification"}, 400),
    "dog": ({"dog", "bark", "barking"}, 300),
    "kitchen": ({"kitchen", "dishes", "cutlery"}, 300),
    "sports": ({"sport", "basketball", "football", "whistle"}, 300),
    "rain_wind": ({"rain", "wind"}, 300),
}

# תגיות שהופכות קטע לעמום - לא חיובי ולא שלילי (צרחה של חיה, אפקט מסונתז, יצור מסרט)
AMBIGUOUS = {"animal", "bird", "fox", "goat", "monkey", "pig", "cat", "owl", "peacock", "seagull", "parrot", "horse",
             "synth", "synthesizer", "monster", "creature", "zombie", "demon", "alien", "robot", "game", "8-bit",
             "chiptune", "cartoon", "toy", "reversed", "glitch"}
# אותו דבר, כתת-מחרוזת בתגית (v1 פספס "diy-synth" - 122 קטעי "סירנה" של מעלה אחד, כולם סינתיסייזר דאב)
AMBIGUOUS_SUB = ("synth", "chiptune", "8-bit", "8bit", "glitch", "cartoon", "monster", "zombie", "alien", "robot",
                 "creature", "demon")

# וטו לתווית יעד מסוימת: (מחלקות בקטלוג) -> תגיות. הקטע מוחרג מהקטגוריות hv2 של המחלקה (לא חיובי ולא שלילי).
_VOICE_ANIMAL = {"dog", "puppy", "whine", "whining", "pig", "squeal", "baby-goat", "kitten", "cow", "sheep"}
_VOICE_PROCESSED = {"processed", "distorted", "pitched", "pitch-shifted", "pitch-shift", "cartoony", "funny",
                    "comedy", "comic", "vocoder", "effected"}
_VOICE_HAPPY = {"happy", "fun", "play", "playing", "kids", "children", "party", "stadium", "cheering", "cheer",
                "celebration", "game-show", "sports", "football", "soccer", "laughing", "laughter"}
VETO = {
    # סירנה: סירנת דאב/רגאיי (אפקט מוזיקלי) ומד"ב - 193/645 מהקטעים, 122 מתוך 192 ב-val ממעלה אחד
    "siren": {"dub", "dubsirene", "dub-siren", "reggae", "dancehall", "soundsystem", "sound-system", "soundclash",
              "rasta", "scifi", "sci-fi", "space", "laser", "ufo", "spaceship", "squarewave", "music", "loop", "song"},
    # אזעקה: רינגטון/טלפון (93), צלילי משחק/ממשק (57), מד"ב (135), שעון מעורר (27) - לא אזעקת סכנה
    "alarm": {"ringtone", "ring-tone", "phone-ring", "cellphone", "notification", "message", "sms", "level-up",
              "quest-finish", "pause-game", "musical-sting", "cinematic-sting", "dramatic-sting", "dramatic-stab",
              "cinematic-hit", "ui", "menu", "button", "scifi", "sci-fi", "space", "alarm-clock", "clock",
              "wake-up", "game-show", "quiz", "music", "loop"},
    # חבטה: בעיטת תוף/בס/טריילר (78) - לא אירוע פיזי
    "impact": {"kick", "808", "bass", "lowend", "low-end", "subs", "sub", "trailer", "cinematic", "beat", "music",
               "drum", "drums", "kick-drum", "bassdrum", "riser", "whoosh"},
    # זיקוקים: קרב/נשק (103) וקטעי סאונד-דיזיין מעובדים (21 ב-test ממעלה אחד)
    "fireworks": {"war", "battle", "grenade", "bomb", "missile", "mortar", "gun", "gunshot", "military", "artillery",
                  "weapon", "rifle", "pistol", "experimental", "processed", "electronic", "synthesized"},
    "scream": _VOICE_ANIMAL | _VOICE_PROCESSED,
    # צעקה/בכי: + צעקות שמחה/משחק (מוגדרות בכוונה כשלילי קשה - לא יכולות להיות גם חיובי)
    "shout": _VOICE_ANIMAL | _VOICE_PROCESSED | _VOICE_HAPPY,
    "crying": _VOICE_ANIMAL | _VOICE_PROCESSED | _VOICE_HAPPY,
}
# קטע בלי תווית היעד, עם מילה נרדפת לה -> מוחרג מהיעד (לא שלילי)
NEG_EXCL = {
    "scream": {"shriek", "screech", "yelp", "wail", "holler", "panic", "terror", "help", "screamer", "scream"},
    "distress_voice": {"shriek", "screech", "yelp", "wail", "holler", "panic", "terror", "help", "sob", "whimper",
                       "weep", "tears", "baby"},
    "siren": {"police", "ambulance", "emergency", "klaxon", "fire-truck", "firetruck", "fire-engine", "air-raid"},
    "alarm": {"alert", "warning", "klaxon", "smoke-detector", "detector", "emergency"},
    "impact": {"impact", "hit", "bang", "crash", "smash", "punch", "whack", "bump", "fall", "body-fall", "bodyfall",
               "drop", "dropped", "slams"},
    "fireworks": {"firework", "fire-works", "fire-crackers", "cracker", "new-year", "new-years", "4th-of-july",
                  "fourth-of-july", "pyrotechnics"},
}
# מחלקה בקטלוג -> קטגוריות hv2 (חייב להיות זהה ל-hv3_freesound.LABEL_MAP)
CAT_OF = {"scream": ("scream", "distress_voice"), "shout": ("distress_voice",), "crying": ("distress_voice",)}
ALL = {**TARGETS, **HARD_NEG}


def api_key():
    k = os.environ.get("FREESOUND_API_KEY")
    if not k and os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            k = winreg.QueryValueEx(h, "FREESOUND_API_KEY")[0]
    if not k:
        sys.exit("חסר FREESOUND_API_KEY")
    return k


def fsd50k_ids():
    ids = set()
    for split in ("dev", "eval"):
        with open(os.path.join(FSD_GT, f"{split}.csv"), encoding="utf-8") as fh:
            ids.update(int(r["fname"]) for r in csv.DictReader(fh))
    return ids


def split_of(username):
    h = int(hashlib.sha256(username.lower().encode()).hexdigest(), 16) % 100
    return "test" if h < 20 else "val" if h < 35 else "train"


def classify(tags):
    """תגיות -> (תוויות קטלוג, עמום?, קטגוריות hv2 שהקטע מוחרג מהן, תוויות שבוטלו)."""
    t = {x.lower() for x in tags}
    labs = {c for c, (ts, _) in ALL.items() if t & ts}
    amb = bool(t & AMBIGUOUS) or any(s in x for x in t for s in AMBIGUOUS_SUB)
    excl, vetoed = set(), set()
    for c in list(labs):
        if c in VETO and t & VETO[c]:
            labs.discard(c)
            vetoed.add(c)
            excl.update(CAT_OF.get(c, (c,)))
    pos_cats = {k for c in labs for k in CAT_OF.get(c, (c,))}
    for cat, words in NEG_EXCL.items():
        if cat not in pos_cats and t & words:
            excl.add(cat)
    excl -= pos_cats                       # תווית חיובית אחרת (למשל scream) גוברת על וטו של shout
    return sorted(labs), amb, sorted(excl), sorted(vetoed)


def labels_for(tags):
    """תאימות לאחור: (תוויות, עמום?)."""
    labs, amb, _, _ = classify(tags)
    return labs, amb


def search():
    import requests
    key, excl = api_key(), fsd50k_ids()
    seen = {}
    if os.path.exists(CATALOG):
        for line in open(CATALOG, encoding="utf-8"):
            r = json.loads(line)
            seen[r["id"]] = r
    stats = {}
    for cls, (tags, want) in ALL.items():
        tag_q = " OR ".join(f'tag:"{t}"' for t in sorted(tags))
        flt = f"({tag_q}) {LICENSE_FILTER} {DURATION_FILTER}"
        got, page, kept = 0, 1, 0
        while got < want:
            for attempt in range(4):
                r = requests.get(API, params={"query": "", "filter": flt, "page": page, "page_size": 150,
                                              "fields": "id,name,tags,license,username,duration,previews,samplerate,pack",
                                              "token": key}, timeout=60)
                if r.status_code == 429:
                    time.sleep(30 * (attempt + 1))
                    continue
                break
            if r.status_code != 200:
                print(f"  {cls}: HTTP {r.status_code} בעמוד {page} - עוצר את המחלקה")
                break
            d = r.json()
            for x in d.get("results", []):
                got += 1
                if x["id"] in excl or x["id"] in seen:
                    continue
                labs, amb = labels_for(x["tags"])
                lic = "zero" if "zero" in x["license"] else "by"
                seen[x["id"]] = {"id": x["id"], "user": x["username"], "split": split_of(x["username"]),
                                 "license": lic, "license_url": x["license"], "duration": x["duration"],
                                 "query_class": cls, "labels": labs, "ambiguous": amb,
                                 "tags": x["tags"],                      # כל התגיות (התוויות מחושבות מהן)
                                 "pack": x.get("pack"), "name": x["name"],
                                 "preview": x["previews"]["preview-hq-mp3"]}
                kept += 1
            if not d.get("next"):
                break
            page += 1
            time.sleep(1.1)                     # מגבלת ה-API: 60 בקשות לדקה
        stats[cls] = (got, kept, d.get("count") if r.status_code == 200 else None)
        print(f"{cls:13s} נמצאו {stats[cls][2]} · נבדקו {got} · חדשים {kept}")
    with open(CATALOG, "w", encoding="utf-8") as fh:
        for r in seen.values():
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"סה\"כ בקטלוג: {len(seen)} (הוחרגו קטעי FSD50K: {len(excl)} מזהים נבדקו)")


def load_catalog():
    """התוויות מחושבות מחדש מהתגיות בכל טעינה - כך שינוי בטקסונומיה חל על כל הקטלוג."""
    rows = [json.loads(line) for line in open(CATALOG, encoding="utf-8")]
    for r in rows:
        r["labels"], r["ambiguous"], r["excl"], r["vetoed"] = classify(r["tags"])
    return rows


def rules_hash():
    """טביעת אצבע של כללי התיוג - נשמרת ב-gate.json כדי לדעת על איזה תיוג נמדד ה-test."""
    blob = json.dumps([RULES_VERSION, sorted((k, sorted(v[0])) for k, v in ALL.items()), sorted(AMBIGUOUS),
                       AMBIGUOUS_SUB, sorted((k, sorted(v)) for k, v in VETO.items()),
                       sorted((k, sorted(v)) for k, v in NEG_EXCL.items())])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _fetch(r):
    import requests
    p = os.path.join(AUDIO, f"{r['id']}.mp3")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return True
    for attempt in range(3):
        try:
            resp = requests.get(r["preview"], timeout=60)
            if resp.status_code == 200 and resp.content:
                with open(p + ".part", "wb") as fh:
                    fh.write(resp.content)
                os.replace(p + ".part", p)
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 * (attempt + 1))
    return False


def download():
    os.makedirs(AUDIO, exist_ok=True)
    rows = [r for r in load_catalog() if not r["ambiguous"]]
    ok = 0
    with cf.ThreadPoolExecutor(16) as ex:
        for i, res in enumerate(ex.map(_fetch, rows), 1):
            ok += res
            if i % 500 == 0:
                print(f"  {i}/{len(rows)} ({ok} תקינים)", flush=True)
    print(f"הורדו {ok}/{len(rows)}")


def audit():
    """כמה קטעים כל כלל תפס, לפי פיצול ומעלה מוביל - כדי שכל כלל יהיה מגובה במספרים."""
    rows = load_catalog()
    print(f"כללים {RULES_VERSION} ({rules_hash()}): {len(rows)} קטעים, עמומים {sum(r['ambiguous'] for r in rows)}")
    ok = [r for r in rows if not r["ambiguous"]]
    for c in VETO:
        hit = [r for r in ok if c in r["vetoed"]]
        print(f"  וטו {c:10s} {len(hit):4d}  {dict(collections.Counter(r['split'] for r in hit))}  "
              f"{collections.Counter(r['user'] for r in hit).most_common(3)}")
    for c in NEG_EXCL:
        hit = [r for r in ok if c in r["excl"] and c not in {k for v in r["vetoed"] for k in CAT_OF.get(v, (v,))}]
        print(f"  שלילי מוחרג {c:14s} {len(hit):4d}")
    for c in ("scream", "shout", "crying", "siren", "alarm", "impact", "fireworks"):
        rs = [r for r in ok if c in r["labels"]]
        u = collections.Counter(r["user"] for r in rs)
        print(f"  {c:10s} חיוביים {len(rs):4d} · מעלים {len(u):3d} · המעלה הגדול {u.most_common(1)}")


def summary():
    rows = [r for r in load_catalog() if not r["ambiguous"] and os.path.exists(os.path.join(AUDIO, f"{r['id']}.mp3"))]
    users = {s: len({r["user"] for r in rows if r["split"] == s}) for s in ("train", "val", "test")}
    lines = ["# מאגר Freesound למנוע הסכנה הקולי", "",
             "נוצר ע\"י `research_tools/freesound_collect.py`. רק CC0 / CC BY, אורך 0.5-30 ש', בלי קטעים שנמצאים ב-FSD50K. "
             "פיצול לפי מעלה (אין מעלה משותף בין train / val / test). התיוג לפי תגיות המעלה - תיוג חלש, "
             f"אחרי כללי ניקוי {RULES_VERSION} (עמום / וטו לתווית / מילים נרדפות בשליליים).", "",
             f"- קטעים: {len(rows):,} · שעות: {sum(r['duration'] for r in rows) / 3600:.1f}",
             f"- מעלים: train {users['train']:,} · val {users['val']:,} · test {users['test']:,}",
             f"- רישיונות: CC0 {sum(r['license'] == 'zero' for r in rows):,} · CC BY {sum(r['license'] == 'by' for r in rows):,}",
             "", "| מחלקה | train | val | test | מעלים ב-test | הוחרגו בוטו |", "|---|---|---|---|---|---|"]
    for cls in ALL:
        c = {s: sum(1 for r in rows if cls in r["labels"] and r["split"] == s) for s in ("train", "val", "test")}
        ut = len({r["user"] for r in rows if cls in r["labels"] and r["split"] == "test"})
        v = sum(1 for r in rows if cls in r["vetoed"])
        lines.append(f"| {cls} | {c['train']} | {c['val']} | {c['test']} | {ut} | {v} |")
    lines += ["", "ייחוס: כל קטע ב-CC BY דורש ייחוס למעלה - הרשימה המלאה (מזהה, משתמש, רישיון) ב-"
              "`research_data/freesound/catalog.jsonl` (לא במאגר)."]
    open(SUMMARY, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    {"search": search, "download": download, "summary": summary, "audit": audit}[sys.argv[1]]()
