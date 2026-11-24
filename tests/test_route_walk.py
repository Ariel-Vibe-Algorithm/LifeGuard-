# -*- coding: utf-8 -*-
"""
צילום מסלול הליכה: שרת אמיתי (הסכמה, מנות, בעלות, פרישה) + חלון אמיתי עם סרטון ו-GPX אמיתיים,
מודלים אמיתיים (CLIP + YuNet) - ובדיקה שאף תמונה לא נשמרת ולא נשלחת.
"""
import json
import os
import threading
import time
import types

import pytest

from backend_client import BackendClient, BackendError
from route_walk_ui import trim_endpoints
from test_street_vision import HAVE_MODELS, _gpx, _video
from test_ui_navigation import shared_root  # noqa: F401

PW = "StrongPass!2026"
OBS = {"lat": 32.08, "lon": 34.78, "attributes": {"lighting": 0.3, "evil": 9}, "luminance": 0.2,
       "image": "data:image/jpeg;base64,AAAA", "labels": {"felt_safe": 0, "hack": 1}}


@pytest.fixture
def server(tmp_path):
    from server.lifeguard_server import create_server
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), tick_seconds=0.5)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.service.close()


def _c(url, name):
    c = BackendClient(url)
    c.login_or_register(name, PW, name)
    return c


def test_server_requires_consent_stores_numbers_only_and_supports_withdrawal(server):
    httpd, url = server
    dana, eve = _c(url, "dana"), _c(url, "eve")
    with pytest.raises(BackendError) as e:
        dana._req("POST", "/street-intel/walks", {"observations": [OBS], "consent": {"accepted": False}})
    assert e.value.code == "consent_required"
    r = dana.submit_walk([OBS] * 320, "walk-1.0", model="m", batch=150)     # 3 מנות, הליכה אחת
    assert r["stored"] == 320
    walks = dana.my_walks()["walks"]
    assert len(walks) == 1 and walks[0]["walk_id"] == r["walk_id"] and walks[0]["n"] == 320
    row = httpd.service._q("SELECT * FROM street_observations LIMIT 1", one=True)
    attrs, consent = json.loads(row["attributes"]), json.loads(row["consent"])
    assert attrs["model_scores"] == {"lighting": 0.3} and attrs["user_labels"] == {"felt_safe": 0}
    assert row["media_ref"] is None and "image" not in row["attributes"] and consent["media_stored"] is False
    # אחר לא יכול להוסיף להליכה שלי, ולא רואה/מוחק אותה
    with pytest.raises(BackendError) as e:
        eve._req("POST", "/street-intel/walks", {"walk_id": r["walk_id"], "observations": [OBS],
                                                 "consent": {"accepted": True, "version": "walk-1.0"}})
    assert e.value.status == 404
    assert eve.my_walks()["walks"] == [] and eve.delete_walks()["deleted"] == 0
    with pytest.raises(BackendError):
        dana._req("POST", "/street-intel/walks", {"observations": [OBS] * 201,
                                                  "consent": {"accepted": True, "version": "walk-1.0"}})
    assert dana.delete_walks()["deleted"] == 320 and dana.my_walks()["walks"] == []
    # עדיין אין "ציון רחוב" - תצפיות לבדן לא הופכות לציון מוצג
    assert dana.street_intel("34,32,35,33")["segments"] == []


@pytest.mark.skipif(not HAVE_MODELS, reason="מודלי CLIP/YuNet לא הורדו")
def test_route_walk_window_end_to_end(server, user, shared_root, tmp_path):  # noqa: F811
    import config
    from route_walk_ui import RouteWalkWindow
    httpd, url = server
    root = shared_root
    client = _c(url, "walker")
    posted = []
    app = types.SimpleNamespace(username="walker", client=client,
                                _post=lambda fn, *a: posted.append((fn, a)))
    vp, gp = str(tmp_path / "walk.avi"), str(tmp_path / "walk.gpx")
    _video(vp, seconds=16)
    _gpx(gp, [("2026-11-06T18:00:00Z", 32.0800, 34.7800), ("2026-11-06T18:00:16Z", 32.1000, 34.7800)])
    assert config.DATA_DIR.startswith(str(tmp_path.parent)) or "pytest" in config.DATA_DIR  # לא data/ האמיתי

    def pump(pred, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            while posted:
                fn, a = posted.pop(0)
                fn(*a)
            root.update()
            if pred():
                return True
            time.sleep(0.05)
        return False

    win = RouteWalkWindow(root, app)
    try:
        win.video, win.gpx = vp, gp
        win._show_files()
        assert str(win.analyze_btn.cget("state")) == "disabled"          # בלי הסכמה - אין ניתוח
        win.consent_var.set(True)
        win._update_buttons()
        assert str(win.analyze_btn.cget("state")) == "disabled"          # בלי אישור גיל 18+
        win.adult_var.set(True)
        win.send_var.set(True)
        win._update_buttons()
        win.analyze()
        assert pump(lambda: bool(win.obs) or "❗" in win.status.cget("text")), win.status.cget("text")
        assert "✅" in win.status.cget("text"), win.status.cget("text")
        assert len(win.obs) == 8 and all(o["lat"] is not None for o in win.obs)
        idx = win.label_indices()
        assert idx and len(win._photos) == len(idx)                       # תמונות מטושטשות - בזיכרון בלבד
        for i in idx:
            lv, sv = win.label_vars[i]
            lv.set("0")
            sv.set("0")
        sent = trim_endpoints(win.obs, meters=win.trim_m)
        assert 0 < len(sent) < len(win.obs)                               # התחלה וסוף (בית?) לא נשלחים
        win.save_and_send()
        assert pump(lambda: "נשלחו לשרת" in win.send_status.cget("text") or "❗" in win.send_status.cget("text"))
        assert f"נשלחו לשרת {len(sent)}" in win.send_status.cget("text"), win.send_status.cget("text")
        rows = httpd.service._q("SELECT lat, lon, attributes FROM street_observations")
        start, end = (win.obs[0]["lat"], win.obs[0]["lon"]), (win.obs[-1]["lat"], win.obs[-1]["lon"])
        from research_tools.street_vision import haversine_m
        assert all(haversine_m(start, (r["lat"], r["lon"])) > 150 and haversine_m(end, (r["lat"], r["lon"])) > 150
                   for r in rows)
        sent_ids = {id(o) for o in sent}
        expect = sum(1 for i in idx if id(win.obs[i]) in sent_ids)
        assert sum(1 for r in rows if json.loads(r["attributes"]).get("user_labels") ==
                   {"lighting": 0, "felt_safe": 0}) == expect
        # במחשב: מספרים + embeddings + תיוגים. אף קובץ תמונה בשום מקום בתיקיית הנתונים
        saved = os.listdir(os.path.dirname(win.saved_path))
        assert any(f.endswith(".jsonl") for f in saved) and any(f.endswith("_labels.json") for f in saved)
        for dirpath, _, files in os.walk(config.DATA_DIR):
            assert not [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))], dirpath
        win.delete_local()                                                # מחיקה מקומית
        assert not [f for f in os.listdir(os.path.dirname(win.saved_path)) if f.startswith("local_")]
    finally:
        win.destroy()


def test_trim_endpoints_removes_home_and_destination():
    obs = [{"lat": 32.08 + i * 0.0005, "lon": 34.78} for i in range(20)]      # ~55 מ' בין נקודות, ~1 ק"מ
    obs.insert(3, {"lat": None, "lon": None})
    kept = trim_endpoints(obs)
    assert kept and all(o["lat"] is not None for o in kept)
    assert min(o["lat"] for o in kept) > 32.08 + 0.0013 and max(o["lat"] for o in kept) < 32.08 + 19 * 0.0005 - 0.0013
    assert trim_endpoints(obs[:3]) == []                                    # מסלול קצר - לא שולחים כלום
