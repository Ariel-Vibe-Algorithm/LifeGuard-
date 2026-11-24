# -*- coding: utf-8 -*-
"""
מחבר Mapillary מול שרת HTTP מקומי אמיתי בפורמט Graph API v4 (אין token ל-Mapillary בסביבת הבדיקה):
אימות ב-header, בלי פנורמות, תמונות לזיכרון בלבד, ייחוס לכל תמונה, ושום גישה ל-Google.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from research_tools import mapillary as M  # noqa: E402
from test_street_vision import HAVE_MODELS  # noqa: E402

TOKEN = "MLY|test|token"


@pytest.fixture
def fake_api():
    seen = []
    img = np.full((240, 320, 3), 30, np.uint8)
    ok, jpg = cv2.imencode(".jpg", img)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization"), self.headers.get("User-Agent")))
            u = urlparse(self.path)
            if u.path == "/images" and self.headers.get("Authorization") != f"OAuth {TOKEN}":
                self.send_response(401); self.end_headers(); return
            if u.path == "/images":
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                data = [{"id": 111, "captured_at": 1700000000000, "is_pano": False, "creator": {"username": "alice"},
                         "computed_geometry": {"type": "Point", "coordinates": [34.781, 32.081]},
                         "thumb_1024_url": base + "/img/111.jpg"},
                        {"id": 222, "is_pano": True, "computed_geometry": {"coordinates": [34.782, 32.082]},
                         "thumb_1024_url": base + "/img/222.jpg"},
                        {"id": 333, "is_pano": False, "computed_geometry": {"coordinates": [34.783, 32.083]},
                         "thumb_1024_url": base + "/img/broken.jpg", "creator": {"username": "bob"}}]
                body = json.dumps({"data": data, "_q": parse_qs(u.query)}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
            elif u.path == "/img/111.jpg":
                body = jpg.tobytes(); self.send_response(200)
            else:
                body = b"not an image"; self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()


def test_requires_token_and_sane_bbox(monkeypatch):
    monkeypatch.delenv("MAPILLARY_TOKEN", raising=False)
    with pytest.raises(M.MapillaryError):
        M.Mapillary()
    c = M.Mapillary(token=TOKEN, base_url="http://127.0.0.1:9")
    with pytest.raises(M.MapillaryError):
        c.search((34.0, 32.0, 35.0, 33.0))          # תיבה ענקית - לא אוספים בכמות
    with pytest.raises(M.MapillaryError):
        c.search((34.8, 32.1, 34.7, 32.0))          # הפוכה


def test_no_google_anywhere():
    src = open(M.__file__, encoding="utf-8").read().lower()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#") and '"""' not in l)
    assert "googleapis" not in code and "maps.google" not in code and "streetview" not in code


@pytest.mark.skipif(not HAVE_MODELS, reason="מודלי CLIP/YuNet לא הורדו")
def test_collect_uses_official_api_memory_only_with_attribution(fake_api, tmp_path):
    from research_tools import street_vision as S
    base, seen = fake_api
    client = M.Mapillary(token=TOKEN, base_url=base, delay_s=0, image_hosts=("127.0.0.1",))
    before = set(os.listdir(str(tmp_path)))
    obs, attr = M.collect((34.77, 32.07, 34.79, 32.09), S.StreetAnalyzer(), client=client, log=lambda m: None)
    assert [o["source_ref"] for o in obs] == ["111"]                 # פנורמה דולגה, תמונה שבורה דולגה
    o = obs[0]
    assert (o["lat"], o["lon"]) == (32.081, 34.781) and o["observed_at"] == 1700000000.0
    assert set(o["attributes"]) == set(S.ATTRIBUTES) and o["luminance"] < 0.2
    assert attr == [{"image_id": "111", "creator": "alice", "license": "CC BY-SA 4.0",
                     "url": "https://www.mapillary.com/app/?pKey=111"}]
    q = json.loads(json.dumps(seen[0][0]))
    assert "bbox=34.77%2C32.07%2C34.79%2C32.09" in q and "fields=" in q
    api = [(p, a) for p, a, _ in seen if p.startswith("/images")]
    imgs = [(p, a) for p, a, _ in seen if p.startswith("/img/")]
    assert api and all(a == f"OAuth {TOKEN}" for _, a in api)              # token ב-header ל-API בלבד
    assert imgs and all(a is None for _, a in imgs)                          # לא נשלח לשרת התמונות
    assert all(ua == M.UA for _, _, ua in seen)
    assert all(TOKEN not in path for path, _, _ in seen)
    assert set(os.listdir(str(tmp_path))) == before                  # שום תמונה לא נשמרה


def test_image_host_allowlist_and_no_redirects():
    assert M.image_host_allowed("https://scontent-fra3-1.xx.fbcdn.net/m1/v/t6/a.jpg")
    assert not M.image_host_allowed("http://scontent.xx.fbcdn.net/a.jpg")          # רק https
    assert not M.image_host_allowed("https://evil.example/fbcdn.net/a.jpg")
    assert not M.image_host_allowed("https://fbcdn.net.evil.example/a.jpg")
    assert not M.image_host_allowed("http://127.0.0.1:8000/a.jpg")                  # רק אם הוגדר במפורש
    assert M.image_host_allowed("http://127.0.0.1:8000/a.jpg", ("127.0.0.1",))
    c = M.Mapillary(token=TOKEN, base_url="http://127.0.0.1:9")
    with pytest.raises(M.MapillaryError):
        c.image({"thumb_1024_url": "https://attacker.example/x.jpg"})
