# -*- coding: utf-8 -*-
"""מסך האימון הקולי: הקלטות נשמרות מוצפנות, מילות קוד לא מוצגות, אימון זמין רק כשיש מספיק, תוצאות מוצגות."""
import os
import time
import types

import numpy as np

import personal_kws as PK
from test_ui_navigation import shared_root  # noqa: F401


def _pump(root, pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if pred():
            return True
        time.sleep(0.03)
    return False


def test_voice_training_flow(user, shared_root):  # noqa: F811
    from voice_training_ui import VoiceTrainingWindow
    rng = np.random.default_rng(0)
    recorder = lambda seconds: (0.2 * rng.standard_normal(int(seconds * PK.SR))).astype(np.float32)  # noqa: E731
    trained = []

    def trainer(takes, free, log=None):
        trained.append({k: len(v) for k, v in takes.items()})
        from research_tools.yamnet_detector import LogisticModel
        m = LogisticModel(epochs=5).fit(np.r_[np.zeros((4, 3)), np.ones((4, 3))], np.r_[np.zeros(4), np.ones(4)])
        models = {pid: {"model": m, "threshold": 1.0} for pid in takes}
        rep = {"variations_total": 1234, "phrases": {pid: {"heldout_clean_detected": "2/2", "heldout_noisy_recall": 0.9,
                                                            "false_positives": 0} for pid in takes}}
        return models, rep

    app = types.SimpleNamespace(username=user)
    win = VoiceTrainingWindow(shared_root, app, recorder=recorder, trainer=trainer)
    try:
        secret = "blue lantern"                                    # מילת הקוד של משתמש הבדיקה
        texts = [w.cget("text") for w in _all(win) if hasattr(w, "cget") and "text" in w.keys()]
        assert not any(secret in t for t in texts)                 # מילת הקוד לא מוצגת על המסך
        assert str(win.train_btn.cget("state")) == "disabled"
        for pid in win.rows:
            for _ in range(PK.HOLDOUT_TAKES + 2):
                win.record_take(pid)
                n = win.counts[pid]
                assert _pump(shared_root, lambda: win.counts[pid] == n + 1 or False) or win.counts[pid] >= 1
        assert str(win.train_btn.cget("state")) == "disabled"      # עוד אין דיבור חופשי
        win.record_free()
        assert _pump(shared_root, lambda: "free_speech" in win.store.names())
        assert _pump(shared_root, lambda: str(win.train_btn.cget("state")) == "normal")
        enc = [f for f in os.listdir(win.store.root) if f.endswith(".enc")]
        assert len(enc) == len(win.rows) * (PK.HOLDOUT_TAKES + 2) + 1
        win.train()
        assert _pump(shared_root, lambda: "1,234" in win.status.cget("text"))
        assert trained and all(v == PK.HOLDOUT_TAKES + 2 for v in trained[0].values())
        assert "models" in win.store.names()
        win.delete_all()
        assert not [f for f in os.listdir(win.store.root) if f.endswith(".enc")]
    finally:
        win.destroy()


def _all(w):
    out = [w]
    for c in w.winfo_children():
        out += _all(c)
    return out
