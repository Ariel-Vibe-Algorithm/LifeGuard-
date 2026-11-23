# -*- coding: utf-8 -*-
"""
appearance.py - הגדרות מראה (בהיר / כהה / לפי Windows) והפחתת תנועה.

נשמרות פעמיים: למשתמש/ת, ול"מכשיר" (__device__) - כדי שגם מסך ההתחברות, שנבנה לפני שידוע מי מתחבר/ת,
ייפתח במראה האחרון שנבחר. ההחלפה נכנסת לתוקף בהפעלה הבאה (הממשק נבנה עם הצבעים פעם אחת).
"""

import incident_store

DEVICE_OWNER = "__device__"


def device_setting(key, default=None, username=None):
    try:
        return incident_store.get_setting(username or DEVICE_OWNER, key, default)
    except Exception:  # noqa: BLE001
        return default


def load_theme_setting(username=None):
    """"light" / "dark" / "system" - של המשתמש/ת, ואם אין - של המכשיר (הבחירה האחרונה)."""
    v = device_setting("theme", None, username) if username else None
    return v or device_setting("theme", "light")


def load_reduce_motion(username=None):
    v = device_setting("reduce_motion", None, username) if username else None
    return v if v is not None else device_setting("reduce_motion", None)


def save_appearance(username, theme=None, reduce_motion=None):
    for owner in (username, DEVICE_OWNER):
        if not owner:
            continue
        try:
            if theme is not None:
                incident_store.set_setting(owner, "theme", theme)
            if reduce_motion is not None:
                incident_store.set_setting(owner, "reduce_motion", reduce_motion)
        except Exception:  # noqa: BLE001
            pass
