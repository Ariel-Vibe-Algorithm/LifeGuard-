# -*- coding: utf-8 -*-
"""
גלאי הסכנה של ה-PWA (server/web/hazard_engine.js + hazard_core.js) == מנוע ה-Python של Windows.

1. הקבצים שה-PWA מגיש תואמים למקור: server/web/models/hazard_heads.json זהה לביט ל-models/*.npz ול-shout_sequence.json
   (tools/build_hazard_web.py), ו-yamnet.bin/yamnet.json תואמים ל-TFLite (tools/build_yamnet_web.py).
2. Node מריץ את קוד ה-JS על וקטורי הבדיקה (tests/web_e2e/fixtures) ומשווה לפלט של Python/TFLite:
   resample_poly, spectral_features, YAMNet, audio_intel.analyze (v1/v2/ראש מצוקה), hazard_monitor.process
   (קיבוץ / שקט / הגבלת קצב / סינון חומרה / SOS / רצף צעקות), AlertManager, contacts_message, קבועים ומחרוזות.
דילוג אם אין node.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_MODELS = os.path.join(ROOT, "server", "web", "models")
E2E_DIR = os.path.join(ROOT, "tests", "web_e2e")


def _arr(o):
    return np.frombuffer(base64.b64decode(o["b64"]), dtype="<f4")


def test_served_heads_are_bit_identical_to_the_npz_models():
    with open(os.path.join(WEB_MODELS, "hazard_heads.json"), encoding="utf-8") as fh:
        H = json.load(fh)
    v1 = np.load(os.path.join(ROOT, "models", "hazard_audio.npz"), allow_pickle=False)
    v2 = np.load(os.path.join(ROOT, "models", "hazard_audio_v2.npz"), allow_pickle=False)
    dh = np.load(os.path.join(ROOT, "models", "distress_head.npz"), allow_pickle=False)
    c1, c2 = json.loads(str(v1["card"][0])), json.loads(str(v2["card"][0]))
    assert H["v1"]["version"] == c1["version"] and H["v2"]["version"] == c2["version"]
    for cat, c in c1["categories"].items():
        e = H["v1"]["categories"][cat]
        assert (e["threshold"], e["platt"], e["champion"], e["yam_idx"]) == (c["threshold"], c["platt"], c["champion"], c["yam_idx"])
        if c["champion"] == "head":
            for k in ("mean", "std", "w"):
                assert np.array_equal(_arr(e["head"][k]), v1[f"{cat}__{k}"])
            assert e["head"]["b"] == float(v1[f"{cat}__b"][0])
    for cat, c in c2["categories"].items():
        assert {k: H["v2"]["categories"][cat][k] for k in ("t1", "t2", "two_stage", "platt")} == \
            {k: c[k] for k in ("t1", "t2", "two_stage", "platt")}
        for st in ("s1", "s2"):
            for k in ("mean", "std", "w"):
                assert np.array_equal(_arr(H["v2"]["heads"][cat][st][k]), v2[f"{cat}__{st}__{k}"])
    info = json.loads(str(dh["info"][0]))
    assert H["distress"]["threshold_logit"] == info["threshold_logit"] and H["distress"]["smooth"] == info["smooth"]
    assert np.array_equal(_arr(H["distress"]["head"]["w"]), dh["w"])
    with open(os.path.join(ROOT, "models", "shout_sequence.json"), encoding="utf-8") as fh:
        assert H["sequence"] == json.load(fh)


def test_served_yamnet_matches_its_source():
    with open(os.path.join(WEB_MODELS, "yamnet.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    src = os.path.join(ROOT, man["source"])
    with open(src, "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == man["source_sha256"]
    with open(os.path.join(WEB_MODELS, "yamnet.bin"), "rb") as fh:
        blob = fh.read()
    assert len(blob) == man["bin_bytes"] and hashlib.sha256(blob).hexdigest() == man["bin_sha256"]
    assert man["verified_max_diff_vs_tflite"] < 1e-4 and "Apache-2.0" in man["license"]


def test_js_equals_python_on_the_parity_vectors():
    node = shutil.which("node")
    if not node:
        pytest.skip("node לא מותקן")
    proc = subprocess.run([node, "run_hazard_parity.mjs"], cwd=E2E_DIR, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    out = proc.stdout or ""
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("PARITY_SUMMARY ")), None)
    assert line, f"no summary\nstdout:\n{out[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
    summary = json.loads(line[len("PARITY_SUMMARY "):])
    report = "\n".join(f"{'PASS' if c['ok'] else 'FAIL'} {c['name']}: {c.get('error') or c.get('info') or ''}"
                       for c in summary["checks"])
    print(report)
    assert proc.returncode == 0 and summary["ok"], report
    assert len(summary["checks"]) >= 9
