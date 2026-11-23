# -*- coding: utf-8 -*-
"""
map_hidpi.py - מפה חדה וקריאה במסכי DPI גבוה, וסמנים בסגנון המערכת.

tkintermapview מצייר אריחים של 256 פיקסלים ופיקסל-לפיקסל. אחרי שהתהליך DPI-aware (ui_theme.enable_dpi_awareness)
אריח של 256 פיקסלים פיזיים במסך 4K ב-250% נראה זעיר (שמות רחובות בגודל ~4pt). לכן:
- גודל האריח במפה = 256 x מקדם ה-DPI, וכל אריח מוגדל (LANCZOS) לגודל הזה - אותו גודל פיזי כמו קודם, אבל
  בלי שכל הממשק סביבו יימתח. (אין לשרת האריחים של OSM אריחי @2x.)
- סמנים: נקודה כחולה עם טבעת לבנה ל"אני" (כמו במפות של אפל), סיכה נייבי ליעד - תמונות חלקות במקדם ה-DPI,
  במקום המשולשים של tkintermapview (משוננים וקטנים מדי).
"""

import ui_theme as T

_patched = False


class _ImageProxy:
    """עוטף את PIL.Image בתוך tkintermapview: אריח 256 שנפתח -> מוגדל לגודל האריח של המפה."""

    def __init__(self, real):
        self._real = real

    def open(self, *a, **k):
        img = self._real.open(*a, **k)
        size = tile_size()
        if size != 256 and getattr(img, "size", None) == (256, 256):
            try:
                img = img.convert("RGB").resize((size, size), self._real.LANCZOS)
            except Exception:  # noqa: BLE001 - אריח לא תקין: מחזירים כמו שהוא
                pass
        return img

    def __getattr__(self, name):
        return getattr(self._real, name)


def tile_size():
    return max(256, int(round(256 * T.dpi_factor())))


def install():
    global _patched
    if _patched:
        return True
    try:
        import tkintermapview.map_widget as mw
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(mw.Image, _ImageProxy):
        mw.Image = _ImageProxy(mw.Image)
    _patched = True
    return True


def prepare(widget):
    """לכל מפה: גודל אריח לפי DPI (אחרי map_tiles.configure, שקובע את שרת האריחים)."""
    if T.dpi_factor() != 1.0 and install():
        try:
            widget.set_tile_server(widget.tile_server, tile_size=tile_size(), max_zoom=getattr(widget, "max_zoom", 19))
        except Exception:  # noqa: BLE001
            pass
    try:
        _raw = T.raw_configure
        _raw(widget.canvas, bg=T.MAP_BG)
    except Exception:  # noqa: BLE001
        pass
    return widget


# ---------------------------------------------------------------- סמנים
def _me_pil(d, fill, halo):
    from PIL import Image, ImageDraw
    S = d * T.SS
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    hr = T._rgb(halo)
    dr.ellipse((0, 0, S - 1, S - 1), fill=hr + (60,))
    c, r = S / 2, S * 0.30
    dr.ellipse((c - r - S * 0.07, c - r - S * 0.07, c + r + S * 0.07, c + r + S * 0.07), fill=(255, 255, 255, 255))
    dr.ellipse((c - r, c - r, c + r, c + r), fill=T._rgb(fill) + (255,))
    return im.resize((d, d), Image.LANCZOS)


def _pin_pil(w, h, fill):
    from PIL import Image, ImageDraw
    W, H = w * T.SS, h * T.SS
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    r = W / 2 - T.SS
    cx, cy = W / 2, r + T.SS
    col = T._rgb(fill) + (255,)
    dr.polygon([(cx - r * 0.62, cy + r * 0.62), (cx, H - 1), (cx + r * 0.62, cy + r * 0.62)], fill=col)
    dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col, outline=(255, 255, 255, 255), width=int(T.SS * T.px(2)))
    ir = r * 0.36
    dr.ellipse((cx - ir, cy - ir, cx + ir, cy + ir), fill=(255, 255, 255, 255))
    return im.resize((w, h), Image.LANCZOS)


def _icon(widget, key, make):
    from PIL import ImageTk
    cache = widget.__dict__.setdefault("_lg_icons", {})
    if key not in cache:
        cache[key] = ImageTk.PhotoImage(make(), master=widget)
    return cache[key]


MARKER_TEXT = "#0A1F44"      # על אריחי המפה (בהירים תמיד) - נייבי קריא גם במצב כהה


def me_marker(map_widget, lat, lon, text=None):
    """"אני": נקודה כחולה עם טבעת לבנה והילה."""
    if not T.HAS_PIL:
        return map_widget.set_marker(lat, lon, text=text)
    d = T.px(26)
    icon = _icon(map_widget, ("me", d, T.BLUE), lambda: _me_pil(d, T.BLUE, T.BLUE))
    m = map_widget.set_marker(lat, lon, text=text, icon=icon, icon_anchor="center", text_color=MARKER_TEXT,
                              font=T.F(10, "bold"))
    m.text_y_offset = -d // 2
    try:
        m.draw()
    except Exception:  # noqa: BLE001
        pass
    return m


def place_marker(map_widget, lat, lon, text=None, color=None):
    """יעד / מקום בטוח: סיכה נייבי (או בצבע אחר) עם נקודה לבנה."""
    if not T.HAS_PIL:
        return map_widget.set_marker(lat, lon, text=text)
    w, h = T.px(28), T.px(38)
    col = color or "#0A1F44"
    icon = _icon(map_widget, ("pin", w, h, col), lambda: _pin_pil(w, h, col))
    m = map_widget.set_marker(lat, lon, text=text, icon=icon, icon_anchor="s", text_color=MARKER_TEXT,
                              font=T.F(10, "bold"))
    m.text_y_offset = -h - T.px(2)
    try:
        m.draw()
    except Exception:  # noqa: BLE001
        pass
    return m
