# -*- coding: utf-8 -*-
"""סיווג רחובות מסרטון: מסלול GPS, דגימת וידאו אמיתי, פרטיות (טשטוש, אין שמירת תמונות), CLIP, צבירה ואימון."""
import json
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
S = pytest.importorskip("research_tools.street_vision")
HAVE_MODELS = os.path.exists(os.path.join(S.CLIP_DIR, "vision_model_quantized.onnx")) and os.path.exists(S.YUNET)
needs_models = pytest.mark.skipif(not HAVE_MODELS, reason="מודלי CLIP/YuNet לא הורדו (research/models)")
SANITY = os.path.join(os.path.dirname(S.MODELS), "samples", "street_sanity")


def _gpx(path, pts):
    body = "".join(f'<trkpt lat="{la}" lon="{lo}"><time>{t}</time></trkpt>' for t, la, lo in pts)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
                 + body + "</trkseg></trk></gpx>")


def _video(path, seconds=6, fps=10, bright_from=3.0):
    """סרטון אמיתי (MJPG): חושך בהתחלה, ואחרי bright_from שניות - בהיר."""
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps, (320, 240))
    for i in range(seconds * fps):
        v = 15 if i / fps < bright_from else 200
        frame = np.full((240, 320, 3), v, dtype=np.uint8)
        cv2.putText(frame, str(i), (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, (255 - v,) * 3, 2)
        w.write(frame)
    w.release()


def test_gpx_parsing_and_interpolation_never_invents_positions(tmp_path):
    p = str(tmp_path / "w.gpx")
    _gpx(p, [("2026-11-06T18:00:00Z", 32.0800, 34.7800), ("2026-11-06T18:00:10.5Z", 32.0810, 34.7800),
             ("2026-11-06T18:05:00Z", 32.0900, 34.7900)])
    tr = S.parse_gpx(p)
    assert len(tr) == 3 and tr[1][0] - tr[0][0] == pytest.approx(10.5)
    mid = S.position_at(tr, tr[0][0] + 5.25)
    assert mid == pytest.approx((32.0805, 34.78))
    assert S.position_at(tr, tr[1][0] + 100) is None       # חור של 5 דקות במסלול - לא ממציאים
    assert S.position_at(tr, tr[0][0] - 120) is None       # לפני תחילת המסלול
    assert S.haversine_m((32.08, 34.78), (32.081, 34.78)) == pytest.approx(111.2, rel=0.01)


def test_video_sampling_reads_real_file_without_writing_frames(tmp_path):
    vp = str(tmp_path / "walk.avi")
    _video(vp)
    before = set(os.listdir(str(tmp_path)))
    samples = S.sample_video(vp, every_s=1.0)
    assert [round(t) for t, _ in samples] == [0, 1, 2, 3, 4, 5]
    assert set(os.listdir(str(tmp_path))) == before               # שום פריים לא נכתב לדיסק
    lum = [S.luminance(f)[0] for _, f in samples]
    assert lum[0] < 0.15 and lum[-1] > 0.7                         # בהירות נמדדת, לא מנוחשת


def test_aggregation_into_street_cells():
    dlat = 25.0 / 111320.0
    base = (np.floor(32.08 / dlat) + 0.5) * dlat          # מרכז תא - כל הנקודות בתוכו
    obs = [{"lat": base + i * 1e-6, "lon": 34.78, "walk_id": f"w{i % 2}", "luminance": 0.2,
            "attributes": {"lighting": 0.2}} for i in range(6)]
    obs.append({"lat": 32.09, "lon": 34.78, "walk_id": "w0", "luminance": 0.8, "attributes": {"lighting": 0.9}})
    obs.append({"lat": None, "lon": None, "walk_id": "w0", "luminance": 0.5, "attributes": {"lighting": 0.5}})
    cells = S.aggregate(obs)
    assert len(cells) == 2
    big = max(cells, key=lambda c: c["observations"])
    assert big["observations"] == 6 and big["walks"] == 2 and big["attributes"]["lighting"] == 0.2


def test_training_uses_walk_grouped_cross_validation():
    rng = np.random.default_rng(0)
    obs, labels = [], {}
    for walk in range(4):
        for j in range(20):
            dark = j % 2 == 0
            e = rng.normal(0, 1, 512).astype(np.float32)
            e[:10] += -2 if dark else 2
            obs.append({"walk_id": f"walk{walk}", "embedding": (e / np.linalg.norm(e)).astype(np.float16)})
            labels[len(obs) - 1] = 0 if dark else 1
    m = S.train_attribute(obs, labels)
    assert m.info["walks"] == 4 and m.info["cv_evaluated"] == 80 and m.info["cv_accuracy_by_walk"] > 0.9


@needs_models
def test_full_walk_pipeline_on_real_video(tmp_path):
    vp, gp = str(tmp_path / "walk.avi"), str(tmp_path / "walk.gpx")
    _video(vp, seconds=6)
    _gpx(gp, [("2026-11-06T18:00:00Z", 32.0800, 34.7800), ("2026-11-06T18:00:06Z", 32.0806, 34.7800)])
    an = S.StreetAnalyzer()
    obs = an.analyze_walk(vp, S.parse_gpx(gp), every_s=1.0, walk_id="w1", log=lambda m: None)
    assert len(obs) == 6 and all(o["lat"] is not None for o in obs)
    assert obs[0]["lat"] < obs[-1]["lat"]
    assert set(obs[0]["attributes"]) == set(S.ATTRIBUTES)
    assert all(0 <= v <= 1 for o in obs for v in o["attributes"].values())
    assert obs[0]["luminance"] < 0.15 < 0.7 < obs[-1]["luminance"]
    assert obs[0]["embedding"].shape == (512,)
    out = str(tmp_path / "obs.jsonl")
    S.save_observations(obs, out)
    row = json.loads(open(out, encoding="utf-8").readline())
    assert "embedding" not in row and row["walk_id"] == "w1"
    assert not [f for f in os.listdir(str(tmp_path)) if f.lower().endswith((".jpg", ".png"))]   # אין תמונות


@needs_models
@pytest.mark.skipif(not os.path.exists(os.path.join(SANITY, "night_lit_1.jpg")), reason="תמונות בדיקה לא הורדו")
def test_faces_are_blurred_before_analysis():
    img = cv2.imread(os.path.join(SANITY, "night_lit_1.jpg"))
    blur = S.FaceBlur()
    out, n = blur(img)
    assert n >= 1
    _, after = blur(out)
    assert after == 0                                   # אחרי הטשטוש הגלאי כבר לא מוצא פנים
    assert not np.array_equal(out, img) and img.shape == out.shape


@needs_models
def test_clip_text_and_image_embeddings_are_consistent():
    c = S.Clip()
    assert c.tok.encode("a photo").ids[0] == 49406       # BOS של CLIP
    dark = np.full((300, 400, 3), 5, np.uint8)
    e = c.embed_images([dark])
    assert e.shape == (1, 512) and abs(np.linalg.norm(e[0]) - 1) < 1e-4
    zs = c.zero_shot(e)
    assert set(zs) == set(S.ATTRIBUTES) and all(0 <= v[0] <= 1 for v in zs.values())
