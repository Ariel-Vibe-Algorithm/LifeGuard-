# -*- coding: utf-8 -*-
"""ניסוי שטח: איחוד התראות, ספירת התראות שווא מול אירועים מבוימים, וההבטחה שאין אודיו ביומן."""
import json

from research_tools import field_test as F


def _log(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": {"mode": "live", "audio_stored": False}}) + "\n")
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_report_counts_false_alarms_per_day_and_staged_detections(tmp_path):
    t0 = 1_700_000_000.0
    rows = []
    for i in range(int(2 * 3600 / F.WINDOW_S)):            # שעתיים
        t = t0 + i * F.WINDOW_S
        detected = (1000 <= i <= 1002) or i == 1500          # אירוע אחד (3 חלונות רצופים) + התראה בודדת
        loud = i % 100 == 0                                   # רעש חזק כל ~7 דקות (תנועה...)
        rows.append({"t": t, "score": 9.0 if detected else -4.0, "detected": detected, "rms": 0.3 if loud else 0.01})
    p = str(tmp_path / "day.jsonl")
    _log(p, rows)
    staged = [(t0 + 1000 * F.WINDOW_S, t0 + 1002 * F.WINDOW_S, "distress")]
    r = F.report([p], staged)
    assert r["hours"] == 2.0 and r["windows"] == 1800
    assert r["model"]["alarms"] == 2 and r["model"]["staged_detected"] == 1 and r["model"]["false_alarms"] == 1
    assert r["model"]["false_per_hour"] == 0.5 and r["model"]["false_per_16h_day"] == 8.0
    # עוצמה: כל רעש חזק = התראה. 18 חלונות רועשים, אחד מהם (1000) נופל על האירוע המבוים -> 17 שווא
    assert r["rms_baseline"]["alarms"] == 18 and r["rms_baseline"]["false_alarms"] == 17
    assert "warning" in r
    # היומן מכיל רק מספרים
    for line in open(p, encoding="utf-8"):
        d = json.loads(line)
        assert "audio" not in d and not any(isinstance(v, list) for v in d.values())


def test_alarm_events_merge_close_windows():
    rows = [{"t": t, "detected": d} for t, d in ((0, 1), (4, 1), (8, 0), (12, 1), (40, 1))]
    assert F.alarm_events(rows) == [(0, 12), (40, 40)]
