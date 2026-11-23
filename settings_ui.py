# -*- coding: utf-8 -*-
"""
settings_ui.py
מסך הגדרות - טלפון, סיסמה, שלוש פקודות קוליות וביטוי כפייה אופציונלי.
(אנשי קשר לחירום מנוהלים בשרת, בלשונית "מעגל הביטחון".)

נפתח רק כש-engine.state == IDLE (לא באמצע אירוע) - ראה main.py.
דורש אימות סיסמה חוזר בכניסה למסך (re-auth) - שכבת הגנה נוספת נגד עריכת הגדרות קריטיות
(אנשי קשר לחירום, קודים) אם המחשב נשאר פתוח/לא מנוטר.
"""

import threading
import tkinter as tk
import ui_theme as T
from tkinter import ttk

import config
import auth_module
import incident_store
import voice_trigger_module


def _try(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - מוצג למשתמש
        return getattr(exc, "message", str(exc))



class SettingsWindow(tk.Toplevel):
    def __init__(self, master, engine, username, on_close=None):
        super().__init__(master)
        self.engine = engine
        self.username = username
        self.on_close = on_close
        self.title("הגדרות")
        self.geometry("560x760")
        self.configure(bg=T.BG)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.contact_rows = []
        self._authenticated = False

        self._build_auth_gate()

    # ---------------- שער אימות (חובה לפני עריכה) ----------------
    def _build_auth_gate(self):
        self.gate_frame = tk.Frame(self, bg=T.BG)
        self.gate_frame.pack(fill="both", expand=True)

        tk.Label(self.gate_frame, text="⚙️ הגדרות", font=T.F(18, "bold"),
                 fg=T.TEXT, bg=T.BG).pack(pady=(30, 8))
        tk.Label(self.gate_frame, text="לצורך שינוי הגדרות רגישות, הזן/י שוב את הסיסמה שלך:",
                 font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG).pack(pady=(0, 16))

        self.gate_password_var = tk.StringVar()
        entry = tk.Entry(self.gate_frame, textvariable=self.gate_password_var, font=T.F(13),
                          show="•", justify="center", width=24)
        entry.pack(pady=6)
        entry.bind("<Return>", lambda e: self._try_unlock())
        entry.focus_set()

        self.gate_error_label = tk.Label(self.gate_frame, text="", font=T.F(9),
                                          fg=T.RED, bg=T.BG)
        self.gate_error_label.pack(pady=(4, 0))

        tk.Button(self.gate_frame, text="פתח הגדרות", font=T.F(11, "bold"),
                  fg=T.ON_ACCENT, bg=T.BLUE, bd=0, padx=24, pady=8, cursor="hand2",
                  command=self._try_unlock).pack(pady=16)

    def _try_unlock(self):
        password = self.gate_password_var.get()
        if auth_module.verify_password(self.username, password):
            self._authenticated = True
            self._current_password = password      # בזיכרון בלבד, לצורך החלפת הסיסמה גם בשרת
            self.gate_frame.destroy()
            self._build_settings_ui()
        else:
            self.gate_error_label.configure(text="סיסמה שגויה")
            self.gate_password_var.set("")

    # ---------------- מסך ההגדרות בפועל (אחרי אימות) ----------------
    def _build_settings_ui(self):
        header = tk.Frame(self, bg=T.BG)
        header.pack(fill="x", pady=(16, 4))
        tk.Label(header, text="⚙️ הגדרות פרופיל", font=T.F(16, "bold"),
                 fg=T.TEXT, bg=T.BG).pack()

        canvas = tk.Canvas(self, bg=T.BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=T.BG)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw", width=T.px(520))
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="left", fill="y")
        canvas.pack(side="right", fill="both", expand=True, padx=(0, 20))

        def _wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120) * 2), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        f = scroll_frame

        def section_label(text):
            tk.Label(f, text=text, font=T.F(11, "bold"), fg=T.BLUE, bg=T.BG,
                     anchor="e").pack(fill="x", padx=20, pady=(16, 4))

        # ===== אנשי קשר: מנוהלים בשרת (מעגל הביטחון) =====
        # בעבר אנשי הקשר נערכו כאן ונשמרו רק במחשב הזה - הם לא הגיעו לשרת ולא קיבלו SOS עד הכניסה
        # הבאה. עכשיו כל קשר נוצר בשרת בהזמנה + אישור, ומסונכרן לכל המכשירים.
        section_label("אנשי קשר לחירום")
        tk.Label(f, text="הזמנה, אישור, שליחה מחדש וניתוק של אנשי קשר - בלשונית 👨‍👩‍👧 'מעגל הביטחון' "
                         "שבתחתית המסך הראשי. הרשימה נשמרת בשרת ומסונכרנת בין המכשירים.",
                 font=T.F(9), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e", justify="right",
                 wraplength=480).pack(fill="x", padx=20)
        tk.Label(f, text="מספר הטלפון שלי (אופציונלי) - כדי שהזמנות שנשלחו למספר שלך יגיעו אליך:",
                 font=T.F(9), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e").pack(fill="x", padx=20, pady=(8, 0))
        self.phone_var = tk.StringVar(value=auth_module.get_phone(self.username))
        tk.Entry(f, textvariable=self.phone_var, font=T.F(11), justify="right").pack(fill="x", padx=20, pady=(2, 4))
        self.contacts_status_label = tk.Label(f, text="", font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG,
                                               anchor="e", wraplength=480)
        self.contacts_status_label.pack(fill="x", padx=20)
        tk.Button(f, text="💾 שמור טלפון", font=T.F(10, "bold"), fg=T.ON_ACCENT, bg=T.BLUE,
                  bd=0, padx=16, pady=6, cursor="hand2",
                  command=self._save_phone).pack(anchor="e", padx=20, pady=(4, 4))
        # אימות SMS + גיבוי SMS - רק אם לשרת יש ספק SMS (אחרת מוסבר שזה לא זמין)
        import features_ui
        gw = getattr(self.engine, "gateway", None) if self.engine is not None else None
        features_ui.PhoneVerifyRow(f, getattr(gw, "client", None), T.BG)

        # ===== התראות סכנה מ-AI (hazard_alerts.py) =====
        self._build_hazard_section(f, section_label)

        # ===== מקום תפילה כמפלט אחרון (worship_pref.py) - מקומי בלבד =====
        import worship_pref
        section_label("מקום מפלט נוסף בחירום (לא חובה)")
        tk.Label(f, text="לא חובה. אם בזמן סכנה אין תחנת משטרה, בית חולים, תחנת כיבוי או מקום ציבורי פתוח קרוב - לאיזה מקום תפילה להפנות אותך? נשמר רק במחשב הזה (מוצפן), לא נשלח לשרת ולא לאנשי הקשר.", font=T.F(9), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e",
                 justify="right", wraplength=480).pack(fill="x", padx=20)
        from tkinter import ttk
        self.worship_var = tk.StringVar(value=worship_pref.OPTIONS[worship_pref.get(self.username)])
        ttk.Combobox(f, textvariable=self.worship_var, values=list(worship_pref.OPTIONS.values()), state="readonly",
                     justify="right", width=26).pack(anchor="e", padx=20, pady=(4, 2))
        self.worship_status = tk.Label(f, text="", font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e")
        self.worship_status.pack(fill="x", padx=20)
        tk.Button(f, text="💾 שמור", font=T.F(9, "bold"), fg=T.ON_ACCENT, bg=T.BLUE, bd=0, padx=12, pady=4,
                  cursor="hand2", command=self._save_worship).pack(anchor="e", padx=20, pady=(2, 8))

        # ===== סיסמה אחת לחשבון ולביטול =====
        section_label("סיסמת החשבון - משמשת גם לביטול אזעקה")
        self.new_password_var = tk.StringVar()
        self.confirm_password_var = tk.StringVar()
        tk.Entry(f, textvariable=self.new_password_var, font=T.F(10), show="•",
                 justify="right").pack(fill="x", padx=20, pady=(2, 4))
        tk.Label(f, text="סיסמה חדשה (8 תווים ומעלה)", font=T.F(10),
                 fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e").pack(fill="x", padx=20)
        tk.Entry(f, textvariable=self.confirm_password_var, font=T.F(10), show="•",
                 justify="right").pack(fill="x", padx=20, pady=(4, 4))
        tk.Label(f, text="אימות סיסמה חדשה", font=T.F(10), fg=T.TEXT_SECONDARY,
                 bg=T.BG, anchor="e").pack(fill="x", padx=20)
        self.password_status = tk.Label(f, text="", font=T.F(10), fg=T.TEXT_SECONDARY,
                                        bg=T.BG, anchor="e", wraplength=480)
        self.password_status.pack(fill="x", padx=20)
        tk.Button(f, text="עדכן סיסמה", font=T.F(9, "bold"), fg=T.ON_ACCENT,
                  bg=T.BLUE, bd=0, padx=12, pady=4, cursor="hand2",
                  command=self._save_password).pack(anchor="e", padx=20, pady=(4, 8))
        tk.Label(f, text="החלפת סיסמה מנתקת מיד את כל המכשירים האחרים שמחוברים לחשבון.", font=T.F(10),
                 fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e").pack(fill="x", padx=20)
        tk.Button(f, text="🔒 נתק את כל המכשירים האחרים", font=T.F(9, "bold"), fg="#FFFFFF",
                  bg=T.SOS_FILL, bd=0, padx=12, pady=4, cursor="hand2",
                  command=self._sign_out_others).pack(anchor="e", padx=20, pady=(4, 2))
        self.sessions_status = tk.Label(f, text="", font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e",
                                        wraplength=480)
        self.sessions_status.pack(fill="x", padx=20, pady=(0, 8))

        # ===== פקודות קוליות =====
        section_label("מילת קוד להפעלת אזעקה רועשת")
        self.voice_word_var, self.voice_word_status = self._phrase_row(
            f, "ביטוי להפעלה רועשת", auth_module.get_voice_secret_word(self.username),
            self._save_voice_word, test_key="voice_word"
        )

        section_label("מילת קוד להפעלת מצוקה שקטה")
        self.silent_phrase_var, self.silent_phrase_status = self._phrase_row(
            f, "ביטוי להפעלה שקטה", auth_module.get_silent_alarm_phrase(self.username),
            self._save_silent_phrase, test_key="silent_phrase"
        )

        section_label("מילת קוד לביטול קולי של שתי האזעקות")
        self.cancel_secret_var, self.cancel_secret_status = self._phrase_row(
            f, "ביטוי לביטול שתי האזעקות", auth_module.get_secret_cancel_phrase(self.username),
            self._save_cancel_secret, test_key="cancel_secret"
        )

        section_label("מילת קוד כפייה (אופציונלית)")
        self.duress_phrase_var, self.duress_phrase_status = self._phrase_row(
            f, "ביטוי כפייה — נראה כמו ביטול אך משאיר מעקב שקט",
            auth_module.get_duress_phrase(self.username),
            self._save_duress_phrase, test_key="duress_phrase"
        )

        tk.Button(f, text="סגור", font=T.F(10), fg=T.TEXT, bg=T.CARD_BG_ALT, bd=0,
                  padx=20, pady=8, cursor="hand2", command=self._close).pack(pady=20)

    # ---------------- רכיבי עזר ----------------
    def _phrase_row(self, parent, label_text, current_value, save_callback, test_key):
        row = tk.Frame(parent, bg=T.BG)
        row.pack(fill="x", padx=20, pady=(6, 2))
        required_text = "(אופציונלי)" if test_key == "duress_phrase" else "(חובה להשאיר מוגדר)"
        tk.Label(row, text=f"{label_text} {required_text}", font=T.F(9), fg=T.TEXT_SECONDARY,
                 bg=T.BG, anchor="e").pack(fill="x")
        var = tk.StringVar(value=current_value)
        tk.Entry(row, textvariable=var, font=T.F(11), justify="right").pack(fill="x", pady=(2, 4))

        btn_row = tk.Frame(row, bg=T.BG)
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="🎙️ בדוק בקול", font=T.F(10), fg=T.TEXT, bg=T.CARD_BG_ALT,
                  bd=0, padx=8, pady=4, cursor="hand2",
                  command=lambda: self._test_phrase(var, status, test_key)).pack(side="right", padx=(6, 0))
        tk.Button(btn_row, text="💾 שמור", font=T.F(9, "bold"), fg=T.ON_ACCENT, bg=T.BLUE,
                  bd=0, padx=12, pady=4, cursor="hand2",
                  command=lambda: save_callback(var, status)).pack(side="right")

        status = tk.Label(row, text="", font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG,
                           anchor="e", wraplength=480)
        status.pack(fill="x", pady=(4, 8))
        return var, status

    # ---------------- שמירה ----------------
    def _build_hazard_section(self, f, section_label):
        import hazard_alerts as HA
        from tkinter import ttk
        section_label("התראות סכנה מ-AI")
        prefs = HA.load_prefs(self.username)
        listener = getattr(self.engine, "_voice_listener", None) if self.engine is not None else None
        tk.Label(f, text=("כשה-AI מזהה צליל שעשוי להעיד על סכנה (ירי, פיצוץ, שבירה, צרחה...) מופיע חלון עם אפשרויות: "
                          "SOS, מקום בטוח, התראה לאנשי הקשר, חיוג לחירום, התראת שווא. ה-AI לעולם לא מפעיל SOS לבד. "
                          + ("הזיהוי ברקע פעיל (מקומי, בלי שמירת אודיו)." if listener is not None else
                             "כרגע הזיהוי ברקע לא פעיל: הוא עובד רק כשההאזנה הקולית למילות הקוד פועלת.")),
                 font=T.F(9), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e", justify="right",
                 wraplength=480).pack(fill="x", padx=20)
        self.hz_popups = tk.BooleanVar(value=prefs["popups"])
        self.hz_sound = tk.BooleanVar(value=prefs["sound"])
        for var, text in ((self.hz_popups, "חלונות קופצים על סכנה שזוהתה"), (self.hz_sound, "צליל התראה")):
            tk.Checkbutton(f, text=text, variable=var, font=T.F(10), bg=T.BG, fg=T.TEXT, selectcolor=T.CARD_BG_ALT,
                           activebackground=T.BG, anchor="e").pack(fill="x", padx=20)
        tk.Checkbutton(f, text="רטט (לא נתמך במחשב - קיים רק בטלפונים)", state="disabled", font=T.F(10), bg=T.BG,
                       fg=T.TEXT_MUTED, anchor="e").pack(fill="x", padx=20)
        row = tk.Frame(f, bg=T.BG)
        row.pack(fill="x", padx=20, pady=(2, 0))
        tk.Label(row, text="להציג מ:", font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG).pack(side="right")
        self.hz_sev = tk.StringVar(value=HA.MIN_SEVERITY_OPTIONS[prefs["min_severity"]])
        ttk.Combobox(row, textvariable=self.hz_sev, values=list(HA.MIN_SEVERITY_OPTIONS.values()), state="readonly",
                     justify="right", width=24).pack(side="right", padx=6)
        tk.Label(row, text="שפת החלון:", font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG).pack(side="right", padx=(12, 0))
        self.hz_lang = tk.StringVar(value={"he": "עברית", "en": "English"}[prefs["language"]])
        ttk.Combobox(row, textvariable=self.hz_lang, values=["עברית", "English"], state="readonly",
                     width=9).pack(side="right", padx=6)
        tk.Label(f, text=("כשהחלונות כבויים: הזיהוי ממשיך לרוץ ונרשם (בלי חלון). נשארות פעילות תמיד: ספירה לאחור של SOS, "
                          "\"הכל בסדר?\" אחרי נפילה, קריאות מאנשי הקשר והתראות מהשרת."),
                 font=T.F(9), fg=T.TEXT_MUTED, bg=T.BG, anchor="e", justify="right", wraplength=480).pack(fill="x", padx=20)
        self.hz_status = tk.Label(f, text="", font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e")
        self.hz_status.pack(fill="x", padx=20)
        tk.Button(f, text="💾 שמור", font=T.F(9, "bold"), fg=T.ON_ACCENT, bg=T.BLUE, bd=0, padx=12, pady=4,
                  cursor="hand2", command=self._save_hazard_prefs).pack(anchor="e", padx=20, pady=(2, 8))

    def _save_hazard_prefs(self):
        import hazard_alerts as HA
        sev = next(k for k, v in HA.MIN_SEVERITY_OPTIONS.items() if v == self.hz_sev.get())
        lang = {"עברית": "he", "English": "en"}[self.hz_lang.get()]
        try:
            HA.save_prefs(self.username, {"popups": bool(self.hz_popups.get()), "sound": bool(self.hz_sound.get()),
                                          "min_severity": sev, "language": lang})
            self.hz_status.configure(text="✅ נשמר", fg=T.GREEN)
        except Exception as exc:  # noqa: BLE001
            self.hz_status.configure(text=f"❌ השמירה נכשלה: {exc}", fg=T.RED)

    def _save_worship(self):
        import worship_pref
        key = next(k for k, v in worship_pref.OPTIONS.items() if v == self.worship_var.get())
        try:
            worship_pref.save(self.username, key)
            self.worship_status.configure(text="✅ נשמר (במחשב הזה בלבד)", fg=T.GREEN)
        except Exception as exc:  # noqa: BLE001
            self.worship_status.configure(text=f"❌ השמירה נכשלה: {exc}", fg=T.RED)

    def _save_phone(self):
        """נשמר מקומית ונשלח לשרת מיד (אם מחובר). השרת שומר רק hash + 4 ספרות אחרונות."""
        phone = self.phone_var.get().strip()
        ok, msg = auth_module.set_phone(self.username, phone)
        if not ok:
            self.contacts_status_label.configure(text=msg, fg=T.RED)
            return
        gw = getattr(self.engine, "gateway", None) if self.engine is not None else None
        client = getattr(gw, "client", None)
        if client is None or not client.token:
            self.contacts_status_label.configure(text="נשמר במחשב. יישלח לשרת בחיבור הבא (כרגע לא מחובר).",
                                                 fg=T.YELLOW)
            return
        result = []

        def work():
            try:
                client.set_phone(phone)
                result.append(("✅ נשמר בשרת (המספר אינו מאומת ב-SMS)", T.GREEN))
            except Exception as exc:  # noqa: BLE001 - מוצג למשתמש
                result.append((f"❌ השרת לא קיבל את המספר: {getattr(exc, 'message', exc)}", T.RED))

        def poll():  # עדכון הממשק רק מה-thread של Tk
            if not self.winfo_exists():
                return
            if result:
                self.contacts_status_label.configure(text=result[0][0], fg=result[0][1])
            else:
                self.after(150, poll)
        self.contacts_status_label.configure(text="שומר...", fg=T.TEXT_SECONDARY)
        threading.Thread(target=work, daemon=True).start()
        self.after(150, poll)

    def _save_password(self):
        new_password = self.new_password_var.get()
        confirm = self.confirm_password_var.get()
        if new_password != confirm:
            self.password_status.configure(text="אימות הסיסמה אינו תואם", fg=T.RED)
            return
        if not new_password:
            self.password_status.configure(text="לא הוזנה סיסמה חדשה", fg=T.TEXT_SECONDARY)
            return
        ok, msg = auth_module.validate_new_password(self.username, new_password)
        if not ok:
            self.password_status.configure(text=msg, fg=T.RED)
            return
        client = getattr(getattr(self.engine, "gateway", None), "client", None) if self.engine is not None else None
        if client is None or not getattr(client, "token", None):
            if incident_store.get_setting(self.username, "server_account", "0") == "1":
                # הסיסמה משותפת לשרת: החלפה רק מקומית הייתה מנתקת את החשבון בשרת בכניסה הבאה
                self.password_status.configure(text="יש להתחבר לשרת LifeGuard כדי להחליף סיסמה "
                                                    "(הסיסמה משמשת גם בשרת)", fg=T.RED)
                return
            self._apply_local_password(new_password, None)
            return
        self.password_status.configure(text="מחליף סיסמה בשרת...", fg=T.TEXT_SECONDARY)
        result = []

        def work():
            try:
                res = client.change_password(self._current_password, new_password)
            except Exception as exc:  # noqa: BLE001 - מוצג למשתמש
                result.append(("err", getattr(exc, "message", str(exc))))
                return
            # סוכן הרקע מחזיק התחברות נפרדת - היא נותקה יחד עם השאר; מחברים אותו מחדש בסיסמה החדשה
            try:
                import agent_bridge
                if incident_store.get_setting(self.username, "background_agent", "1") == "1":
                    agent_bridge.provision(config.SERVER_URL, self.username, new_password)
            except Exception:  # noqa: BLE001 - לא מונע את החלפת הסיסמה
                pass
            result.append(("ok", res))

        def poll():
            if not self.winfo_exists():
                return
            if not result:
                self.after(150, poll)
                return
            kind, val = result[0]
            if kind == "err":
                self.password_status.configure(text=f"ההחלפה בשרת נכשלה: {val}", fg=T.RED)
            else:
                self._apply_local_password(new_password, val.get("other_sessions_revoked", 0))
        threading.Thread(target=work, daemon=True).start()
        self.after(150, poll)

    def _apply_local_password(self, new_password, revoked):
        ok, msg = auth_module.update_password(self.username, new_password)
        if ok and revoked is not None:
            msg += f" · נותקו {revoked} מכשירים אחרים מהחשבון"
        self.password_status.configure(text=msg, fg=T.GREEN if ok else T.RED)
        if ok:
            self._current_password = new_password
            self.new_password_var.set("")
            self.confirm_password_var.set("")
            if self.engine is not None:
                self.engine.log("⚙️ סיסמת החשבון עודכנה")

    def _sign_out_others(self):
        """'נתק את כל המכשירים האחרים' - למשל אם מישהו אחר ידע את הסיסמה או נכנס מהמכשיר שלו."""
        client = getattr(getattr(self.engine, "gateway", None), "client", None) if self.engine is not None else None
        if client is None or not getattr(client, "token", None):
            self.sessions_status.configure(text="אין חיבור לשרת", fg=T.RED)
            return
        result = []
        threading.Thread(target=lambda: result.append(_try(lambda: client.revoke_sessions())), daemon=True).start()

        def poll():
            if not self.winfo_exists():
                return
            if not result:
                self.after(150, poll)
                return
            r = result[0]
            self.sessions_status.configure(
                text=(f"נותקו {r['revoked']} מכשירים. אם מישהו ידע את הסיסמה - החליפי/החלף אותה עכשיו."
                      if isinstance(r, dict) else f"נכשל: {r}"), fg=T.GREEN if isinstance(r, dict) else T.RED)
        self.after(150, poll)

    def _save_voice_word(self, var, status_label):
        ok, msg = auth_module.update_voice_secret_word(self.username, var.get().strip())
        self._after_phrase_save(ok, msg, status_label, "מילת הקוד לאזעקה רועשת")

    def _save_silent_phrase(self, var, status_label):
        ok, msg = auth_module.update_silent_alarm_phrase(self.username, var.get().strip())
        self._after_phrase_save(ok, msg, status_label, "מילת הקוד למצוקה שקטה")

    def _save_cancel_secret(self, var, status_label):
        ok, msg = auth_module.update_secret_cancel_phrase(self.username, var.get().strip())
        self._after_phrase_save(ok, msg, status_label, "מילת הקוד לביטול קולי")

    def _save_duress_phrase(self, var, status_label):
        ok, msg = auth_module.update_duress_phrase(self.username, var.get().strip())
        self._after_phrase_save(ok, msg, status_label, "מילת קוד כפייה")

    def _after_phrase_save(self, ok, msg, status_label, field_label):
        status_label.configure(text=msg, fg=T.GREEN if ok else T.RED)
        if ok and self.engine is not None:
            self.engine.log(f"⚙️ {field_label} עודכן דרך מסך ההגדרות")
            # מפעילים מחדש את ההאזנה הקולית כדי שהשינוי ייכנס לתוקף מיידית, בלי להזדקק
            # להפעלה מחדש של כל התוכנה.
            self.engine.stop_voice_trigger()
            self.engine.start_voice_trigger()

    def _test_phrase(self, var, status_label, test_key):
        phrase = var.get().strip()
        if not phrase:
            status_label.configure(text="הזן קודם ביטוי בשדה למעלה", fg=T.YELLOW)
            return
        if not voice_trigger_module.HAS_SR:
            status_label.configure(text="⚠️ speech_recognition לא מותקן - לא ניתן לבדוק", fg=T.TEXT_SECONDARY)
            return
        status_label.configure(text="🎙️ מקליט... אמור/אמרי את הביטוי עכשיו", fg=T.BLUE)
        threading.Thread(target=self._run_voice_test, args=(phrase, status_label), daemon=True).start()

    def _run_voice_test(self, expected_phrase, status_label):
        matched, recognized, error = voice_trigger_module.test_phrase_recognition(expected_phrase)
        self.after(0, self._on_voice_test_result, matched, recognized, error, status_label)

    def _on_voice_test_result(self, matched, recognized, error, status_label):
        if error:
            status_label.configure(text=f"⚠️ שגיאה: {error}", fg=T.RED)
        elif matched:
            status_label.configure(text=f'✅ זוהה בהצלחה: "{recognized}"', fg=T.GREEN)
        else:
            shown = recognized if recognized else "(לא זוהה דיבור בכלל)"
            status_label.configure(text=f'❌ לא זוהתה התאמה. נשמע: "{shown}"', fg=T.RED)

    def _close(self):
        self.destroy()
        if self.on_close:
            self.on_close()
