# -*- coding: utf-8 -*-
"""
חדות במסכי DPI גבוה (ui_theme): בלי מודעות DPI, Windows מתח את החלון פי 2.5 (טקסט מטושטש). עכשיו התהליך
DPI-aware, וכל מידה בפיקסלים מוכפלת במקום אחד. בודקים בתהליך נפרד עם מקדם מאולץ 2.5 (LIFEGUARD_DPI_SCALE),
ובתהליך הבדיקות עצמו - שבמקדם 1.0 שום דבר לא משתנה.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r"""
import tkinter as tk
import ui_theme as T
f = T.enable_dpi_awareness()
assert abs(f - 2.5) < 1e-9, f
root = tk.Tk()
root.withdraw()
# גופנים בנקודות: tk scaling = DPI/72 -> חדים, לא מוכפלים פעמיים
assert abs(float(root.tk.call("tk", "scaling")) - 2.5 * 96 / 72) < 0.01
fr = tk.Frame(root, width=100, height=40, padx=4)
assert int(fr.cget("width")) == 250 and int(fr.cget("height")) == 100 and int(fr.cget("padx")) == 10
fr.configure(width=10)
assert int(fr.cget("width")) == 25
lab = tk.Label(root, text="שלום", width=12, padx=8, wraplength=200)
assert int(lab.cget("width")) == 12                      # תווים - לא מוכפל
assert int(lab.cget("padx")) == 20
assert lab.cget("wraplength") == 200                    # מוחזר במידה הלוגית
ent = tk.Entry(root, width=16, highlightthickness=2)
assert int(ent.cget("width")) == 16 and int(ent.cget("highlightthickness")) == 5
cv = tk.Canvas(root, width=40, height=40)
assert int(cv.cget("width")) == 100
assert T._scale_geometry("1280x820+20+10") == "3200x2050+50+25"
assert T.px(3) == 8 and T.unpx(250) == 100 and T.px(0) == 0
lab.pack(padx=(4, 0), pady=2)
info = lab.pack_info()
px_ = info["padx"]
assert (px_[0] if isinstance(px_, (tuple, list)) else int(str(px_).split()[0])) == 10, info
assert int(info["pady"]) == 5, info
# רכיבים מצוירים (Pillow) בגודל פיזי
card = T.RoundedCard(root, padding=16)
assert card.padding == 40 and card.radius == T.px(T.RADIUS_CARD)
b = T.big_button(root, "אישור", lambda: None)
root.update_idletasks()
assert b.winfo_reqheight() >= T.px(30)
print("OK")
"""


def test_forced_factor_scales_pixels_not_fonts_or_characters():
    env = dict(os.environ, LIFEGUARD_DPI_SCALE="2.5", LIFEGUARD_DPI_AWARE="1")
    r = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


def test_disabled_awareness_keeps_factor_one():
    env = dict(os.environ, LIFEGUARD_DPI_AWARE="0")
    code = "import ui_theme as T; assert T.enable_dpi_awareness() == 1.0; assert T.px(14) == 14; print('OK')"
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]


def test_factor_one_in_tests_is_identity(shared_root):  # noqa: F811
    import tkinter as tk
    import ui_theme as T
    assert T.dpi_factor() == 1.0
    fr = tk.Frame(shared_root, width=100, padx=4)
    try:
        assert int(fr.cget("width")) == 100 and int(fr.cget("padx")) == 4
    finally:
        fr.destroy()


def test_map_environment_helpers_are_honest():
    import map_env_ui as M
    s, w, n, e = M.clamp_bbox(32.0, 34.7, 32.2, 34.9)
    assert round(n - s, 5) <= M.MAX_SPAN and round(e - w, 5) <= M.MAX_SPAN      # מגבלת השרת (~3 ק"מ)
    assert abs((s + n) / 2 - 32.1) < 1e-6
    assert M.lighting_color(None) is None and M.lighting_color(0) is None       # "אין נתונים" לא נצבע
    assert M.lighting_color(1) == M.SCALE[0] and M.lighting_color(9) == M.SCALE[2]
    marks = M.cell_marks({"lighting": 4, "accident_hotspot": True, "nightlife": None, "reports": 2},
                         {"lighting": True, "accidents": True, "nightlife": True, "reports": False})
    assert [m[0] for m in marks] == ["lighting", "accidents"]
    reds = {"#ff0000", "#d70015", "#ff4f45", "#a50011"}
    assert not reds & {c.lower() for c in M.SCALE}                              # אף פעם לא אזורים באדום


from test_ui_navigation import shared_root  # noqa: E402,F401
