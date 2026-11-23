# -*- coding: utf-8 -*-
"""
wellness_check.py - "הכל בסדר?": המערכת שואלת אחרי אירוע חשוד, ופועלת אם אין תשובה.

טריגרים:
- נפילה / מכה חזקה (חיישן תאוצה בטלפון/שעון - sensor_module, דרך engine.report_sensor_signal)
- עצירה פתאומית: תנועה מהירה (ריצה/רכב) ואז עמידה במקום - לפי רצף מיקומים (detect_abrupt_stop)
- אין קליטה: הטלפון בלי חיבור יותר מ-SIGNAL_LOSS_SECONDS בזמן ליווי/טיימר בטיחות פעיל.
  במצב כזה השרת לא יכול לשאול - לכן השאלה והתגובה קורות *במכשיר עצמו* (סירנה מקומית, SOS שנשמר
  ויישלח כשהקליטה תחזור; בטלפון - גם SMS ישיר לאנשי הקשר).

התנהגות:
- שאלה "הכל בסדר?" עם ספירה לאחור (CHECKIN_SECONDS). "אני בסדר" -> שקט COOLDOWN_SECONDS.
  "אני צריך/ה עזרה" או אין תשובה -> SOS (כמו לחיצה על הכפתור).
- לא שואלים כשכבר יש אירוע פעיל, ולא במעקב שקט (שם אסור שיופיע משהו על המסך).
"""

import math
import threading
import time

CHECKIN_SECONDS = 45
COOLDOWN_SECONDS = 5 * 60
SIGNAL_LOSS_SECONDS = 5 * 60
SENSOR_TRIGGERS = {"fall": 0.7, "impact": 0.8, "abrupt_stop": 0.7}
FAST_MPS = 4.0          # ~14 קמ"ש: ריצה או רכב
STILL_MPS = 0.5
STILL_SECONDS = 20
REASONS_HE = {"fall": "זוהתה נפילה", "impact": "זוהתה מכה חזקה", "abrupt_stop": "עצירה פתאומית",
              "signal_loss": "אין קליטה כבר כמה דקות"}


def _speed(a, b):
    dt = b[0] - a[0]
    if dt <= 0:
        return 0.0
    la1, lo1, la2, lo2 = map(math.radians, (a[1], a[2], b[1], b[2]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h)) / dt


def detect_abrupt_stop(fixes, fast=FAST_MPS, still=STILL_MPS, still_seconds=STILL_SECONDS, window=60):
    """fixes: [(t, lat, lon)] ממוין. True אם הייתה תנועה מהירה ואחריה עמידה של still_seconds לפחות,
    והמעבר קרה תוך window שניות (עצירה *פתאומית*, לא האטה הדרגתית לרמזור)."""
    if len(fixes) < 3:
        return False
    speeds = [(fixes[i + 1][0], _speed(fixes[i], fixes[i + 1])) for i in range(len(fixes) - 1)]
    last_fast = max((t for t, v in speeds if v >= fast), default=None)
    if last_fast is None:
        return False
    after = [(t, v) for t, v in speeds if t > last_fast]
    if not after or any(v > still for _, v in after):
        return False
    still_from = after[0][0]
    return fixes[-1][0] - still_from >= still_seconds and still_from - last_fast <= window


class WellnessMonitor:
    def __init__(self, engine, ask=None, clock=time.time, checkin_seconds=CHECKIN_SECONDS):
        """ask(reason_text, deadline, answer_fn) - מציג שאלה בממשק. answer_fn(ok: bool)."""
        self.engine, self.ask, self.clock = engine, ask, clock
        self.checkin_seconds = checkin_seconds
        self.pending = None            # {"reason", "deadline"}
        self.quiet_until = 0.0
        self.fixes = []
        self.offline_since = None
        self._lock = threading.RLock()
        self._timer = None

    # ------------------------------------------------------------ טריגרים
    def on_sensor(self, signal, confidence):
        thr = SENSOR_TRIGGERS.get(signal)
        if thr is not None and confidence >= thr:
            self.start_checkin(signal)

    def on_location(self, t, lat, lon):
        with self._lock:
            self.fixes = [f for f in self.fixes if t - f[0] <= 120] + [(t, lat, lon)]
            fire = detect_abrupt_stop(self.fixes)
            if fire:
                self.fixes = self.fixes[-1:]          # לא לשאול שוב על אותה עצירה
        if fire:
            self.start_checkin("abrupt_stop")

    def on_connectivity(self, online, watching):
        """watching: האם יש ליווי/טיימר בטיחות פעיל (רק אז היעדר קליטה מדאיג)."""
        now = self.clock()
        with self._lock:
            if online or not watching:
                self.offline_since = None
                return
            if self.offline_since is None:
                self.offline_since = now
                return
            fire = now - self.offline_since >= SIGNAL_LOSS_SECONDS
            if fire:
                self.offline_since = now               # שאלה אחת לכל חלון
        if fire:
            self.start_checkin("signal_loss")

    # ------------------------------------------------------------ שאלה
    def _engine_busy(self):
        st = getattr(getattr(self.engine, "state", None), "name", "IDLE")
        return st not in ("IDLE", "FEEDBACK")

    def start_checkin(self, reason):
        now = self.clock()
        with self._lock:
            if self.pending or now < self.quiet_until or self._engine_busy():
                return False
            self.pending = {"reason": reason, "deadline": now + self.checkin_seconds}
        self._log(f"❓ {REASONS_HE.get(reason, reason)} - שואל 'הכל בסדר?' ({self.checkin_seconds} שנ')")
        if self.ask:
            self.ask(REASONS_HE.get(reason, reason), self.pending["deadline"], self.answer)
        self._timer = threading.Timer(self.checkin_seconds, self._expire)
        self._timer.daemon = True
        self._timer.start()
        return True

    def answer(self, ok):
        with self._lock:
            if not self.pending:
                return
            reason = self.pending["reason"]
            self.pending = None
            if self._timer:
                self._timer.cancel()
            if ok:
                self.quiet_until = self.clock() + COOLDOWN_SECONDS
        if ok:
            self._log("✅ המשתמש/ת אישר/ה שהכל בסדר")
        else:
            self._escalate(reason, "המשתמש/ת ביקש/ה עזרה")

    def _expire(self):
        with self._lock:
            if not self.pending:
                return
            reason = self.pending["reason"]
            self.pending = None
        self._escalate(reason, "לא הייתה תשובה ל'הכל בסדר?'")

    def _escalate(self, reason, why):
        self._log(f"🚨 {why} ({REASONS_HE.get(reason, reason)}) - מפעיל SOS")
        trig = getattr(self.engine, "trigger", None)
        if trig:
            trig(force_panic=True, source="wellness_check")

    def _log(self, text):
        log = getattr(self.engine, "log", None)
        if log:
            log(text)
