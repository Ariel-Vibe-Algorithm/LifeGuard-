# -*- coding: utf-8 -*-
"""
עברית + אמוג'י ב-Tk (Windows): Tk מצייר שורה בחלקים בכל החלפת גופן (אמוג'י), מסדר אותם משמאל לימין לפי סדר
הטקסט, ומחיל RTL על כל חלק בנפרד. נמצא בצילום מסך: "⚠️ תל אביב | מיקום IP ..." הוצג בסדר מילים הפוך.
התיקון: חלקים בסדר תצוגה + RLE...PDF לכל חלק טקסט. הטקסט המקורי נשמר (cget מחזיר אותו).
"""
import ui_theme as T

RLE, PDF = "‫", "‬"


def test_plain_hebrew_unchanged():
    assert T.rtl("שלום עולם") == RLE + "שלום עולם" + PDF
    assert T.rtl("hello") == "hello"


def test_emoji_line_split_into_visual_order():
    out = T.rtl("⚠️ תל אביב | מיקום IP משוער")
    # חלק הטקסט קודם (משמאל), האמוג'י אחריו (מימין = תחילת השורה בעברית)
    assert out == RLE + " תל אביב | מיקום IP משוער" + PDF + "⚠️"
    assert T.rtl("שלב 1 ✅ שלב 2") == RLE + " שלב 2" + PDF + "✅" + RLE + "שלב 1 " + PDF


def test_idempotent_and_multiline():
    once = T.rtl("📍 מיקום\nשורה שנייה")
    assert T.rtl(once) == once
    assert once.split("\n")[1] == RLE + "שורה שנייה" + PDF


def test_label_keeps_original_text(shared_root):  # noqa: F811
    import tkinter as tk
    lab = tk.Label(shared_root, text="⚠️ תל אביב | מיקום IP")
    try:
        assert lab.cget("text") == "⚠️ תל אביב | מיקום IP"          # הקוד והבדיקות רואים את הטקסט המקורי
        lab.configure(text="✅ נשלח")
        assert lab.cget("text") == "✅ נשלח"
    finally:
        lab.destroy()


from test_ui_navigation import shared_root  # noqa: E402,F401
