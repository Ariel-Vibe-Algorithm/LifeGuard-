# -*- coding: utf-8 -*-
"""
incident_history_ui.py
היסטוריית אירועים: רשימה, ציר זמן מפורט, ייצוא דוח, מחיקה ומדיניות שמירה.

אבטחה: נפתח רק אחרי הזנת סיסמת החשבון (כמו מסך ההגדרות) - ההיסטוריה כוללת מיקומים
ואירועי מצוקה שקטה/כפייה, ואסור שמי שניגש למחשב פתוח יראה אותם.
"""

import time
import os
import tkinter as tk
from tkinter import filedialog

import auth_module
import incident_store
import ui_theme as T

KIND_LABELS = {"SOS": "🚨 SOS", "SILENT_ALERT": "🤫 מצוקה שקטה"}
OUTCOME_LABELS = {
    None: "פעיל / לא הסתיים",
    "stopped_by_user": "הופסק ע\"י המשתמש",
    "cancelled_during_countdown": "בוטל בספירה",
    "cancelled_during_assessment": "בוטל בהערכה",
    "silent_tracking_stopped_by_account_owner": "מעקב שקט הופסק",
    "app_shutdown": "התוכנה נסגרה באמצע",
    "interrupted": "נקטע (קריסה/כיבוי)",
}
CATEGORY_ICONS = {"state": "⚙️", "user_action": "👤", "location": "📍", "delivery": "📨",
                  "acknowledgment": "✅", "media": "🎙️", "system": "🖥️", "guidance": "🧭", "note": "📝"}


def _fmt(ts):
    return time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ts)) if ts else "—"


class IncidentHistoryWindow(tk.Toplevel):
    def __init__(self, master, username, client=None, vault=None):
        super().__init__(master)
        self.username = username
        self.client, self.vault = client, vault
        self.title("LifeGuard | היסטוריית אירועים")
        self.geometry("1040x760")
        self.minsize(820, 560)
        self.configure(bg=T.BG)
        self.bind("<Escape>", lambda e: self.destroy())
        self.incidents = []
        self.selected_id = None
        self._build_gate()

    # ---------- שער סיסמה ----------
    def _build_gate(self):
        self.gate = tk.Frame(self, bg=T.BG)
        self.gate.pack(fill="both", expand=True)
        T.label(self.gate, "🗂️ היסטוריית אירועים", 20, T.TEXT, bold=True).pack(pady=(60, 8))
        T.label(self.gate, "ההיסטוריה כוללת מיקומים ואירועים רגישים. הזן/י את סיסמת החשבון:", 12,
                T.TEXT_SECONDARY).pack(pady=(0, 14))
        self.pw = tk.StringVar()
        entry = tk.Entry(self.gate, textvariable=self.pw, show="•", font=T.F(16), justify="center", width=20,
                         highlightthickness=3, highlightcolor=T.FOCUS)
        entry.pack()
        entry.bind("<Return>", lambda e: self._unlock())
        entry.focus_set()
        self.gate_err = T.label(self.gate, "", 12, T.RED)
        self.gate_err.pack(pady=8)
        T.big_button(self.gate, "פתח", self._unlock, bg=T.RED_DARK).pack()

    def _unlock(self):
        if not auth_module.verify_password(self.username, self.pw.get()):
            self.gate_err.configure(text="סיסמה שגויה")
            self.pw.set("")
            return
        self.pw.set("")
        self.gate.destroy()
        self._build_main()

    # ---------- מסך ראשי ----------
    def _build_main(self):
        top = tk.Frame(self, bg=T.BG)
        top.pack(fill="x", padx=14, pady=(12, 4))
        T.label(top, "🗂️ היסטוריית אירועים", 18, T.TEXT, bold=True).pack(side="right")
        self.status = T.label(top, "", 11, T.GREEN)
        self.status.pack(side="left")

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=14, pady=6)
        right = tk.Frame(body, bg=T.CARD_BG, width=340, highlightbackground=T.BORDER, highlightthickness=1)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        T.label(right, "אירועים", 13, T.TEXT, T.CARD_BG, bold=True, anchor="e").pack(fill="x", padx=10, pady=8)
        self.listbox = tk.Listbox(right, font=T.F(11), bg=T.CARD_BG_ALT, fg=T.TEXT, selectbackground=T.RED_DARK,
                                  bd=0, highlightthickness=2, highlightcolor=T.FOCUS, justify="right",
                                  activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._show_selected())

        left = tk.Frame(body, bg=T.BG)
        left.pack(side="right", fill="both", expand=True, padx=(0, 10))
        self.summary = T.label(left, "בחר/י אירוע מהרשימה", 12, T.TEXT_SECONDARY, anchor="e", wraplength=620)
        self.summary.pack(fill="x", pady=(0, 6))
        legend = tk.Frame(left, bg=T.BG)
        legend.pack(fill="x")
        T.label(legend, "■ מאושר - פעולת משתמש/אדם", 10, T.GREEN).pack(side="right", padx=6)
        T.label(legend, "■ אוטומטי - נאסף ע\"י המערכת, לא אומת", 10, T.TEXT_MUTED).pack(side="right", padx=6)
        self.timeline = tk.Text(left, font=T.F(11), bg=T.CARD_BG, fg=T.TEXT, wrap="word", bd=0,
                                highlightthickness=2, highlightcolor=T.FOCUS, padx=10, pady=8)
        self.timeline.tag_configure("confirmed", foreground=T.GREEN, justify="right")
        self.timeline.tag_configure("automatic", foreground=T.TEXT_SECONDARY, justify="right")
        self.timeline.tag_configure("time", foreground=T.TEXT_MUTED, justify="right")
        self.timeline.pack(fill="both", expand=True)
        self.timeline.configure(state="disabled")

        actions = tk.Frame(self, bg=T.BG)
        actions.pack(fill="x", padx=14, pady=8)
        T.big_button(actions, "📄 ייצוא דוח (טקסט)", lambda: self._export("txt"), size=11).pack(side="right", padx=4)
        T.big_button(actions, "🧾 ייצוא JSON", lambda: self._export("json"), size=11).pack(side="right", padx=4)
        T.big_button(actions, "📦 חבילת ראיות", self._export_package, size=11).pack(side="right", padx=4)
        T.big_button(actions, "🧠 דיוק ההערכות", self._show_ledger_report, size=11).pack(side="right", padx=4)
        self.delete_btn = T.big_button(actions, "🗑️ מחק אירוע", self._ask_delete, bg=T.RED_DARK, size=11)
        self.delete_btn.pack(side="right", padx=4)
        self.confirm_frame = tk.Frame(actions, bg=T.BG)
        self.delete_media_var = tk.BooleanVar(value=True)

        retention = tk.Frame(self, bg=T.BG)
        retention.pack(fill="x", padx=14, pady=(0, 12))
        T.label(retention, "מחיקה אוטומטית של אירועים שהסתיימו אחרי (ימים, 0 = לעולם לא):", 11,
                T.TEXT_SECONDARY).pack(side="right")
        self.retention_var = tk.StringVar(value=str(incident_store.get_retention_days(self.username)))
        tk.Spinbox(retention, from_=0, to=3650, increment=30, textvariable=self.retention_var, width=6,
                   font=T.F(11), justify="center").pack(side="right", padx=6)
        T.big_button(retention, "שמור", self._save_retention, size=10, pady=4).pack(side="right")
        self.outbox_label = T.label(retention, "", 10, T.TEXT_MUTED)
        self.outbox_label.pack(side="left")
        self._refresh()

    def _refresh(self):
        try:
            self.incidents = incident_store.list_incidents(self.username)
        except Exception as exc:
            self.summary.configure(text=f"⚠️ טעינת ההיסטוריה נכשלה: {exc}", fg=T.RED)
            return
        self.listbox.delete(0, "end")
        for inc in self.incidents:
            self.listbox.insert("end", f"{_fmt(inc['started_at'])}  {KIND_LABELS.get(inc['kind'], inc['kind'])}")
        if not self.incidents:
            self.summary.configure(text="אין אירועים שמורים. כל אירוע עתידי יתועד כאן אוטומטית.")
        counts = incident_store.outbox_counts(self.username)
        if counts:
            names = {"pending": "ממתינות", "provider_accepted": "נשלחו באיחור", "expired": "פג תוקף",
                     "no_channel": "ללא ערוץ", "failed": "נכשלו"}
            self.outbox_label.configure(text="תור שליחה: " + ", ".join(f"{names.get(k, k)} {v}" for k, v in counts.items()))

    def _show_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        inc_meta = self.incidents[sel[0]]
        self.selected_id = inc_meta["id"]
        self._hide_confirm()
        inc = incident_store.get_incident(self.username, self.selected_id)
        if inc is None:
            return
        duration = ""
        if inc["ended_at"]:
            mins = (inc["ended_at"] - inc["started_at"]) / 60
            duration = f" · משך {mins:.1f} דק׳"
        self.summary.configure(
            fg=T.TEXT,
            text=(f"{KIND_LABELS.get(inc['kind'], inc['kind'])} · התחיל {_fmt(inc['started_at'])}{duration}\n"
                  f"תוצאה: {OUTCOME_LABELS.get(inc['outcome'], inc['outcome'])} · דירוג: {inc['rating'] or '—'}"),
        )
        self.timeline.configure(state="normal")
        self.timeline.delete("1.0", "end")
        for ev in inc["events"]:
            tag = "confirmed" if ev["provenance"] == incident_store.PROVENANCE_CONFIRMED else "automatic"
            icon = CATEGORY_ICONS.get(ev["category"], "•")
            self.timeline.insert("end", time.strftime("%H:%M:%S", time.localtime(ev["ts"])) + "  ", "time")
            self.timeline.insert("end", f"{icon} {ev['message']}\n", tag)
        self.timeline.configure(state="disabled")

    def _show_ledger_report(self):
        """דו"ח חוקר הטעויות (assessment_ledger): מה ההערכה אמרה מול הדירוג שלך, לפי מקור ראיה."""
        try:
            import assessment_ledger
            text = assessment_ledger.format_report(assessment_ledger.report(self.username))
        except Exception as exc:  # noqa: BLE001
            text = f"⚠️ טעינת פנקס ההערכות נכשלה: {exc}"
        self.selected_id = None
        self.listbox.selection_clear(0, "end")
        self.summary.configure(fg=T.TEXT, text="🧠 דיוק ההערכות - מה המערכת העריכה מול מה שדירגת אחרי האירוע")
        self.timeline.configure(state="normal")
        self.timeline.delete("1.0", "end")
        self.timeline.insert("end", text + "\n", "automatic")
        self.timeline.configure(state="disabled")

    def _export(self, fmt):
        if not self.selected_id:
            T.flash_feedback(self.status, "בחר/י אירוע תחילה", T.YELLOW)
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=f".{fmt}",
            initialfile=f"lifeguard_{self.selected_id}.{fmt}",
            filetypes=[("Text", "*.txt")] if fmt == "txt" else [("JSON", "*.json")],
        )
        if not path:
            return
        ok, msg = incident_store.export_incident(self.username, self.selected_id, path, fmt)
        T.flash_feedback(self.status, ("✅ " if ok else "❌ ") + msg + (" (הקובץ אינו מוצפן)" if ok else ""),
                         T.GREEN if ok else T.RED, 9000)

    def _export_package(self):
        """חבילת ראיות: מדיה מקורית מהכספת + ציר הזמן + רשומת השרת החתומה + SHA256SUMS + יומן גישה."""
        if not self.selected_id:
            T.flash_feedback(self.status, "בחר/י אירוע תחילה", T.YELLOW)
            return
        if self.vault is None:
            T.flash_feedback(self.status, "כספת הראיות לא זמינה", T.RED)
            return
        dest = filedialog.askdirectory(parent=self, title="תיקייה ריקה לחבילת הראיות")
        if not dest:
            return
        dest = os.path.join(dest, f"lifeguard_evidence_{self.selected_id}")
        if os.path.exists(dest) and os.listdir(dest):
            T.flash_feedback(self.status, "❌ התיקייה כבר קיימת ואינה ריקה - בחר/י מקום אחר", T.RED, 9000)
            return
        timeline = incident_store.get_incident(self.username, self.selected_id)
        server_record, server_note = None, "לא מחובר לשרת - בלי רשומת שרת חתומה"
        if self.client is not None and self.client.token:
            try:
                server_record = self.client.evidence_by_client(self.selected_id)
                server_note = "כולל רשומת שרת חתומה"
            except Exception as exc:  # noqa: BLE001 - החבילה המקומית עדיין שימושית
                server_note = f"רשומת השרת לא התקבלה ({getattr(exc, 'message', exc)})"
        try:
            res = self.vault.export_package(self.selected_id, dest, timeline=timeline,
                                            server_record=server_record, actor=self.username)
        except Exception as exc:  # noqa: BLE001
            T.flash_feedback(self.status, f"❌ הייצוא נכשל: {exc}", T.RED, 9000)
            return
        chk = res.get("server_record") or {}
        T.flash_feedback(self.status, f"✅ נשמר: {res['media']} קבצי מדיה · {server_note}"
                         + (" · החתימה אומתה" if chk.get("verified") else "")
                         + " (הקבצים אינם מוצפנים - לשמור במקום בטוח)", T.GREEN, 12000)

    # מחיקה עם אישור בתוך החלון (לא דיאלוג קופץ) - פעולה בלתי הפיכה
    def _ask_delete(self):
        if not self.selected_id:
            T.flash_feedback(self.status, "בחר/י אירוע תחילה", T.YELLOW)
            return
        for w in self.confirm_frame.winfo_children():
            w.destroy()
        T.label(self.confirm_frame, "למחוק לצמיתות?", 11, T.YELLOW).pack(side="right", padx=4)
        tk.Checkbutton(self.confirm_frame, text="כולל הקלטות ותמונות", variable=self.delete_media_var,
                       font=T.F(10), fg=T.TEXT, bg=T.BG, selectcolor=T.CARD_BG_ALT,
                       activebackground=T.BG).pack(side="right", padx=4)
        T.big_button(self.confirm_frame, "כן, מחק", self._do_delete, bg=T.RED_DARK, size=10, pady=4).pack(side="right", padx=4)
        T.big_button(self.confirm_frame, "ביטול", self._hide_confirm, size=10, pady=4).pack(side="right", padx=4)
        self.confirm_frame.pack(side="left")

    def _hide_confirm(self):
        self.confirm_frame.pack_forget()

    def _do_delete(self):
        ok, files = incident_store.delete_incident(self.username, self.selected_id,
                                                   delete_media=self.delete_media_var.get())
        self._hide_confirm()
        self.selected_id = None
        self.timeline.configure(state="normal")
        self.timeline.delete("1.0", "end")
        self.timeline.configure(state="disabled")
        T.flash_feedback(self.status, f"✅ האירוע נמחק ({files} קבצי מדיה נמחקו)" if ok else "❌ המחיקה נכשלה",
                         T.GREEN if ok else T.RED)
        self._refresh()

    def _save_retention(self):
        try:
            incident_store.set_retention_days(self.username, int(self.retention_var.get()))
            removed = incident_store.purge_expired(self.username)
            T.flash_feedback(self.status, f"✅ מדיניות שמירה נשמרה ({removed} אירועים ישנים נמחקו)")
            self._refresh()
        except (ValueError, TypeError):
            T.flash_feedback(self.status, "❌ ערך לא תקין (0-3650)", T.RED)
