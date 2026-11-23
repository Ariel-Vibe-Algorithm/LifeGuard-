# -*- coding: utf-8 -*-
"""
alert_window.py
חלון התראת חירום נכנסת (אצל איש קשר מהימן או מתנדב): פרטי האירוע, מיקום חי, סטטוס ותגובות,
וכפתורים: קבלת האירוע, "בדרך", "הגעתי", "לא זמין/ה", הודעה למשתמש, ניווט.
כל המידע מגיע מהשרת ומתעדכן בזמן אמת; מתנדב רואה מיקום מדויק רק אחרי שקיבל את האירוע.
"""

import time
import tkinter as tk
import webbrowser

import ui_theme as T
from app_screens import EMERGENCY_TYPE_LABELS, SEVERITY_LABELS, run_bg
from gauge_widget import RiskGauge

REFRESH_MS = 3000
RISK_LEVEL_LABELS = {"HIGH": "גבוהה", "MEDIUM": "בינונית", "LOW": "נמוכה", "UNKNOWN": "לא ידועה"}


def _beep():
    try:
        import winsound
        for _ in range(3):
            winsound.MessageBeep(winsound.MB_ICONHAND)
    except Exception:
        pass


class AlertWindow(tk.Toplevel):
    def __init__(self, master, app, incident_id, alert_sound=True):
        super().__init__(master)
        self.app, self.iid = app, incident_id
        self.view = None
        self.title("LifeGuard | התראת חירום")
        self.geometry("620x760")
        self.minsize(520, 600)
        self.configure(bg=T.BG)
        self.attributes("-topmost", True)
        self.bind("<Escape>", lambda e: self.destroy())

        head = tk.Frame(self, bg=T.RED)
        head.pack(fill="x")
        self.title_label = tk.Label(head, text="🚨 התראת חירום", font=T.F(20, "bold"), fg="#FFFFFF", bg=T.SOS_FILL)
        self.title_label.pack(pady=(14, 2))
        self.sub_label = tk.Label(head, text="טוען פרטים מהשרת...", font=T.F(12), fg="#FFFFFF", bg=T.SOS_FILL,
                                  wraplength=560, justify="center")
        self.sub_label.pack(pady=(0, 12))

        outer, body = T.scrollable(self)
        outer.pack(fill="both", expand=True, padx=14, pady=10)
        outer.bind_all_keys(self)

        # מד רמת הסכנה - מוצג רק למגיבים (השרת לא שולח אותו לאדם שבמצוקה)
        self.risk_card, rb = T.card(body)
        rrow = tk.Frame(rb, bg=T.CARD_BG)
        rrow.pack(fill="x")
        self.risk_gauge = RiskGauge(rrow, width=200, height=150, bg=T.CARD_BG)
        self.risk_gauge.pack(side="left")
        self.risk_text = T.label(rrow, "", 12, T.TEXT, anchor="e", wraplength=300)
        self.risk_text.pack(side="right", fill="x", expand=True, padx=(8, 0))

        c, self.info = T.card(body)
        c.pack(fill="x", pady=(0, 10))
        self.silent_label = tk.Label(self.info, text="", font=T.F(12, "bold"), fg=T.RED, bg=T.RED_BG,
                                     wraplength=520, justify="right")
        self.details = T.label(self.info, "", 12, T.TEXT, anchor="e", wraplength=520)
        self.details.pack(fill="x")
        self.loc_label = T.label(self.info, "", 12, T.TEXT_SECONDARY, anchor="e", wraplength=520)
        self.loc_label.pack(fill="x", pady=(6, 0))
        self.profile_label = T.label(self.info, "", 11, T.TEXT_SECONDARY, anchor="e", wraplength=520)
        self.profile_label.pack(fill="x", pady=(6, 0))
        self.responders_label = T.label(self.info, "", 11, T.GREEN, anchor="e", wraplength=520)
        self.responders_label.pack(fill="x", pady=(6, 0))

        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 10))
        T.label(b, "התגובה שלך", 13, T.TEXT, bold=True, anchor="e").pack(fill="x")
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=(6, 0))
        self.accept_btn = T.big_button(row, "✋ אני מטפל/ת", lambda: self.respond("accept"), bg=T.GREEN, size=13)
        self.accept_btn.pack(side="right", padx=3)
        self.decline_btn = T.big_button(row, "לא זמין/ה", lambda: self.respond("decline"), size=11)
        self.decline_btn.pack(side="right", padx=3)
        self.progress_row = tk.Frame(b, bg=T.CARD_BG)
        T.big_button(self.progress_row, "🚶 בדרך", lambda: self.respond("on_my_way"), bg=T.BLUE, size=11).pack(side="right", padx=3)
        T.big_button(self.progress_row, "📍 הגעתי", lambda: self.respond("arrived"), bg=T.BLUE, size=11).pack(side="right", padx=3)
        self.nav_row = tk.Frame(b, bg=T.CARD_BG)
        self.nav_row.pack(fill="x", pady=(8, 0))
        self.nav_btn = T.big_button(self.nav_row, "🧭 נווט למיקום", self.navigate, bg=T.CARD_BG_ALT, size=11)
        self.nav_btn.pack(side="right", padx=3)
        T.big_button(self.nav_row, "🗺️ הצג במפה", self.show_on_map, size=11).pack(side="right", padx=3)
        # מתנדב בלבד: דיווח על התראת שווא/ניצול (מגביל יתרונות עדיפות לחשבון חוזר - לא חוסם חירום)
        self.report_btn = T.big_button(self.nav_row, "⚠️ התראת שווא", lambda: self.report("false_alarm"), size=10)
        self.action_status = T.label(b, "", 11, T.GREEN, anchor="e", wraplength=520)
        self.action_status.pack(fill="x", pady=(6, 0))

        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 10))
        from chat_panel import ChatPanel
        self.chat = ChatPanel(b, app, lambda: self.iid, getattr(app, "username", None),
                              on_error=lambda t: T.flash_feedback(self.action_status, "❌ " + t, T.RED, 8000))
        self.chat.pack(fill="x")

        self._ringing = False
        self._ring_started = 0.0
        if alert_sound:
            self.start_ringing()
        self.refresh()

    # ---------- נתונים ----------
    def refresh(self):
        if not self.winfo_exists() or self.app.client is None:
            return
        run_bg(self.app, lambda: self.app.client.get_incident(self.iid), self._apply, self._gone)

    def _schedule(self):
        try:
            self.after(REFRESH_MS, self.refresh)
        except tk.TclError:
            pass

    def _gone(self, exc):
        if not self.winfo_exists():
            return
        self.title_label.configure(text="האירוע הסתיים או שהגישה אליו הוסרה")
        self.sub_label.configure(text=getattr(exc, "message", str(exc)))
        for w in (self.accept_btn, self.decline_btn):
            w.configure(state="disabled")

    def _apply(self, v):
        if not self.winfo_exists():
            return
        self.view = v
        if v.get("my_role") == "helper":
            return self._apply_helper(v)
        etype = EMERGENCY_TYPE_LABELS.get(v["emergency_type"], v["emergency_type"])
        role = "איש קשר מהימן" if v["my_role"] == "trusted" else "מתנדב/ת"
        closed = v["status"] != "active"
        self.title_label.configure(text=("✅ האירוע הסתיים" if closed else f"🚨 {v['owner_display']} זקוק/ה לעזרה"))
        self.sub_label.configure(text=f"סוג: {etype} · חומרה: {SEVERITY_LABELS.get(v['severity'], '')} · "
                                      f"את/ה: {role} · נפתח {time.strftime('%H:%M', time.localtime(v['created_at']))}")
        if v["mode"] == "silent":
            self.silent_label.configure(text="🤫 מצוקה שקטה: ייתכן שהמשתמש/ת אינו/ה יכול/ה לדבר.\n"
                                             "אל תתקשר/י בקול רם ואל תחשוף/י שקיבלת התראה. פעל/י בזהירות.")
            self.silent_label.pack(fill="x", pady=(0, 8), before=self.details)
        self._apply_risk(v.get("risk"), closed)
        dist = f"מרחק: {v['distance_km']:.1f} ק\"מ" if v.get("distance_km") else ""
        if v.get("eta_minutes"):
            dist += (" · " if dist else "") + f"~{v['eta_minutes']} דק' הליכה" + \
                    (" (לפי מסלול)" if v.get("eta_source") == "osrm" else " (הערכה)")
        if v["my_role"] == "volunteer" and not closed:
            self.report_btn.pack(side="right", padx=3)
        self.details.configure(text=dist or "")
        loc = v.get("location")
        if loc:
            age = int(time.time() - loc.get("received_at", time.time()))
            if loc.get("approximate"):
                self.loc_label.configure(text=f"📍 אזור משוער (~500 מ'). המיקום המדויק יוצג אחרי שתקבל/י את האירוע.",
                                         fg=T.YELLOW)
            else:
                src = str(loc.get("source") or "")
                kind = ("הוזן ידנית ע\"י האדם" if src.startswith("manual") else
                        "GPS" if loc.get("is_precise") else "משוער (IP)")
                self.loc_label.configure(text=f"📍 {loc['lat']:.5f}, {loc['lon']:.5f} · {kind} · עודכן לפני {age} שנ'",
                                         fg=T.TEXT if loc.get("is_precise") else T.YELLOW)
        else:
            self.loc_label.configure(text="📍 אין מיקום מהמשתמש/ת", fg=T.RED)
        if v.get("signal_lost_since") and loc:
            # לעולם לא מציגים מיקום ישן כאילו הוא עדכני
            self.loc_label.configure(text=self.loc_label.cget("text") + "\n📵 המיקום לא מתעדכן - ייתכן שאין קליטה. "
                                     "זה המיקום האחרון הידוע.", fg=T.RED)
        prof = v.get("profile") or {}
        lines = []
        # מידע רפואי מובנה מהשרת: מוחזר רק למי שיש לו גישה מלאה לאירוע פעיל, ורק אם הבעלים בחר/ה לשתף
        med = v.get("medical") or {}
        labels = {"blood_type": "סוג דם", "allergies": "אלרגיות", "medications": "תרופות", "conditions": "מצבים רפואיים",
                  "emergency_medical_contact": "איש קשר רפואי", "notes": "הערות"}
        for k, lbl in labels.items():
            if med.get(k):
                lines.append(f"💊 {lbl}: {med[k]}")
        if prof.get("medical_notes"):
            lines.append("💊 מידע רפואי: " + prof["medical_notes"])
        self.profile_label.configure(text="\n".join(lines), fg=T.RED if med.get("allergies") else T.TEXT_SECONDARY)
        resp = v.get("responders") or []
        self.responders_label.configure(text=("מטפלים: " + ", ".join(r["display_name"] + (" ✓מאומת/ת" if r.get("verified") else "")
                                                                   for r in resp)) if resp else
                                        "עדיין אף אחד לא קיבל את האירוע")
        accepted = v.get("my_state") == "accepted"
        self.accept_btn.configure(state="disabled" if accepted or closed else "normal",
                                  text="✅ קיבלת את האירוע" if accepted else "✋ אני מטפל/ת")
        self.decline_btn.configure(state="disabled" if closed else "normal")
        if accepted and not closed:
            self.progress_row.pack(fill="x", pady=(6, 0), before=self.nav_row)
        self.nav_btn.configure(state="normal" if (v.get("full_access") and loc) else "disabled")
        self.chat.render(v)
        if v.get("my_state") in ("accepted", "declined") or closed:
            self.stop_ringing()
        if not closed:
            self._schedule()

    # ---------- פעולות ----------
    def _apply_risk(self, risk, closed):
        if not risk or closed:
            self.risk_card.pack_forget()
            return
        if not self.risk_card.winfo_ismapped():
            self.risk_card.pack(fill="x", pady=(0, 10), before=self.info.master)
        level = risk.get("level") or "UNKNOWN"
        score = risk.get("score")
        self.risk_gauge.max_score = risk.get("scale") or self.risk_gauge.max_score
        # בלי ניקוד מספרי (למשל כפתור פאניקה) - המחוג לפי הרמה בלבד
        shown = score if score is not None else {"HIGH": 7, "MEDIUM": 4, "LOW": 1}.get(level, 0)
        self.risk_gauge.set_value(shown, level, extra_text="" if score is not None else "ללא ניקוד מספרי")
        lines = [f"עדיפות בתור: {risk.get('band_label', '')}", f"רמת סכנה: {RISK_LEVEL_LABELS.get(level, level)}"]
        conf = risk.get("confidence")
        # ביטחון בזיהוי - ציר נפרד: "כמה מסוכן אם זה אמיתי" מול "כמה סביר שזה אמיתי"
        lines.append(f"ביטחון בזיהוי: {conf:.0%}" if isinstance(conf, (int, float)) else "ביטחון בזיהוי: לא ידוע")
        ctx = risk.get("context") or {}
        tags = [{"night": "🌙 לילה", "twilight": "🌆 דמדומים"}.get(ctx.get("phase")),
                {"dark": "רחוב חשוך (אין עמודי תאורה)", "lit": "רחוב מואר"}.get(ctx.get("lighting"))]
        if any(tags):
            lines.append(" · ".join(t for t in tags if t))
        for r in (risk.get("reasons") or [])[:4]:
            lines.append("• " + r)
        if "reasons" not in risk:
            lines.append("פירוט הסיבות יוצג אחרי קבלת האירוע")
        self.risk_text.configure(text="\n".join(lines),
                                 fg=T.RED if level == "HIGH" else (T.YELLOW if level == "MEDIUM" else T.TEXT))

    def report(self, kind):
        def done(_):
            if self.winfo_exists():
                self.action_status.configure(text="הדיווח נשמר. תודה - זה עוזר להגן על המתנדבים.", fg=T.TEXT_SECONDARY)
                self.report_btn.configure(state="disabled")
        run_bg(self.app, lambda: self.app.client.report_incident(self.iid, kind), done,
               lambda e: self.winfo_exists() and self.action_status.configure(text=f"הדיווח נכשל: {e}", fg=T.RED))

    # ---------- מתנדב/ת שלא בשטח: שיחת תמיכה מרחוק / הזעקת רשויות (server/helpers.py) ----------
    def _apply_helper(self, v):
        closed = v["status"] != "active"
        liaison = v.get("helper_kind") == "authority_liaison"
        accepted = v.get("my_state") == "accepted"
        self.title_label.configure(text="✅ הבקשה הסתיימה" if closed else
                                   ("👮 בקשה להזעיק רשויות" if liaison else "💬 מישהו צריך לדבר"))
        self.sub_label.configure(text=f"את/ה: {v.get('helper_kind_label') or ''} · "
                                      + ("לא חירום - שיחה בלבד" if v.get("support_request") else "בתוך אירוע חירום")
                                      + f" · נפתח {time.strftime('%H:%M', time.localtime(v['created_at']))}")
        self.risk_card.pack_forget()
        self.details.configure(text="\n".join("• " + g for g in v.get("guidance") or []))
        loc = v.get("location")
        if liaison and accepted and loc:
            stale = "\n⚠️ המיקום לא התעדכן מעל 5 דקות - זה המיקום האחרון הידוע, לא מיקום חי" if loc.get("stale") else ""
            self.loc_label.configure(text=f"📍 {loc['lat']:.5f}, {loc['lon']:.5f} - למסירה לרשויות{stale}",
                                     fg=T.RED if loc.get("stale") else T.TEXT)
        elif liaison:
            self.loc_label.configure(text="📍 השם והמיקום יוצגו רק אחרי שתקבל/י את הבקשה", fg=T.TEXT_SECONDARY)
        else:
            self.loc_label.configure(text="🔒 לשמירה על הפרטיות לא מוצגים שם ומיקום של הפונה", fg=T.TEXT_SECONDARY)
        if liaison and accepted:
            self.profile_label.configure(text=f"שם: {v.get('owner_display', '')}", fg=T.TEXT)
        else:
            hot = [h for h in v.get("hotlines") or [] if h.get("number")]
            self.profile_label.configure(text=("קווי סיוע שאפשר להפנות אליהם: " + " · ".join(
                f"{h['name']} {h['number']}" for h in hot)) if hot and not liaison else "", fg=T.TEXT_SECONDARY)
        self.responders_label.configure(text="")
        self.accept_btn.configure(state="disabled" if accepted or closed else "normal",
                                  text="✅ הבקשה אצלך" if accepted else "✋ אני לוקח/ת את הבקשה")
        self.decline_btn.configure(state="disabled" if closed else "normal")
        self.nav_row.pack_forget()
        self.progress_row.pack_forget()
        if accepted and not closed:
            if not hasattr(self, "helper_row"):
                self.helper_row = tk.Frame(self.progress_row.master, bg=T.CARD_BG)
                for action, label in ((v.get("liaison_actions") or {}) if liaison else {}).items():
                    T.big_button(self.helper_row, label, lambda a=action: self.respond(a), size=10,
                                 bg=T.BLUE if action.startswith("called") else T.CARD_BG_ALT).pack(fill="x", pady=1)
                T.big_button(self.helper_row, "סיימתי", lambda: self.respond("done"), size=10).pack(fill="x", pady=1)
            if not self.helper_row.winfo_ismapped():
                self.helper_row.pack(fill="x", pady=(6, 0), before=self.action_status)
        elif hasattr(self, "helper_row"):
            self.helper_row.pack_forget()
        self.chat.render(v)
        if accepted or closed:
            self.stop_ringing()
        if not closed:
            self._schedule()

    HELPER_ACTIONS = {"called_police": "עודכן: הזעקת משטרה", "called_mda": "עודכן: הזעקת מד\"א",
                      "called_fire": "עודכן: הזעקת כבאות", "reported_other": "עודכן: דיווחת לגורם רשמי",
                      "could_not_reach": "עודכן: עוד לא הצלחת ליצור קשר", "done": "תודה! הבקשה נסגרה מצדך"}

    def respond(self, action):
        labels = {"accept": "קיבלת את האירוע - המשתמש/ת רואה זאת", "decline": "סומנת כלא זמין/ה",
                  "on_my_way": "עודכן: בדרך", "arrived": "עודכן: הגעת", **self.HELPER_ACTIONS}
        helper = (self.view or {}).get("my_role") == "helper"

        self.stop_ringing()

        def done(res):
            T.flash_feedback(self.action_status, "✅ " + labels[action])
            if action in ("decline", "done"):
                self.after(1500, self.destroy)
            else:
                self.refresh()
        call = (lambda: self.app.client.helper_respond(self.iid, action)) if helper else \
            (lambda: self.app.client.respond(self.iid, action))
        run_bg(self.app, call, done,
               lambda e: T.flash_feedback(self.action_status, f"❌ {getattr(e, 'message', e)}", T.RED, 8000))

    # ---------- צלצול (איש הקשר שמצלצלים אליו עכשיו) ----------
    RING_INTERVAL_MS = 2500
    RING_MAX_SECONDS = 180

    def start_ringing(self):
        """צפצוף חוזר עד מענה, עד שהשרת מעביר לאיש הקשר הבא (ring_stopped), או עד 3 דקות."""
        if self._ringing:
            return
        self._ringing, self._ring_started = True, time.time()
        self.title_label.master.configure(bg=T.RED)
        self._ring_tick()

    def _ring_tick(self):
        if not self._ringing or not self.winfo_exists():
            return
        if time.time() - self._ring_started > self.RING_MAX_SECONDS:
            self.stop_ringing()
            return
        _beep()
        self.lift()
        try:
            self.after(self.RING_INTERVAL_MS, self._ring_tick)
        except tk.TclError:
            pass

    def stop_ringing(self, note=None):
        was = self._ringing
        self._ringing = False
        if was and note and self.winfo_exists():
            self.action_status.configure(text="🔕 " + note, fg=T.TEXT_SECONDARY)

    def navigate(self):
        loc = (self.view or {}).get("location")
        if loc and not loc.get("approximate"):
            webbrowser.open(f"https://www.google.com/maps/dir/?api=1&destination={loc['lat']:.6f},{loc['lon']:.6f}")

    def show_on_map(self):
        loc = (self.view or {}).get("location")
        if not loc:
            return
        name = f"{(self.view or {}).get('owner_display', '')} (אירוע)"
        self.app.show_screen("map")
        self.app.screens["map"].set_destination(loc["lat"], loc["lon"], name + (" - משוער" if loc.get("approximate") else ""))
