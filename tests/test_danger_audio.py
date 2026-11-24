# -*- coding: utf-8 -*-
"""
אבן דרך 4 - הקלטות סכנה ממאגרים פתוחים (research_tools/danger_audio_download.py, danger_audio_external.py):
- ההורדה: checksum לא תואם נכשל; פריסה חסומה מפני zip slip; כל מקור נושא רישיון/ייחוס.
- הקריאה: wav של 16/24 ביט ו-float, סטריאו -> מונו.
- המדידה: חיובי נמדד ברמת קטע, רקע בחלונות של 1.5 ש'; כלל ההחלטה (> 1% חלונות שגויים) מחושב נכון.
- אין תמלול דיבור במאגרי רחוב (רק המנוע האקוסטי).
"""
import io
import json
import os
import zipfile

import numpy as np
import pytest
from scipy.io import wavfile

from research_tools import danger_audio_download as D
from research_tools import danger_audio_external as E

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_source_declares_license_attribution_and_role_and_no_gated_or_signup():
    for name, s in D.SOURCES.items():
        assert s.get("attribution") and s.get("role") and s.get("name"), name
        assert "zenodo" in s or s.get("license"), name               # Zenodo: הרישיון נקרא מה-API
        assert "url" not in s or s["url"].startswith("https://"), name
    assert D.UA == "LifeGuard-Research/1.0"                            # בלי פרטים מזהים


def test_zip_slip_is_blocked(tmp_path, monkeypatch):
    d = tmp_path / "evil"
    d.mkdir()
    with zipfile.ZipFile(d / "x.zip", "w") as z:
        z.writestr("../../outside.txt", "nope")
    monkeypatch.setattr(D, "BASE", str(tmp_path))
    monkeypatch.setitem(D.SOURCES, "evil", {"dir": "evil"})
    with pytest.raises(RuntimeError):
        D.extract("evil")
    assert not (tmp_path.parent / "outside.txt").exists()


def test_checksum_mismatch_fails(tmp_path, monkeypatch):
    payload = b"hello"
    rec = {"id": 1, "doi": "x", "metadata": {"license": {"id": "cc-by-4.0"}, "access_right": "open", "version": "1"},
           "files": [{"key": "a.bin", "size": len(payload), "checksum": "md5:" + "0" * 32,
                      "links": {"self": "https://example.invalid/a.bin"}}]}
    monkeypatch.setattr(D, "BASE", str(tmp_path))
    monkeypatch.setattr(D, "MANIFEST", str(tmp_path / "MANIFEST.json"))
    monkeypatch.setitem(D.SOURCES, "t", {"zenodo": 1, "files": ["a.bin"], "dir": "t", "name": "t",
                                         "attribution": "t", "role": "t"})
    monkeypatch.setattr(D, "_get_json", lambda url: rec)

    def fake_download(url, dest, size=None):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(payload)
    monkeypatch.setattr(D, "_download", fake_download)
    with pytest.raises(RuntimeError, match="checksum"):
        D.fetch("t")
    assert not (tmp_path / "MANIFEST.json").exists()                  # לא נרשם כ"מאומת"


@pytest.mark.parametrize("dtype,scale", [(np.int16, 32767), (np.int32, 2 ** 31 - 1), (np.float32, 1.0)])
def test_read_wav_formats_and_stereo(dtype, scale):
    t = np.linspace(0, 1, 8000, endpoint=False)
    mono = 0.5 * np.sin(2 * np.pi * 440 * t)
    stereo = np.stack([mono, mono], axis=1)
    buf = io.BytesIO()
    wavfile.write(buf, 8000, (stereo * scale).astype(dtype))
    x, sr = E.read_wav(buf.getvalue())
    assert sr == 8000 and x.ndim == 1 and abs(float(np.abs(x).max()) - 0.5) < 0.01


def _write_cache(tmp_path, name, rows):
    p = tmp_path / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _w(events=(), distress=False, points=0):
    return {"events": list(events), "distress": distress, "points": points, "quality": "ok"}


def test_summary_recall_hard_negatives_background_and_decision_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "CACHE", str(tmp_path))
    pos = [{"id": f"g{i}", "role": "positive", "targets": ["gunshot"], "group": "rifle|phone",
            "clip": _w(["gunshot"] if i < 6 else [], points=3 if i < 6 else 0)} for i in range(20)]
    _write_cache(tmp_path, "gunshot_fit", pos)
    hn = [{"id": f"l{i}", "role": "hard_negative", "targets": [], "group": "laugh",
           "clip": _w(distress=i == 0)} for i in range(20)]
    _write_cache(tmp_path, "nonspeech7k", hn + [{"id": "x", "role": "excluded_fsd_overlap", "group": "laugh",
                                                 "targets": []}])
    bg = [{"id": f"t{i}", "role": "background", "targets": [], "group": "metro|b",
           "windows": [_w(["glass_break"], points=2) if (i == 0 and j == 0) else _w() for j in range(5)]}
          for i in range(10)]                                          # 1/50 = 2% > 1%
    _write_cache(tmp_path, "tau2020_mobile", bg)
    out = E.summarize()
    r = out["datasets"]["gunshot_fit"]["recall"]["gunshot"]
    assert r["n"] == 20 and r["rate"] == pytest.approx(0.3) and r["ci"][0] < 0.3 < r["ci"][1]
    assert out["datasets"]["nonspeech7k"]["excluded_fsd_overlap"] == 1
    assert out["datasets"]["nonspeech7k"]["hard_negative"]["all"]["distress_rate"] == pytest.approx(0.05)
    b = out["datasets"]["tau2020_mobile"]["background"]["all"]
    assert b["windows"] == 50 and b["points"] == pytest.approx(0.02)
    assert out["decision"]["glass_break"]["recommend_demote"] is True
    assert out["decision"]["gunshot"]["recommend_demote"] is False


def test_no_speech_recognition_in_external_evaluation():
    src = open(os.path.join(ROOT, "research_tools", "danger_audio_external.py"), encoding="utf-8").read()
    assert "speech_local" not in src and "transcribe" not in src and "detect_dangerous_keywords" not in src


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "research", "danger_audio_external_v1.json")),
                    reason="המבחן החיצוני לא הורץ")
def test_external_report_structure():
    out = json.load(open(os.path.join(ROOT, "research", "danger_audio_external_v1.json"), encoding="utf-8"))
    assert out["window_s"] == 1.5 and out["datasets"]
    for ds in out["datasets"].values():
        for r in ds["recall"].values():
            assert 0 <= r["rate"] <= 1 and r["n"] > 0


def test_operating_point_interpolation_and_never_raises_threshold():
    from research_tools import hazard_operating_point as OP
    table = [[1.0, 50.0], [6.0, 2.66], [10.0, 0.06], [12.0, 0.0]]
    t = OP.interp_fa_threshold(table, 2.0, default=99)
    assert 6.0 < t < 10.0                                   # בין שתי הנקודות שחוצות את היעד - לא 10
    assert OP.interp_fa_threshold([[1.0, 0.5]], 2.0, default=99) == 1.0
    assert OP.interp_fa_threshold([[1.0, 9.0], [2.0, 8.0]], 2.0, default=99) == 99
    card = {"categories": {"gunshot": {"threshold": 5.0, "fa_table": table}}}
    bg = [{"scores": {"gunshot": float(s)}} for s in range(10)]
    thr = OP.pick_thresholds(card, ["gunshot"], bg)["gunshot"]
    assert thr["new"] <= thr["old"] == 5.0                  # לעולם לא מעלים סף
