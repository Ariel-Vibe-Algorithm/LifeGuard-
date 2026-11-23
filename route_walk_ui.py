# -*- coding: utf-8 -*-
"""
route_walk_ui.py - "צילום מסלול הליכה": מסרטון + מסלול GPS לתצפיות על הרחובות.

הזרימה:
1. הסבר + הסכמה (חובה לעיבוד; שליחה לשרת - הסכמה נפרדת ואופציונלית).
2. בחירת סרטון (מהטלפון) וקובץ מסלול GPX (מכל אפליקציית GPS).
3. ניתוח במחשב הזה: טשטוש פנים -> CLIP -> מאפיינים + בהירות נמדדת. שום תמונה לא נשמרת.
4. תיוג קצר (אופציונלי): "מואר/חשוך", "הרגשתי בטוח/לא" על כמה פריימים מטושטשים - אלה התיוגים
   שמהם המודל לומד לסווג טוב יותר.
5. שמירה מקומית (לאימון עתידי) + שליחה לשרת (מספרים בלבד) אם הוסכם. אפשר למחוק הכול בכל רגע.
"""

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog

import config
import ui_theme as T

CONSENT_VERSION = "walk-1.1"
CONSENT_TEXT = (
    "• העיבוד מתבצע במחשב הזה. פנים מטושטשות לפני הניתוח, ושום תמונה מהסרטון לא נשמרת ולא נשלחת.\n"
    "• נשמרים רק מספרים: מיקום, בהירות שנמדדה, הערכות מודל ותיוגים שלך.\n"
    "• תחילת המסלול וסופו (150-400 מ', משתנה בכל הליכה) לא נשלחים לשרת - כדי לא לחשוף בית או עבודה.\n"
    "• הערכות המודל הן השערות לא מאומתות - הן לא מוצגות כציון בטיחות של רחוב.\n"
    "• אל תצלמו בתוך בתים/חצרות פרטיות, ואל תכוונו את המצלמה לאנשים. לוחיות רישוי אינן מטושטשות אוטומטית.\n"
    "• בטיחות: אל תצלמו כשאתם מרגישים בסכנה, שימו לב לדרך ולתנועה, ואל תלכו במיוחד למקומות חשוכים בשביל הצילום.\n"
    "• אחרי הניתוח מומלץ למחוק את הסרטון המקורי מהטלפון.\n"
    "• אפשר למחוק בכל רגע את הנתונים ששלחת לשרת ואת הנתונים שנשמרו במחשב.")
TRIM_ENDPOINTS_M = 150


def trim_endpoints(obs, meters=None, rng=None):
    """מסיר תצפיות קרובות לנקודת ההתחלה/הסיום של ההליכה (בית/עבודה) לפני שליחה.
    רדיוס *אקראי* לכל הליכה (150-400 מ'): ברדיוס קבוע הבית שוחזר מכמה הליכות בטעות חציונית של
    26 מ' - הוא מרכז המעגל שהתצפיות "נחתכות" סביבו (סקירה 2)."""
    import random
    from research_tools.street_vision import haversine_m
    if meters is None:
        meters = (rng or random.SystemRandom()).uniform(TRIM_ENDPOINTS_M, 400)
    located = [o for o in obs if o.get("lat") is not None]
    if not located:
        return []
    a, b = (located[0]["lat"], located[0]["lon"]), (located[-1]["lat"], located[-1]["lon"])
    return [o for o in located
            if haversine_m(a, (o["lat"], o["lon"])) > meters and haversine_m(b, (o["lat"], o["lon"])) > meters]
SEND_TEXT = "אני מסכים/ה לשלוח את הנתונים המספריים לשרת LifeGuard לצורך מחקר על בטיחות רחובות"


def walks_dir(username):
    d = os.path.join(config.DATA_DIR, "walks", username)
    os.makedirs(d, exist_ok=True)
    return d


class RouteWalkWindow(tk.Toplevel):
    MAX_LABEL_FRAMES = 8

    def __init__(self, master, app, analyzer_factory=None):
        super().__init__(master)
        self.app = app
        self.analyzer_factory = analyzer_factory
        self.title("LifeGuard | צילום מסלול הליכה")
        self.geometry("760x820")
        self.configure(bg=T.BG)
        self.video = self.gpx = None
        self.obs, self.thumbs, self.labels = [], [], {}
        import random
        self.trim_m = random.SystemRandom().uniform(TRIM_ENDPOINTS_M, 400)   # רדיוס אחד להליכה הזו
        self._photos = []

        outer, body = T.scrollable(self)
        outer.pack(fill="both", expand=True, padx=14, pady=10)
        outer.bind_all_keys(self)

        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 10))
        T.label(b, "📹 צילום מסלול הליכה", 16, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(b, "צלמו בטלפון את הדרך תוך כדי הליכה, והפעילו במקביל אפליקציית GPS ששומרת קובץ GPX. "
                   "המערכת תזהה מאפייני רחוב (תאורה, מדרכה, אנשים, תחזוקה, שדה ראייה) ותלמד מהתיוגים שלכם.",
                11, T.TEXT_SECONDARY, anchor="e", wraplength=680).pack(fill="x", pady=(4, 8))
        T.label(b, CONSENT_TEXT, 10, T.TEXT, anchor="e", wraplength=680, justify="right").pack(fill="x")
        self.consent_var = tk.BooleanVar(value=False)
        self.adult_var = tk.BooleanVar(value=False)
        self.send_var = tk.BooleanVar(value=False)
        tk.Checkbutton(b, text="אני בן/בת 18 ומעלה", variable=self.adult_var,
                       command=self._update_buttons, bg=T.CARD_BG, font=T.F(11), anchor="e").pack(fill="x", pady=(6, 0))
        tk.Checkbutton(b, text="קראתי ואני מסכים/ה לעיבוד הסרטון כמתואר", variable=self.consent_var,
                       command=self._update_buttons, bg=T.CARD_BG, font=T.F(11), anchor="e").pack(fill="x")
        tk.Checkbutton(b, text=SEND_TEXT, variable=self.send_var, bg=T.CARD_BG, font=T.F(10),
                       anchor="e", wraplength=640, justify="right").pack(fill="x")

        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 10))
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x")
        T.big_button(row, "🎬 בחר סרטון", self.choose_video, size=11).pack(side="right", padx=3)
        T.big_button(row, "🛰️ בחר מסלול GPX", self.choose_gpx, size=11).pack(side="right", padx=3)
        self.files_label = T.label(b, "לא נבחרו קבצים", 10, T.TEXT_MUTED, anchor="e", wraplength=680)
        self.files_label.pack(fill="x", pady=(6, 0))
        self.analyze_btn = T.primary_button(b, "🔍 נתח את המסלול", self.analyze, size=12, pady=6)
        self.analyze_btn.pack(anchor="e", pady=(8, 0))
        self.status = T.label(b, "", 11, T.TEXT_SECONDARY, anchor="e", wraplength=680)
        self.status.pack(fill="x", pady=(6, 0))

        self.results_card, self.results = T.card(body)
        self.label_card, self.label_body = T.card(body)

        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 10), side="bottom")
        T.big_button(b, "🗑️ מחק את ההליכות שלי מהשרת", self.delete_mine, size=10).pack(anchor="e")
        T.big_button(b, "🗑️ מחק את ההליכות שנשמרו במחשב", self.delete_local, size=10).pack(anchor="e", pady=(4, 0))
        self.delete_status = T.label(b, "", 10, T.TEXT_MUTED, anchor="e")
        self.delete_status.pack(fill="x")
        self._update_buttons()

    # ---------------------------------------------------------- קבצים
    def _update_buttons(self):
        ok = self.consent_var.get() and self.adult_var.get() and self.video and self.gpx
        self.analyze_btn.configure(state="normal" if ok else "disabled")

    def _show_files(self):
        parts = [f"סרטון: {os.path.basename(self.video)}" if self.video else "סרטון: —",
                 f"מסלול: {os.path.basename(self.gpx)}" if self.gpx else "מסלול: —"]
        self.files_label.configure(text=" · ".join(parts))
        self._update_buttons()

    def choose_video(self):
        p = filedialog.askopenfilename(parent=self, title="בחר סרטון הליכה",
                                       filetypes=[("וידאו", "*.mp4 *.mov *.avi *.mkv *.m4v"), ("הכול", "*.*")])
        if p:
            self.video = p
            self._show_files()

    def choose_gpx(self):
        p = filedialog.askopenfilename(parent=self, title="בחר קובץ מסלול", filetypes=[("GPX", "*.gpx")])
        if p:
            self.gpx = p
            self._show_files()

    # ---------------------------------------------------------- ניתוח
    def analyze(self):
        if not (self.consent_var.get() and self.adult_var.get() and self.video and self.gpx):
            return
        self.analyze_btn.configure(state="disabled")
        self.status.configure(text="⏳ מנתח במחשב הזה (טשטוש פנים, זיהוי מאפייני רחוב)... זה יכול לקחת כמה דקות.",
                              fg=T.TEXT_SECONDARY)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            from research_tools import street_vision as S
            track = S.parse_gpx(self.gpx)
            if len(track) < 2:
                raise ValueError("בקובץ ה-GPX אין מספיק נקודות עם זמן")
            an = self.analyzer_factory() if self.analyzer_factory else S.StreetAnalyzer()
            thumbs = []
            walk_id = time.strftime("local_%Y%m%d_%H%M%S")
            obs = an.analyze_walk(self.video, track, walk_id=walk_id, log=lambda m: None, thumbs=thumbs)
            cells = S.aggregate(obs)
            path = os.path.join(walks_dir(self.app.username), walk_id + ".jsonl")
            S.save_observations(obs, path)
            self.app._post(self._done, obs, thumbs, cells, path)
        except ImportError as exc:
            self.app._post(self._fail, f"רכיבי המחקר לא מותקנים ({exc}). "
                                       "התקנה: pip install -r research/requirements-research.txt והורדת המודלים.")
        except FileNotFoundError as exc:
            self.app._post(self._fail, f"מודל חסר: {exc}")
        except Exception as exc:  # noqa: BLE001 - מוצג למשתמש
            self.app._post(self._fail, f"הניתוח נכשל: {exc}")

    def _fail(self, text):
        if self.winfo_exists():
            self.status.configure(text="❗ " + text, fg=T.RED)
            self._update_buttons()

    def _done(self, obs, thumbs, cells, path):
        if not self.winfo_exists():
            return
        from research_tools import street_vision as S
        self.obs, self.thumbs, self.saved_path = obs, thumbs, path
        located = [o for o in obs if o["lat"] is not None]
        faces = sum(o["faces_blurred"] for o in obs)
        self.status.configure(text=f"✅ נותחו {len(obs)} פריימים ({len(located)} עם מיקום) · {len(cells)} קטעי רחוב · "
                                   f"{faces} פנים טושטשו · נשמר במחשב (מספרים בלבד)", fg=T.GREEN)
        for w in self.results.winfo_children():
            w.destroy()
        self.results_card.pack(fill="x", pady=(0, 10))
        T.label(self.results, "מה נמצא במסלול", 13, T.TEXT, bold=True, anchor="e").pack(fill="x")
        dark = sorted(cells, key=lambda c: c["luminance"])[:3]
        if dark:
            T.label(self.results, "🌑 הקטעים החשוכים ביותר (בהירות שנמדדה בסרטון):", 11, T.TEXT, anchor="e").pack(fill="x")
            for c in dark:
                T.label(self.results, f"   {c['lat']:.5f}, {c['lon']:.5f} · בהירות {c['luminance']:.0%} · "
                                      f"{c['observations']} תצפיות", 10, T.TEXT_SECONDARY, anchor="e").pack(fill="x")
        if located:
            avg = {k: sum(o["attributes"][k] for o in located) / len(located) for k in S.ATTRIBUTES}
            T.label(self.results, "הערכת מודל (לא מאומתת, לא ציון בטיחות): " +
                    " · ".join(f"{S.LABELS_HE[k]} {v:.0%}" for k, v in avg.items()),
                    10, T.TEXT_MUTED, anchor="e", wraplength=680).pack(fill="x", pady=(6, 0))
        self._build_labeling()
        self._update_buttons()

    # ---------------------------------------------------------- תיוג
    def label_indices(self):
        """הפריימים שהכי כדאי לתייג: הכי "בטוחים" והכי "לא בטוחים" לפי המודל, מפוזרים לאורך המסלול."""
        n = len(self.obs)
        if n == 0:
            return []
        order = sorted(range(n), key=lambda i: self.obs[i]["attributes"].get("lighting", 0.5))
        k = min(self.MAX_LABEL_FRAMES, n) // 2 or 1
        picks = order[:k] + order[-k:]
        return sorted(set(picks))[:self.MAX_LABEL_FRAMES]

    def _build_labeling(self):
        for w in self.label_body.winfo_children():
            w.destroy()
        self._photos = []
        idx = self.label_indices()
        if not idx or not self.thumbs:
            return
        self.label_card.pack(fill="x", pady=(0, 10))
        T.label(self.label_body, "🏷️ עזרו למודל ללמוד (אופציונלי)", 13, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(self.label_body, "התמונות מטושטשות ומוצגות רק עכשיו - הן לא נשמרות.", 10, T.TEXT_MUTED,
                anchor="e").pack(fill="x")
        grid = tk.Frame(self.label_body, bg=T.CARD_BG)
        grid.pack(fill="x", pady=6)
        self.label_vars = {}
        try:
            from PIL import Image, ImageTk
        except ImportError:
            Image = ImageTk = None
        for n, i in enumerate(idx):
            cell = tk.Frame(grid, bg=T.CARD_BG, highlightthickness=1, highlightbackground=T.BORDER)
            cell.grid(row=n // 4, column=3 - n % 4, padx=4, pady=4, sticky="n")
            if Image is not None and i < len(self.thumbs):
                rgb = self.thumbs[i][:, :, ::-1]
                photo = ImageTk.PhotoImage(Image.fromarray(rgb), master=self)
                self._photos.append(photo)
                tk.Label(cell, image=photo, bg=T.CARD_BG).pack()
            lv, sv = tk.StringVar(value=""), tk.StringVar(value="")
            self.label_vars[i] = (lv, sv)
            for var, opts in ((lv, (("מואר", "1"), ("חשוך", "0"))), (sv, (("בטוח", "1"), ("לא בטוח", "0")))):
                r = tk.Frame(cell, bg=T.CARD_BG)
                r.pack()
                for text, val in opts:
                    tk.Radiobutton(r, text=text, variable=var, value=val, bg=T.CARD_BG, font=T.F(9)).pack(side="right")
        self.save_btn = T.primary_button(self.label_body, "💾 שמור תיוגים" + (" ושלח לשרת" if self.send_var.get() else ""),
                                         self.save_and_send, size=11, pady=6)
        self.save_btn.pack(anchor="e", pady=(6, 0))
        self.send_status = T.label(self.label_body, "", 10, T.TEXT_SECONDARY, anchor="e", wraplength=680)
        self.send_status.pack(fill="x")

    def collect_labels(self):
        out = {}
        for i, (lv, sv) in getattr(self, "label_vars", {}).items():
            lab = {}
            if lv.get():
                lab["lighting"] = int(lv.get())
            if sv.get():
                lab["felt_safe"] = int(sv.get())
            if lab:
                out[i] = lab
        return out

    def save_and_send(self):
        import json
        self.labels = self.collect_labels()
        for i, lab in self.labels.items():
            self.obs[i]["labels"] = lab
        with open(os.path.splitext(self.saved_path)[0] + "_labels.json", "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in self.labels.items()}, fh, ensure_ascii=False)
        self.send_status.configure(text=f"נשמרו {len(self.labels)} תיוגים במחשב.", fg=T.GREEN)
        if not self.send_var.get():
            return
        if self.app.client is None:
            self.send_status.configure(text="נשמר במחשב. אין חיבור לשרת - לא נשלח.", fg=T.YELLOW)
            return
        payload = [{"lat": o["lat"], "lon": o["lon"], "observed_at": o.get("observed_at"),
                    "attributes": o["attributes"], "luminance": o["luminance"], "dark_fraction": o["dark_fraction"],
                    **({"labels": o["labels"]} if o.get("labels") else {})}
                   for o in trim_endpoints(self.obs, meters=self.trim_m)]
        if not payload:
            self.send_status.configure(text="המסלול קצר מדי - אחרי הסרת ההתחלה והסוף לא נשאר מה לשלוח. נשמר במחשב בלבד.",
                                       fg=T.YELLOW)
            return
        from app_screens import run_bg
        run_bg(self.app, lambda: self.app.client.submit_walk(payload, CONSENT_VERSION, model="clip-vit-b32-zs-v1"),
               lambda r: self.winfo_exists() and self.send_status.configure(
                   text=f"✅ נשלחו לשרת {r['stored']} תצפיות (מספרים בלבד, ללא תמונות).", fg=T.GREEN),
               lambda e: self.winfo_exists() and self.send_status.configure(
                   text=f"❗ השליחה נכשלה: {getattr(e, 'message', e)}. הנתונים נשמרו במחשב.", fg=T.RED))

    def delete_local(self):
        """מוחק רק את הקבצים שהחלון הזה יוצר (jsonl / _emb.npz / _labels.json) בתיקיית ההליכות של המשתמש."""
        d = walks_dir(self.app.username)
        n = 0
        for f in os.listdir(d):
            if f.startswith("local_") and f.endswith((".jsonl", "_emb.npz", "_labels.json")):
                os.remove(os.path.join(d, f))
                n += 1
        self.obs, self.thumbs = [], []
        self.delete_status.configure(text=f"נמחקו {n} קבצים מהמחשב")

    def delete_mine(self):
        if self.app.client is None:
            self.delete_status.configure(text="אין חיבור לשרת")
            return
        from app_screens import run_bg
        run_bg(self.app, lambda: self.app.client.delete_walks(),
               lambda r: self.winfo_exists() and self.delete_status.configure(text=f"נמחקו {r['deleted']} תצפיות מהשרת"),
               lambda e: self.winfo_exists() and self.delete_status.configure(text=f"המחיקה נכשלה: {e}"))
