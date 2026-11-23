# -*- coding: utf-8 -*-
"""
chat_panel.py - צ'אט של אירוע חירום: בעל/ת האירוע, אנשי הקשר והמתנדבים.

- הודעות מהירות בלחיצה אחת (מי שבמצוקה לא תמיד יכול/ה להקליד). הנוסח נקבע בשרת.
- שם, תפקיד ושעה לכל הודעה; אישור קריאה להודעות שלי ("נקרא ע"י ...").
- סימון "נקרא" נשלח לשרת רק כשהחלון גלוי (אחרת "נקרא" יהיה שקר).
"""

import time
import tkinter as tk

import ui_theme as T

ROLE_HE = {"owner": "במצוקה", "trusted": "איש קשר", "volunteer": "מתנדב/ת", "helper": "מתנדב/ת מרחוק"}


def voice_input_max():
    import voice_input
    return voice_input.MAX_SECONDS


class ChatPanel(tk.Frame):
    def __init__(self, parent, app, incident_id_fn, username, bg=None, height=8, on_error=None, quick_cols=None,
                 voice=True):
        super().__init__(parent, bg=bg or T.CARD_BG)
        self.quick_cols = quick_cols        # עמודה צרה (מסך SOS): כפתורים מהירים בשורות, לא שורה אחת שנחתכת
        self.app, self.incident_id_fn, self.username = app, incident_id_fn, username
        self.on_error = on_error or (lambda text: None)
        self._last_read_sent = 0
        self._quick_codes = None
        bg = bg or T.CARD_BG
        T.label(self, "💬 צ'אט האירוע", 12, T.TEXT, bold=True, anchor="e").pack(fill="x")
        self.text = tk.Text(self, height=height, wrap="word", font=T.F(11), bg=T.CARD_BG_ALT, fg=T.TEXT,
                            relief="flat", padx=8, pady=6, state="disabled")
        self.text.tag_configure("rtl", justify="right")
        self.text.tag_configure("meta", foreground=T.TEXT_MUTED, font=T.F(9))
        self.text.tag_configure("mine", foreground=T.BLUE)
        self.text.pack(fill="x")
        self.quick_row = tk.Frame(self, bg=bg)
        self.quick_row.pack(fill="x", pady=(6, 0))
        row = tk.Frame(self, bg=bg)
        row.pack(fill="x", pady=(6, 0))
        self.var = tk.StringVar()
        e = tk.Entry(row, textvariable=self.var, font=T.F(12), justify="right", bg=T.CARD_BG_ALT, relief="flat",
                     highlightthickness=2, highlightcolor=T.FOCUS)
        e.pack(side="right", fill="x", expand=True, ipady=5)
        e.bind("<Return>", lambda ev: self.send())
        T.rtl_entry(e, self.var)
        T.primary_button(row, "שלח", self.send, size=11, pady=5).pack(side="right", padx=(0, 6))
        self.entry = e
        # הכתבה קולית - זיהוי מקומי בלבד; הטקסט נכנס לשורה לבדיקה לפני שליחה (זיהוי שגוי לא יוצא לבד)
        self.mic_btn, self._dictation = None, None
        self.voice_status = T.label(self, "", 9, T.TEXT_MUTED, anchor="e")
        if voice:
            import voice_input
            if voice_input.available():
                self.mic_btn = T.big_button(row, "🎤", self.toggle_dictation, bg=T.CARD_BG_ALT, size=11, pady=3)
                self.mic_btn.pack(side="right", padx=(0, 6))
                self.voice_status.pack(fill="x")
                try:        # טעינת המודל ברקע (~6 שנ' בפעם הראשונה) - כדי שההכתבה הראשונה לא תחכה
                    import speech_local
                    speech_local.preload(["he"], wait=False)
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------ הכתבה קולית
    def toggle_dictation(self):
        import voice_input
        if self._dictation is not None and self._dictation.active:
            self._dictation.stop()
            self.mic_btn.configure(state="disabled", text="⏳")
            self.voice_status.configure(text="מזהה דיבור (במחשב, בלי אינטרנט)...")
            return
        box = {}
        self._dictation = voice_input.Dictation(lambda text, err: box.update(text=text, err=err))
        self._dictation.start()
        self.mic_btn.configure(text="⏹️", bg=T.SOS_FILL, fg="#FFFFFF")
        self._dictation_poll(box)

    def _dictation_poll(self, box):
        if not self.winfo_exists():
            if self._dictation is not None:
                self._dictation.stop()
            return
        if "text" not in box:
            d = self._dictation
            if d is not None and d.active and not d._stop.is_set():
                self.voice_status.configure(text=f"🔴 מקליט... {int(d.elapsed())} שנ' - לחצו ⏹️ לסיום "
                                                 f"(עד {voice_input_max()} שנ')")
            self.after(200, lambda: self._dictation_poll(box))
            return
        self.mic_btn.configure(state="normal", text="🎤", bg=T.CARD_BG_ALT, fg=T.TEXT)
        if box.get("text"):
            cur = T.entry_text(self.var.get()).strip()
            self.var.set((cur + " " if cur else "") + box["text"])
            self.entry.focus_set()
            self.entry.icursor("end")
            self.voice_status.configure(text="✅ נוסף לשורה - בדקו ולחצו שלח / Enter")
        else:
            self.voice_status.configure(text="לא זוהה דיבור" + (f" ({box['err']})" if box.get("err") else "")
                                             + " - אפשר להקליד או להשתמש בהודעות המהירות")

    # ------------------------------------------------------------ תצוגה
    def render(self, view):
        msgs = (view or {}).get("messages") or []
        self._render_quick((view or {}).get("quick_replies") or {})
        lines = []
        for m in msgs[-30:]:
            mine = m["sender"] == self.username
            when = time.strftime("%H:%M", time.localtime(m["created_at"]))
            who = "את/ה" if mine else f"{m['sender_display']} ({ROLE_HE.get(m['role'], m['role'])})"
            receipt = ""
            if mine:
                receipt = ("  ✓✓ נקרא ע\"י " + ", ".join(m["read_by"])) if m.get("read_by") else "  ✓ נשלח"
            lines.append((f"{when} · {who}{receipt}\n", "meta"))
            lines.append((m["text"] + "\n\n", "mine" if mine else None))
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for chunk, tag in lines:
            self.text.insert("end", chunk, ("rtl",) + ((tag,) if tag else ()))
        if not lines:
            self.text.insert("end", "אין עדיין הודעות.", ("rtl", "meta"))
        self.text.configure(state="disabled")
        self.text.see("end")
        self._mark_read(msgs)

    def _render_quick(self, quick):
        codes = tuple(quick.items())
        if codes == self._quick_codes:
            return
        self._quick_codes = codes
        for w in self.quick_row.winfo_children():
            w.destroy()
        n = self.quick_cols
        for i, (code, text) in enumerate(codes):
            b = T.big_button(self.quick_row, text, lambda c=code: self.send(quick=c), bg=T.LIGHT_BLUE, size=10,
                             bold=False, padx=8, pady=5)
            if n:
                b.grid(row=i // n, column=n - 1 - i % n, sticky="ew", padx=2, pady=2)     # מימין לשמאל
            else:
                b.pack(side="right", padx=2, pady=2)
        for c in range(n or 0):
            self.quick_row.grid_columnconfigure(c, weight=1, uniform="q")

    def _mark_read(self, msgs):
        others = [m["id"] for m in msgs if m["sender"] != self.username]
        if not others or max(others) <= self._last_read_sent or not self.winfo_viewable():
            return
        up_to = max(others)
        self._last_read_sent = up_to
        client, iid = self.app.client, self.incident_id_fn()
        if client is None or not iid:
            return
        from app_screens import run_bg
        run_bg(self.app, lambda: client.mark_read(iid, up_to), None, lambda e: None)

    # ------------------------------------------------------------ שליחה
    def send(self, quick=None):
        text = T.entry_text(self.var.get()).strip()
        if not quick and not text:
            return
        client, iid = self.app.client, self.incident_id_fn()
        if client is None or not iid:
            self.on_error("אין חיבור לשרת - ההודעה לא נשלחה. התקשר/י 100.")
            return
        from app_screens import run_bg

        def ok(_):
            if not quick:
                self.var.set("")
            self.refresh()

        run_bg(self.app, lambda: client.send_message(iid, text=None if quick else text, quick=quick), ok,
               lambda e: self.on_error(f"ההודעה לא נשלחה: {getattr(e, 'message', e)}"))

    def refresh(self):
        client, iid = self.app.client, self.incident_id_fn()
        if client is None or not iid:
            return
        from app_screens import run_bg
        run_bg(self.app, lambda: client.get_incident(iid), lambda v: self.winfo_exists() and self.render(v),
               lambda e: None)
