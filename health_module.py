# -*- coding: utf-8 -*-
"""
health_module.py
בדיקות תקינות אמיתיות לכל רכיב שמשפיע על חירום. כל בדיקה *מנסה בפועל* (פותחת מיקרופון,
כותבת קובץ, מתחברת לרשת, מריצה PRAGMA quick_check) - ולא מסתפקת ב"הספרייה מותקנת".
רכיב שלא ניתן לבדוק בלי תופעת לוואי (למשל להדליק מצלמה) מסומן NOT_VERIFIED ולא OK.

כל תוצאה כוללת: מה לא עובד, למה זה חשוב, איך מתקנים, והאם פונקציית חירום מושפעת.
"""

import os
import tempfile
import time

import config
import storage

OK = "ok"                    # פועל - נבדק בפועל
LIMITED = "limited"          # פועל חלקית
UNAVAILABLE = "unavailable"  # לא פועל
NOT_VERIFIED = "not_verified"  # לא נבדק (בדיקה הייתה מפעילה חומרה/שולחת הודעה)

STATUS_LABELS = {OK: "תקין", LIMITED: "פעולה מוגבלת", UNAVAILABLE: "לא זמין", NOT_VERIFIED: "לא נבדק"}

MIN_FREE_DISK_BYTES = 500 * 1024 * 1024


def _result(check_id, name, status, what="", why="", fix="", affects_emergency=False, detail=""):
    return {"id": check_id, "name": name, "status": status, "what": what, "why": why,
            "fix": fix, "affects_emergency": bool(affects_emergency), "detail": detail,
            "checked_at": time.time()}


# ==================== בדיקות בודדות ====================

def check_internet():
    import connectivity_module
    st = connectivity_module.check_now()
    if st["online"]:
        return _result("internet", "חיבור לאינטרנט", OK, detail=st["detail"])
    return _result("internet", "חיבור לאינטרנט", UNAVAILABLE,
                   what="אין חיבור לאינטרנט.",
                   why="בלי אינטרנט לא נשלחות התראות לאנשי קשר, פקודות קוליות (תמלול Google) לא "
                       "פועלות, ואין מיקום IP או מפות.",
                   fix="בדוק/י Wi-Fi או כבל רשת, או חבר/י נקודה חמה מהטלפון. התראות שנכשלות נשמרות "
                       "ונשלחות שוב אוטומטית כשהחיבור חוזר - כל עוד האירוע פעיל.",
                   affects_emergency=True, detail=st["detail"])


def _client_of(engine):
    gw = getattr(engine, "gateway", None) if engine is not None else None
    return getattr(gw, "client", None) if gw is not None else None


def check_central_backend(engine=None):
    """שרת LifeGuard: זמין? מחוברים? (בודק בפועל GET /health)."""
    import backend_client
    client = _client_of(engine)
    url = client.base_url if client else config.SERVER_URL
    try:
        info = backend_client.BackendClient(url).health()
    except backend_client.BackendError as exc:
        return _result("backend", "שרת LifeGuard", UNAVAILABLE,
                       what=f"השרת לא זמין ({url}): {exc.message}",
                       why="כל התראות ה-SOS עוברות דרך השרת. בלעדיו אף אחד לא מקבל התראה "
                           "(ההתראה נשמרת ונשלחת כשהשרת חוזר, כל עוד האירוע פעיל).",
                       fix="הפעל/י את השרת (RUN_SERVER_WINDOWS.bat) או בדוק/י את כתובת השרת בהגדרות.",
                       affects_emergency=True)
    if client is None or not client.token:
        return _result("backend", "שרת LifeGuard", UNAVAILABLE,
                       what="השרת זמין אך האפליקציה לא מחוברת אליו.",
                       why="בלי התחברות לא נשלחות התראות ולא מתקבלות התראות מאחרים.",
                       fix="פרופיל ← חיבור לשרת: התחבר/י מחדש.", affects_emergency=True, detail=url)
    stream = client.stream_state
    detail = f"{url} · קבלת התראות: {'זרם פעיל' if stream.get('connected') else 'לא מחובר'} · "              f"Push למובייל (FCM/APNs): {info['push_providers'].get('fcm')}"
    if not stream.get("connected"):
        return _result("backend", "שרת LifeGuard", LIMITED,
                       what="מחובר לשרת, אך ערוץ קבלת ההתראות בזמן אמת לא פעיל.",
                       why="התראות חירום מאחרים עלולות להתעכב.",
                       fix="בדוק/י חיבור אינטרנט; האפליקציה מתחברת מחדש אוטומטית.", affects_emergency=True,
                       detail=detail)
    return _result("backend", "שרת LifeGuard", OK, detail=detail)


def check_delivery_channels(username, engine=None):
    """למי SOS יגיע בפועל: אנשי קשר שאישרו + מתנדבים זמינים בטווח (לפי השרת). הבדיקה החשובה ביותר."""
    import backend_client
    client = _client_of(engine)
    if client is None or not client.token:
        return _result("delivery", "למי תגיע התראת SOS", UNAVAILABLE,
                       what="לא מחובר לשרת LifeGuard - התראת SOS לא תגיע לאף אחד.",
                       why="זו הפונקציה המרכזית של SOS.",
                       fix="התחבר/י לשרת (פרופיל ← חיבור לשרת). בחירום - התקשר/י בעצמך למוקד.",
                       affects_emergency=True)
    try:
        circle = client.relationships()
        summary = client.community_summary()
    except backend_client.BackendError as exc:
        return _result("delivery", "למי תגיע התראת SOS", UNAVAILABLE, what=f"לא ניתן לבדוק: {exc.message}",
                       why="לא ידוע אם יש מי שיקבל התראה.", fix="בדוק/י את החיבור לשרת.", affects_emergency=True)
    accepted = circle["my_contacts"]  # השרת מחזיר רק קשרים שהצד השני אישר
    pending = [i for i in circle.get("sent_invitations", []) if i["status"] == "pending"]
    vols = len(summary.get("volunteers") or [])
    detail = (f"{len(accepted)} אנשי קשר מחוברים · {len(pending)} הזמנות ממתינות · "
              f"{vols} מתנדבים זמינים בטווח {summary.get('radius_km')} ק\"מ"
              + ("" if summary.get("location_known") else " (מיקומך לא ידוע לשרת)"))
    if not accepted and not vols:
        return _result("delivery", "למי תגיע התראת SOS", UNAVAILABLE,
                       what="אין אף איש קשר שאישר את ההזמנה ואין מתנדב זמין בקרבתך.",
                       why="התראת SOS תגיע לשרת אך לא לאף אדם.",
                       fix="מעגל הביטחון: הזמן/י בני משפחה לפי שם משתמש, טלפון או קוד הזמנה, "
                           "ובקש/י מהם לאשר באפליקציה שלהם.",
                       affects_emergency=True, detail=detail)
    if not accepted:
        return _result("delivery", "למי תגיע התראת SOS", LIMITED,
                       what="אין איש קשר מהימן שאישר - רק מתנדבים זמינים יקבלו התראה.",
                       why="משפחה/חברים לא יידעו על האירוע.", fix="הזמן/י אנשי קשר מהימנים.",
                       affects_emergency=True, detail=detail)
    return _result("delivery", "למי תגיע התראת SOS", NOT_VERIFIED,
                   what="יש נמענים מאושרים; מסירה בפועל לא נבדקה בבדיקה זו.",
                   why="אישור קשר לא מוכיח שהאפליקציה שלהם מחוברת עכשיו.",
                   fix="לחץ/י 'בדיקת התראה' - תוצג רשימת מי שהאפליקציה שלו אישרה קבלה.",
                   affects_emergency=True, detail=detail)


def check_databases():
    import auth_module
    import incident_store
    problems, details = [], []
    auth_module._load_users()
    if auth_module.LAST_STORAGE_ERROR:
        problems.append(f"קובץ המשתמשים: {auth_module.LAST_STORAGE_ERROR}")
    else:
        details.append("users.json תקין")
    try:
        incident_store.list_incidents("__health__", limit=1)
        ok, msg = storage.sqlite_health(incident_store.DB_PATH)
        (details if ok else problems).append(f"היסטוריית אירועים: {msg}")
    except Exception as exc:
        problems.append(f"היסטוריית אירועים: {exc}")
    if problems:
        return _result("database", "מסדי נתונים מקומיים", UNAVAILABLE,
                       what="; ".join(problems),
                       why="סיסמת הביטול, אנשי הקשר והיסטוריית האירועים נשמרים כאן.",
                       fix="קבצים פגומים גובו אוטומטית (.corrupt-...). שחזר/י מגיבוי, או פנה/י לתמיכה. "
                           "אל תמחק/י את תיקיית data.",
                       affects_emergency=True, detail="; ".join(details))
    return _result("database", "מסדי נתונים מקומיים", OK, detail="; ".join(details))


def check_storage():
    free = storage.free_disk_bytes(config.DATA_DIR)
    try:
        fd, path = tempfile.mkstemp(prefix=".health-", dir=config.DATA_DIR)
        os.write(fd, b"ok")
        os.close(fd)
        os.remove(path)
        writable = True
        err = ""
    except Exception as exc:
        writable, err = False, str(exc)
    if not writable:
        return _result("storage", "אחסון מקומי", UNAVAILABLE,
                       what=f"לא ניתן לכתוב לתיקיית הנתונים ({err}).",
                       why="הקלטות, תמונות והיסטוריית אירועים לא יישמרו.",
                       fix="הפעל/י את התוכנה מתיקייה עם הרשאות כתיבה (לא Program Files).",
                       affects_emergency=True)
    free_mb = (free or 0) / 1024 / 1024
    if free is not None and free < MIN_FREE_DISK_BYTES:
        return _result("storage", "אחסון מקומי", LIMITED,
                       what=f"נותרו רק {free_mb:.0f}MB פנויים.",
                       why="הקלטה של אירוע ארוך עלולה להיכשל באמצע.",
                       fix="פנה/י מקום בדיסק או מחק/י אירועים ישנים במסך ההיסטוריה.",
                       affects_emergency=True)
    return _result("storage", "אחסון מקומי", OK, detail=f"כתיבה נבדקה; {free_mb:,.0f}MB פנויים")


def check_microphone():
    try:
        import sounddevice as sd
    except Exception:
        return _result("microphone", "מיקרופון / קלט שמע", UNAVAILABLE,
                       what="ספריית sounddevice אינה מותקנת.",
                       why="אין הקלטה באירוע ואין מד רעש.",
                       fix="הרץ/י INSTALL_WINDOWS.bat מחדש.", affects_emergency=True)
    try:
        devices = sd.query_devices()
        inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
    except Exception as exc:
        inputs, devices = [], []
        err = str(exc)
    else:
        err = ""
    if not inputs:
        return _result("microphone", "מיקרופון / קלט שמע", UNAVAILABLE,
                       what="לא נמצא התקן קלט שמע." + (f" ({err})" if err else ""),
                       why="אין הקלטה, אין פקודות קוליות ואין הערכת רעש.",
                       fix="חבר/י מיקרופון וודא/י שהוא מופעל בהגדרות הקול של Windows.",
                       affects_emergency=True)
    # פתיחה אמיתית לשבריר שנייה - זה מה שחושף חסימת פרטיות של Windows ("Microphone access")
    try:
        import numpy as np
        data = sd.rec(int(0.25 * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
        peak = int(np.abs(data).max()) if data is not None and data.size else 0
    except Exception as exc:
        return _result("microphone", "מיקרופון / קלט שמע", UNAVAILABLE,
                       what=f"לא ניתן לפתוח את המיקרופון: {exc}",
                       why="אין הקלטה באירוע ואין פקודות קוליות.",
                       fix="הגדרות Windows ← פרטיות ואבטחה ← מיקרופון: אפשר/י גישה לאפליקציות שולחן עבודה. "
                           "סגור/י תוכנות אחרות שתופסות את המיקרופון.",
                       affects_emergency=True)
    if peak == 0:
        return _result("microphone", "מיקרופון / קלט שמע", LIMITED,
                       what="המיקרופון נפתח אך החזיר שקט מוחלט (ייתכן שהוא מושתק).",
                       why="הקלטות ופקודות קוליות יהיו ריקות.",
                       fix="בדוק/י שהמיקרופון לא מושתק (מקש השתקה / הגדרות קול).",
                       affects_emergency=True, detail=f"{len(inputs)} התקני קלט")
    return _result("microphone", "מיקרופון / קלט שמע", OK,
                   detail=f"נפתח והקליט בהצלחה ({len(inputs)} התקני קלט)")


def check_voice_recognition(engine=None, username=None):
    try:
        import speech_recognition  # noqa: F401
        has_sr = True
    except Exception:
        has_sr = False
    try:
        import pyaudio  # noqa: F401
        has_pa = True
    except Exception:
        has_pa = False
    if not (has_sr and has_pa):
        missing = ", ".join(n for n, ok in (("SpeechRecognition", has_sr), ("PyAudio", has_pa)) if not ok)
        return _result("voice", "פקודות קוליות", UNAVAILABLE,
                       what=f"חסרות ספריות: {missing}.",
                       why="ארבע הפקודות הקוליות (אזעקה רועשת/שקטה/ביטול/כפייה) לא יעבדו.",
                       fix=f"בחלון פקודה בתיקיית התוכנה: .venv\\Scripts\\pip install {missing}",
                       affects_emergency=True)
    if username:
        import auth_module
        phrases = (auth_module.get_voice_secret_word(username), auth_module.get_silent_alarm_phrase(username),
                   auth_module.get_secret_cancel_phrase(username))
        if not all(phrases):
            return _result("voice", "פקודות קוליות", UNAVAILABLE,
                           what="לא הוגדרו כל שלוש מילות הקוד.",
                           why="ההאזנה הקולית לא מופעלת בפרופיל חלקי.",
                           fix="הגדרות ← מילות קוד: השלם/י את שלושת הביטויים.", affects_emergency=True)
    import speech_local
    local_langs = speech_local.available_languages()
    cloud = speech_local.cloud_allowed()
    if not local_langs and not cloud:
        return _result("voice", "פקודות קוליות", UNAVAILABLE,
                       what="מודל זיהוי הדיבור המקומי לא הותקן (ושליחת אודיו לענן כבויה, לשמירה על פרטיות).",
                       why="ארבע הפקודות הקוליות ומילות המצוקה לא יזוהו.",
                       fix="בחלון פקודה בתיקיית התוכנה: .venv\\Scripts\\python speech_local.py --download he",
                       affects_emergency=True)
    listener = getattr(engine, "_voice_listener", None) if engine is not None else None
    running = bool(listener is not None and getattr(listener, "_running", False))
    import connectivity_module
    # אינטרנט רלוונטי רק כשאין מודל מקומי והמשתמש הסכים במפורש לתמלול בענן
    online = connectivity_module.status().get("online") if not local_langs else True
    where = (f"תמלול במכשיר ({', '.join(local_langs)})" if local_langs
             else "תמלול דרך Google (הסכמה מפורשת; דורש אינטרנט)")
    if engine is not None and not running:
        return _result("voice", "פקודות קוליות", LIMITED,
                       what="ההאזנה הקולית אינה פעילה כרגע (הושהתה או נכשלה בהפעלה).",
                       why="אמירת מילת קוד לא תפעיל/תבטל אזעקה.",
                       fix="לחץ/י 'הפעל האזנה קולית' במסך הראשי, ובדוק/י את המיקרופון.",
                       affects_emergency=True)
    if online is False:
        return _result("voice", "פקודות קוליות", UNAVAILABLE,
                       what="ההאזנה פועלת אך אין אינטרנט - התמלול נעשה בשרתי Google.",
                       why="מילות קוד לא יזוהו עד שהחיבור יחזור.",
                       fix="השתמש/י בכפתור SOS או בקיצור המקלדת עד שהחיבור יחזור.",
                       affects_emergency=True)
    return _result("voice", "פקודות קוליות", OK if running else NOT_VERIFIED,
                   what="" if running else "הספריות מותקנות; ההאזנה לא נבדקה בהקשר זה.",
                   detail=f"מאזין פעיל; {where}" if running else where)


def check_location():
    import location_module
    sources = location_module.location_sources_status()
    loc = location_module.get_location()
    desc = location_module.describe_location(loc)
    if sources["gps_fix"]:
        return _result("location", "מיקום", OK, detail=desc["label"])
    gps_note = ("הוגדר מקלט GPS אך אין קליטה" if sources["gps_port_configured"] or sources["gps_candidate_ports"]
                else "אין מקלט GPS מחובר")
    if loc.get("available"):
        return _result("location", "מיקום", LIMITED,
                       what=f"{gps_note}; קיים רק מיקום משוער לפי IP (אזור עיר, לא רחוב).",
                       why="אנשי קשר יקבלו אזור כללי בלבד; לא יחושב מסלול למקום בטוח.",
                       fix="חבר/י מקלט GPS ב-USB והגדר/י LIFEGUARD_GPS_PORT (ראה README). "
                           "במחשב ללא GPS - ציין/י את מיקומך בשיחה עם המוקד.",
                       affects_emergency=True, detail=desc["label"])
    return _result("location", "מיקום", UNAVAILABLE,
                   what=f"{gps_note}, ומיקום IP לא זמין.",
                   why="אנשי הקשר לא יקבלו מיקום כלל.",
                   fix="בדוק/י חיבור אינטרנט; חבר/י מקלט GPS.", affects_emergency=True,
                   detail=desc["label"])


def check_siren():
    try:
        import winsound  # noqa: F401
        has_sound = True
    except Exception:
        has_sound = False
    try:
        import pycaw  # noqa: F401
        has_volume = True
    except Exception:
        has_volume = False
    if not has_sound:
        return _result("siren", "סירנה", UNAVAILABLE, what="winsound לא זמין (לא Windows?).",
                       why="לא תושמע סירנה.", fix="הפעל/י ב-Windows.", affects_emergency=True)
    if not has_volume:
        return _result("siren", "סירנה", LIMITED, what="pycaw לא מותקן - הווליום לא יוגבר אוטומטית.",
                       why="אם המחשב מושתק, הסירנה לא תישמע.", fix="pip install pycaw comtypes",
                       affects_emergency=True)
    return _result("siren", "סירנה", NOT_VERIFIED,
                   what="הרכיבים קיימים; הסירנה לא הושמעה בבדיקה (כדי לא להבהיל).",
                   detail="winsound + pycaw זמינים")


def check_camera():
    try:
        import cv2  # noqa: F401
    except Exception:
        return _result("camera", "מצלמה", UNAVAILABLE, what="opencv-python לא מותקן.",
                       why="לא יצולמו תמונות באירוע.", fix="pip install opencv-python")
    return _result("camera", "מצלמה", NOT_VERIFIED,
                   what="הספרייה מותקנת; המצלמה לא הופעלה בבדיקה (כדי לא להדליק אותה ללא צורך).")


def check_hotkeys():
    try:
        import keyboard  # noqa: F401
    except Exception:
        return _result("hotkeys", "קיצורי מקלדת גלובליים", UNAVAILABLE,
                       what="חבילת keyboard לא מותקנת.",
                       why=f"{config.TRIGGER_HOTKEY}/{config.PANIC_HOTKEY} לא יפעילו SOS מחוץ לחלון.",
                       fix="pip install keyboard", affects_emergency=True)
    return _result("hotkeys", "קיצורי מקלדת גלובליים", OK,
                   detail=f"SOS={config.TRIGGER_HOTKEY} · PANIC={config.PANIC_HOTKEY}")


def _windows_toasts_enabled():
    """None = לא ידוע. Windows שומר את מתג 'התראות' הכללי ואת המתג לכל אפליקציה ב-HKCU."""
    try:
        import winreg
        import windows_notify
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\PushNotifications") as k:
            if winreg.QueryValueEx(k, "ToastEnabled")[0] == 0:
                return False
    except OSError:
        pass
    try:
        import winreg
        import windows_notify
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Notifications"
                                                      rf"\Settings\{windows_notify.APP_ID}") as k:
            if winreg.QueryValueEx(k, "Enabled")[0] == 0:
                return False
    except OSError:
        pass
    return True


def check_notifications():
    """התראות Windows (Toast) כשהאפליקציה ממוזערת. לא שולחים התראת בדיקה מתוך בדיקת התקינות."""
    import windows_notify
    if not windows_notify.available():
        return _result("notifications", "התראות Windows", UNAVAILABLE, what="לא מערכת Windows",
                       why="התראות יוצגו רק בתוך חלון LifeGuard.", fix="-", affects_emergency=False)
    if _windows_toasts_enabled() is False:
        return _result("notifications", "התראות Windows", LIMITED,
                       what="ההתראות של Windows (או של LifeGuard) כבויות בהגדרות המערכת.",
                       why="כשהחלון ממוזער לא תראה/י התראת חירום נכנסת או הזמנה.",
                       fix="הגדרות ← מערכת ← הודעות: להפעיל הודעות ואת LifeGuard.", affects_emergency=True)
    st = windows_notify.status()
    if st.get("last_error"):
        return _result("notifications", "התראות Windows", LIMITED, what=f"שליחה אחרונה נכשלה: {st['last_error']}",
                       why="כשהחלון ממוזער ייתכן שלא תראה/י התראה נכנסת.",
                       fix="בדוק/י ש-PowerShell לא חסום במחשב.", affects_emergency=True)
    if st.get("last_ok"):
        return _result("notifications", "התראות Windows", OK,
                       detail=f"נשלחו {st['sent']} התראות מערכת בהפעלה הזו. 'נמסר למערכת' אינו מבטיח שהוצג - "
                              "מצב 'נא לא להפריע' יכול להסתיר.")
    return _result("notifications", "התראות Windows", NOT_VERIFIED,
                   what="פעיל ומוגדר, אך טרם נשלחה התראה בהפעלה הזו.",
                   why="התראה נשלחת רק כשהחלון ממוזער/ברקע ומגיעה התראת חירום או הזמנה.",
                   fix="אפשר לבדוק: מזער/י את החלון ובקש/י מאיש קשר לשלוח 'בדיקת שליחה'. "
                       "מצב 'נא לא להפריע' של Windows עלול להסתיר התראות.", affects_emergency=False)


def check_backup_channels(engine=None):
    """גיבויים למסירה: SMS (ספק בשרת + מספר מאומת) ו-Web Push לטלפון (PWA). לפי השרת בפועל."""
    import backend_client
    client = _client_of(engine)
    if client is None or not client.token:
        return _result("backup_channels", "ערוצי גיבוי (SMS / טלפון)", NOT_VERIFIED,
                       what="לא מחובר לשרת - לא ניתן לבדוק.", fix="התחבר/י לשרת.", affects_emergency=False)
    try:
        me = client.me()
        push = client._req("GET", "/push/status")
    except backend_client.BackendError as exc:
        return _result("backup_channels", "ערוצי גיבוי (SMS / טלפון)", NOT_VERIFIED, what=f"לא ניתן לבדוק: {exc.message}",
                       affects_emergency=False)
    sms = me.get("sms") or {}
    parts = [f"מכשירי טלפון רשומים להתראות Push: {push.get('devices', 0)}"]
    if not sms.get("configured"):
        parts.append("SMS: לא מוגדר בשרת")
        return _result("backup_channels", "ערוצי גיבוי (SMS / טלפון)", LIMITED,
                       what="לשרת אין ספק SMS - אין גיבוי SMS כשאפליקציה של איש קשר לא מאשרת קבלה.",
                       why="אם האפליקציה של איש הקשר סגורה ואין לו טלפון רשום ל-Push - הוא יראה את ההתראה רק כשיפתח.",
                       fix="מנהל השרת: להגדיר Twilio (DEPLOY_HE.md). כל אחד: להתקין את LifeGuard בטלפון ולאפשר התראות.",
                       affects_emergency=False, detail=" · ".join(parts))
    parts.append("SMS: מוגדר" + (" · הטלפון שלך מאומת" if me.get("phone_verified") else " · הטלפון שלך לא מאומת"))
    return _result("backup_channels", "ערוצי גיבוי (SMS / טלפון)", OK if me.get("phone_verified") else LIMITED,
                   what="" if me.get("phone_verified") else "הטלפון שלך לא מאומת - לא תקבל/י SMS גיבוי כשמישהו בחירום.",
                   fix="" if me.get("phone_verified") else "הגדרות ← טלפון ← שלח קוד אימות.",
                   affects_emergency=False, detail=" · ".join(parts))


def check_background(engine=None):
    import connectivity_module
    parts, problems = [], []
    if connectivity_module.is_monitor_running():
        parts.append("ניטור חיבור פעיל")
    else:
        problems.append("ניטור החיבור אינו רץ")
    if engine is not None:
        if engine.is_safety_timer_active():
            parts.append("טיימר בטיחות פעיל")
        parts.append(f"מצב מנוע: {engine.state.name}")
    if problems:
        return _result("background", "תהליכי רקע", LIMITED, what="; ".join(problems),
                       why="מצב החיבור המוצג עלול להיות לא עדכני.", fix="הפעל/י מחדש את LifeGuard.",
                       detail="; ".join(parts))
    return _result("background", "תהליכי רקע", OK, detail="; ".join(parts))


def run_all(engine=None, username=None, include_hardware=True):
    """מריץ את כל הבדיקות. חלקן לוקחות כמה שניות (רשת/מיקרופון) - להריץ ב-thread."""
    checks = [
        lambda: check_delivery_channels(username, engine),
        check_internet,
        lambda: check_central_backend(engine),
        lambda: check_backup_channels(engine),
        check_databases,
        check_storage,
        check_location,
        lambda: check_voice_recognition(engine, username),
        check_hotkeys,
        check_siren,
        check_camera,
        check_notifications,
        lambda: check_background(engine),
    ]
    if include_hardware:
        checks.insert(4, check_microphone)
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as exc:  # בדיקה שקרסה היא עצמה ממצא - לא מסתירים אותה
            results.append(_result("check_error", getattr(fn, "__name__", "בדיקה"), UNAVAILABLE,
                                   what=f"הבדיקה עצמה נכשלה: {exc}", affects_emergency=False))
    return results


def summarize(results):
    """סטטוס כולל: unavailable אם רכיב חירום לא זמין; limited אם משהו מוגבל; אחרת ok."""
    if any(r["status"] == UNAVAILABLE and r["affects_emergency"] for r in results):
        return UNAVAILABLE
    if any(r["status"] in (UNAVAILABLE, LIMITED) for r in results):
        return LIMITED
    return OK
