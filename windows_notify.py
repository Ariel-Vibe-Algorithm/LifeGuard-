# -*- coding: utf-8 -*-
"""
windows_notify.py
התראות מערכת של Windows (Toast) - כשהאפליקציה ממוזערת או ברקע, התראת חירום נכנסת או הזמנה
מוצגות גם במרכז ההודעות של Windows, ולא רק בתוך החלון.

- בלי תלויות חדשות: WinRT (Windows.UI.Notifications) דרך Windows PowerShell 5.1 שמובנה ב-Windows 10/11.
- מזהה אפליקציה (AUMID) "LifeGuard.Alerts" נרשם ב-HKCU (לא דורש הרשאות מנהל), כדי שההודעה
  תופיע בשם LifeGuard ולא בשם PowerShell.
- נקרא רק עבור התראות שהגיעו מהשרת (כלומר אירוע שהשרת כבר שמר). לעולם לא במצב מעקב שקט/כפייה.
- launch (אופציונלי): URI בפרוטוקול lifeguard:// - לחיצה על ההודעה מפעילה את הפרוטוקול
  (activationType="protocol") ופותחת את האפליקציה על האירוע. ראה agent_bridge.register_protocol().
- כנות: "נשלח למערכת ההפעלה" != "המשתמש ראה". Focus Assist / הגדרות התראות של Windows יכולים
  להסתיר את ההודעה - ראה status().
"""

import base64
import subprocess
import sys
import threading
import time
from xml.sax.saxutils import escape

APP_ID = "LifeGuard.Alerts"
APP_NAME = "LifeGuard"
_state = {"registered": False, "last_ok": None, "last_error": None, "sent": 0}
_lock = threading.Lock()

_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{xml_b64}'))
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
$toast.Tag = '{tag}'
$toast.Group = 'lifeguard'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


def available():
    return sys.platform == "win32"


def _register_app_id():
    """רישום AUMID לאפליקציה לא-ארוזה (HKCU\\Software\\Classes\\AppUserModelId)."""
    if _state["registered"] or not available():
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "ShowInSettings", 0, winreg.REG_DWORD, 1)
    _state["registered"] = True


LAUNCH_PREFIX = "lifeguard://"


def _safe_launch(launch):
    """רק URI של הפרוטוקול שלנו, קצר, בלי תווי בקרה. אחרת - בלי launch (לחיצה רגילה)."""
    if not launch or not isinstance(launch, str):
        return None
    if not launch.lower().startswith(LAUNCH_PREFIX) or len(launch) > 200:
        return None
    if any(ord(c) < 0x21 or ord(c) > 0x7e for c in launch) or any(c in launch for c in "\"'<>`"):
        return None
    return launch


def build_xml(title, body, urgent=False, launch=None):
    """urgent: ההודעה נשארת על המסך עד שנסגרת (scenario=reminder) - להתראת חירום נכנסת.
    launch: URI lifeguard://... שמופעל בלחיצה על ההודעה (activationType=protocol)."""
    launch = _safe_launch(launch)
    scenario = ' scenario="reminder"' if urgent else ""
    launch_attr = f' launch="{escape(launch)}" activationType="protocol"' if launch else ""
    open_action = (f'<action activationType="protocol" arguments="{escape(launch)}" content="פתח/י"/>'
                   if launch and urgent else "")
    actions = (f'<actions>{open_action}<action activationType="system" arguments="dismiss" content=""/></actions>'
               if urgent else "")
    audio = '<audio src="ms-winsoundevent:Notification.Looping.Alarm"/>' if urgent else ""
    return (f'<toast{scenario}{launch_attr} duration="{"long" if urgent else "short"}">'
            f'<visual><binding template="ToastGeneric">'
            f'<text hint-maxLines="2">{escape(title)}</text><text>{escape(body)}</text>'
            f'</binding></visual>{actions}{audio}</toast>')


def notify(title, body, urgent=False, tag="lg", wait=False, launch=None):
    """שולח Toast ברקע. מחזיר True/False רק כש-wait=True (בדיקות/בדיקת תקינות).
    launch: URI lifeguard://... אופציונלי שנפתח בלחיצה על ההודעה."""
    if not available():
        _state["last_error"] = "לא Windows"
        return False
    result = {}

    def run():
        try:
            _register_app_id()
            xml_b64 = base64.b64encode(build_xml(title, body, urgent, launch).encode("utf-8")).decode("ascii")
            script = _PS.format(xml_b64=xml_b64, tag=escape(str(tag))[:16].replace("'", ""), app_id=APP_ID)
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                   "-EncodedCommand", encoded], capture_output=True, timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ok = proc.returncode == 0
            with _lock:
                if ok:
                    _state.update(last_ok=time.time(), last_error=None)
                    _state["sent"] += 1
                else:
                    err = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
                    _state["last_error"] = (err[-1] if err else f"exit {proc.returncode}")[:200]
            result["ok"] = ok
        except Exception as exc:  # noqa: BLE001 - לא מפילים את האפליקציה בגלל התראת מערכת
            with _lock:
                _state["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
            result["ok"] = False

    if wait:
        run()
        return result.get("ok", False)
    threading.Thread(target=run, name="LG-Toast", daemon=True).start()
    return None


def remove(tag=None):
    """מסיר הודעה (למשל אירוע שהסתיים) ממרכז ההודעות. tag=None -> כל ההודעות של LifeGuard."""
    if not available():
        return
    if tag is None:
        cmd = f"[Windows.UI.Notifications.ToastNotificationManager]::History.Clear('{APP_ID}')"
    else:
        safe = escape(str(tag))[:16].replace("'", "")
        cmd = f"[Windows.UI.Notifications.ToastNotificationManager]::History.Remove('{safe}', 'lifeguard', '{APP_ID}')"
    script = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
              "ContentType = WindowsRuntime] > $null\n" + cmd)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")

    def run():
        try:
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                           capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def status():
    with _lock:
        return dict(_state)
