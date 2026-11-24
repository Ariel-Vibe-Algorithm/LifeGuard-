# -*- coding: utf-8 -*-
"""
שירות המיקום של Windows (Wi-Fi): באג - "המקום הבטוח שוב לא עובד" במחשב בלי GPS: היה רק מיקום IP (נמדד: עשרות
ק"מ מהמיקום האמיתי) והמנוע (בצדק) סירב לחשב מסלול ממנו. עכשיו: GPS > Windows (Wi-Fi) > IP.
"""
import pytest

import location_module as L


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.delenv("LIFEGUARD_DISABLE_WINDOWS_LOCATION", raising=False)
    monkeypatch.setattr(L, "_WINLOC_CACHE", {"at": 0.0, "loc": None})


def test_parses_windows_fix_and_marks_it_honestly():
    loc = L._windows_location(runner=lambda: "32.1071;35.2061;200")
    assert loc["source"] == "windows_location" and loc["accuracy_m_estimate"] == 200
    assert loc["is_precise"] is False                      # Wi-Fi של 200 מ' הוא לא GPS
    d = L.describe_location(loc)
    assert "Wi-Fi" in d["label"] and "לא GPS" in d["label"] and d["is_current"]


@pytest.mark.parametrize("raw", ["DENIED", "UNKNOWN", "", "0;0;100", "32.1;35.2;9000", "garbage"])
def test_denied_unknown_or_useless_falls_back(raw):
    assert L._windows_location(runner=lambda: raw) is None


def test_priority_gps_then_windows_then_ip(monkeypatch):
    monkeypatch.setattr(L, "_ensure_gps_reader", lambda **k: None)
    monkeypatch.setattr(L, "get_gps_fix", lambda *a, **k: None)
    monkeypatch.setattr(L, "_windows_location", lambda **k: {"available": True, "lat": 32.1, "lon": 35.2,
                                                              "source": "windows_location", "is_precise": False,
                                                              "accuracy_m_estimate": 150, "timestamp": 1})
    monkeypatch.setattr(L, "_get_ip_location", lambda **k: pytest.fail("IP used although Windows location exists"))
    assert L.get_location()["source"] == "windows_location"


def test_engine_routes_from_wifi_location_but_not_from_ip(temp_env, user):
    from engine import LifeGuardEngine
    eng = LifeGuardEngine(username=user)
    try:
        wifi = {"available": True, "lat": 32.1, "lon": 35.2, "source": "windows_location", "is_precise": False,
                "accuracy_m_estimate": 200}
        assert eng._route_origin(wifi)[0] is wifi
        coarse = dict(wifi, accuracy_m_estimate=2500)
        assert eng._route_origin(coarse)[0] is None and "כתבו כתובת" in eng._route_origin(coarse)[1]
        ip = {"available": True, "lat": 31.9, "lon": 34.9, "source": "ipwho.is (approximate)", "is_precise": False}
        assert eng._route_origin(ip)[0] is None
    finally:
        eng.shutdown()


def test_never_blocks_sos_refreshes_in_background(monkeypatch):
    import threading
    import time as _t
    gate = threading.Event()
    calls = []

    def slow_fetch(log_callback=None, runner=None):
        calls.append(1)
        gate.wait(2)                                          # שירות איטי (אמיתי: 3-7 שנ')
        with L._WINLOC_LOCK:
            L._WINLOC_CACHE.update(at=_t.time(), loc={"lat": 1, "lon": 2, "source": "windows_location",
                                                      "timestamp": _t.time(), "available": True})
    monkeypatch.setattr(L, "_fetch_windows_location", slow_fetch)
    t = _t.time()
    assert L._windows_location() is None                     # מטמון ריק: לא מחכים - IP/GPS ממשיכים מיד
    assert _t.time() - t < 0.2 and calls == [1]
    assert L._windows_location() is None and calls == [1]    # רענון אחד בכל פעם
    gate.set()
    end = _t.time() + 2
    while L._WINLOC_REFRESHING.is_set() and _t.time() < end:
        _t.sleep(0.02)
    assert L._windows_location()["source"] == "windows_location"   # בקריאה הבאה - מהמטמון


def test_failed_refresh_keeps_recent_result_with_original_timestamp():
    ok = L._windows_location(runner=lambda: "32.1;35.2;120")
    L._WINLOC_CACHE["at"] = 0.0
    assert L._windows_location(runner=lambda: "UNKNOWN") is None          # הקריאה עצמה נכשלה
    kept = L._WINLOC_CACHE["loc"]
    assert kept and kept["timestamp"] == ok["timestamp"]                   # לא "מתחדש" כאילו נמדד עכשיו
