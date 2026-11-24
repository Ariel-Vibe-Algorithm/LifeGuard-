# -*- coding: utf-8 -*-
"""
collection.py - איסוף הקלטות למחקר זיהוי מצוקה קולית, בהסכמה מדעת.

עקרונות:
- אין הקלטה בלי רשומת הסכמה חתומה (גרסת נוסח + זמן + כל הסעיפים מסומנים).
- אין שמות: משתתף מזוהה בכינוי אקראי (P-xxxxxx). מתבקשים לא לומר פרטים מזהים בהקלטות.
- פרישה בכל עת: delete_participant() מוחק את כל הקבצים ומשאיר רק רשומת מחיקה (מזהה + זמן) לביקורת.
- ייצוא לאימון כולל רק משתתפים שהסכימו לשימוש באימון, ולא כולל משתתפים שפרשו.
- הנתונים נשמרים מקומית בלבד (research_data/), לא נשלחים לשרת LifeGuard ולא לשום שירות.

מבנה:
  research_data/acoustic/
    participants/<pid>/consent.json
    participants/<pid>/manifest.jsonl     (שורה לכל הקלטה)
    participants/<pid>/audio/<file>.wav   (16kHz mono int16)
    withdrawals.jsonl
"""

import json
import os
import secrets
import shutil
import time
import wave
import zipfile

import numpy as np

SAMPLE_RATE = 16000
CONSENT_VERSION = "1.0"
DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research_data", "acoustic")

CONSENT_TEXT = f"""טופס הסכמה להשתתפות במחקר - זיהוי מצוקה קולית (גרסה {CONSENT_VERSION})

מטרת המחקר: לפתח ולבדוק מודל שמזהה במכשיר עצמו סימני מצוקה קוליים (קריאה לעזרה, צעקה, שבירת זכוכית)
ומבחין ביניהם לבין צלילים רגילים (צחוק, דיבור, טלוויזיה), כדי לצמצם התראות שווא באפליקציית בטיחות.

מה יוקלט: הקלטות קצרות (2-10 שניות) של המילים והצלילים שיוצגו לך על המסך, וכן רעש רקע.
מה לא יוקלט: לא נשמר שמך. אנא אל תאמר/י שם, כתובת או פרט מזהה אחר בהקלטות.
אחסון: ההקלטות נשמרות במחשב המחקר בלבד, תחת כינוי אקראי, ומשמשות למחקר לא-מסחרי.
שימוש: אימון והערכה של מודלים במסגרת המחקר. לא יפורסמו הקלטות גולמיות ללא הסכמה נפרדת.
פרישה: אפשר להפסיק בכל רגע, ולבקש מחיקה של כל ההקלטות שלך בכל עת לפי הכינוי שתקבל/י.
זהירות: חלק מהפריטים מבקשים לצעוק. הם אופציונליים - דלג/י אם זה לא נוח, מעייף את הקול או מעורר מצוקה.
"""

CONSENT_ITEMS = (
    ("adult", "אני בן/בת 18 ומעלה"),
    ("understood", "קראתי והבנתי את מטרת המחקר ומה יוקלט"),
    ("record", "אני מסכים/ה שהקול שלי יוקלט לפי הפריטים שיוצגו"),
    ("training", "אני מסכים/ה שההקלטות ישמשו לאימון ולהערכה של מודלים במחקר"),
    ("withdraw", "ידוע לי שאפשר להפסיק ולבקש מחיקה בכל עת"),
)
REQUIRED_CONSENT = ("adult", "understood", "record", "withdraw")  # "training" - נפרד, ומשפיע על הייצוא

ENVIRONMENTS = ("חדר שקט", "בית עם טלוויזיה/דיבור", "רחוב", "רכב/תחבורה ציבורית", "מקום הומה (קפה/מסיבה)", "אחר")
DISTANCES = ("קרוב (עד חצי מטר)", "בינוני (1-2 מטר)", "רחוק (3 מטר ומעלה)", "בכיס/בתיק")

# פרוטוקול ההקלטה. label: positive = סימן מצוקה; hard_negative = דומה אך לא מצוקה; ambient = רקע.
PROTOCOL = (
    {"id": "kw_help_normal", "label": "distress_keyword", "group": "positive", "seconds": 3,
     "text": "אמור/י בקול רגיל: \"הצילו\""},
    {"id": "kw_help_loud", "label": "distress_keyword", "group": "positive", "seconds": 3,
     "text": "קרא/י בקול רם: \"הצילו!\""},
    {"id": "kw_leave_me", "label": "distress_keyword", "group": "positive", "seconds": 3,
     "text": "אמור/י בתקיפות: \"עזוב אותי!\""},
    {"id": "kw_leave_us", "label": "distress_keyword", "group": "positive", "seconds": 3,
     "text": "אמור/י בתקיפות: \"תעזבו אותי!\""},
    {"id": "kw_stop", "label": "distress_keyword", "group": "positive", "seconds": 3,
     "text": "אמור/י: \"די! תפסיק!\""},
    {"id": "kw_police", "label": "distress_keyword", "group": "positive", "seconds": 3,
     "text": "קרא/י: \"משטרה!\""},
    {"id": "kw_need_help", "label": "distress_keyword", "group": "positive", "seconds": 4,
     "text": "אמור/י: \"אני צריך/ה עזרה, תתקשרו למשטרה\""},
    {"id": "kw_whisper_help", "label": "distress_keyword", "group": "positive", "seconds": 3,
     "text": "לחש/י: \"הצילו\" (כאילו אסור שישמעו)"},
    {"id": "scream_acted", "label": "scream", "group": "positive", "seconds": 3, "optional": True,
     "text": "(אופציונלי - דלג/י אם לא נוח) צעקה קצרה של בהלה"},
    {"id": "neg_call_friend", "label": "loud_normal_speech", "group": "hard_negative", "seconds": 3,
     "text": "קרא/י בקול רם לחבר: \"היי! בוא לפה!\""},
    {"id": "neg_cheer", "label": "cheering", "group": "hard_negative", "seconds": 3,
     "text": "קריאת שמחה: \"יש! גול!\""},
    {"id": "neg_laugh", "label": "laughter", "group": "hard_negative", "seconds": 4,
     "text": "צחוק (אפשר לחשוב על משהו מצחיק)"},
    {"id": "neg_help_joke", "label": "keyword_non_distress", "group": "hard_negative", "seconds": 4,
     "text": "אמור/י בצחוק, בטון רגוע: \"הצילו, יש לי מבחן מחר\""},
    {"id": "neg_story", "label": "normal_speech", "group": "hard_negative", "seconds": 6,
     "text": "ספר/י במשפט או שניים מה אכלת היום"},
    {"id": "neg_cough", "label": "cough", "group": "hard_negative", "seconds": 3,
     "text": "שיעול קצר"},
    {"id": "neg_clap", "label": "clapping", "group": "hard_negative", "seconds": 3,
     "text": "מחיאות כפיים"},
    {"id": "ambient", "label": "ambient", "group": "ambient", "seconds": 10,
     "text": "שקט - לא לדבר. מקליטים את רעש הרקע של המקום"},
)


class ConsentError(Exception):
    pass


def _now():
    return time.time()


def _append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class CollectionStore:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = root
        self.pdir = os.path.join(root, "participants")
        os.makedirs(self.pdir, exist_ok=True)
        readme = os.path.join(root, "README.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as fh:
                fh.write("נתוני מחקר רגישים (הקלטות קול). לא להעלות ל-git / ענן / לשתף.\n"
                         "פרישה: python -m research_tools.collection delete <pid>\n")

    # ---------- משתתפים והסכמה ----------
    def new_participant(self, consent, demographics=None):
        """consent: dict של CONSENT_ITEMS -> bool. מחזיר pid. זורק ConsentError אם חסר סעיף חובה."""
        missing = [k for k in REQUIRED_CONSENT if not consent.get(k)]
        if missing:
            raise ConsentError("חסרים סעיפי הסכמה: " + ", ".join(missing))
        pid = "P-" + secrets.token_hex(3).upper()
        while os.path.exists(os.path.join(self.pdir, pid)):
            pid = "P-" + secrets.token_hex(3).upper()
        os.makedirs(os.path.join(self.pdir, pid, "audio"))
        demo = {k: str(v)[:40] for k, v in (demographics or {}).items()
                if k in ("age_group", "gender", "native_language") and v}
        record = {"participant": pid, "consent_version": CONSENT_VERSION, "consent_text": CONSENT_TEXT,
                  "items": {k: bool(consent.get(k)) for k, _ in CONSENT_ITEMS}, "signed_at": _now(),
                  "demographics": demo}
        with open(os.path.join(self.pdir, pid, "consent.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        return pid

    def consent(self, pid):
        path = os.path.join(self._pdir(pid), "consent.json")
        if not os.path.exists(path):
            raise ConsentError(f"אין רשומת הסכמה ל-{pid}")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def _pdir(self, pid):
        if not (isinstance(pid, str) and pid.startswith("P-") and pid[2:].isalnum() and len(pid) <= 12):
            raise ValueError("מזהה משתתף לא תקין")
        return os.path.join(self.pdir, pid)

    def participants(self):
        return sorted(d for d in os.listdir(self.pdir) if os.path.isdir(os.path.join(self.pdir, d)))

    # ---------- הקלטות ----------
    def save_take(self, pid, prompt_id, samples, session):
        """samples: np.ndarray float32 [-1,1] או int16, 16kHz mono. session: environment/distance/device."""
        self.consent(pid)  # אין הקלטה בלי הסכמה
        prompt = next((p for p in PROTOCOL if p["id"] == prompt_id), None)
        if prompt is None:
            raise ValueError(f"פריט לא מוכר: {prompt_id}")
        pcm = _to_int16(samples)
        q = quality(pcm)
        takes = [r for r in self.manifest(pid) if r["prompt_id"] == prompt_id]
        name = f"{prompt_id}_{len(takes) + 1:02d}_{int(_now())}.wav"
        path = os.path.join(self._pdir(pid), "audio", name)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        row = {"file": f"audio/{name}", "participant": pid, "prompt_id": prompt_id, "label": prompt["label"],
               "group": prompt["group"], "take": len(takes) + 1, "duration_s": round(len(pcm) / SAMPLE_RATE, 3),
               "environment": session.get("environment"), "distance": session.get("distance"),
               "device": str(session.get("device") or "")[:80], "consent_version": CONSENT_VERSION,
               "created_at": _now(), **q}
        _append_jsonl(os.path.join(self._pdir(pid), "manifest.jsonl"), row)
        return row

    def manifest(self, pid):
        return _read_jsonl(os.path.join(self._pdir(pid), "manifest.jsonl"))

    def discard_take(self, pid, file_rel):
        """מחיקת הקלטה בודדת (למשל המשתתף לא מרוצה) - מהדיסק ומהמניפסט."""
        rows = self.manifest(pid)
        keep = [r for r in rows if r["file"] != file_rel]
        target = os.path.realpath(os.path.join(self._pdir(pid), file_rel))
        if os.path.commonpath([target, os.path.realpath(self._pdir(pid))]) != os.path.realpath(self._pdir(pid)):
            raise ValueError("נתיב לא חוקי")
        if os.path.exists(target):
            os.remove(target)
        with open(os.path.join(self._pdir(pid), "manifest.jsonl"), "w", encoding="utf-8") as fh:
            for r in keep:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ---------- פרישה וייצוא ----------
    def delete_participant(self, pid, reason="withdrawal"):
        d = self._pdir(pid)
        if not os.path.isdir(d):
            raise ValueError("משתתף לא קיים")
        n = len(self.manifest(pid))
        shutil.rmtree(d)
        _append_jsonl(os.path.join(self.root, "withdrawals.jsonl"),
                      {"participant": pid, "deleted_at": _now(), "reason": reason, "takes_deleted": n})
        return n

    def withdrawn(self):
        return {r["participant"] for r in _read_jsonl(os.path.join(self.root, "withdrawals.jsonl"))}

    def export_training_set(self, zip_path):
        """רק משתתפים שסימנו הסכמה לאימון ושלא פרשו. מחזיר מספר הקלטות שיוצאו."""
        withdrawn = self.withdrawn()
        count = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            rows_all = []
            for pid in self.participants():
                if pid in withdrawn:
                    continue
                c = self.consent(pid)
                if not c["items"].get("training"):
                    continue
                for r in self.manifest(pid):
                    src = os.path.join(self._pdir(pid), r["file"])
                    if os.path.exists(src):
                        z.write(src, f"{pid}/{r['file']}")
                        rows_all.append({**r, "file": f"{pid}/{r['file']}"})
                        count += 1
            z.writestr("manifest.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows_all))
            z.writestr("README.txt", f"LifeGuard acoustic research set. consent version {CONSENT_VERSION}. "
                                     "Non-commercial research use only. Withdrawn participants excluded.\n")
        return count

    def stats(self):
        out = {"participants": 0, "takes": 0, "by_label": {}, "by_environment": {}, "clipped": 0}
        for pid in self.participants():
            out["participants"] += 1
            for r in self.manifest(pid):
                out["takes"] += 1
                out["by_label"][r["label"]] = out["by_label"].get(r["label"], 0) + 1
                env = r.get("environment") or "?"
                out["by_environment"][env] = out["by_environment"].get(env, 0) + 1
                out["clipped"] += int(bool(r.get("clipped")))
        return out


def _to_int16(samples):
    a = np.asarray(samples)
    if a.dtype == np.int16:
        return a.reshape(-1)
    a = np.clip(a.astype(np.float32).reshape(-1), -1.0, 1.0)
    return (a * 32767).astype(np.int16)


def quality(pcm):
    """בדיקות איכות פשוטות שמוצגות למשתתף: חיתוך (clipping), חלש מדי, שקט מוחלט."""
    x = pcm.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    clipped = float(np.mean(np.abs(pcm) >= 32700)) > 0.001
    return {"rms": round(rms, 5), "peak": round(peak, 4), "clipped": bool(clipped),
            "too_quiet": bool(peak < 0.02)}


def read_wav(path):
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("נדרש WAV mono 16-bit")
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0, sr


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="LifeGuard acoustic research collection")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("delete")
    p.add_argument("participant")
    p = sub.add_parser("export")
    p.add_argument("zip_path")
    a = ap.parse_args(argv)
    store = CollectionStore(a.root)
    if a.cmd == "stats":
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    elif a.cmd == "delete":
        print(f"נמחקו {store.delete_participant(a.participant)} הקלטות של {a.participant}")
    else:
        print(f"יוצאו {store.export_training_set(a.zip_path)} הקלטות ל-{a.zip_path}")


if __name__ == "__main__":
    main()
