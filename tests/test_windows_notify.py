# -*- coding: utf-8 -*-
"""
התראות Windows: בדיקה אמיתית מול מערכת ההפעלה (ההודעה נכנסת למרכז ההודעות של Windows ונמחקת
מיד), ובדיקות החלטה - מתי האפליקציה שולחת התראת מערכת ומתי לא (חלון בפוקוס / מעקב שקט).
"""
import subprocess
import sys
import time

import pytest

import windows_notify


def _history_count():
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]"
          f" > $null; ([Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory('{windows_notify.APP_ID}')).Count")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30)
    return int((out.stdout or "0").strip() or 0)


@pytest.mark.real_toast
@pytest.mark.skipif(sys.platform != "win32", reason="Windows בלבד")
def test_real_toast_reaches_windows_action_center():
    before = _history_count()
    assert windows_notify.notify("LifeGuard · בדיקה אוטומטית", "בדיקת התראות מערכת - נמחקת מיד", tag="selftest",
                                 wait=True), windows_notify.status()
    assert _history_count() == before + 1, "Windows לא רשם את ההודעה במרכז ההודעות"
    windows_notify.remove("selftest")
    end = time.time() + 10
    while time.time() < end and _history_count() != before:
        time.sleep(0.3)
    assert _history_count() == before


def test_xml_is_escaped():
    xml = windows_notify.build_xml("<b>&", "a\"b'<", urgent=True)
    assert "<b>" not in xml.split("<text")[1] and "&amp;" in xml and 'scenario="reminder"' in xml
