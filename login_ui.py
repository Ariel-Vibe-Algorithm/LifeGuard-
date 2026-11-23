# -*- coding: utf-8 -*-
"""
login_ui.py
מסך התחברות/הרשמה - נפתח לפני החלון הראשי של האפליקציה.
בהרשמה: שם משתמש, סיסמת חשבון אחת, שלוש פקודות קוליות ואנשי קשר לחירום.
"""

import tkinter as tk
import ui_theme as T
from tkinter import ttk, messagebox
import re
import threading

import config
import auth_module
import voice_trigger_module


class LoginWindow:
    """
    מנהל את כל מסך ההתחברות/הרשמה בתוך ה-root שכבר קיים.
    בסיום מוצלח קורא ל-on_success(username) ומנקה את עצמו מהמסך.
    """

    def __init__(self, root, on_success):
        self.root = root
        self.on_success = on_success
        self.contact_rows = []  # רשימת dict של StringVar לכל איש קשר בטופס ההרשמה

        self.container = tk.Frame(root, bg=T.BG)
        self.container.pack(fill="both", expand=True)

        self._build_ui()

    def _build_ui(self):
        tk.Label(
            self.container, text="🛡️ LifeGuard Engine", font=T.F(22, "bold"),
            fg=T.TEXT, bg=T.BG
        ).pack(pady=(30, 4))
        tk.Label(
            self.container, text="התחבר או הירשם כדי להמשיך", font=T.F(10),
            fg=T.TEXT_SECONDARY, bg=T.BG
        ).pack(pady=(0, 16))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=T.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=T.CARD_BG, foreground=T.TEXT, padding=(20, 8))
        style.map("TNotebook.Tab", background=[("selected", T.BLUE)], foreground=[("selected", T.ON_ACCENT)])

        notebook = ttk.Notebook(self.container)
        notebook.pack(fill="both", expand=True, padx=24, pady=8)

        login_tab = tk.Frame(notebook, bg=T.BG)
        register_tab_container = tk.Frame(notebook, bg=T.BG)
        notebook.add(login_tab, text="  התחברות  ")
        notebook.add(register_tab_container, text="  הרשמה  ")

        self._build_login_tab(login_tab)
        self._build_register_tab(register_tab_container)

    # ---------------- טאב התחברות ----------------
    def _build_login_tab(self, parent):
        frame = tk.Frame(parent, bg=T.BG)
        frame.pack(pady=30)

        tk.Label(frame, text="שם משתמש", font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG).pack(anchor="e")
        self.login_username_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.login_username_var, font=T.F(12), width=28,
                 justify="right").pack(pady=(2, 12))

        tk.Label(frame, text="סיסמה", font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG).pack(anchor="e")
        self.login_password_var = tk.StringVar()
        pwd_entry = tk.Entry(frame, textvariable=self.login_password_var, font=T.F(12), show="•",
                              width=28, justify="right")
        pwd_entry.pack(pady=(2, 4))
        pwd_entry.bind("<Return>", lambda e: self._do_login())
        tk.Label(
            frame, text="אותה סיסמה משמשת להתחברות וגם לביטול אזעקה כשאת/ה בטוח/ה",
            font=T.F(8), fg=T.TEXT_MUTED, bg=T.BG, anchor="e"
        ).pack(fill="x", pady=(0, 12))

        self.login_error_label = tk.Label(frame, text="", font=T.F(9), fg=T.RED, bg=T.BG)
        self.login_error_label.pack()

        tk.Button(
            frame, text="התחבר", font=T.F(11, "bold"), bg=T.BLUE, fg=T.ON_ACCENT,
            bd=0, padx=30, pady=8, cursor="hand2", command=self._do_login
        ).pack(pady=10)

    def _do_login(self):
        username = self.login_username_var.get().strip()
        password = self.login_password_var.get()
        if not username or not password:
            self.login_error_label.configure(text="נא למלא שם משתמש וסיסמה")
            return
        success, message = auth_module.verify_login(username, password)
        if success:
            self.root.unbind_all("<MouseWheel>")
            self.root.unbind_all("<Button-4>")
            self.root.unbind_all("<Button-5>")
            self.container.destroy()
            self.on_success(username, password)
        else:
            self.login_error_label.configure(text=message)

    # ---------------- טאב הרשמה ----------------
    def _build_register_tab(self, parent):
        """טופס הרשמה קצר: חשבון אחד, אנשי קשר ושלוש פקודות קוליות."""
        canvas = tk.Canvas(parent, bg=T.BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=T.BG)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw", width=T.px(420))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            try:
                delta = event.delta
                if delta == 0 and hasattr(event, "num"):
                    delta = 120 if event.num == 4 else -120
                canvas.yview_scroll(int(-1 * (delta / 120) * 2), "units")
            except tk.TclError:
                pass

        def _bind_wheel(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_mousewheel)
            canvas.bind_all("<Button-5>", _on_mousewheel)

        def _unbind_wheel(_event):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        scroll_frame.bind("<Enter>", _bind_wheel)
        scroll_frame.bind("<Leave>", _unbind_wheel)

        f = scroll_frame

        def section_label(text):
            tk.Label(f, text=text, font=T.F(11, "bold"), fg=T.RED, bg=T.BG,
                     anchor="e").pack(fill="x", padx=20, pady=(14, 4))

        def field(label_text, secret=False):
            tk.Label(f, text=label_text, font=T.F(9), fg=T.TEXT_SECONDARY, bg=T.BG,
                     anchor="e").pack(fill="x", padx=20)
            var = tk.StringVar()
            tk.Entry(f, textvariable=var, font=T.F(11), justify="right",
                     show="•" if secret else "").pack(fill="x", padx=20, pady=(2, 8))
            return var

        section_label("פתיחת חשבון")
        self.reg_username_var = field("שם משתמש")
        self.reg_password_var = field("סיסמה אחת להתחברות ולביטול אזעקה (8 תווים ומעלה)", secret=True)
        self.reg_password_confirm_var = field("אימות סיסמה", secret=True)
        tk.Label(
            f, text="חשוב: כשתהיה/י בטוח/ה, הזנת הסיסמה הזו במסך האירוע תבטל את האזעקה. "
                    "אין צורך לזכור קוד ביטול נוסף.",
            font=T.F(8), fg=T.RED, bg=T.BG, anchor="e", justify="right",
            wraplength=380
        ).pack(fill="x", padx=20, pady=(0, 6))

        # מקום תפילה כמפלט אחרון בחירום (worship_pref.py) - לא חובה, נשמר רק במחשב
        import worship_pref
        section_label("מקום מפלט נוסף בחירום (לא חובה)")
        tk.Label(f, text="לא חובה. אם בזמן סכנה אין תחנת משטרה, בית חולים, תחנת כיבוי או מקום ציבורי פתוח קרוב - לאיזה מקום תפילה להפנות אותך? נשמר רק במחשב הזה (מוצפן), לא נשלח לשרת ולא לאנשי הקשר.", font=T.F(8), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e",
                 justify="right", wraplength=380).pack(fill="x", padx=20, pady=(0, 4))
        self.reg_worship_var = tk.StringVar(value=worship_pref.OPTIONS[worship_pref.DEFAULT])
        ttk.Combobox(f, textvariable=self.reg_worship_var, values=list(worship_pref.OPTIONS.values()),
                     state="readonly", justify="right", width=26).pack(anchor="e", padx=20, pady=(0, 8))

        section_label("פקודות קוליות לחירום")
        tk.Label(
            f, text="בחר/י שלושה צירופי מילים ייחודיים שלא נאמרים בשיחה רגילה. "
                    "הזיהוי הקולי דורש מיקרופון, אינטרנט וספריות SpeechRecognition + PyAudio.",
            font=T.F(8), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e", justify="right",
            wraplength=380
        ).pack(fill="x", padx=20, pady=(0, 8))

        # אזעקה שקטה
        tk.Label(f, text="🤫 מילת קוד להפעלת מצוקה שקטה", font=T.F(9, "bold"),
                 fg=T.YELLOW, bg=T.BG, anchor="e").pack(fill="x", padx=20)
        self.reg_silent_phrase_var = tk.StringVar()
        tk.Entry(f, textvariable=self.reg_silent_phrase_var, font=T.F(11),
                 justify="right").pack(fill="x", padx=20, pady=(2, 4))
        silent_test_frame = tk.Frame(f, bg=T.BG)
        silent_test_frame.pack(fill="x", padx=20, pady=(0, 6))
        tk.Button(silent_test_frame, text="🎙️ בדוק בקול", font=T.F(8), fg=T.TEXT,
                  bg=T.CARD_BG_ALT, bd=0, padx=10, pady=5, cursor="hand2",
                  command=self._test_silent_phrase).pack(anchor="e")
        self.silent_phrase_result_label = tk.Label(
            silent_test_frame, text="בדיקת מיקרופון (לא חובה)", font=T.F(8),
            fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e", justify="right", wraplength=380
        )
        self.silent_phrase_result_label.pack(fill="x", pady=(3, 0))

        # אזעקה רועשת
        tk.Label(f, text="🚨 מילת קוד להפעלת אזעקה רועשת", font=T.F(9, "bold"),
                 fg=T.RED, bg=T.BG, anchor="e").pack(fill="x", padx=20, pady=(8, 0))
        self.reg_voice_word_var = tk.StringVar()
        tk.Entry(f, textvariable=self.reg_voice_word_var, font=T.F(11),
                 justify="right").pack(fill="x", padx=20, pady=(2, 4))
        voice_test_frame = tk.Frame(f, bg=T.BG)
        voice_test_frame.pack(fill="x", padx=20, pady=(0, 6))
        tk.Button(voice_test_frame, text="🎙️ בדוק בקול", font=T.F(8), fg=T.TEXT,
                  bg=T.CARD_BG_ALT, bd=0, padx=10, pady=5, cursor="hand2",
                  command=self._test_voice_word).pack(anchor="e")
        self.voice_word_result_label = tk.Label(
            voice_test_frame, text="בדיקת מיקרופון (לא חובה)", font=T.F(8),
            fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e", justify="right", wraplength=380
        )
        self.voice_word_result_label.pack(fill="x", pady=(3, 0))

        # ביטול קולי
        tk.Label(f, text="🛑 מילת קוד לביטול קולי של אזעקה שקטה או רועשת",
                 font=T.F(9, "bold"), fg=T.RED, bg=T.BG,
                 anchor="e").pack(fill="x", padx=20, pady=(8, 0))
        self.reg_cancel_secret_var = tk.StringVar()
        tk.Entry(f, textvariable=self.reg_cancel_secret_var, font=T.F(11),
                 justify="right").pack(fill="x", padx=20, pady=(2, 4))
        cancel_secret_test_frame = tk.Frame(f, bg=T.BG)
        cancel_secret_test_frame.pack(fill="x", padx=20, pady=(0, 6))
        tk.Button(cancel_secret_test_frame, text="🎙️ בדוק בקול", font=T.F(8),
                  fg=T.TEXT, bg=T.CARD_BG_ALT, bd=0, padx=10, pady=5, cursor="hand2",
                  command=self._test_cancel_secret).pack(anchor="e")
        self.cancel_secret_result_label = tk.Label(
            cancel_secret_test_frame, text="בדיקת מיקרופון (לא חובה)", font=T.F(8),
            fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e", justify="right", wraplength=380
        )
        self.cancel_secret_result_label.pack(fill="x", pady=(3, 0))

        tk.Label(f, text="🕶️ מילת קוד כפייה (אופציונלית)", font=T.F(9, "bold"),
                 fg=T.BLUE, bg=T.BG, anchor="e").pack(fill="x", padx=20, pady=(8, 0))
        tk.Label(f, text="אם מכריחים אותך לבטל אירוע: הזן/י או אמור/י ביטוי זה. הוא ייראה כמו ביטול, אך ימשיך התראת מצוקה שקטה לאנשי הקשר.",
                 font=T.F(8), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e", justify="right",
                 wraplength=380).pack(fill="x", padx=20, pady=(0, 4))
        self.reg_duress_phrase_var = tk.StringVar()
        tk.Entry(f, textvariable=self.reg_duress_phrase_var, font=T.F(11),
                 justify="right").pack(fill="x", padx=20, pady=(2, 6))

        self._make_expandable(
            f, "מה קורה כשאומרים את מילות הקוד?",
            "מצוקה שקטה: מתחיל מעקב ומדווח למעגל המשפחה ולמוקד המתנדבים באפליקציה. "
            "אזעקה רועשת: מפעילה SOS מלא, סירנה ורישום אירוע במרכז הקהילה. "
            "ביטול קולי: מפסיק את שתי הפעולות ומעדכן את מעגלי הקשר באפליקציה. "
            "קוד כפייה אופציונלי: נראה כמו ביטול, אך מפעיל דיווח שקט. אינדיקטורי הפרטיות של מערכת ההפעלה אינם מוסתרים. "
            "הפקודות הקוליות מתומללות באמצעות שירות Google ולכן דורשות חיבור אינטרנט; "
            "אין להסתמך עליהן כתחליף לחיוג ישיר למוקדי החירום."
        )

        section_label("מעגל הביטחון - אופציונלי, אפשר לדלג")
        tk.Label(
            f, text="אפשר להירשם בלי אנשי קשר ולהוסיף אותם אחר כך (מעגל הביטחון). גם בלי אנשי קשר, "
                    "SOS נשלח למתנדבים זמינים בקרבתך. איש קשר יקבל התראות רק אחרי שאישר את ההזמנה.",
            font=T.F(8), fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e", justify="right", wraplength=380
        ).pack(fill="x", padx=20, pady=(0, 6))
        self.reg_phone_var = field("מספר הטלפון שלך (אופציונלי) - כדי שהזמנות שנשלחו למספר שלך יגיעו אליך")
        self.reg_invite_code_var = field("קיבלת קוד הזמנה? (אופציונלי, למשל ABCD-EFGH-JKMN)")
        tk.Label(f, text="הזמנת בני משפחה לפי שם המשתמש שלהם ב-LifeGuard (אופציונלי):", font=T.F(9),
                 fg=T.TEXT_SECONDARY, bg=T.BG, anchor="e").pack(fill="x", padx=20)
        self.contacts_container = tk.Frame(f, bg=T.BG)
        self.contacts_container.pack(fill="x", padx=20)
        self._add_contact_row()
        tk.Button(
            f, text="+ הוסף בן משפחה / חבר", font=T.F(9), fg=T.RED, bg=T.BG,
            bd=0, cursor="hand2", command=self._add_contact_row
        ).pack(anchor="e", padx=20, pady=(4, 8))

        if not voice_trigger_module.HAS_SR:
            tk.Label(
                f, text="⚠️ זיהוי קולי לא יהיה פעיל עד להתקנת SpeechRecognition ו-PyAudio. "
                        "אפשר עדיין להפעיל SOS מהכפתור באפליקציה.",
                font=T.F(8), fg=T.YELLOW, bg=T.BG, anchor="e",
                justify="right", wraplength=380
            ).pack(fill="x", padx=20, pady=(4, 8))

        section_label("תנאי שימוש")
        tos_text_widget = tk.Text(
            f, height=7, font=T.F(8), bg=T.BG, fg=T.TEXT_SECONDARY,
            wrap="word", bd=0, padx=8, pady=8
        )
        tos_text_widget.tag_configure("rtl", justify="right")
        tos_text_widget.insert("1.0", config.TERMS_OF_SERVICE_TEXT, "rtl")
        tos_text_widget.configure(state="disabled")
        tos_text_widget.pack(fill="x", padx=20)

        self.tos_accepted_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            f, text="קראתי ואני מסכים/ה לתנאי השימוש", variable=self.tos_accepted_var,
            font=T.F(9, "bold"), fg=T.TEXT, bg=T.BG, selectcolor=T.CARD_BG_ALT,
            activebackground=T.BG, activeforeground=T.TEXT, anchor="e"
        ).pack(fill="x", padx=20, pady=(6, 0))

        self.register_error_label = tk.Label(f, text="", font=T.F(9), fg=T.RED,
                                             bg=T.BG, wraplength=380, justify="right")
        self.register_error_label.pack(padx=20, pady=(8, 4))
        tk.Button(
            f, text="הירשם", font=T.F(11, "bold"), bg=T.BLUE, fg=T.ON_ACCENT,
            bd=0, padx=30, pady=8, cursor="hand2", command=self._do_register
        ).pack(pady=16)

    def _make_expandable(self, parent, short_text, full_text):
        """תווית מצומצמת + כפתור 'קרא עוד' שמרחיב לטקסט המלא. שומר על הטופס קצר וסרוק,
        בלי לאבד מידע למי שכן רוצה להעמיק."""
        frame = tk.Frame(parent, bg=T.BG)
        frame.pack(fill="x", padx=20, pady=(0, 10))

        tk.Label(frame, text=short_text, font=T.F(8), fg=T.TEXT_SECONDARY, bg=T.BG,
                 anchor="e", justify="right", wraplength=380).pack(fill="x")

        toggle_btn = tk.Button(
            frame, text="קרא עוד ▾", font=T.F(8), fg=T.RED, bg=T.BG,
            bd=0, cursor="hand2"
        )
        toggle_btn.pack(anchor="e", pady=(2, 0))

        full_label = tk.Label(frame, text=full_text, font=T.F(8), fg=T.TEXT_SECONDARY,
                               bg=T.BG, anchor="e", justify="right", wraplength=380)

        state = {"expanded": False}

        def _toggle():
            if state["expanded"]:
                full_label.pack_forget()
                toggle_btn.configure(text="קרא עוד ▾")
            else:
                full_label.pack(fill="x", pady=(4, 0), before=toggle_btn)
                toggle_btn.configure(text="הסתר ▴")
            state["expanded"] = not state["expanded"]

        toggle_btn.configure(command=_toggle)
        return frame

    def _test_voice_word(self):
        word = self.reg_voice_word_var.get().strip()
        if not word:
            self.voice_word_result_label.configure(text="הזן קודם ביטוי בשדה למעלה", fg=T.YELLOW)
            return
        if not voice_trigger_module.HAS_SR:
            self.voice_word_result_label.configure(
                text="⚠️ speech_recognition לא מותקן - לא ניתן לבדוק", fg=T.TEXT_SECONDARY
            )
            return
        self.voice_word_result_label.configure(text="🎙️ מקליט... אמור/אמרי את הביטוי עכשיו", fg=T.RED)
        threading.Thread(target=self._run_voice_test, args=(word, "voice_word"), daemon=True).start()

    def _test_silent_phrase(self):
        phrase = self.reg_silent_phrase_var.get().strip()
        if not phrase:
            self.silent_phrase_result_label.configure(text="הזן קודם ביטוי בשדה למעלה", fg=T.YELLOW)
            return
        if not voice_trigger_module.HAS_SR:
            self.silent_phrase_result_label.configure(
                text="⚠️ speech_recognition לא מותקן - לא ניתן לבדוק", fg=T.TEXT_SECONDARY
            )
            return
        self.silent_phrase_result_label.configure(text="🎙️ מקליט... אמור/אמרי את הביטוי עכשיו", fg=T.RED)
        threading.Thread(target=self._run_voice_test, args=(phrase, "silent_phrase"), daemon=True).start()

    def _test_cancel_secret(self):
        phrase = self.reg_cancel_secret_var.get().strip()
        if not phrase:
            self.cancel_secret_result_label.configure(text="הזן קודם ביטוי בשדה למעלה", fg=T.YELLOW)
            return
        if not voice_trigger_module.HAS_SR:
            self.cancel_secret_result_label.configure(
                text="⚠️ speech_recognition לא מותקן - לא ניתן לבדוק", fg=T.TEXT_SECONDARY
            )
            return
        self.cancel_secret_result_label.configure(text="🎙️ מקליט... אמור/אמרי את הביטוי עכשיו", fg=T.RED)
        threading.Thread(target=self._run_voice_test, args=(phrase, "cancel_secret"), daemon=True).start()

    def _run_voice_test(self, expected_phrase, which):
        matched, recognized, error = voice_trigger_module.test_phrase_recognition(expected_phrase)
        self.root.after(0, self._on_voice_test_result, which, matched, recognized, error)

    def _on_voice_test_result(self, which, matched, recognized, error):
        labels = {
            "voice_word": self.voice_word_result_label,
            "silent_phrase": self.silent_phrase_result_label,
            "cancel_secret": self.cancel_secret_result_label,
        }
        label = labels[which]
        if error:
            label.configure(text=f"⚠️ שגיאה בגישה למיקרופון: {error}", fg=T.RED)
            return
        if matched:
            label.configure(text=f'✅ זוהה בהצלחה: "{recognized}"', fg=T.GREEN)
            if which == "voice_word":
                self._voice_word_confirmed = True
            elif which == "silent_phrase":
                self._silent_phrase_confirmed = True
            else:
                self._cancel_secret_confirmed = True
        else:
            shown = recognized if recognized else "(לא זוהה דיבור בכלל)"
            label.configure(text=f'❌ לא זוהתה התאמה. נשמע: "{shown}" - אפשר לנסות שוב; הבדיקה אינה תנאי להרשמה', fg=T.RED)

    def _add_contact_row(self):
        if len(self.contact_rows) >= config.MAX_CONTACTS_PER_USER:
            return
        row_frame = tk.Frame(self.contacts_container, bg=T.CARD_BG, padx=10, pady=8)
        row_frame.pack(fill="x", pady=4)
        name_var = tk.StringVar()
        account_var = tk.StringVar()
        role_var = tk.StringVar(value="family")
        tk.Label(row_frame, text="שם:", font=T.F(8), fg=T.TEXT_SECONDARY, bg=T.CARD_BG).grid(row=0, column=0)
        tk.Entry(row_frame, textvariable=name_var, width=14, justify="right").grid(row=0, column=1, padx=4)
        tk.Label(row_frame, text="שם משתמש LifeGuard:", font=T.F(8), fg=T.TEXT_SECONDARY, bg=T.CARD_BG).grid(row=0, column=2)
        tk.Entry(row_frame, textvariable=account_var, width=20, justify="left").grid(row=0, column=3, padx=4)
        ttk.Combobox(row_frame, textvariable=role_var, values=("family", "volunteer"), state="readonly", width=10).grid(row=1, column=1, pady=5)
        tk.Label(row_frame, text="סוג קשר (family / volunteer):", fg=T.TEXT_SECONDARY, bg=T.CARD_BG).grid(row=1, column=0)
        self.contact_rows.append({"frame": row_frame, "name": name_var, "account": account_var, "role": role_var})

    def _do_register(self):
        username = self.reg_username_var.get().strip()
        password = self.reg_password_var.get()
        password_confirm = self.reg_password_confirm_var.get()
        if password != password_confirm:
            self.register_error_label.configure(text="אימות הסיסמה אינו תואם")
            return

        contacts = []
        for row in self.contact_rows:
            name = row["name"].get().strip()
            account = row["account"].get().strip()
            if not name or not account:
                continue
            contacts.append({"name": name, "account": account, "role": row["role"].get()})

        voice_word = self.reg_voice_word_var.get().strip()
        silent_phrase = self.reg_silent_phrase_var.get().strip()
        cancel_secret = self.reg_cancel_secret_var.get().strip()
        duress_phrase = self.reg_duress_phrase_var.get().strip()

        if not self.tos_accepted_var.get():
            self.register_error_label.configure(text="יש לאשר את תנאי השימוש לפני ההרשמה")
            return

        phone = self.reg_phone_var.get().strip()
        if phone and len(re.sub(r"\D", "", phone)) < 8:
            self.register_error_label.configure(text="מספר הטלפון לא תקין (אפשר להשאיר ריק)")
            return
        invite_code = re.sub(r"[^A-Za-z0-9]", "", self.reg_invite_code_var.get()).upper()
        if invite_code and len(invite_code) != 12:
            self.register_error_label.configure(text="קוד ההזמנה צריך להכיל 12 תווים (אפשר להשאיר ריק)")
            return

        success, message = auth_module.register_user(
            username, password, contacts,
            voice_secret_word=voice_word,
            silent_alarm_phrase=silent_phrase,
            secret_cancel_phrase=cancel_secret,
            terms_accepted=self.tos_accepted_var.get(),
            duress_phrase=duress_phrase,
            phone=phone,
        )

        if success:
            try:
                import worship_pref
                choice = next(k for k, v in worship_pref.OPTIONS.items() if v == self.reg_worship_var.get())
                worship_pref.save(username.strip().lower(), choice)
            except Exception:  # noqa: BLE001 - העדפה אופציונלית; כשל לא מבטל הרשמה
                pass
            if invite_code:
                # ימומש מול השרת מיד אחרי החיבור הראשון (מעגל הביטחון -> אישור). לא נחשב "מחובר" עד אז.
                try:
                    import incident_store
                    incident_store.set_setting(username.strip().lower(), "pending_invite_code", invite_code)
                except Exception:
                    pass
            self.register_error_label.configure(text="")
            self.root.unbind_all("<MouseWheel>")
            self.root.unbind_all("<Button-4>")
            self.root.unbind_all("<Button-5>")
            self.container.destroy()
            self._onboarding = OnboardingWindow(
                self.root, username, has_silent_alarm=True,
                on_done=lambda: self.on_success(username, password)
            )
        else:
            self.register_error_label.configure(text=message)


class OnboardingWindow:
    """
    מסך הסבר חד-פעמי שמוצג מיד אחרי הרשמה מוצלחת - עובר על כל פונקציה באפליקציה
    ומסביר מה היא עושה, לפני שהמשתמש נכנס למסך הראשי בפעם הראשונה.
    """

    _SECTIONS = [
        ("🚨 כפתור SOS", "מתחיל הערכת סיכון קצרה (רעש+מילים), ולפי התוצאה - ספירה לאחור עם "
         "אפשרות ביטול (סיכון נמוך), או הפעלה מיידית ללא ביטול (סיכון בינוני/גבוה)."),
        ("🆘 כפתור PANIC", "מדלג לגמרי על הערכת הסיכון והספירה - הפעלת SOS מיידית. השתמש/י "
         "בו כשאת/ה בטוח/ה שזה מצב אמיתי ורוצה תגובה הכי מהירה."),
        ("🔑 סיסמת החשבון", "אותה סיסמה משמשת להתחברות וגם לביטול אזעקה או מעקב שקט. אין קוד ביטול נוסף."),
        ("🤫 מילת קוד למצוקה שקטה", "אמירתה מפעילה מעקב ודיווח לאנשי הקשר בלי סירנה ובלי סימן גלוי על המסך."),
        ("🛑 מילת קוד לביטול קולי", "אמירתה מפסיקה מצוקה שקטה או אזעקה רועשת ומעדכנת את אנשי הקשר."),
        ("🕶️ מילת קוד כפייה", "ביטוי אופציונלי שנראה כמו ביטול, אך ממשיך דיווח שקט. יש להגדיר ולבדוק אותו מראש."),
        ("🚨 מילת קוד לאזעקה רועשת", "אמירתה מפעילה מיד SOS מלא, סירנה ודיווח לאנשי הקשר."),
        
        ("📢 מצב הרתעה קולית", "משמיע אזהרה חוזרת בעברית/אנגלית/ערבית שמזהירה שהמצב מתועד - "
         "בלי להפעיל סירנה/הקלטה/דיווח. יש גם הפעלה עצמאית מהמסך הראשי."),
        ("🎚️ מד כיול חי", "מציג רעש סביבתי אמיתי מהמיקרופון בזמן אמת - כלי לכייל את הרגישות "
         "לפני שסומכים על המערכת בשטח."),
        ("🗺️ מפה וניווט", "בזמן אירוע, מוצא תחנת משטרה/בי\"ח קרובה ומחשב מסלול הליכה אליה."),
        ("📷 צילום אוטומטי", "בזמן אירוע, מצלם כשמזוהות פנים ברורות או כשנראית סביבה - לא "
         "מצלם בחושך/כשהמצלמה מכוסה (למשל טלפון בכיס)."),
        ("🧪 בדוק שליחה לאנשי קשר", "שולח הודעת בדיקה אמיתית (לא אירוע!) לכל אנשי הקשר, "
         "בכל הערוצים המוגדרים - כדי לוודא שהשידור באמת עובד לפני חירום אמיתי."),
    ]

    def __init__(self, root, username, has_silent_alarm, on_done):
        self.root = root
        self.on_done = on_done
        self.container = tk.Frame(root, bg=T.BG)
        self.container.pack(fill="both", expand=True)

        tk.Label(self.container, text="🛡️ ברוך/ה הבא/ה!", font=T.F(20, "bold"),
                 fg=T.TEXT, bg=T.BG).pack(pady=(24, 4))
        tk.Label(self.container, text="לפני שממשיכים - אלה פקודות החירום והדרך לבטל אותן:",
                 font=T.F(10), fg=T.TEXT_SECONDARY, bg=T.BG).pack(pady=(0, 12))

        # ההסבר עצמו מוצג מיושר לצד ימין (קנבס בצד ימין, פס גלילה בצד שמאל) - כך שהתוכן
        # מתחיל מהצד שבו קוראים קודם בעברית, בהתאם למה שהתבקש.
        canvas = tk.Canvas(self.container, bg=T.BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(self.container, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=T.BG)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw", width=T.px(460))
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="left", fill="y")
        canvas.pack(side="right", fill="both", expand=True, padx=(0, 20))

        def _wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120) * 2), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        sections = list(self._SECTIONS)
        if not has_silent_alarm:
            sections = [s for s in sections if "מצוקה שקט" not in s[0]]

        for title, desc in sections:
            row = tk.Frame(scroll_frame, bg=T.CARD_BG, padx=14, pady=10)
            row.pack(fill="x", pady=4, padx=4)
            tk.Label(row, text=title, font=T.F(11, "bold"), fg=T.RED, bg=T.CARD_BG,
                     anchor="e", justify="right").pack(fill="x")
            tk.Label(row, text=desc, font=T.F(9), fg=T.TEXT_SECONDARY, bg=T.CARD_BG,
                     anchor="e", justify="right", wraplength=420).pack(fill="x", pady=(4, 0))

        tk.Button(
            self.container, text="✅ הבנתי, המשך לאפליקציה", font=T.F(11, "bold"),
            fg=T.ON_ACCENT, bg=T.BLUE, bd=0, padx=24, pady=10, cursor="hand2",
            command=self._finish,
        ).pack(pady=16)

    def _finish(self):
        self.container.destroy()
        self.on_done()
