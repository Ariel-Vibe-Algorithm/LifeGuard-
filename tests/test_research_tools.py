# -*- coding: utf-8 -*-
"""כלי המחקר: איסוף בהסכמה (אחסון, פרישה, ייצוא, ממשק) והערכת גלאים (מטריקות מחושבות נכון)."""
import json
import os
import time
import tkinter as tk
import zipfile

import numpy as np
import pytest

from research_tools import acoustic_eval as E
from research_tools import collection as C
from test_ui_navigation import shared_root  # noqa: F401

SR = 16000
FULL = {k: True for k, _ in C.CONSENT_ITEMS}


def tone(seconds, amp=0.5, freq=440.0):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def quiet(seconds, amp=0.003):
    return (np.random.default_rng(0).standard_normal(int(seconds * SR)) * amp).astype(np.float32)


# ===================================================================== איסוף
def test_no_recording_without_required_consent(tmp_path):
    store = C.CollectionStore(str(tmp_path))
    for missing in C.REQUIRED_CONSENT:
        with pytest.raises(C.ConsentError):
            store.new_participant({**FULL, missing: False})
    assert store.participants() == []
    with pytest.raises(C.ConsentError):
        store.save_take("P-ABC123", "kw_help_normal", tone(1), {})


def test_take_is_saved_with_metadata_and_no_identity(tmp_path):
    store = C.CollectionStore(str(tmp_path))
    pid = store.new_participant(FULL, {"age_group": "30-49", "name": "should be dropped"})
    row = store.save_take(pid, "kw_help_loud", tone(3), {"environment": "רחוב", "distance": "קרוב", "device": "Mic"})
    assert row["label"] == "distress_keyword" and row["group"] == "positive" and row["duration_s"] == 3.0
    x, sr = C.read_wav(os.path.join(str(tmp_path), "participants", pid, row["file"]))
    assert sr == SR and abs(len(x) - 3 * SR) < 2
    consent = store.consent(pid)
    assert consent["consent_version"] == C.CONSENT_VERSION and "name" not in consent["demographics"]
    assert C.quality(C._to_int16(np.ones(SR, dtype=np.float32)))["clipped"] is True
    assert C.quality(C._to_int16(quiet(1)))["too_quiet"] is True


def test_withdrawal_deletes_everything_and_export_respects_training_consent(tmp_path):
    store = C.CollectionStore(str(tmp_path))
    a = store.new_participant(FULL)
    b = store.new_participant({**FULL, "training": False})   # הסכים להקלטה אבל לא לאימון
    c = store.new_participant(FULL)
    for pid in (a, b, c):
        store.save_take(pid, "kw_police", tone(1), {})
    assert store.delete_participant(c) == 1
    assert not os.path.exists(os.path.join(str(tmp_path), "participants", c))
    assert c in store.withdrawn()
    out = os.path.join(str(tmp_path), "export.zip")
    assert store.export_training_set(out) == 1
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        rows = [json.loads(line) for line in z.read("manifest.jsonl").decode().splitlines()]
    assert all(n.startswith(a) or n in ("manifest.jsonl", "README.txt") for n in names)
    assert [r["participant"] for r in rows] == [a]
    with pytest.raises(ValueError):
        store.delete_participant("../../etc")


def test_collection_ui_full_flow_without_microphone(tmp_path, shared_root):  # noqa: F811
    from research_tools.collection_ui import CollectionApp
    root = shared_root
    store = C.CollectionStore(str(tmp_path))
    played = []
    win = tk.Toplevel(root)
    app = CollectionApp(win, store, recorder=lambda s: tone(s, 0.3), player=played.append, device_name="Test Mic")

    def pump(sec=0.3):
        end = time.time() + sec
        while time.time() < end:
            root.update()
            time.sleep(0.02)
    try:
        assert str(app.start_btn.cget("state")) == "disabled"  # בלי הסכמה - אי אפשר להתחיל
        for k in C.REQUIRED_CONSENT:
            app.consent_vars[k].set(True)
        app._update_consent_btn()
        app.accept_consent()
        assert app.pid and store.consent(app.pid)["items"]["training"] is False
        app.start_protocol()
        app.record()
        pump(1.6)
        assert "תקין" in app.status.cget("text")
        app.play()
        assert played
        app.save()
        app.next_item()                     # דילוג
        assert app.index == 2
        rows = store.manifest(app.pid)
        assert [r["prompt_id"] for r in rows] == [C.PROTOCOL[0]["id"]] and rows[0]["device"] == "Test Mic"
        app.show_done()
    finally:
        win.destroy()


# ===================================================================== הערכה
def test_event_merging():
    s = np.array([0, 1, 1, 0, 1, 0, 0, 0, 0, 0, 1, 0], dtype=float)
    assert E.events(s, 0.5, hop_seconds=0.25, merge_gap_seconds=0.25) == [(1, 4), (10, 10)]
    assert E.events(s, 0.5, hop_seconds=0.25, merge_gap_seconds=0) == [(1, 2), (4, 4), (10, 10)]
    assert E.events(s, 2, 0.25) == []


def test_fa_per_hour_and_recall_at_budget_are_computed_correctly():
    det = E.RmsDetector()
    pos = [E.Clip(f"p{i}", np.concatenate([quiet(1), tone(1, 0.4), quiet(1)]), SR, True, "distress") for i in range(4)]
    pos.append(E.Clip("p_weak", tone(3, 0.02), SR, True, "distress"))          # מצוקה חלשה (לחישה)
    neg = [E.Clip("n_quiet", quiet(3600), SR, False, "ambient"),                 # שעה של שקט
           E.Clip("n_loud", np.concatenate([quiet(10), tone(2, 0.6), quiet(10)]), SR, False, "cheering")]
    res = E.evaluate(det, pos + neg, budgets=(1.0, 0.5))
    hours = (3600 + 22) / 3600
    assert res["negative_hours"] == pytest.approx(hours, rel=1e-3)
    # תקציב 1 לשעה: מותר בדיוק התראה אחת (קריאת השמחה החזקה) ב-1.006 שעות -> אפשר סף נמוך מספיק
    # לתפוס את כל 5 הקטעים, כולל הלחישה (הרקע השקט עוד יותר חלש)
    best = res["recall_at_fa_budget"]["1.0"]
    assert best["recall"] == 1.0 and best["false_alarms"] == 1
    assert best["fa_per_hour"] == pytest.approx(1 / hours, rel=1e-3)
    # תקציב 0.5 לשעה = אפס התראות. קריאת השמחה חזקה מכל קטעי המצוקה, ולכן גלאי עוצמה לא יכול
    # להבחין ביניהם: recall אפס. זו בדיוק המגבלה שהמנגנון הנוכחי סובל ממנה.
    assert res["recall_at_fa_budget"]["0.5"]["recall"] == 0.0
    md = E.report_markdown(res)
    assert "rms_baseline" in md and "⚠️" in md                                     # אזהרת גודל מדגם


def test_collection_export_loads_into_evaluator(tmp_path):
    store = C.CollectionStore(str(tmp_path))
    pid = store.new_participant(FULL)
    store.save_take(pid, "kw_help_loud", tone(3, 0.5), {})
    store.save_take(pid, "neg_laugh", tone(4, 0.1), {})
    store.save_take(pid, "ambient", quiet(10), {})
    out = os.path.join(str(tmp_path), "x.zip")
    store.export_training_set(out)
    for source in (out, str(tmp_path)):
        clips = E.load_collection(source)
        assert sorted((c.label, c.positive) for c in clips) == [("ambient", False), ("distress_keyword", True),
                                                                 ("laughter", False)]
        assert all(c.sr == SR for c in clips)
    assert E.wilson_interval(8, 10)[0] < 0.8 < E.wilson_interval(8, 10)[1]
