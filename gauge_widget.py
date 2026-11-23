# -*- coding: utf-8 -*-
"""
gauge_widget.py
מד רמת-סכנה גרפי (קשת + מחוג) - Tkinter Canvas בלבד, ללא תלויות חיצוניות.
משמש גם בזמן אמת (בתוך אירוע SOS) וגם במצב "ניטור חי לכיול" מהמסך הראשי.
"""

import math
import tkinter as tk

import ui_theme as T

_COLORS = {"LOW": "#3ba55d", "MEDIUM": "#e2b93b", "HIGH": "#c0392b"}


class RiskGauge(tk.Canvas):
    def __init__(self, master, width=200, height=160, max_score=8, bg="#101418", **kwargs):
        super().__init__(master, width=width, height=height, bg=bg,
                          highlightthickness=0, **kwargs)
        # צבעים לפי הרקע: על רקע בהיר (מסך החירום בפלטת הבית) - מחוג כהה וטקסט מעומעם קריא
        r, g, b = self.winfo_rgb(bg)
        light = (0.299 * r + 0.587 * g + 0.114 * b) / 257 > 150
        self._bg = bg
        self._needle = "#0b1f3a" if light else "white"
        self._muted = "#5b6b82" if light else "#8a97a3"
        self._ring = "#0b1f3a" if light else "#c7d1db"
        # הקנבס עצמו מוגדל לפי DPI (ui_theme); הציור בפיקסלים פיזיים - כל המידות עוברות דרך T.px
        width, height = T.px(width), T.px(height)
        self.width = width
        self.height = height
        self.max_score = max_score
        self._cx = width / 2
        self._cy = height - T.px(24)
        self._r = min(width, height) - T.px(40)
        self._needle_id = None
        self._label_id = None
        self._sub_label_id = None
        self._draw_static_arc()
        self.set_value(0, "LOW", extra_text="")

    def _draw_static_arc(self):
        cx, cy, r = self._cx, self._cy, self._r
        # קשת: 0°-60° אדום (סכנה גבוהה, ימין), 60°-120° צהוב (אמצע), 120°-180° ירוק (שמאל, בטוח)
        self.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=60,
                         fill="#c0392b", outline="", style="pieslice")
        self.create_arc(cx - r, cy - r, cx + r, cy + r, start=60, extent=60,
                         fill="#e2b93b", outline="", style="pieslice")
        self.create_arc(cx - r, cy - r, cx + r, cy + r, start=120, extent=60,
                         fill="#3ba55d", outline="", style="pieslice")
        self.create_oval(cx - T.px(6), cy - T.px(6), cx + T.px(6), cy + T.px(6), fill=self._bg, outline=self._ring)
        self.create_text(cx, cy + T.px(20), text="מד רמת סכנה", fill=self._muted, font=("Segoe UI", 9))

    def set_value(self, score, level, extra_text=""):
        """score: מספרי (0..max_score בערך). level: 'LOW'/'MEDIUM'/'HIGH'."""
        cx, cy, r = self._cx, self._cy, self._r
        frac = max(0.0, min(1.0, score / self.max_score if self.max_score else 0))
        # 180° = שמאל (נמוך) -> 0° = ימין (גבוה)
        angle_deg = 180 - (frac * 180)
        angle_rad = math.radians(angle_deg)
        x = cx + r * 0.8 * math.cos(angle_rad)
        y = cy - r * 0.8 * math.sin(angle_rad)

        if self._needle_id:
            self.delete(self._needle_id)
        if self._label_id:
            self.delete(self._label_id)
        if self._sub_label_id:
            self.delete(self._sub_label_id)

        self._needle_id = self.create_line(cx, cy, x, y, width=T.px(3), fill=self._needle, arrow=tk.LAST)
        color = _COLORS.get(level, self._needle)
        self._label_id = self.create_text(
            cx, T.px(16), text=f"{level}  (ניקוד {score:.1f})", fill=color, font=("Segoe UI", 12, "bold")
        )
        if extra_text:
            self._sub_label_id = self.create_text(
                cx, T.px(34), text=extra_text, fill=self._muted, font=("Segoe UI", 8)
            )
