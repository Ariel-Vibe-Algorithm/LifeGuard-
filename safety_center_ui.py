# -*- coding: utf-8 -*-
"""
safety_center_ui.py
מרכז בטיחות ומוכנות: תרחישי חירום עם צ'קליסט תגובה, צ'קליסטים של מוכנות, תוכנית בטיחות
אישית (נקודות מפגש, יעדים חלופיים, הוראות חשובות), ורשימת המקורות של כל ההנחיות.

הכל מקומי - זמין גם בלי אינטרנט (התוכן ב-content/, התוכנית ב-SQLite מקומי ומוצפן).
GuidancePanel משמש גם בתוך מסך ה-SOS (main.py) - שם הוא מוצג רק *אחרי* שה-SOS כבר פעיל.
"""

import tkinter as tk
from tkinter import ttk

import guidance_engine
import incident_store
import ui_theme as T


def _style_notebook(widget):
    style = ttk.Style(widget)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("LG.TNotebook", background=T.BG, borderwidth=0)
    style.configure("LG.TNotebook.Tab", background=T.CARD_BG, foreground=T.TEXT,
                    padding=(18, 10), font=T.F(12, "bold"))
    style.map("LG.TNotebook.Tab", background=[("selected", T.BLUE)], foreground=[("selected", T.ON_ACCENT)])


class GuidancePanel(tk.Frame):
    """בוחר תרחיש -> מציג צעדים קצרים + מספרי חירום + הערות כללים + מקור לכל צעד."""

    def __init__(self, master, username, context_provider=None, bg=None, compact=False,
                 on_scenario_selected=None, persist_checks=True, fg=None, cols=3, text_wrap=None):
        bg = bg or T.BG
        super().__init__(master, bg=bg)
        # עמודה צרה (מסך SOS, מתחת למפה): פחות עמודות ושבירת שורות צרה יותר
        self.cols = cols
        self.text_wrap = text_wrap
        # משטח של הערכה הפעילה (בהירה או כהה) -> טוקנים של הערכה; רקע חירום כהה ישן -> הצבעים הנגישים שלו
        themed = bg in (T.BG, T.CARD_BG, T.CARD_BG_ALT)
        dark_legacy = T._is_dark(bg) and not themed
        self.fg = fg or (T.TEXT_ON_DARK if dark_legacy else T.TEXT)
        self.muted = T.EMERGENCY_MUTED if dark_legacy else T.TEXT_MUTED
        self.warn_fg = "#ffd166" if dark_legacy else T.YELLOW   # ניגודיות על רקע החירום הכהה
        self.call_fg = "#ff8fa0" if dark_legacy else T.RED
        self.username = username
        self.context_provider = context_provider or (lambda: {})
        self.bg = bg
        self.compact = compact
        self.on_scenario_selected = on_scenario_selected
        self.persist_checks = persist_checks
        self.check_vars = {}
        self.current = None
        try:
            self.content = guidance_engine.load_content()
            self.load_error = None
        except guidance_engine.ContentError as exc:
            self.content, self.load_error = None, str(exc)
        self._build()

    def _build(self):
        if self.load_error:
            T.label(self, f"⚠️ לא ניתן לטעון הנחיות: {self.load_error}", 12, T.RED, self.bg,
                    wraplength=500).pack(pady=20)
            return
        T.label(self, "מה קורה? בחר/י:", 13 if self.compact else 14, self.fg, self.bg, bold=True,
                anchor="e").pack(fill="x", padx=8, pady=(4, 6))
        grid = tk.Frame(self, bg=self.bg)
        grid.pack(fill="x", padx=6)
        # במסך SOS (כרטיס מרכזי צר יותר מאז מעבר לפלטת הבית) 4 עמודות חתכו כותרות - 3 עמודות
        cols = self.cols
        btn_wrap = int((220 if cols >= 3 else 150) * T.get_scale())
        for i, sc in enumerate(guidance_engine.list_scenarios(self.content)):
            btn = T.big_button(grid, f"{sc['icon']}  {sc['title']}", lambda s=sc["id"]: self.show(s),
                               bg=T.CARD_BG_ALT, size=11 if self.compact else 12, pady=8, padx=8,
                               wraplength=btn_wrap)  # כותרת ארוכה נשברת לשורה ולא נחתכת
            btn.grid(row=i // cols, column=cols - 1 - (i % cols), sticky="nsew", padx=4, pady=4)
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1)

        self.calls_frame = tk.Frame(self, bg=self.bg)
        self.calls_frame.pack(fill="x", padx=8, pady=(10, 0))
        self.notes_label = T.label(self, "", 11, self.warn_fg, self.bg, wraplength=self.text_wrap or 560, anchor="e")
        self.notes_label.pack(fill="x", padx=10, pady=(6, 0))
        self.steps_frame = tk.Frame(self, bg=self.bg)
        self.steps_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.footer = T.label(self, "", 9, self.muted, self.bg, wraplength=self.text_wrap or 560, anchor="e")
        self.footer.pack(fill="x", padx=10, pady=(0, 6))

    def show(self, scenario_id):
        if self.content is None:
            return
        ctx = {}
        try:
            ctx = self.context_provider() or {}
        except Exception:
            ctx = {}
        result = guidance_engine.select_guidance(scenario_id, ctx, self.content)
        self.current = result
        for w in self.calls_frame.winfo_children() + self.steps_frame.winfo_children():
            w.destroy()
        if result["suppressed"]:
            self.notes_label.configure(text="")
            return
        T.label(self.calls_frame, f"{result['scenario']['icon']} {result['scenario']['title']}",
                15, self.fg, self.bg, bold=True, anchor="e").pack(fill="x")
        for call in result["calls"]:
            T.label(self.calls_frame, f"📞 {call['label']}: {call['number']}", 18, self.call_fg, self.bg,
                    bold=True, anchor="e").pack(fill="x")
        self.notes_label.configure(text="\n".join(result["notes"]))

        checklist_id = f"scenario:{scenario_id}"
        saved = {}
        if self.persist_checks and self.username:
            try:
                saved = incident_store.get_checklist_state(self.username, checklist_id)
            except Exception:
                saved = {}
        self.check_vars = {}
        for n, step in enumerate(result["steps"], start=1):
            row = tk.Frame(self.steps_frame, bg=T.CARD_BG, highlightbackground=T.BORDER, highlightthickness=1)
            row.pack(fill="x", pady=3)
            var = tk.BooleanVar(value=saved.get(step["id"], {}).get("done", False))
            self.check_vars[step["id"]] = var
            cb = tk.Checkbutton(
                row, text=f"{n}. {step['text']}", variable=var, font=T.F(12), fg=T.TEXT, bg=T.CARD_BG,
                selectcolor=T.CARD_BG_ALT, activebackground=T.CARD_BG, activeforeground=T.TEXT,
                anchor="e", justify="right", wraplength=self.text_wrap or (520 if self.compact else 640),
                highlightthickness=2, highlightcolor=T.FOCUS, takefocus=True,
                command=lambda sid=step["id"], v=var: self._toggle(checklist_id, sid, v),
            )
            cb.pack(fill="x", padx=6, pady=(6, 0))
            src = step.get("source_info") or {}
            src_txt = f"מקור: {src.get('org', '?')}"
            if src.get("retrieved"):
                src_txt += f" · {src['retrieved']}"
            if src.get("verification") == "not_checked":
                src_txt += " · ⚠️ לא אומת אוטומטית"
            if src.get("note"):
                src_txt += f" · {src['note']}"
            T.label(row, src_txt, 9, T.TEXT_MUTED, T.CARD_BG, anchor="e").pack(fill="x", padx=10, pady=(0, 4))
        self.footer.configure(text=f"{result['disclaimer']}\nגרסת תוכן {result['content_version']} · "
                                   f"{result['review_status']}")
        if self.on_scenario_selected:
            self.on_scenario_selected(scenario_id, result["scenario"]["title"])

    def _toggle(self, checklist_id, step_id, var):
        if not (self.persist_checks and self.username):
            return
        try:
            incident_store.set_checklist_item(self.username, checklist_id, step_id, var.get())
        except Exception:
            pass


class SafetyCenterWindow(tk.Toplevel):
    def __init__(self, master, username, context_provider=None, initial_tab=None):
        super().__init__(master)
        self.username = username
        self.title("LifeGuard | מרכז בטיחות ומוכנות")
        self.geometry("1000x860")
        self.minsize(720, 600)
        self.configure(bg=T.BG)
        self.bind("<Escape>", lambda e: self.destroy())
        _style_notebook(self)

        T.label(self, "🧭 מרכז בטיחות ומוכנות", 20, T.TEXT, T.BG, bold=True).pack(pady=(14, 2))
        T.label(self, "זמין גם בלי אינטרנט · Esc לסגירה · Ctrl+Tab למעבר בין לשוניות", 10,
                T.TEXT_MUTED, T.BG).pack()
        nb = ttk.Notebook(self, style="LG.TNotebook")
        nb.pack(fill="both", expand=True, padx=12, pady=10)
        nb.enable_traversal()
        self.nb = nb

        tab1, inner1 = self._scroll_tab(nb, "תרחישי חירום")
        GuidancePanel(inner1, username, context_provider).pack(fill="both", expand=True)
        tab2, inner2 = self._scroll_tab(nb, "מוכנות לחירום")
        self._build_checklists(inner2)
        tab3, inner3 = self._scroll_tab(nb, "התוכנית שלי")
        self._build_plan(inner3)
        tab4, inner4 = self._scroll_tab(nb, "מקורות")
        self._build_sources(inner4)
        if initial_tab:
            self.select_tab(initial_tab)

    def select_tab(self, title):
        for tab_id in self.nb.tabs():
            if self.nb.tab(tab_id, "text") == title:
                self.nb.select(tab_id)
                return

    def _scroll_tab(self, nb, title):
        outer, inner = T.scrollable(nb)
        nb.add(outer, text=title)
        return outer, inner

    # ---------- צ'קליסטים של מוכנות ----------
    def _build_checklists(self, parent):
        try:
            checklists = guidance_engine.get_checklists()
            content = guidance_engine.load_content()
        except guidance_engine.ContentError as exc:
            T.label(parent, str(exc), 12, T.RED).pack()
            return
        T.label(parent, "סמן/י מה כבר מוכן. ההתקדמות נשמרת במחשב.", 12, T.TEXT_SECONDARY,
                anchor="e").pack(fill="x", padx=12, pady=8)
        for cl in checklists:
            box = tk.Frame(parent, bg=T.CARD_BG, highlightbackground=T.BORDER, highlightthickness=1)
            box.pack(fill="x", padx=10, pady=6)
            header = tk.Frame(box, bg=T.CARD_BG)
            header.pack(fill="x", padx=8, pady=(8, 2))
            title = T.label(header, cl["title"], 14, T.TEXT, T.CARD_BG, bold=True, anchor="e")
            title.pack(side="right")
            progress = T.label(header, "", 11, T.GREEN, T.CARD_BG)
            progress.pack(side="left")
            state = {}
            try:
                state = incident_store.get_checklist_state(self.username, cl["id"])
            except Exception:
                pass
            vars_ = []

            def _update(cl=cl, vars_=vars_, progress=progress):
                done = sum(1 for v in vars_ if v.get())
                progress.configure(text=f"{done}/{len(vars_)} מוכן",
                                   fg=T.GREEN if done == len(vars_) else T.YELLOW)

            for item in cl["items"]:
                var = tk.BooleanVar(value=state.get(item["id"], {}).get("done", False))
                vars_.append(var)
                src = content["sources"].get(item["source"], {})
                text = item["text"]
                cb = tk.Checkbutton(
                    box, text=text, variable=var, font=T.F(12), fg=T.TEXT, bg=T.CARD_BG,
                    selectcolor=T.CARD_BG_ALT, activebackground=T.CARD_BG, activeforeground=T.TEXT,
                    anchor="e", justify="right", wraplength=720, highlightthickness=2,
                    highlightcolor=T.FOCUS,
                    command=lambda c=cl["id"], i=item["id"], v=var, u=_update: (
                        incident_store.set_checklist_item(self.username, c, i, v.get()), u()),
                )
                cb.pack(fill="x", padx=10, pady=1)
                T.label(box, f"מקור: {src.get('org', '?')} · {src.get('retrieved', '')}", 9,
                        T.TEXT_MUTED, T.CARD_BG, anchor="e").pack(fill="x", padx=14)
            _update()
            tk.Frame(box, bg=T.CARD_BG, height=6).pack()

    # ---------- תוכנית בטיחות אישית ----------
    def _build_plan(self, parent):
        try:
            plan = incident_store.get_safety_plan(self.username)
        except Exception as exc:
            T.label(parent, f"⚠️ טעינת התוכנית נכשלה: {exc}", 12, T.RED).pack()
            return
        T.label(parent, "תוכנית מוכנה מראש = פחות החלטות ברגע של לחץ. נשמר מוצפן במחשב זה.",
                12, T.TEXT_SECONDARY, anchor="e", wraplength=760).pack(fill="x", padx=12, pady=8)
        self.plan_rows = {"meeting_points": [], "fallback_destinations": []}
        for key, title, hint in (
            ("meeting_points", "📍 נקודות מפגש מוסכמות", "למשל: הכניסה לגן העירוני, בית סבתא"),
            ("fallback_destinations", "🏠 יעדים חלופיים (אם אי אפשר לחזור הביתה)", "למשל: חבר/ה, תחנת משטרה קרובה"),
        ):
            box = tk.Frame(parent, bg=T.CARD_BG, highlightbackground=T.BORDER, highlightthickness=1)
            box.pack(fill="x", padx=10, pady=6)
            T.label(box, title, 13, T.TEXT, T.CARD_BG, bold=True, anchor="e").pack(fill="x", padx=10, pady=(8, 0))
            T.label(box, f"שם · כתובת · הערה ({hint})", 10, T.TEXT_MUTED, T.CARD_BG, anchor="e").pack(fill="x", padx=10)
            rows_frame = tk.Frame(box, bg=T.CARD_BG)
            rows_frame.pack(fill="x", padx=8)
            for item in plan.get(key) or [{}]:
                self._add_place_row(rows_frame, key, item)
            T.big_button(box, "+ הוסף", lambda f=rows_frame, k=key: self._add_place_row(f, k, {}),
                         size=10, pady=4).pack(anchor="e", padx=10, pady=6)

        self.plan_texts = {}
        for key, title in (
            ("important_instructions", "📝 הוראות חשובות (מה לעשות, את מי לאסוף, מפתחות...)"),
            ("medical_notes", "💊 מידע רפואי למסירה למוקד (תרופות, אלרגיות, מצבים רפואיים)"),
            ("household_contacts", "☎️ טלפונים חשובים (גם למקרה שאין גישה לטלפון) - מומלץ להדפיס"),
        ):
            box = tk.Frame(parent, bg=T.CARD_BG, highlightbackground=T.BORDER, highlightthickness=1)
            box.pack(fill="x", padx=10, pady=6)
            T.label(box, title, 13, T.TEXT, T.CARD_BG, bold=True, anchor="e").pack(fill="x", padx=10, pady=(8, 2))
            txt = tk.Text(box, height=4, font=T.F(12), bg=T.CARD_BG_ALT, fg=T.TEXT, insertbackground=T.TEXT,
                          wrap="word", bd=0, highlightthickness=2, highlightcolor=T.FOCUS)
            txt.tag_configure("rtl", justify="right")
            txt.insert("1.0", plan.get(key) or "", "rtl")
            txt.pack(fill="x", padx=10, pady=(0, 10))
            self.plan_texts[key] = txt

        bottom = tk.Frame(parent, bg=T.BG)
        bottom.pack(fill="x", padx=10, pady=10)
        T.big_button(bottom, "💾 שמור תוכנית", self._save_plan, bg=T.RED_DARK, size=13).pack(side="right")
        self.plan_status = T.label(bottom, "", 12, T.GREEN)
        self.plan_status.pack(side="right", padx=12)
        if plan.get("updated_at"):
            import time
            T.label(parent, "עודכן לאחרונה: " + time.strftime("%d/%m/%Y %H:%M", time.localtime(plan["updated_at"])),
                    10, T.TEXT_MUTED, anchor="e").pack(fill="x", padx=14)

    def _add_place_row(self, frame, key, item):
        row = tk.Frame(frame, bg=T.CARD_BG)
        row.pack(fill="x", pady=2)
        vars_ = {}
        for field, width in (("name", 18), ("address", 26), ("notes", 22)):
            var = tk.StringVar(value=item.get(field, ""))
            tk.Entry(row, textvariable=var, font=T.F(11), width=width, justify="right", bg=T.CARD_BG_ALT,
                     fg=T.TEXT, insertbackground=T.TEXT, relief="flat", highlightthickness=2,
                     highlightcolor=T.FOCUS).pack(side="right", padx=3, ipady=4)
            vars_[field] = var
        entry = {"frame": row, "vars": vars_}

        def _remove():
            row.destroy()
            self.plan_rows[key].remove(entry)
        T.big_button(row, "✕", _remove, size=10, padx=8, pady=2).pack(side="right", padx=3)
        self.plan_rows[key].append(entry)

    def _save_plan(self):
        plan = {}
        for key, rows in self.plan_rows.items():
            plan[key] = [{f: v.get() for f, v in r["vars"].items()} for r in rows]
        for key, txt in self.plan_texts.items():
            plan[key] = txt.get("1.0", "end").strip()
        try:
            incident_store.save_safety_plan(self.username, plan)
            T.flash_feedback(self.plan_status, "✅ התוכנית נשמרה")
        except Exception as exc:
            T.flash_feedback(self.plan_status, f"❌ השמירה נכשלה: {exc}", T.RED, 10000)

    # ---------- מקורות ----------
    def _build_sources(self, parent):
        try:
            content = guidance_engine.load_content()
        except guidance_engine.ContentError as exc:
            T.label(parent, str(exc), 12, T.RED).pack()
            return
        T.label(parent, f"גרסת תוכן: {content['content_version']} · נסקר: {content['reviewed_on']}",
                12, T.TEXT, anchor="e").pack(fill="x", padx=12, pady=(10, 2))
        T.label(parent, content["review_status"], 11, T.YELLOW, anchor="e", wraplength=780).pack(fill="x", padx=12)
        T.label(parent, content["disclaimer"], 11, T.TEXT_SECONDARY, anchor="e", wraplength=780).pack(fill="x", padx=12, pady=(2, 10))
        levels = content["verification_levels"]
        for sid, src in content["sources"].items():
            box = tk.Frame(parent, bg=T.CARD_BG, highlightbackground=T.BORDER, highlightthickness=1)
            box.pack(fill="x", padx=10, pady=4)
            T.label(box, f"{src['org']} - {src['title']}", 12, T.TEXT, T.CARD_BG, bold=True, anchor="e").pack(fill="x", padx=10, pady=(6, 0))
            if src.get("url"):
                url = tk.Entry(box, font=T.F(10), fg=T.BLUE, bg=T.CARD_BG, relief="flat", justify="left",
                               readonlybackground=T.CARD_BG)
                url.insert(0, src["url"])
                url.configure(state="readonly")
                url.pack(fill="x", padx=10)
            color = T.YELLOW if src["verification"] != "content_checked" else T.GREEN
            T.label(box, f"נבדק: {src['retrieved']} · {levels.get(src['verification'], '')}"
                    + (f" · {src['note']}" if src.get("note") else ""),
                    10, color, T.CARD_BG, anchor="e", wraplength=760).pack(fill="x", padx=10, pady=(0, 6))
