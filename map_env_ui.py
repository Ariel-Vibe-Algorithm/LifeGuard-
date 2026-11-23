# -*- coding: utf-8 -*-
"""
map_env_ui.py - "מפת סביבה" על המפה של Windows (GET /v1/map/environment, server/heatmap.py).

כללים (design/DESIGN_SYSTEM_HE.md, "שכבות המפה"):
- גורמי סביבה ברמת רחוב בלבד: תאורה רשומה, מוקדי תאונות הולכי רגל, אזור בילוי פעיל (בלילה בלבד),
  דיווחים מאומתים/מאוששים. לא "מפת סיכון שכונתית", לא אזורים באדום, לא המילה "מסוכן".
- סקאלה רציפה בגווני כחול-נייבי; מקרא שמציין לכל שכבה מה היא ומה המקור (מהשרת).
- מתג לכל שכבה. בלי שרת / שכבה לא זמינה באזור -> לא מציירים, ואומרים את זה בשקט במקרא.
- השרת מגביל בקשה ל~3 ק"מ (MAX_SPAN_DEG = 0.03): מבקשים רק את מרכז התצוגה, עד 0.028 מעלות, ורק מזום 14.
אריחי המפה (OSM) בהירים גם במצב כהה, לכן הסקאלה על המפה היא תמיד הסקאלה הבהירה.
"""

import time
import tkinter as tk

import ui_theme as T

MAX_SPAN = 0.028            # קצת מתחת ל-MAX_SPAN_DEG של השרת (0.03)
MIN_ZOOM = 14
# סקאלה רציפה נייבי-כחול (מעט -> הרבה) - כהה מספיק כדי להיראות על אריחים בהירים
SCALE = ("#A9C3F2", "#4F86E8", "#0A1F44")
LAYERS = (
    ("lighting", "💡", "תאורה רשומה", "רישום ≠ תאורה שעובדת"),
    ("accidents", "🚸", "מוקדי תאונות הולכי רגל", "סיכון תנועה, לא פשיעה"),
    ("nightlife", "🌙", "אזור בילוי פעיל", "בלילה בלבד"),
    ("reports", "📍", "דיווחים מאומתים", "דיווח יחיד לא מוצג"),
)


def clamp_bbox(s, w, n, e, max_span=MAX_SPAN):
    """חותך את התצוגה לריבוע במרכזה, לכל היותר max_span מעלות בכל ציר (מגבלת השרת)."""
    s, n = min(s, n), max(s, n)
    w, e = min(w, e), max(w, e)
    cy, cx = (s + n) / 2, (w + e) / 2
    hy, hx = min((n - s) / 2, max_span / 2), min((e - w) / 2, max_span / 2)
    return round(cy - hy, 5), round(cx - hx, 5), round(cy + hy, 5), round(cx + hx, 5)


def lighting_color(poles, cuts=(2, 5)):
    """0 = לא מציירים (אין עמודים רשומים או אין נתונים); שלוש דרגות כחול לפי ספי שלישונים (ברירת מחדל 2 / 5)."""
    if not poles:
        return None
    return SCALE[0] if poles <= cuts[0] else SCALE[1] if poles <= cuts[1] else SCALE[2]


def tertile_cuts(cells):
    """ספים יחסיים לאזור המוצג: בעיר כמעט כל תא "הרבה" במספרים מוחלטים - שלישונים מראים הבדל אמיתי."""
    vals = sorted(c["lighting"] for c in cells if c.get("lighting"))
    if len(vals) < 6:
        return (2, 5)
    return (vals[len(vals) // 3], vals[(2 * len(vals)) // 3])


def cell_marks(cell, enabled, cuts=(2, 5)):
    """מה לצייר בתא: רשימת (layer, style). פונקציה טהורה - נבדקת בלי Tk."""
    out = []
    if enabled.get("nightlife") and cell.get("nightlife"):
        out.append(("nightlife", SCALE[1]))
    if enabled.get("lighting"):
        col = lighting_color(cell.get("lighting"), cuts)
        if col:
            out.append(("lighting", col))
    if enabled.get("accidents") and cell.get("accident_hotspot"):
        out.append(("accidents", SCALE[2]))
    if enabled.get("reports") and (cell.get("reports") or 0) > 0:
        out.append(("reports", SCALE[2]))
    return out


def view_bbox(map_widget):
    """(s, w, n, e) של מה שמוצג עכשיו במפה."""
    from tkintermapview.utility_functions import osm_to_decimal
    z = round(map_widget.zoom)
    n, w = osm_to_decimal(*map_widget.upper_left_tile_pos, z)
    s, e = osm_to_decimal(*map_widget.lower_right_tile_pos, z)
    return s, w, n, e


class EnvironmentLayer:
    """כרטיס צף על המפה (מתגים + מקרא + מקור) ושכבת נקודות על קנבס המפה."""

    TAG = "lg_env"

    def __init__(self, map_widget, app, holder):
        self.map, self.app, self.holder = map_widget, app, holder
        get = getattr(app, "get_setting", lambda k, d=None: d)
        self.vars = {k: tk.BooleanVar(master=holder, value=get(f"env_layer_{k}", "1") == "1") for k, *_ in LAYERS}
        self.data = None
        self._fetched = None            # (bbox, time)
        self._after = None
        self._ref = None                # (lat, lon, x, y) לנקודת ייחוס - להזזה זולה בגרירה
        self._collapsed = False
        self._build_card()
        self._hook()

    # ---------------------------------------------------------- כרטיס
    def _build_card(self):
        bg = T.CARD_BG
        self.card = tk.Frame(self.holder, bg=bg, highlightthickness=1, highlightbackground=T.BORDER)
        self.card.place(in_=self.map, relx=0.0, rely=1.0, x=12, y=-12, anchor="sw")
        head = tk.Frame(self.card, bg=bg)
        head.pack(fill="x", padx=12, pady=(10, 4))
        T.label(head, "🗺️ שכבות סביבה", 12, T.TEXT, bold=True).pack(side="right")
        self.toggle_btn = T.plain_button(head, "הסתר", self.collapse, size=10)
        self.toggle_btn.pack(side="left")
        self.body = tk.Frame(self.card, bg=bg)
        self.body.pack(fill="x", padx=12, pady=(0, 10))
        self.rows = {}
        for key, icon, title, sub in LAYERS:
            row = tk.Frame(self.body, bg=bg)
            row.pack(fill="x", pady=2)
            sw = T.Toggle(row, self.vars[key], command=lambda k=key: self._on_toggle(k), bg=bg)
            sw.pack(side="left")
            sw_icon = tk.Canvas(row, width=16, height=16, bg=bg, highlightthickness=0)
            sw_icon.pack(side="right", padx=(6, 0))
            self._swatch(sw_icon, key)
            txt = tk.Frame(row, bg=bg)
            txt.pack(side="right", fill="x", expand=True)
            name = T.label(txt, f"{icon} {title}", 11, T.TEXT, anchor="e")
            name.pack(fill="x")
            src = T.label(txt, sub, 9, T.TEXT_MUTED, anchor="e", wraplength=250)
            src.pack(fill="x")
            self.rows[key] = (name, src)
        scale = tk.Frame(self.body, bg=bg)
        scale.pack(fill="x", pady=(6, 0))
        T.label(scale, "מעט", 9, T.TEXT_MUTED).pack(side="right")
        bar = tk.Canvas(scale, width=90, height=10, bg=bg, highlightthickness=0)
        bar.pack(side="right", padx=6)
        T.label(scale, "הרבה", 9, T.TEXT_MUTED).pack(side="right")
        self._scale_bar(bar)
        self.status = T.label(self.body, "גורמי סביבה ברמת רחוב - לא ציון סכנה לשכונה.", 9, T.TEXT_SECONDARY,
                              anchor="e", wraplength=290)
        self.status.pack(fill="x", pady=(6, 0))

    def _swatch(self, cv, key):
        s = T.px(16)
        c = s / 2
        r = T.px(5)
        if key == "nightlife":
            cv.create_rectangle(T.px(2), T.px(2), s - T.px(2), s - T.px(2), outline=SCALE[2], width=T.px(1.5),
                                dash=(3, 2))
        elif key == "accidents":
            cv.create_oval(c - r - T.px(1), c - r - T.px(1), c + r + T.px(1), c + r + T.px(1), outline=SCALE[2],
                           width=T.px(2))
        elif key == "reports":
            cv.create_polygon(c, c - r - 1, c + r + 1, c, c, c + r + 1, c - r - 1, c, fill=SCALE[2], outline="white")
        else:
            cv.create_rectangle(T.px(2), T.px(2), s - T.px(2), s - T.px(2), fill=SCALE[2], outline="", stipple="gray25")

    def _scale_bar(self, cv):
        w, h = T.px(90), T.px(10)
        for i, col in enumerate(SCALE):
            cv.create_rectangle(w - (i + 1) * w / 3, 0, w - i * w / 3, h, fill=col, outline="")

    def collapse(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.body.pack_forget()
            self.toggle_btn.configure(text="הצג")
        else:
            self.body.pack(fill="x", padx=12, pady=(0, 10))
            self.toggle_btn.configure(text="הסתר")

    def _on_toggle(self, key):
        try:
            self.app.set_setting(f"env_layer_{key}", "1" if self.vars[key].get() else "0")
        except Exception:  # noqa: BLE001
            pass
        self.redraw()
        self.schedule(0)

    # ---------------------------------------------------------- מפה
    def _hook(self):
        orig_move, orig_zoom = self.map.draw_move, self.map.draw_zoom

        def move(*a, **k):
            r = orig_move(*a, **k)
            self._follow()
            self.schedule()
            return r

        def zoom(*a, **k):
            r = orig_zoom(*a, **k)
            self.redraw()
            self.schedule()
            return r
        self.map.draw_move, self.map.draw_zoom = move, zoom

    def schedule(self, ms=700):
        try:
            if self._after:
                self.holder.after_cancel(self._after)
            self._after = self.holder.after(ms, self.fetch)
        except tk.TclError:
            pass

    def _set_status(self, text, color=None):
        try:
            self.status.configure(text=text, fg=color or T.TEXT_SECONDARY)
        except tk.TclError:
            pass

    def fetch(self):
        self._after = None
        if not any(v.get() for v in self.vars.values()):
            self.clear()
            self._set_status("כל השכבות כבויות.")
            return
        c = getattr(self.app, "client", None)
        if c is None or not getattr(c, "token", None):
            self.data = None
            self.clear()
            self._set_status("שכבות הסביבה דורשות חיבור לשרת LifeGuard - כרגע לא מוצגות.", T.TEXT_MUTED)
            return
        try:
            if self.map.zoom < MIN_ZOOM:
                self.clear()
                self._set_status("התקרב/י במפה (עד ~3 ק\"מ) כדי לראות שכבות סביבה.")
                return
            bbox = clamp_bbox(*view_bbox(self.map))
        except Exception:  # noqa: BLE001 - המפה עוד לא מוכנה
            return
        if self._fetched and self._fetched[0] == bbox and time.time() - self._fetched[1] < 120:
            self.redraw()
            return
        self._fetched = (bbox, time.time())
        params = {"bbox": ",".join(str(x) for x in bbox), "at": int(time.time())}
        from app_screens import run_bg
        run_bg(self.app, lambda: c._req("GET", "/map/environment", params=params), self._apply, self._fail)

    def _fail(self, exc):
        self.data = None
        self._fetched = None
        self.clear()
        self._set_status(f"שכבות הסביבה לא זמינות כרגע ({getattr(exc, 'message', None) or 'אין חיבור'}).",
                         T.TEXT_MUTED)

    def _apply(self, data):
        try:
            if not self.holder.winfo_exists():
                return
        except tk.TclError:
            return
        self.data = data
        layers = data.get("layers") or {}
        night = data.get("phase") == "night"
        self._cuts = tertile_cuts(data.get("cells") or [])
        for key, icon, title, sub in LAYERS:
            meta = layers.get(key) or {}
            name, src = self.rows[key]
            avail = meta.get("available")
            source = meta.get("source")
            parts = [f"מקור: {source}" if source else sub]
            if not avail:
                parts.insert(0, "אין נתונים כאן" if key != "nightlife" or night else "בלילה בלבד")
            src.configure(text=" · ".join(parts))
            name.configure(fg=T.TEXT if avail else T.TEXT_MUTED)
        self._set_status((data.get("disclaimer") or "גורמי סביבה ברמת רחוב - לא ציון סכנה לשכונה.")
                         + (" · לילה" if night else ""))
        self.redraw()

    def clear(self):
        try:
            self.map.canvas.delete(self.TAG)
        except (tk.TclError, AttributeError):
            pass
        self._ref = None

    def _xy(self, lat, lon):
        from tkintermapview.utility_functions import decimal_to_osm
        m = self.map
        tx, ty = decimal_to_osm(lat, lon, round(m.zoom))
        ww = m.lower_right_tile_pos[0] - m.upper_left_tile_pos[0]
        wh = m.lower_right_tile_pos[1] - m.upper_left_tile_pos[1]
        return (tx - m.upper_left_tile_pos[0]) / ww * m.width, (ty - m.upper_left_tile_pos[1]) / wh * m.height

    def _follow(self):
        """גרירה: מזיזים את כל השכבה יחד (זול), במקום לצייר מחדש מאות פריטים בכל תזוזת עכבר."""
        if self._ref is None:
            return
        lat, lon, x0, y0 = self._ref
        try:
            x, y = self._xy(lat, lon)
        except Exception:  # noqa: BLE001
            return
        dx, dy = x - x0, y - y0
        if dx or dy:
            self.map.canvas.move(self.TAG, dx, dy)
            self._ref = (lat, lon, x, y)

    def redraw(self):
        self.clear()
        d = self.data
        if not d or not d.get("cells"):
            return
        enabled = {k: v.get() for k, v in self.vars.items()}
        if not any(enabled.values()):
            return
        try:
            if self.map.zoom < MIN_ZOOM:
                return
            cell = float(d.get("cell_deg") or 0.001)
            c0 = d["cells"][0]
            x0, y0 = self._xy(c0["lat"], c0["lon"])
            x1, y1 = self._xy(c0["lat"] + cell, c0["lon"] + cell)
        except Exception:  # noqa: BLE001
            return
        cpx = max(2.0, abs(x1 - x0))
        cpy = max(2.0, abs(y1 - y0))            # מעלת רוחב ≠ מעלת אורך בפיקסלים - תאים מלבניים, בלי פסים ריקים
        cv = self.map.canvas
        W, H = self.map.width, self.map.height
        tags = ("polygon", self.TAG)          # tkintermapview מרים "polygon" מעל האריחים ומתחת למסלולים/סמנים
        cuts = getattr(self, "_cuts", (2, 5))
        for cl in d["cells"]:
            marks = cell_marks(cl, enabled, cuts)
            if not marks:
                continue
            x, y = self._xy(cl["lat"], cl["lon"])
            if x < -cpx or y < -cpy or x > W + cpx or y > H + cpy:
                continue
            for layer, col in marks:
                if layer == "nightlife":
                    hx, hy = cpx / 2, cpy / 2
                    cv.create_rectangle(x - hx + 1, y - hy + 1, x + hx - 1, y + hy - 1, outline=SCALE[2], width=T.px(1.5),
                                        dash=(4, 3), tags=tags)
                elif layer == "lighting":
                    # תא שקוף-למחצה (stipple): המפה נראית מתחתיו - "משטח" רציף של צפיפות, לא נקודות כבדות
                    hx, hy = cpx / 2, cpy / 2
                    cv.create_rectangle(x - hx, y - hy, x + hx, y + hy, fill=col, outline="", stipple="gray25",
                                        tags=tags)
                elif layer == "accidents":
                    r = max(T.px(4), min(T.px(9), cpx * 0.2))
                    cv.create_oval(x - r, y - r, x + r, y + r, outline=col, width=T.px(1.5), tags=tags)
                elif layer == "reports":
                    r = T.px(6)
                    cv.create_polygon(x, y - r, x + r, y, x, y + r, x - r, y, fill=col, outline="white",
                                      width=T.px(1), tags=tags)
        self._ref = (c0["lat"], c0["lon"], x0, y0)
        try:
            self.map.manage_z_order()
        except Exception:  # noqa: BLE001
            pass
