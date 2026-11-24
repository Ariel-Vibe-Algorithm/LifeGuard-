# -*- coding: utf-8 -*-
"""הקשר זמן ותאורה בהערכת הסכנה: לילה לפי השמש, רחוב חשוך לפי עמודי תאורה, והקשר לעולם לא מספיק ל-SOS."""
import calendar
import time
import unittest
from unittest import mock

import numpy as np
import pytest

import context_risk as CR
import risk_module

TLV = (32.0853, 34.7818)


def utc(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M"))


def test_night_follows_the_sun_not_the_clock():
    # שקיעה בתל אביב: ~19:50 ביוני (16:50 UTC), ~16:40 בדצמבר (14:40 UTC)
    assert CR.phase(CR.solar_elevation(*TLV, utc("2026-06-21 16:30"))) == "day"
    assert CR.phase(CR.solar_elevation(*TLV, utc("2026-06-21 17:30"))) == "night"
    assert CR.phase(CR.solar_elevation(*TLV, utc("2026-12-21 14:45"))) == "twilight"
    assert CR.phase(CR.solar_elevation(*TLV, utc("2026-12-21 15:30"))) == "night"
    assert CR.solar_elevation(*TLV, utc("2026-06-21 09:45")) > 75          # צהרי יוני - שמש כמעט מעל הראש


def _grid():
    base_lat, base_lon = 32.0800, 34.7800
    pts = [(base_lat + i * 0.0003, base_lon) for i in range(40)]        # רחוב מואר צפונה
    pts += [(32.0700, 34.7600 + j * 0.0004) for j in range(60)]        # עוד רחוב, לכיסוי האזור
    return CR.LightGrid.build(pts)


def test_lighting_from_grid_and_coverage():
    g = _grid()
    assert g.poles_near(32.0806, 34.7800) >= 2
    assert g.covered(32.0850, 34.7900) and g.poles_near(32.0850, 34.7900) == 0     # חשוך בתוך אזור מכוסה
    assert not g.covered(31.0, 35.0)                                                 # אין נתונים בכלל


def test_assess_combines_night_and_dark_street_with_honest_unknowns():
    g = _grid()
    night = utc("2026-12-21 22:00")
    dark = CR.assess({"lat": 32.0850, "lon": 34.7900, "is_precise": True, "timestamp": night}, now=night, grid=g)
    assert dark["phase"] == "night" and dark["lighting"] == "dark" and dark["score"] == 2
    lit = CR.assess({"lat": 32.0806, "lon": 34.7800, "is_precise": True, "timestamp": night}, now=night, grid=g)
    assert lit["lighting"] == "lit" and lit["score"] == 1
    nodata = CR.assess({"lat": 31.0, "lon": 35.0, "is_precise": True, "timestamp": night}, now=night, grid=g)
    assert nodata["lighting"] == "unknown" and nodata["score"] == 1          # אין נתונים - בלי ניחוש
    ip = CR.assess({"lat": 32.0850, "lon": 34.7900, "is_precise": False}, now=night, grid=g)
    assert ip["lighting"] == "unknown"                                        # מיקום משוער - לא לתאורה
    stale = CR.assess({"lat": 32.0850, "lon": 34.7900, "is_precise": True, "timestamp": night - 3600},
                      now=night, grid=g)
    assert stale["lighting"] == "unknown" and stale["score"] == 1             # מיקום ישן - לא לתאורה
    day = utc("2026-06-21 09:00")
    assert CR.assess({"lat": 32.0850, "lon": 34.7900, "is_precise": True, "timestamp": day}, now=day,
                     grid=g)["score"] == 0
    assert CR.assess(None)["score"] == 0


class ContextInRiskTests(unittest.TestCase):
    def _assess(self, rms, now, loc):
        with mock.patch.object(risk_module, "record_audio", lambda *a, **k: (np.full(16000, rms, np.float32), 16000)), \
             mock.patch.object(risk_module, "classify_distress_audio", return_value=None), \
             mock.patch.object(risk_module, "personal_keywords", return_value=[]), \
             mock.patch.object(risk_module, "detect_dangerous_keywords", return_value=[]), \
             mock.patch.object(CR, "_grid", _grid):
            return risk_module.assess_risk(sensor_signals=[], location=loc, now=now)

    def test_same_noise_is_riskier_at_night_on_a_dark_street_but_never_auto_sos(self):
        night, day = utc("2026-12-21 22:00"), utc("2026-06-21 09:00")
        loc = lambda t: {"lat": 32.0850, "lon": 34.7900, "is_precise": True, "timestamp": t}  # noqa: E731
        d = self._assess(0.9, day, loc(day))
        n = self._assess(0.9, night, loc(night))
        self.assertEqual(d["level"], "LOW")
        self.assertEqual(n["level"], "MEDIUM")                    # חלון אימות קצר יותר, עדיפות גבוהה יותר
        self.assertGreater(n["score"], d["score"])
        self.assertFalse(n["automatic_sos_eligible"])             # הקשר אינו ראיה
        self.assertEqual(n["independent_sources"], ["acoustic_anomaly"])
        self.assertTrue(any("רחוב חשוך" in r for r in n["reasons"]))
        self.assertEqual(n["context"]["lighting"], "dark")

    def test_no_location_means_no_context_effect(self):
        night = utc("2026-12-21 22:00")
        r = self._assess(0.9, night, None)
        self.assertEqual(r["level"], "LOW")
        self.assertEqual(r["context"]["score"], 0)


def test_server_adds_night_context_to_priority_only_without_device_assessment(tmp_path, monkeypatch):
    from server.lifeguard_server import LifeGuardService
    monkeypatch.setattr(CR, "_grid", _grid)
    svc = LifeGuardService(str(tmp_path / "s.db"))
    try:
        for u in ("dana", "noa"):
            svc.register(u, "StrongPass!2026")
        loc = {"lat": 32.0850, "lon": 34.7900, "is_precise": True}
        a = svc.create_incident("dana", "a", location=loc)                       # אנדרואיד/PWA: בלי הערכה
        b = svc.create_incident("noa", "b", location=loc, risk={"level": "LOW", "score": 0, "reasons": []})
        row = lambda i: svc._incident_row(i["id"])  # noqa: E731
        svc._x("UPDATE incident_locations SET received_at=?", (utc("2026-12-21 22:00"),))
        svc._x("UPDATE incidents SET created_at=?", (utc("2026-01-01 00:00"),))   # הזדקנות זהה בשני הזמנים
        night, day = utc("2026-12-21 22:00"), utc("2026-06-21 09:00")
        assert svc._incident_priority(row(a), night) > svc._incident_priority(row(a), day)
        assert svc._incident_priority(row(b), night) == svc._incident_priority(row(b), day)   # המכשיר כבר שקלל
        svc.register("mom", "StrongPass!2026")
        svc._x("INSERT INTO relationships(owner, contact, status, created_at, responded_at) "
               "VALUES ('dana','mom','accepted',0,0)")
        svc._add_recipient(a["id"], "mom", "trusted")
        with mock.patch("time.time", return_value=night):
            ctx = svc.get_incident("mom", a["id"])["risk"]["context"]
        assert ctx["phase"] == "night"
    finally:
        svc.close()
