# -*- coding: utf-8 -*-
"""
support_ui.py - "צריך/ה לדבר עם מישהו?" (לא חירום).

- כרטיס במסך הבית + חלון שיחה עם מתנדב/ת תמיכה מרחוק (server/helpers.py, activation='support').
- המתנדב/ת לא רואה שם ולא מיקום. אין אנשי קשר, אין מתנדבים בשטח, אין הקלטות.
- קווי הסיוע הרשמיים מוצגים תמיד - גם בלי שרת ובלי אינטרנט (הרשימה מקומית).
- סטטוס רק לפי מה שהשרת החזיר: "מתנדב/ת הצטרף/ה" רק אחרי קבלה אמיתית.
"""
import tkinter as tk

import ui_theme as T

try:
    from server.helpers import HOTLINES, HOTLINES_NOTE
except Exception:            # התקנה בלי חבילת השרת - רשימה מינימלית
    HOTLINES = [{"name": "ער\"ן - עזרה ראשונה נפשית", "number": "1201", "note": "24/7"},
                {"name": "משטרה", "number": "100", "note": "בסכנה מיידית"}]
    HOTLINES_NOTE = ""


def hotlines_text(hotlines=None):
    out = []
    for h in hotlines or HOTLINES:
        tail = h.get("number") or h.get("url") or ""
        note = f" ({h['note']})" if h.get("note") else ""
        out.append(f"☎️ {h['name']}: {tail}{note}")
    return "\n".join(out)


def support_status(view):
    """טקסט + צבע למצב הבקשה - רק לפי נתוני השרת."""
    if not view:
        return "", T.TEXT_SECONDARY
    if view.get("status") != "active":
        return "השיחה הסתיימה. אפשר תמיד לפתוח שיחה חדשה או לפנות לקווי הסיוע.", T.TEXT_SECONDARY
    hs = view.get("helpers") or []
    if any(h["state"] == "accepted" for h in hs):
        return "✅ מתנדב/ת הצטרף/ה לשיחה - אפשר לכתוב. המתנדב/ת לא רואה את שמך ואת מיקומך.", T.GREEN
    if any(h["state"] == "notified" for h in hs):
        n = sum(1 for h in hs if h["state"] == "notified")
        return f"⏳ הבקשה נשלחה ל-{n} מתנדבים. ממתינים שמישהו יקבל - בינתיים אפשר לכתוב כאן.", T.TEXT_SECONDARY
    if any(h["state"] == "done" for h in hs):
        return "המתנדב/ת סיים/ה את השיחה. אפשר לסגור ולפתוח בקשה חדשה.", T.TEXT_SECONDARY
    return ("⚠️ אין כרגע מתנדבי שיחה זמינים. הבקשה נשארת פתוחה וננסה שוב - "
            "ובינתיים אפשר לפנות לקווי הסיוע למטה."), T.YELLOW


class SupportCard:
    """כרטיס קטן במסך הבית."""

    def __init__(self, parent, app):
        self.app = app
        c, b = T.card(parent)
        c.pack(fill="x", pady=(0, 12))
        T.label(b, "💬 צריך/ה לדבר עם מישהו?", 13, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(b, "לא חייב להיות חירום. מתנדב/ת יקשיב/תקשיב בצ'אט - בלי לראות את שמך ואת מיקומך.",
                10, T.TEXT_SECONDARY, anchor="e", wraplength=440).pack(fill="x", pady=(2, 6))
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x")
        T.big_button(row, "💬 פתח/י שיחה", app.open_support, bg=T.BLUE, size=11).pack(side="right")
        T.label(row, "ער\"ן 1201 · בסכנה מיידית 100", 10, T.TEXT_MUTED, anchor="e").pack(side="right", padx=10)


class SupportWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app, self.iid, self.view = app, None, None
        self.title("LifeGuard | שיחה עם מתנדב/ת")
        self.geometry("560x720")
        self.minsize(480, 560)
        self.configure(bg=T.BG)

        head = tk.Frame(self, bg=T.NAVY)
        head.pack(fill="x")
        tk.Label(head, text="💬 מישהו לדבר איתו", font=T.F(18, "bold"), fg=T.ON_NAVY, bg=T.NAVY).pack(pady=(12, 2))
        tk.Label(head, text="לא חירום · אנונימי למתנדב/ת · אפשר לסגור בכל רגע", font=T.F(11), fg=T.ON_NAVY_MUTED,
                 bg=T.NAVY).pack(pady=(0, 10))

        outer, body = T.scrollable(self)
        outer.pack(fill="both", expand=True, padx=14, pady=10)
        outer.bind_all_keys(self)

        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 10))
        self.start_frame = b
        T.label(b, "מה עובר עליך? (לא חובה - אפשר גם להתחיל בלי לכתוב)", 11, T.TEXT, anchor="e").pack(fill="x")
        # שורת קלט (לא Text): עם תיקון RTL - רווח אחרי מילה עברית מוצג במקום הנכון (ui_theme.rtl_entry)
        self.note_var = tk.StringVar(master=self)
        self.note = tk.Entry(b, textvariable=self.note_var, font=T.F(12), justify="right", bg=T.CARD_BG_ALT,
                             fg=T.TEXT, relief="flat", highlightthickness=2, highlightcolor=T.FOCUS,
                             highlightbackground=T.BORDER)
        self.note.pack(fill="x", pady=(4, 6), ipady=6)
        self.note.bind("<Return>", lambda e: self.start())
        T.rtl_entry(self.note, self.note_var)
        self.start_btn = T.big_button(b, "🔎 חפש/י מתנדב/ת לשיחה", self.start, bg=T.BLUE, size=12)
        self.start_btn.pack(anchor="e")
        self.status = T.label(b, "", 11, T.TEXT_SECONDARY, anchor="e", wraplength=480)
        self.status.pack(fill="x", pady=(6, 0))

        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 10))
        from chat_panel import ChatPanel
        self.chat = ChatPanel(b, app, lambda: self.iid, getattr(app, "username", None),
                              on_error=lambda t: self.status.configure(text="❌ " + t, fg=T.RED))
        self.chat_card = c
        self.chat.pack(fill="x")
        self.end_btn = T.big_button(b, "סיום השיחה", self.end, size=10)

        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 10))
        T.label(b, "קווי סיוע (זמינים תמיד, גם בלי האפליקציה)", 12, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(b, hotlines_text(), 11, T.TEXT_SECONDARY, anchor="e", wraplength=480).pack(fill="x", pady=(4, 0))
        if HOTLINES_NOTE:
            T.label(b, HOTLINES_NOTE, 9, T.TEXT_MUTED, anchor="e").pack(fill="x", pady=(4, 0))
        T.label(b, "בסכנה מיידית לחצו SOS או התקשרו 100 / 101 - שיחה עם מתנדב/ת אינה שירות חירום.",
                10, T.RED, anchor="e", wraplength=480).pack(fill="x", pady=(6, 0))

        if app.client is None or not getattr(app.client, "token", None):
            self.start_btn.configure(state="disabled")
            self.status.configure(text="לא מחובר לשרת LifeGuard - השיחה עם מתנדבים לא זמינה כרגע. "
                                       "קווי הסיוע למטה זמינים תמיד.", fg=T.YELLOW)

    # ---------- פעולות ----------
    def start(self):
        from app_screens import run_bg
        client = self.app.client
        note = T.entry_text(self.note_var.get()).strip()[:500]
        self.start_btn.configure(state="disabled")
        self.status.configure(text="⏳ שולח בקשה לשרת...", fg=T.TEXT_SECONDARY)

        def err(e):
            if self.winfo_exists():
                self.start_btn.configure(state="normal")
                self.status.configure(text=f"❌ {getattr(e, 'message', e)}", fg=T.RED)
        run_bg(self.app, lambda: client.create_support_request(note, "he"), self._apply, err)

    def refresh(self):
        if not self.iid or self.app.client is None or not self.winfo_exists():
            return
        from app_screens import run_bg
        client, iid = self.app.client, self.iid
        run_bg(self.app, lambda: client.get_incident(iid), self._apply, lambda e: None)

    def _apply(self, view):
        if not self.winfo_exists():
            return
        self.view, self.iid = view, view["id"]
        text, color = support_status(view)
        self.status.configure(text=text, fg=color)
        self.chat.render(view)
        active = view.get("status") == "active"
        self.start_btn.configure(state="disabled")
        if active and not self.end_btn.winfo_ismapped():
            self.end_btn.pack(anchor="e", pady=(6, 0))
        elif not active:
            self.end_btn.pack_forget()
            self.start_btn.configure(state="normal")
            self.iid = None
        if getattr(self, "_poll", None):
            self.after_cancel(self._poll)
        self._poll = self.after(8000, self.refresh) if active else None     # גיבוי לעדכוני push

    def end(self):
        from app_screens import run_bg
        if not self.iid:
            return
        client, iid = self.app.client, self.iid
        run_bg(self.app, lambda: client.update_incident(iid, status="resolved"), self._apply,
               lambda e: self.status.configure(text=f"❌ {getattr(e, 'message', e)}", fg=T.RED))
