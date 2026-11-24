# -*- coding: utf-8 -*-
"""
collection_ui.py - אפליקציית איסוף הקלטות למחקר (בהסכמה).

הפעלה:  .venv\\Scripts\\python.exe -m research_tools.collection_ui
זרימה: טופס הסכמה -> פרטי סשן (סביבה, מרחק, מיקרופון) -> פריטי הפרוטוקול אחד-אחד.
כל הקלטה נשמרת רק כשהמשתתף/ת לוחץ/ת "שמור" אחרי שהאזין/ה לה. אפשר לדלג על כל פריט ולהפסיק בכל רגע.
"""

import threading
import tkinter as tk
from tkinter import ttk

import numpy as np

import ui_theme as T
from research_tools import collection as C


def _sd_recorder(seconds):
    import sounddevice as sd
    data = sd.rec(int(seconds * C.SAMPLE_RATE), samplerate=C.SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return data.reshape(-1)


def _sd_player(samples):
    import sounddevice as sd
    sd.play(np.asarray(samples, dtype=np.float32), C.SAMPLE_RATE)


def _default_device():
    try:
        import sounddevice as sd
        return sd.query_devices(kind="input")["name"]
    except Exception as exc:  # noqa: BLE001
        return f"לא ידוע ({type(exc).__name__})"


class CollectionApp:
    def __init__(self, root, store=None, recorder=_sd_recorder, player=_sd_player, device_name=None):
        self.root, self.store = root, store or C.CollectionStore()
        self.recorder, self.player = recorder, player
        self.device_name = device_name or _default_device()
        self.pid, self.session, self.index, self.current = None, None, 0, None
        root.title("LifeGuard - איסוף הקלטות למחקר")
        root.configure(bg=T.BG)
        self.frame = None
        self.show_consent()

    def _screen(self):
        if self.frame is not None:
            self.frame.destroy()
        c, b = T.card(self.root)
        c.pack(fill="both", expand=True, padx=16, pady=16)
        self.frame = c
        return b

    # ------------------------------------------------------------ הסכמה
    def show_consent(self):
        b = self._screen()
        T.label(b, "טופס הסכמה", 18, T.TEXT, bold=True, anchor="e").pack(fill="x")
        txt = tk.Text(b, height=14, wrap="word", font=T.F(11), bg=T.CARD_BG_ALT, fg=T.TEXT, bd=0, padx=8, pady=6)
        txt.tag_configure("rtl", justify="right")
        txt.insert("1.0", C.CONSENT_TEXT, "rtl")
        txt.configure(state="disabled")
        txt.pack(fill="x", pady=6)
        self.consent_vars = {}
        for key, text in C.CONSENT_ITEMS:
            v = tk.BooleanVar(value=False)
            req = "" if key in C.REQUIRED_CONSENT else " (נדרש כדי שההקלטות ישמשו לאימון)"
            tk.Checkbutton(b, text=text + req, variable=v, font=T.F(11), bg=T.CARD_BG, anchor="e",
                           command=self._update_consent_btn).pack(fill="x")
            self.consent_vars[key] = v
        demo = tk.Frame(b, bg=T.CARD_BG)
        demo.pack(fill="x", pady=(8, 0))
        T.label(demo, "אופציונלי (לבדיקת הטיות במודל):", 10, T.TEXT_MUTED).pack(side="right")
        self.age_var, self.gender_var, self.lang_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for label, var, vals in (("גיל", self.age_var, ("", "18-29", "30-49", "50-69", "70+")),
                                 ("מגדר", self.gender_var, ("", "אישה", "גבר", "אחר", "מעדיף/ה לא לציין")),
                                 ("שפת אם", self.lang_var, ("", "עברית", "ערבית", "רוסית", "אנגלית", "אמהרית", "אחר"))):
            T.label(demo, label + ":", 10, T.TEXT_SECONDARY).pack(side="right", padx=(8, 2))
            ttk.Combobox(demo, textvariable=var, values=vals, width=12, state="readonly").pack(side="right")
        self.error = T.label(b, "", 11, T.RED, anchor="e")
        self.error.pack(fill="x")
        self.start_btn = T.primary_button(b, "אני מסכים/ה - התחל", self.accept_consent, size=12, pady=6)
        self.start_btn.pack(anchor="e", pady=(8, 0))
        self._update_consent_btn()

    def _update_consent_btn(self):
        ok = all(self.consent_vars[k].get() for k in C.REQUIRED_CONSENT)
        self.start_btn.configure(state="normal" if ok else "disabled")

    def accept_consent(self):
        try:
            self.pid = self.store.new_participant({k: v.get() for k, v in self.consent_vars.items()},
                                                  {"age_group": self.age_var.get(), "gender": self.gender_var.get(),
                                                   "native_language": self.lang_var.get()})
        except C.ConsentError as e:
            self.error.configure(text=str(e))
            return
        self.show_session()

    # ------------------------------------------------------------ סשן
    def show_session(self):
        b = self._screen()
        T.label(b, f"הכינוי שלך: {self.pid}", 20, T.NAVY, bold=True, anchor="e").pack(fill="x")
        T.label(b, "שמור/י אותו - בעזרתו אפשר לבקש מחיקה של כל ההקלטות בכל עת.", 11, T.TEXT_SECONDARY,
                anchor="e").pack(fill="x", pady=(0, 10))
        self.env_var, self.dist_var = tk.StringVar(value=C.ENVIRONMENTS[0]), tk.StringVar(value=C.DISTANCES[0])
        for label, var, vals in (("איפה מקליטים?", self.env_var, C.ENVIRONMENTS),
                                 ("מרחק מהמיקרופון", self.dist_var, C.DISTANCES)):
            T.label(b, label, 12, T.TEXT, anchor="e").pack(fill="x", pady=(6, 0))
            ttk.Combobox(b, textvariable=var, values=vals, state="readonly", width=30).pack(anchor="e")
        T.label(b, f"🎙️ מיקרופון: {self.device_name}", 10, T.TEXT_MUTED, anchor="e").pack(fill="x", pady=(8, 0))
        T.primary_button(b, "המשך להקלטות", self.start_protocol, size=12, pady=6).pack(anchor="e", pady=(12, 0))

    def start_protocol(self):
        self.session = {"environment": self.env_var.get(), "distance": self.dist_var.get(), "device": self.device_name}
        self.index = 0
        self.show_item()

    # ------------------------------------------------------------ פרוטוקול
    def show_item(self):
        if self.index >= len(C.PROTOCOL):
            return self.show_done()
        item = C.PROTOCOL[self.index]
        self.current = None
        b = self._screen()
        T.label(b, f"פריט {self.index + 1} מתוך {len(C.PROTOCOL)}", 10, T.TEXT_MUTED, anchor="e").pack(fill="x")
        T.label(b, item["text"], 18, T.TEXT, bold=True, anchor="e", wraplength=560).pack(fill="x", pady=10)
        if item.get("optional"):
            T.label(b, "פריט אופציונלי - אפשר לדלג בלי להסביר", 11, T.YELLOW, anchor="e").pack(fill="x")
        T.label(b, f"משך: {item['seconds']} שניות. ההקלטה מתחילה אחרי שנייה מהלחיצה.", 10, T.TEXT_MUTED,
                anchor="e").pack(fill="x")
        self.status = T.label(b, "", 13, T.TEXT_SECONDARY, anchor="e")
        self.status.pack(fill="x", pady=8)
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x")
        self.rec_btn = T.primary_button(row, "🎙️ הקלט", self.record, size=12, pady=6)
        self.rec_btn.pack(side="right")
        self.play_btn = T.big_button(row, "▶ השמע", self.play, size=11, pady=6, state="disabled")
        self.play_btn.pack(side="right", padx=6)
        self.save_btn = T.big_button(row, "✓ שמור", self.save, bg=T.GREEN, size=11, pady=6, state="disabled")
        self.save_btn.pack(side="right")
        bottom = tk.Frame(b, bg=T.CARD_BG)
        bottom.pack(fill="x", pady=(16, 0))
        T.big_button(bottom, "דלג", self.next_item, size=10, pady=4).pack(side="right")
        T.big_button(bottom, "סיום והפסקה", self.show_done, size=10, pady=4).pack(side="left")

    def record(self):
        item = C.PROTOCOL[self.index]
        self.rec_btn.configure(state="disabled")
        self.status.configure(text="מתכוננים...", fg=T.TEXT_SECONDARY)
        box = []

        def work():
            try:
                box.append(("ok", self.recorder(item["seconds"])))
            except Exception as exc:  # noqa: BLE001
                box.append(("err", exc))

        def start():
            self.status.configure(text="● מקליט עכשיו", fg=T.RED)
            threading.Thread(target=work, daemon=True).start()
            poll()

        def poll():
            if not box:
                self.root.after(100, poll)
                return
            kind, val = box[0]
            self.rec_btn.configure(state="normal", text="↻ הקלט שוב")
            if kind == "err":
                self.status.configure(text=f"❌ ההקלטה נכשלה: {val}", fg=T.RED)
                return
            self.current = val
            q = C.quality(C._to_int16(val))
            if q["too_quiet"]:
                msg, color = "⚠️ חלש מאוד - אולי המיקרופון כבוי? אפשר להקליט שוב", T.YELLOW
            elif q["clipped"]:
                msg, color = "⚠️ חזק מדי (עיוות) - כדאי להתרחק מעט ולהקליט שוב", T.YELLOW
            else:
                msg, color = "✓ נשמע תקין. האזן/י ולחץ/י שמור, או הקלט/י שוב", T.GREEN
            self.status.configure(text=msg, fg=color)
            self.play_btn.configure(state="normal")
            self.save_btn.configure(state="normal")
        self.root.after(1000, start)

    def play(self):
        if self.current is not None:
            try:
                self.player(self.current)
            except Exception as exc:  # noqa: BLE001
                self.status.configure(text=f"❌ השמעה נכשלה: {exc}", fg=T.RED)

    def save(self):
        if self.current is None:
            return
        self.store.save_take(self.pid, C.PROTOCOL[self.index]["id"], self.current, self.session)
        self.next_item()

    def next_item(self):
        self.index += 1
        self.show_item()

    def show_done(self):
        b = self._screen()
        n = len(self.store.manifest(self.pid)) if self.pid else 0
        T.label(b, "תודה רבה!", 20, T.GREEN, bold=True, anchor="e").pack(fill="x")
        T.label(b, f"נשמרו {n} הקלטות תחת הכינוי {self.pid}.", 13, T.TEXT, anchor="e").pack(fill="x", pady=6)
        T.label(b, f"למחיקה בכל עת: לפנות לחוקר/ת עם הכינוי {self.pid}.", 11, T.TEXT_SECONDARY,
                anchor="e").pack(fill="x")
        T.big_button(b, "משתתף/ת חדש/ה", self.show_consent, size=11, pady=5).pack(anchor="e", pady=(12, 0))


def main():
    root = tk.Tk()
    root.geometry("700x720")
    CollectionApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
