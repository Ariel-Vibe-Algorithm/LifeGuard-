# -*- coding: utf-8 -*-
"""
diagnostics.py - בדיקת מוכנות אוטומטית (גרסת Windows): כל מה שצריך לעבוד כדי ש-SOS באמת יגיע.

כל בדיקה מחזירה: key, title, status (OK/WARN/FAIL/UNAVAILABLE), detail (מה נמצא), fix (מה לעשות).
- FAIL  = SOS עלול לא להגיע לאף אחד / חסר רכיב קריטי.
- WARN  = עובד חלקית (למשל מיקום משוער בלבד, סוללה נמוכה).
- UNAVAILABLE = הרכיב לא קיים בגרסה הזו / במחשב הזה - נאמר במפורש, בלי "לדמות".

הבדיקות לא עושות שום דבר "רועש": לא שולחות התראה לאנשי קשר (לזה יש את בדיקת המוכנות החודשית בשרת),
לא מפעילות סירנה. המצלמה נפתחת רק בבדיקה מלאה שהמשתמש/ת הפעיל/ה (נורית המצלמה נדלקת לשנייה).
כל probe ניתן להחלפה (probes=...) - כך הלוגיקה נבדקת בלי חומרה (tests/test_diagnostics.py).
"""

import ctypes
import time

OK, WARN, FAIL, UNAVAILABLE = "OK", "WARN", "FAIL", "UNAVAILABLE"
ORDER = {FAIL: 0, WARN: 1, UNAVAILABLE: 2, OK: 3}
ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌", UNAVAILABLE: "➖"}


def _r(key, title, status, detail, fix=""):
    return {"key": key, "title": title, "status": status, "detail": detail, "fix": fix}


# ---------------------------------------------------------------- probes (חומרה / מערכת)
def probe_location():
    import location_module
    return {"sources": location_module.location_sources_status(), "last": location_module.get_last_known()}


def probe_microphone():
    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        return {"installed": False, "error": str(exc)}
    try:
        dev = sd.query_devices(kind="input")
        return {"installed": True, "device": dev.get("name"), "channels": dev.get("max_input_channels", 0)}
    except Exception as exc:  # noqa: BLE001
        return {"installed": True, "device": None, "error": str(exc)}


def probe_camera():
    try:
        import cv2
    except Exception as exc:  # noqa: BLE001
        return {"installed": False, "error": str(exc)}
    cap = cv2.VideoCapture(0, getattr(cv2, "CAP_DSHOW", 0))
    try:
        ok = cap.isOpened() and cap.read()[0]
    finally:
        cap.release()
    return {"installed": True, "opened": bool(ok)}


class _SPS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def probe_power():
    """Windows GetSystemPowerStatus: AC, אחוז, וחיסכון סוללה (SystemStatusFlag=1)."""
    try:
        s = _SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return {"available": False}
        has_battery = (s.BatteryFlag & 128) == 0
        pct = s.BatteryLifePercent & 0xFF
        return {"available": True, "ac": s.ACLineStatus == 1, "has_battery": has_battery,
                "percent": None if pct == 255 else pct, "saver": s.SystemStatusFlag == 1}
    except Exception:  # noqa: BLE001 - לא Windows
        return {"available": False}


def probe_agent():
    import agent_bridge
    return {"autostart": agent_bridge.is_autostart_installed(),
            "running": agent_bridge.mutex_exists(agent_bridge.AGENT_MUTEX)}


def probe_notifications():
    import windows_notify
    return {"available": windows_notify.available(), "status": windows_notify.status()}


def probe_server(client):
    if client is None:
        return {"configured": False}
    t0 = time.time()
    try:
        h = client.health()
        latency = (time.time() - t0) * 1000
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "reachable": False, "error": str(exc)}
    out = {"configured": True, "reachable": True, "latency_ms": round(latency), "health": h,
           "logged_in": bool(client.token)}
    if client.token:
        try:
            out["setup"] = client.setup()
        except Exception as exc:  # noqa: BLE001
            out["setup_error"] = str(exc)
    return out


DEFAULT_PROBES = {"location": probe_location, "microphone": probe_microphone, "camera": probe_camera,
                  "power": probe_power, "agent": probe_agent, "notifications": probe_notifications}


# ---------------------------------------------------------------- evaluation (טהור)
def eval_location(p, now=None):
    now = now or time.time()
    src = p.get("sources") or {}
    last = p.get("last")
    if src.get("gps_fix"):
        return _r("location", "מיקום", OK, "מקלט GPS מחובר ונותן מיקום מדויק")
    if last and last.get("lat") is not None:
        age = now - float(last.get("timestamp") or now)
        if last.get("is_precise") and age < 600:
            return _r("location", "מיקום", OK, "מיקום מדויק עדכני")
        return _r("location", "מיקום", WARN,
                  "רק מיקום משוער לפי כתובת IP (רדיוס של קילומטרים) - אנשי הקשר יראו אזור, לא כתובת",
                  "לחבר מקלט GPS (USB) או להשתמש באפליקציית הטלפון, שבה יש GPS")
    if src.get("ip_fallback_enabled"):
        return _r("location", "מיקום", WARN, "עדיין לא התקבל מיקום (גם לא משוער)",
                  "לבדוק חיבור לאינטרנט; לחבר מקלט GPS לדיוק")
    return _r("location", "מיקום", FAIL, "אין מקור מיקום", "לחבר מקלט GPS או לאפשר מיקום משוער בהגדרות")


def eval_microphone(p):
    if not p.get("installed"):
        return _r("microphone", "מיקרופון", FAIL, "רכיב ההקלטה לא מותקן (sounddevice)",
                  "pip install sounddevice numpy")
    if not p.get("device") or not p.get("channels"):
        return _r("microphone", "מיקרופון", FAIL, "לא נמצא מיקרופון" + (f" ({p['error']})" if p.get("error") else ""),
                  "לחבר מיקרופון / לאשר גישה: הגדרות Windows → פרטיות → מיקרופון")
    return _r("microphone", "מיקרופון", OK, f"נמצא: {p['device']}")


def eval_camera(p):
    if p is None:
        return _r("camera", "מצלמה", UNAVAILABLE, "לא נבדקה (נבדקת רק ב'בדיקה מלאה' - נורית המצלמה תידלק לרגע)")
    if not p.get("installed"):
        return _r("camera", "מצלמה", WARN, "OpenCV לא מותקן - אין צילום ראיות", "pip install opencv-python")
    if not p.get("opened"):
        return _r("camera", "מצלמה", WARN, "המצלמה לא נפתחה (תפוסה / חסומה / לא קיימת)",
                  "הגדרות Windows → פרטיות → מצלמה → לאפשר לאפליקציות שולחן עבודה")
    return _r("camera", "מצלמה", OK, "המצלמה נפתחה וצילמה פריים")


def eval_power(p):
    if not p.get("available") or not p.get("has_battery"):
        return _r("power", "סוללה וחיסכון", OK if p.get("available") else UNAVAILABLE,
                  "מחשב בלי סוללה (חשמל רציף)" if p.get("available") else "לא ניתן לקרוא את מצב החשמל")
    if p.get("saver"):
        return _r("power", "סוללה וחיסכון", WARN, "מצב חיסכון סוללה פעיל - Windows עלול להשהות את הסוכן ברקע",
                  "לכבות 'חיסכון בסוללה' או להחריג את LifeGuard")
    if p.get("percent") is not None and p["percent"] < 20 and not p.get("ac"):
        return _r("power", "סוללה וחיסכון", WARN, f"סוללה {p['percent']}% ולא בטעינה", "לחבר לחשמל")
    return _r("power", "סוללה וחיסכון", OK,
              f"סוללה {p.get('percent')}%" + (" · בטעינה" if p.get("ac") else ""))


def eval_agent(p):
    if p.get("running"):
        return _r("agent", "סוכן רקע", OK, "פועל - התראות יגיעו גם כשהחלון סגור")
    if p.get("autostart"):
        return _r("agent", "סוכן רקע", WARN, "מוגדר לעלייה עם Windows אבל לא פועל כרגע",
                  "להתחבר מחדש לאפליקציה (מפעיל את הסוכן) או להפעיל מחדש את המחשב")
    return _r("agent", "סוכן רקע", FAIL, "כבוי - כשהאפליקציה סגורה לא תתקבל קריאה מאנשי הקשר",
              "פרופיל → פרטיות → להפעיל 'סוכן רקע'")


def eval_notifications(p):
    if not p.get("available"):
        return _r("notifications", "התראות Windows", WARN,
                  "התראות מערכת לא זמינות - התראות יוצגו רק בחלון האפליקציה",
                  "pip install winotify / לבדוק הגדרות → מערכת → התראות")
    st = p.get("status") or {}
    if st.get("last_error"):
        return _r("notifications", "התראות Windows", WARN, f"ההתראה האחרונה נכשלה: {st['last_error']}",
                  "הגדרות → מערכת → התראות → לאפשר ל-LifeGuard; לכבות 'נא לא להפריע' / Focus")
    return _r("notifications", "התראות Windows", OK, "זמינות")


def eval_server(p):
    out = []
    if not p.get("configured"):
        out.append(_r("server", "שרת LifeGuard", FAIL, "לא מחובר לשרת - SOS לא יגיע לאף אחד",
                      "להתחבר בחשבון שרת (כתובת ב-server_url.txt)"))
        return out
    if not p.get("reachable"):
        out.append(_r("server", "שרת LifeGuard", FAIL, f"השרת לא עונה: {p.get('error', '')}",
                      "לבדוק חיבור לאינטרנט / חומת אש; התראות יישמרו וישלחו כשהחיבור יחזור"))
        out.append(_r("internet", "אינטרנט", WARN, "לא ניתן לאשר חיבור (השרת לא זמין)"))
        return out
    lat = p.get("latency_ms", 0)
    out.append(_r("internet", "אינטרנט", OK, "מחובר"))
    out.append(_r("server", "שרת LifeGuard", OK if lat < 2000 else WARN,
                  f"זמין · {lat} ms" + ("" if lat < 2000 else " (איטי)")))
    if not p.get("logged_in"):
        out.append(_r("account", "חשבון", FAIL, "לא מחובר לחשבון בשרת", "להתחבר מחדש"))
        return out
    s = p.get("setup")
    if s is None:
        out.append(_r("contacts", "אנשי קשר לחירום", WARN, "לא ניתן לבדוק (" + p.get("setup_error", "") + ")"))
        return out
    n = s.get("contacts_accepted", 0)
    if n == 0:
        out.append(_r("contacts", "אנשי קשר לחירום", WARN,
                      "אין אנשי קשר מאושרים - SOS יגיע רק למתנדבים זמינים בקרבת מקום"
                      + (f" ({s['contacts_pending']} הזמנות ממתינות)" if s.get("contacts_pending") else ""),
                      "מעגל הביטחון → להזמין לפחות 2 אנשי קשר"))
    elif n == 1:
        out.append(_r("contacts", "אנשי קשר לחירום", WARN, "איש קשר מאושר אחד בלבד - אם לא יענה, אין גיבוי",
                      "להוסיף לפחות עוד איש קשר אחד"))
    else:
        out.append(_r("contacts", "אנשי קשר לחירום", OK, f"{n} אנשי קשר מאושרים"))
    last = s.get("last_readiness")
    if not last:
        out.append(_r("readiness", "בדיקה שאנשי הקשר מקבלים", WARN, "עוד לא נבדק שאנשי הקשר באמת מקבלים התראות",
                      "כרטיס 'בדיקת מוכנות חודשית' → הרץ עכשיו"))
    else:
        age_d = (s.get("server_time", time.time()) - last["created_at"]) / 86400
        if last["confirmed"] < last["total"]:
            out.append(_r("readiness", "בדיקה שאנשי הקשר מקבלים", WARN,
                          f"בבדיקה האחרונה {last['confirmed']}/{last['total']} אישרו קבלה",
                          "לבקש ממי שלא קיבל לפתוח את האפליקציה ולאשר התראות"))
        elif age_d > 30:
            out.append(_r("readiness", "בדיקה שאנשי הקשר מקבלים", WARN, f"הבדיקה האחרונה לפני {age_d:.0f} ימים",
                          "להריץ שוב"))
        else:
            out.append(_r("readiness", "בדיקה שאנשי הקשר מקבלים", OK, f"כולם קיבלו ({last['total']})"))
    return out


def eval_integrations(setup):
    """ערוץ גיבוי: SMS מהשרת למי שאין לו את האפליקציה / כשה-Push לא מגיע (server/sms.py)."""
    sms = (setup or {}).get("sms") or {}
    if sms.get("configured"):
        return _r("integrations", "ערוץ גיבוי (SMS)", OK, f"SMS מהשרת מוגדר ({sms.get('provider', '')})".strip())
    return _r("integrations", "ערוץ גיבוי (SMS)", UNAVAILABLE,
              "בשרת הזה לא מוגדר ספק SMS - ההתראות עוברות רק באפליקציה (Push/חיבור פתוח)",
              "למנהל השרת: LIFEGUARD_TWILIO_* (DEPLOY_HE.md)")


def run_all(client=None, include_camera=False, probes=None, now=None):
    pr = dict(DEFAULT_PROBES, **(probes or {}))

    def safe(name, *a):
        try:
            return pr[name](*a)
        except Exception as exc:  # noqa: BLE001 - בדיקה שקרסה = ממצא, לא קריסה של המסך
            return {"_error": str(exc)}

    results = [eval_location(safe("location"), now)]
    results.append(eval_microphone(safe("microphone")))
    results.append(eval_camera(safe("camera") if include_camera else None))
    results.append(eval_power(safe("power")))
    results.append(eval_agent(safe("agent")))
    results.append(eval_notifications(safe("notifications")))
    srv = pr["server"](client) if "server" in pr else probe_server(client)
    results.extend(eval_server(srv))
    if srv.get("setup") is not None:
        results.append(eval_integrations(srv["setup"]))
    return sorted(results, key=lambda r: ORDER[r["status"]])


def summary(results):
    fails = [r for r in results if r["status"] == FAIL]
    warns = [r for r in results if r["status"] == WARN]
    if fails:
        return FAIL, f"{len(fails)} בעיות קריטיות: " + ", ".join(r["title"] for r in fails)
    if warns:
        return WARN, f"{len(warns)} אזהרות: " + ", ".join(r["title"] for r in warns)
    return OK, "הכל מוכן"
