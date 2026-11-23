# -*- coding: utf-8 -*-
"""
features_ui.py - רכיבי ממשק ליכולות החדשות (כולם מול השרת, בלי נתונים מקומיים מומצאים):

- EscortWatchCard: ליווי הביתה של אנשי הקשר שלי - זמן שנותר, מיקום אחרון *עם גילו*, ניווט.
- ReadinessCard: בדיקת מוכנות חודשית - מי מאנשי הקשר באמת קיבל, ותזכורת כשהבדיקה ישנה.
- VolunteerTrustSection: סטטוס אימות מתנדב, בקשת אימות, דירוג ממוצע/דיווחים.
- MedicalWindow: מידע רפואי לחירום (מוצפן בשרת, משותף רק אם המשתמש בחר).
- VolunteerFeedbackWindow: דירוג/דיווח על מתנדב שקיבל את האירוע, אחרי סיומו.
- PhoneVerifyRow: אימות טלפון ב-SMS + גיבוי SMS (רק כשהשרת מדווח שספק SMS מוגדר).
"""

import io
import time
import tkinter as tk
import webbrowser

import config
import ui_theme as T
from app_screens import run_bg, section_header

MEDICAL_LABELS = (("blood_type", "סוג דם"), ("allergies", "אלרגיות"), ("medications", "תרופות קבועות"),
                  ("conditions", "מצבים רפואיים (סוכרת, אפילפסיה...)"),
                  ("emergency_medical_contact", "איש קשר רפואי (רופא/ה, טלפון)"), ("notes", "הערות נוספות למגיבים"))
QUAL_LABELS = (("first_aid", "עזרה ראשונה"), ("emt", "חובש/ת"), ("paramedic", "פרמדיק/ית"), ("doctor", "רופא/ה"),
               ("nurse", "אח/ות"), ("firefighter", "כבאי/ת"), ("security", "ביטחון / שיטור"),
               ("search_rescue", "חיפוש והצלה"), ("mental_health", "עזרה נפשית ראשונה"))
VERIFICATION_LABELS = {"unverified": ("לא מאומת/ת", "not_verified"), "pending": ("בקשת אימות בבדיקה", "pending"),
                       "verified": ("מתנדב/ת מאומת/ת ✓", "ok"), "rejected": ("האימות נדחה", "unavailable")}


def _err(e):
    return getattr(e, "message", None) or str(e)


def _ago(ts):
    if not ts:
        return "—"
    s = max(0, int(time.time() - ts))
    if s < 90:
        return f"לפני {s} שניות"
    if s < 5400:
        return f"לפני {s // 60} דקות"
    return f"לפני {s // 3600} שעות"


def _clear(frame):
    for w in frame.winfo_children():
        w.destroy()


# ===================================================================== ליווי הביתה - עוקב/ת
class EscortWatchCard:
    def __init__(self, parent, app):
        self.app = app
        self.card, b = T.card(parent)
        section_header(b, "🚶 בדרך הביתה - אנשים שאני מלווה",
                       "אנשי קשר שהפעילו ליווי. אם לא יאשרו הגעה בזמן - תקבל/י קריאת חירום אוטומטית.")
        self.list = tk.Frame(b, bg=T.CARD_BG)
        self.list.pack(fill="x")
        self._shown = False

    def refresh(self):
        c = self.app.client
        if c is None or not c.token:
            return
        run_bg(self.app, c.escorts, self._apply, lambda e: None)

    def _apply(self, data):
        if not self.list.winfo_exists():
            return
        watching = data.get("watching") or []
        _clear(self.list)
        if not watching:
            if self._shown:
                self.card.pack_forget()
                self._shown = False
            return
        if not self._shown:
            self.card.pack(fill="x", pady=(0, 12), before=self.app.screens["home"].sos_card)
            self._shown = True
        for e in watching:
            row = tk.Frame(self.list, bg=T.RED_BG if e.get("overdue") else T.CARD_BG_ALT)
            row.pack(fill="x", pady=3)
            mins = int(e["remaining_seconds"] // 60)
            when = "⏰ הזמן עבר - ממתין לאישור הגעה" if e.get("overdue") else f"עוד {mins} דק'"
            dest = f" ל{e['destination']}" if e.get("destination") else ""
            T.label(row, f"{e['owner_display']}{dest} · {when}", 12, T.TEXT, row.cget("bg"), bold=True,
                    anchor="e").pack(fill="x", padx=8, pady=(4, 0))
            for line, color in ride_lines(e):
                T.label(row, line, 10, getattr(T, color), row.cget("bg"), anchor="e").pack(fill="x", padx=8)
            loc = e.get("location")
            if loc:
                txt = f"📍 מיקום אחרון {_ago(loc.get('updated_at'))}" + ("" if loc.get("is_precise") else " (משוער)")
                T.label(row, txt, 10, T.TEXT_SECONDARY, row.cget("bg"), anchor="e").pack(fill="x", padx=8)
                url = f"https://www.google.com/maps/search/?api=1&query={loc['lat']:.6f},{loc['lon']:.6f}"
                T.big_button(row, "🧭 ניווט", lambda u=url: webbrowser.open(u), size=10, pady=3).pack(
                    side="left", padx=6, pady=4)
            else:
                T.label(row, "📍 אין עדיין מיקום מהמכשיר שלו/ה", 10, T.TEXT_MUTED, row.cget("bg"),
                        anchor="e").pack(fill="x", padx=8, pady=(0, 4))


def ride_lines(e):
    """נסיעה בטוחה (לצופים): סוג, רכב, התקדמות, וחריגה פתוחה. ליווי רגלי -> []."""
    if e.get("kind") != "ride":
        return []
    out = []
    v = e.get("vehicle") or {}
    car = " · ".join(x for x in (v.get("plate"), v.get("description"), v.get("driver") and f"נהג/ת: {v['driver']}") if x)
    out.append((f"🚕 {e.get('ride_label') or 'נסיעה'}" + (f" · {car}" if car else ""), "TEXT_SECONDARY"))
    p = e.get("progress") or {}
    if p.get("remaining_m") is not None:
        km = p["remaining_m"] / 1000
        out.append((f"נותרו כ-{km:.1f} ק\"מ" + (f" · הגעה משוערת בעוד {p['eta_minutes']} דק'" if p.get("eta_minutes")
                                                else ""), "TEXT_SECONDARY"))
    if e.get("route_source") == "straight_line":
        out.append(("(בלי מסלול כביש - מזהים רק התרחקות מהיעד)", "TEXT_MUTED"))
    a = e.get("anomaly")
    if a:
        out.append(("⚠️ " + a.get("text", "חריגה בנסיעה - בודקים"), "YELLOW"))
    return out


# ===================================================================== בדיקת מוכנות
class ReadinessCard:
    def __init__(self, parent, app):
        self.app = app
        c, b = T.card(parent)
        c.pack(fill="x", pady=(0, 12))
        top = tk.Frame(b, bg=T.CARD_BG)
        top.pack(fill="x")
        T.label(top, "🧪 בדיקת מוכנות חודשית", 12, T.TEXT, bold=True).pack(side="right")
        self.run_btn = T.big_button(top, "הרץ עכשיו", self.run, bg=T.CARD_BG_ALT, size=10, pady=4)
        self.run_btn.pack(side="left")
        T.label(b, "שולחת הודעת בדיקה (לא חירום) לכל אנשי הקשר, ומראה מי באמת קיבל.", 10, T.TEXT_MUTED,
                anchor="e", wraplength=440).pack(fill="x")
        self.status = T.label(b, "—", 11, T.TEXT_SECONDARY, anchor="e", wraplength=440)
        self.status.pack(fill="x", pady=(4, 0))
        self.results = tk.Frame(b, bg=T.CARD_BG)
        self.results.pack(fill="x")
        self._check_id = None

    def refresh(self):
        c = self.app.client
        if c is None or not c.token:
            self.status.configure(text="לא מחובר לשרת - אי אפשר לבדוק", fg=T.TEXT_MUTED)
            return
        run_bg(self.app, lambda: c.readiness(self._check_id), self._apply, lambda e: None)

    def run(self):
        c = self.app.client
        if c is None or not c.token:
            self.status.configure(text="❌ לא מחובר לשרת", fg=T.RED)
            return
        self.status.configure(text="שולח הודעות בדיקה...", fg=T.TEXT_SECONDARY)

        def done(res):
            self._check_id = res["id"]
            self._apply(res)
            # תוצאות מתעדכנות כשהאפליקציות של אנשי הקשר מאשרות קבלה
            for delay in (3000, 8000, 20000, 60000):
                self.app.root.after(delay, self.refresh)
        run_bg(self.app, c.run_readiness_check, done,
               lambda e: self.status.configure(text=f"❌ {_err(e)}", fg=T.RED))

    def _apply(self, r):
        if not self.results.winfo_exists():
            return
        _clear(self.results)
        if not r.get("created_at"):
            self.status.configure(text="⚠️ עוד לא בוצעה בדיקת מוכנות. מומלץ להריץ אחת עכשיו.", fg=T.YELLOW)
            return
        age_days = (time.time() - r["created_at"]) / 86400
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(r["created_at"]))
        got = sum(1 for x in r["results"] if x["received_at"])
        stale = age_days > config.READINESS_REMINDER_DAYS
        self.status.configure(
            text=(f"⚠️ הבדיקה האחרונה ({when}) ישנה מ-{config.READINESS_REMINDER_DAYS} יום - הרץ/י שוב · "
                  if stale else f"בדיקה אחרונה: {when} · ") + f"{got}/{len(r['results'])} אישרו קבלה",
            fg=T.YELLOW if stale or got < len(r["results"]) else T.GREEN)
        for x in r["results"]:
            ok = bool(x["received_at"])
            T.label(self.results, f"{'✅' if ok else '⏳'} {x['display_name']} - "
                                  f"{'האפליקציה שלו/ה אישרה קבלה' if ok else 'עדיין לא אישר/ה קבלה'}",
                    10, T.GREEN if ok else T.TEXT_SECONDARY, anchor="e").pack(fill="x")

    def is_stale(self, r):
        return not r.get("created_at") or time.time() - r["created_at"] > config.READINESS_REMINDER_DAYS * 86400


# ===================================================================== חירום לאומי
class NationalCard:
    """מצב חירום לאומי: הודעת מנהל/ת, התרעות (רק אם השרת מחובר למקור מורשה), הנחיות, מקלטים רשמיים קרובים."""
    REFRESH_MS = 60_000

    def __init__(self, parent, app):
        self.app = app
        c, b = T.card(parent)
        c.pack(fill="x", pady=(0, 12))
        self.body = b
        top = tk.Frame(b, bg=T.CARD_BG)
        top.pack(fill="x")
        T.label(top, "🛡️ חירום לאומי", 12, T.TEXT, bold=True).pack(side="right")
        T.label(top, "100 · 101 · 102 · פיקוד העורף 104", 10, T.TEXT_MUTED).pack(side="left")
        self.banner = T.label(b, "", 12, T.RED, anchor="e", wraplength=440, bold=True)
        self.alert = T.label(b, "", 11, T.TEXT_SECONDARY, anchor="e", wraplength=440)
        self.alert.pack(fill="x", pady=(4, 0))
        T.autowrap(self.alert)
        self.shelters = tk.Frame(b, bg=T.CARD_BG)
        self.shelters.pack(fill="x", pady=(4, 0))
        areas = tk.Frame(b, bg=T.CARD_BG)
        areas.pack(fill="x", pady=(6, 0))
        T.label(areas, "אזורי התרעה שלי:", 10, T.TEXT_SECONDARY).pack(side="right")
        self.areas_var = tk.StringVar()
        tk.Entry(areas, textvariable=self.areas_var, font=T.F(10), justify="right", width=24).pack(side="right", padx=4)
        T.big_button(areas, "שמור", self.save_areas, size=9, pady=2).pack(side="right")
        self._scheduled = False

    def refresh(self):
        c = self.app.client
        if c is None or not c.token:
            self.alert.configure(text="לא מחובר לשרת · במצב חירום פעלו לפי אפליקציית פיקוד העורף", fg=T.TEXT_MUTED)
            return
        import location_module
        loc = location_module.get_last_known() or {}
        lat, lon = (loc.get("lat"), loc.get("lon")) if loc.get("lat") is not None else (None, None)
        run_bg(self.app, lambda: c.national_status(lat, lon), self._apply, lambda e: None)
        if not self._scheduled:
            self._scheduled = True
            self.app.root.after(self.REFRESH_MS, self._tick)

    def _tick(self):
        self._scheduled = False
        if self.alert.winfo_exists():
            self.refresh()

    def save_areas(self):
        c = self.app.client
        if c is None or not c.token:
            return
        areas = [a.strip() for a in self.areas_var.get().replace("،", ",").split(",") if a.strip()]
        run_bg(self.app, lambda: c.set_national_areas(areas), lambda r: self.refresh(), lambda e: None)

    def _apply(self, st):
        if not self.alert.winfo_exists():
            return
        mode = st.get("national_mode")
        if mode and mode.get("active"):
            self.banner.configure(text=f"⚠️ {mode['title']}" + (f" - {mode['message']}" if mode.get("message") else ""))
            self.banner.pack(fill="x", before=self.alert)
        else:
            self.banner.pack_forget()
        if not self.areas_var.get() and st.get("my_areas"):
            self.areas_var.set(", ".join(st["my_areas"]))
        a = st.get("alerts") or {}
        cur = a.get("current")
        if cur and cur.get("in_my_areas"):
            self.alert.configure(text=f"🚨 {cur['title']} באזור שלך\n" + "\n".join("• " + s for s in cur["instructions"])
                                 + "\n" + a.get("official_note", ""), fg=T.RED)
        elif cur:
            self.alert.configure(text=f"התרעה פעילה ({cur['title']}) ב-{cur['areas_count']} אזורים - לא באזורים שלך"
                                      + ("" if st.get("my_areas") else " (לא הוגדרו אזורים)"), fg=T.YELLOW)
        elif a.get("status") == "ok":
            self.alert.configure(text="אין התרעות כרגע · " + a.get("official_note", ""), fg=T.GREEN)
        else:
            self.alert.configure(text=a.get("text", ""), fg=T.TEXT_MUTED)
        _clear(self.shelters)
        sh = st.get("shelters") or []
        if sh:
            T.label(self.shelters, "מקלטים ציבוריים קרובים (מאגר עירוני רשמי):", 10, T.TEXT_SECONDARY,
                    anchor="e").pack(fill="x")
        for s in sh[:3]:
            row = tk.Frame(self.shelters, bg=T.CARD_BG)
            row.pack(fill="x")
            T.label(row, f"{s['name']} · {s['distance_m']} מ'" + (f" · {s['address']}" if s.get("address") else ""),
                    10, T.TEXT, anchor="e").pack(side="right")
            url = f"https://www.google.com/maps/dir/?api=1&destination={s['lat']:.6f},{s['lon']:.6f}&travelmode=walking"
            T.big_button(row, "🧭", lambda u=url: webbrowser.open(u), size=9, pady=1).pack(side="left")
        if not sh and st.get("shelters_coverage"):
            T.label(self.shelters, st["shelters_coverage"], 9, T.TEXT_MUTED, anchor="e", wraplength=440).pack(fill="x")


# ===================================================================== בדיקת מערכת
class DiagnosticsCard:
    """שורת סיכום במסך הבית ("הכל מוכן" / "2 בעיות קריטיות") + חלון פירוט עם תיקון לכל בעיה."""
    COLORS = {"OK": "GREEN", "WARN": "YELLOW", "FAIL": "RED", "UNAVAILABLE": "TEXT_MUTED"}

    def __init__(self, parent, app):
        import diagnostics
        self.app, self.D = app, diagnostics
        c, b = T.card(parent)
        c.pack(fill="x", pady=(0, 12))
        top = tk.Frame(b, bg=T.CARD_BG)
        top.pack(fill="x")
        T.label(top, "🩺 בדיקת מערכת", 12, T.TEXT, bold=True).pack(side="right")
        T.big_button(top, "פרטים", self.open_details, bg=T.CARD_BG_ALT, size=10, pady=4).pack(side="left")
        self.status = T.label(b, "בודק...", 11, T.TEXT_SECONDARY, anchor="e", wraplength=440)
        self.status.pack(fill="x", pady=(4, 0))
        T.autowrap(self.status)
        self.results = []
        self.window = None

    def refresh(self, include_camera=False, then=None):
        def done(res):
            self.results = res
            st, text = self.D.summary(res)
            if self.status.winfo_exists():
                self.status.configure(text=f"{self.D.ICON[st]} {text}", fg=getattr(T, self.COLORS[st]))
            if then:
                then(res)
        run_bg(self.app, lambda: self.D.run_all(self.app.client, include_camera=include_camera), done,
               lambda e: self.status.configure(text=f"❌ הבדיקה נכשלה: {_err(e)}", fg=T.RED))

    def open_details(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        w = self.window = tk.Toplevel(self.app.root)
        w.title("LifeGuard | בדיקת מערכת")
        w.configure(bg=T.BG)
        w.geometry("620x640")
        bar = tk.Frame(w, bg=T.BG)
        bar.pack(fill="x", padx=14, pady=10)
        T.label(bar, "🩺 בדיקת מערכת", 15, T.TEXT, bold=True).pack(side="right")
        T.big_button(bar, "בדיקה מלאה (כולל מצלמה)", lambda: self.refresh(True, self._render),
                     bg=T.CARD_BG_ALT, size=10, pady=4).pack(side="left")
        T.big_button(bar, "רענן", lambda: self.refresh(False, self._render), bg=T.CARD_BG_ALT, size=10,
                     pady=4).pack(side="left", padx=6)
        outer, self.list_frame = T.scrollable(w, bg=T.BG)       # 11+ בדיקות - לא נכנסות במסך אחד
        outer.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        outer.bind_all_keys(w)
        self._render(self.results)
        if not self.results:
            self.refresh(False, self._render)

    def _render(self, results):
        if self.window is None or not self.window.winfo_exists():
            return
        _clear(self.list_frame)
        for r in results:
            c, b = T.card(self.list_frame)
            c.pack(fill="x", pady=4)
            T.label(b, f"{self.D.ICON[r['status']]} {r['title']}", 12, getattr(T, self.COLORS[r['status']]),
                    bold=True, anchor="e").pack(fill="x")
            d = T.label(b, r["detail"], 10, T.TEXT_SECONDARY, anchor="e", wraplength=540)
            d.pack(fill="x")
            if r.get("fix") and r["status"] != "OK":
                f = T.label(b, "🔧 " + r["fix"], 10, T.TEXT, anchor="e", wraplength=540)
                f.pack(fill="x")


# ===================================================================== אמון במתנדבים
class VolunteerTrustSection:
    def __init__(self, parent, app):
        self.app = app
        self.frame = tk.Frame(parent, bg=T.CARD_BG)
        self.frame.pack(fill="x", pady=(8, 0))
        self._build_roles()
        # אימות זהות - חובה לכל מתנדב/ת, גם מי שכבר רשום/ה (server/volunteer_identity.py)
        idr = tk.Frame(self.frame, bg=T.CARD_BG)
        idr.pack(fill="x", pady=(0, 4))
        T.label(idr, "אימות זהות:", 11, T.TEXT).pack(side="right")
        self.id_chip = T.chip(idr, "—", "not_verified")
        self.id_chip.pack(side="right", padx=6)
        self.id_btn = T.big_button(idr, "🪪 העלאת ת\"ז וסלפי", self.open_identity, size=10, pady=3)
        self.id_btn.pack(side="left")
        self.id_note = T.label(self.frame, "", 10, T.RED, anchor="e", wraplength=440)
        self.id_note.pack(fill="x")
        row = tk.Frame(self.frame, bg=T.CARD_BG)
        row.pack(fill="x")
        T.label(row, "אימות ארגון/הכשרה:", 11, T.TEXT).pack(side="right")
        self.chip = T.chip(row, "—", "not_verified")
        self.chip.pack(side="right", padx=6)
        self.stats = T.label(self.frame, "", 10, T.TEXT_MUTED, anchor="e", wraplength=440)
        self.stats.pack(fill="x")
        self.form = tk.Frame(self.frame, bg=T.CARD_BG)
        self.org_var, self.ref_var = tk.StringVar(), tk.StringVar()
        for label, var in (("ארגון (מד\"א, איחוד הצלה...) או \"אימות זהות\":", self.org_var),
                           ("מספר תעודה/חבר:", self.ref_var)):
            r = tk.Frame(self.form, bg=T.CARD_BG)
            r.pack(fill="x", pady=1)
            T.label(r, label, 10, T.TEXT_SECONDARY).pack(side="right")
            tk.Entry(r, textvariable=var, font=T.F(11), justify="right", width=18, bg=T.CARD_BG_ALT, relief="flat",
                     highlightthickness=2, highlightcolor=T.FOCUS).pack(side="left", fill="x", expand=True)
        T.big_button(self.form, "בקש/י אימות", self.request, size=10, pady=4).pack(anchor="e", pady=(4, 0))
        self.msg = T.label(self.frame, "", 10, T.GREEN, anchor="e", wraplength=440)
        self.msg.pack(fill="x")
        T.label(self.frame, "בלי אימות זהות (ת\"ז + סלפי, בדיקה ידנית) לא יישלחו אליך קריאות. אימות ארגון "
                            "(מד\"א, איחוד הצלה...) הוא תוספת: מתנדבים מאומתים בארגון מקבלים עדיפות באותו טווח "
                            "מרחק, ובלעדיו - עד 3 קריאות בחודש.",
                10, T.TEXT_MUTED, anchor="e", wraplength=440).pack(fill="x")
        # הכשרות (חובש/ת, כבאי/ת...) - מאומתות ידנית; הכשרה מאומתת שמתאימה לסוג החירום = עדיפות באותה רצועת זמן
        q = tk.Frame(self.frame, bg=T.CARD_BG)
        q.pack(fill="x", pady=(8, 0))
        T.label(q, "הכשרה:", 10, T.TEXT_SECONDARY).pack(side="right")
        self.qual_var = tk.StringVar(value=QUAL_LABELS[0][1])
        tk.OptionMenu(q, self.qual_var, *[label for _, label in QUAL_LABELS]).pack(side="right", padx=4)
        T.big_button(q, "בקש/י אימות הכשרה", self.request_qual, size=9, pady=3).pack(side="right")
        self.quals = T.label(self.frame, "", 10, T.TEXT_SECONDARY, anchor="e", wraplength=440)
        self.quals.pack(fill="x")
        T.label(self.frame, "ארגון ומספר תעודה נלקחים מהשדות למעלה. בשיבוץ: מי שמגיע מהר קודם; בתוך אותו זמן "
                            "הגעה - הכשרה מאומתת שמתאימה לאירוע, אימות, ואמינות (מענה לקריאות).",
                9, T.TEXT_MUTED, anchor="e", wraplength=440).pack(fill="x")
        self.refresh_quals()

    # ---------- תפקידים: לא חייבים להיות מד"א (server/helpers.py) ----------
    ROLE_ROWS = (("on_scene", "🚶 הגעה למקום", "קריאות בקרבתך - כמו היום"),
                 ("remote_support", "💬 שיחת תמיכה מרחוק",
                  "צ'אט עם מי שצריך/ה להרגיש שמישהו איתו/ה. מכל מקום, בלי לראות שם ומיקום. חשבון בן 3 ימים לפחות."),
                 ("authority_liaison", "👮 הזעקת רשויות",
                  "מתקשר/ת ל-100/101/102 בשביל מי שלא יכול/ה לדבר. רואה שם ומיקום - לכן דורש אימות "
                  "(גם אימות זהות ידני, בלי ארגון)."))
    LANG_ROWS = (("he", "עברית"), ("ar", "ערבית"), ("en", "אנגלית"), ("ru", "רוסית"), ("am", "אמהרית"))

    def _build_roles(self):
        T.label(self.frame, "איך תרצה/י לעזור? (לא חייבים להיות מד\"א)", 11, T.TEXT, bold=True,
                anchor="e").pack(fill="x")
        self.role_vars, self.role_status = {}, {}
        for role, label, sub in self.ROLE_ROWS:
            r = tk.Frame(self.frame, bg=T.CARD_BG)
            r.pack(fill="x", pady=(3, 0))
            var = tk.BooleanVar(value=role == "on_scene")
            tk.Checkbutton(r, text=label, variable=var, command=self.save_roles, font=T.F(10, "bold"), bg=T.CARD_BG,
                           fg=T.TEXT, activebackground=T.CARD_BG, selectcolor=T.CARD_BG_ALT).pack(side="right")
            st = T.label(r, "", 9, T.TEXT_MUTED, anchor="e", wraplength=220)
            st.pack(side="left", fill="x", expand=True)
            T.label(self.frame, sub, 9, T.TEXT_MUTED, anchor="e", wraplength=440).pack(fill="x")
            self.role_vars[role], self.role_status[role] = var, st
        lr = tk.Frame(self.frame, bg=T.CARD_BG)
        lr.pack(fill="x", pady=(4, 0))
        T.label(lr, "שפות לשיחה:", 10, T.TEXT_SECONDARY).pack(side="right")
        self.lang_vars = {}
        for code, label in self.LANG_ROWS:
            var = tk.BooleanVar(value=code == "he")
            tk.Checkbutton(lr, text=label, variable=var, command=self.save_roles, font=T.F(9), bg=T.CARD_BG,
                           fg=T.TEXT, activebackground=T.CARD_BG, selectcolor=T.CARD_BG_ALT).pack(side="right")
            self.lang_vars[code] = var
        self.roles_msg = T.label(self.frame, "", 10, T.GREEN, anchor="e", wraplength=440)
        self.roles_msg.pack(fill="x")
        tk.Frame(self.frame, bg=T.BORDER, height=1).pack(fill="x", pady=8)
        self.refresh_roles()

    def refresh_roles(self):
        c = self.app.client
        if c is None or not getattr(c, "token", None):
            return
        run_bg(self.app, c.volunteer_roles, self.apply_roles, lambda e: None)

    def apply_roles(self, r):
        if not self.roles_msg.winfo_exists():
            return
        roles, eff = r.get("roles") or [], r.get("effective") or {}
        if roles:
            for role, var in self.role_vars.items():
                var.set(role in roles)
        for code, var in self.lang_vars.items():
            var.set(code in (r.get("languages") or ["he"]))
        off = r.get("available") is False          # "פעיל" בתפקיד אבל לא זמין/ה - לא מקבלים בקשות
        for role, st in self.role_status.items():
            state = eff.get(role)
            if state == "active" and off:
                st.configure(text="⏸️ מוגדר - אבל את/ה מסומן/ת כלא זמין/ה", fg=T.YELLOW)
                continue
            st.configure(text="" if state is None else ("✓ פעיל" if state == "active" else f"⏳ {state}"),
                         fg=T.GREEN if state == "active" else T.TEXT_MUTED)

    def save_roles(self):
        c = self.app.client
        roles = [r for r, v in self.role_vars.items() if v.get()]
        if not roles:
            T.flash_feedback(self.roles_msg, "יש לבחור לפחות דרך אחת לעזור", T.RED)
            return self.refresh_roles()
        if c is None or not getattr(c, "token", None):
            T.flash_feedback(self.roles_msg, "❌ לא מחובר לשרת - ההגדרה לא נשמרה", T.RED)
            return
        langs = [k for k, v in self.lang_vars.items() if v.get()] or ["he"]
        run_bg(self.app, lambda: c.set_volunteer_roles(roles, langs),
               lambda r: (self.apply_roles(r), T.flash_feedback(self.roles_msg, "✅ נשמר בשרת")),
               lambda e: T.flash_feedback(self.roles_msg, f"❌ {_err(e)}", T.RED))

    def refresh_quals(self):
        c = self.app.client
        if c is None or not getattr(c, "token", None):
            return
        run_bg(self.app, c.volunteer_history, self._apply_history, lambda e: None)

    def _apply_history(self, h):
        if not self.quals.winfo_exists():
            return
        st = {"verified": "✓ מאומת", "pending": "⏳ בבדיקה", "rejected": "✗ נדחה"}
        q = " · ".join(f"{x['label']} {st.get(x['status'], x['status'])}" for x in h.get("qualifications") or [])
        rel = {"reliable": "עונה לקריאות", "unknown": "עדיין מעט היסטוריה", "poor": "הרבה קריאות ללא מענה"}
        s = h.get("stats") or {}
        self.quals.configure(text=(q + "\n" if q else "") + f"קריאות: {s.get('offers') or 0} · קיבלת: "
                                  f"{s.get('accepted') or 0} · אמינות: {rel.get(h['reliability']['bucket'], '')}")

    def request_qual(self):
        c = self.app.client
        if not c:
            return
        qual = next(k for k, label in QUAL_LABELS if label == self.qual_var.get())
        org, ref = self.org_var.get().strip(), self.ref_var.get().strip()
        run_bg(self.app, lambda: c.request_qualification(qual, org, ref),
               lambda v: (self.refresh_quals(), T.flash_feedback(self.msg, "✅ בקשת אימות ההכשרה נשלחה")),
               lambda e: T.flash_feedback(self.msg, f"❌ {_err(e)}", T.RED))

    def apply(self, vol):
        vol = vol or {}
        ident = vol.get("identity") or {"status": "none"}
        ist = ident.get("status", "none")
        lvl = {"verified": "ok", "pending": "pending"}.get(ist, "unavailable")
        self.id_chip.configure(text=f" {ident.get('status_he') or ist} ", fg=T.STATUS_COLORS.get(lvl, T.TEXT),
                               bg=T.STATUS_BG.get(lvl, T.CARD_BG))
        self.id_btn.configure(state="disabled" if ist in ("verified", "pending") else "normal")
        note = ""
        if ist != "verified":
            note = "לא יישלחו אליך קריאות עד שהזהות תאומת"
        if ist == "rejected" and ident.get("note"):
            note += f" · סיבת הדחייה: {ident['note']}"
        self.id_note.configure(text=note)
        status = vol.get("verification") or "unverified"
        if (status, vol.get("suspended")) != getattr(self, "_last_status", None):
            self._last_status = (status, vol.get("suspended"))
            self.refresh_roles()           # אימות/השעיה משנים אילו תפקידים פעילים בפועל
        txt, lvl = VERIFICATION_LABELS.get(status, (status, "not_verified"))
        if vol.get("suspended"):
            txt, lvl = "מושעה/ית - בבדיקה בעקבות דיווחים", "unavailable"
        self.chip.configure(text=f" {txt} ", fg=T.STATUS_COLORS[lvl], bg=T.STATUS_BG[lvl])
        parts = []
        if vol.get("verification_org") and status in ("pending", "verified"):
            parts.append(f"ארגון: {vol['verification_org']}")
        if vol.get("ratings"):
            parts.append(f"דירוג ממוצע {vol['avg_rating']} ({vol['ratings']} דירוגים)")
        if vol.get("verification_note") and status == "rejected":
            parts.append(f"סיבה: {vol['verification_note']}")
        self.stats.configure(text=" · ".join(parts))
        if status in ("unverified", "rejected"):
            self.form.pack(fill="x", pady=(4, 0), before=self.msg)
        else:
            self.form.pack_forget()

    def open_identity(self):
        if not self.app.client:
            return
        IdentityUploadWindow(self.frame.winfo_toplevel(), self.app, on_done=lambda st: self.app_refresh_identity(st))

    def app_refresh_identity(self, st):
        self.id_chip.configure(text=f" {st.get('status_he') or st.get('status')} ")
        self.id_btn.configure(state="disabled")
        T.flash_feedback(self.msg, "✅ נשלח לאימות - תקבל/י הודעה אחרי הבדיקה")

    def request(self):
        c = self.app.client
        if not c:
            return
        org, ref = self.org_var.get().strip(), self.ref_var.get().strip()
        run_bg(self.app, lambda: c.request_verification(org, ref),
               lambda v: (self.apply(v), T.flash_feedback(self.msg, "✅ הבקשה נשלחה - תעודכן/י כשתיבדק"))
               , lambda e: T.flash_feedback(self.msg, f"❌ {_err(e)}", T.RED))


# ===================================================================== אימות זהות מתנדב/ת
def israeli_id_ok(value):
    """ספרת ביקורת של ת"ז (זהה לבדיקה בשרת - server/volunteer_identity.valid_israeli_id)."""
    from server.volunteer_identity import valid_israeli_id
    return valid_israeli_id(value) is not None


def prepare_photo(path, max_side=1600):
    """מקטין ומקודד מחדש ל-JPEG: נכנס במגבלת הגודל, ו-EXIF (כולל מיקום הצילום) לא נשלח לשרת."""
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)        # בלי exif= -> המטא-דאטה לא נשמר
    return out.getvalue()


class IdentityUploadWindow(tk.Toplevel):
    def __init__(self, master, app, on_done=None):
        super().__init__(master)
        self.app, self.on_done = app, on_done
        self.title("אימות זהות מתנדב/ת")
        self.configure(bg=T.BG)
        self.bind("<Escape>", lambda e: self.destroy())
        c, b = T.card(self)
        c.pack(fill="both", expand=True, padx=16, pady=16)
        section_header(b, "🪪 אימות זהות", "כל מתנדב/ת - גם מי שכבר רשום/ה - צריך/ה לאמת זהות כדי לקבל קריאות. "
                                           "מנהל/ת משווה ידנית את הסלפי לתמונה בתעודה. התמונות נשמרות בשרת מוצפנות "
                                           "ונמחקות מיד אחרי ההחלטה (או אחרי 30 יום). מספר הת\"ז לא נשמר - רק 4 ספרות.")
        self.num = tk.StringVar()
        r = tk.Frame(b, bg=T.CARD_BG)
        r.pack(fill="x", pady=4)
        T.label(r, "מספר תעודת זהות:", 11, T.TEXT).pack(side="right")
        tk.Entry(r, textvariable=self.num, font=T.F(12), justify="left", width=12, bg=T.CARD_BG_ALT, relief="flat",
                 highlightthickness=2, highlightcolor=T.FOCUS).pack(side="right", padx=6)
        self.paths = {"doc": None, "selfie": None}
        self.labels = {}
        for key, text in (("doc", "צילום תעודת הזהות (הצד עם התמונה)"), ("selfie", "סלפי (פנים גלויות)")):
            row = tk.Frame(b, bg=T.CARD_BG)
            row.pack(fill="x", pady=3)
            T.big_button(row, "בחירת קובץ...", lambda k=key: self.pick(k), size=10, pady=3).pack(side="left")
            T.label(row, text, 11, T.TEXT).pack(side="right")
            self.labels[key] = T.label(b, "לא נבחר קובץ", 9, T.TEXT_MUTED, anchor="e")
            self.labels[key].pack(fill="x")
        self.consent = tk.BooleanVar(value=False)
        tk.Checkbutton(b, text="אני מאשר/ת שימוש בתמונות ובמספר הת\"ז לאימות זהותי כמתנדב/ת בלבד",
                       variable=self.consent, bg=T.CARD_BG, fg=T.TEXT, selectcolor=T.CARD_BG_ALT,
                       activebackground=T.CARD_BG, font=T.F(10), anchor="e").pack(fill="x", pady=(8, 2))
        self.send_btn = T.big_button(b, "שליחה לאימות", self.send, size=11, pady=5)
        self.send_btn.pack(anchor="e", pady=(6, 0))
        self.msg = T.label(b, "", 10, T.RED, anchor="e", wraplength=420)
        self.msg.pack(fill="x")

    def pick(self, key):
        from tkinter import filedialog
        p = filedialog.askopenfilename(parent=self, title="בחירת תמונה",
                                       filetypes=[("תמונות", "*.jpg *.jpeg *.png"), ("כל הקבצים", "*.*")])
        if p:
            self.paths[key] = p
            self.labels[key].configure(text=p.replace("\\", "/").split("/")[-1])

    def validate(self):
        if not israeli_id_ok(self.num.get().strip()):
            return "מספר תעודת הזהות לא תקין"
        if not self.paths["doc"] or not self.paths["selfie"]:
            return "צריך צילום של תעודת הזהות וגם סלפי"
        if not self.consent.get():
            return "צריך לאשר את השימוש בתמונות לאימות"
        return None

    def send(self):
        err = self.validate()
        if err:
            self.msg.configure(text=err)
            return
        num, paths, c = self.num.get().strip(), dict(self.paths), self.app.client
        self.send_btn.configure(state="disabled")
        self.msg.configure(text="שולח...", fg=T.TEXT_MUTED)

        def work():
            return c.submit_identity(num, prepare_photo(paths["doc"]), prepare_photo(paths["selfie"]), True)

        def ok(st):
            if self.on_done:
                self.on_done(st)
            self.destroy()

        def fail(e):
            self.send_btn.configure(state="normal")
            self.msg.configure(text=f"❌ {_err(e)}", fg=T.RED)
        run_bg(self.app, work, ok, fail)


# ===================================================================== מידע רפואי
class MedicalWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title("מידע רפואי לחירום")
        self.configure(bg=T.BG)
        self.geometry("560x680")
        self.bind("<Escape>", lambda e: self.destroy())
        c, b = T.card(self)
        c.pack(fill="both", expand=True, padx=16, pady=16)
        section_header(b, "🩺 מידע רפואי לחירום",
                       "מוצפן בשרת. מוצג רק למי שיש לו גישה מלאה לאירוע פעיל שלך (אנשי קשר מחוברים, ומתנדב "
                       "אחרי שקיבל את האירוע) - ורק אם סימנת שיתוף. אחרי סיום האירוע - לא מוצג.")
        self.vars = {}
        for key, label in MEDICAL_LABELS:
            T.label(b, label, 10, T.TEXT_SECONDARY, anchor="e").pack(fill="x", pady=(6, 0))
            v = tk.StringVar()
            tk.Entry(b, textvariable=v, font=T.F(12), justify="right", bg=T.CARD_BG_ALT, relief="flat",
                     highlightthickness=2, highlightcolor=T.FOCUS).pack(fill="x", ipady=4)
            self.vars[key] = v
        self.share = tk.BooleanVar(value=False)
        tk.Checkbutton(b, text="לשתף את המידע הזה עם מי שמגיב לאירוע חירום שלי", variable=self.share,
                       font=T.F(11, "bold"), bg=T.CARD_BG, fg=T.TEXT, selectcolor=T.CARD_BG, anchor="e",
                       activebackground=T.CARD_BG).pack(fill="x", pady=(10, 0))
        T.label(b, "אל תכתוב/י כאן מידע שאינך רוצה שאדם זר (מתנדב) יראה במצב חירום. זה לא תחליף לתג רפואי.",
                10, T.TEXT_MUTED, anchor="e", wraplength=480).pack(fill="x")
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=(10, 0))
        T.primary_button(row, "💾 שמור בשרת", self.save, size=11, pady=6).pack(side="right")
        self.status = T.label(row, "", 10, T.TEXT_SECONDARY)
        self.status.pack(side="right", padx=8)
        if app.client is None or not app.client.token:
            self.status.configure(text="❌ לא מחובר לשרת - לא ניתן לטעון/לשמור", fg=T.RED)
        else:
            self.status.configure(text="טוען...")
            run_bg(app, app.client.get_medical, self._load, lambda e: self.status.configure(text=f"❌ {_err(e)}", fg=T.RED))

    def _load(self, m):
        if not self.winfo_exists():
            return
        for k, v in self.vars.items():
            v.set(m.get(k) or "")
        self.share.set(bool(m.get("share")))
        self.status.configure(text=("עודכן " + time.strftime("%d/%m %H:%M", time.localtime(m["updated_at"])))
                              if m.get("updated_at") else "עדיין לא נשמר מידע", fg=T.TEXT_SECONDARY)

    def save(self):
        c = self.app.client
        if c is None or not c.token:
            return
        data = {k: v.get().strip() for k, v in self.vars.items()}
        data["share"] = self.share.get()
        run_bg(self.app, lambda: c.set_medical(data),
               lambda m: (self._load(m), self.status.configure(text="✅ נשמר בשרת (מוצפן)", fg=T.GREEN)),
               lambda e: self.status.configure(text=f"❌ לא נשמר: {_err(e)}", fg=T.RED))


# ===================================================================== דירוג מתנדב אחרי אירוע
class VolunteerFeedbackWindow(tk.Toplevel):
    def __init__(self, master, app, incident_id, volunteers):
        super().__init__(master)
        self.app, self.incident_id = app, incident_id
        self.title("דירוג המתנדבים שעזרו")
        self.configure(bg=T.BG)
        self.bind("<Escape>", lambda e: self.destroy())
        c, b = T.card(self)
        c.pack(fill="both", expand=True, padx=16, pady=16)
        section_header(b, "🙏 מי עזר לך?", "הדירוג עוזר לקהילה לסמוך על מתנדבים. דיווח על התנהגות לא תקינה "
                                          "נבדק, ומספר דיווחים משעה מתנדב עד לבדיקה.")
        self.rows = []
        for v in volunteers:
            box = tk.Frame(b, bg=T.CARD_BG_ALT)
            box.pack(fill="x", pady=4)
            T.label(box, f"{v['display_name']}" + (" ✓ מאומת/ת" if v.get("verified") else ""), 12, T.TEXT,
                    T.CARD_BG_ALT, bold=True, anchor="e").pack(fill="x", padx=8, pady=(4, 0))
            rating = tk.IntVar(value=0)
            stars = tk.Frame(box, bg=T.CARD_BG_ALT)
            stars.pack(anchor="e", padx=8)
            for n in range(5, 0, -1):
                tk.Radiobutton(stars, text=f"{n}★", variable=rating, value=n, font=T.F(11), bg=T.CARD_BG_ALT,
                               indicatoron=False, padx=8, pady=3, selectcolor=T.LIGHT_BLUE).pack(side="right", padx=1)
            report = tk.StringVar()
            T.label(box, "דיווח על בעיה (אופציונלי):", 10, T.TEXT_SECONDARY, T.CARD_BG_ALT, anchor="e").pack(fill="x", padx=8)
            tk.Entry(box, textvariable=report, font=T.F(11), justify="right", relief="flat").pack(fill="x", padx=8, pady=(0, 6))
            self.rows.append((v["username"], rating, report))
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=(8, 0))
        T.primary_button(row, "שלח", self.send, size=11, pady=5).pack(side="right")
        T.big_button(row, "דלג", self.destroy, size=11, pady=5).pack(side="right", padx=6)
        self.status = T.label(b, "", 10, T.GREEN, anchor="e")
        self.status.pack(fill="x")

    def send(self):
        c = self.app.client
        if c is None:
            return
        todo = [(u, r.get() or None, rep.get().strip() or None) for u, r, rep in self.rows if r.get() or rep.get().strip()]
        if not todo:
            self.destroy()
            return

        def work():
            for u, rating, report in todo:
                c.give_feedback(self.incident_id, u, rating, report)
        run_bg(self.app, work, lambda _: (self.status.configure(text="✅ תודה!"), self.after(1200, self.destroy)),
               lambda e: self.status.configure(text=f"❌ {_err(e)}", fg=T.RED))


def offer_volunteer_feedback(app, server_incident_id):
    """אחרי סיום אירוע: אם מתנדב קיבל את האירוע - מציעים לדרג. נתונים מהשרת בלבד."""
    c = app.client
    if not server_incident_id or c is None or not c.token:
        return

    def work():
        v = c.get_incident(server_incident_id)
        return [r for r in v.get("recipients", []) if r["role"] == "volunteer"
                and r.get("response") in ("accept", "on_my_way", "arrived", "handled")]

    def done(vols):
        if vols:
            VolunteerFeedbackWindow(app.root, app, server_incident_id, vols)
    run_bg(app, work, done, lambda e: None)


# ===================================================================== טלפון: אימות SMS + גיבוי
class PhoneVerifyRow:
    """בחלון ההגדרות. מוצג רק כשהשרת מדווח שאימות SMS מוגדר; אחרת - הסבר כן."""

    def __init__(self, parent, client, bg):
        self.client = client
        self.frame = tk.Frame(parent, bg=bg)
        self.frame.pack(fill="x", padx=20, pady=(0, 8))
        self.bg = bg
        self.status = tk.Label(self.frame, text="", font=T.F(9), fg=T.TEXT_SECONDARY, bg=bg, anchor="e",
                               justify="right", wraplength=480)
        self.status.pack(fill="x")
        self.body = tk.Frame(self.frame, bg=bg)
        self.body.pack(fill="x")
        self._result = []
        if client is None or not client.token:
            self.status.configure(text="אימות טלפון וגיבוי SMS: דורש חיבור לשרת")
            return
        self._bg(client.me, self._render)

    def _bg(self, fn, done):
        import threading
        box = []

        def work():
            try:
                box.append(("ok", fn()))
            except Exception as e:  # noqa: BLE001
                box.append(("err", e))

        def poll():
            if not self.frame.winfo_exists():
                return
            if not box:
                self.frame.after(150, poll)
                return
            kind, val = box[0]
            if kind == "ok":
                done(val)
            else:
                self.status.configure(text=f"❌ {_err(val)}", fg=T.RED)
        threading.Thread(target=work, daemon=True).start()
        self.frame.after(150, poll)

    def _render(self, me):
        _clear(self.body)
        sms = me.get("sms") or {}
        if not sms.get("configured"):
            self.status.configure(text="ℹ️ לשרת אין ספק SMS מוגדר: אין אימות טלפון ואין גיבוי SMS. "
                                       "(מנהל השרת מגדיר Twilio - ראה DEPLOY_HE.md)", fg=T.TEXT_MUTED)
            return
        if not me.get("phone_last4"):
            self.status.configure(text="שמור/י מספר טלפון כדי לאמת אותו ב-SMS", fg=T.TEXT_SECONDARY)
            return
        if me.get("phone_verified"):
            self.status.configure(text=f"✅ הטלפון •••{me['phone_last4']} אומת ב-SMS", fg=T.GREEN)
        else:
            self.status.configure(text=f"הטלפון •••{me['phone_last4']} לא מאומת. אימות מאפשר גיבוי SMS בחירום "
                                       "וחיבור אוטומטי להזמנות שנשלחו למספר שלך.", fg=T.YELLOW)
            row = tk.Frame(self.body, bg=self.bg)
            row.pack(fill="x", pady=2)
            T.primary_button(row, "📲 שלח קוד אימות", size=10, pady=5, padx=10,
                      command=lambda: self._bg(self.client.phone_verify_start,
                                               lambda r: self.status.configure(text="קוד נשלח ב-SMS", fg=T.GREEN))
                      ).pack(side="right")
            self.code = tk.StringVar()
            tk.Entry(row, textvariable=self.code, width=8, font=T.F(11), justify="center").pack(side="right", padx=6)
            T.big_button(row, "אמת", size=10, pady=5, padx=10,
                      command=lambda: self._bg(lambda: self.client.phone_verify_check(self.code.get()),
                                               lambda r: self._bg(self.client.me, self._render))).pack(side="right")
        fb = tk.BooleanVar(value=bool(me.get("sms_fallback")))
        tk.Checkbutton(self.body, text="גיבוי SMS: אם האפליקציה שלי לא אישרה קבלת קריאת חירום תוך דקה - שלחו לי SMS "
                                       "עם קישור מיקום (רק למספר מאומת)", variable=fb, bg=self.bg, anchor="e",
                       justify="right", wraplength=460, font=T.F(9),
                       command=lambda: self._bg(lambda: self.client.set_sms_fallback(fb.get()), lambda r: None)
                       ).pack(fill="x")
