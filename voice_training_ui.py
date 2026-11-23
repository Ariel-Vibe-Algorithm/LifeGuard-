# -*- coding: utf-8 -*-
"""
voice_training_ui.py - "אימון קולי אישי": הקלטת "הצילו" ומילות הקוד בקול של המשתמש/ת, ואימון זיהוי
עם אלפי וריאציות (personal_kws.py). הכל במחשב הזה; ההקלטות מוצפנות ולא נשלחות לשום מקום.
"""

import threading
import tkinter as tk

import numpy as np

import auth_module
import personal_kws as PK
import ui_theme as T


def phrases_for(username):
    """הביטויים לאימון: "הצילו" + מילות הקוד שהמשתמש/ת הגדיר/ה (בלי להציג אותן בגלוי במסך)."""
    out = [("help", "הצילו", True)]
    for pid, getter, label in (("loud", auth_module.get_voice_secret_word, "מילת הקוד לאזעקה רועשת"),
                               ("silent", auth_module.get_silent_alarm_phrase, "מילת הקוד למצוקה שקטה"),
                               ("cancel", auth_module.get_secret_cancel_phrase, "מילת הקוד לביטול")):
        try:
            if getter(username):
                out.append((pid, label, False))
        except Exception:  # noqa: BLE001
            pass
    return out


def _record(seconds, sr=PK.SR):
    import sounddevice as sd
    rec = sd.rec(int(seconds * sr), samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    return rec.reshape(-1)


class VoiceTrainingWindow(tk.Toplevel):
    def __init__(self, master, app, recorder=None, trainer=None):
        super().__init__(master)
        self.app = app
        self.recorder = recorder or _record
        self.trainer = trainer or PK.train
        self.store = PK.store_for(app.username)
        self.title("LifeGuard | אימון קולי אישי")
        self.geometry("640x720")
        self.configure(bg=T.BG)
        self.phrases = phrases_for(app.username)
        self.counts = {pid: len(PK.load_takes(self.store).get(pid, [])) for pid, _, _ in self.phrases}

        outer, body = T.scrollable(self)
        outer.pack(fill="both", expand=True, padx=14, pady=10)
        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 10))
        T.label(b, "🎙️ אימון קולי אישי", 16, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(b, "המערכת תלמד לזהות את \"הצילו\" ואת מילות הקוד שלך - בקול שלך, גם ברעש, בלחישה ובצעקה. "
                   f"כל ביטוי מוקלט {PK.TAKES_PER_PHRASE} פעמים בסגנונות שונים, ומכל הקלטה נוצרות מאות וריאציות "
                   "(רעשי רחוב, הדהוד, טלפון בכיס...). ההקלטות מוצפנות, נשארות במחשב הזה ולא נשלחות לאף אחד. "
                   "מילות הקוד לא מוצגות על המסך - אמור/י אותן כשאף אחד לא שומע.", 11, T.TEXT_SECONDARY,
                anchor="e", wraplength=580, justify="right").pack(fill="x", pady=(4, 0))
        self.rows = {}
        for pid, label, show in self.phrases:
            c, b = T.card(body)
            c.pack(fill="x", pady=(0, 8))
            T.label(b, f"«{label}»" if show else f"🔒 {label}", 13, T.TEXT, bold=True, anchor="e").pack(fill="x")
            st = T.label(b, "", 11, T.TEXT_SECONDARY, anchor="e")
            st.pack(fill="x")
            btn = T.big_button(b, "🔴 הקלט (2 שניות)", lambda p=pid: self.record_take(p), size=11)
            btn.pack(anchor="e", pady=(4, 0))
            self.rows[pid] = (st, btn)
        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 8))
        T.label(b, "🗣️ דיבור חופשי (20 שניות)", 13, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(b, "ספר/י משהו רגיל (מה עשית היום). זה מלמד את המערכת מה *לא* ביטוי חירום.", 11,
                T.TEXT_SECONDARY, anchor="e", wraplength=580).pack(fill="x")
        self.free_status = T.label(b, "✅ הוקלט" if "free_speech" in self.store.names() else "", 11, T.GREEN, anchor="e")
        self.free_status.pack(fill="x")
        T.big_button(b, "🔴 הקלט דיבור חופשי", self.record_free, size=11).pack(anchor="e", pady=(4, 0))
        c, b = T.card(body)
        c.pack(fill="x", pady=(0, 8))
        self.train_btn = T.primary_button(b, "🧠 אמן את הזיהוי", self.train, size=12, pady=6)
        self.train_btn.pack(anchor="e")
        self.status = T.label(b, "", 11, T.TEXT_SECONDARY, anchor="e", wraplength=580, justify="right")
        self.status.pack(fill="x", pady=(6, 0))
        T.big_button(b, "🗑️ מחק את כל ההקלטות והמודל", self.delete_all, size=10).pack(anchor="e", pady=(8, 0))
        self._refresh()

    def _refresh(self):
        for pid, (st, btn) in self.rows.items():
            n = self.counts.get(pid, 0)
            style = PK.TAKE_STYLES[n] if n < len(PK.TAKE_STYLES) else "בכל סגנון שתרצה/י"
            st.configure(text=f"{n}/{PK.TAKES_PER_PHRASE} הקלטות" + (f" · הבאה: {style}" if n < PK.TAKES_PER_PHRASE else " ✅"))
        ready = (all(self.counts.get(p, 0) >= PK.HOLDOUT_TAKES + 2 for p in self.rows)
                 and "free_speech" in self.store.names())
        self.train_btn.configure(state="normal" if ready else "disabled")

    def _bg(self, work, done):
        res = []
        threading.Thread(target=lambda: res.append(_safe(work)), daemon=True).start()

        def poll():
            if not self.winfo_exists():
                return
            if res:
                done(res[0])
            else:
                self.after(100, poll)
        self.after(100, poll)

    def record_take(self, pid):
        st, btn = self.rows[pid]
        btn.configure(state="disabled")
        st.configure(text="🔴 מקליט... אמור/י עכשיו", fg=T.RED)

        def done(r):
            btn.configure(state="normal")
            if isinstance(r, Exception):
                st.configure(text=f"ההקלטה נכשלה: {r}", fg=T.RED)
                return
            peak = float(np.max(np.abs(r))) if len(r) else 0.0
            if peak < 0.02:
                st.configure(text="לא נשמע כלום - בדוק/י את המיקרופון ונסה/י שוב", fg=T.RED)
                return
            PK.save_take(self.store, pid, self.counts.get(pid, 0), r)
            self.counts[pid] = self.counts.get(pid, 0) + 1
            st.configure(fg=T.TEXT_SECONDARY)
            self._refresh()
        self._bg(lambda: self.recorder(PK.CLIP_SECONDS), done)

    def record_free(self):
        self.free_status.configure(text="🔴 מקליט 20 שניות... דבר/י חופשי", fg=T.RED)

        def done(r):
            if isinstance(r, Exception):
                self.free_status.configure(text=f"ההקלטה נכשלה: {r}", fg=T.RED)
                return
            self.store.put("free_speech", np.asarray(r, np.float32).tobytes())
            self.free_status.configure(text="✅ הוקלט", fg=T.GREEN)
            self._refresh()
        self._bg(lambda: self.recorder(PK.FREE_SPEECH_SECONDS), done)

    def train(self):
        self.train_btn.configure(state="disabled")
        self.status.configure(text="🧠 מאמן... (יוצר אלפי וריאציות - כמה דקות)", fg=T.TEXT_SECONDARY)

        def work():
            takes = PK.load_takes(self.store)
            free = np.frombuffer(self.store.get("free_speech"), np.float32)
            models, report = self.trainer(takes, free, log=lambda m: None)
            PK.save_models(self.store, models, report)
            return report

        def done(r):
            self._refresh()
            if isinstance(r, Exception):
                self.status.configure(text=f"האימון נכשל: {r}", fg=T.RED)
                return
            names = {pid: (label if show else label) for pid, label, show in self.phrases}
            lines = [f"✅ אומן על {r['variations_total']:,} וריאציות."]
            for pid, p in r["phrases"].items():
                lines.append(f"• {names.get(pid, pid)}: זוהו {p['heldout_clean_detected']} הקלטות שהמערכת לא ראתה, "
                             f"{p['heldout_noisy_recall']:.0%} מהן ברעש · התראות שווא בבדיקה: {p['false_positives']}")
            lines.append("כדי לשפר: עוד הקלטות בסגנונות שונים, ובמקומות שונים.")
            self.status.configure(text="\n".join(lines), fg=T.GREEN)
        self._bg(work, done)

    def delete_all(self):
        self.store.delete_all()
        self.counts = {pid: 0 for pid in self.rows}
        self.free_status.configure(text="")
        self.status.configure(text="כל ההקלטות והמודל נמחקו מהמחשב.", fg=T.TEXT_SECONDARY)
        self._refresh()


def _safe(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - מוצג למשתמש
        return exc
