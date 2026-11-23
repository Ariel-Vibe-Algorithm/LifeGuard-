# -*- coding: utf-8 -*-
"""health_ui.py - לוח תקינות מערכת: מה עובד, מה לא, למה זה חשוב ואיך מתקנים."""

import threading
import time
import tkinter as tk

import health_module as H
import ui_theme as T

OVERALL_TEXT = {
    H.OK: ("✅ כל הרכיבים שנבדקו פועלים", T.GREEN),
    H.LIMITED: ("⚠️ פעולה מוגבלת - חלק מהרכיבים לא פועלים במלואם", T.YELLOW),
    H.UNAVAILABLE: ("❗ פונקציות חירום חיוניות לא זמינות - ראה/י פירוט", T.RED),
}


class HealthWindow(tk.Toplevel):
    def __init__(self, master, engine=None, username=None):
        super().__init__(master)
        self.engine = engine
        self.username = username
        self.title("LifeGuard | תקינות מערכת")
        self.geometry("900x800")
        self.minsize(700, 560)
        self.configure(bg=T.BG)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<F5>", lambda e: self.run_checks())

        T.label(self, "🩺 תקינות מערכת", 20, T.TEXT, bold=True).pack(pady=(14, 2))
        T.label(self, "כל רכיב נבדק בפועל. 'לא נבדק' = לא הופעל כדי לא להבהיל (סירנה/מצלמה). F5 לבדיקה חוזרת",
                10, T.TEXT_MUTED, wraplength=820).pack()
        self.overall = T.label(self, "בודק...", 15, T.YELLOW, bold=True, wraplength=820)
        self.overall.pack(pady=(10, 4))
        self.checked_label = T.label(self, "", 10, T.TEXT_MUTED)
        self.checked_label.pack()
        self.rerun_btn = T.big_button(self, "🔄 בדוק שוב", self.run_checks, size=11)
        self.rerun_btn.pack(pady=6)
        outer, self.inner = T.scrollable(self)
        outer.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.run_checks()

    def run_checks(self):
        self.rerun_btn.configure(state="disabled", text="⏳ בודק... (עד כ-15 שניות)")
        self.overall.configure(text="בודק את כל הרכיבים...", fg=T.YELLOW)

        # ה-thread רק ממלא את holder; ה-UI thread דוגם אותו. (Tk אינו thread-safe - קריאה ל-after()
        # מ-thread רקע עלולה לזרוק RuntimeError ולהשאיר את המסך תקוע על "בודק...").
        holder = {}

        def _work():
            holder["results"] = H.run_all(self.engine, self.username)
        threading.Thread(target=_work, daemon=True).start()

        def _poll():
            if not self.winfo_exists():
                return
            if "results" in holder:
                self._render(holder["results"])
            else:
                self.after(200, _poll)
        self.after(200, _poll)

    def _render(self, results):
        if not self.winfo_exists():
            return
        for w in self.inner.winfo_children():
            w.destroy()
        overall = H.summarize(results)
        text, color = OVERALL_TEXT[overall]
        self.overall.configure(text=text, fg=color)
        self.checked_label.configure(text="נבדק: " + time.strftime("%H:%M:%S"))
        self.rerun_btn.configure(state="normal", text="🔄 בדוק שוב")
        order = {H.UNAVAILABLE: 0, H.LIMITED: 1, H.NOT_VERIFIED: 2, H.OK: 3}
        for r in sorted(results, key=lambda r: (order.get(r["status"], 9), not r["affects_emergency"])):
            self._render_row(r)

    def _render_row(self, r):
        color = T.STATUS_COLORS.get(r["status"], T.TEXT_MUTED)
        box = tk.Frame(self.inner, bg=T.CARD_BG, highlightbackground=color, highlightthickness=2)
        box.pack(fill="x", padx=6, pady=5)
        head = tk.Frame(box, bg=T.CARD_BG)
        head.pack(fill="x", padx=10, pady=(8, 2))
        T.label(head, r["name"], 14, T.TEXT, T.CARD_BG, bold=True).pack(side="right")
        T.label(head, f"  {H.STATUS_LABELS.get(r['status'], r['status'])}  ", 11, T.BG, color,
                bold=True).pack(side="left")
        if r["affects_emergency"] and r["status"] in (H.UNAVAILABLE, H.LIMITED):
            T.label(head, "משפיע על חירום", 10, T.RED, T.CARD_BG, bold=True).pack(side="left", padx=8)
        for title, key in (("מה לא עובד", "what"), ("למה זה חשוב", "why"), ("איך מתקנים", "fix")):
            if r.get(key):
                T.label(box, f"{title}: {r[key]}", 11, T.TEXT_SECONDARY if key != "fix" else T.TEXT,
                        T.CARD_BG, anchor="e", wraplength=800).pack(fill="x", padx=12, pady=1)
        if r.get("detail"):
            T.label(box, r["detail"], 10, T.TEXT_MUTED, T.CARD_BG, anchor="e", wraplength=800).pack(fill="x", padx=12)
        tk.Frame(box, bg=T.CARD_BG, height=6).pack()
