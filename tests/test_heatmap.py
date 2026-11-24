# -*- coding: utf-8 -*-
"""מפת סביבה (server/heatmap.py): גורמי סביבה ברמת רחוב, בלי ציון סכנה לשכונה, בלי דיווח יחיד לא מאומת."""
import datetime as dt
import json
import time

import pytest

from server import risk_factors_layer as RF
from server import safety_intel as SI
from server.lifeguard_server import LifeGuardService
from test_route_risk_factors import BAR_ST, _grid

PW = "StrongPass!2026"
NIGHT = dt.datetime(2026, 11, 5, 21, 30, tzinfo=dt.timezone.utc).timestamp()   # 23:30 בישראל
NOON = dt.datetime(2026, 11, 5, 10, 0, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = LifeGuardService(str(tmp_path / "hm.db"))
    s._rf_layer = RF.RiskFactors(_grid())
    s._safety_layers = SI.Layers()
    import context_risk as CR
    monkeypatch.setattr(CR, "_grid", lambda: None)
    for u in ("dana", "rep1", "rep2", "rep3", "lone"):
        s.register(u, PW)
    s._x("UPDATE users SET created_at=?", (time.time() - 30 * 86400,))
    yield s
    s.close()


def _bbox(c, half=0.002):
    return f"{c[0] - half},{c[1] - half},{c[0] + half},{c[1] + half}"


def test_nightlife_only_at_night_and_no_neighbourhood_score(svc):
    night = svc.map_environment("dana", _bbox(BAR_ST), at=NIGHT)
    assert night["phase"] == "night" and night["layers"]["nightlife"]["available"]
    assert any(c["nightlife"] for c in night["cells"])
    day = svc.map_environment("dana", _bbox(BAR_ST), at=NOON)
    assert all(c["nightlife"] is None for c in day["cells"])
    blob = json.dumps(night, ensure_ascii=False)
    for word in ("score", "danger", "מסוכן", "risk_level"):
        assert word not in blob                                   # אין ציון / תיוג שכונה
    assert "לא ציון סכנה" in night["disclaimer"]


def test_missing_layers_are_none_not_zero(svc):
    r = svc.map_environment("dana", _bbox(BAR_ST), at=NOON)
    c = r["cells"][0]
    assert c["lighting"] is None and c["accidents"] is None       # "אין נתונים" ולא "0 עמודים / 0 תאונות"
    assert r["layers"]["lighting"]["available"] is False


def test_only_corroborated_or_verified_reports(svc):
    p = BAR_ST
    svc.add_street_report("lone", "dark_street", p[0], p[1])        # דיווח יחיד - לא מוצג
    r = svc.map_environment("dana", _bbox(p), at=NOON)
    assert sum(c["reports"] for c in r["cells"]) == 0
    for u in ("rep1", "rep2", "rep3"):
        svc.add_street_report(u, "dark_street", p[0], p[1])
    r = svc.map_environment("dana", _bbox(p), at=NOON)
    assert sum(c["reports"] for c in r["cells"]) >= 1                # אושש ע"י 3 מדווחים


@pytest.mark.parametrize("bbox", ["", "1,2,3", "32.0,34.7,32.2,34.9"])
def test_invalid_or_too_large_bbox(svc, bbox):
    with pytest.raises(Exception):
        svc.map_environment("dana", bbox)


def test_over_http(tmp_path):
    import threading
    from backend_client import BackendClient
    from server.lifeguard_server import create_server
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "w.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = BackendClient(f"http://127.0.0.1:{httpd.server_address[1]}")
        c.login_or_register("dana", PW)
        r = c._req("GET", "/map/environment", params={"bbox": _bbox(BAR_ST, 0.001), "at": NOON})
        assert r["cells"] and "layers" in r
    finally:
        httpd.shutdown()
        httpd.service.close()
