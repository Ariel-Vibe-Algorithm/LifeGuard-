# -*- coding: utf-8 -*-
"""
fake_call.py - "שיחה מזויפת": אומרים ביטוי קולי (או לוחצים) -> אחרי השהיה קצרה מופיע מסך של שיחה נכנסת,
לפי ההגדרות שנבחרו בפרופיל. כלי ליציאה ממצב לא נוח ("סליחה, אני חייב/ת לענות").

- כברירת מחדל זה **לא חירום**: לא נשלח כלום לשרת, לא נפתח אירוע, לא נרשם כלום. מצוקה אמיתית = ביטויי ה-SOS.
- "גם התראה שקטה" (with_silent_alert, כבוי כברירת מחדל): הביטוי מפעיל מיד התראה שקטה (engine), והשיחה
  היא נקודת ההחלטה - ניתוק לפני מענה מבטל אותה (engine.fake_call_declined); מענה או בלי תגובה - נשארת.
- הביטוי נבדק **אחרי** כל ביטויי ה-SOS (כפייה, ביטול, שקט, רועש) - הוא לעולם לא חוסם חירום, ואסור
  שיהיה זהה לאחד מהם (נבדק בשמירה).
- הביטוי והתמלול לא נרשמים ליומן (כמו כל ביטויי הקוד).
- הצלצול: קובץ WAV שנוצר במחשב (בלי קבצים חיצוניים), מתנגן בלולאה עד מענה / דחייה / 45 שניות.
"""

import io
import json
import math
import os
import struct
import tempfile
import threading
import time
import tkinter as tk
import wave

import ui_theme as T

SETTING_KEY = "fake_call"
DELAYS_S = (0, 5, 10, 30, 60)
RINGTONES = {"classic": "צלצול קלאסי", "soft": "צלצול רך", "vibrate": "רטט בלבד (שקט)"}
RING_TIMEOUT_S = 45
DEFAULTS = {"enabled": False, "phrase": "", "caller_name": "אמא", "caller_label": "נייד", "delay_s": 10,
            "ringtone": "classic",
            # גם התראה שקטה: הביטוי מפעיל מיד התראה שקטה; ניתוק השיחה מבטל אותה, מענה / בלי תגובה - נשארת
            "with_silent_alert": False}


# ---------------------------------------------------------------- הגדרות
def load_settings(username):
    import incident_store
    try:
        raw = incident_store.get_setting(username, SETTING_KEY, None)
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        data = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in data.items() if k in DEFAULTS})
    return out


def _norm(s):
    return " ".join(str(s or "").strip().lower().split())


def validate(settings, sos_phrases=()):
    """-> רשימת שגיאות בעברית (ריקה = תקין)."""
    errs = []
    phrase = _norm(settings.get("phrase"))
    name = str(settings.get("caller_name") or "").strip()
    if settings.get("enabled"):
        if len(phrase) < 3:
            errs.append("כתבו ביטוי קולי של לפחות 3 תווים (עדיף 2-3 מילים שלא נאמרות בשיחה רגילה)")
        for p in sos_phrases:
            p = _norm(p)
            if p and phrase and (p == phrase or p in phrase or phrase in p):
                errs.append("הביטוי לשיחה המזויפת חייב להיות שונה מביטויי החירום שלך (אחרת תופעל שיחה במקום SOS)")
                break
    if not name:
        errs.append("כתבו את שם המתקשר/ת שיוצג")
    if settings.get("delay_s") not in DELAYS_S:
        errs.append("השהיה לא חוקית")
    if settings.get("ringtone") not in RINGTONES:
        errs.append("צלצול לא חוקי")
    return errs


def sos_phrases_of(username):
    import auth_module
    out = []
    for fn in ("get_voice_secret_word", "get_silent_alarm_phrase", "get_secret_cancel_phrase", "get_duress_phrase"):
        try:
            out.append(getattr(auth_module, fn)(username) or "")
        except Exception:  # noqa: BLE001
            pass
    return out


def save_settings(username, settings):
    import incident_store
    s = dict(DEFAULTS)
    s.update({k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS})
    s["phrase"] = str(s["phrase"]).strip()[:60]
    s["caller_name"] = str(s["caller_name"]).strip()[:40]
    s["caller_label"] = str(s["caller_label"]).strip()[:20]
    errs = validate(s, sos_phrases_of(username))
    if errs:
        raise ValueError("\n".join(errs))
    incident_store.set_setting(username, SETTING_KEY, json.dumps(s, ensure_ascii=False))
    return s


# ---------------------------------------------------------------- צלצול
def ringtone_wav(kind="classic", seconds=4.0, rate=16000):
    """WAV בזיכרון: צלצול כפול (מחזור של 4 שנ') - נוצר כאן, בלי קבצי צליל חיצוניים."""
    frames = io.BytesIO()
    n = int(seconds * rate)
    freqs = (440, 480) if kind == "classic" else (523, 659)
    vol = 0.35 if kind == "classic" else 0.2
    buf = bytearray()
    for i in range(n):
        t = i / rate
        cyc = t % 4.0
        on = (cyc < 0.4) or (0.6 <= cyc < 1.0)               # "טר-טר" ואז שקט
        if kind == "vibrate":
            on = False
        v = 0.0
        if on:
            env = min(1.0, (cyc % 0.6) * 40)                 # כניסה רכה, בלי קליקים
            v = vol * env * (math.sin(2 * math.pi * freqs[0] * t) + math.sin(2 * math.pi * freqs[1] * t)) / 2
        buf += struct.pack("<h", int(max(-1, min(1, v)) * 32767))
    with wave.open(frames, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(buf))
    return frames.getvalue()



def _disc(cv, off, d, color, bg):
    """עיגול חלק (Pillow, במקדם ה-DPI) במקום create_oval המשונן של Tk."""
    if not T.HAS_PIL:
        cv.create_oval(T.px(off), T.px(off), T.px(off + d), T.px(off + d), fill=color, outline="")
        return
    dp = T.px(d)
    ph = T._photo(cv, ("disc", dp, color, bg), lambda: T.circle_pil(dp, color, bg))
    cv._lg_img = ph
    cv.create_image(T.px(off), T.px(off), image=ph, anchor="nw")

class _Ringer:
    """מנגן בלולאה מקובץ זמני (winsound לא מנגן מזיכרון באופן אסינכרוני)."""

    def __init__(self, kind):
        self.kind, self.path = kind, None

    def start(self):
        if self.kind == "vibrate":
            return
        try:
            import winsound
            fd, self.path = tempfile.mkstemp(prefix="lg_ring_", suffix=".wav")
            with os.fdopen(fd, "wb") as f:
                f.write(ringtone_wav(self.kind))
            winsound.PlaySound(self.path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
        except Exception:  # noqa: BLE001 - בלי צליל המסך עדיין מופיע
            self.path = None

    def stop(self):
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except Exception:  # noqa: BLE001
            pass
        if self.path:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.path = None


# ---------------------------------------------------------------- מסך השיחה
class FakeCallWindow(tk.Toplevel):
    """מסך שיחה נכנסת: שם המתקשר/ת, דחייה / מענה; אחרי מענה - "בשיחה" עם שעון. נראה כמו שיחה רגילה."""

    BG, FG, MUTED = "#0b1426", "#ffffff", "#aab4c6"

    def __init__(self, master, settings, on_close=None, on_declined=None):
        super().__init__(master)
        self.settings, self.on_close = settings, on_close
        # on_declined: רק בניתוק *לפני* מענה (דחייה / Esc / סגירת החלון). מענה או בלי תגובה - לא נקרא.
        self.on_declined = on_declined
        self.outcome = None          # "declined" / "answered" / "no_answer"
        self.title(settings.get("caller_name") or "שיחה נכנסת")
        self.configure(bg=self.BG)
        self.attributes("-topmost", True)
        w, h = 380, 640
        try:
            sx, sy = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"{w}x{h}+{max(0, sx - w - 40)}+{max(0, (sy - h) // 2)}")
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self.decline)
        self.bind("<Escape>", lambda e: self.decline())
        self.ringer = _Ringer(settings.get("ringtone", "classic"))
        self.answered_at = None
        self._timeout = None
        self._build_incoming()
        self.ringer.start()
        self._timeout = self.after(RING_TIMEOUT_S * 1000, self.no_answer)

    def _clear(self):
        for c in self.winfo_children():
            c.destroy()

    def _avatar(self, parent):
        name = (self.settings.get("caller_name") or "?").strip()
        cv = tk.Canvas(parent, width=120, height=120, bg=self.BG, highlightthickness=0)
        _disc(cv, 4, 112, "#2a3a5c", self.BG)
        cv.create_text(T.px(60), T.px(60), text=name[:1], fill=self.FG, font=T.F(40, "bold"))
        return cv

    def _build_incoming(self):
        self._clear()
        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True, padx=24, pady=(60, 40))
        self._avatar(body).pack()
        tk.Label(body, text=self.settings.get("caller_name", ""), font=T.F(26, "bold"), fg=self.FG,
                 bg=self.BG).pack(pady=(18, 2))
        tk.Label(body, text=self.settings.get("caller_label", ""), font=T.F(13), fg=self.MUTED, bg=self.BG).pack()
        tk.Label(body, text="שיחה נכנסת...", font=T.F(12), fg=self.MUTED, bg=self.BG).pack(pady=(4, 0))
        row = tk.Frame(body, bg=self.BG)
        row.pack(side="bottom", fill="x", pady=(0, 10))
        self.decline_btn = self._round_button(row, "✕", "דחייה", "#e5383b", self.decline)
        self.decline_btn.pack(side="right", expand=True)
        self.accept_btn = self._round_button(row, "✆", "מענה", "#2fb36d", self.accept)
        self.accept_btn.pack(side="left", expand=True)

    def _round_button(self, parent, glyph, label, color, cmd):
        f = tk.Frame(parent, bg=self.BG)
        cv = tk.Canvas(f, width=76, height=76, bg=self.BG, highlightthickness=2, highlightbackground=self.BG,
                       highlightcolor=T.FOCUS, takefocus=True, cursor="hand2")
        _disc(cv, 2, 72, color, self.BG)
        cv.create_text(T.px(38), T.px(38), text=glyph, fill="white", font=T.F(24, "bold"))
        cv.bind("<Button-1>", lambda e: cmd())
        T.bind_activate(cv, cmd)
        cv.pack()
        tk.Label(f, text=label, font=T.F(11), fg=self.FG, bg=self.BG).pack(pady=(6, 0))
        f.invoke = cmd
        return f

    def accept(self):
        if self.outcome is None:
            self.outcome = "answered"
        self.ringer.stop()
        if self._timeout:
            self.after_cancel(self._timeout)
            self._timeout = None
        self.answered_at = time.time()
        self._clear()
        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True, padx=24, pady=(60, 40))
        self._avatar(body).pack()
        tk.Label(body, text=self.settings.get("caller_name", ""), font=T.F(24, "bold"), fg=self.FG,
                 bg=self.BG).pack(pady=(18, 2))
        self.timer_label = tk.Label(body, text="00:00", font=T.F(16), fg=self.MUTED, bg=self.BG)
        self.timer_label.pack()
        row = tk.Frame(body, bg=self.BG)
        row.pack(side="bottom", pady=(0, 10))
        self.end_btn = self._round_button(row, "✕", "סיום", "#e5383b", self.end)
        self.end_btn.pack()
        self._tick()

    def _tick(self):
        if not self.winfo_exists() or self.answered_at is None:
            return
        s = int(time.time() - self.answered_at)
        self.timer_label.configure(text=f"{s // 60:02d}:{s % 60:02d}")
        self.after(1000, self._tick)

    def decline(self):
        """ניתוק. לפני מענה = דחייה (מפעיל on_declined פעם אחת); אחרי מענה (Esc / סגירה) = סתם סיום שיחה."""
        if self.outcome is None:
            self.outcome = "declined"
            cb, self.on_declined = self.on_declined, None
            if cb is not None:
                try:
                    cb()
                except Exception:  # noqa: BLE001 - כשל בביטול לא משאיר חלון תקוע
                    pass
        self.end()

    def no_answer(self):
        """הצלצול נגמר בלי תגובה - נחשב כמו מענה מבחינת ההתראה (נשארת)."""
        self._timeout = None
        if self.outcome is None:
            self.outcome = "no_answer"
        self.end()

    def end(self):
        self.ringer.stop()
        if self._timeout:
            try:
                self.after_cancel(self._timeout)
            except tk.TclError:
                pass
        self.answered_at = None
        if self.on_close:
            self.on_close()
        try:
            self.destroy()
        except tk.TclError:
            pass


class FakeCallScheduler:
    """מתזמן שיחה אחת בכל פעם. trigger() בטוח לקריאה מכל thread דרך post (תור ה-UI של האפליקציה)."""

    def __init__(self, root, settings_fn, post=None, on_declined=None, run_bg=None):
        self.root, self.settings_fn = root, settings_fn
        self.post = post or (lambda fn, *a: root.after(0, fn, *a))
        # on_declined: engine.fake_call_declined - מבטל את ההתראה השקטה שהשיחה התחילה (רשת -> לא ב-thread ה-UI)
        self.on_declined = on_declined
        self.run_bg = run_bg or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.window = None
        self._pending = None
        self._link_silent = False
        self._lock = threading.Lock()

    def trigger(self, link_silent=False):
        """
        מהמאזין הקולי (thread רקע) או מכפתור. אם כבר יש שיחה / תזמון - מתעלמים.
        link_silent=True: המנוע כבר התחיל התראה שקטה בשביל השיחה הזו -> ניתוק יבטל אותה.
        """
        with self._lock:
            if self._pending is not None or (self.window is not None and self._alive()):
                return False
            self._pending = True
            self._link_silent = bool(link_silent)
        self.post(self._schedule)
        return True

    def _alive(self):
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def _schedule(self):
        s = self.settings_fn()
        self._pending = self.root.after(int(s.get("delay_s", 0)) * 1000, lambda: self._show(s))

    def _show(self, s):
        self._pending = None
        linked, self._link_silent = self._link_silent, False
        cb = self._declined if (linked and self.on_declined is not None) else None
        self.window = FakeCallWindow(self.root, s, on_close=self._closed, on_declined=cb)

    def _declined(self):
        self.run_bg(self.on_declined)

    def _closed(self):
        self.window = None

    def cancel(self):
        if self._pending not in (None, True):
            try:
                self.root.after_cancel(self._pending)
            except tk.TclError:
                pass
        self._pending = None
        if self.window is not None and self._alive():
            self.window.end()


# ---------------------------------------------------------------- מסך ההגדרות (פרופיל)
class FakeCallSettingsWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title("שיחה מזויפת")
        self.configure(bg=T.BG)
        self.geometry("520x620")
        self.bind("<Escape>", lambda e: self.destroy())
        s = load_settings(app.username)
        c, b = T.card(self)
        c.pack(fill="both", expand=True, padx=16, pady=16)
        T.label(b, "📞 שיחה מזויפת", 18, T.TEXT, bold=True, anchor="e").pack(fill="x")
        T.label(b, "אומרים את הביטוי, ואחרי ההשהיה מופיעה שיחה נכנסת - דרך מנומסת לצאת ממצב לא נוח. "
                   "בלי \"גם התראה שקטה\" לא נשלח כלום לאף אחד. במצוקה אמיתית - ביטויי ה-SOS.",
                10, T.TEXT_SECONDARY, anchor="e", wraplength=460).pack(fill="x", pady=(4, 10))
        self.enabled = tk.BooleanVar(master=self, value=bool(s["enabled"]))
        tk.Checkbutton(b, text="להפעיל בביטוי קולי", variable=self.enabled, font=T.F(11, "bold"), bg=T.CARD_BG,
                       fg=T.TEXT, selectcolor=T.CARD_BG_ALT, activebackground=T.CARD_BG).pack(anchor="e")
        self.with_silent = tk.BooleanVar(master=self, value=bool(s.get("with_silent_alert")))
        tk.Checkbutton(b, text="גם התראה שקטה לאנשי הקשר", variable=self.with_silent, font=T.F(11, "bold"),
                       bg=T.CARD_BG, fg=T.TEXT, selectcolor=T.CARD_BG_ALT, activebackground=T.CARD_BG).pack(anchor="e")
        T.label(b, "הביטוי מפעיל מיד התראה שקטה. ניתוק השיחה = ההתראה מבוטלת (ואנשי הקשר מעודכנים שהכל בסדר); "
                   "מענה או בלי תגובה = ההתראה נשארת.", 9, T.TEXT_MUTED, anchor="e",
                wraplength=460).pack(fill="x", pady=(0, 4))
        self.vars = {}
        for key, label in (("phrase", "הביטוי הקולי (2-3 מילים ייחודיות)"), ("caller_name", "שם המתקשר/ת"),
                           ("caller_label", "תיאור (למשל: נייד, עבודה)")):
            T.label(b, label, 10, T.TEXT_SECONDARY, anchor="e").pack(fill="x", pady=(8, 2))
            var = tk.StringVar(master=self, value=s[key])
            e = tk.Entry(b, textvariable=var, font=T.F(12), justify="right", bg=T.CARD_BG_ALT, fg=T.TEXT,
                         relief="flat", highlightthickness=2, highlightcolor=T.FOCUS, highlightbackground=T.BORDER)
            e.pack(fill="x", ipady=5)
            T.rtl_entry(e, var)
            self.vars[key] = var
        row = tk.Frame(b, bg=T.CARD_BG)
        row.pack(fill="x", pady=(12, 0))
        T.label(row, "השהיה:", 10, T.TEXT_SECONDARY).pack(side="right")
        self.delay = tk.StringVar(master=self, value=str(s["delay_s"]))
        tk.OptionMenu(row, self.delay, *[str(d) for d in DELAYS_S]).pack(side="right", padx=6)
        T.label(row, "שנ'", 10, T.TEXT_SECONDARY).pack(side="right")
        T.label(row, "צלצול:", 10, T.TEXT_SECONDARY).pack(side="right", padx=(16, 0))
        self.ring = tk.StringVar(master=self, value=RINGTONES[s["ringtone"]])
        tk.OptionMenu(row, self.ring, *RINGTONES.values()).pack(side="right", padx=6)
        btns = tk.Frame(b, bg=T.CARD_BG)
        btns.pack(fill="x", pady=(18, 0))
        T.primary_button(btns, "שמירה", self.save, size=11, pady=6).pack(side="right")
        T.big_button(btns, "▶ נסיון עכשיו (בלי השהיה)", self.try_now, size=10, pady=6).pack(side="right", padx=8)
        self.msg = T.label(b, "", 10, T.GREEN, anchor="e", wraplength=460)
        self.msg.pack(fill="x", pady=(10, 0))
        T.label(b, "הביטוי נבדק רק אחרי ביטויי החירום, ונשמר רק במחשב שלך. ההאזנה הקולית צריכה להיות פעילה "
                   "(שלושת ביטויי החירום מוגדרים).", 9, T.TEXT_MUTED, anchor="e", wraplength=460).pack(fill="x", pady=(8, 0))

    def _collect(self):
        return {"enabled": self.enabled.get(), "with_silent_alert": self.with_silent.get(),
                "phrase": T.entry_text(self.vars["phrase"].get()),
                "caller_name": T.entry_text(self.vars["caller_name"].get()),
                "caller_label": T.entry_text(self.vars["caller_label"].get()),
                "delay_s": int(self.delay.get()),
                "ringtone": next(k for k, v in RINGTONES.items() if v == self.ring.get())}

    def save(self):
        try:
            save_settings(self.app.username, self._collect())
        except ValueError as e:
            self.msg.configure(text=f"❌ {e}", fg=T.RED)
            return False
        self.msg.configure(text="✅ נשמר. ההאזנה הקולית תתעדכן תוך שניות.", fg=T.GREEN)
        eng = getattr(self.app, "engine", None)
        if eng is not None and hasattr(eng, "restart_voice_trigger"):
            threading.Thread(target=eng.restart_voice_trigger, daemon=True).start()   # לא חוסמים את המסך
        return True

    def try_now(self):
        s = dict(self._collect(), delay_s=0)
        FakeCallWindow(self.winfo_toplevel(), s)


def open_settings(app):
    return FakeCallSettingsWindow(app.root, app)
