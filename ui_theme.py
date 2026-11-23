# -*- coding: utf-8 -*-
"""
ui_theme.py
מערכת העיצוב של LifeGuard ל-Windows (Tk): כחול נייבי, לבן וכחול - לפי design/tokens.json ו-DESIGN_SYSTEM_HE.md.

מה יש כאן, לפי הסדר:
1. חדות במסכי DPI גבוה (enable_dpi_awareness / px): בלי מודעות DPI, Windows מותח את כל החלון כתמונה (פי 2.5 במסך
   4K ב-250%) - טקסט מטושטש ועיגולים משוננים (נמדד). התהליך מוצהר DPI-aware *לפני* יצירת Tk, הגופנים (בנקודות)
   מתחדדים דרך "tk scaling", וכל מידה בפיקסלים (padx, pady, bd, highlightthickness, width/height של Frame/Canvas,
   מחרוזות geometry, place x/y) מוכפלת במקדם ה-DPI במקום אחד (_install_px). רוחב/גובה של Label/Button/Entry/Text
   הם *תווים* ולא מוכפלים. גופנים לא מוכפלים פעמיים. LIFEGUARD_DPI_AWARE=0 מבטל; LIFEGUARD_DPI_SCALE=2.5 מאלץ מקדם.
2. כיווניות (RTL) לכל טקסט עברי ב-Label/Button/Canvas (נבדק בצילומי מסך - ראו docstrings).
3. ערכות צבע: בהירה וכהה אמיתית (apply_theme: light / dark / system = AppsUseLightTheme ברישום של Windows).
   ההחלפה נכנסת לתוקף מלא בהפעלה הבאה (הווידג'טים נבנים עם הצבעים פעם אחת).
4. גרפיקה חדה: כרטיסים, צללים, כפתורים, צ'יפים, כפתור ה-SOS, מתג ובקר מקטעים מצוירים כתמונות Pillow
   (דגימת-יתר פי 4 והקטנה, במקדם ה-DPI האמיתי, במטמון לפי גודל/צבע) - במקום פוליגונים משוננים של Tk.

נגישות: ניגודיות 4.5:1 לטקסט (הטוקנים נבדקו), טבעת פוקוס בכל רכיב לחיץ, Enter/רווח מפעילים, גודל טקסט
(set_scale) נפרד ממקדם ה-DPI, ו"הפחתת תנועה" (REDUCED_MOTION) מבטלת אנימציות. אין שום אנימציה במסלול ה-SOS.
מסך ה-SOS משתמש באותה פלטה של מסך הבית; מצב החירום מסומן בפס ובתג אדום. EMERGENCY_* נשמרו לתאימות בלבד.
"""

import os
import re
import sys
import weakref
import tkinter as tk
from collections import OrderedDict

# =====================================================================================
# 1. DPI: חדות אמיתית במסכים עם הגדלת תצוגה
# =====================================================================================
_dpi = 1.0          # פיקסלים פיזיים לכל פיקסל "לוגי" (96 DPI). 1.0 = בלי שינוי (בדיקות, מסך 100%)


def _system_dpi():
    try:
        import ctypes
        try:
            return int(ctypes.windll.user32.GetDpiForSystem())
        except (AttributeError, OSError):
            hdc = ctypes.windll.user32.GetDC(0)
            try:
                return int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))    # LOGPIXELSX
            finally:
                ctypes.windll.user32.ReleaseDC(0, hdc)
    except Exception:  # noqa: BLE001
        return 96


def enable_dpi_awareness():
    """
    לקרוא *לפני* tk.Tk() (main.main). מצהיר על התהליך כ-DPI aware (Per-Monitor, ואם לא - System), ומחשב את
    המקדם = DPI אמיתי / 96. מחזיר את המקדם. LIFEGUARD_DPI_AWARE=0 - בלי שינוי (Windows ימתח את החלון כמו קודם).
    """
    global _dpi
    if os.environ.get("LIFEGUARD_DPI_AWARE", "1") == "0":
        _dpi = 1.0
        return _dpi
    aware = False
    if sys.platform == "win32":
        try:
            import ctypes
            try:
                hr = ctypes.windll.shcore.SetProcessDpiAwareness(2)          # PROCESS_PER_MONITOR_DPI_AWARE
                if hr in (0, -2147024891):                                     # S_OK / E_ACCESSDENIED (כבר הוגדר)
                    aware = True
                else:
                    aware = ctypes.windll.shcore.SetProcessDpiAwareness(1) in (0, -2147024891)
            except (AttributeError, OSError):
                aware = bool(ctypes.windll.user32.SetProcessDPIAware())
        except Exception:  # noqa: BLE001
            aware = False
    forced = os.environ.get("LIFEGUARD_DPI_SCALE")
    if forced:
        try:
            _dpi = max(0.5, min(4.0, float(forced)))
        except ValueError:
            _dpi = 1.0
    elif aware:
        _dpi = max(1.0, _system_dpi() / 96.0)
    return _dpi


def set_dpi_factor(f):
    """לבדיקות: מקדם DPI מאולץ בלי לגעת במערכת."""
    global _dpi
    _dpi = float(f)


def dpi_factor():
    return _dpi


def px(n):
    """מידה לוגית (פיקסלים ב-96 DPI) -> פיקסלים פיזיים. מחרוזות ("2m") ו-0 לא משתנים."""
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        return n
    if n == 0 or _dpi == 1.0:
        return n if isinstance(n, int) else int(round(n))
    v = int(round(n * _dpi))
    return v if v else (1 if n > 0 else -1)


def unpx(n):
    """פיקסלים פיזיים -> לוגיים (לקוד שמודד winfo_width ומחזיר מידה לאפשרות שתוכפל שוב)."""
    return int(round(n / _dpi)) if _dpi != 1.0 else int(n)


_PX_ALWAYS = {"padx", "pady", "bd", "borderwidth", "highlightthickness", "insertwidth", "selectborderwidth",
              "insertborderwidth", "activeborderwidth", "sliderlength", "length", "spacing1", "spacing2",
              "spacing3", "elementborderwidth"}
# רק כאן width/height הם פיקסלים. ב-Label/Button/Entry/Text/Listbox/Spinbox/Checkbutton הם תווים/שורות.
_PX_SIZE_WIDGETS = {"frame", "canvas", "toplevel", "labelframe", "message", "scrollbar", "scale",
                    "ttk::frame", "ttk::labelframe", "ttk::progressbar"}
_GEOM_PAD = {"padx", "pady", "ipadx", "ipady"}
_PLACE_PX = {"x", "y", "width", "height"}


def _scale_val(v):
    if isinstance(v, (tuple, list)):
        return type(v)(px(x) for x in v)
    if isinstance(v, str):
        parts = v.split()
        if parts and all(p.lstrip("-").isdigit() for p in parts):
            return " ".join(str(px(int(p))) for p in parts)
        return v
    return px(v)


def _scale_opts(widget_name, opts, keys=None):
    if not opts or _dpi == 1.0:
        return opts
    out = dict(opts)
    sized = widget_name in _PX_SIZE_WIDGETS
    for k, v in opts.items():
        kk = k[:-1] if k.endswith("_") else k
        if keys is not None:
            hit = kk in keys
        else:
            hit = kk in _PX_ALWAYS or (sized and kk in ("width", "height"))
        if hit and v is not None:
            out[k] = _scale_val(v)
    return out


def _scale_geometry(spec):
    if not isinstance(spec, str) or _dpi == 1.0:
        return spec
    m = re.fullmatch(r"(?:(\d+)x(\d+))?(?:([+-])(-?\d+)([+-])(-?\d+))?", spec.strip())
    if not m:
        return spec
    w, h, sx, x, sy, y = m.groups()
    out = f"{px(int(w))}x{px(int(h))}" if w else ""
    if sx:
        out += f"{sx}{px(int(x))}{sy}{px(int(y))}"
    return out


_raw_configure = tk.Misc.configure          # בלי הכפלת DPI - לערכים שכבר נמדדו בפיקסלים פיזיים
_tk_roots = weakref.WeakSet()


def raw_configure(widget, **kw):
    return _raw_configure(widget, **kw)


def _install_px():
    if getattr(tk, "_lg_px", False):
        return
    tk._lg_px = True
    orig_bw = tk.BaseWidget.__init__

    def bw_init(self, master, widgetName, cnf={}, kw={}, extra=()):   # noqa: B006 - חתימת tkinter
        if _dpi != 1.0:
            cnf = _scale_opts(widgetName, cnf) if isinstance(cnf, dict) else cnf
            kw = _scale_opts(widgetName, kw)
        orig_bw(self, master, widgetName, cnf, kw, extra)
    tk.BaseWidget.__init__ = bw_init

    orig_conf = tk.Misc.configure

    def configure(self, cnf=None, **kw):
        if _dpi != 1.0 and (isinstance(cnf, dict) or kw):
            name = getattr(self, "widgetName", "toplevel")
            if isinstance(cnf, dict):
                cnf = _scale_opts(name, cnf)
            kw = _scale_opts(name, kw)
        return orig_conf(self, cnf, **kw)
    tk.Misc.configure = tk.Misc.config = configure

    def geom(orig, keys):
        def f(self, cnf={}, **kw):   # noqa: B006
            if _dpi != 1.0:
                cnf = _scale_opts("", cnf, keys) if isinstance(cnf, dict) else cnf
                kw = _scale_opts("", kw, keys)
            return orig(self, cnf, **kw)
        return f
    tk.Pack.pack = tk.Pack.pack_configure = geom(tk.Pack.pack_configure, _GEOM_PAD)
    tk.Grid.grid = tk.Grid.grid_configure = geom(tk.Grid.grid_configure, _GEOM_PAD)
    tk.Place.place = tk.Place.place_configure = geom(tk.Place.place_configure, _PLACE_PX)

    orig_geo = tk.Wm.wm_geometry

    def wm_geometry(self, newGeometry=None):
        return orig_geo(self, _scale_geometry(newGeometry) if newGeometry else newGeometry)
    tk.Wm.wm_geometry = tk.Wm.geometry = wm_geometry

    for name in ("wm_minsize", "wm_maxsize"):
        orig = getattr(tk.Wm, name)

        def sized(self, width=None, height=None, _o=orig):
            if width is not None and height is not None:
                return _o(self, px(width), px(height))
            return _o(self, width, height)
        setattr(tk.Wm, name, sized)
        setattr(tk.Wm, name[3:], sized)

    # המסך בפיקסלים לוגיים - קוד שמחשב "min(1280, רוחב המסך)" נשאר עקבי עם המידות הלוגיות
    osw, osh = tk.Misc.winfo_screenwidth, tk.Misc.winfo_screenheight
    tk.Misc.winfo_screenwidth = lambda self: unpx(osw(self))
    tk.Misc.winfo_screenheight = lambda self: unpx(osh(self))

    orig_tk = tk.Tk.__init__

    def tk_init(self, *a, **k):
        orig_tk(self, *a, **k)
        if _dpi != 1.0:
            try:
                self.tk.call("tk", "scaling", _dpi * 96.0 / 72.0)      # נקודות -> פיקסלים פיזיים: גופן חד
            except tk.TclError:
                pass
        _tk_roots.add(self)
        try:
            style_root(self)
        except Exception:  # noqa: BLE001 - עיצוב לא מפיל הפעלה
            pass
    tk.Tk.__init__ = tk_init


_install_px()

# =====================================================================================
# 2. כיווניות (RTL) לכל הטקסט העברי
# =====================================================================================
# Tk מציג כל שורה בכיוון בסיס משמאל-לימין: פיסוק בסוף משפט עברי קופץ להתחלה ("?הכל בסדר",
# ".זוהתה נפילה"), וטקסט מעורב עם LifeGuard/SOS מתסדר הפוך. הטמעת RTL (RLE...PDF) סביב כל שורה
# מתקנת את כל המקרים (נבדק בצילומי מסך). מותקן פעם אחת לכל הווידג'טים; cget מחזיר טקסט נקי.
_RLE, _PDF = "‫", "‬"
_HEB = re.compile("[֐-׿]")


def _is_symbol(ch):
    """תו שה-Tk מצייר בגופן אחר (אמוג'י / סמלים): שם הוא שובר את השורה לחלקים נפרדים."""
    cp = ord(ch)
    return 0x2190 <= cp <= 0x2BFF or cp >= 0x1F000 or cp in (0xFE0F, 0x200D, 0x20E3)


def _rtl_line(line):
    """
    באג (נמדד בצילומי מסך): Tk ב-Windows מצייר שורה בחלקים בכל מקום שבו הוא מחליף גופן (אמוג'י כמו ⚠️ 📍),
    מסדר את החלקים משמאל לימין לפי סדר הטקסט, ומחיל כיוון RTL על כל חלק בנפרד - וסימן ה-RLE חל רק על החלק
    הראשון. לכן "⚠️ תל אביב | מיקום IP ..." הוצג בסדר מילים הפוך. הפתרון: מחלקים לחלקי טקסט/סמלים, מסדרים
    אותם בסדר התצוגה (מימין לשמאל = הפוך), ועוטפים כל חלק טקסט ב-RLE...PDF משלו.
    """
    if not any(_is_symbol(c) for c in line):
        return _RLE + line + _PDF
    runs, cur, cur_sym = [], "", None
    for c in line:
        s = _is_symbol(c)
        if cur and s != cur_sym:
            runs.append((cur, cur_sym))
            cur = ""
        cur, cur_sym = cur + c, s
    if cur:
        runs.append((cur, cur_sym))
    return "".join(r if sym else _RLE + r + _PDF for r, sym in reversed(runs))


def rtl(text):
    if not isinstance(text, str) or not _HEB.search(text) or _RLE in text:      # כבר עובד - לא פעמיים
        return text
    return "\n".join(_rtl_line(line) if line else line for line in text.split("\n"))


def plain(text):
    return text.replace(_RLE, "").replace(_PDF, "") if isinstance(text, str) else text


MAX_RTL_LINE_CHARS = 95   # Tk ב-Windows מצייר שורת RTL ארוכה (~110+ תווים) בחלקים בסדר שגוי (נמדד)


def wrap_lines(text, width_px, measure, max_chars=MAX_RTL_LINE_CHARS):
    """שבירת שורות לפי מילים: עד width_px פיקסלים (0 = בלי מגבלת רוחב) ועד max_chars תווים לשורה."""
    out = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            cand = f"{line} {word}" if line else word
            if line and ((width_px and measure(cand) > width_px) or len(cand) > max_chars):
                out.append(line)
                line = word
            else:
                line = cand
        out.append(line)
    return "\n".join(out)


def _install_bidi():
    """
    כל טקסט עברי בווידג'טים: הטמעת RTL לכל שורה + שבירת שורות *ידנית*.
    למה ידנית: wraplength של Tk שובר שורה לוגית אחת שעטופה ב-RLE...PDF - וסוף המשפט "קופץ" לתחילת השורה
    (".ההמחשב ... קליט"), וגם שורה ארוכה מאוד (~110+ תווים) מצוירת בחלקים בסדר שגוי. לכן: הטקסט והרוחב
    המבוקש נשמרים בווידג'ט, השבירה נעשית לפי מדידת הגופן, ו-Tk מקבל wraplength=0. cget("text") /
    cget("wraplength") מחזירים את הערכים המקוריים - קוד ובדיקות קיימים לא רואים הבדל.
    wraplength הוא מידה לוגית: נשמר בפיקסלים פיזיים (px) כי מדידת הגופן היא בפיקסלים פיזיים.
    """
    if getattr(tk, "_lg_bidi", False):
        return
    tk._lg_bidi = True
    from tkinter import font as tkfont

    for cls in (tk.Label, tk.Button, tk.Checkbutton, tk.Radiobutton, tk.Message, tk.LabelFrame):
        orig_init, orig_conf, orig_cget = cls.__init__, cls.configure, cls.cget
        wraps = cls is not tk.LabelFrame and cls is not tk.Message   # Message שובר בעצמו לפי width

        def _render(self, _conf=orig_conf, _cget=orig_cget, _wraps=wraps):
            txt = self.__dict__.get("_lg_text")
            if txt is None:
                return
            wl = self.__dict__.get("_lg_wrap", 0) if _wraps else 0
            if not isinstance(txt, str) or not _HEB.search(txt):
                if _wraps:
                    _raw_configure(self, text=txt, wraplength=wl)
                else:
                    _conf(self, text=txt)
                return
            try:
                f = tkfont.Font(root=self, font=_cget(self, "font"))
                text = wrap_lines(txt, wl, f.measure)
            except tk.TclError:
                text = txt
            if _wraps:
                _raw_configure(self, text=rtl(text), wraplength=0)
            else:
                _conf(self, text=rtl(text))

        def _px(self, v):
            try:
                if v in (None, "", 0, "0"):
                    return 0
                if isinstance(v, (int, float)):
                    return px(v)
                return int(self.winfo_fpixels(v))
            except (tk.TclError, ValueError):
                return 0

        def __init__(self, master=None, cnf=None, _o=orig_init, _r=_render, _wraps=wraps, **kw):
            cnf = dict(cnf or {})
            text = kw.pop("text", cnf.pop("text", None))
            wl = kw.pop("wraplength", cnf.pop("wraplength", None)) if _wraps else None
            _o(self, master, cnf, **kw)
            self.__dict__["_lg_wrap"] = _px(self, wl) if wl is not None else 0
            if text is not None:
                self.__dict__["_lg_text"] = plain(text)
                _r(self)

        def configure(self, cnf=None, _o=orig_conf, _r=_render, _wraps=wraps, **kw):
            if cnf is None and not kw:
                return _o(self)
            if isinstance(cnf, str):            # configure("text") = שאילתה
                return _o(self, cnf)
            merged = dict(cnf or {})
            merged.update(kw)
            changed = False
            if "text" in merged:
                self.__dict__["_lg_text"] = plain(merged.pop("text"))
                changed = True
            if _wraps and "wraplength" in merged:
                self.__dict__["_lg_wrap"] = _px(self, merged.pop("wraplength"))
                changed = True
            if "font" in merged:
                changed = True
            res = _o(self, merged) if merged else None
            if changed and "_lg_text" in self.__dict__:
                _r(self)
            return res

        def cget(self, key, _o=orig_cget, _wraps=wraps):
            if key == "text" and "_lg_text" in self.__dict__:
                return self.__dict__["_lg_text"]
            if key == "wraplength" and _wraps and "_lg_wrap" in self.__dict__:
                return unpx(self.__dict__["_lg_wrap"])
            v = _o(self, key)
            return plain(v) if key == "text" else v

        cls.__init__, cls.configure, cls.config, cls.cget = __init__, configure, configure, cget
        cls.__getitem__ = cget
    orig_ct, orig_ic = tk.Canvas.create_text, tk.Canvas.itemconfigure

    def create_text(self, *args, _o=orig_ct, **kw):
        if "text" in kw:
            kw["text"] = rtl(kw["text"])
        return _o(self, *args, **kw)

    def itemconfigure(self, tag, cnf=None, _o=orig_ic, **kw):
        if "text" in kw:
            kw["text"] = rtl(kw["text"])
        return _o(self, tag, cnf, **kw)

    tk.Canvas.create_text, tk.Canvas.itemconfigure, tk.Canvas.itemconfig = create_text, itemconfigure, itemconfigure


_install_bidi()

# =====================================================================================
# 3. ערכות צבע (design/tokens.json) - בהירה וכהה
# =====================================================================================
# כחול האקסנט ‎#0B63E5 ולא ‎#007AFF של אפל: לבן על ‎#007AFF = 4.0:1 בלבד. כל טקסט עובר 4.5:1 על המשטח שלו.
PALETTES = {
    "light": dict(
        BG="#F4F6FA", CARD_BG="#FFFFFF", CARD_BG_ALT="#EEF2F8", FILL="#E6EBF3", BORDER="#D5DCE7",
        TEXT="#0B1426", TEXT_SECONDARY="#47546A", TEXT_MUTED="#5F6B80", TEXT_ON_DARK="#FFFFFF",
        BLUE="#0B63E5", BLUE_DARK="#0950BA", LIGHT_BLUE="#E3EEFF", LINK="#0B5ACF", ON_ACCENT="#FFFFFF",
        RED="#D70015", RED_DARK="#A50011", RED_BG="#FFE9EA", SOS_FILL="#D70015",
        GREEN="#1E7F3B", GREEN_BG="#E4F5E9", YELLOW="#A44A00", YELLOW_BG="#FFF1DE", ORANGE="#C93400",
        NAVY="#0A1F44", NAVY_2="#132E5C", BRAND="#0A1F44", ON_NAVY="#FFFFFF", ON_NAVY_MUTED="#C3CFE4",
        NAV_BG="#FFFFFF", FOCUS="#0B63E5", SHADOW="#E3E8F0",
        MAP_LOW="#DCE8FB", MAP_MID="#7FA8EC", MAP_HIGH="#0A1F44", ROUTE_ALT="#8592A8", ROUTE_CASING="#FFFFFF",
        MAP_BG="#EEF2F8",
    ),
    "dark": dict(
        BG="#060D1A", CARD_BG="#0F1B2E", CARD_BG_ALT="#16243B", FILL="#1E2E48", BORDER="#26375A",
        TEXT="#EEF2F8", TEXT_SECONDARY="#B2BDD0", TEXT_MUTED="#97A3B8", TEXT_ON_DARK="#FFFFFF",
        BLUE="#4C93FF", BLUE_DARK="#2F7BF0", LIGHT_BLUE="#13294D", LINK="#8DB4FF", ON_ACCENT="#06101F",
        RED="#FF4F45", RED_DARK="#C41A26", RED_BG="#3A1416", SOS_FILL="#D41F2B",
        GREEN="#4ADE80", GREEN_BG="#123222", YELLOW="#FFB547", YELLOW_BG="#3A2710", ORANGE="#FF8A4C",
        NAVY="#0B1A33", NAVY_2="#16243B", BRAND="#8DB4FF", ON_NAVY="#FFFFFF", ON_NAVY_MUTED="#A9B8D3",
        NAV_BG="#0B1526", FOCUS="#8DB4FF", SHADOW="#03070F",
        MAP_LOW="#1A2B47", MAP_MID="#3E6BB8", MAP_HIGH="#B7CEFF", ROUTE_ALT="#7D8AA3", ROUTE_CASING="#0F1B2E",
        MAP_BG="#16243B",
    ),
}
_SHADOW_RGBA = {"light": (10, 31, 68, 34), "dark": (0, 0, 0, 150)}
THEME = "light"
THEME_LABELS = {"light": "בהיר", "dark": "כהה", "system": "לפי Windows"}

# ערכי ברירת המחדל (בהיר) - כדי ש-T.BG וכו' קיימים כבר בייבוא
BG = CARD_BG = CARD_BG_ALT = FILL = BORDER = TEXT = TEXT_SECONDARY = TEXT_MUTED = TEXT_ON_DARK = ""
BLUE = BLUE_DARK = LIGHT_BLUE = LINK = ON_ACCENT = RED = RED_DARK = RED_BG = SOS_FILL = ""
GREEN = GREEN_BG = YELLOW = YELLOW_BG = ORANGE = NAVY = NAVY_2 = BRAND = ON_NAVY = ON_NAVY_MUTED = ""
NAV_BG = FOCUS = SHADOW = MAP_LOW = MAP_MID = MAP_HIGH = ROUTE_ALT = ROUTE_CASING = MAP_BG = ""
STATUS_COLORS, STATUS_BG = {}, {}

# ---------------- פלטת חירום (נפרדת בכוונה, לתאימות) ----------------
EMERGENCY_BG = "#2a0710"
EMERGENCY_CARD = "#3d0d18"
EMERGENCY_TEXT = "#ffffff"
EMERGENCY_MUTED = "#f2b8c0"


def system_prefers_dark():
    """Windows: HKCU\\...\\Themes\\Personalize\\AppsUseLightTheme = 0 -> מצב כהה."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def resolve_theme(name):
    if name == "system":
        return "dark" if system_prefers_dark() else "light"
    return name if name in PALETTES else "light"


def apply_theme(name="light"):
    """מחליף את כל הטוקנים (T.BG, T.TEXT ...). לקרוא לפני בניית הממשק. מחזיר "light"/"dark"."""
    global THEME, STATUS_COLORS, STATUS_BG
    THEME = resolve_theme(name)
    g = globals()
    g.update(PALETTES[THEME])
    STATUS_COLORS = {"ok": GREEN, "limited": YELLOW, "unavailable": RED, "not_verified": TEXT_MUTED,
                     "warn": YELLOW, "critical": RED, "pending": YELLOW, "info": LINK}
    STATUS_BG = {"ok": GREEN_BG, "limited": YELLOW_BG, "unavailable": RED_BG, "not_verified": CARD_BG_ALT,
                 "warn": YELLOW_BG, "critical": RED_BG, "pending": YELLOW_BG, "info": LIGHT_BLUE}
    for root in list(_tk_roots):
        try:
            style_root(root)
        except Exception:  # noqa: BLE001
            pass
    return THEME


def is_dark_theme():
    return THEME == "dark"


apply_theme("light")

# הפחתת תנועה: ברירת המחדל לפי Windows (אנימציות בממשק כבויות -> מופחת); ההגדרה באפליקציה גוברת
REDUCED_MOTION = False


def _windows_animations_off():
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        val = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(val), 0)   # SPI_GETCLIENTAREAANIMATION
        return not val.value
    except Exception:  # noqa: BLE001
        return False


def set_reduced_motion(value):
    """value: "1"/"0"/None (None = לפי Windows)."""
    global REDUCED_MOTION
    REDUCED_MOTION = _windows_animations_off() if value in (None, "", "system") else str(value) == "1"


# ---------------- מרווחים, פינות, תנועה (tokens.json) ----------------
SPACE = (4, 8, 12, 16, 20, 24, 32, 40)
RADIUS_CONTROL, RADIUS_CARD, RADIUS_SHEET = 12, 16, 22
MOTION_FAST, MOTION_STD, MOTION_EMPH = 160, 240, 360


def animate(widget, duration_ms, step, done=None, frames=None):
    """step(t) עם t 0..1 בעקומת ease-out (קירוב ל-cubic-bezier(.2,.8,.2,1)). בהפחתת תנועה - ישר לסוף.
    לעולם לא בשימוש במסלול שליחת SOS."""
    if REDUCED_MOTION or duration_ms <= 0:
        step(1.0)
        if done:
            done()
        return
    n = frames or max(4, duration_ms // 16)
    state = {"i": 0}

    def tick():
        try:
            if not widget.winfo_exists():
                return
        except tk.TclError:
            return
        state["i"] += 1
        t = min(1.0, state["i"] / n)
        e = 1 - (1 - t) ** 3
        try:
            step(e)
        except tk.TclError:
            return
        if t < 1.0:
            widget.after(max(1, duration_ms // n), tick)
        elif done:
            done()
    tick()


# =====================================================================================
# 4. גופנים
# =====================================================================================
FAMILY = "Segoe UI"
MIN_FONT_SIZE = 10
SCALES = {"normal": 1.0, "large": 1.25, "xlarge": 1.5}
SCALE_LABELS = {"normal": "רגיל", "large": "גדול", "xlarge": "גדול מאוד"}
_scale = 1.0
# סולם הטיפוגרפיה (נקודות; ב-Windows ‏1pt = 1.33px ב-96 DPI): large title / title1-3 / headline / body / ...
TYPE = {"large_title": 26, "title1": 21, "title2": 17, "title3": 15, "headline": 13, "body": 12, "callout": 12,
        "subhead": 11, "footnote": 10, "caption": 10}


def set_scale(name):
    global _scale
    _scale = SCALES.get(name, 1.0)


def get_scale():
    return _scale


# לא Segoe UI Variable (הקרוב ל-SF Pro): אין בו אותיות עבריות - Tk מחליף גופן לכל אות, השורה נשברת לחלקים
# והעברית מוצגת הפוכה (נמצא בצילום מסך). Segoe UI כולל עברית.


def F(size, weight=None, family=FAMILY):
    """גופן מוגדל לפי הסקאלה, ולעולם לא קטן מ-MIN_FONT_SIZE. נקודות - ה-DPI מטופל ב-tk scaling."""
    scaled = max(MIN_FONT_SIZE, int(round(size * _scale)))
    return (family, scaled, weight) if weight else (family, scaled)


def autowrap(label, margin=24):
    """wraplength שמתעדכן לרוחב הרכיב בפועל - טקסט ארוך נשבר לשורות במקום להיחתך."""
    def _fit(event):
        w = max(80, unpx(event.width) - margin)
        if abs(int(label.cget("wraplength") or 0) - w) > 4:
            label.configure(wraplength=w)
    label.bind("<Configure>", _fit, add="+")
    return label


RLM = "‏"
_HEB_CHARS = _HEB


def entry_text(value):
    """הטקסט האמיתי משורת קלט RTL - בלי סימון הכיווון הבלתי נראה."""
    return str(value or "").replace(RLM, "")


def rtl_entry(entry, var):
    """
    שורת קלט בעברית: Tk לא תומך בכיוון RTL בשורת קלט, ורווח שמקלידים אחרי מילה עברית מוצג בצד ימין
    (= בתחילת המשפט) - נראה כאילו "אי אפשר לכתוב רווח" (באג שדווח במסך ה-SOS). סימן RLM בלתי נראה בסוף
    הטקסט (רק כשיש עברית) גורם לרווח להופיע במקום הנכון. מי שקורא את הערך משתמש ב-entry_text().
    """
    busy = [False]

    def fix(*_a):
        if busy[0]:
            return
        raw = var.get()
        clean = raw.replace(RLM, "")
        want = clean + RLM if _HEB_CHARS.search(clean) else clean
        if want == raw:
            return
        try:
            pos = entry.index("insert")
        except tk.TclError:
            pos = len(raw)
        pos -= raw[:pos].count(RLM)
        busy[0] = True
        try:
            var.set(want)
            entry.icursor(min(pos, len(clean)))          # הסמן לפני ה-RLM, כדי שההקלדה תמשיך בטקסט
        finally:
            busy[0] = False
    var.trace_add("write", fix)
    entry.bind("<End>", lambda e: (entry.icursor(len(entry_text(var.get()))), "break")[1], add="+")
    return entry


def bind_activate(widget, command):
    """Enter ורווח מפעילים את הרכיב כשהוא בפוקוס (נגישות מקלדת)."""
    widget.bind("<Return>", lambda e: command())
    widget.bind("<KP_Enter>", lambda e: command())
    widget.bind("<space>", lambda e: command())


# =====================================================================================
# 5. צבעים - עזרים
# =====================================================================================
def _parent_bg(widget):
    try:
        v = widget.cget("bg")
        return v if v else BG
    except (tk.TclError, AttributeError):
        return BG


def _hex_to_rgb(c):
    c = c.lstrip("#")
    if len(c) == 3:  # "#333" -> "#333333" (בלי זה כפתור כהה קיבל טקסט כהה - לא קריא)
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


_NAMED = {"white": "#ffffff", "black": "#000000", "SystemButtonFace": "#f0f0f0", "gray50": "#7f7f7f"}


def _rgb(color):
    try:
        return _hex_to_rgb(_NAMED.get(color, color))
    except (ValueError, AttributeError):
        return (255, 255, 255)


def _is_dark(color):
    try:
        r, g, b = _hex_to_rgb(_NAMED.get(color, color))
    except (ValueError, AttributeError):
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) < 150


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = _lum(_rgb(a)), _lum(_rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def on(bg):
    """צבע הטקסט על משטח צבעוני: לבן או נייבי כהה - מה שנותן ניגודיות גבוהה יותר (WCAG)."""
    dark = "#06101F"
    return "#FFFFFF" if contrast("#FFFFFF", bg) >= contrast(dark, bg) else dark


def _shade(color, factor=0.9):
    try:
        r, g, b = _hex_to_rgb(_NAMED.get(color, color))
    except (ValueError, AttributeError):
        return color
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v * factor))) for v in (r, g, b))


def mix(a, b, t):
    """ערבוב צבעים (t=0 -> a, t=1 -> b). מחליף שקיפות, ש-Tk לא יודע לעשות."""
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(ra, rb))


def hover_of(color):
    """מצב ריחוף/לחיצה: כהה יותר במצב בהיר, בהיר יותר על משטחים כהים."""
    return mix(color, "#000000", 0.08) if not _is_dark(color) else mix(color, "#FFFFFF", 0.10)


def pressed_of(color):
    return mix(color, "#000000", 0.16) if not _is_dark(color) else mix(color, "#FFFFFF", 0.18)


# =====================================================================================
# 6. גרפיקה חדה (Pillow): דגימת-יתר פי 4 -> הקטנה, במקדם ה-DPI האמיתי, במטמון
# =====================================================================================
try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    HAS_PIL = True
except Exception:  # noqa: BLE001
    HAS_PIL = False

SS = 4
_PIL_CACHE = OrderedDict()
_PHOTO_CACHE = weakref.WeakKeyDictionary()


def _cached_pil(key, make):
    img = _PIL_CACHE.get(key)
    if img is None:
        img = make()
        _PIL_CACHE[key] = img
        if len(_PIL_CACHE) > 500:
            _PIL_CACHE.popitem(last=False)
    else:
        _PIL_CACHE.move_to_end(key)
    return img


def _photo(widget, key, make):
    """PhotoImage למפרש ה-Tk של הווידג'ט (מטמון לכל שורש). הקורא שומר הפניה על הווידג'ט."""
    root = widget._root()
    per = _PHOTO_CACHE.get(root)
    if per is None:
        per = OrderedDict()
        _PHOTO_CACHE[root] = per
    ph = per.get(key)
    if ph is None:
        ph = ImageTk.PhotoImage(_cached_pil(key, make), master=widget)
        per[key] = ph
        if len(per) > 400:
            per.popitem(last=False)
    else:
        per.move_to_end(key)
    return ph


def _rrect(draw, box, r, **kw):
    x1, y1, x2, y2 = box
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    draw.rounded_rectangle(box, radius=r, **kw)


def pill_pil(w, h, fill, outer, radius=None, border=None, ring=None, ring_w=0, inset=0):
    """מלבן מעוגל חלק w x h (פיקסלים פיזיים). ring = טבעת פוקוס מחוץ לצורה (inset שמור תמיד)."""
    W, H = max(2, w) * SS, max(2, h) * SS
    im = Image.new("RGB", (W, H), _rgb(outer))
    d = ImageDraw.Draw(im)
    ins = inset * SS
    r = (h / 2 - inset) if radius is None else radius
    if ring:
        _rrect(d, (0, 0, W - 1, H - 1), (r + inset) * SS, fill=_rgb(ring))
        gap = max(1, inset - ring_w) * SS
        _rrect(d, (ring_w * SS, ring_w * SS, W - 1 - ring_w * SS, H - 1 - ring_w * SS), (r + gap / SS) * SS,
               fill=_rgb(outer))
    box = (ins, ins, W - 1 - ins, H - 1 - ins)
    if border:
        _rrect(d, box, r * SS, fill=_rgb(border))
        bw = max(1, px(1)) * SS
        _rrect(d, (box[0] + bw, box[1] + bw, box[2] - bw, box[3] - bw), max(0, r * SS - bw), fill=_rgb(fill))
    else:
        _rrect(d, box, r * SS, fill=_rgb(fill))
    return im.resize((max(2, w), max(2, h)), Image.LANCZOS)


def _card_template(r, fill, border, outer, shadow_rgba, m, mt, mb, dy, blur):
    cl = m + r
    ct, cb = mt + r, r + mb
    Wt, Ht = 2 * cl + 1, ct + cb + 1
    W, H = Wt * SS, Ht * SS
    base = Image.new("RGBA", (W, H), _rgb(outer) + (255,))
    if shadow_rgba and shadow_rgba[3]:
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        _rrect(md, (m * SS, (mt + dy) * SS, W - 1 - m * SS, H - 1 - (mb - dy) * SS), r * SS, fill=shadow_rgba[3])
        mask = mask.filter(ImageFilter.GaussianBlur(max(1, blur) * SS / 2))
        sh = Image.new("RGBA", (W, H), shadow_rgba[:3] + (0,))
        sh.putalpha(mask)
        base = Image.alpha_composite(base, sh)
    d = ImageDraw.Draw(base)
    box = (m * SS, mt * SS, W - 1 - m * SS, H - 1 - mb * SS)
    if border:
        _rrect(d, box, r * SS, fill=_rgb(border) + (255,))
        bw = max(1, px(1)) * SS
        _rrect(d, (box[0] + bw, box[1] + bw, box[2] - bw, box[3] - bw), max(0, r * SS - bw), fill=_rgb(fill) + (255,))
    else:
        _rrect(d, box, r * SS, fill=_rgb(fill) + (255,))
    return base.resize((Wt, Ht), Image.LANCZOS).convert("RGB"), (cl, ct, cb)


def card_pil(w, h, r, fill, border, outer, m, mt, mb, dy, blur):
    """כרטיס w x h עם צל רך, בשיטת nine-patch: הפינות מצוירות פעם אחת, הצלעות נמתחות (מהיר בכל גודל)."""
    shadow = _SHADOW_RGBA[THEME]
    tmpl, (cl, ct, cb) = _cached_pil(("cardtpl", r, fill, border, outer, m, mt, mb, dy, blur, THEME),
                                     lambda: _card_template(r, fill, border, outer, shadow, m, mt, mb, dy, blur))
    Wt, Ht = tmpl.size
    w, h = max(w, 2 * cl + 2), max(h, ct + cb + 2)
    out = Image.new("RGB", (w, h), tmpl.getpixel((cl, ct)))
    out.paste(tmpl.crop((0, 0, cl, ct)), (0, 0))
    out.paste(tmpl.crop((Wt - cl, 0, Wt, ct)), (w - cl, 0))
    out.paste(tmpl.crop((0, Ht - cb, cl, Ht)), (0, h - cb))
    out.paste(tmpl.crop((Wt - cl, Ht - cb, Wt, Ht)), (w - cl, h - cb))
    mw, mh = w - 2 * cl, h - ct - cb
    if mw > 0:
        out.paste(tmpl.crop((cl, 0, cl + 1, ct)).resize((mw, ct)), (cl, 0))
        out.paste(tmpl.crop((cl, Ht - cb, cl + 1, Ht)).resize((mw, cb)), (cl, h - cb))
    if mh > 0:
        out.paste(tmpl.crop((0, ct, cl, ct + 1)).resize((cl, mh)), (0, ct))
        out.paste(tmpl.crop((Wt - cl, ct, Wt, ct + 1)).resize((cl, mh)), (w - cl, ct))
    return out


def sos_pil(size, outer, state="normal", focus=False, ring=None):
    """כפתור ה-SOS: הילה כפולה עדינה, דיסק אדום עם מעבר צבע אנכי והדגשה עליונה. מצויר חלק (דגימת-יתר)."""
    S = size * SS
    im = Image.new("RGBA", (S, S), _rgb(outer) + (255,))
    d = ImageDraw.Draw(im)
    c = S / 2
    base = _rgb(SOS_FILL)
    if state == "hover":
        base = _rgb(mix(SOS_FILL, "#000000", 0.08))
    elif state == "pressed":
        base = _rgb(mix(SOS_FILL, "#000000", 0.18))
    halo1 = _rgb(mix(SOS_FILL, outer, 0.88))
    halo2 = _rgb(mix(SOS_FILL, outer, 0.74))
    if focus and ring:
        d.ellipse((SS * 1, SS * 1, S - SS * 1, S - SS * 1), outline=_rgb(ring), width=int(px(3) * SS))
    for rad, col in ((c - px(6) * SS, halo1), (c - px(20) * SS, halo2)):
        d.ellipse((c - rad, c - rad, c + rad, c + rad), fill=col)
    rad = c - px(34) * SS
    disc = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGBA", (1, 256))
    top = tuple(min(255, int(v + (255 - v) * 0.16)) for v in base)
    bot = tuple(int(v * 0.86) for v in base)
    for y in range(256):
        t = y / 255
        grad.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))
    grad = grad.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse((c - rad, c - rad, c + rad, c + rad), fill=255)
    disc.paste(grad, (0, 0), mask)
    im = Image.alpha_composite(im, disc)
    # טבעת פנימית דקה בהירה - עומק עדין, כמו כפתור זכוכית (שכבה נפרדת: ImageDraw לא ממזג שקיפות)
    ov = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    inner = rad - px(5) * SS
    ImageDraw.Draw(ov).ellipse((c - inner, c - inner, c + inner, c + inner), outline=(255, 255, 255, 70),
                               width=max(1, int(px(1.5) * SS)))
    im = Image.alpha_composite(im, ov)
    return im.resize((size, size), Image.LANCZOS).convert("RGB")


def circle_pil(d_px, fill, outer, border=None):
    S = d_px * SS
    im = Image.new("RGB", (S, S), _rgb(outer))
    dr = ImageDraw.Draw(im)
    if border:
        dr.ellipse((0, 0, S - 1, S - 1), fill=_rgb(border))
        b = max(1, px(1)) * SS
        dr.ellipse((b, b, S - 1 - b, S - 1 - b), fill=_rgb(fill))
    else:
        dr.ellipse((0, 0, S - 1, S - 1), fill=_rgb(fill))
    return im.resize((d_px, d_px), Image.LANCZOS)


def shield_pil(size, fill, outer, mark="#FFFFFF"):
    """סמל המגן של LifeGuard (וקטורי, חד) - במקום אמוג'י שמצויר בגופן אחר."""
    S = size * SS
    im = Image.new("RGB", (S, S), _rgb(outer))
    d = ImageDraw.Draw(im)
    w = S
    pts = [(w * 0.5, w * 0.06), (w * 0.88, w * 0.2), (w * 0.86, w * 0.52), (w * 0.5, w * 0.95),
           (w * 0.14, w * 0.52), (w * 0.12, w * 0.2)]
    d.polygon(pts, fill=_rgb(fill))
    # "V" לבן בתוך המגן
    lw = max(1, int(w * 0.09))
    d.line([(w * 0.32, w * 0.5), (w * 0.46, w * 0.64), (w * 0.7, w * 0.36)], fill=_rgb(mark), width=lw, joint="curve")
    return im.resize((size, size), Image.LANCZOS)


def set_image(widget, img_key, make, **conf):
    """מציב תמונה מהמטמון על Label/Button ושומר הפניה (אחרת Tk מוחק אותה)."""
    ph = _photo(widget, img_key, make)
    widget.__dict__["_lg_img"] = ph
    _raw_configure(widget, image=ph, **conf)
    return ph


# =====================================================================================
# 7. רכיבים
# =====================================================================================
class _Pill:
    """מערבב ל-Label/Button: רקע גלולה חלק (תמונה) מתחת לטקסט, בגודל הטקסט או ברוחב שהוקצה (fill)."""

    _PILL_KEYS = {"bg", "background"}

    def _pill_init(self, fill, outer, radius=None, border=None, ring_inset=0, min_w=0, interactive=False):
        self.__dict__.update(_fill=fill, _outer=outer, _radius=radius, _border=border, _inset=ring_inset,
                             _min_w=min_w, _alloc=0, _img_w=0, _hover=False, _pressed=False, _focus=False,
                             _interactive=interactive, _pad=(px(6), px(2)))
        if HAS_PIL:
            self.bind("<Configure>", self._on_conf, add="+")

    def _on_conf(self, e):
        # אירועי Configure יכולים להגיע *בתוך* configure() (סינכרוני) - בזמן ציור מתעלמים (בלי רקורסיה),
        # ושינוי רוחב אמיתי מצייר פעם אחת כשהממשק פנוי.
        if self.__dict__.get("_busy") or not self._img_w or abs(e.width - self._img_w) <= 2:
            return
        self.__dict__["_alloc"] = e.width
        if not self.__dict__.get("_pending"):
            self.__dict__["_pending"] = True

            def run():
                self.__dict__["_pending"] = False
                try:
                    if self.winfo_exists() and abs(self.winfo_width() - self._img_w) > 2 and self.winfo_width() > 1:
                        self.__dict__["_alloc"] = self.winfo_width()
                        self._pill_render()
                except tk.TclError:
                    pass
            self.after_idle(run)

    def _measure(self):
        _raw_configure(self, image="", padx=self._pad[0], pady=self._pad[1], compound="none")
        return self.winfo_reqwidth(), self.winfo_reqheight()

    def _pill_fill(self):
        f = self._fill
        if self._interactive:
            try:
                if str(tk.Button.cget(self, "state")) == "disabled":
                    return mix(f, self._outer, 0.45)
            except tk.TclError:
                pass
            if self._pressed:
                return pressed_of(f)
            if self._hover:
                return hover_of(f)
        return f

    def _pill_render(self):
        if not HAS_PIL:
            _raw_configure(self, bg=self._fill)
            return
        if self.__dict__.get("_busy"):
            return
        self.__dict__["_busy"] = True
        try:
            self._pill_render_now()
        except tk.TclError:
            pass
        finally:
            self.__dict__["_busy"] = False

    def _pill_render_now(self):
        mw, mh = self._measure()
        ins = self._inset
        w = max(mw + 2 * ins, self._min_w, self._alloc or 0)
        h = mh + 2 * ins
        ring = FOCUS if (self._focus and self._interactive) else None
        fill = self._pill_fill()
        r = None if self._radius is None else min(self._radius, (h - 2 * ins) / 2)
        key = ("pill", w, h, fill, self._outer, r, self._border, ring, ins)
        set_image(self, key, lambda: pill_pil(w, h, fill, self._outer, r, self._border, ring, px(2), ins),
                  compound="center", padx=0, pady=0)
        self.__dict__["_img_w"] = w

    def _pill_configure(self, base, cnf, kw):
        merged = dict(cnf or {}) if isinstance(cnf, dict) else {}
        merged.update(kw)
        rerender = False
        for k in list(merged):
            if k in self._PILL_KEYS:
                self.__dict__["_fill"] = merged.pop(k)
                rerender = True
            elif k in ("activebackground",):
                merged.pop(k)
            elif k in ("text", "font", "state", "fg", "foreground"):
                rerender = rerender or k != "fg" and k != "foreground"
        res = base.configure(self, merged) if merged else None
        if "fg" in merged or "foreground" in merged:
            fg = merged.get("fg", merged.get("foreground"))
            try:
                _raw_configure(self, activeforeground=fg)
            except tk.TclError:
                pass
        if rerender:
            self._pill_render()
        return res


class PillButton(_Pill, tk.Button):
    """כפתור בסגנון iOS: גלולה/מלבן מעוגל חלק, ריחוף, לחיצה, טבעת פוקוס, Enter/רווח. API של tk.Button."""

    def __init__(self, parent, text="", command=None, fill=None, fg=None, size=12, bold=True, padx=18, pady=10,
                 radius=RADIUS_CONTROL, min_w=0, **kw):
        outer = _parent_bg(parent)
        fill = fill or CARD_BG_ALT
        if fg is None:
            fg = auto_fg(fill)
        kw.pop("activebackground", None)
        kw.pop("highlightthickness", None)
        kw.pop("bg", None)
        super().__init__(parent, text=text, command=command, bg=outer, fg=fg, activebackground=outer,
                         activeforeground=fg, font=F(size, "bold" if bold else None), bd=0, relief="flat",
                         overrelief="flat", cursor="hand2", takefocus=True, highlightthickness=0,
                         disabledforeground=TEXT_MUTED, **kw)
        self._pill_init(fill, outer, radius=px(radius) if radius is not None else None, ring_inset=px(3),
                        min_w=min_w, interactive=True)
        self.__dict__["_pad"] = (px(padx), px(pady))
        if command is not None:
            bind_activate(self, command)
        for ev, attr, val in (("<Enter>", "_hover", True), ("<Leave>", "_hover", False),
                              ("<FocusIn>", "_focus", True), ("<FocusOut>", "_focus", False)):
            self.bind(ev, lambda e, a=attr, v=val: self._set_state(a, v), add="+")
        self.bind("<ButtonPress-1>", lambda e: self._set_state("_pressed", True), add="+")
        self.bind("<ButtonRelease-1>", lambda e: self._set_state("_pressed", False), add="+")
        self._pill_render()

    def _set_state(self, attr, val):
        if self.__dict__.get(attr) != val:
            self.__dict__[attr] = val
            if attr == "_hover" and not val:
                self.__dict__["_pressed"] = False
            try:
                self._pill_render()
            except tk.TclError:
                pass

    def configure(self, cnf=None, **kw):
        if cnf is None and not kw or isinstance(cnf, str):
            return tk.Button.configure(self, cnf, **kw) if cnf else tk.Button.configure(self)
        return self._pill_configure(tk.Button, cnf, kw)

    config = configure

    def cget(self, key):
        if key in ("bg", "background"):
            return self._fill
        return tk.Button.cget(self, key)

    __getitem__ = cget


class Chip(_Pill, tk.Label):
    """תגית סטטוס: גלולה צבעונית חלקה. configure(text, fg, bg) כמו Label (bg = צבע הגלולה)."""

    def __init__(self, parent, text, fill, fg, size=10, bold=True, padx=10, pady=3, radius=None, border=None):
        outer = _parent_bg(parent)
        super().__init__(parent, text=text, font=F(size, "bold" if bold else None), fg=fg, bg=outer, bd=0)
        self._pill_init(fill, outer, radius=radius, border=border)
        self.__dict__["_pad"] = (px(padx), px(pady))
        self._pill_render()

    def configure(self, cnf=None, **kw):
        if cnf is None and not kw or isinstance(cnf, str):
            return tk.Label.configure(self, cnf, **kw) if cnf else tk.Label.configure(self)
        return self._pill_configure(tk.Label, cnf, kw)

    config = configure

    def cget(self, key):
        if key in ("bg", "background"):
            return self._fill
        return tk.Label.cget(self, key)

    __getitem__ = cget


def auto_fg(fill):
    """טקסט על כפתור: משני (אפור-תכלת) = כחול; צבעוני = לבן/כהה לפי ניגודיות; משטח = טקסט רגיל."""
    if fill in (CARD_BG_ALT, LIGHT_BLUE, FILL):
        return LINK
    if fill in (CARD_BG, BG, NAV_BG):
        return TEXT
    return on(fill)


def _button_fill(bg):
    """אדום כטקסט (RED) שונה במצב כהה מאדום כמילוי כפתור (SOS_FILL, לבן עליו 4.5:1+)."""
    return SOS_FILL if bg == RED else bg


def big_button(parent, text, command, bg=None, fg=None, size=12, bold=True, padx=18, pady=10, **kw):
    """כפתור עם משטח לחיצה גדול (44px+), פינות מעוגלות, ריחוף/לחיצה וטבעת פוקוס. צבע הטקסט אוטומטי."""
    fill = _button_fill(bg or CARD_BG_ALT)
    width_chars = kw.pop("width", None)
    min_w = 0
    if width_chars:
        try:
            from tkinter import font as tkfont
            min_w = int(width_chars) * tkfont.Font(root=parent, font=F(size)).measure("0")
        except (tk.TclError, ValueError):
            min_w = 0
    return PillButton(parent, text=text, command=command, fill=fill, fg=fg, size=size, bold=bold, padx=padx,
                      pady=pady, min_w=min_w, **kw)


def primary_button(parent, text, command, **kw):
    return big_button(parent, text, command, bg=BLUE, **kw)


def secondary_button(parent, text, command, **kw):
    return big_button(parent, text, command, bg=CARD_BG_ALT, **kw)


def destructive_button(parent, text, command, **kw):
    return big_button(parent, text, command, bg=SOS_FILL, **kw)


def plain_button(parent, text, command, size=11, fg=None, bold=False, **kw):
    """כפתור טקסט (כמו "Edit" ב-iOS): בלי מילוי, טקסט כחול, ריחוף עדין."""
    bg = _parent_bg(parent)
    return PillButton(parent, text=text, command=command, fill=bg, fg=fg or LINK, size=size, bold=bold,
                      padx=10, pady=6, **kw)


def list_rows(parent, rows, size=12):
    """
    רשימה בסגנון "הגדרות" של iOS: שורות לחיצות, חץ "‹" בצד השמאלי (בעברית - כיוון ההמשך), וקו מפריד דק
    בין השורות שמוזח מהקצה כמו ב-iOS. rows = [(טקסט, פקודה), ...]. מחזיר את רשימת הכפתורים.
    ריחוף צובע את כל השורה (גם את החץ).
    """
    bg = _parent_bg(parent)
    out = []
    for i, (text, cmd) in enumerate(rows):
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x")
        chev = tk.Label(row, text="‹", font=F(size + 4), fg=TEXT_MUTED, bg=bg, cursor="hand2")
        chev.pack(side="left", padx=(10, 0))
        chev.bind("<Button-1>", lambda e, c=cmd: c())
        btn = tk.Button(row, text=text, command=cmd, bg=bg, fg=TEXT, activebackground=hover_of(bg),
                        activeforeground=TEXT, font=F(size), bd=0, relief="flat", anchor="e", padx=10, pady=11,
                        cursor="hand2", takefocus=True, highlightthickness=2, highlightbackground=bg,
                        highlightcolor=FOCUS)
        btn.pack(side="right", fill="x", expand=True)
        bind_activate(btn, cmd)

        def hov(on_, r=row, b=btn, c=chev):
            col = hover_of(bg) if on_ else bg
            for w in (r, b, c):
                try:
                    _raw_configure(w, bg=col)
                except tk.TclError:
                    pass
        for w in (row, btn, chev):
            w.bind("<Enter>", lambda e, h=hov: h(True), add="+")
            w.bind("<Leave>", lambda e, h=hov: h(False), add="+")
        if i < len(rows) - 1:
            tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=(0, 16))   # מוזח מימין, כמו אחרי אייקון
        out.append(btn)
    return out


def label(parent, text="", size=11, fg=None, bg=None, bold=False, **kw):
    kw.setdefault("justify", "right")
    return tk.Label(parent, text=text, font=F(size, "bold" if bold else None), fg=fg or TEXT,
                    bg=bg if bg is not None else _parent_bg(parent), **kw)


def chip(parent, text, level="not_verified", size=10, bg=None):
    """תגית סטטוס צבעונית (טקסט צבעוני על גלולה בהירה תואמת)."""
    return Chip(parent, text, bg or STATUS_BG.get(level, CARD_BG_ALT), STATUS_COLORS.get(level, TEXT_MUTED), size=size)


def section_label(parent, text):
    """כותרת קבוצה מעל כרטיס (כמו ב-iOS: אותיות קטנות ומעומעמות)."""
    lab = label(parent, text, TYPE["footnote"], TEXT_MUTED, bold=True, anchor="e")
    lab.pack(fill="x", padx=(0, 6), pady=(6, 4))
    return lab


def separator(parent, padx=0, pady=8):
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=padx, pady=pady)


class RoundedCard(tk.Canvas):
    """
    כרטיס מעוגל עם צל רך (תמונה חלקה, nine-patch). התוכן נכנס ל-self.body (Frame רגיל).
    גובה הכרטיס נקבע אוטומטית לפי התוכן; הרוחב לפי ה-layout של ההורה. radius/padding במידות לוגיות.
    """

    def __init__(self, parent, bg=None, radius=RADIUS_CARD, padding=16, outer_bg=None, border=None,
                 elevation=1, **kw):
        outer = outer_bg or _parent_bg(parent)
        bg = bg or CARD_BG
        super().__init__(parent, bg=outer, highlightthickness=0, bd=0, **kw)
        if border is None and THEME == "dark":
            border = mix(bg, BORDER, 0.6)
        self.card_bg, self.border, self.outer = bg, border, outer
        self.radius, self.padding = px(radius), px(padding)
        self._m = px(4) if elevation else 0
        self._mt = px(2) if elevation else 0
        self._dy = px(2) if elevation else 0
        self._mb = self._m + self._dy if elevation else 0
        self._blur = px(8) if elevation else 0
        self.body = tk.Frame(self, bg=bg)
        self._win = self.create_window(self._m + self.padding, self._mt + self.padding, window=self.body, anchor="nw")
        self._img_id = None
        self._last = None
        self.bind("<Configure>", self._redraw)
        self.body.bind("<Configure>", self._fit)

    def _height(self):
        v = tk.Canvas.cget(self, "height")
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return int(self.winfo_fpixels(v))

    def _fit(self, _e=None):
        h = self.body.winfo_reqheight() + 2 * self.padding + self._mt + self._mb
        if self._height() != h:
            _raw_configure(self, height=h)
        self._redraw()

    def _redraw(self, _e=None):
        w, h = self.winfo_width(), self._height()
        if w < 10:
            return
        if (w, h) != self._last:
            self._last = (w, h)
            if HAS_PIL:
                key = ("card", w, h, self.radius, self.card_bg, self.border, self.outer, self._m, self._mt,
                       self._mb, self._dy, self._blur, THEME)
                ph = _photo(self, key, lambda: card_pil(w, h, self.radius, self.card_bg, self.border, self.outer,
                                                        self._m, self._mt, self._mb, self._dy, self._blur))
                self.__dict__["_lg_img"] = ph
                if self._img_id is None:
                    self._img_id = self.create_image(0, 0, image=ph, anchor="nw", tags="bgshape")
                else:
                    self.itemconfigure(self._img_id, image=ph)
            else:
                self.delete("bgshape")
                _round_rect(self, self._m, self._mt, w - self._m, h - self._mb, self.radius, fill=self.card_bg,
                            outline=self.border or self.card_bg, tags="bgshape")
            self.tag_lower("bgshape")
        self.itemconfigure(self._win, width=max(1, w - 2 * self._m - 2 * self.padding))


def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def card(parent, **kw):
    """מחזיר (card, body) - נוחות."""
    c = RoundedCard(parent, **kw)
    return c, c.body


class SlimScrollbar(tk.Canvas):
    """פס גלילה דק בסגנון macOS/iOS (גלולה חלקה), תואם tk.Scrollbar: set(lo, hi) ו-command=yview."""

    def __init__(self, parent, command, bg=None):
        bg = bg or _parent_bg(parent)
        super().__init__(parent, width=10, bg=bg, highlightthickness=0, bd=0)
        self.command, self._bg = command, bg
        self._lo, self._hi, self._drag = 0.0, 1.0, None
        self._hover = False
        self._img = None
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def set(self, lo, hi):
        self._lo, self._hi = float(lo), float(hi)
        self._draw()

    def _draw(self):
        self.delete("all")
        if self._hi - self._lo >= 0.999:
            return
        h, w = self.winfo_height(), self.winfo_width()
        if h < 10:
            return
        y1, y2 = int(self._lo * h), int(self._hi * h)
        th = max(px(24), y2 - y1)
        bw = px(6) if self._hover else px(4)
        col = mix(TEXT_MUTED, self._bg, 0.25 if self._hover else 0.45)
        if HAS_PIL:
            ph = _photo(self, ("sb", bw, th, col, self._bg), lambda: pill_pil(bw, th, col, self._bg))
            self._img = ph
            self.create_image(w - bw - px(2), y1, image=ph, anchor="nw")
        else:
            self.create_rectangle(w - bw - px(2), y1, w - px(2), y1 + th, fill=col, outline="")

    def _press(self, e):
        h = max(1, self.winfo_height())
        f = e.y / h
        if self._lo <= f <= self._hi:
            self._drag = (e.y, self._lo)
        else:
            self.command("moveto", max(0.0, f - (self._hi - self._lo) / 2))

    def _motion(self, e):
        if self._drag is None:
            return
        h = max(1, self.winfo_height())
        y0, lo0 = self._drag
        self.command("moveto", max(0.0, lo0 + (e.y - y0) / h))


def scrollable(parent, bg=None):
    """מחזיר (outer, inner): מסגרת עם גלילה אנכית (גלגלת + PageUp/Down) ופס גלילה דק."""
    bg = bg or _parent_bg(parent)
    outer = tk.Frame(parent, bg=bg)
    canvas = tk.Canvas(outer, bg=bg, highlightthickness=0)
    bar = SlimScrollbar(outer, command=canvas.yview, bg=bg)
    inner = tk.Frame(canvas, bg=bg)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
    canvas.configure(yscrollcommand=bar.set)
    bar.pack(side="left", fill="y")
    canvas.pack(side="right", fill="both", expand=True)

    def _wheel(event):
        try:
            canvas.yview_scroll(int(-1 * (event.delta / 120) * 3), "units")
        except tk.TclError:
            pass
    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    outer.canvas = canvas
    outer.bind_all_keys = lambda top: (
        top.bind("<Prior>", lambda e: canvas.yview_scroll(-1, "pages")),
        top.bind("<Next>", lambda e: canvas.yview_scroll(1, "pages")),
    )
    return outer, inner


class Segmented(tk.Canvas):
    """
    בקר מקטעים (Segmented Control) של iOS: מסילה אפורה ו"אגודל" לבן עם צל שזז לבחירה (אנימציה קצרה,
    מבוטלת בהפחתת תנועה). options = [(value, label)], variable = StringVar. מקלדת: חיצים / Enter.
    בעברית: האפשרות הראשונה מימין.
    """

    def __init__(self, parent, options, variable, command=None, size=11, bg=None):
        bg = bg or _parent_bg(parent)
        from tkinter import font as tkfont
        self._font = F(size, "bold")
        f = tkfont.Font(root=parent, font=self._font)
        h = unpx(f.metrics("linespace")) + 16
        super().__init__(parent, height=h, bg=bg, highlightthickness=0, bd=0, takefocus=True, cursor="hand2")
        self.options, self.var, self.command, self._bg = list(options), variable, command, bg
        self._pos = None
        self._focus = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Left>", lambda e: self._step(1))
        self.bind("<Right>", lambda e: self._step(-1))
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.var.trace_add("write", lambda *a: self._animate_to(self._index()))

    def _set_focus(self, v):
        self._focus = v
        self._draw()

    def _index(self):
        vals = [v for v, _ in self.options]
        return vals.index(self.var.get()) if self.var.get() in vals else 0

    def _seg_box(self, i, w):
        n = len(self.options)
        sw = (w - px(4)) / n
        x2 = w - px(2) - i * sw            # RTL: אינדקס 0 בצד ימין
        return x2 - sw, x2

    def _click(self, e):
        self.focus_set()
        n = len(self.options)
        w = self.winfo_width()
        sw = (w - px(4)) / n
        i = int((w - px(2) - e.x) // sw)
        self._choose(max(0, min(n - 1, i)))

    def _step(self, d):
        self._choose(max(0, min(len(self.options) - 1, self._index() + d)))

    def _choose(self, i):
        val = self.options[i][0]
        if self.var.get() != val:
            self.var.set(val)
            if self.command:
                self.command(val)

    def _animate_to(self, i):
        target = float(i)
        start = self._pos if self._pos is not None else target
        if start == target:
            self._pos = target
            self._draw()
            return

        def step(t):
            self._pos = start + (target - start) * t
            self._draw()
        animate(self, MOTION_FAST, step)

    def _draw(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w < 20:
            return
        if self._pos is None:
            self._pos = float(self._index())
        self.delete("all")
        track = FILL if THEME == "light" else CARD_BG_ALT
        thumb = CARD_BG if THEME == "light" else mix(CARD_BG_ALT, "#FFFFFF", 0.14)
        ring = FOCUS if self._focus else None
        if HAS_PIL:
            ph = _photo(self, ("segtrack", w, h, track, self._bg, ring),
                        lambda: pill_pil(w, h, track, self._bg, radius=px(9), ring=ring, ring_w=px(2),
                                         inset=px(2) if ring else 0))
            self._track = ph
            self.create_image(0, 0, image=ph, anchor="nw")
        n = len(self.options)
        sw = (w - px(4)) / n
        x2 = w - px(2) - self._pos * sw
        tw, th = int(sw) - px(2), h - px(6)
        if HAS_PIL and tw > 4:
            ph2 = _photo(self, ("segthumb", tw, th, thumb, track),
                         lambda: card_pil(tw, th, px(7), thumb, None, track, px(1), px(1), px(2), px(1), px(3)))
            self._thumb = ph2
            self.create_image(int(x2 - sw + px(1)), px(3), image=ph2, anchor="nw")
        sel = self._index()
        for i, (_v, text) in enumerate(self.options):
            a, b = self._seg_box(i, w)
            self.create_text((a + b) / 2, h / 2, text=text, font=self._font,
                             fill=TEXT if i == sel else TEXT_SECONDARY)


class Toggle(tk.Canvas):
    """מתג iOS (51x31) מחובר ל-BooleanVar. ירוק = פעיל. לחיצה/רווח/Enter; הידית מחליקה (אלא אם הופחתה תנועה)."""

    def __init__(self, parent, variable, command=None, bg=None):
        bg = bg or _parent_bg(parent)
        super().__init__(parent, width=46, height=28, bg=bg, highlightthickness=0, bd=0, takefocus=True,
                         cursor="hand2")
        self.var, self.command, self._bg = variable, command, bg
        self._pos = 1.0 if variable.get() else 0.0
        self._focus = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", lambda e: self.toggle())
        bind_activate(self, self.toggle)
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.var.trace_add("write", lambda *a: self._sync())

    def _set_focus(self, v):
        self._focus = v
        self._draw()

    def toggle(self):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _sync(self):
        target = 1.0 if self.var.get() else 0.0
        start = self._pos

        def step(t):
            self._pos = start + (target - start) * t
            self._draw()
        animate(self, MOTION_FAST, step)

    def _draw(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w < 10 or not HAS_PIL:
            return
        self.delete("all")
        on_col = GREEN if THEME == "light" else "#30B158"
        off_col = mix(FILL, TEXT_MUTED, 0.25)
        col = mix(off_col, on_col, self._pos)
        ring = FOCUS if self._focus else None
        ph = _photo(self, ("tgl", w, h, col, self._bg, ring),
                    lambda: pill_pil(w, h, col, self._bg, ring=ring, ring_w=px(2), inset=px(2)))
        self._t = ph
        self.create_image(0, 0, image=ph, anchor="nw")
        k = h - px(8)
        x = px(4) + (w - px(8) - k) * self._pos
        ph2 = _photo(self, ("knob", k, col), lambda: circle_pil(k, "#FFFFFF", col))
        self._k = ph2
        self.create_image(int(x), px(4), image=ph2, anchor="nw")


def toggle_row(parent, text, variable, command=None, sub=None, size=12):
    """שורת הגדרה: טקסט מימין, מתג משמאל, והסבר קצר מתחת. מחזיר את המתג."""
    bg = _parent_bg(parent)
    row = tk.Frame(parent, bg=bg)
    row.pack(fill="x", pady=(4, 2))
    sw = Toggle(row, variable, command, bg=bg)
    sw.pack(side="left", padx=(0, 6))
    lab = label(row, text, size, TEXT, anchor="e", wraplength=380)
    lab.pack(side="right", fill="x", expand=True)
    lab.bind("<Button-1>", lambda e: sw.toggle())
    autowrap(lab, margin=8)
    if sub:
        s = label(parent, sub, TYPE["footnote"], TEXT_MUTED, anchor="e", wraplength=420)
        s.pack(fill="x")
        autowrap(s)
    return sw


class ProgressBar(tk.Canvas):
    """פס התקדמות דק ומעוגל; set(frac) מחליק לערך החדש (טיימרים - לא במסלול שליחת ה-SOS)."""

    def __init__(self, parent, height=6, color=None, bg=None, track=None):
        bg = bg or _parent_bg(parent)
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, bd=0)
        self._bg, self._color, self._track = bg, color or BLUE, track or FILL
        self._frac = 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, frac, color=None, smooth=True):
        frac = max(0.0, min(1.0, float(frac)))
        if color:
            self._color = color
        start = self._frac
        if not smooth or abs(frac - start) < 0.002:
            self._frac = frac
            self._draw()
            return

        def step(t):
            self._frac = start + (frac - start) * t
            self._draw()
        animate(self, MOTION_STD, step, frames=8)

    def _draw(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w < 4 or not HAS_PIL:
            return
        self.delete("all")
        ph = _photo(self, ("pbt", w, h, self._track, self._bg), lambda: pill_pil(w, h, self._track, self._bg))
        self._a = ph
        self.create_image(0, 0, image=ph, anchor="nw")
        fw = int(w * self._frac)
        if fw >= h:
            ph2 = _photo(self, ("pbf", fw, h, self._color, self._track),
                         lambda: pill_pil(fw, h, self._color, self._track))
            self._b = ph2
            self.create_image(w - fw, 0, image=ph2, anchor="nw")     # RTL: מתמלא מימין


def icon_tile(parent, glyph, tint=None, fg=None, size=40, font_size=15):
    """אייקון בריבוע מעוגל צבעוני (כמו אייקוני ההגדרות של iOS)."""
    bg = _parent_bg(parent)
    tint = tint or LIGHT_BLUE
    s = px(size)
    lab = tk.Label(parent, text=glyph, font=F(font_size), fg=fg or LINK, bg=bg, bd=0)
    if HAS_PIL:
        set_image(lab, ("tile", s, tint, bg), lambda: pill_pil(s, s, tint, bg, radius=px(10)), compound="center")
    return lab


def flash_feedback(label_widget, text, color=None, ms=6000):
    """משוב ברור אחרי פעולה (נשאר על המסך כמה שניות; ללא הבהוב)."""
    try:
        label_widget.configure(text=text, fg=color or GREEN)
        label_widget.after(ms, lambda: label_widget.winfo_exists() and label_widget.configure(text=""))
    except tk.TclError:
        pass


# =====================================================================================
# 8. ברירות מחדל לכל שורש Tk (שדות קלט, רשימות, תפריטים, ttk) לפי הערכה הפעילה
# =====================================================================================
def style_root(root):
    field = CARD_BG_ALT if THEME == "light" else "#0B1526"
    opts = {
        "*Entry.background": field, "*Entry.foreground": TEXT, "*Entry.insertBackground": TEXT,
        "*Entry.selectBackground": BLUE, "*Entry.selectForeground": ON_ACCENT, "*Entry.relief": "flat",
        "*Entry.highlightThickness": px(2), "*Entry.highlightColor": FOCUS, "*Entry.highlightBackground": BORDER,
        "*Entry.disabledBackground": FILL, "*Entry.readonlyBackground": FILL,
        "*Spinbox.background": field, "*Spinbox.foreground": TEXT, "*Spinbox.insertBackground": TEXT,
        "*Spinbox.buttonBackground": CARD_BG_ALT, "*Spinbox.relief": "flat",
        "*Spinbox.highlightThickness": px(2), "*Spinbox.highlightColor": FOCUS,
        "*Spinbox.highlightBackground": BORDER,
        "*Text.background": field, "*Text.foreground": TEXT, "*Text.insertBackground": TEXT,
        "*Text.selectBackground": BLUE, "*Text.selectForeground": ON_ACCENT,
        "*Listbox.background": field, "*Listbox.foreground": TEXT, "*Listbox.selectBackground": BLUE,
        "*Listbox.selectForeground": ON_ACCENT,
        "*Checkbutton.selectColor": CARD_BG_ALT if THEME == "light" else "#0B1526",
        "*Checkbutton.activeForeground": TEXT, "*Radiobutton.selectColor": CARD_BG_ALT if THEME == "light" else "#0B1526",
        "*Radiobutton.activeForeground": TEXT,
        "*Menubutton.background": CARD_BG_ALT, "*Menubutton.foreground": TEXT, "*Menubutton.relief": "flat",
        "*Menubutton.activeBackground": FILL, "*Menubutton.activeForeground": TEXT,
        "*Menubutton.highlightThickness": 0, "*Menubutton.borderWidth": 0,
        "*Menu.background": CARD_BG, "*Menu.foreground": TEXT, "*Menu.activeBackground": BLUE,
        "*Menu.activeForeground": ON_ACCENT, "*Scale.troughColor": FILL, "*Scale.background": CARD_BG,
        "*Scale.foreground": TEXT, "*Scale.activeBackground": BLUE, "*Scale.highlightThickness": 0,
        "*Toplevel.background": BG,
    }
    for k, v in opts.items():
        root.option_add(k, v, 60)
    try:
        from tkinter import ttk
        st = ttk.Style(root)
        if st.theme_use() not in ("clam",):
            st.theme_use("clam")
        st.configure(".", background=BG, foreground=TEXT, fieldbackground=field, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, troughcolor=FILL, focuscolor=FOCUS, font=F(11))
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(px(6), px(6), px(6), 0))
        st.configure("TNotebook.Tab", background=CARD_BG_ALT, foreground=TEXT_SECONDARY, padding=(px(14), px(7)),
                     borderwidth=0, font=F(11, "bold"))
        st.map("TNotebook.Tab", background=[("selected", BLUE)], foreground=[("selected", ON_ACCENT)])
        st.configure("TFrame", background=BG)
        st.configure("TCombobox", fieldbackground=field, background=CARD_BG_ALT, foreground=TEXT,
                     arrowcolor=TEXT_SECONDARY)
        st.configure("Vertical.TScrollbar", background=CARD_BG_ALT, troughcolor=BG, arrowcolor=TEXT_MUTED)
        st.configure("TProgressbar", background=BLUE, troughcolor=FILL)
    except Exception:  # noqa: BLE001
        pass


# =====================================================================================
# 9. תיבות סימון ולחצני בחירה חדים: המחוון המובנה של Tk ב-Windows הוא ~13 פיקסלים קבועים - במסך 4K ב-250%
#    הוא זעיר (נמדד בצילום מסך). מחליפים אותו בתמונה (Pillow) בגודל DPI: ריבוע מעוגל / עיגול בכחול האקסנט.
# =====================================================================================
def _check_pil(s, on_, kind, bg):
    gap = px(7)
    W, H = (s + gap) * SS, s * SS
    im = Image.new("RGB", (W, H), _rgb(bg))
    d = ImageDraw.Draw(im)
    x0 = gap * SS
    box = (x0 + SS, SS, W - SS, H - SS)
    edge = mix(TEXT_MUTED, bg, 0.25)
    fill = BLUE if on_ else (CARD_BG if THEME == "light" else "#0B1526")
    bw = max(1, int(px(1.6) * SS))
    if kind == "radio":
        d.ellipse(box, fill=_rgb(BLUE if on_ else edge))
        if not on_:
            d.ellipse((box[0] + bw, box[1] + bw, box[2] - bw, box[3] - bw), fill=_rgb(fill))
        else:
            c = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            r = (box[2] - box[0]) * 0.2
            d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=_rgb(ON_ACCENT))
    else:
        r = (box[2] - box[0]) * 0.24
        d.rounded_rectangle(box, radius=r, fill=_rgb(BLUE if on_ else edge))
        if not on_:
            d.rounded_rectangle((box[0] + bw, box[1] + bw, box[2] - bw, box[3] - bw), radius=max(0, r - bw),
                                fill=_rgb(fill))
        else:
            w = box[2] - box[0]
            pts = [(box[0] + w * 0.24, box[1] + w * 0.52), (box[0] + w * 0.43, box[1] + w * 0.70),
                   (box[0] + w * 0.77, box[1] + w * 0.32)]
            d.line(pts, fill=_rgb(ON_ACCENT), width=max(1, int(w * 0.13)), joint="curve")
    return im.resize((s + gap, s), Image.LANCZOS)


def style_check(widget, kind="check"):
    """מחוון חד בגודל DPI לתיבת סימון / לחצן בחירה קיים (המשתנה, הפקודה והטקסט לא משתנים)."""
    if not HAS_PIL:
        return
    bg = _parent_bg(widget)
    s = px(18)
    off = _photo(widget, ("chk", kind, 0, s, bg, THEME), lambda: _check_pil(s, False, kind, bg))
    onp = _photo(widget, ("chk", kind, 1, s, bg, THEME), lambda: _check_pil(s, True, kind, bg))
    widget.__dict__["_lg_chk"] = (off, onp)
    _raw_configure(widget, image=off, selectimage=onp, indicatoron=0, compound="right", relief="flat",
                   offrelief="flat", overrelief="flat", bd=0, selectcolor=bg, activebackground=bg,
                   activeforeground=TEXT, cursor="hand2")


def _install_checks():
    if getattr(tk, "_lg_checks", False):
        return
    tk._lg_checks = True
    for cls, kind in ((tk.Checkbutton, "check"), (tk.Radiobutton, "radio")):
        orig = cls.__init__

        def __init__(self, master=None, cnf=None, _o=orig, _k=kind, **kw):
            _o(self, master, cnf, **kw)
            if kw.get("indicatoron") in (0, False) or "image" in kw:
                return
            try:
                style_check(self, _k)
            except Exception:  # noqa: BLE001 - מראה בלבד
                pass
        cls.__init__ = __init__


_install_checks()
