# -*- coding: utf-8 -*-
"""
map_tiles.py
באג שתוקן: tkintermapview שולח לשרתי האריחים של OpenStreetMap את ה-User-Agent הגנרי
"TkinterMapView", ו-OSM חוסם אותו לפי מדיניות השימוש שלו - כל אריח שחזר היה תמונת
"403 Access blocked". כלומר המפה (כולל מפת המקום הבטוח במסך ה-SOS) מעולם לא הוצגה בפועל.

התיקון: כל בקשת אריח מ-tkintermapview יוצאת עם User-Agent מזהה של LifeGuard
(config.HTTP_USER_AGENT), ומשתמשים בכתובת האריחים העדכנית של OSM (ללא תת-דומיין a/b/c).
"""

import config

OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_patched = False


class _RequestsWithUA:
    """עוטף את מודול requests ש-tkintermapview משתמש בו, ומחליף רק את ה-User-Agent."""

    def __init__(self, real):
        self._real = real

    def get(self, url, *args, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers["User-Agent"] = config.HTTP_USER_AGENT
        kwargs.setdefault("timeout", 10)
        return self._real.get(url, *args, headers=headers, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def install():
    """מתקין פעם אחת. בטוח לקרוא גם כשהספרייה לא מותקנת."""
    global _patched
    if _patched:
        return True
    try:
        import tkintermapview.map_widget as mw
    except Exception:
        return False
    if not isinstance(mw.requests, _RequestsWithUA):
        mw.requests = _RequestsWithUA(mw.requests)
    _patched = True
    return True


def configure(widget):
    """לכל מופע מפה: שרת האריחים העדכני של OSM."""
    install()
    try:
        widget.set_tile_server(OSM_TILE_URL, max_zoom=19)
    except Exception:
        pass
    return widget
