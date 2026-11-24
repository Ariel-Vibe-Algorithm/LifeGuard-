# -*- coding: utf-8 -*-
"""
hv2_data.py - רישום מאגרים, רישוי ופיצולים למנוע הסכנה v2 (קליטה ובדיקה אוטומטית).

כל קטע מכל מקור מקבל רשומה אחת: מקור, רישיון (לכל קטע כשיש), האם מותר לאימון מסחרי, ייחוס, תוויות בטקסונומיה
שלנו, סביבה, מכשיר, הקלטה/סשן, פיצול (train / val / test) וסטטוס אימות התווית.

כללי פיצול (אין דליפה בין פיצולים):
- FSD50K: dev -> train/val לפי hash של המעלה (uploader); eval -> test. אין מעלה משותף בין dev ל-eval (נבדק).
- ירי (gunshot_fit): לפי מזהה ההקלטה (uuid). חצי 1 -> test (אותו חצי כמו ב-hazard_operating_point);
  חצי 0 -> train/val. קטעים מאותה הקלטה לעולם לא נחצים.
- SONYC: לפי ה-split של המאגר (train/validate מחיישנים שונים; test - אותם חיישנים בזמן מאוחר יותר).
- מאגרים בלי זכות אימון מסחרי (TAU, ESC-50, Nonspeech7k; וקטעי FSD50K ב-CC BY-NC / Sampling+) -> **מבחן בלבד**.
  Nonspeech7k: ב-Zenodo מופיע CC BY 4.0, במאמר וברשימת DCASE - CC BY-NC-SA. נוהגים בזהירות: NC.
- ESC-50 / Nonspeech7k: קטע שמזהה ה-Freesound שלו מופיע ב-FSD50K - מוצא (לא נמדד על מה שהמודל ראה).

שימוש: python -I research_tools/hv2_data.py manifest   -> research_data/manifests/hazard_v2_manifest.jsonl
                                                           + research/hazard_v2_dataset_summary.md
"""
import csv
import hashlib
import json
import os
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

AUDIO = os.path.join(ROOT, "research_data", "audio")
FSD = os.path.join(ROOT, "research", "samples", "fsd50k")
MANIFEST = os.path.join(ROOT, "research_data", "manifests", "hazard_v2_manifest.jsonl")
SUMMARY = os.path.join(ROOT, "research", "hazard_v2_dataset_summary.md")

# טקסונומיה: יעדים (מה מזהים) ושליליים קשים (מה דומה ולא מסוכן)
TARGETS = ("gunshot", "explosion", "fireworks", "glass_break", "siren", "alarm", "scream", "impact", "distress_voice")
HARD_NEG = ("horn", "drums_music", "music", "crowd_cheer", "children", "laughter", "construction", "traffic",
            "speech", "tv_like", "door_knock", "thunder")
# תוויות נוספות מחוץ ל-TARGETS (לא משנות את צורת המטמון): מכה/אגרוף, התנגשות רכב, פולי אחר
EXTRA_TAGS = ("blow", "crash", "foley_other", "braking", "dog")
FOLEY_MAP = {"PunchHit": ["blow"], "Knock": ["door_knock"], "DoorSlam": ["impact"], "GlassBreak": ["glass_break"],
             "Cough": [], "Snore": [], "Burp": []}
FOLEY_AMBIGUOUS = {"BoneBreak", "Sword", "Knife", "WoodBreak"}     # אלים-למחצה / צלילי קולנוע - לא חיובי ולא שלילי
UFRB_MAP = {"grito": ["scream", "distress_voice"], "tiro": ["gunshot"], "explosao": ["explosion"], "sirene": ["siren"],
            "buzina": ["horn"], "colisao": ["crash"], "freio": ["braking"], "cachorro": ["dog"], "outros": []}

FSD_MAP = {
    "gunshot": {"Gunshot_and_gunfire"}, "explosion": {"Explosion", "Boom"}, "fireworks": {"Fireworks"},
    "glass_break": {"Shatter"}, "siren": {"Siren"}, "alarm": {"Alarm"}, "scream": {"Screaming"},
    "impact": {"Slam", "Thump_and_thud"},
    "distress_voice": {"Screaming", "Shout", "Yell", "Crying_and_sobbing"},
    "horn": {"Vehicle_horn_and_car_horn_and_honking"},
    "drums_music": {"Drum", "Drum_kit", "Snare_drum", "Bass_drum", "Percussion", "Cymbal", "Crash_cymbal", "Hi-hat",
                    "Tabla", "Gong", "Mallet_percussion"},
    "music": {"Music", "Musical_instrument", "Guitar", "Piano", "Singing", "Electric_guitar", "Keyboard_(musical)"},
    "crowd_cheer": {"Crowd", "Cheering", "Applause", "Chatter", "Human_group_actions"},
    "children": {"Child_speech_and_kid_speaking"}, "laughter": {"Laughter", "Giggle", "Chuckle_and_chortle"},
    "construction": {"Power_tool", "Drill", "Hammer", "Sawing", "Tools"},
    "traffic": {"Traffic_noise_and_roadway_noise", "Car_passing_by", "Motor_vehicle_(road)", "Bus", "Truck",
                "Motorcycle", "Engine", "Accelerating_and_revving_and_vroom"},
    "speech": {"Speech", "Conversation", "Male_speech_and_man_speaking", "Female_speech_and_woman_speaking"},
    "tv_like": {"Speech_synthesizer"}, "door_knock": {"Knock", "Door"}, "thunder": {"Thunder", "Thunderstorm"},
}
SONYC_MAP = {"7-2_person-or-small-group-shouting": "distress_voice", "5-3_siren": "siren", "5-2_car-alarm": "alarm",
             "5-1_car-horn": "horn", "3-1_non-machinery-impact": "impact", "6-1_stationary-music": "music",
             "6-2_mobile-music": "music", "7-3_large-crowd": "crowd_cheer", "2-1_rock-drill": "construction",
             "2-2_jackhammer": "construction", "2-3_hoe-ram": "construction", "2-4_pile-driver": "construction",
             "1-1_small-sounding-engine": "traffic", "1-2_medium-sounding-engine": "traffic",
             "1-3_large-sounding-engine": "traffic", "7-1_person-or-small-group-talking": "speech"}
ESC_MAP = {"glass_breaking": "glass_break", "siren": "siren", "clock_alarm": "alarm", "fireworks": "fireworks",
           "crying_baby": "distress_voice", "car_horn": "horn", "laughing": "laughter", "door_wood_knock": "door_knock",
           "thunderstorm": "thunder", "engine": "traffic", "chainsaw": "construction", "hand_saw": "construction"}
TAU_ENV = {"street_pedestrian": "street", "street_traffic": "street", "public_square": "street", "park": "street",
           "bus": "vehicle", "metro": "vehicle", "tram": "vehicle", "airport": "indoor_public",
           "shopping_mall": "indoor_public", "metro_station": "indoor_public"}
COMMERCIAL_LICENSES = ("zero", "by")       # CC0, CC BY (FSD50K לכל קטע)


def _h(key, mod=100):
    return int(hashlib.sha256(str(key).encode()).hexdigest(), 16) % mod


def _license_kind(url):
    url = (url or "").lower()
    for k in ("by-nc", "sampling+", "zero", "by"):
        if f"/{k}/" in url or url.rstrip("/").endswith(k):
            return k
    return "unknown"


# ================================================================= מקורות
def fsd50k():
    for split in ("dev", "eval"):
        info = json.load(open(os.path.join(FSD, "meta", "FSD50K.metadata", f"{split}_clips_info_FSD50K.json"),
                              encoding="utf-8"))
        with open(os.path.join(FSD, "meta", "FSD50K.ground_truth", f"{split}.csv"), encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                fn, labs = r["fname"], set(r["labels"].split(","))
                meta = info[fn]
                lic = _license_kind(meta.get("license"))
                tags = sorted(t for t, ls in FSD_MAP.items() if labs & ls)
                if split == "eval":
                    sp = "test"
                else:
                    sp = "val" if _h(meta["uploader"]) < 20 else "train"
                yield {"id": f"fsd50k/{split}/{fn}", "source": "fsd50k", "split": sp, "license": lic,
                       "commercial_ok": lic in COMMERCIAL_LICENSES,
                       "attribution": f"{meta.get('uploader')} (freesound.org/s/{fn}), FSD50K - Fonseca et al. 2022",
                       "tags": tags, "env": "mixed", "device": "unknown", "session": f"uploader:{meta['uploader']}",
                       "verified": "human_label_dataset", "ref": {"zip_split": split, "fname": fn},
                       "freesound_id": fn}


def gunshot_fit():
    d = os.path.join(AUDIO, "gunshot_fit", "extracted")
    meta = {}
    p = os.path.join(d, "gunshot-audio-all-metadata.csv")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            meta[r["filename"]] = r
    base = os.path.join(d, "edge-collected-gunshot-audio")
    for firearm in sorted(os.listdir(base)):
        for f in sorted(os.listdir(os.path.join(base, firearm))):
            if not f.endswith(".wav") or "_chan" in f:
                continue
            stem = f[:-4]
            m = meta.get(stem, {})
            uuid = stem.split("_")[0]
            if _h(uuid, 2) == 1:               # = hazard_operating_point.half() == 1 (חצי המבחן)
                sp = "test"
            else:
                sp = "val" if _h("v" + uuid) < 25 else "train"
            mic = (m.get("microphone") or "").lower()
            phone = "internal mic" in mic or m.get("device_manufacturer") in ("Samsung", "Apple", "Google")
            try:
                at = json.loads(m.get("gunshot_location_in_seconds") or "[]")
            except ValueError:
                at = []
            yield {"id": f"gunshot_fit/{firearm}/{stem}", "source": "gunshot_fit", "split": sp, "license": "cc-by-4.0",
                   "commercial_ok": True, "attribution": "Kabealo et al., Data in Brief 2023 (Zenodo 7004819)",
                   "tags": ["gunshot"], "env": "outdoor_range", "device": "phone" if phone else "other_mic",
                   "session": f"recording:{uuid}", "verified": "dataset_timestamps",
                   "ref": {"path": os.path.join(base, firearm, f)}, "gunshot_at_s": at}


def sonyc():
    d = os.path.join(AUDIO, "sonyc_ust")
    ann = os.path.join(d, "annotations.csv")
    if not os.path.exists(ann):
        return
    from research_tools import danger_audio_external as E
    labels = E._sonyc_labels()
    sensors = {}
    with open(ann, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            sensors[r["audio_filename"]] = r["sensor_id"]
    for tf in sorted(f for f in os.listdir(d) if f.startswith("audio-") and f.endswith(".tar.gz")):
        with tarfile.open(os.path.join(d, tf), "r:gz") as t:
            for m in t:
                if not m.isfile() or not m.name.endswith(".wav"):
                    continue
                fn = os.path.basename(m.name)
                info = labels.get(fn)
                if info is None:
                    continue
                tags = sorted({SONYC_MAP[c] for c, v in info["labels"].items() if v and c in SONYC_MAP})
                sp = {"train": "train", "validate": "val"}.get(info["split"], "test")
                yield {"id": f"sonyc/{fn}", "source": "sonyc_ust", "split": sp, "license": "cc-by-4.0",
                       "commercial_ok": True, "attribution": "Cartwright et al., DCASE 2020 (Zenodo 3966543)",
                       "tags": tags, "env": "street", "device": "sensor", "session": f"sensor:{sensors.get(fn)}",
                       "verified": "sonyc_verified" if info["verified"] else "crowd_majority_3",
                       "ref": {"tar": tf, "member": m.name},
                       "unlabeled_any": not any(info["labels"].values())}


def tau():
    from research_tools import danger_audio_external as E
    for it in E.items_tau2020_mobile():
        scene, dev = it["group"].split("|")
        yield {"id": it["id"], "source": "tau2020_mobile", "split": "test", "license": "other-nc (TAU)",
               "commercial_ok": False, "attribution": "Heittola, Mesaros, Virtanen - TAU Urban 2020 Mobile",
               "tags": [], "env": TAU_ENV.get(scene, "unknown"), "device": "phone" if dev in ("b", "c") else "binaural_mic",
               "session": f"tau:{scene}:{dev}", "verified": "background_scene_label", "ref": {"path": it["path"]},
               "scene": scene}


def esc50(fsd_ids):
    d = os.path.join(AUDIO, "esc50", "extracted", "ESC-50-master")
    p = os.path.join(d, "meta", "esc50.csv")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            overlap = r["src_file"] in fsd_ids
            yield {"id": f"esc50/{r['category']}/{r['filename']}", "source": "esc50",
                   "split": "excluded_overlap" if overlap else "test",
                   "license": "cc-by" if r["esc10"] == "True" else "cc-by-nc-3.0", "commercial_ok": False,
                   "attribution": f"ESC-50 (Piczak 2015), freesound.org/s/{r['src_file']}",
                   "tags": [ESC_MAP[r["category"]]] if r["category"] in ESC_MAP else [],
                   "env": "mixed", "device": "unknown", "session": f"freesound:{r['src_file']}",
                   "verified": "human_label_dataset", "ref": {"path": os.path.join(d, "audio", r["filename"])},
                   "category": r["category"], "freesound_id": r["src_file"]}


def foleyset(fsd_lic):
    """FoleySet (Zenodo 20735877): פולי מ-Freesound. הרשומה ב-CC BY 4.0, אבל לכל קטע ב-Freesound רישיון משלו
    שלא מופיע במאגר. קטע שנמצא גם ב-FSD50K מקבל את הרישיון הידוע משם; השאר - 'per-clip unverified'
    (לא מותר לאימון מסחרי עד אימות). קטע שנמצא ב-FSD50K test - מוצא (דליפה)."""
    d = os.path.join(AUDIO, "foleyset", "extracted", "FoleySet")
    p = os.path.join(d, "metadata", "foleyset_metadata.csv")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            sub, fid = r["sub-category"], r["freesound-id"].strip()
            known = fsd_lic.get(fid)
            if known and known[1] == "eval":
                sp = "excluded_overlap"
            else:
                # הפיצול של FoleySet עצמו מערבב מעלים בין train ל-test (נמצא בבדיקה: 364 מעלים) - מפצלים לפי מעלה
                h = _h("foley:" + r["username"])
                sp = "test" if h < 20 else ("val" if h < 35 else "train")
            lic = known[0] if known else "per-clip unverified (FoleySet record: cc-by-4.0)"
            tags = FOLEY_MAP.get(sub, ["foley_other"])
            if sub in FOLEY_AMBIGUOUS:
                tags, sp = [], "excluded_ambiguous"
            yield {"id": f"foleyset/{r['name']}", "source": "foleyset", "split": sp, "license": lic,
                   "commercial_ok": bool(known and known[0] in COMMERCIAL_LICENSES), "license_verified": bool(known),
                   "attribution": f"{r['username']} (freesound.org/s/{fid}), FoleySet (Zenodo 20735877)",
                   "tags": tags, "env": "studio_foley", "device": "unknown", "session": f"uploader:{r['username']}",
                   "verified": "human_label_dataset", "ref": {"path": os.path.join(d, "audio", r["name"])},
                   "category": sub, "freesound_id": fid}


def ufrb_esd():
    """UFRB-ESD (Zenodo 17517988): "ממקורות ציבוריים" בלי מקור לכל קטע ובלי מזהה הקלטה - מבחן בלבד."""
    d = os.path.join(AUDIO, "ufrb_esd", "extracted", "data")
    if not os.path.isdir(d):
        return
    for cls in sorted(os.listdir(d)):
        for f in sorted(os.listdir(os.path.join(d, cls))):
            if f.lower().endswith(".wav"):
                yield {"id": f"ufrb_esd/{cls}/{f}", "source": "ufrb_esd", "split": "test",
                       "license": "cc-by-4.0 (record) / underlying sources unknown - test only", "commercial_ok": False,
                       "attribution": "UFRB-ESD, Universidade Federal do Reconcavo da Bahia (Zenodo 17517988)",
                       "tags": UFRB_MAP.get(cls, []), "env": "mixed", "device": "unknown", "session": f"ufrb:{cls}/{f}",
                       "verified": "human_label_dataset", "ref": {"path": os.path.join(d, cls, f)}, "category": cls}


def nonspeech7k(fsd_ids):
    d = os.path.join(AUDIO, "nonspeech7k")
    p = os.path.join(d, "metadata of test set.csv")
    if not os.path.exists(p):
        return
    tagmap = {"screaming": ["scream", "distress_voice"], "crying": ["distress_voice"], "laugh": ["laughter"]}
    with open(p, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if r["Augmentation type"].strip().lower() != "original":
                continue
            lab = r["Classname"].strip().lower()
            overlap = "freesound" in r["source"].lower() and r["File_ID"].strip() in fsd_ids
            yield {"id": f"nonspeech7k/{lab}/{r['Filename'].strip()}", "source": "nonspeech7k",
                   "split": "excluded_overlap" if overlap else "test",
                   "license": "cc-by-4.0 (Zenodo) / cc-by-nc-sa-4.0 (paper) - treated as NC", "commercial_ok": False,
                   "attribution": "Rashid, Li, Du, IET Signal Processing 2023 (Zenodo 6967442)",
                   "tags": tagmap.get(lab, []), "env": "mixed", "device": "unknown",
                   "session": f"ns7k:{r['File_ID'].strip()}", "verified": "human_label_dataset",
                   "ref": {"path": os.path.join(d, "extracted", "test", r["Filename"].strip())}, "category": lab}


# ================================================================= בדיקות + מניפסט
def validate(rows):
    """בדיקות שחייבות לעבור לפני אימון: אין דליפת סשן בין פיצולים, אין NC באימון, אין מזהה כפול."""
    problems = []
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        problems.append("מזהה כפול")
    by_session = {}
    for r in rows:
        if r["split"] in ("train", "val", "test"):
            by_session.setdefault((r["source"], r["session"]), set()).add(r["split"])
    leaks = [k for k, s in by_session.items() if len(s) > 1 and k[0] != "sonyc_ust"]
    # SONYC: test = אותם חיישנים בזמן מאוחר (הגדרת המאגר); train/val חייבים להיות זרים
    leaks += [k for k, s in by_session.items() if k[0] == "sonyc_ust" and {"train", "val"} <= s]
    if leaks:
        problems.append(f"דליפת סשן בין פיצולים: {len(leaks)} (למשל {leaks[0]})")
    nc_train = [r["id"] for r in rows if r["split"] in ("train", "val") and not r["commercial_ok"]
                and r["source"] not in ("fsd50k", "foleyset")]
    if nc_train:
        problems.append(f"{len(nc_train)} קטעים בלי זכות מסחרית בפיצול אימון")
    fsd_up = {}
    for r in rows:
        if r["source"] == "fsd50k":
            fsd_up.setdefault(r["session"], set()).add(r["ref"]["zip_split"])
    if any(len(s) > 1 for s in fsd_up.values()):
        problems.append("מעלה FSD50K גם ב-dev וגם ב-eval")
    return problems


def build_manifest(log=print):
    t0 = time.time()
    rows = list(fsd50k())
    fsd_ids = {r["freesound_id"] for r in rows}
    fsd_lic = {r["freesound_id"]: (r["license"], r["ref"]["zip_split"]) for r in rows}
    for gen in (gunshot_fit(), sonyc(), tau(), esc50(fsd_ids), nonspeech7k(fsd_ids), foleyset(fsd_lic), ufrb_esd()):
        rows.extend(gen or [])
    probs = validate(rows)
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary(rows, probs)
    log(f"מניפסט: {len(rows)} קטעים ({time.time() - t0:.0f}s); בעיות: {probs or 'אין'}")
    return rows, probs


def load_manifest():
    with open(MANIFEST, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def trainable(r):
    """מותר לאימון/כיול: פיצול train/val ורישיון שמתיר אימון מסחרי."""
    return r["split"] in ("train", "val") and r["commercial_ok"]


def summary(rows, probs, path=SUMMARY):
    from collections import Counter
    L = ["# מניפסט מאגרים - מנוע סכנה v2", "",
         f"נוצר ע\"י `research_tools/hv2_data.py` ({time.strftime('%Y-%m-%d')}). המניפסט המלא (רשומה לכל קטע: מקור, רישיון, "
         "ייחוס, תוויות, סביבה, מכשיר, סשן, פיצול, אימות) נמצא ב-`research_data/manifests/hazard_v2_manifest.jsonl` - "
         "מחוץ למאגר הקוד, כמו האודיו עצמו.", "",
         f"**בדיקות תקינות:** {'עברו (אין דליפת סשן, אין NC באימון, אין כפילות)' if not probs else '; '.join(probs)}", "",
         "## לפי מקור, פיצול ורישיון", "", "| מקור | פיצול | רישיון | מותר לאימון מסחרי | קטעים |", "|---|---|---|---|---|"]
    c = Counter((r["source"], r["split"], r["license"], r["commercial_ok"]) for r in rows)
    for (s, sp, lic, ok), n in sorted(c.items()):
        L.append(f"| {s} | {sp} | {lic} | {'כן' if ok else 'לא'} | {n} |")
    L += ["", "## יעדים ושליליים קשים - קטעים שמותרים לאימון (train / val) מול מבחן", "",
          "| תווית | train | val | test |", "|---|---|---|---|"]
    for t in TARGETS + HARD_NEG:
        n = Counter(r["split"] for r in rows if t in r["tags"] and (r["split"] == "test" or trainable(r)))
        L.append(f"| {t} | {n.get('train', 0)} | {n.get('val', 0)} | {n.get('test', 0)} |")
    L += ["", "## מקורות שנבדקו ולא נכללו", "",
          "- **AudioSet:** האודיו זמין רק כסרטוני YouTube - לא מורידים (אין הרשאה). משתמשים רק באונטולוגיה (דרך YAMNet).",
          "- **UrbanSound8K:** CC BY-NC (הערכה בלבד), 6 GB, וכולו מ-Freesound - חופף ברובו ל-FSD50K ו-ESC-50. לא הורד.",
          "- **Common Voice:** דורש אישור תנאים בחשבון המשתמש/ת - לא בוצע בשמך. FLEURS (CC BY) כבר קיים - לדיבור בלבד, "
          "לא כתחליף לקריאות מצוקה אמיתיות.",
          "- **SONYC v2.3:** הקישור שבבקשה (2590742) הוא רשומת הגרסה; נעשה שימוש ברשומה העדכנית 3966543 (אותו מאגר).",
          "", "## הערות רישוי",
          "- FSD50K: רישיון לכל קטע. לאימון רק CC0 / CC BY. CC BY-NC ו-Sampling+ - מבחן בלבד.",
          "- **ממצא ביקורת:** hazard_audio_v1 אומן על כל dev של FSD50K, כולל ~6,000 קטעי CC BY-NC / Sampling+. v2 לא.",
          "- Nonspeech7k: סתירה ברישוי (Zenodo: CC BY 4.0; המאמר / DCASE: CC BY-NC-SA) - מבחן בלבד.",
          "- TAU, ESC-50: NC - מבחן בלבד. ירי (Florida Tech) ו-SONYC: CC BY - ייחוס נדרש (בעמודת attribution)."]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "manifest":
        build_manifest()
    else:
        print(__doc__)
