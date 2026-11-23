# -*- coding: utf-8 -*-
"""
evidence_vault.py - כספת ראיות לתמונות שצולמו בזמן SOS.

מה ביקשנו: בזמן חירום המצלמה מצלמת *בלי טשטוש* - פני התוקף הם ראיה שיכולה להביא לתפיסתו.
אבל תמונה לא מטושטשת של אנשים היא גם מידע רגיש מאוד. לכן:

1. כל תמונה מוצפנת מיד (AES-256-GCM; המפתח של האירוע מוצפן ב-DPAPI של חשבון Windows) והקובץ
   הגלוי נמחק. רק המשתמש/ת במחשב הזה יכול/ה לפתוח. שום דבר לא נשלח לשרת או לאף אחד.
2. לכל תמונה נרשם SHA-256 של המקור + זמן צילום - כדי שאפשר יהיה להראות למשטרה שהתמונה לא שונתה.
3. אחרי האירוע, לפי הדירוג:
   - "אירוע אמת" / "תחושת סכנה": התמונות נשמרות *כראיה* (מוצפנות, לא מטושטשות), לא נכנסות לאימון,
     ונמחקות אוטומטית אחרי RETENTION_DAYS אם לא יוצאו. ייצוא למשטרה - רק בפעולה יזומה של המשתמש/ת.
   - "התראת שווא": אין ערך ראייתי. הפנים מטושטשות; אם המשתמש/ת הסכים/ה - הגרסה המטושטשת עוברת
     למאגר האימון; המקור הלא-מטושטש נמחק.
4. חבילת ראיות (export_package): מדיה מקורית (תמונות + הקלטות), ציר הזמן המקומי (זמנים, מיקומים,
   אירועי חיישנים, התראות ואישורי קבלה), רשומת השרת החתומה (Ed25519 - server/evidence.py) עם תוצאת
   האימות שלה, manifest ו-SHA256SUMS לכל קובץ. verify_package() מאמת תיקייה שיוצאה - גם אצל מי שקיבל.
5. יומן גישה: כל פתיחה/ייצוא/עיבוד נרשמים ב-manifest בשרשרת hash (כל רשומה מכילה hash של הקודמת) -
   מחיקה או שינוי של רשומה באמצע מתגלים ב-verify().
"""

import base64
import hashlib
import json
import os
import shutil
import time

RETENTION_DAYS = 365
_EXT = ".lgev"
PACKAGE_FORMAT = "lifeguard-evidence-package-v1"
GENESIS = "0" * 64


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _access_hash(prev, entry):
    return hashlib.sha256(f"{prev}|{entry['at']:.6f}|{entry['action']}|{entry.get('actor') or ''}|"
                          f"{entry.get('detail') or ''}".encode("utf-8")).hexdigest()


def verify_access_log(log):
    prev = GENESIS
    for e in log or []:
        if e.get("prev") != prev or e.get("hash") != _access_hash(prev, e):
            return False
        prev = e["hash"]
    return True


def _dpapi_protect(data):
    from storage import HAS_DPAPI
    if not HAS_DPAPI:
        return b"PLAIN" + data          # בלי DPAPI (לא Windows) - מתועד בכרטיס האירוע; הקבצים עדיין AES
    import win32crypt
    return b"DPAPI" + win32crypt.CryptProtectData(data, "LifeGuard evidence", None, None, None, 0)


def _dpapi_unprotect(blob):
    from storage import HAS_DPAPI
    if blob.startswith(b"PLAIN"):
        # מפתח לא מוגן מתקבל רק במחשב בלי DPAPI. אם DPAPI זמין - זה סימן לזיוף/העתקה (סקירה 2)
        if HAS_DPAPI:
            raise ValueError("מפתח לא מוגן במחשב עם DPAPI - נדחה")
        return blob[5:]
    import win32crypt
    return win32crypt.CryptUnprotectData(blob[5:], None, None, None, 0)[1]


def _wipe(path):
    """מחיקה עם דריסה בסיסית (בדיסק SSD אין הבטחה מלאה - מתועד)."""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as fh:
            left = size
            while left > 0:                       # כל הקובץ, לא רק 4MB הראשונים (סקירה 2)
                n = min(left, 4 * 1024 * 1024)
                fh.write(os.urandom(n))
                left -= n
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass
    try:
        os.remove(path)
    except OSError:
        pass


class EvidenceVault:
    def __init__(self, root):
        self.root = root

    def _dir(self, event_id):
        safe = "".join(ch for ch in str(event_id) if ch.isalnum() or ch in "-_")[:80]
        d = os.path.join(self.root, safe)
        os.makedirs(d, exist_ok=True)
        return d

    def _manifest_path(self, event_id):
        return os.path.join(self._dir(event_id), "evidence.json")

    def manifest(self, event_id):
        p = self._manifest_path(event_id)
        if not os.path.exists(p):
            return {"event_id": event_id, "status": "evidence", "created_at": time.time(), "items": []}
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def _save_manifest(self, event_id, m):
        p = self._manifest_path(event_id)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(m, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)

    def log_access(self, event_id, action, actor=None, detail=None, m=None):
        """רשומה ביומן הגישה (שרשרת hash). m: manifest פתוח שיישמר ע"י הקורא (אחרת נשמר כאן)."""
        own = m is None
        m = m if m is not None else self.manifest(event_id)
        log = m.setdefault("access_log", [])
        e = {"at": round(time.time(), 6), "action": action, "actor": actor, "detail": detail,
             "prev": log[-1]["hash"] if log else GENESIS}
        e["hash"] = _access_hash(e["prev"], e)
        log.append(e)
        if own:
            self._save_manifest(event_id, m)
        return e

    def _key(self, event_id):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        kp = os.path.join(self._dir(event_id), "key.bin")
        if os.path.exists(kp):
            with open(kp, "rb") as fh:
                return _dpapi_unprotect(fh.read())
        key = AESGCM.generate_key(bit_length=256)
        with open(kp, "wb") as fh:
            fh.write(_dpapi_protect(key))
        return key

    # ------------------------------------------------------------ בזמן האירוע
    def seal(self, path, event_id, kind="photo", captured_at=None):
        """מצפין קובץ שצולם ומוחק את הגלוי. מחזיר נתיב מוצפן."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        with open(path, "rb") as fh:
            data = fh.read()
        digest = hashlib.sha256(data).hexdigest()
        nonce = os.urandom(12)
        name = os.path.basename(path)
        aad = f"{event_id}/{name}".encode("utf-8")
        blob = nonce + AESGCM(self._key(event_id)).encrypt(nonce, data, aad)
        out = os.path.join(self._dir(event_id), name + _EXT)
        with open(out, "wb") as fh:
            fh.write(blob)
        _wipe(path)
        m = self.manifest(event_id)
        m["items"].append({"file": name, "sealed": os.path.basename(out), "sha256": digest, "kind": kind,
                           "captured_at": captured_at or time.time(), "blurred": False})
        self._save_manifest(event_id, m)
        return out

    def open_item(self, event_id, item):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        with open(os.path.join(self._dir(event_id), item["sealed"]), "rb") as fh:
            blob = fh.read()
        data = AESGCM(self._key(event_id)).decrypt(blob[:12], blob[12:], f"{event_id}/{item['file']}".encode("utf-8"))
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("שלמות הראיה נפגעה (hash לא תואם)")
        return data

    # ------------------------------------------------------------ אחרי האירוע
    def on_rating(self, event_id, rating, training_dir=None, blur=None):
        """
        rating: true_event | felt_danger | false_alarm.
        training_dir: יעד לתמונות מטושטשות (רק אם המשתמש/ת הסכים/ה), אחרת None.
        blur(bgr_image) -> (blurred, faces) - ברירת מחדל: YuNet מ-street_vision.
        """
        m = self.manifest(event_id)
        if not m["items"]:
            return m
        self.log_access(event_id, "rating", detail=rating, m=m)
        if rating != "false_alarm":
            m["status"] = "evidence"
            m["rating"] = rating
            m["delete_after"] = time.time() + RETENTION_DAYS * 86400
            self._save_manifest(event_id, m)
            return m
        import cv2
        import numpy as np
        if blur is None:
            from research_tools.street_vision import FaceBlur
            blur = FaceBlur()
        kept = 0
        for it in m["items"]:
            data = self.open_item(event_id, it)
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if training_dir and img is not None:
                blurred, faces = blur(img)
                os.makedirs(training_dir, exist_ok=True)
                ok, enc = cv2.imencode(".jpg", blurred, [cv2.IMWRITE_JPEG_QUALITY, 90])
                if ok:
                    with open(os.path.join(training_dir, f"{event_id}_{it['file']}"), "wb") as fh:
                        fh.write(enc.tobytes())
                    kept += 1
                it["faces_blurred"] = int(faces)
            _wipe(os.path.join(self._dir(event_id), it["sealed"]))
            it["blurred"], it["sealed"] = True, None
        _wipe(os.path.join(self._dir(event_id), "key.bin"))
        m.update(status="false_alarm_blurred" if kept else "false_alarm_deleted", rating=rating,
                 training_items=kept, originals_deleted_at=time.time())
        self._save_manifest(event_id, m)
        return m

    def export(self, event_id, dest_dir, actor=None):
        """ייצוא יזום (למשטרה): קבצים מקוריים + קובץ hash-ים לאימות שלמות."""
        m = self.manifest(event_id)
        os.makedirs(dest_dir, exist_ok=True)
        lines = []
        for it in m["items"]:
            if not it.get("sealed"):
                continue
            data = self.open_item(event_id, it)
            with open(os.path.join(dest_dir, it["file"]), "wb") as fh:
                fh.write(data)
            lines.append(f"{it['sha256']}  {it['file']}  "
                         f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(it['captured_at']))}")
        with open(os.path.join(dest_dir, "SHA256SUMS.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        m.setdefault("exports", []).append({"at": time.time(), "count": len(lines)})
        self.log_access(event_id, "export_media", actor=actor, detail=str(len(lines)), m=m)
        self._save_manifest(event_id, m)
        return len(lines)

    def verify(self, event_id):
        """בדיקת שלמות בכספת: כל קובץ מוצפן נפתח ונבדק מול ה-SHA-256 של המקור + שרשרת יומן הגישה."""
        m = self.manifest(event_id)
        bad = []
        checked = 0
        for it in m["items"]:
            if not it.get("sealed"):
                continue
            try:
                self.open_item(event_id, it)
                checked += 1
            except Exception as exc:  # noqa: BLE001 - כל כשל = ממצא
                bad.append({"file": it["file"], "error": str(exc)})
        chain_ok = verify_access_log(m.get("access_log"))
        if chain_ok:   # לא מוסיפים רשומה לשרשרת שבורה - שלא "תכסה" על השבר
            self.log_access(event_id, "verify", detail="ok" if not bad else "FAILED")
        return {"ok": not bad and chain_ok, "checked": checked, "bad": bad, "access_log_ok": chain_ok}

    def export_package(self, event_id, dest_dir, timeline=None, server_record=None, actor=None):
        """
        חבילת ראיות מלאה לתיקייה שהמשתמש/ת בחר/ה:
          media/...            מדיה מקורית (לא מטושטשת) מהכספת
          timeline.json        ציר הזמן המקומי (זמנים, מיקומים, חיישנים, התראות, אישורי קבלה)
          server_record.json   רשומת השרת החתומה (אם התקבלה) + תוצאת האימות ב-manifest
          manifest.json        פרטי החבילה, פריטים, יומן גישה
          SHA256SUMS.txt       hash לכל קובץ בחבילה (sha256sum -c / Get-FileHash)
          VERIFY_HE.txt        איך מאמתים
        """
        m = self.manifest(event_id)
        os.makedirs(os.path.join(dest_dir, "media"), exist_ok=True)
        files = []
        for it in m["items"]:
            if not it.get("sealed"):
                continue
            data = self.open_item(event_id, it)
            rel = f"media/{it['file']}"
            with open(os.path.join(dest_dir, rel), "wb") as fh:
                fh.write(data)
            files.append(rel)
        if timeline is not None:
            with open(os.path.join(dest_dir, "timeline.json"), "w", encoding="utf-8") as fh:
                json.dump(timeline, fh, ensure_ascii=False, indent=1)
            files.append("timeline.json")
        server_check = None
        if server_record is not None:
            try:
                from server.evidence import verify_record
                ok, why = verify_record(server_record)
            except ImportError:
                ok, why = None, "כלי האימות לא זמין במחשב הזה"
            server_check = {"verified": ok, "reason": why, "public_key": server_record.get("public_key")}
            with open(os.path.join(dest_dir, "server_record.json"), "w", encoding="utf-8") as fh:
                json.dump(server_record, fh, ensure_ascii=False, indent=1)
            files.append("server_record.json")
        self.log_access(event_id, "export_package", actor=actor, detail=f"{len(files)} files", m=m)
        self._save_manifest(event_id, m)
        manifest = {"format": PACKAGE_FORMAT, "event_id": event_id, "generated_at": time.time(),
                    "status": m.get("status"), "rating": m.get("rating"),
                    "items": [{k: it.get(k) for k in ("file", "sha256", "kind", "captured_at")}
                              for it in m["items"] if it.get("sealed")],
                    "access_log": m.get("access_log", []), "server_record_check": server_check,
                    "notes": ["captured_at = שעון המחשב בזמן הצילום (לא מאומת חיצונית)",
                              "server_record.json חתום ע\"י השרת; ניתן לאמת עם המפתח הציבורי שבתוכו "
                              "ומול GET /v1/evidence/public-key"]}
        with open(os.path.join(dest_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        files.append("manifest.json")
        with open(os.path.join(dest_dir, "VERIFY_HE.txt"), "w", encoding="utf-8") as fh:
            fh.write("אימות החבילה:\n"
                     "1. Windows PowerShell:  Get-FileHash -Algorithm SHA256 <קובץ>  - להשוות ל-SHA256SUMS.txt\n"
                     "   Linux/macOS:          sha256sum -c SHA256SUMS.txt\n"
                     "2. ה-hash של כל קובץ מדיה מופיע גם ב-manifest.json - נרשם ברגע הצילום, לפני ההצפנה.\n"
                     "3. server_record.json: חתימת Ed25519 של שרת LifeGuard על ציר הזמן, המיקומים ואישורי הקבלה.\n"
                     "   אימות: python -c \"import json; from server.evidence import verify_record; "
                     "print(verify_record(json.load(open('server_record.json', encoding='utf-8'))))\"\n")
        files.append("VERIFY_HE.txt")
        with open(os.path.join(dest_dir, "SHA256SUMS.txt"), "w", encoding="utf-8") as fh:
            for rel in files:
                fh.write(f"{_sha256_file(os.path.join(dest_dir, rel))}  {rel}\n")
        return {"files": files, "media": sum(1 for f in files if f.startswith("media/")),
                "server_record": server_check}

    def purge_expired(self, now=None):
        now = now or time.time()
        n = 0
        if not os.path.isdir(self.root):
            return 0
        for d in os.listdir(self.root):
            p = os.path.join(self.root, d, "evidence.json")
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as fh:
                m = json.load(fh)
            if m.get("delete_after") and m["delete_after"] < now:
                for f in os.listdir(os.path.join(self.root, d)):
                    _wipe(os.path.join(self.root, d, f))
                shutil.rmtree(os.path.join(self.root, d), ignore_errors=True)
                n += 1
        return n


def verify_package(dest_dir):
    """אימות תיקיית חבילה שיוצאה (אצל מי שקיבל אותה): SHA256SUMS, hash המדיה מול ה-manifest, רשומת השרת."""
    problems = []
    sums = {}
    with open(os.path.join(dest_dir, "SHA256SUMS.txt"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                h, rel = line.rstrip("\n").split("  ", 1)
                sums[rel] = h
    for rel, h in sums.items():
        p = os.path.join(dest_dir, rel)
        if not os.path.exists(p):
            problems.append(f"חסר: {rel}")
        elif _sha256_file(p) != h:
            problems.append(f"שונה: {rel}")
    with open(os.path.join(dest_dir, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    for it in man.get("items", []):
        p = os.path.join(dest_dir, "media", it["file"])
        if os.path.exists(p) and _sha256_file(p) != it["sha256"]:
            problems.append(f"המדיה לא תואמת ל-hash מרגע הצילום: media/{it['file']}")
    if not verify_access_log(man.get("access_log")):
        problems.append("שרשרת יומן הגישה שבורה")
    server_ok = None
    rec_path = os.path.join(dest_dir, "server_record.json")
    if os.path.exists(rec_path):
        try:
            from server.evidence import verify_record
            with open(rec_path, encoding="utf-8") as fh:
                server_ok, why = verify_record(json.load(fh))
            if not server_ok:
                problems.append(f"רשומת השרת: {why}")
        except ImportError:
            pass
    return {"ok": not problems, "problems": problems, "files": len(sums), "server_record_ok": server_ok}
