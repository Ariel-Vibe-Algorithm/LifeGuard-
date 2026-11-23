# -*- coding: utf-8 -*-
"""
privacy_ui.py - "היסטוריית פרטיות והרשאות": מי קיבל את המיקום שלי ומתי, הקלטות, הרשאות פעילות, הורה-ילד.

כל הנתונים מהשרת (יומן ביקורת append-only, server/privacy.py). המסך לא "ממציא" צפייה: גישה למיקום מוצגת
כ"המיקום נמסר לאפליקציה של X" (השרת יודע מה שלח, לא מה מישהו ראה). זמן שרת וזמן מכשיר מוצגים בנפרד.
"""

import json
import time
import tkinter as tk
from tkinter import filedialog

import ui_theme as T
from app_screens import run_bg

ACTION_HE = {
    "location_access": "📍 גישה למיקום", "emergency_activated": "🚨 הפעלת חירום", "emergency_cancelled": "↩️ ביטול (שווא)",
    "emergency_resolved": "✅ סיום אירוע", "emergency_confirmed": "☑️ אישור חירום",
    "permission_location_mode": "🔧 שינוי הרשאת מיקום", "permission_recording_access": "🔧 הרשאת הקלטות",
    "permission_sharing_paused": "⏸️ השהיית שיתוף", "permission_sharing_resumed": "▶️ חידוש שיתוף",
    "permission_recording": "🎙️ הגדרת הקלטה בחירום", "permission_discreet_recording": "🎙️ הסכמה להקלטה דיסקרטית",
    "recording_uploaded": "⬆️ הקלטה הועלתה", "recording_recipient_notified": "📨 נמען הקלטה עודכן",
    "recording_access": "▶️ גישה להקלטה", "recording_deleted": "🗑️ הקלטה נמחקה",
    "recording_upload_failed": "⚠️ העלאת הקלטה נכשלה", "recording_stored_offline": "💾 הקלטה נשמרה במכשיר",
    "recording_started": "⏺️ הקלטה התחילה", "recording_stopped": "⏹️ הקלטה הסתיימה",
    "sos_activated_offline": "🚨 SOS בלי חיבור", "false_alarm_cancelled": "↩️ ביטול התראת שווא",
    "guardian_link_requested": "👪 בקשת קישור הורה-ילד", "guardian_link_approved": "👪 אישור קישור",
    "guardian_link_revoked": "👪 ביטול קישור", "guardian_verified": "👪 הורה אומת",
    "privacy_report_exported": "📤 דוח פרטיות יוצא",
    # מתנדבים שלא בשטח (server/helpers.py)
    "helper_requested": "🙋 בקשה למתנדב/ת", "helper_accepted": "🤝 מתנדב/ת הצטרף/ה",
    "helper_released": "🚪 מתנדב/ת הוסר/ה", "support_requested": "💬 בקשת שיחה (לא חירום)",
    "authority_contacted": "👮 הזעקת רשויות", "authority_contact_failed": "⚠️ הזעקה - לא הצליח/ה ליצור קשר"}
HELPER_KIND_HE = {"remote_support": "שיחת תמיכה מרחוק", "authority_liaison": "הזעקת רשויות"}
LIAISON_HE = {"called_police": "משטרה (100)", "called_mda": "מד\"א (101)", "called_fire": "כבאות (102)",
              "reported_other": "גורם רשמי אחר", "could_not_reach": "עוד לא הצליח/ה ליצור קשר"}
RESULT_HE = {"completed": ("✓ הושלם", "GREEN"), "failed": ("✗ נכשל / נחסם", "RED"), "pending": ("⏳ ממתין", "YELLOW")}
VIA_HE = {"always": "שיתוף רציף", "emergency": "אירוע חירום", "escort": "ליווי", "sms_link": "קישור SMS",
          "volunteer_assigned": "מתנדב משובץ", "volunteer_nearby_approx": "מתנדב בקרבת מקום (אזור משוער בלבד)",
          "authority_liaison": "מתנדב/ת הזעקת רשויות (מאומת/ת)"}
MODES = (("always", "תמיד (מיקום חי)"), ("emergency", "רק בחירום"), ("off", "כבוי"))
TYPES = (("", "הכל"), ("location", "מיקום"), ("recording", "הקלטות"), ("permission", "הרשאות"),
         ("emergency", "חירום"), ("guardian", "הורה-ילד"))
CHILD_CONSENT = ("ההורה שלך יוכל לקבל הקלטה קולית רק אם תפעיל/י SOS או שתזוהה סכנה ממשית. אף אחד לא יכול "
                 "להאזין לך בזמן אמת או להדליק את המיקרופון מרחוק. הטלפון יראה שהמיקרופון פועל. אפשר לבטל בכל רגע.")


def _w(text, n=95):
    """שבירת שורות ידנית - Tk מסדר הפוך שורת RTL ארוכה / שבורה אוטומטית (ראו first_aid_ui.wrap_text)."""
    from first_aid_ui import wrap_text
    return wrap_text(text, 10 ** 6, len, max_chars=n)


def fmt_ts(ts):
    return time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ts)) if ts else "—"


PERM_HE = {"always": "תמיד", "emergency": "רק בחירום", "off": "כבוי", "assigned_incident": "שיבוץ לאירוע",
           "nearby_alert": "התראה בקרבת מקום (משוער)", "sms_fallback": "קישור SMS", "none": "אין", None: "—"}
REASON_HE = {"owner_request": "לבקשתך", "retention_expired": "תום תקופת השמירה (30 יום)"}


def _yn(v):
    return "כן" if v in (True, "True", "true", 1) else "לא"


def describe(e):
    """שורת תיאור לאירוע ביקורת - בלי להגזים במה שהשרת יודע."""
    a, d = e["action"], e.get("details") or {}
    who = e.get("actor_display") or e.get("actor") or ""
    if a == "location_access":
        via = VIA_HE.get(e.get("via"), e.get("via") or "")
        if e["result"] == "failed":
            return f"{who} ביקש/ה את המיקום ({via}) - נחסם: אין הרשאה"
        per = e.get("access_period")
        span = (f" · {fmt_ts(per['from'])} עד {fmt_ts(per['to'])} ({per['requests']} עדכונים)" if per else "")
        return f"המיקום נמסר לאפליקציה של {who} · דרך: {via} · הרשאה: {PERM_HE.get(e.get('permission'), e.get('permission'))}{span}"
    if a == "permission_location_mode":
        return f"{e.get('recipient_display') or e.get('recipient')}: {PERM_HE.get(d.get('from'))} ← {PERM_HE.get(d.get('to'))}"
    if a == "permission_recording_access":
        return f"{e.get('recipient_display') or e.get('recipient')}: גישה להקלטות - {_yn(d.get('to'))}"
    if a == "permission_recording":
        return f"העלאת הקלטות בחירום: {_yn(d.get('record_on_emergency'))}"
    if a.startswith("guardian_link"):
        return (f"הורה: {d.get('guardian', '')} · ילד/ה: {d.get('child', '')}"
                + (f" · גיל {d['child_age']}" if d.get("child_age") is not None else ""))
    if a == "permission_discreet_recording":
        return f"{'הורה' if d.get('party') == 'guardian' else 'ילד/ה'}: הסכמה להקלטה דיסקרטית - {_yn(d.get('consent'))}"
    if a == "recording_recipient_notified":
        return f"{e.get('recipient_display')}: {e.get('delivery', 'ממתין לאישור קבלה')}"
    if a == "recording_access":
        return f"ההקלטה נמסרה לאפליקציה של {who}" if e["result"] == "completed" else f"{who} - נחסם: אין הרשאה"
    if a == "recording_uploaded":
        return f"קטע {d.get('bytes', '?')} בתים{' · דיסקרטי (פרופיל ילד)' if d.get('discreet') else ''}"
    if a == "recording_deleted":
        return f"סיבה: {REASON_HE.get(d.get('reason'), d.get('reason', ''))}"
    if a == "emergency_activated":
        trig = {"manual": "ידני", "sensor": "חיישן", "acoustic": "קול", "wellness_no_response": "אין תשובה לבדיקה",
                "server_timer": "טיימר ליווי/נסיעה", "auto": "זיהוי אוטומטי"}.get(d.get("trigger"), d.get("trigger"))
        act = {"manual": "הופעל במלואו", "confirmed_high": "ביטחון גבוה - הופעל במלואו",
               "unconfirmed": "לא מאושר - אנשי קשר בלבד, בלי מתנדבים", "server_timer": "הופעל במלואו"}.get(
            d.get("activation"), d.get("activation"))
        return f"הפעלה: {trig} · {act}"
    if a in ("helper_requested", "helper_accepted"):
        kind = HELPER_KIND_HE.get(d.get("kind"), d.get("kind", ""))
        return (f"{kind}" + (" · נשלח אוטומטית (אף אחד לא קיבל 90 שנ')" if d.get("auto") else "")
                if a == "helper_requested" else f"{who} · {kind}")
    if a in ("authority_contacted", "authority_contact_failed"):
        return f"{who}: {LIAISON_HE.get(d.get('action'), d.get('action', ''))}"
    if a == "helper_released":
        return f"הוסר/ה: {e.get('recipient_display') or e.get('recipient') or ''}"
    if a == "support_requested":
        return "המתנדב/ת לא רואה שם ומיקום"
    return ", ".join(f"{k}: {v}" for k, v in d.items() if k != "note")


class PrivacyWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title("LifeGuard | היסטוריית פרטיות והרשאות")
        self.geometry("980x760")
        self.configure(bg=T.BG)
        self.bind("<Escape>", lambda e: self.destroy())
        top = tk.Frame(self, bg=T.BG)
        top.pack(fill="x", padx=14, pady=(12, 4))
        T.label(top, "🔐 פרטיות: מי קיבל מה, ומתי", 17, T.TEXT, bold=True).pack(side="right")
        self.status = T.label(top, "", 10, T.TEXT_SECONDARY)
        self.status.pack(side="left")
        tabs = tk.Frame(self, bg=T.BG)
        tabs.pack(fill="x", padx=14)
        self.pages = {}
        for key, label in (("history", "היסטוריה"), ("perms", "הרשאות פעילות"), ("guardian", "הורה-ילד")):
            T.big_button(tabs, label, lambda k=key: self.show(k), bg=T.CARD_BG_ALT, size=11, pady=4).pack(
                side="right", padx=3)
        self.body = tk.Frame(self, bg=T.BG)
        self.body.pack(fill="both", expand=True, padx=14, pady=8)
        self.events = []
        self.show("history")

    def client(self):
        c = self.app.client
        if c is None or not c.token:
            self.status.configure(text="לא מחובר לשרת - אין גישה ליומן", fg=T.RED)
            return None
        return c

    def show(self, key):
        for w in self.body.winfo_children():
            w.destroy()
        self.current = key
        {"history": self._history, "perms": self._perms, "guardian": self._guardian}[key]()

    # ------------------------------------------------------------ היסטוריה
    def _history(self):
        f = tk.Frame(self.body, bg=T.BG)
        f.pack(fill="x")
        self.f_type, self.f_contact, self.f_q = tk.StringVar(value=""), tk.StringVar(), tk.StringVar()
        self.f_from, self.f_to = tk.StringVar(), tk.StringVar()
        T.label(f, "סוג:", 10, T.TEXT_SECONDARY).pack(side="right")
        tk.OptionMenu(f, self.f_type, *[k for k, _ in TYPES]).pack(side="right", padx=2)
        for label, var, w in (("איש קשר:", self.f_contact, 12), ("מתאריך (YYYY-MM-DD):", self.f_from, 11),
                              ("עד:", self.f_to, 11), ("חיפוש:", self.f_q, 14)):
            T.label(f, label, 10, T.TEXT_SECONDARY).pack(side="right", padx=(6, 0))
            tk.Entry(f, textvariable=var, width=w, font=T.F(10), justify="right").pack(side="right")
        T.big_button(f, "סנן", self.load_history, size=10, pady=2).pack(side="right", padx=6)
        T.big_button(f, "📤 ייצוא דוח חתום", self.export, bg=T.CARD_BG_ALT, size=10, pady=2).pack(side="left")
        T.label(self.body, _w("\"נמסר לאפליקציה\" = השרת שלח את המידע למכשיר של הצד השני. אין לנו דרך לדעת אם "
                              "מישהו הסתכל במסך. זמן שרת = מאומת; זמן מכשיר = שעון הטלפון/המחשב.", 85), 9, T.TEXT_MUTED,
                anchor="e", justify="right").pack(fill="x", pady=(4, 2))
        outer, self.list = T.scrollable(self.body, bg=T.BG)
        outer.pack(fill="both", expand=True)
        self.load_history()

    @staticmethod
    def _date(s, end=False):
        try:
            t = time.mktime(time.strptime(s.strip(), "%Y-%m-%d"))
            return t + 86399 if end else t
        except (ValueError, AttributeError):
            return None

    def load_history(self):
        c = self.client()
        if not c:
            return
        kind = self.f_type.get()
        flt = {"type": {"location": "location", "recording": "recording", "permission": "permission",
                        "emergency": "emergency", "guardian": "guardian"}.get(kind),
               "contact": self.f_contact.get().strip().lower(), "q": self.f_q.get().strip(),
               "from": self._date(self.f_from.get()), "to": self._date(self.f_to.get(), True)}
        self.status.configure(text="טוען...", fg=T.TEXT_SECONDARY)
        run_bg(self.app, lambda: c.privacy_history(**flt), self._render_history,
               lambda e: self.status.configure(text=f"❌ {getattr(e, 'message', e)}", fg=T.RED))

    def _render_history(self, res):
        if not self.winfo_exists() or self.current != "history":
            return
        self.events = res.get("events", [])
        self.status.configure(text=f"{len(self.events)} רשומות", fg=T.TEXT_SECONDARY)
        for w in self.list.winfo_children():
            w.destroy()
        if not self.events:
            T.label(self.list, "אין רשומות בסינון הזה.", 11, T.TEXT_MUTED, anchor="e").pack(fill="x", pady=8)
        for e in self.events:
            res_key = e.get("result_effective") or e["result"]
            label, color = RESULT_HE.get(res_key, (res_key, "TEXT"))
            c, b = T.card(self.list)
            c.pack(fill="x", pady=2)
            head = tk.Frame(b, bg=T.CARD_BG)
            head.pack(fill="x")
            T.label(head, ACTION_HE.get(e["action"], e["action"]), 11, T.TEXT, bold=True).pack(side="right")
            T.label(head, label, 10, getattr(T, color)).pack(side="right", padx=8)
            T.label(head, f"שרת: {fmt_ts(e['server_ts'])}" + (f" · מכשיר: {fmt_ts(e['device_ts'])}"
                                                             if e.get("device_ts") else ""), 9, T.TEXT_MUTED).pack(side="left")
            T.label(b, _w(describe(e), 110), 10, T.TEXT_SECONDARY, anchor="e", justify="right").pack(fill="x")

    def export(self):
        c = self.client()
        if not c:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json",
                                            initialfile=f"lifeguard_privacy_{time.strftime('%Y%m%d')}.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return

        def done(rep):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, ensure_ascii=False, indent=1)
            self.status.configure(text="✅ נשמר דוח חתום (Ed25519) - הקובץ אינו מוצפן", fg=T.GREEN)
        run_bg(self.app, c.privacy_export, done, lambda e: self.status.configure(text=f"❌ {e}", fg=T.RED))

    # ------------------------------------------------------------ הרשאות
    def _perms(self):
        c = self.client()
        if not c:
            return
        self.perm_frame = tk.Frame(self.body, bg=T.BG)
        self.perm_frame.pack(fill="both", expand=True)
        run_bg(self.app, c.privacy_permissions, self._render_perms,
               lambda e: self.status.configure(text=f"❌ {getattr(e, 'message', e)}", fg=T.RED))

    def _render_perms(self, p):
        if not self.winfo_exists() or self.current != "perms":
            return
        for w in self.perm_frame.winfo_children():
            w.destroy()
        c = self.app.client
        f = self.perm_frame
        top, b = T.card(f)
        top.pack(fill="x", pady=4)
        who = ", ".join(p["who_can_see_location_now"]) or "אף אחד (אין שיתוף רציף פעיל)"
        T.label(b, f"👁️ מקבלים עכשיו את המיקום החי שלך: {who}", 12, T.TEXT, bold=True, anchor="e").pack(fill="x")
        live = p.get("my_live_location")
        T.label(b, ("המיקום האחרון ששותף: " + fmt_ts(live["received_at"])) if live else "לא נשמר מיקום חי בשרת",
                10, T.TEXT_SECONDARY, anchor="e").pack(fill="x")
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=4)
        paused = p["sharing_paused"]
        T.big_button(row, "▶️ חידוש שיתוף רציף" if paused else "⏸️ השהיה מיידית של כל השיתוף הרציף",
                     lambda: self._call(lambda: c.pause_sharing(not paused)), bg=T.RED if not paused else T.GREEN,
                     size=11, pady=4).pack(side="right")
        rec = tk.BooleanVar(value=p["record_on_emergency"])
        tk.Checkbutton(b, text="להעלות הקלטות קול מאירוע חירום מאושר (SOS ידני) לאנשי קשר שאישרתי להם הקלטות",
                       variable=rec, font=T.F(11), bg=T.CARD_BG, fg=T.TEXT, selectcolor=T.CARD_BG, anchor="e",
                       command=lambda: self._call(lambda: c.set_record_on_emergency(rec.get()))).pack(fill="x")
        T.label(b, "אין האזנה חיה ואין הפעלה מרחוק של מיקרופון. הקלטה רק בזמן אירוע חירום, ונשמרת 30 יום.", 9,
                T.TEXT_MUTED, anchor="e").pack(fill="x")
        for ct in p["contacts"]:
            cc, cb = T.card(f)
            cc.pack(fill="x", pady=2)
            r1 = tk.Frame(cb, bg=T.CARD_BG)
            r1.pack(fill="x")
            T.label(r1, ct["display_name"], 12, T.TEXT, bold=True).pack(side="right")
            mode = tk.StringVar(value=ct["location_mode"])
            for key, label in MODES:
                tk.Radiobutton(r1, text=label, value=key, variable=mode, font=T.F(10), bg=T.CARD_BG,
                               indicatoron=False, padx=8, selectcolor=T.LIGHT_BLUE,
                               command=lambda u=ct["username"], m=mode: self._call(
                                   lambda: c.set_location_mode(u, m.get()))).pack(side="right", padx=2)
            ra = tk.BooleanVar(value=ct["recording_access"])
            tk.Checkbutton(r1, text="הקלטות", variable=ra, font=T.F(10), bg=T.CARD_BG, selectcolor=T.CARD_BG,
                           command=lambda u=ct["username"], v=ra: self._call(lambda: c.set_recording_access(u, v.get()))
                           ).pack(side="left")
            if ct["effective_now"] == "paused":
                T.label(cb, "⏸️ מושהה כרגע", 9, T.YELLOW, anchor="e").pack(fill="x")
            lc = ct.get("last_change")
            if lc:
                T.label(cb, f"שינוי אחרון: {fmt_ts(lc['server_ts'])}", 9, T.TEXT_MUTED, anchor="e").pack(fill="x")
        if p["volunteers_with_access"]:
            vc, vb = T.card(f)
            vc.pack(fill="x", pady=4)
            T.label(vb, "🦺 מתנדבים עם גישה למיקום (רק באירוע פעיל ששובצו אליו): "
                    + ", ".join(v["display_name"] for v in p["volunteers_with_access"]), 11, T.TEXT,
                    anchor="e").pack(fill="x")

    def _call(self, fn):
        run_bg(self.app, fn, lambda r: self.show(self.current),
               lambda e: self.status.configure(text=f"❌ {getattr(e, 'message', e)}", fg=T.RED))

    # ------------------------------------------------------------ הורה-ילד
    def _guardian(self):
        c = self.client()
        if not c:
            return
        f = self.body
        card, b = T.card(f)
        card.pack(fill="x", pady=4)
        T.label(b, "👪 קישור הורה-ילד (דורש אישור של שני הצדדים)", 12, T.TEXT, bold=True, anchor="e").pack(fill="x")
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=4)
        other, role, age = tk.StringVar(), tk.StringVar(value="guardian"), tk.StringVar()
        T.label(row, "שם משתמש של הצד השני:", 10, T.TEXT_SECONDARY).pack(side="right")
        tk.Entry(row, textvariable=other, width=14, font=T.F(10)).pack(side="right", padx=4)
        for key, label in (("guardian", "אני ההורה"), ("child", "אני הילד/ה")):
            tk.Radiobutton(row, text=label, value=key, variable=role, font=T.F(10), bg=T.CARD_BG).pack(side="right")
        T.label(row, "גיל הילד/ה:", 10, T.TEXT_SECONDARY).pack(side="right", padx=(6, 0))
        tk.Entry(row, textvariable=age, width=4, font=T.F(10)).pack(side="right")
        T.big_button(row, "שלח בקשה", lambda: self._call(lambda: c.guardian_invite(
            other.get().strip().lower(), role.get(), int(age.get()) if age.get().strip().isdigit() else None)),
            size=10, pady=2).pack(side="left")
        T.label(b, _w("הקלטה דיסקרטית בחירום פעילה רק אם: הקישור אושר ע\"י שניכם, כל אחד/ת הסכים/ה בנפרד "
                      "להקלטה, וההורה אומת ע\"י מנהל/ת המערכת. היא לא עוקפת הרשאות של מערכת ההפעלה.", 85), 9, T.TEXT_MUTED,
                anchor="e", justify="right").pack(fill="x")
        self.g_list = tk.Frame(f, bg=T.BG)
        self.g_list.pack(fill="both", expand=True)
        run_bg(self.app, c.guardian_links, self._render_links,
               lambda e: self.status.configure(text=f"❌ {getattr(e, 'message', e)}", fg=T.RED))

    def _render_links(self, res):
        if not self.winfo_exists() or self.current != "guardian":
            return
        c = self.app.client
        for g in res.get("links", []):
            card, b = T.card(self.g_list)
            card.pack(fill="x", pady=3)
            me_guardian = g["my_role"] == "guardian"
            T.label(b, f"{'הילד/ה' if me_guardian else 'ההורה'}: {g['child_display'] if me_guardian else g['guardian_display']}"
                       f" · סטטוס: {'פעיל' if g['status'] == 'active' else 'ממתין לאישור'}"
                       + (f" · גיל {g['child_age']}" if g.get("child_age") is not None else ""), 12, T.TEXT,
                    bold=True, anchor="e").pack(fill="x")
            flags = [("אישור הורה", g["guardian_approved"]), ("אישור ילד/ה", g["child_approved"]),
                     ("הורה מאומת", g["guardian_verified"]), ("הסכמת הורה להקלטה", g["recording_consent"]["guardian"]),
                     ("הסכמת ילד/ה להקלטה", g["recording_consent"]["child"])]
            T.label(b, " · ".join(f"{'✓' if ok else '✗'} {n}" for n, ok in flags), 10, T.TEXT_SECONDARY,
                    anchor="e").pack(fill="x")
            T.label(b, "🎙️ הקלטה דיסקרטית בחירום: " + ("פעילה" if g["discreet_recording_active"] else "לא פעילה"),
                    10, T.GREEN if g["discreet_recording_active"] else T.TEXT_MUTED, anchor="e").pack(fill="x")
            if not me_guardian:
                T.label(b, _w(CHILD_CONSENT), 10, T.TEXT, anchor="e", justify="right", wraplength=860).pack(fill="x")
            row = tk.Frame(b, bg=T.CARD_BG)
            row.pack(fill="x", pady=2)
            mine_ok = g["guardian_approved"] if me_guardian else g["child_approved"]
            if not mine_ok:
                T.primary_button(row, "אישור הקישור", lambda i=g["id"]: self._call(lambda: c.guardian_approve(i)),
                                 size=10, pady=2).pack(side="right", padx=3)
            if g["status"] == "active":
                my_consent = g["recording_consent"]["guardian" if me_guardian else "child"]
                T.big_button(row, "ביטול הסכמה להקלטה" if my_consent else "הסכמה להקלטה דיסקרטית בחירום",
                             lambda i=g["id"], v=not my_consent: self._call(lambda: c.guardian_consent(i, v)),
                             size=10, pady=2).pack(side="right", padx=3)
            T.big_button(row, "ביטול הקישור", lambda i=g["id"]: self._call(lambda: c.guardian_revoke(i)), bg=T.RED,
                         size=10, pady=2).pack(side="left")
        self.show_status_after = None
