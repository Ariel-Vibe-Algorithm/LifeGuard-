# -*- coding: utf-8 -*-
"""
first_aid_ui.py - מסך "עזרה ראשונה": שורת חיפוש אחת בולטת, תוצאות רק אחרי שמקלידים, ומדריך מלא.

- אין קטלוג/רשימת מצבים במסך הפתיחה (דרישת חבילת התוכן): כותבים מה קרה -> תוצאות.
- הכל מקומי (first_aid/library.py). המסך לא פונה לרשת: מצב החיבור נקרא מהמטמון של connectivity_module,
  המיקום מהמטמון של location_module. קישורי מקורות נפתחים רק בלחיצה יזומה.
- גיל: בחנק/החייאה, אם הגיל לא ידוע - שואלים לפני הצעדים. לא מערבבים מבוגר/ילד/תינוק.
- חיוג: המחשב לא מתקשר בעצמו. "חייג" מנסה לפתוח אפליקציית חיוג מקושרת (tel:), ואם אין - אומר זאת.
"""

import os
import threading
import time
import tkinter as tk
import webbrowser

import ui_theme as T
from first_aid import library as FA

PLACEHOLDER = "מה קרה? חפש מצב חירום או תסמין..."
NO_MATCH = "לא מצאנו מדריך מתאים. במקרה של סכנת חיים התקשרו 101 או בקשו ממישהו להזעיק עזרה."
SUGGESTIONS = ("חוסר הכרה", "קושי בנשימה", "דימום חמור")
URGENCY_COLORS = {"critical": "RED", "high": "YELLOW", "medium": "BLUE", "low": "TEXT_SECONDARY"}
REVIEW_HE = {"REQUIRES_REVIEW_BY_ISRAELI_EMERGENCY_MEDICINE_CLINICIAN_BEFORE_PUBLIC_RELEASE":
             "טיוטה - טעונה אישור רופא/ה לרפואה דחופה או פרמדיק/ית בישראל לפני פרסום לציבור",
             "pending_medical_review":
             "ממתין לבדיקה של גורם רפואי/מקצועי מוסמך בישראל - אינו הנחיה רשמית"}
POISON_CATEGORY = "חומרים והרעלות"
FAMILY_TITLES = {"choking": "חנק", "cpr": "החייאה / לא נושם"}
SEARCH_DEBOUNCE_MS = 220


def _color(name):
    return getattr(T, name, T.TEXT)


class FirstAidScreen(tk.Frame):
    def __init__(self, parent, app, lib=None, compact=False, bg=None):
        super().__init__(parent, bg=bg or T.BG)
        # compact: בתוך מסך ה-SOS (עמודה צרה) - גופנים ושוליים קטנים, שורות שנשברות במקום להיחתך
        self.compact = compact
        self.pad = 6 if compact else 18
        self.app = app
        self.lib = lib or FA.library()
        self.age = None                 # גיל שנבחר במסך (adult / child / infant) או None
        self.current = None             # מזהה המדריך הפתוח
        self._after = None
        self._last_results = None
        self._build()
        self.show_landing()

    # ------------------------------------------------------------ מבנה
    def _build(self):
        c, px = self.compact, self.pad
        top = tk.Frame(self, bg=self.cget("bg"))
        top.pack(fill="x", padx=px, pady=(4 if c else 14, 4))
        if not c:
            T.label(top, "🩹 עזרה ראשונה", 18, T.TEXT, bold=True).pack(side="right")
        self.net_label = T.label(top, "", 9 if c else 10, T.TEXT_MUTED)
        self.net_label.pack(side="right" if c else "left")

        # שדה חיפוש בסגנון iOS: משטח אפור בהיר, זכוכית מגדלת, בלי מסגרת; מסגרת כחולה דקה רק בפוקוס
        fbg = T.CARD_BG_ALT
        bar = tk.Frame(self, bg=fbg, highlightbackground=fbg, highlightcolor=T.BLUE, highlightthickness=2)
        bar.pack(fill="x", padx=px, pady=(4, 6))
        tk.Label(bar, text="🔍", font=T.F(12 if c else 16), fg=T.TEXT_MUTED, bg=fbg).pack(side="right", padx=(0, 10))
        self.query = tk.StringVar()
        self.entry = tk.Entry(bar, textvariable=self.query, font=T.F(13 if c else 20), justify="right", bd=0,
                              bg=fbg, fg=T.TEXT, insertbackground=T.BLUE, highlightthickness=0)
        self.entry.bind("<FocusIn>", lambda e: bar.configure(highlightbackground=T.BLUE), add="+")
        self.entry.bind("<FocusOut>", lambda e: bar.configure(highlightbackground=fbg), add="+")
        self.entry.pack(side="right", fill="x", expand=True, padx=8 if c else 12, ipady=5 if c else 10)
        self.entry.bind("<KeyRelease>", self._on_key)
        self.entry.bind("<Return>", lambda e: self.run_search())
        T.rtl_entry(self.entry, self.query)
        # טקסט עזר בתוך השורה (מוצג כשהשורה ריקה - גם כשהיא בפוקוס, כדי שיהיה ברור מה לכתוב)
        self.hint = tk.Label(bar, text="מה קרה? למשל: דימום, נחנק" if c else PLACEHOLDER, font=T.F(11 if c else 18),
                             fg=T.TEXT_MUTED, bg=fbg, cursor="xterm")
        self.hint.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.query.trace_add("write", lambda *a: self._update_hint())
        T.big_button(bar, "✕", self.clear, bg=T.CARD_BG_ALT, size=12, pady=4).pack(side="left", padx=4)
        self.mic_btn = None
        if _local_voice_available():
            self.mic_btn = T.big_button(bar, "🎤", self.voice_search, bg=T.CARD_BG_ALT, size=12, pady=4)
            self.mic_btn.pack(side="left", padx=4)

        if c:
            T.label(self, "גיל הנפגע/ת:", 10, T.TEXT_SECONDARY, self.cget("bg"), anchor="e").pack(fill="x", padx=px)
        ages = tk.Frame(self, bg=self.cget("bg"))
        ages.pack(fill="x", padx=px)
        if not c:
            T.label(ages, "גיל הנפגע/ת:", 11, T.TEXT_SECONDARY).pack(side="right")
        self.age_var = tk.StringVar(value="")
        # בורר מקטעים בסגנון iOS (segmented control): משטח אפור, המקטע הנבחר לבן
        seg = tk.Frame(ages, bg=T.CARD_BG_ALT, padx=2, pady=2)
        seg.pack(side="right", padx=(0, 6))
        for key, label in (("", "לא ידוע"),) + FA.AGE_CHOICES:
            tk.Radiobutton(seg, text=label, value=key, variable=self.age_var, command=self._on_age,
                           font=T.F(10 if c else 11), bg=T.CARD_BG_ALT, fg=T.TEXT, selectcolor=T.CARD_BG,
                           activebackground=T.CARD_BG_ALT, indicatoron=False, padx=6 if c else 12, pady=3, bd=0,
                           relief="flat", offrelief="flat", highlightthickness=0,
                           cursor="hand2").pack(side="right", padx=1)
        self.status = T.label(self, "", 10, T.TEXT_SECONDARY, anchor="e")
        self.status.pack(fill="x", padx=px)

        outer, self.body = T.scrollable(self, bg=T.BG)
        outer.pack(fill="both", expand=True, padx=px, pady=(4, 0))
        self._outer = outer
        if c:       # במסך SOS: מספרי החירום כבר מוצגים, ואין מקום לפס תחתון
            self._update_hint()
            return

        foot = tk.Frame(self, bg=T.NAVY)
        foot.pack(fill="x", side="bottom")
        nums = self.lib.emergency_numbers
        T.label(foot, f"🚑 מד״א {nums.get('mda', '101')}" + (f"   ☠️ מוקד הרעלות {nums['poison_center']}"
                                                            if nums.get("poison_center") else ""),
                12, T.ON_NAVY, T.NAVY, bold=True).pack(side="right", padx=14, pady=6)
        T.label(foot, "אינו תחליף ל-101, לטיפול מקצועי או להכשרה מעשית", 10, T.ON_NAVY_MUTED, T.NAVY).pack(side="left", padx=14)
        self._update_hint()

    def _update_hint(self):
        if T.entry_text(self.query.get()):
            self.hint.place_forget()
        else:
            self.hint.place(in_=self.entry, relx=1.0, rely=0.5, anchor="e")

    def query_text(self):
        return T.entry_text(self.query.get()).strip()

    def _wrap_all(self):
        """טקסט ארוך נשבר לפי רוחב האזור הנראה בפועל (לא נחתך בחלון צר / ב-DPI גבוה).
        הרוחב נלקח מה-canvas הגולל - התווית עצמה מדווחת את רוחבה הטבעי, ולכן autowrap רגיל לא מספיק."""
        labels = []

        def walk(w):
            for ch in w.winfo_children():
                if isinstance(ch, tk.Label) and int(float(str(ch.cget("wraplength") or 0))) > 0:
                    labels.append(ch)
                walk(ch)
        walk(self.body)
        self._wrap_labels = labels
        canvas = getattr(self._outer, "canvas", None)
        if canvas is not None and not getattr(self, "_wrap_bound", False):
            self._wrap_bound = True
            canvas.bind("<Configure>", lambda e: self._apply_wrap(e.width), add="+")
        if canvas is not None:
            self.update_idletasks()
            self._apply_wrap(canvas.winfo_width())

    def _apply_wrap(self, width):
        """שבירת שורות ידנית לפי רוחב בפיקסלים. לא wraplength של Tk: הטמעת ה-RTL (RLE...PDF) עוטפת שורה
        לוגית שלמה, וכשה-Tk שובר אותה בעצמו סוף המשפט "קופץ" לתחילת השורה (".ההמחשב ... קליט")."""
        if width <= 1:
            return
        from tkinter import font as tkfont
        wl = max(200, width - 70)
        for lbl in getattr(self, "_wrap_labels", []):
            try:
                orig = getattr(lbl, "_orig_text", None)
                if orig is None:
                    orig = lbl._orig_text = T.plain(lbl.cget("text"))
                f = tkfont.Font(font=lbl.cget("font"))
                lbl.configure(text=wrap_text(orig, wl, f.measure), wraplength=0)
            except tk.TclError:
                pass

    def on_show(self):
        self._refresh_net()
        self.entry.focus_set()

    def _refresh_net(self):
        try:
            import connectivity_module
            online = connectivity_module.status().get("online")      # מטמון בלבד - בלי בדיקת רשת
        except Exception:  # noqa: BLE001
            online = None
        self.net_label.configure(text="📴 אין אינטרנט - כל המדריכים זמינים במכשיר" if online is False
                                 else ("🟢 המדריכים שמורים במכשיר (עובדים גם בלי אינטרנט)" if online else
                                       "המדריכים שמורים במכשיר"))

    def _clear_body(self):
        for w in self.body.winfo_children():
            w.destroy()
        try:
            self._outer.canvas.yview_moveto(0)
        except (AttributeError, tk.TclError):
            pass

    # ------------------------------------------------------------ חיפוש
    def _on_key(self, event=None):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(SEARCH_DEBOUNCE_MS, self.run_search)

    def _on_age(self):
        self.age = self.age_var.get() or None
        if self.current:
            self.open_guide(self.current)
        elif self.query_text():
            self.run_search()

    def clear(self):
        self.query.set("")
        self.entry.focus_set()
        self.show_landing()

    def search(self, text):
        """לשימוש מכפתורי הצעה / קול: ממלא את השורה ומחפש."""
        self.query.set(text)
        self.run_search()

    def run_search(self):
        self._after = None
        q = self.query_text()
        self.current = None
        if not q:
            self.show_landing()
            return
        res = self.lib.search(q, age=self.age)
        if res["age"] and not self.age:
            self.age_var.set(res["age"])      # הגיל זוהה מהחיפוש ("תינוק נחנק") - מוצג ונשמר
            self.age = res["age"]
        self._last_results = res
        self.show_results(res)

    # ------------------------------------------------------------ מסכים
    def show_landing(self):
        self._clear_body()
        self.status.configure(text="")
        c, b = T.card(self.body)
        c.pack(fill="x", pady=8)
        T.label(b, "כתבו בשורה למעלה מה קרה - למשל \"נחנק\", \"דימום\", \"לא נושם\", \"כווייה\".", 13, T.TEXT,
                anchor="e", wraplength=760).pack(fill="x")
        T.label(b, "אפשר לכתוב במילים שלכם, גם עם שגיאות כתיב. לא צריך אינטרנט.", 11, T.TEXT_SECONDARY,
                anchor="e", wraplength=760).pack(fill="x", pady=(4, 0))
        self._emergency_box(self.body)
        self._disclaimer(self.body)
        self._wrap_all()

    def show_results(self, res):
        self._clear_body()
        results = res["results"]
        if not results:
            self.status.configure(text="")
            c, b = T.card(self.body)
            c.pack(fill="x", pady=8)
            T.label(b, NO_MATCH, 13, T.RED, anchor="e", wraplength=760, bold=True).pack(fill="x")
            row = tk.Frame(b, bg=T.CARD_BG)
            row.pack(fill="x", pady=(8, 0))
            T.label(row, "אולי:", 10, T.TEXT_SECONDARY).pack(side="right")
            for s in SUGGESTIONS:
                T.big_button(row, s, lambda q=s: self.search(q), bg=T.CARD_BG_ALT, size=10, pady=2).pack(
                    side="right", padx=3)
            self._emergency_box(self.body)
            self._wrap_all()
            return
        self.status.configure(text=("כמה מצבים מתאימים - בחרו את המתאים. (זו לא אבחנה)" if res["ambiguous"]
                                    else f"{len(results)} תוצאות"))
        shown_families = set()
        for r in results:
            fam = self.lib.family_of(r["id"])
            if fam and not self.age:
                if fam in shown_families:
                    continue
                shown_families.add(fam)
                self._family_card(fam)
                continue
            self._result_card(r)
        self._disclaimer(self.body)
        self._wrap_all()

    def _result_card(self, r):
        c, b = T.card(self.body)
        c.pack(fill="x", pady=4)
        for w in (c, b):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e, gid=r["id"]: self.open_guide(gid))
        head = tk.Frame(b, bg=T.CARD_BG)
        head.pack(fill="x")
        t = T.label(head, r["title"], 14, T.TEXT, bold=True)
        t.pack(side="right")
        t.bind("<Button-1>", lambda e, gid=r["id"]: self.open_guide(gid))
        T.label(head, f" {r['urgency_he']} ", 10, "white", _color(URGENCY_COLORS[r["urgency"]])).pack(side="left")
        s = T.label(b, r["summary"], 11, T.TEXT_SECONDARY, anchor="e", wraplength=760)
        s.pack(fill="x")
        s.bind("<Button-1>", lambda e, gid=r["id"]: self.open_guide(gid))
        if r.get("age_note"):
            T.label(b, "ℹ️ " + r["age_note"], 10, T.YELLOW, anchor="e", wraplength=760).pack(fill="x")
        T.big_button(b, "פתח מדריך", lambda gid=r["id"]: self.open_guide(gid), size=10, pady=3).pack(anchor="w")

    def _family_card(self, fam):
        """חנק / החייאה בלי גיל ידוע: בוחרים גיל לפני שרואים צעדים (הטכניקה שונה)."""
        c, b = T.card(self.body)
        c.pack(fill="x", pady=4)
        head = tk.Frame(b, bg=T.CARD_BG)
        head.pack(fill="x")
        T.label(head, FAMILY_TITLES[fam], 14, T.TEXT, bold=True).pack(side="right")
        T.label(head, " מסכן חיים ", 10, "#FFFFFF", T.SOS_FILL).pack(side="left")
        T.label(b, "הטיפול שונה לפי גיל. מה גיל הנפגע/ת?", 12, T.TEXT, anchor="e").pack(fill="x", pady=(4, 4))
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x")
        any_id = next(iter(FA.AGE_FAMILIES[fam].values()))
        for key, label in FA.AGE_CHOICES:
            T.primary_button(row, label, lambda k=key, g=any_id: self._choose_age(k, g), size=12, pady=6).pack(
                side="right", padx=4)

    def _choose_age(self, age, gid):
        self.age = age
        self.age_var.set(age)
        self.open_guide(self.lib.resolve(gid, age))

    def _age_prompt(self, gid):
        self._clear_body()
        g = self.lib.guide(gid)
        c, b = T.card(self.body)
        c.pack(fill="x", pady=8)
        T.label(b, g["title"], 16, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(b, "לפני ההוראות: מה גיל הנפגע/ת? הטכניקה שונה בין מבוגר, ילד ותינוק.", 13, T.RED, anchor="e",
                wraplength=760, bold=True).pack(fill="x", pady=6)
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x")
        for key, label in FA.AGE_CHOICES:
            T.primary_button(row, label, lambda k=key: self._choose_age(k, gid), size=13, pady=8).pack(
                side="right", padx=4)
        self._emergency_box(self.body)
        self._wrap_all()

    def open_guide(self, gid):
        g = self.lib.guide(gid)
        if g is None:
            return
        if self.lib.needs_age(gid, self.age):
            self.current = None
            self._age_prompt(gid)
            return
        gid = self.lib.resolve(gid, self.age)
        g = self.lib.guide(gid)
        self.current = gid
        self._clear_body()
        self.status.configure(text="")
        nav = tk.Frame(self.body, bg=T.BG)
        nav.pack(fill="x")
        if self._last_results and self._last_results["results"]:
            T.big_button(nav, "→ חזרה לתוצאות", lambda: self.show_results(self._last_results), bg=T.CARD_BG_ALT,
                         size=10, pady=3).pack(side="right")

        # 1. כותרת ודחיפות
        head = tk.Frame(self.body, bg=T.BG)
        head.pack(fill="x", pady=(6, 2))
        T.label(head, g["title"], 15 if self.compact else 20, T.TEXT, bold=True).pack(side="right")
        T.label(head, f" {FA.URGENCY_HE[g['urgency']]} ", 12, "white", _color(URGENCY_COLORS[g["urgency"]]),
                bold=True).pack(side="right", padx=10)
        # 2. אזהרת חירום בולטת
        crit = g["urgency"] in ("critical", "high")
        warn = tk.Frame(self.body, bg=T.RED if crit else T.YELLOW)
        warn.pack(fill="x", pady=4)
        T.label(warn, "🚨 " + g["call_emergency_now"], 14 if crit else 12, "white", warn.cget("bg"), bold=True,
                anchor="e", wraplength=780).pack(fill="x", padx=12, pady=8)
        # נוהל בטיחות (אש, גז, תקיפה, שיטפון, התרעות) - מופרד במפורש מעזרה ראשונה רפואית
        if g.get("kind") == "safety":
            kb = tk.Frame(self.body, bg=T.NAVY)
            kb.pack(fill="x", pady=2)
            T.label(kb, "🛡️ נוהל בטיחות - לא טיפול רפואי"
                        + (f" · נדרש אישור: {g['authority_review']}" if g.get("authority_review") else ""),
                    11, T.ON_NAVY, T.NAVY, bold=True, anchor="e").pack(fill="x", padx=10, pady=4)
        # תוכן שמשתנה ומחייב מקור רשמי עדכני (התרעות טילים): לא מקבעים זמנים/מקלטים
        od = g.get("official_dynamic")
        if od:
            ob = tk.Frame(self.body, bg=T.YELLOW)
            ob.pack(fill="x", pady=2)
            T.label(ob, f"📢 המקור המחייב: {od['authority']}. {od['text']}", 12, T.TEXT, T.YELLOW, bold=True,
                    anchor="e", wraplength=760).pack(fill="x", padx=10, pady=6)
        # גיל (6 בדרישות - מוצג כאן כדי שיראו לפני הצעדים)
        note = self.lib.age_note(gid, self.age)
        groups = ", ".join(FA.AGE_HE[a] for a in g["age_groups"]) or "לא צוין בחבילה"
        age_box = tk.Frame(self.body, bg=T.CARD_BG_ALT)
        age_box.pack(fill="x", pady=2)
        T.label(age_box, f"👤 מיועד ל: {groups}" + (f"  ·  נבחר: {dict(FA.AGE_CHOICES)[self.age]}" if self.age else ""),
                11, T.TEXT, T.CARD_BG_ALT, anchor="e").pack(fill="x", padx=10, pady=(4, 0))
        if note:
            T.label(age_box, "⚠️ " + note, 12, T.RED, T.CARD_BG_ALT, bold=True, anchor="e", wraplength=760).pack(
                fill="x", padx=10)
        tk.Frame(age_box, bg=T.CARD_BG_ALT, height=4).pack()
        # 3-5
        self._section("✅ מה עושים עכשיו", g["what_to_do"], numbered=True, size=14)
        self._section("⛔ מה אסור לעשות", g["do_not"], color=T.RED)
        if g["red_flags"]:
            self._section("🚑 מתי חייבים להזעיק עזרה", g["red_flags"])
        else:   # חבילת הרחבה: "מתי חירום" הוא שדה emergency (מוצג גם בפס האדום) - כאן כסעיף קבוע
            self._section("🚑 מתי חייבים להזעיק עזרה", [g["call_emergency_now"]])
        if g.get("recognition"):
            self._section("🔎 סימנים לזיהוי", g["recognition"])
        if g.get("important_notes"):
            self._section("📝 חשוב לדעת", g["important_notes"])
        if g.get("setting"):
            T.label(self.body, "📍 רלוונטי ל: " + " · ".join(g["setting"]), 10, T.TEXT_SECONDARY,
                    anchor="e", wraplength=760).pack(fill="x", pady=2)
        rel = g.get("overlaps")
        if rel and self.lib.guide(rel):
            rr = tk.Frame(self.body, bg=T.BG)
            rr.pack(fill="x", pady=2)
            T.label(rr, "מדריך קשור:", 10, T.TEXT_SECONDARY).pack(side="right")
            T.big_button(rr, self.lib.guide(rel)["title"], lambda r=rel: self.open_guide(r), bg=T.CARD_BG_ALT,
                         size=10, pady=2).pack(side="right", padx=4)
        if g["category"] == POISON_CATEGORY or gid in ("poison_ingestion", "chemical_exposure"):
            pc = self.lib.emergency_numbers.get("poison_center")
            if pc:
                box = tk.Frame(self.body, bg=T.CARD_BG_ALT)
                box.pack(fill="x", pady=4)
                T.label(box, f"☠️ המכון הארצי למידע בהרעלות (רמב״ם): {pc} - 24/7. במצב מסכן חיים: 101.", 12, T.TEXT,
                        T.CARD_BG_ALT, bold=True, anchor="e").pack(fill="x", padx=10, pady=6)
        self._emergency_box(self.body, safety=g.get("kind") == "safety")
        self._meta(g)
        self._disclaimer(self.body)
        self._wrap_all()

    def _section(self, title, items, numbered=False, color=None, size=12):
        c, b = T.card(self.body)
        c.pack(fill="x", pady=4)
        T.label(b, title, 14, color or T.TEXT, bold=True, anchor="e").pack(fill="x")
        for i, text in enumerate(items, 1):
            T.label(b, (f"{i}. " if numbered else "• ") + text, size, T.TEXT, anchor="e", wraplength=780,
                    justify="right").pack(fill="x", pady=2)

    def _meta(self, g):
        """מקורות וסטטוס אישור - משני וקומפקטי."""
        box = tk.Frame(self.body, bg=T.BG)
        box.pack(fill="x", pady=(8, 2))
        T.label(box, f"סטטוס: {REVIEW_HE.get(g['medical_review_status'], g['medical_review_status'])}", 9,
                T.YELLOW, anchor="e", wraplength=780).pack(fill="x")
        origin = (f"חבילת הרחבה {g['package_id']} גרסה {g['package_version']}" if g.get("package_id")
                  else f"מאגר בסיס גרסה {self.lib.meta.get('schema_version', '?')}")
        T.label(box, f"עדכון תוכן: {g['content_updated']} · {origin} · מזהה: {g['id']}", 9, T.TEXT_MUTED,
                anchor="e", wraplength=760).pack(fill="x")
        for s in self.lib.sources_for(g["id"]):
            row = tk.Frame(box, bg=T.BG)
            row.pack(fill="x")
            T.label(row, f"מקור: {s.get('title', s['id'])}", 9, T.TEXT_MUTED, anchor="e").pack(side="right")
            if s.get("url"):
                lnk = T.label(row, "(קישור - דורש אינטרנט)", 9, T.BLUE)
                lnk.configure(cursor="hand2")
                lnk.bind("<Button-1>", lambda e, u=s["url"]: webbrowser.open(u))
                lnk.pack(side="right", padx=4)

    def _emergency_box(self, parent, safety=False):
        c, b = T.card(parent)
        c.pack(fill="x", pady=6)
        if self.compact:
            T.label(b, "בסכנת חיים:", 11, T.TEXT, bold=True, anchor="e").pack(fill="x")
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x")
        if not self.compact:
            T.label(row, "בסכנת חיים:", 12, T.TEXT, bold=True).pack(side="right")
        nums = self.lib.emergency_numbers
        T.primary_button(row, f"📞 מד״א {nums.get('mda', '101')}", lambda: self.dial(nums.get("mda", "101")),
                         size=12, pady=4).pack(side="right", padx=6)
        if safety:   # נהלי בטיחות: משטרה / כבאות לפי האירוע
            for key, label in (("police", "👮 משטרה"), ("fire_and_rescue", "🚒 כבאות")):
                if nums.get(key):
                    T.big_button(row, f"{label} {nums[key]}", lambda n=nums[key]: self.dial(n), size=10,
                                 pady=4).pack(side="right", padx=4)
        if nums.get("poison_center"):
            T.big_button(row, f"☠️ הרעלות {nums['poison_center']}", lambda: self.dial(nums["poison_center"]),
                         size=10, pady=4).pack(side="right", padx=4)
        self.dial_note = T.label(b, "המחשב לא מתקשר בעצמו: החיוג נעשה מהטלפון. בלי קליטה סלולרית - בקשו ממישהו "
                                    "להזעיק עזרה או חפשו טלפון/נקודת קליטה.", 9, T.TEXT_MUTED, anchor="e",
                                 wraplength=760)
        self.dial_note.pack(fill="x", pady=(4, 0))
        loc = _last_location()
        if loc:
            T.label(b, loc, 10, T.TEXT_SECONDARY, anchor="e", wraplength=760).pack(fill="x")

    def dial(self, number):
        """tel: -> אפליקציית חיוג מקושרת (למשל Phone Link). אם אין - אומרים, לא מעמידים פנים."""
        digits = "".join(ch for ch in number if ch.isdigit())
        try:
            os.startfile(f"tel:{digits}")      # noqa: S606 - פעולה יזומה של המשתמש/ת
            self.dial_note.configure(text=f"נפתחה אפליקציית החיוג ל-{number} (אם מקושר טלפון). אם לא - חייגו מהטלפון.",
                                     fg=T.TEXT_SECONDARY)
        except (OSError, AttributeError):
            self.dial_note.configure(text=f"אין במחשב הזה אפליקציית חיוג. חייגו {number} מהטלפון.", fg=T.RED)

    def _disclaimer(self, parent):
        T.label(parent, "⚕️ " + self.lib.disclaimer, 9, T.TEXT_MUTED, anchor="e", wraplength=780).pack(fill="x",
                                                                                                       pady=(8, 12))

    # ------------------------------------------------------------ קול (מקומי בלבד)
    def voice_search(self):
        if self.mic_btn is None:
            return
        self.mic_btn.configure(state="disabled", text="🎙️")
        self.status.configure(text="מקשיב 4 שניות... (זיהוי דיבור במחשב, בלי אינטרנט)")
        box = {}

        def work():
            try:
                import numpy as np
                import sounddevice as sd
                import speech_local
                sr = 16000
                audio = sd.rec(int(4 * sr), samplerate=sr, channels=1, dtype="float32")
                sd.wait()
                box["text"] = speech_local.transcribe(np.asarray(audio).reshape(-1), sr, "he") or ""
            except Exception as exc:  # noqa: BLE001
                box["error"] = str(exc)

        def poll():
            if not self.winfo_exists():
                return
            if "text" in box or "error" in box:
                self.mic_btn.configure(state="normal", text="🎤")
                if box.get("text"):
                    self.search(box["text"])
                else:
                    self.status.configure(text="לא זוהה דיבור - כתבו בשורה" + (f" ({box['error']})" if box.get("error")
                                                                                else ""))
                return
            self.after(150, poll)
        threading.Thread(target=work, daemon=True).start()
        self.after(150, poll)


def _local_voice_available():
    """כפתור קול רק אם יש מודל עברי מקומי (sherpa-onnx) - בלי זיהוי בענן."""
    try:
        import speech_local
        import sounddevice  # noqa: F401
        return bool(speech_local.is_available("he"))
    except Exception:  # noqa: BLE001
        return False


def _last_location():
    """המיקום האחרון שנשמר במחשב (בלי פנייה לרשת), עם זמן ודיוק - כדי להקריא למוקד."""
    try:
        import location_module
        loc = location_module.get_last_known()
    except Exception:  # noqa: BLE001
        return None
    if not loc or loc.get("lat") is None:
        return None
    ts = loc.get("timestamp")
    when = time.strftime("%H:%M", time.localtime(ts)) if ts else "?"
    acc = loc.get("accuracy_m_estimate") or loc.get("accuracy_m")
    kind = "GPS" if loc.get("is_precise") else "משוער (לא GPS)"
    return (f"📍 המיקום האחרון שנשמר: {loc['lat']:.5f}, {loc['lon']:.5f} · {kind}"
            + (f" · דיוק ~{int(acc)} מ'" if acc else "") + f" · נמדד ב-{when}")


MAX_LINE_CHARS = 90   # Tk ב-Windows מצייר שורת RTL ארוכה (~110+ תווים) בחלקים בסדר שגוי - נמדד במסך הזה


def wrap_text(text, width, measure, max_chars=MAX_LINE_CHARS):
    """שבירה חמדנית לפי מילים: כל שורה עד width פיקסלים ועד max_chars תווים. שורות קיימות נשמרות."""
    out = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            cand = f"{line} {word}" if line else word
            if line and (measure(cand) > width or len(cand) > max_chars):
                out.append(line)
                line = word
            else:
                line = cand
        out.append(line)
    return "\n".join(out)
