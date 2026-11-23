# -*- coding: utf-8 -*-
"""
app_screens.py
מסכי האפליקציה הראשית בעיצוב החדש: בית, מפה, קהילה, פרופיל.

כל מסך מחובר לפונקציונליות אמיתית (מנוע, שרת LifeGuard, מיקום, מפות). פעולות רשת רצות
ב-thread רקע, והעדכון לממשק עובר דרך app._post (thread-safe).
אין נתונים מומצאים: כשאין מידע (למשל דירוג סיכון לרחובות) המסך אומר זאת במפורש.
"""

import threading
import time
import tkinter as tk
import webbrowser

import config
import location_module
import map_hidpi
import map_tiles
import map_module
import street_intel
import ui_theme as T
from backend_client import BackendError

try:
    from tkintermapview import TkinterMapView
    HAS_MAP = True
except Exception:
    HAS_MAP = False

EMERGENCY_TYPE_LABELS = {
    "unknown": "לא צוין", "medical": "רפואי", "fire": "שריפה", "earthquake": "רעידת אדמה",
    "security": "ביטחוני", "physical_danger": "סכנה אישית", "road_accident": "תאונת דרכים",
    "lost": "הלך/ה לאיבוד", "missing_person": "אדם נעדר", "flood_weather": "מזג אוויר",
    "power_outage": "הפסקת חשמל",
}
SEVERITY_LABELS = {"unknown": "לא ידועה", "low": "נמוכה", "medium": "בינונית", "high": "גבוהה", "critical": "קריטית"}
STATE_LABELS = {"notified": "נשלח", "delivered": "נמסר לאפליקציה", "accepted": "קיבל/ה", "declined": "לא זמין/ה",
                "expired": "לא הגיב/ה בזמן", "revoked": "הגישה בוטלה"}


def run_bg(app, work, done=None, error=None):
    """מריץ work() ב-thread; done(result)/error(exc) רצים על ה-UI thread."""
    def _t():
        try:
            res = work()
        except Exception as exc:  # noqa: BLE001 - מוצג למשתמש
            if error:
                app._post(error, exc)
            return
        if done:
            app._post(done, res)
    threading.Thread(target=_t, daemon=True).start()


def section_header(parent, title, subtitle=None):
    """כותרת כרטיס: כותרת (headline) + שורת הסבר מעומעמת שנשברת לפי הרוחב."""
    T.label(parent, title, 13, T.TEXT, bold=True, anchor="e").pack(fill="x")
    if subtitle:
        s = T.label(parent, subtitle, 10, T.TEXT_MUTED, anchor="e", wraplength=420)
        s.pack(fill="x", pady=(2, 6))
        T.autowrap(s)


# =====================================================================================
_HE_DAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")


def _today_he():
    t = time.localtime()
    return f"יום {_HE_DAYS[t.tm_wday]}, {t.tm_mday}.{t.tm_mon}.{t.tm_year}"


def _tile_clickable(widgets, cmd):
    for w in widgets:
        w.bind("<Button-1>", lambda e: cmd(), add="+")
        try:
            w.configure(cursor="hand2")
        except tk.TclError:
            pass


class HomeScreen(tk.Frame):
    """
    בית (בהשראת Apple Health): מצב במבט אחד, SOS כגיבור המסך, קיצורי דרך כאריחים נקיים, וכלים.
    עמודה ימנית = הראשית (בעברית העין מתחילה מימין).
    """

    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        outer, body = T.scrollable(self)
        outer.pack(fill="both", expand=True)
        head = tk.Frame(body, bg=T.BG)
        head.pack(fill="x", padx=26, pady=(18, 2))
        T.label(head, f"שלום, {app.display_name}", 24, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(head, _today_he(), 11, T.TEXT_MUTED, anchor="e").pack(fill="x")
        cols = tk.Frame(body, bg=T.BG)
        cols.pack(fill="both", expand=True, padx=20, pady=(8, 16))
        right = tk.Frame(cols, bg=T.BG)
        left = tk.Frame(cols, bg=T.BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        cols.grid_columnconfigure(1, weight=11, uniform="home")
        cols.grid_columnconfigure(0, weight=9, uniform="home")
        self._build_status(right)
        self._build_sos(right)
        self._build_shortcuts(left)
        self._build_tools(left)
        import features_ui
        self.escort_watch = features_ui.EscortWatchCard(right, app)   # מוצג רק כשמישהו בדרך
        self.national = features_ui.NationalCard(right, app)
        import support_ui
        self.support = support_ui.SupportCard(right, app)              # "צריך/ה לדבר עם מישהו?" (לא חירום)
        self.diagnostics = features_ui.DiagnosticsCard(left, app)
        self.readiness = features_ui.ReadinessCard(left, app)

    def on_show(self):
        self.refresh_extras()

    def refresh_extras(self):
        self.escort_watch.refresh()
        self.readiness.refresh()
        self.diagnostics.refresh()
        self.national.refresh()

    # ---------- מצב במבט אחד ----------
    def _build_status(self, parent):
        c, b = T.card(parent)
        c.pack(fill="x", pady=(0, 14))
        top = tk.Frame(b, bg=T.CARD_BG)
        top.pack(fill="x")
        T.label(top, "מצב הבטיחות שלך", 11, T.TEXT_MUTED, bold=True).pack(side="right")
        self.app.recording_label = T.chip(top, "🎙️ בודק מיקרופון...", "not_verified")
        self.app.recording_label.pack(side="left")
        self.app.status_label = T.label(b, "● ממתין", 22, T.GREEN, bold=True, anchor="e")
        self.app.status_label.pack(fill="x", pady=(6, 2))
        self.app.reach_label = T.label(b, "מתחבר לשרת LifeGuard...", 12, T.TEXT_SECONDARY, anchor="e",
                                       wraplength=440)
        self.app.reach_label.pack(fill="x")
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=(10, 0))
        self.app.connectivity_label = T.chip(row, "🌐 בודק חיבור...", "not_verified")
        self.app.connectivity_label.pack(side="right", padx=(0, 6))
        self.app.server_chip = T.chip(row, "📡 שרת: מתחבר...", "pending")
        self.app.server_chip.pack(side="right", padx=6)
        self.app.health_banner = T.Chip(b, "", T.RED_BG, T.RED, size=11, padx=12, pady=8, radius=T.px(10))
        self.app.health_banner.configure(wraplength=440, justify="right", anchor="e", cursor="hand2")
        self.app.health_banner.bind("<Button-1>", lambda e: self.app._open_health())
        for w in (self.app.reach_label, self.app.health_banner):
            T.autowrap(w)        # שבירת שורות לפי הרוחב האמיתי (440 קבוע חתך טקסט בחלון צר)

    # ---------- SOS: גיבור המסך ----------
    def _build_sos(self, parent):
        c, b = T.card(parent, padding=18)
        c.pack(fill="x", pady=(0, 14))
        self.sos_card = c
        size = int(236 * T.get_scale())                 # מידה לוגית; הציור עצמו בפיקסלים פיזיים
        S = T.px(size)
        cv = tk.Canvas(b, width=size, height=size, bg=T.CARD_BG, highlightthickness=0, bd=0, takefocus=True,
                       cursor="hand2")
        cv.pack(pady=(2, 8))
        st = {"hover": False, "pressed": False, "focus": False}
        img = cv.create_image(0, 0, anchor="nw")
        m = S / 2
        if not T.HAS_PIL:
            r = m - T.px(34)
            cv.create_oval(m - r, m - r, m + r, m + r, fill=T.SOS_FILL, outline="")
        cv.create_text(m, m - T.px(14), text="SOS", fill="#FFFFFF", font=T.F(34, "bold"))
        cv.create_text(m, m + T.px(28), text="לחץ/י לחירום", fill="#FFFFFF", font=T.F(12, "bold"))

        def paint():
            if not T.HAS_PIL:
                return
            state = "pressed" if st["pressed"] else "hover" if st["hover"] else "normal"
            key = ("sos", S, state, st["focus"], T.CARD_BG, T.SOS_FILL, T.FOCUS)
            ph = T._photo(cv, key, lambda: T.sos_pil(S, T.CARD_BG, state, st["focus"], T.FOCUS))
            cv._lg_img = ph
            cv.itemconfigure(img, image=ph)

        def trigger():
            # קודם שולחים - אחר כך (אם בכלל) משנים מראה. שום עיכוב לפני ההפעלה.
            self.app.engine.trigger(source="button")

        def press(_e):
            trigger()
            st["pressed"] = True
            paint()

        def flag(k, v):
            st[k] = v
            if k == "hover" and not v:
                st["pressed"] = False
            paint()
        cv.bind("<Button-1>", press)
        cv.bind("<ButtonRelease-1>", lambda e: flag("pressed", False))
        cv.bind("<Enter>", lambda e: flag("hover", True))
        cv.bind("<Leave>", lambda e: flag("hover", False))
        cv.bind("<FocusIn>", lambda e: flag("focus", True))
        cv.bind("<FocusOut>", lambda e: flag("focus", False))
        T.bind_activate(cv, trigger)
        paint()
        self.app.sos_button = cv
        T.label(b, f"או {config.TRIGGER_HOTKEY} מכל מקום · Enter כשהכפתור מסומן", 10, T.TEXT_MUTED).pack()
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(pady=(12, 2))
        self.app.voice_trigger_btn = T.big_button(row, "🎙️ פקודת קול: פעילה", self.app._toggle_voice_trigger,
                                                  bg=T.LIGHT_BLUE, size=11, padx=16, pady=8)
        self.app.voice_trigger_btn.pack(side="right", padx=5)
        T.big_button(row, "🆘 PANIC - מיידי", lambda: self.app.engine.trigger(force_panic=True, source="panic_button"),
                     bg=T.RED_DARK, size=11, padx=16, pady=8).pack(side="right", padx=5)
        T.label(b, "PANIC שולח מיד, בלי בדיקת סכנה ובלי ספירה לאחור", 10, T.TEXT_MUTED).pack(pady=(6, 0))

    # ---------- קיצורי דרך: אריחים ----------
    def _build_shortcuts(self, parent):
        T.section_label(parent, "קיצורי דרך")
        grid = tk.Frame(parent, bg=T.BG)
        grid.pack(fill="x", pady=(0, 6))
        items = [
            ("📍", "מפת בטיחות", "מיקום, מקום בטוח ומסלול", T.LIGHT_BLUE, T.LINK, lambda: self.app.show_screen("map")),
            ("🛡️", "התוכנית שלי", "נקודות מפגש, יעדים, מידע רפואי", T.GREEN_BG, T.GREEN,
             lambda: self.app._open_safety_center("התוכנית שלי")),
            ("📋", "הכנה לחירום", "תרחישים וצ'קליסטים", T.YELLOW_BG, T.YELLOW, lambda: self.app._open_safety_center()),
            ("🕘", "היסטוריית אירועים", "ציר זמן, דוחות ומחיקה", T.FILL, T.TEXT_SECONDARY, self.app._open_history),
        ]
        for i, (icon, title, sub, tint, fg, cmd) in enumerate(items):
            c, b = T.card(grid, padding=14)
            c.grid(row=i // 2, column=1 - (i % 2), sticky="nsew", padx=(0 if i % 2 == 0 else 0, 0), pady=0)
            tile = T.icon_tile(b, icon, tint, fg, size=40, font_size=15)
            tile.pack(anchor="e")
            btn = tk.Button(b, text=title, font=T.F(13, "bold"), fg=T.TEXT, bg=T.CARD_BG, anchor="e",
                            activebackground=T.CARD_BG, activeforeground=T.LINK, bd=0, cursor="hand2", command=cmd,
                            takefocus=True, highlightthickness=2, highlightcolor=T.FOCUS,
                            highlightbackground=T.CARD_BG, padx=0, pady=2)
            T.bind_activate(btn, cmd)
            btn.pack(fill="x", pady=(8, 0))
            sub_l = T.label(b, sub, 10, T.TEXT_MUTED, anchor="e")
            sub_l.pack(fill="x")
            _tile_clickable((b, tile, sub_l), cmd)
            btn.bind("<Enter>", lambda e, w=btn: w.configure(fg=T.LINK), add="+")
            btn.bind("<Leave>", lambda e, w=btn: w.configure(fg=T.TEXT), add="+")
        grid.grid_columnconfigure(0, weight=1, uniform="tiles")
        grid.grid_columnconfigure(1, weight=1, uniform="tiles")

    # ---------- כלים ----------
    def _build_tools(self, parent):
        T.section_label(parent, "כלים")
        # מספר חירום
        c, b = T.card(parent)
        c.pack(fill="x", pady=(0, 12))
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x")
        T.label(row, "📞 מספר חירום מקומי", 12, T.TEXT, bold=True).pack(side="right")
        self.app.emergency_number_label = T.label(row, "...", 24, T.RED, bold=True)
        self.app.emergency_number_label.pack(side="left")
        self.app.emergency_detail_label = T.label(b, "מזהה מדינה...", 10, T.TEXT_SECONDARY, anchor="e", wraplength=400)
        self.app.emergency_detail_label.pack(fill="x", pady=(2, 0))
        T.autowrap(self.app.emergency_detail_label)

        # ליווי הביתה (טיימר בטיחות)
        c, b = T.card(parent)
        c.pack(fill="x", pady=(0, 12))
        top = tk.Frame(b, bg=T.CARD_BG)
        top.pack(fill="x")
        T.label(top, "⏳ ליווי הביתה", 12, T.TEXT, bold=True).pack(side="right")
        self.app.safety_timer_status_label = T.label(top, "לא פעיל", 11, T.TEXT_SECONDARY, bold=True)
        self.app.safety_timer_status_label.pack(side="left")
        desc = T.label(b, "אנשי הקשר שלך רואים את המיקום שלך בדרך. אם לא תאשר/י הגעה בזמן - תיפתח קריאת חירום, "
                          "גם אם המחשב כבה (השרת אוכף את הזמן).", 10, T.TEXT_MUTED, anchor="e", wraplength=400)
        desc.pack(fill="x", pady=(2, 0))
        T.autowrap(desc)
        self.app.safety_timer_progress = T.ProgressBar(b, height=6, bg=T.CARD_BG)
        holder = tk.Frame(b, bg=T.CARD_BG)
        holder.pack(fill="x", pady=(10, 0))
        self.app.safety_timer_holder = holder
        self.app.safety_timer_btn = T.primary_button(holder, "▶ הפעל טיימר", self.app._open_safety_timer_dialog,
                                                     size=11, pady=7)
        self.app.safety_timer_btn.pack(side="right")
        self.app.safety_timer_actions = tk.Frame(holder, bg=T.CARD_BG)
        T.big_button(self.app.safety_timer_actions, "✅ הגעתי", self.app._confirm_safe_arrival, bg=T.GREEN,
                     size=10, pady=6).pack(side="right", padx=3)
        T.big_button(self.app.safety_timer_actions, "+10 דק'", lambda: self.app._extend_safety_timer(10),
                     size=10, pady=6).pack(side="right", padx=3)
        T.big_button(self.app.safety_timer_actions, "🚫 בטל", self.app._cancel_safety_timer, size=10,
                     pady=6).pack(side="right", padx=3)

        # הרתעה
        c, b = T.card(parent)
        c.pack(fill="x", pady=(0, 12))
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x")
        self.app.deterrent_on = False
        self.app.deterrent_btn = T.big_button(row, "📢 הרתעה קולית", self.app._toggle_deterrent_mode,
                                              bg=T.CARD_BG_ALT, size=11, pady=7)
        self.app.deterrent_btn.pack(side="right")
        T.label(row, "אזהרה קולית בלי לשלוח התראה", 10, T.TEXT_MUTED, anchor="e").pack(side="right", padx=10)


# =====================================================================================
MODE_SHORT = {"fastest": "⚡ מהיר", "safest": "🛡️ בטוח יותר", "balanced": "⚖️ מאוזן"}
MODE_DESC = {
    "fastest": "המסלול הקצר ביותר בזמן הליכה (OSRM).",
    "safest": "בטוח יותר לפי הנתונים: הכי פחות גורמי סיכון רשומים, עד פי 1.5 מזמן המהיר.",
    "balanced": "מאוזן: דקות + 1 לכל נקודת סיכון לפי הנתונים.",
}
_LEVEL_HE = {"high": "גבוה", "medium": "בינוני", "low": "נמוך"}


class MapScreen(tk.Frame):
    """
    מפה (בהשראת Apple Maps): המפה גדולה משמאל, לוח בקרה מימין. בקרים צפים על המפה (זום, מיקום, שכבות
    סביבה + מקרא). מצבי מסלול = בקר מקטעים עם כרטיס השוואה; המסלול הנבחר בכחול, החלופות באפור.
    """

    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.origin = None          # dict lat/lon/precise/label
        self.destination = None     # dict lat/lon/name
        self._markers = {}
        self._radius_poly = None
        self._path = None
        self._alt_paths = []
        self._last_mode = "fastest"
        self._last_modes = None
        self.env = None
        try:
            self.radius_km = float(app.get_setting("safety_radius_km", "1"))
        except ValueError:
            self.radius_km = 1.0

        panel_outer, panel = T.scrollable(self)
        panel_outer.configure(width=int(410 * T.get_scale()))
        panel_outer.pack(side="right", fill="y", padx=(0, 14), pady=14)
        panel_outer.pack_propagate(False)
        map_holder = tk.Frame(self, bg=T.BORDER)
        map_holder.pack(side="right", fill="both", expand=True, padx=(16, 10), pady=16)
        self.map_holder = map_holder
        if HAS_MAP:
            self.map = map_hidpi.prepare(map_tiles.configure(TkinterMapView(map_holder, corner_radius=0)))
            self.map.pack(fill="both", expand=True, padx=1, pady=1)
            self.map.set_position(31.77, 35.21)
            self.map.set_zoom(8)
            self.map.add_left_click_map_command(self._on_map_click)
            try:
                self.map.canvas.delete("button")       # כפתורי הזום הזעירים של הספרייה - מוחלפים בבקרים שלנו
            except tk.TclError:
                pass
            self._build_map_controls(map_holder)
            try:
                import map_env_ui
                self.env = map_env_ui.EnvironmentLayer(self.map, app, map_holder)
            except Exception:  # noqa: BLE001 - שכבת הסביבה לעולם לא מפילה את המפה
                self.env = None
        else:
            self.map = None
            T.label(map_holder, "⚠️ רכיב המפה לא מותקן (tkintermapview)", 13, T.RED, bg=T.BG).pack(pady=40)

        self._build_panel(panel)

    def _build_map_controls(self, holder):
        """זום + "המיקום שלי" - כפתורים צפים בפינה הימנית-עליונה של המפה."""
        box = tk.Frame(holder, bg=T.CARD_BG, highlightthickness=1, highlightbackground=T.BORDER)
        box.place(in_=self.map, relx=1.0, x=-12, y=12, anchor="ne")
        for text, cmd in (("＋", lambda: self.map.set_zoom(self.map.zoom + 1)),
                          ("－", lambda: self.map.set_zoom(self.map.zoom - 1)),
                          ("◎", self._recenter)):
            T.PillButton(box, text=text, command=cmd, fill=T.CARD_BG, fg=T.LINK, size=13, padx=10,
                         pady=3).pack(pady=1, padx=2)

    def _recenter(self):
        if self.origin and self.map:
            self.map.set_position(self.origin["lat"], self.origin["lon"])
            self.map.set_zoom(max(15, round(self.map.zoom)))
        else:
            self.refresh_location()

    def _card(self, p, title, subtitle=None):
        c, b = T.card(p)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, title, subtitle)
        return b

    def _build_panel(self, p):
        T.label(p, "מפה", 22, T.TEXT, bold=True, anchor="e").pack(fill="x", pady=(0, 8))
        b = self._card(p, "📍 המיקום שלי")
        self.loc_label = T.label(b, "מאתר מיקום...", 11, T.TEXT_SECONDARY, anchor="e", wraplength=340)
        self.loc_label.pack(fill="x")
        T.big_button(b, "🔄 רענן מיקום", self.refresh_location, size=10, pady=6).pack(anchor="e", pady=(8, 0))
        T.label(b, "אין GPS? לחיצה על המפה קובעת נקודת מוצא ידנית (מסומנת \"נקבע ידנית\").", 10, T.TEXT_MUTED,
                anchor="e", wraplength=340).pack(fill="x", pady=(6, 0))

        # מקום בטוח קרוב - כרטיס גילוי
        b = self._card(p, "🏥 מקום בטוח קרוב", "משטרה, בית חולים, כבאות, בית מרקחת ועוד (OpenStreetMap)")
        T.primary_button(b, "🔎 חפש מקום בטוח", self.find_safe_place, size=11, pady=7).pack(fill="x", pady=(4, 0))
        self.place_box = tk.Frame(b, bg=T.CARD_BG)
        self.place_box.pack(fill="x")
        self.place_label = T.label(b, "", 11, T.TEXT_SECONDARY, anchor="e", wraplength=340)
        self.place_label.pack(fill="x", pady=(6, 0))

        # תכנון מסלול
        b = self._card(p, "🧭 תכנון מסלול")
        self.route_mode = tk.StringVar(master=self, value="fastest")
        self.mode_control = T.Segmented(b, [(v, MODE_SHORT[v]) for v, _ in ROUTE_MODES], self.route_mode,
                                        command=self._on_mode, size=10)
        self.mode_control.pack(fill="x", pady=(4, 6))
        self.mode_desc = T.label(b, MODE_DESC["fastest"], 10, T.TEXT_SECONDARY, anchor="e", wraplength=340)
        self.mode_desc.pack(fill="x")
        T.label(b, "החלופות מדורגות לפי נתוני LifeGuard בלבד (תאורה רשומה, מוקדי תאונות, דיווחים).",
                9, T.TEXT_MUTED, anchor="e", wraplength=340).pack(fill="x", pady=(2, 0))
        self.dest_label = T.label(b, "יעד: טרם נבחר (חפש/י מקום בטוח, או נווט/י לאירוע מהקהילה)", 10,
                                  T.TEXT_MUTED, anchor="e", wraplength=340)
        self.dest_label.pack(fill="x", pady=(8, 0))
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=(8, 0))
        T.primary_button(row, "חשב מסלול", self.compute_route, size=11, pady=7).pack(side="right")
        T.big_button(row, "פתח ב-Google Maps", self.open_external, size=10, pady=7).pack(side="right", padx=6)
        self.route_label = T.label(b, "", 11, T.TEXT, anchor="e", wraplength=340)
        self.route_label.pack(fill="x", pady=(8, 0))
        self.compare_box = tk.Frame(b, bg=T.CARD_BG)
        self.compare_box.pack(fill="x")
        # בטיחות המסלול (שרת: תאורה רשומה בלילה, מוקדי תאונות, דיווחים) - עם מהימנות הנתונים
        self.safety_label = T.label(b, "", 10, T.TEXT_SECONDARY, anchor="e", wraplength=340)
        self.safety_label.pack(fill="x", pady=(6, 0))
        T.autowrap(self.safety_label)
        legend = tk.Frame(b, bg=T.CARD_BG)
        legend.pack(fill="x", pady=(8, 0))
        for col, text, w in ((T.BLUE, "המסלול שנבחר", 5), (T.ROUTE_ALT, "חלופות", 3)):
            sw = tk.Canvas(legend, width=26, height=10, bg=T.CARD_BG, highlightthickness=0)
            sw.create_line(T.px(2), T.px(5), T.px(24), T.px(5), fill=col, width=T.px(w), capstyle="round")
            sw.pack(side="right", padx=(8, 4))
            T.label(legend, text, 10, T.TEXT_SECONDARY).pack(side="right")
        # דיווחים מהתקשורת: כבוי כברירת מחדל (אות חלש ולא מאומת, כיסוי מוטה) - המשתמש/ת מפעיל/ה בעצמו/ה
        get = getattr(self.app, "get_setting", lambda k, d=None: d)
        self.news_var = tk.BooleanVar(master=self, value=get("show_news", "0") == "1")
        T.toggle_row(b, "📰 לכלול דיווחים מהתקשורת (לא מאומתים)", self.news_var, self._toggle_news, size=10)

        # רדיוס
        b = self._card(p, "⭕ רדיוס בטיחות", "משמש להצגה במפה ולחיפוש מתנדבים זמינים בקהילה")
        self.radius_var = tk.DoubleVar(master=self, value=self.radius_km)
        tk.Scale(b, from_=0.5, to=10, resolution=0.5, orient="horizontal", variable=self.radius_var,
                 bg=T.CARD_BG, fg=T.TEXT, troughcolor=T.FILL, highlightthickness=0, font=T.F(10), bd=0,
                 sliderrelief="flat", activebackground=T.BLUE, command=lambda v: self._on_radius(float(v)),
                 label="ק\"מ").pack(fill="x")

        # נתוני רחוב ודיווחים
        b = self._card(p, "🚦 נתוני רחוב ודיווחים")
        self.risk_label = T.label(b, street_intel.NO_DATA_TEXT, 11, T.TEXT_SECONDARY, anchor="e", wraplength=340)
        self.risk_label.pack(fill="x")
        T.label(b, "גורמי סביבה מוצגים על המפה בשכבות (כחול: מעט ← הרבה). אין ציון סכנה לשכונה ואין נתוני פשיעה "
                   "ברמת רחוב.", 10, T.TEXT_MUTED, anchor="e", wraplength=340).pack(fill="x", pady=(6, 0))
        rep = tk.Frame(b, bg=T.CARD_BG)
        rep.pack(fill="x", pady=(10, 0))
        T.label(rep, "📍 דווח/י על נקודת המוצא:", 10, T.TEXT_SECONDARY).pack(side="right")
        from server_kinds import REPORT_CHOICES
        self.report_kind = tk.StringVar(master=self, value=REPORT_CHOICES[0][1])
        om = tk.OptionMenu(rep, self.report_kind, *[label for _, label in REPORT_CHOICES])
        om.configure(font=T.F(10), bg=T.CARD_BG_ALT, fg=T.TEXT, activebackground=T.FILL, relief="flat", bd=0,
                     highlightthickness=0, padx=8, pady=4)
        om.pack(side="right", padx=6)
        T.big_button(b, "שלח דיווח", self.send_street_report, size=10, pady=6).pack(anchor="e", pady=(6, 0))
        self.report_label = T.label(b, "", 10, T.TEXT_MUTED, anchor="e", wraplength=340)
        self.report_label.pack(fill="x")
        T.label(b, "עזרו למפה ללמוד: צלמו מסלול הליכה (וידאו + GPS). העיבוד במחשב שלכם, תמונות לא נשמרות.",
                10, T.TEXT_MUTED, anchor="e", wraplength=340).pack(fill="x", pady=(10, 4))
        T.big_button(b, "📹 צילום מסלול הליכה", self.open_route_walk, size=10, pady=6).pack(anchor="e")

    def _on_mode(self, mode):
        self.mode_desc.configure(text=MODE_DESC.get(mode, ""))
        # יש כבר תוצאות לחלופות? מחליפים את המסלול המודגש מיד, בלי חישוב חוזר
        if self._last_modes and (self._last_modes.get("res") or {}).get("modes", {}).get(mode):
            self._last_mode = mode
            self._apply_modes(self._last_modes, mode)

    # ---------- מיקום ----------
    def on_show(self):
        if self.origin is None:
            self.refresh_location()
        if self.env is not None:
            self.env.schedule(300)

    def refresh_location(self):
        self.loc_label.configure(text="מאתר מיקום...", fg=T.TEXT_SECONDARY)
        run_bg(self.app, location_module.get_location, self._apply_location)

    def _apply_location(self, loc):
        desc = location_module.describe_location(loc)
        color = {"verified": T.GREEN, "estimated": T.YELLOW}.get(desc["confidence"], T.RED)
        self.loc_label.configure(text=desc["label"], fg=color)
        if not loc.get("available"):
            return
        wifi = loc.get("source") == "windows_location" and (loc.get("accuracy_m_estimate") or 10 ** 6) <= 800
        # Wi-Fi של Windows (±50-300 מ') מספיק למסלול הליכה למקום בטוח - לא כמו IP (עשרות ק"מ)
        precise = bool(loc.get("is_precise")) or wifi
        self.origin = {"lat": loc["lat"], "lon": loc["lon"], "precise": precise,
                       "label": "GPS" if loc.get("is_precise") else
                       (f"Wi-Fi ±{loc.get('accuracy_m_estimate')} מ'" if wifi else "משוער (IP)")}
        self._draw_origin(zoom=15 if precise else 12)
        run_bg(self.app, lambda: street_intel.overlay_for(self.app.client, loc["lat"], loc["lon"], self.radius_km),
               lambda r: self.risk_label.configure(text=r["message"]))

    def _on_map_click(self, coords):
        self.origin = {"lat": coords[0], "lon": coords[1], "precise": True, "label": "נקבע ידנית"}
        self.loc_label.configure(text=f"נקודת מוצא נקבעה ידנית: {coords[0]:.5f}, {coords[1]:.5f}", fg=T.LINK)
        self._draw_origin(zoom=None)

    def _draw_origin(self, zoom=15):
        if not self.map or not self.origin:
            return
        lat, lon = self.origin["lat"], self.origin["lon"]
        self._set_marker("me", lat, lon, f"אני ({self.origin['label']})")
        if zoom:
            self.map.set_position(lat, lon)
            self.map.set_zoom(zoom)
        self._draw_radius()

    def _draw_radius(self):
        if not self.map or not self.origin:
            return
        if self._radius_poly is not None:
            self._radius_poly.delete()
        import math
        lat, lon = self.origin["lat"], self.origin["lon"]
        pts = []
        for i in range(0, 361, 10):
            a = math.radians(i)
            dlat = (self.radius_km / 111.0) * math.cos(a)
            dlon = (self.radius_km / (111.0 * max(0.1, math.cos(math.radians(lat))))) * math.sin(a)
            pts.append((lat + dlat, lon + dlon))
        self._radius_poly = self.map.set_polygon(pts, outline_color=T.BLUE, border_width=T.px(2))

    def _on_radius(self, value):
        self.radius_km = value
        self.app.set_setting("safety_radius_km", value)
        self._draw_radius()

    def _set_marker(self, key, lat, lon, text):
        if not self.map:
            return
        old = self._markers.get(key)
        if old is not None:
            old.delete()
        if key == "me":
            self._markers[key] = map_hidpi.me_marker(self.map, lat, lon, text)
        else:
            self._markers[key] = map_hidpi.place_marker(self.map, lat, lon, text)

    # ---------- מקום בטוח + מסלול ----------
    def open_route_walk(self):
        from route_walk_ui import RouteWalkWindow
        w = getattr(self, "_walk_win", None)
        if w is not None and w.winfo_exists():
            w.lift()
            return
        self._walk_win = RouteWalkWindow(self, self.app)

    def find_safe_place(self):
        if not self.origin:
            self.place_label.configure(text="אין מיקום. רענן/י מיקום או לחץ/י על המפה.", fg=T.RED)
            return
        self.place_label.configure(text="מחפש...", fg=T.TEXT_SECONDARY)
        o = self.origin
        import worship_pref
        worship = worship_pref.get(self.app.username)
        run_bg(self.app, lambda: map_module.find_nearest_safe_place(o["lat"], o["lon"], worship=worship),
               self._apply_place,
               lambda e: self.place_label.configure(text=f"החיפוש נכשל: {e}", fg=T.RED))

    def _apply_place(self, place):
        for w in self.place_box.winfo_children():
            w.destroy()
        if not place:
            self.place_label.configure(text="לא נמצא מקום בטוח מוכר ברדיוס החיפוש (או שהשירות לא זמין).", fg=T.YELLOW)
            return
        warn = "" if self.origin["precise"] else "\n⚠️ המרחק מחושב ממיקום משוער"
        # כרטיס מקום (place card): אייקון, שם, סוג ומרחק - ואז "נווט"
        row = tk.Frame(self.place_box, bg=T.CARD_BG_ALT)
        row.pack(fill="x", pady=(10, 0))
        T.icon_tile(row, "🏥", T.LIGHT_BLUE, T.LINK, size=40).pack(side="right", padx=10, pady=10)
        txt = tk.Frame(row, bg=T.CARD_BG_ALT)
        txt.pack(side="right", fill="x", expand=True, pady=8)
        T.label(txt, place["name"], 12, T.TEXT, bold=True, anchor="e", wraplength=230).pack(fill="x")
        T.label(txt, f"{place['type_label']} · {place['distance_m']:.0f} מ' אווירי", 10, T.TEXT_SECONDARY,
                anchor="e").pack(fill="x")
        if place.get("fallback_note"):
            T.label(txt, place["fallback_note"], 9, T.YELLOW, anchor="e", wraplength=230).pack(fill="x")
        T.primary_button(row, "נווט", self.compute_route, size=10, padx=12, pady=5).pack(side="left", padx=10)
        self.place_label.configure(text=warn.strip(), fg=T.YELLOW)
        self.set_destination(place["lat"], place["lon"], place["name"])

    def set_destination(self, lat, lon, name):
        self.destination = {"lat": lat, "lon": lon, "name": name}
        self.dest_label.configure(text=f"יעד: {name}", fg=T.TEXT)
        self._set_marker("dest", lat, lon, name)
        if self.map and self.origin:
            self.map.fit_bounding_box((max(lat, self.origin["lat"]) + 0.002, min(lon, self.origin["lon"]) - 0.002),
                                      (min(lat, self.origin["lat"]) - 0.002, max(lon, self.origin["lon"]) + 0.002))

    def compute_route(self):
        if not (self.origin and self.destination):
            self.route_label.configure(text="נדרשים נקודת מוצא ויעד.", fg=T.RED)
            return
        if not self.origin["precise"]:
            self.route_label.configure(text="המיקום משוער (IP) - לחץ/י על המפה כדי לקבוע נקודת מוצא מדויקת.",
                                       fg=T.YELLOW)
            return
        mode = self.route_mode.get()
        self._last_mode = mode
        if mode != "fastest":
            self._compute_modes(mode)
            return
        o, d = self.origin, self.destination
        self._clear_compare()
        self.route_label.configure(text="מחשב מסלול הליכה...", fg=T.TEXT_SECONDARY)
        run_bg(self.app, lambda: map_module.get_walking_route(o["lat"], o["lon"], d["lat"], d["lon"]),
               self._apply_route, lambda e: self.route_label.configure(text=f"החישוב נכשל: {e}", fg=T.RED))

    # ---------- מצבי מסלול: בטוח יותר / מאוזן (חלופות OSRM + ניקוד LifeGuard בשרת) ----------
    def _compute_modes(self, mode):
        o, d, c = self.origin, self.destination, self.app.client
        news = bool(getattr(self, "news_var", None) and self.news_var.get())
        self.route_label.configure(text="מחשב חלופות הליכה ובודק אותן...", fg=T.TEXT_SECONDARY)
        self.safety_label.configure(text="")
        run_bg(self.app, lambda: fetch_route_modes(c, o, d, news),
               lambda r: self._apply_modes(r, mode),
               lambda e: self.route_label.configure(text=f"החישוב נכשל: {e}", fg=T.RED))

    def _apply_modes(self, r, mode):
        if not self.route_label.winfo_exists():
            return
        self._last_modes = r
        alts = r.get("alts")
        if not alts:
            self.route_label.configure(text="לא ניתן לחשב חלופות הליכה כרגע (שירות הניתוב לא זמין?).", fg=T.RED)
            return
        res = r.get("res")
        if not res or not (res.get("modes") or {}).get(mode):
            # בלי שרת אין ניקוד סיכון - מציגים את המהיר בלבד ואומרים זאת, לא מנחשים "בטוח"
            i = min(range(len(alts)), key=lambda k: alts[k]["duration_s"])
            self._draw_routes(alts, i)
            a = alts[i]
            self.route_label.configure(text=f"🚶 {a['distance_m']:.0f} מ' · כ-{a['duration_s'] / 60:.0f} דק' הליכה "
                                            "(מוצג המהיר - אין דירוג בטיחות)", fg=T.YELLOW)
            self.safety_label.configure(text=r.get("error") or "בדיקת הבטיחות לא זמינה", fg=T.TEXT_MUTED)
            self._clear_compare()
            return
        idx = res["modes"][mode]["index"]
        self._draw_routes(alts, idx)
        self._last_route_pts = alts[idx]["points"]
        self.route_label.configure(text=mode_summary_line(res, mode), fg=T.TEXT)
        self._show_compare(res, mode, r)
        self.safety_label.configure(text=route_safety_text(res, index=idx) + "\n" + map_module.ROUTING_ATTRIBUTION,
                                    fg={"high": T.RED, "medium": T.YELLOW}.get(res["modes"][mode].get("level"),
                                                                               T.TEXT_SECONDARY))

    def _clear_compare(self):
        for w in self.compare_box.winfo_children():
            w.destroy()

    def _show_compare(self, res, chosen, r):
        """כרטיס השוואה: שורה לכל מצב (זמן, אורך, רמת סיכון לפי הנתונים); לחיצה בוחרת. הנבחר מודגש בכחול."""
        self._clear_compare()
        modes = res.get("modes") or {}
        for key, _label in ROUTE_MODES:
            m = modes.get(key)
            if not m:
                continue
            on = key == chosen
            bg = T.LIGHT_BLUE if on else T.CARD_BG_ALT
            row = tk.Frame(self.compare_box, bg=bg, cursor="hand2")
            row.pack(fill="x", pady=(6, 0))
            name = T.label(row, MODE_SHORT[key], 11, T.LINK if on else T.TEXT, bg, bold=True)
            name.pack(side="right", padx=10, pady=7)
            extra = f" · +{m['extra_minutes']} דק'" if m.get("extra_minutes") else ""
            stats = T.label(row, f"{m['minutes']} דק' · {m['length_m'] / 1000:.1f} ק\"מ{extra}", 10, T.TEXT, bg)
            stats.pack(side="left", padx=10)
            lvl = T.label(row, f"סיכון {_LEVEL_HE.get(m.get('level'), m.get('level'))} לפי הנתונים", 9,
                          T.TEXT_SECONDARY, bg)
            lvl.pack(side="right", padx=(0, 4))
            for w in (row, name, stats, lvl):
                w.bind("<Button-1>", lambda e, k=key: self.route_mode.set(k) or self._on_mode(k))

    def _clear_paths(self):
        for p in [self._path] + list(self._alt_paths):
            if p is not None:
                try:
                    p.delete()
                except Exception:  # noqa: BLE001
                    pass
        self._path, self._alt_paths = None, []

    def _draw_routes(self, alts, chosen):
        if not self.map:
            return
        self._clear_paths()
        for i, a in enumerate(alts):
            if i != chosen:
                self._alt_paths.append(self.map.set_path(a["points"], color=T.ROUTE_ALT, width=T.px(4)))
        self._draw_chosen(alts[chosen]["points"])

    def _draw_chosen(self, points):
        """המסלול הנבחר: "מעטפת" לבנה מתחת לקו הכחול - בולט על כל רקע מפה (כמו ב-Apple Maps)."""
        self._alt_paths.append(self.map.set_path(points, color=T.ROUTE_CASING, width=T.px(9)))
        self._path = self.map.set_path(points, color=T.BLUE, width=T.px(5))

    def _apply_route(self, route):
        if not route:
            self.route_label.configure(text="לא ניתן לחשב מסלול כרגע (השירות לא זמין?).", fg=T.RED)
            return
        if self.map:
            self._clear_paths()
            self._draw_chosen(route["points"])
        self.route_label.configure(text=f"🚶 {route['distance_m']:.0f} מ' · כ-{route['duration_s'] / 60:.0f} דק' הליכה",
                                   fg=T.TEXT)
        self.check_route_safety(route["points"])

    def check_route_safety(self, points):
        c = self.app.client
        if c is None or not c.token:
            self.safety_label.configure(text="בדיקת בטיחות המסלול דורשת חיבור לשרת", fg=T.TEXT_MUTED)
            return
        pts = [[round(p[0], 6), round(p[1], 6)] for p in points][:4000]
        self.safety_label.configure(text="בודק בטיחות מסלול...", fg=T.TEXT_SECONDARY)
        news = bool(getattr(self, "news_var", None) and self.news_var.get())
        self._last_route_pts = points
        run_bg(self.app, lambda: c.route_safety(routes=[pts], include_news=news), self._apply_safety,
               lambda e: self.safety_label.configure(text=f"בדיקת הבטיחות נכשלה: {getattr(e, 'message', e)}",
                                                     fg=T.TEXT_MUTED))

    def _toggle_news(self):
        self.app.set_setting("show_news", "1" if self.news_var.get() else "0")
        if self._last_mode != "fastest" and getattr(self, "_last_route_pts", None) and self.origin and self.destination:
            self._compute_modes(self._last_mode)
        elif getattr(self, "_last_route_pts", None):
            self.check_route_safety(self._last_route_pts)

    def _apply_safety(self, res):
        if not self.safety_label.winfo_exists():
            return
        self.safety_label.configure(text=route_safety_text(res), fg={"high": T.RED, "medium": T.YELLOW}.get(
            (res.get("routes") or [{}])[0].get("level"), T.TEXT_SECONDARY))

    def send_street_report(self):
        c = self.app.client
        if c is None or not c.token:
            self.report_label.configure(text="דיווח דורש חיבור לשרת", fg=T.RED)
            return
        if not self.origin or not self.origin.get("precise"):
            self.report_label.configure(text="צריך נקודה מדויקת: לחצ/י על המפה במקום הבעיה", fg=T.YELLOW)
            return
        from server_kinds import REPORT_CHOICES
        kind = next(k for k, label in REPORT_CHOICES if label == self.report_kind.get())
        o = self.origin
        run_bg(self.app, lambda: c.street_report(kind, o["lat"], o["lon"]),
               lambda r: self.report_label.configure(
                   text="✅ הדיווח נשמר (\"לא מאומת\" עד שמשתמשים נוספים מדווחים או מנהל/ת מאשר/ת)", fg=T.GREEN),
               lambda e: self.report_label.configure(text=f"❌ {getattr(e, 'message', e)}", fg=T.RED))

    def open_external(self):
        if not self.destination:
            self.route_label.configure(text="בחר/י יעד תחילה.", fg=T.RED)
            return
        d = self.destination
        url = f"https://www.google.com/maps/dir/?api=1&destination={d['lat']:.6f},{d['lon']:.6f}&travelmode=walking"
        if self.origin:
            url += f"&origin={self.origin['lat']:.6f},{self.origin['lon']:.6f}"
        webbrowser.open(url)


ROUTE_MODES = (("fastest", "⚡ המהיר ביותר (הליכה)"),
               ("safest", "🛡️ בטוח יותר לפי הנתונים (עד פי 1.5 זמן)"),
               ("balanced", "⚖️ מאוזן (זמן + סיכון)"))
MODE_HE = {"fastest": "המהיר", "safest": "בטוח יותר לפי הנתונים", "balanced": "מאוזן"}


def fetch_route_modes(client, origin, dest, include_news=False):
    """רץ ב-thread רקע: חלופות OSRM (FOSSGIS) + ניקוד ובחירת מצבים בשרת. -> {alts, res?, error?}."""
    alts = map_module.get_walking_alternatives(origin["lat"], origin["lon"], dest["lat"], dest["lon"])
    if not alts:
        return {"alts": None}
    if client is None or not getattr(client, "token", None):
        return {"alts": alts, "error": "דירוג בטיחות החלופות דורש חיבור לשרת LifeGuard"}
    try:
        return {"alts": alts, "res": client.route_options(alts, include_news=include_news)}
    except Exception as e:  # noqa: BLE001 - מוצג למשתמש
        return {"alts": alts, "error": f"בדיקת הבטיחות נכשלה: {getattr(e, 'message', e)}"}


def mode_summary_line(res, mode):
    """שורת השוואה אחת: זמן, רמת סיכון, ולמה (מול המהיר). לעולם לא "בטוח" לבד."""
    m = (res.get("modes") or {}).get(mode)
    if not m:
        return "אין תוצאה"
    he = {"high": "גבוה", "medium": "בינוני", "low": "נמוך"}
    head = (f"🚶 {MODE_HE.get(mode, mode)}: {m['length_m']:.0f} מ' · כ-{m['minutes']} דק' · "
            f"סיכון {he.get(m['level'], m['level'])} לפי הנתונים")
    return head + ("" if mode == "fastest" and not res.get("single_route") else f"\n{m['text']}")


def route_safety_text(res, index=0):
    """תוצאת /route-safety -> טקסט קצר: רמה, רכיבים, מהימנות, והמקטעים הבעייתיים (של המסלול index)."""
    routes = res.get("routes") or []
    r = next((x for x in routes if x.get("index") == index), None) or (routes[0] if routes else None)
    if not r:
        return "אין תוצאה"
    he = {"high": "גבוה", "medium": "בינוני", "low": "נמוך"}
    comp = r.get("components") or {}
    names = {"lighting": "חושך", "traffic": "תנועה", "reports": "דיווחים", "nightlife": "בילוי לילי",
             "transit": "תחנה", "news": "תקשורת (לא מאומת)"}
    parts = [f"{names[k]}: {he[v['level']]}" for k, v in comp.items()
             if k in names and (k in ("lighting", "traffic", "reports") or v["per_km"] > 0)]
    rel = {"high": "גבוהה", "partial": "חלקית", "low": "נמוכה (אין נתוני תאורה לרוב המסלול)"}
    lines = [f"🛡️ סיכון במסלול: {he.get(r['level'], r['level'])}"
             + (" (לילה)" if res.get("phase") == "night" else "") + " · " + " · ".join(parts),
             f"מהימנות הנתונים: {rel.get(r['data']['reliability'], r['data']['reliability'])} · פשיעה: לא זמינה ברמת רחוב"]
    for sg in (r.get("segments") or [])[:2]:
        lines.append(f"• אחרי {sg['from_m']} מ': " + "; ".join(sg["reasons"]))
    news = res.get("news")
    if news:
        for c in news.get("city_context") or []:
            cnt = "דיווח אחד" if c["count"] == 1 else f"{c['count']} דיווחים"
            lines.append(f"📰 {cnt} בתקשורת ב{c['city']} בשבוע האחרון ({', '.join(c['categories'])})"
                         f" - ברמת עיר, {news['label']}")
        lines.append("📰 " + news["bias_note"])
    for cv in res.get("caveats") or []:
        lines.append("⚠️ " + cv)
    wx = res.get("weather") or {}
    if wx.get("status") == "ok":
        lines.append(f"🌦️ מזג אוויר - {wx.get('label', 'הקשר בלבד')}: {wx.get('text', '')}")
        lines.append(wx.get("attribution") or "Weather data by Open-Meteo.com")
    if res.get("routing_attribution"):
        lines.append(res["routing_attribution"])
    return "\n".join(lines)


# =====================================================================================
class CommunityScreen(tk.Frame):
    """קהילה: התראות נכנסות, מתנדבים זמינים, משתמשים בקרבת מקום, משפחה וחברים מהימנים."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self._last_summary = None
        outer, body = T.scrollable(self)
        outer.pack(fill="both", expand=True)
        cols = tk.Frame(body, bg=T.BG)
        T.label(body, "קהילה", 24, T.TEXT, bold=True, anchor="e").pack(fill="x", padx=26, pady=(18, 4))
        cols.pack(fill="both", expand=True, padx=20, pady=(6, 16))
        right = tk.Frame(cols, bg=T.BG)
        left = tk.Frame(cols, bg=T.BG)
        right.pack(side="right", fill="both", expand=True, padx=(8, 0))
        left.pack(side="right", fill="both", expand=True, padx=(0, 8))

        top = tk.Frame(body, bg=T.BG)
        c, self.inbox_box = T.card(right)
        c.pack(fill="x", pady=(0, 12))
        section_header(self.inbox_box, "🚨 התראות חירום פעילות אליי")
        self.inbox_list = tk.Frame(self.inbox_box, bg=T.CARD_BG)
        self.inbox_list.pack(fill="x")

        c, b = T.card(right)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "👨‍👩‍👧 משפחה וחברים מהימנים",
                       "מקבלים SOS ומיקום מדויק מיד. כל קשר דורש אישור של הצד השני באפליקציה שלו.")
        T.primary_button(b, "ניהול מעגל הביטחון: הזמנה לפי שם, טלפון או קוד", lambda: app.show_screen("circle"),
                         size=11, pady=5).pack(anchor="e", pady=(4, 6))
        self.invite_status = T.label(b, "", 10, T.GREEN, anchor="e")
        self.invite_status.pack(fill="x")
        self.trusted_list = tk.Frame(b, bg=T.CARD_BG)
        self.trusted_list.pack(fill="x", pady=(6, 0))

        c, b = T.card(left)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "🙋 אני מתנדב/ת", "כשזמין/ה - תקבל/י קריאות חירום מאנשים בטווח שבחרת")
        self.vol_var = tk.BooleanVar(master=self, value=False)
        self.first_aid_var = tk.BooleanVar(master=self, value=False)
        self.vol_radius = tk.StringVar(master=self, value="3")
        T.toggle_row(b, "זמין/ה עכשיו לקריאות", self.vol_var, self.save_volunteer, size=12)
        T.toggle_row(b, "עברתי הכשרת עזרה ראשונה", self.first_aid_var, self.save_volunteer, size=11)
        r = tk.Frame(b, bg=T.CARD_BG)
        r.pack(fill="x", pady=(4, 0))
        T.label(r, "טווח קריאות (ק\"מ):", 11, T.TEXT).pack(side="right")
        tk.Spinbox(r, from_=0.5, to=20, increment=0.5, textvariable=self.vol_radius, width=5, font=T.F(11),
                   justify="center", command=self.save_volunteer).pack(side="right", padx=8, ipady=3)
        self.vol_status = T.label(b, "", 10, T.TEXT_MUTED, anchor="e", wraplength=440)
        self.vol_status.pack(fill="x", pady=(4, 0))
        T.label(b, "פרטיות: אחרים רואים רק את שמך ומרחק מעוגל. מיקום מדויק של נפגע נחשף לך רק אחרי שקיבלת "
                   "את האירוע, ונחסם כשהאירוע מסתיים.", 10, T.TEXT_MUTED, anchor="e", wraplength=440).pack(fill="x")
        import features_ui
        self.trust = features_ui.VolunteerTrustSection(b, app)

        c, b = T.card(left)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "🧑‍🤝‍🧑 מתנדבים זמינים בקרבתך")
        self.volunteers_list = tk.Frame(b, bg=T.CARD_BG)
        self.volunteers_list.pack(fill="x")

        c, b = T.card(left)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "📍 משתמשי LifeGuard בקרבת מקום")
        self.nearby_label = T.label(b, "—", 12, T.TEXT, anchor="e", wraplength=440)
        self.nearby_label.pack(fill="x")
        T.label(b, "מוצג מספר בלבד - LifeGuard לא חושף זהות או מיקום של משתמשים אחרים.", 10, T.TEXT_MUTED,
                anchor="e", wraplength=440).pack(fill="x")

        bar = tk.Frame(right, bg=T.BG)
        bar.pack(fill="x")
        T.big_button(bar, "🔄 רענון", self.refresh, size=10, pady=5).pack(side="right")
        self.updated_label = T.label(bar, "", 10, T.TEXT_MUTED, bg=T.BG)
        self.updated_label.pack(side="right", padx=8)

    def on_show(self):
        self.refresh()

    def _offline(self, why):
        for frame in (self.trusted_list, self.volunteers_list, self.inbox_list):
            for w in frame.winfo_children():
                w.destroy()
        T.label(self.trusted_list, f"❗ {why}", 11, T.RED, anchor="e", wraplength=440).pack(fill="x")
        self.nearby_label.configure(text="—")

    def refresh(self):
        c = self.app.client
        if c is None or not c.token:
            self._offline("לא מחובר לשרת LifeGuard - הקהילה לא זמינה. ראה/י פרופיל ← חיבור לשרת.")
            return
        radius = self.app.get_setting("safety_radius_km", "1")

        def work():
            return {"summary": c.community_summary(max(float(radius), 2)), "inbox": c.inbox(), "me": c.me()}
        run_bg(self.app, work, self._apply, lambda e: self._offline(f"שגיאת שרת: {e}"))

    def _apply(self, data):
        if not self.winfo_exists():
            return
        s, inbox, me = data["summary"], data["inbox"], data["me"]
        self._last_summary = s
        self.updated_label.configure(text="עודכן " + time.strftime("%H:%M:%S"))
        # מתנדב
        vol = me.get("volunteer") or {}
        self.vol_var.set(bool(vol.get("available")))
        self.first_aid_var.set("first_aid" in (vol.get("skills") or []))
        if vol.get("radius_km"):
            self.vol_radius.set(str(vol["radius_km"]))
        self.vol_status.configure(text=("✅ את/ה זמין/ה לקריאות" if vol.get("available") else "לא זמין/ה כרגע")
                                  + ("" if s.get("location_known") else " · ⚠️ מיקומך לא ידוע לשרת - לא תותאם/י לקריאות"))
        self.trust.apply(vol)
        # התראות נכנסות
        for w in self.inbox_list.winfo_children():
            w.destroy()
        if not inbox:
            T.label(self.inbox_list, "אין התראות פעילות", 11, T.TEXT_MUTED, anchor="e").pack(fill="x")
        for inc in inbox:
            row = tk.Frame(self.inbox_list, bg=T.RED_BG)
            row.pack(fill="x", pady=3)
            dist = f" · {inc['distance_km']:.1f} ק\"מ" if inc.get("distance_km") else ""
            if inc.get("my_role") == "helper":       # שיחת תמיכה / הזעקת רשויות (מרחוק)
                icon = "👮" if inc.get("helper_kind") == "authority_liaison" else "💬"
                text = f"{icon} {inc['owner_display']} · {inc.get('helper_kind_label') or ''}"
            else:
                text = f"🚨 {inc['owner_display']} · {EMERGENCY_TYPE_LABELS.get(inc['emergency_type'], '')}{dist}"
            T.label(row, text, 12, T.RED, T.RED_BG, bold=True).pack(side="right", padx=8, pady=6)
            T.big_button(row, "פתח", lambda i=inc["id"]: self.app.open_alert(i), bg=T.RED_DARK, size=10,
                         pady=4).pack(side="left", padx=6)
        # מהימנים
        for w in self.trusted_list.winfo_children():
            w.destroy()
        rels = s["trusted"]
        if not rels["my_contacts"]:
            T.label(self.trusted_list, "⚠️ אין לך אנשי קשר מחוברים - SOS יגיע רק למתנדבים זמינים בקרבתך",
                    11, T.YELLOW, anchor="e", wraplength=440).pack(fill="x")
        for r in rels["my_contacts"]:
            row = tk.Frame(self.trusted_list, bg=T.CARD_BG)
            row.pack(fill="x", pady=2)
            T.label(row, f"{r['display_name']} (@{r['username']})", 12, T.TEXT).pack(side="right")
            T.chip(row, "מחובר", "ok").pack(side="right", padx=6)
        waiting = [i for i in rels.get("sent_invitations", []) if i["status"] == "pending"]
        if waiting:
            T.label(self.trusted_list, f"📤 {len(waiting)} הזמנות ששלחת ממתינות לאישור", 10, T.TEXT_MUTED,
                    anchor="e").pack(fill="x", pady=(4, 0))
        for inv in rels.get("received_invitations", []):
            if inv.get("awaiting_confirmation"):
                continue
            row = tk.Frame(self.trusted_list, bg=T.YELLOW_BG)
            row.pack(fill="x", pady=4)
            T.label(row, f"📨 {inv['from_display']} מבקש/ת שתהיה/י איש קשר לחירום", 11, T.TEXT, T.YELLOW_BG).pack(side="right", padx=6, pady=4)
            T.big_button(row, "אשר", lambda i=inv["id"]: self.respond(i, True), bg=T.GREEN, size=9, pady=3).pack(side="left", padx=3)
            T.big_button(row, "דחה", lambda i=inv["id"]: self.respond(i, False), size=9, pady=3).pack(side="left", padx=3)
        # מתנדבים קרובים + משתמשים
        for w in self.volunteers_list.winfo_children():
            w.destroy()
        if not s.get("location_known"):
            T.label(self.volunteers_list, "מיקומך לא ידוע לשרת - לא ניתן להציג מתנדבים קרובים.", 11, T.YELLOW,
                    anchor="e").pack(fill="x")
            self.nearby_label.configure(text="—")
            return
        vols = s.get("volunteers") or []
        if not vols:
            T.label(self.volunteers_list, f"אין מתנדבים זמינים בטווח {s['radius_km']:.0f} ק\"מ כרגע", 11,
                    T.TEXT_MUTED, anchor="e").pack(fill="x")
        for v in vols:
            row = tk.Frame(self.volunteers_list, bg=T.CARD_BG)
            row.pack(fill="x", pady=2)
            T.label(row, v["display_name"], 12, T.TEXT).pack(side="right")
            T.chip(row, f"עד {v['distance_band_km']} ק\"מ" + (" (משוער)" if v.get("approximate") else ""), "ok").pack(side="right", padx=6)
            if v.get("verified"):
                T.chip(row, "מאומת/ת ✓", "ok").pack(side="right", padx=(0, 4))
            if "first_aid" in v.get("skills", []):
                T.chip(row, "עזרה ראשונה (מוצהר)" if not v.get("verified") else "עזרה ראשונה",
                       "not_verified").pack(side="right")
        n = s.get("nearby_users_count")
        if n is not None:
            text = f"{n}+ משתמשים פעילים בטווח {s['radius_km']:.0f} ק\"מ"
        elif s.get("nearby_users_few"):
            text = f"פחות מ-5 משתמשים פעילים בטווח {s['radius_km']:.0f} ק\"מ (מספר מדויק לא מוצג - פרטיות)"
        else:
            text = "—"
        self.nearby_label.configure(text=text)

    def respond(self, invitation_id, accept):
        def ok(r):
            T.flash_feedback(self.invite_status, "✅ אישרת - ממתין לאישור השולח" if r.get("status") == "awaiting_confirmation"
                             else ("✅ מחובר/ת" if accept else "ההזמנה נדחתה"))
            self.refresh()

        def fail(e):
            T.flash_feedback(self.invite_status, f"❌ {getattr(e, 'message', e)}", T.RED, 8000)
            self.refresh()
        run_bg(self.app, lambda: self.app.client.respond_invitation(invitation_id, accept), ok, fail)

    def save_volunteer(self):
        c = self.app.client
        if not c or not c.token:
            return
        try:
            radius = float(self.vol_radius.get())
        except ValueError:
            radius = 3
        skills = ["first_aid"] if self.first_aid_var.get() else []
        available = self.vol_var.get()

        def work():
            res = c.set_volunteer(available, radius, skills)
            self.app.push_presence_now()
            return res
        run_bg(self.app, work, lambda r: self.refresh(),
               lambda e: self.vol_status.configure(text=f"❌ השמירה נכשלה: {getattr(e, 'message', e)}", fg=T.RED))


# =====================================================================================
class NoiseMeter(tk.Frame):
    """מד רעש פשוט וקריא (מחליף את מד הכיול הכהה). API תואם ל-RiskGauge.set_value."""

    def __init__(self, parent):
        super().__init__(parent, bg=T.CARD_BG)
        self.cv = tk.Canvas(self, height=22, bg=T.CARD_BG_ALT, highlightthickness=0)
        self.cv.pack(fill="x")
        self.text = T.label(self, "—", 10, T.TEXT_MUTED, anchor="e")
        self.text.pack(fill="x")

    def set_value(self, score, level, caption=""):
        w = max(10, self.cv.winfo_width())
        frac = max(0.0, min(1.0, (score or 0) / 2.0))
        color = {"LOW": T.GREEN, "MEDIUM": T.YELLOW, "HIGH": T.RED}.get(level, T.TEXT_MUTED)
        self.cv.delete("all")
        self.cv.create_rectangle(w - w * frac, 0, w, T.px(22), fill=color, outline="")
        self.text.configure(text=f"{caption} · {'שקט' if level == 'LOW' else 'רעש חזק' if level == 'MEDIUM' else 'לא זמין'}")


class ProfileScreen(tk.Frame):
    """פרופיל: חשבון וחיבור לשרת, רשימת הגדרות בסגנון iOS, פרטיות, מראה ונגישות, מיקרופון, יומן."""

    def open_voice_training(self):
        from voice_training_ui import VoiceTrainingWindow
        w = getattr(self, "_voice_win", None)
        if w is not None and w.winfo_exists():
            w.lift()
            return
        self._voice_win = VoiceTrainingWindow(self, self.app)

    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        outer, body = T.scrollable(self)
        outer.pack(fill="both", expand=True)
        T.label(body, "פרופיל", 24, T.TEXT, bold=True, anchor="e").pack(fill="x", padx=26, pady=(18, 4))
        cols = tk.Frame(body, bg=T.BG)
        cols.pack(fill="both", expand=True, padx=20, pady=(6, 16))
        right = tk.Frame(cols, bg=T.BG)
        left = tk.Frame(cols, bg=T.BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        cols.grid_columnconfigure(0, weight=1, uniform="prof")
        cols.grid_columnconfigure(1, weight=1, uniform="prof")

        # --- כרטיס חשבון: אווטאר (האות הראשונה), שם, משתמש, חיבור לשרת ---
        c, b = T.card(right)
        c.pack(fill="x", pady=(0, 14))
        top = tk.Frame(b, bg=T.CARD_BG)
        top.pack(fill="x")
        initial = (app.display_name or app.username or "?").strip()[:1].upper()
        av = tk.Label(top, text=initial, font=T.F(20, "bold"), fg=T.ON_ACCENT, bg=T.CARD_BG, bd=0)
        if T.HAS_PIL:
            s = T.px(56)
            T.set_image(av, ("avatar", s, T.BLUE, T.CARD_BG), lambda: T.circle_pil(s, T.BLUE, T.CARD_BG),
                        compound="center")
        av.pack(side="right", padx=(12, 0))
        names = tk.Frame(top, bg=T.CARD_BG)
        names.pack(side="right", fill="x", expand=True)
        T.label(names, app.display_name, 18, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(names, f"@{app.username}", 11, T.TEXT_MUTED, anchor="e").pack(fill="x")
        self.server_label = T.label(b, "", 11, T.TEXT_SECONDARY, anchor="e", wraplength=440)
        self.server_label.pack(fill="x", pady=(10, 0))
        T.autowrap(self.server_label)
        self.reconnect_row = tk.Frame(b, bg=T.CARD_BG)
        self.pw_var = tk.StringVar(master=self)
        pw = tk.Entry(self.reconnect_row, textvariable=self.pw_var, show="•", font=T.F(12), justify="center",
                      width=16, highlightthickness=2, highlightcolor=T.FOCUS, relief="flat")
        pw.pack(side="right", ipady=5)
        pw.bind("<Return>", lambda e: self.reconnect())
        T.primary_button(self.reconnect_row, "התחבר לשרת", self.reconnect, size=10, pady=6).pack(side="right", padx=8)
        self.reconnect_status = T.label(b, "", 10, T.RED, anchor="e", wraplength=440)
        self.reconnect_status.pack(fill="x")

        # --- רשימת הגדרות בסגנון iOS ---
        T.section_label(right, "הבטיחות שלי")
        c, b = T.card(right, padding=6)
        c.pack(fill="x", pady=(0, 14))

        def fake_call_settings():
            import fake_call
            fake_call.open_settings(app)
        T.list_rows(b, [(f"{icon}  {text}", cmd) for icon, text, cmd in (
            ("👨‍👩‍👧", "מעגל הביטחון: אנשי קשר לחירום", lambda: app.show_screen("circle")),
            ("🩺", "מידע רפואי לחירום (מוצפן)", app._open_medical),
            ("🔐", "פרטיות: מי קיבל את המיקום שלי, הקלטות והרשאות", app._open_privacy),
            ("🎙️", "אימון קולי אישי: \"הצילו\" ומילות הקוד בקול שלי", self.open_voice_training),
            ("📞", "שיחה מזויפת: ביטוי קולי שמציג שיחה נכנסת", fake_call_settings),
            ("🛡️", "תוכנית הבטיחות שלי", lambda: app._open_safety_center("התוכנית שלי")),
            ("🕘", "היסטוריית אירועים", app._open_history),
            ("🩺", "תקינות מערכת", app._open_health),
            ("⚙️", "הגדרות: סיסמה ומילות קוד קוליות", app._open_settings),
        )])

        # --- פרטיות: מתגים ---
        T.section_label(right, "פרטיות")
        c, b = T.card(right)
        c.pack(fill="x", pady=(0, 14))

        def setter(key, var):
            return lambda: app.set_setting(key, "1" if var.get() else "0")
        self.share_med = tk.BooleanVar(master=self, value=app.get_setting("share_medical_with_responders", "0") == "1")
        T.toggle_row(b, "לשתף את המידע הרפואי מהתוכנית שלי עם מי שמגיב לאירוע", self.share_med,
                     setter("share_medical_with_responders", self.share_med),
                     sub="אנשי קשר מהימנים רואים אותו מיד; מתנדב - רק אחרי שקיבל את האירוע.", size=11)
        T.separator(b)
        self.train_media = tk.BooleanVar(master=self, value=app.get_setting("train_on_false_alarm_media", "0") == "1")
        T.toggle_row(b, "בהתראת שווא: לטשטש פנים בתמונות ולהשתמש בהן לשיפור המערכת", self.train_media,
                     setter("train_on_false_alarm_media", self.train_media),
                     sub="באירוע אמיתי התמונות נשמרות כראיה מוצפנת ולא מטושטשת (לזיהוי התוקף), ולא נכנסות לאימון.",
                     size=11)
        T.separator(b)
        self.cloud_speech = tk.BooleanVar(master=self, value=app.get_setting("cloud_speech_in_danger", "1") == "1")
        T.toggle_row(b, "שיפור זיהוי הדיבור בזמן סכנה בעזרת Google", self.cloud_speech,
                     setter("cloud_speech_in_danger", self.cloud_speech),
                     sub="ברגיל הזיהוי מקומי בלבד (חוסך חשמל, בלי רשת). בזמן סכנה בלבד קטעי דיבור נשלחים גם ל-Google "
                         "לדיוק נוסף.", size=11)
        T.separator(b)
        self.agent_var = tk.BooleanVar(master=self, value=app.get_setting("background_agent", "1") == "1")
        T.toggle_row(b, "לקבל התראות חירום גם כשהתוכנה סגורה (סוכן רקע שעולה עם Windows)", self.agent_var,
                     self._toggle_agent, size=11)
        self.agent_status = T.label(b, "", 10, T.TEXT_MUTED, anchor="e", wraplength=440)
        self.agent_status.pack(fill="x")

        # --- מראה ונגישות ---
        T.section_label(left, "מראה ונגישות")
        c, b = T.card(left)
        c.pack(fill="x", pady=(0, 14))
        T.label(b, "🎨 מראה", 12, T.TEXT, bold=True, anchor="e").pack(fill="x")
        import appearance
        self.theme_var = tk.StringVar(master=self, value=appearance.load_theme_setting(app.username))
        T.Segmented(b, [("light", "☀️ בהיר"), ("dark", "🌙 כהה"), ("system", "🖥️ לפי Windows")], self.theme_var,
                    command=self._set_theme, size=10).pack(fill="x", pady=(6, 4))
        self.theme_note = T.label(b, "ההחלפה נכנסת לתוקף בהפעלה הבאה של LifeGuard.", 10, T.TEXT_MUTED, anchor="e")
        self.theme_note.pack(fill="x")
        T.separator(b)
        self.motion_var = tk.BooleanVar(master=self, value=T.REDUCED_MOTION)
        T.toggle_row(b, "הפחתת תנועה", self.motion_var, self._set_motion,
                     sub="בלי החלקות ואנימציות קטנות. במסכי החירום אין אנימציה בכל מקרה.", size=11)
        T.separator(b)
        app.text_size_btn = T.big_button(b, "", app._cycle_text_size, size=11, pady=7)
        app.text_size_btn.pack(anchor="e")
        app._update_text_size_btn()
        T.label(b, "קיצורים: F2 מוכנות · F3 היסטוריה · F4 תקינות · Esc סוגר חלון משני", 10, T.TEXT_MUTED,
                anchor="e").pack(fill="x", pady=(8, 0))

        T.section_label(left, "מיקרופון")
        c, b = T.card(left)
        c.pack(fill="x", pady=(0, 14))
        section_header(b, "🎚️ כיול מיקרופון", "רעש סביבתי בזמן אמת - לבדיקה שהמיקרופון עובד")
        app.idle_gauge = NoiseMeter(b)
        app.idle_gauge.pack(fill="x", pady=(4, 0))
        app.live_monitor_btn = T.big_button(b, "", app._toggle_live_monitor, size=10, pady=6)
        app.live_monitor_btn.pack(anchor="e", pady=(8, 0))

        T.section_label(left, "יומן פעילות")
        c, b = T.card(left)
        c.pack(fill="x", pady=(0, 14))
        app.log_text = tk.Text(b, height=12, font=T.F(10, family="Consolas"), bg=T.CARD_BG_ALT, fg=T.TEXT,
                               bd=0, wrap="word", state="disabled", padx=10, pady=8, highlightthickness=0)
        app.log_text.tag_configure("rtl", justify="right")
        app.log_text.pack(fill="x")

        T.big_button(left, "⏻ כיבוי התוכנה (סיסמה)", app._open_account_password_dialog, bg=T.CARD_BG,
                     fg=T.RED, size=11, pady=7).pack(anchor="e", pady=(0, 12))

    def on_show(self):
        self.update_server()

    def _set_theme(self, value):
        import appearance
        appearance.save_appearance(self.app.username, theme=value)
        self.theme_note.configure(text=f"נשמר: {T.THEME_LABELS.get(value, value)}. ייכנס לתוקף בהפעלה הבאה של LifeGuard.",
                                  fg=T.GREEN)

    def _set_motion(self):
        val = "1" if self.motion_var.get() else "0"
        import appearance
        appearance.save_appearance(self.app.username, reduce_motion=val)
        T.set_reduced_motion(val)

    def _toggle_agent(self):
        import agent_bridge
        on = self.agent_var.get()
        self.app.set_setting("background_agent", "1" if on else "0")
        if on:
            self.agent_status.configure(text="יופעל בחיבור הבא לשרת (נדרשת סיסמה פעם אחת)", fg=T.TEXT_MUTED)
            return

        def work():
            agent_bridge.uninstall_autostart()
            agent_bridge.clear_session(revoke=True)  # מבטל גם את הסשן של הסוכן בשרת
        run_bg(self.app, work, lambda _: self.agent_status.configure(
            text="כבוי: התראות יוצגו רק כשהתוכנה פתוחה", fg=T.YELLOW),
            lambda e: self.agent_status.configure(text=f"❌ {e}", fg=T.RED))

    def update_server(self):
        c = self.app.client
        if c is not None and c.token:
            self.server_label.configure(text=f"📡 מחובר לשרת LifeGuard · {c.base_url}", fg=T.GREEN)
            self.reconnect_row.pack_forget()
        else:
            err = self.app.server_error or "לא מחובר"
            self.server_label.configure(text=f"❗ לא מחובר לשרת LifeGuard ({err}).\n"
                                             "בלי חיבור, התראות SOS לא יגיעו לאף אחד.", fg=T.RED)
            self.reconnect_row.pack(fill="x", pady=(8, 0))

    def reconnect(self):
        pw = self.pw_var.get()
        self.pw_var.set("")
        self.reconnect_status.configure(text="מתחבר...", fg=T.TEXT_SECONDARY)
        self.app.connect_server(pw, on_done=lambda ok, msg: (
            self.reconnect_status.configure(text=msg, fg=T.GREEN if ok else T.RED), self.update_server()))
