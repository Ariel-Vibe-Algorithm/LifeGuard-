# -*- coding: utf-8 -*-
"""
safety_circle_ui.py
מעגל הביטחון: הזמנת אנשי קשר לחירום (שם משתמש / טלפון / קובץ אנשי קשר / קוד הזמנה),
אישור הזמנות, שליחה מחדש, ביטול וניתוק.

השרת הוא מקור האמת: כל מה שמוצג כאן נטען מהשרת אחרי כל פעולה ואחרי כל התראה רלוונטית
(גם ממכשיר אחר), ולכן המצב זהה בכל המכשירים ואחרי התקנה מחדש. אין מצב "מחובר" מקומי.
"""

import time
import tkinter as tk
from tkinter import filedialog

import contacts_import
import ui_theme as T
from app_screens import run_bg, section_header

STATUS = {"pending": ("ממתין לאישור", "warn"), "connected": ("מחובר", "ok"), "declined": ("נדחה", "unavailable"),
          "expired": ("פג תוקף", "not_verified"), "cancelled": ("בוטל", "not_verified"),
          "awaiting_confirmation": ("ממתין לאישורך", "warn")}


def _when(ts):
    return time.strftime("%d/%m %H:%M", time.localtime(ts)) if ts else "—"


def _err(e):
    return getattr(e, "message", None) or str(e)


def invite_message(display_name, code, expires_at):
    return (f"{display_name} מזמין/ה אותך להיות איש/אשת קשר לחירום ב-LifeGuard.\n"
            f"1. מתקינים LifeGuard ונרשמים.\n"
            f"2. מעגל הביטחון ← \"יש לי קוד הזמנה\" ← {code}\n"
            f"הקוד חד-פעמי ובתוקף עד {_when(expires_at)}. אין לשתף אותו עם אחרים.")


class SafetyCircleScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.data = None
        self._contacts = []
        self._confirm_armed = None
        outer, body = T.scrollable(self)
        outer.pack(fill="both", expand=True)
        cols = tk.Frame(body, bg=T.BG)
        cols.pack(fill="both", expand=True, padx=18, pady=12)
        right = tk.Frame(cols, bg=T.BG)
        left = tk.Frame(cols, bg=T.BG)
        right.pack(side="right", fill="both", expand=True, padx=(8, 0))
        left.pack(side="right", fill="both", expand=True, padx=(0, 8))

        bar = tk.Frame(right, bg=T.BG)
        bar.pack(fill="x", pady=(0, 8))
        T.label(bar, "👨‍👩‍👧 מעגל הביטחון", 18, T.TEXT, bg=T.BG, bold=True).pack(side="right")
        T.big_button(bar, "🔄 רענון", self.refresh, size=10, pady=4).pack(side="left")
        self.updated_label = T.label(bar, "", 10, T.TEXT_MUTED, bg=T.BG)
        self.updated_label.pack(side="left", padx=8)
        self.banner = T.label(right, "", 11, T.TEXT, bg=T.BG, anchor="e", wraplength=520)
        self.banner.pack(fill="x", pady=(0, 8))
        self.status = T.label(right, "", 11, T.GREEN, bg=T.BG, anchor="e", wraplength=520)
        self.status.pack(fill="x")

        # --- הזמנות אליי
        c, b = T.card(right)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "📨 הזמנות אליי", "מי שמאשר/ת הזמנה יקבל/תקבל את התראות ה-SOS והמיקום של השולח.")
        self.received_list = tk.Frame(b, bg=T.CARD_BG)
        self.received_list.pack(fill="x")

        # --- קוד הזמנה
        c, b = T.card(right)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "🔑 יש לי קוד הזמנה", "קוד שקיבלת ב-SMS/WhatsApp ממי שמבקש שתהיה/י איש הקשר שלו.")
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x")
        self.code_var = tk.StringVar()
        self._entry(row, self.code_var, self.check_code).pack(side="right", fill="x", expand=True, ipady=5)
        T.primary_button(row, "בדוק קוד", self.check_code, size=11, pady=4).pack(side="right", padx=(0, 6))
        self.code_box = tk.Frame(b, bg=T.CARD_BG)
        self.code_box.pack(fill="x", pady=(6, 0))

        # --- אנשי הקשר שלי
        c, b = T.card(right)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "🛡️ אנשי הקשר שלי לחירום", "מקבלים את ה-SOS שלך ואת המיקום המדויק שלך מיד.")
        self.contacts_list = tk.Frame(b, bg=T.CARD_BG)
        self.contacts_list.pack(fill="x")

        c, b = T.card(right)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "🤝 אני איש קשר עבור", "את/ה מקבל/ת את התראות החירום שלהם.")
        self.for_list = tk.Frame(b, bg=T.CARD_BG)
        self.for_list.pack(fill="x")

        # --- הזמנה חדשה
        c, b = T.card(left)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "➕ הזמנת איש קשר", "הקשר נוצר רק אחרי שהצד השני מאשר. ההזמנה בתוקף 7 ימים.")
        T.label(b, "לפי שם משתמש LifeGuard:", 11, T.TEXT, anchor="e").pack(fill="x", pady=(4, 0))
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=(2, 8))
        self.username_var = tk.StringVar()
        self._entry(row, self.username_var, self.invite_username).pack(side="right", fill="x", expand=True, ipady=5)
        T.primary_button(row, "הזמן", self.invite_username, size=11, pady=4).pack(side="right", padx=(0, 6))

        T.label(b, "לפי מספר טלפון (גם למי שעוד לא התקין):", 11, T.TEXT, anchor="e").pack(fill="x")
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=(2, 2))
        self.phone_var, self.phone_name_var = tk.StringVar(), tk.StringVar()
        self._entry(row, self.phone_var, self.invite_phone, width=16).pack(side="right", ipady=5)
        T.label(row, "שם:", 10, T.TEXT_MUTED).pack(side="right", padx=(8, 2))
        self._entry(row, self.phone_name_var, self.invite_phone, width=12).pack(side="right", ipady=5)
        T.primary_button(row, "הזמן", self.invite_phone, size=11, pady=4).pack(side="right", padx=(6, 0))
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=(4, 2))
        T.big_button(row, "📇 מקובץ אנשי קשר (vcf/csv)", self.import_contacts, size=10, pady=4).pack(side="right")
        if contacts_import.outlook_available():
            T.big_button(row, "📧 מ-Outlook", self.import_outlook, size=10, pady=4).pack(side="right", padx=(6, 0))
        row2 = tk.Frame(b, bg=T.CARD_BG)             # שורה נפרדת: שלושה כפתורים בשורה אחת נחתכו בחלון צר
        row2.pack(fill="x", pady=(4, 2))
        T.big_button(row2, "🔗 צור קוד הזמנה לשיתוף", self.invite_code, size=10, pady=4).pack(side="right")
        self.picker = tk.Frame(b, bg=T.CARD_BG)
        self.picker.pack(fill="x")
        self.share_box = tk.Frame(b, bg=T.CARD_BG)
        self.share_box.pack(fill="x", pady=(6, 0))
        T.label(b, "אנשי קשר מהמחשב: Outlook הקלאסי (כשהוא פתוח), או קובץ ייצוא מהטלפון (Google Contacts / "
                   "iPhone / Outlook החדש). הכל נקרא רק במחשב הזה - רק המספר שבחרת נשלח לשרת, וגם הוא נשמר שם "
                   "כ-hash בלבד. LifeGuard לא שולח SMS בעצמו: את הקוד שולחים מהטלפון שלך.",
                10, T.TEXT_MUTED, anchor="e", wraplength=440).pack(fill="x", pady=(6, 0))

        # --- הזמנות שנשלחו
        c, b = T.card(left)
        c.pack(fill="x", pady=(0, 12))
        section_header(b, "📤 הזמנות ששלחתי")
        self.sent_list = tk.Frame(b, bg=T.CARD_BG)
        self.sent_list.pack(fill="x")

    # ------------------------------------------------------------ helpers
    def _entry(self, parent, var, on_enter, width=None):
        e = tk.Entry(parent, textvariable=var, font=T.F(12), justify="right", bg=T.CARD_BG_ALT, fg=T.TEXT,
                     relief="flat", highlightthickness=2, highlightcolor=T.FOCUS, insertbackground=T.TEXT,
                     **({"width": width} if width else {}))
        e.bind("<Return>", lambda ev: on_enter())
        return e

    def _clear(self, frame):
        for w in frame.winfo_children():
            w.destroy()

    def _say(self, text, ok=True):
        T.flash_feedback(self.status, text, T.GREEN if ok else T.RED, 9000)

    def _client(self):
        c = self.app.client
        if c is None or not c.token:
            self._say("❌ לא מחובר לשרת LifeGuard - מעגל הביטחון לא זמין. פרופיל ← חיבור לשרת.", False)
            return None
        return c

    def _act(self, work, ok_text, after=None):
        """פעולה מול השרת. הצלחה מוצגת רק אחרי שהשרת אישר; אחריה טוענים מחדש את המצב מהשרת."""
        c = self._client()
        if c is None:
            return

        def done(res):
            text = ok_text(res) if callable(ok_text) else ok_text
            if text:
                self._say(text)
            if after:
                after(res)
            self.refresh()

        def fail(e):
            self._say(f"❌ {_err(e)}", False)
            self.refresh()
        run_bg(self.app, lambda: work(c), done, fail)

    def _two_step(self, key, button, text_confirm, action):
        """ניתוק/ביטול דורש לחיצה שנייה (בלי חלונות קופצים - מתאים גם תחת לחץ)."""
        if self._confirm_armed == key:
            self._confirm_armed = None
            action()
            return
        self._confirm_armed = key
        button.configure(text=text_confirm, bg=T.SOS_FILL, fg="#FFFFFF")
        self.after(5000, lambda: self._confirm_armed == key and setattr(self, "_confirm_armed", None))

    # ------------------------------------------------------------ data
    def on_show(self):
        self.refresh()

    def refresh(self):
        c = self.app.client
        if c is None or not c.token:
            self.banner.configure(text="❗ לא מחובר לשרת LifeGuard - לא ניתן להציג או לנהל את מעגל הביטחון.",
                                  fg=T.RED)
            return
        run_bg(self.app, c.circle, self._apply, lambda e: self.banner.configure(text=f"❗ שגיאת שרת: {_err(e)}",
                                                                                 fg=T.RED))

    def _apply(self, d):
        if not self.winfo_exists():
            return
        self.data = d
        self.updated_label.configure(text="עודכן מהשרת " + time.strftime("%H:%M:%S"))
        if d["my_contacts"]:
            self.banner.configure(text=f"✅ SOS שלך יגיע ל-{len(d['my_contacts'])} אנשי קשר מחוברים "
                                       "ולמתנדבים זמינים בקרבתך.", fg=T.GREEN)
        else:
            self.banner.configure(text="⚠️ אין לך עדיין אנשי קשר מחוברים. SOS יישלח רק למתנדבים זמינים בקרבתך "
                                       "(אם יש). הזמן/י בן משפחה או חבר.", fg=T.YELLOW)
        self._render_received(d["received_invitations"])
        self._render_people(self.contacts_list, d["my_contacts"], "my_contact",
                            "אין אנשי קשר מחוברים עדיין.", "נתק")
        self._render_people(self.for_list, d["i_am_contact_for"], "contact_for",
                            "אף אחד לא הגדיר אותך כאיש קשר.", "הפסק")
        self._render_sent(d["sent_invitations"])

    def _render_received(self, items):
        self._clear(self.received_list)
        if not items:
            T.label(self.received_list, "אין הזמנות ממתינות", 11, T.TEXT_MUTED, anchor="e").pack(fill="x")
        for inv in items:
            row = tk.Frame(self.received_list, bg=T.YELLOW_BG)
            row.pack(fill="x", pady=3)
            txt = f"{inv['from_display']} (@{inv['from']}) מבקש/ת שתהיה/י איש קשר לחירום"
            if inv.get("via_phone"):
                txt += " · נשלח למספר הטלפון שלך"
            T.label(row, txt, 11, T.TEXT, T.YELLOW_BG, anchor="e", wraplength=330).pack(side="right", padx=6, pady=4)
            if inv.get("awaiting_confirmation"):
                T.chip(row, "אישרת · ממתין לאישור השולח", "pending").pack(side="left", padx=6)
                continue
            T.big_button(row, "דחה", lambda i=inv: self.respond(i, False), size=10, pady=3).pack(side="left", padx=3)
            T.big_button(row, "אשר", lambda i=inv: self.respond(i, True), bg=T.GREEN, size=10, pady=3).pack(side="left", padx=3)

    def _render_people(self, frame, people, scope, empty, verb):
        self._clear(frame)
        if not people:
            T.label(frame, empty, 11, T.TEXT_MUTED, anchor="e").pack(fill="x")
        for p in people:
            row = tk.Frame(frame, bg=T.CARD_BG)
            row.pack(fill="x", pady=2)
            T.label(row, f"{p['display_name']} (@{p['username']})", 12, T.TEXT).pack(side="right")
            T.chip(row, "מחובר", "ok").pack(side="right", padx=6)
            T.label(row, f"מאז {_when(p.get('connected_at'))}", 10, T.TEXT_MUTED).pack(side="right")
            btn = T.big_button(row, verb, None, size=9, pady=2, padx=8)
            btn.configure(command=lambda u=p["username"], b=btn: self._two_step(
                ("disc", scope, u), b, "לחץ/י שוב לאישור", lambda: self.disconnect(u, scope)))
            T.bind_activate(btn, btn.invoke)
            btn.pack(side="left")

    def _render_sent(self, items):
        self._clear(self.sent_list)
        if not items:
            T.label(self.sent_list, "לא נשלחו הזמנות", 11, T.TEXT_MUTED, anchor="e").pack(fill="x")
        for inv in items:
            box = tk.Frame(self.sent_list, bg=T.CARD_BG, highlightbackground=T.BORDER, highlightthickness=1)
            box.pack(fill="x", pady=3)
            row = tk.Frame(box, bg=T.CARD_BG)
            row.pack(fill="x", padx=6, pady=4)
            if inv["channel"] == "username":
                who = f"{inv['target_display']} (@{inv['target_username']})"
            elif inv["channel"] == "phone":
                who = f"{inv.get('label') or 'טלפון'} · ‎•••{inv['phone_last4']}"
            else:
                who = f"{inv.get('label') or 'קוד הזמנה לשיתוף'} · קוד"
            T.label(row, who, 12, T.TEXT).pack(side="right")
            st = "awaiting_confirmation" if inv.get("awaiting_confirmation") else inv["status"]
            txt, lvl = STATUS.get(st, (st, "not_verified"))
            T.chip(row, txt, lvl).pack(side="right", padx=6)
            meta = (f"בתוקף עד {_when(inv['expires_at'])}" if inv["status"] == "pending"
                    else f"{txt} · {_when(inv.get('responded_at') or inv['expires_at'])}")
            T.label(box, meta + (f" · נשלח שוב {inv['resend_count']} פעמים" if inv["resend_count"] else ""),
                    10, T.TEXT_MUTED, anchor="e").pack(fill="x", padx=6)
            actions = tk.Frame(box, bg=T.CARD_BG)
            actions.pack(fill="x", padx=6, pady=(2, 4))
            if inv.get("awaiting_confirmation"):
                T.label(box, f"⚠️ {inv['accepted_by_display']} (@{inv['accepted_by']}) אישר/ה את ההזמנה לטלפון. "
                             "מספרי טלפון בשרת אינם מאומתים - אשר/י רק אם זה באמת האדם שהזמנת.",
                        10, T.YELLOW, anchor="e", wraplength=420).pack(fill="x", padx=6, before=actions)
                T.big_button(actions, "✅ כן, זה הוא/היא", lambda i=inv: self.confirm(i, True), bg=T.GREEN,
                             size=10, pady=3).pack(side="right")
                T.big_button(actions, "זה לא האדם", lambda i=inv: self.confirm(i, False), size=10,
                             pady=3).pack(side="right", padx=6)
                continue
            if inv["status"] in ("pending", "expired") and inv.get("resends_left", 0) > 0:
                T.big_button(actions, "📨 שלח שוב", lambda i=inv: self.resend(i), size=10, pady=3).pack(side="right")
            if inv["status"] == "pending":
                btn = T.big_button(actions, "בטל הזמנה", None, size=10, pady=3)
                btn.configure(command=lambda i=inv, b=btn: self._two_step(
                    ("cancel", i["id"]), b, "לחץ/י שוב לביטול", lambda: self.cancel(i)))
                T.bind_activate(btn, btn.invoke)
                btn.pack(side="right", padx=6)

    def _show_share(self, inv):
        """קוד חד-פעמי מוצג פעם אחת (השרת שומר רק hash). העתקה ללוח כדי לשלוח מהטלפון."""
        self._clear(self.share_box)
        if not inv.get("code"):
            return
        msg = invite_message(self.app.display_name, inv["code"], inv["expires_at"])
        box = tk.Frame(self.share_box, bg=T.LIGHT_BLUE)
        box.pack(fill="x")
        T.label(box, f"קוד ההזמנה: {inv['code']}", 16, T.BRAND, T.LIGHT_BLUE, bold=True).pack(anchor="e", padx=8, pady=(6, 0))
        T.label(box, "שלח/י את ההודעה הזו מהטלפון שלך (SMS / WhatsApp). הקוד יוצג רק עכשיו.", 10,
                T.TEXT_SECONDARY, T.LIGHT_BLUE, anchor="e", wraplength=420).pack(fill="x", padx=8)
        txt = tk.Text(box, height=5, font=T.F(10), wrap="word", bg=T.CARD_BG, fg=T.TEXT, bd=0, padx=6, pady=4)
        txt.tag_configure("rtl", justify="right")
        txt.insert("1.0", msg, "rtl")
        txt.configure(state="disabled")
        txt.pack(fill="x", padx=8, pady=4)

        def copy():
            self.clipboard_clear()
            self.clipboard_append(msg)
            self._say("📋 ההודעה הועתקה - הדבק/י אותה ב-SMS או WhatsApp")
        T.primary_button(box, "📋 העתק הודעה", copy, size=10, pady=3).pack(anchor="e", padx=8, pady=(0, 6))

    # ------------------------------------------------------------ actions
    def invite_username(self):
        name = self.username_var.get().strip().lstrip("@")
        if not name:
            return
        self._act(lambda c: c.invite(name), f"✅ ההזמנה נשלחה ל-@{name} - הקשר ייווצר אחרי שיאשר/תאשר",
                  lambda r: self.username_var.set(""))

    def invite_phone(self):
        phone = self.phone_var.get().strip()
        if not phone:
            return
        label = self.phone_name_var.get().strip() or None

        def after(inv):
            self.phone_var.set("")
            self.phone_name_var.set("")
            self._clear(self.picker)
            self._show_share(inv)
        self._act(lambda c: c.invite_phone(phone, label),
                  "✅ ההזמנה נשמרה בשרת. אם למספר יש חשבון - היא כבר מופיעה אצלו. שלח/י גם את הקוד:", after)

    def invite_code(self):
        self._act(lambda c: c.invite_code(), "✅ נוצר קוד הזמנה חד-פעמי", self._show_share)

    def import_contacts(self):
        path = filedialog.askopenfilename(parent=self, title="בחר/י קובץ אנשי קשר",
                                          filetypes=[("אנשי קשר", "*.vcf *.csv"), ("vCard", "*.vcf"),
                                                     ("CSV", "*.csv"), ("הכל", "*.*")])
        if not path:
            return
        try:
            contacts = contacts_import.load_file(path)
        except OSError as e:
            self._say(f"❌ לא ניתן לקרוא את הקובץ: {e}", False)
            return
        self._show_picker(contacts)

    def import_outlook(self):
        """אנשי קשר מ-Outlook הקלאסי שפתוח במחשב (קריאה מקומית בלבד, שום דבר לא נשלח לשרת)."""
        self._say("קורא אנשי קשר מ-Outlook...")
        run_bg(self.app, contacts_import.load_outlook, self._show_picker,
               lambda e: self._say(f"❌ {e}", False))

    def _show_picker(self, contacts):
        self._contacts = contacts
        self._clear(self.picker)
        if not self._contacts:
            self._say("❌ לא נמצאו אנשי קשר עם מספרי טלפון", False)
            return
        self.status.configure(text="")
        T.label(self.picker, f"נמצאו {len(self._contacts)} מספרים. בחר/י איש קשר:", 10, T.TEXT_MUTED,
                anchor="e").pack(fill="x", pady=(6, 0))
        q = tk.StringVar()
        self._entry(self.picker, q, lambda: None).pack(fill="x", ipady=3)
        lb = tk.Listbox(self.picker, height=6, font=T.F(11), justify="right", activestyle="dotbox",
                        highlightthickness=2, highlightcolor=T.FOCUS)
        lb.pack(fill="x", pady=(2, 0))
        shown = []

        def fill(*_):
            lb.delete(0, "end")
            shown.clear()
            needle = q.get().strip().lower()
            for ct in self._contacts:
                if not needle or needle in ct["name"].lower() or needle in ct["phone"]:
                    shown.append(ct)
                    lb.insert("end", f"{ct['name']}  ‎{ct['phone']}")
                    if len(shown) >= 300:
                        break

        def pick(_e=None):
            sel = lb.curselection()
            if sel:
                ct = shown[sel[0]]
                self.phone_var.set(ct["phone"])
                self.phone_name_var.set(ct["name"][:60])
        q.trace_add("write", fill)
        lb.bind("<<ListboxSelect>>", pick)
        lb.bind("<Double-Button-1>", lambda e: (pick(), self.invite_phone()))
        lb.bind("<Return>", lambda e: (pick(), self.invite_phone()))
        fill()

    def respond(self, inv, accept):
        def ok(r):
            if r.get("status") == "awaiting_confirmation":
                return "✅ אישרת. ההזמנה נשלחה לטלפון שלך, ולכן השולח/ת יאשר/תאשר שזה באמת את/ה."
            return f"✅ את/ה עכשיו איש קשר לחירום של {inv['from_display']}" if accept else "ההזמנה נדחתה"
        self._act(lambda c: c.respond_invitation(inv["id"], accept), ok)

    def confirm(self, inv, yes):
        self._act(lambda c: c.confirm_invitation(inv["id"], yes),
                  f"✅ {inv['accepted_by_display']} מחובר/ת עכשיו" if yes else "ההזמנה בוטלה - לא נוצר קשר")

    def resend(self, inv):
        self._act(lambda c: c.resend_invitation(inv["id"]),
                  "✅ ההזמנה נשלחה שוב והתוקף הוארך" + (" - הקוד הקודם בוטל, שלח/י את הקוד החדש:"
                                                         if inv["channel"] != "username" else ""),
                  self._show_share)

    def cancel(self, inv):
        self._act(lambda c: c.cancel_invitation(inv["id"]), "ההזמנה בוטלה")

    def disconnect(self, username, scope):
        self._act(lambda c: c.disconnect(username, scope),
                  f"הקשר עם @{username} נותק" + (" - הגישה שלו/ה לאירועים פעילים שלך בוטלה" if scope == "my_contact" else ""))

    def check_code(self):
        code = self.code_var.get().strip()
        if not code:
            return
        self._clear(self.code_box)
        c = self._client()
        if c is None:
            return

        def show(p):
            self._clear(self.code_box)
            if p.get("status") == "connected":
                self._say("✅ כבר מחוברים דרך הקוד הזה")
                self.code_var.set("")
                return
            box = tk.Frame(self.code_box, bg=T.YELLOW_BG)
            box.pack(fill="x")
            T.label(box, f"{p['from_display']} (@{p['from']}) מבקש/ת שתהיה/י איש הקשר שלו/ה לחירום. "
                         "אם תאשר/י - תקבל/י את התראות ה-SOS והמיקום שלו/ה.", 11, T.TEXT, T.YELLOW_BG,
                    anchor="e", wraplength=440).pack(fill="x", padx=6, pady=4)
            r = tk.Frame(box, bg=T.YELLOW_BG)
            r.pack(fill="x", padx=6, pady=(0, 4))

            def redeem(accept):
                self._act(lambda cl: cl.redeem_code(code, accept),
                          f"✅ את/ה עכשיו איש קשר לחירום של {p['from_display']}" if accept else "ההזמנה נדחתה",
                          lambda res: (self.code_var.set(""), self._clear(self.code_box)))
            T.big_button(r, "אשר", lambda: redeem(True), bg=T.GREEN, size=10, pady=3).pack(side="left", padx=3)
            T.big_button(r, "דחה", lambda: redeem(False), size=10, pady=3).pack(side="left", padx=3)
        run_bg(self.app, lambda: c.preview_code(code), show, lambda e: self._say(f"❌ {_err(e)}", False))
