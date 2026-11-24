# -*- coding: utf-8 -*-
"""בדיקות מצב לא-מקוון (outbox retry) ואמינות מיקום (טריות/אחרון ידוע)."""
import time

import pytest

import location_module as loc
from engine import LifeGuardEngine, State


def test_stale_gps_marked_not_current():
    old_fix = {"available": True, "is_precise": True, "lat": 32.0, "lon": 34.0,
               "timestamp": time.time() - 120, "accuracy_m_estimate": 10}
    d = loc.describe_location(old_fix)
    assert d["confidence"] == "stale"
    assert d["is_current"] is False


def test_fresh_gps_is_verified():
    fix = {"available": True, "is_precise": True, "lat": 32.0, "lon": 34.0,
           "timestamp": time.time(), "accuracy_m_estimate": 8}
    d = loc.describe_location(fix)
    assert d["confidence"] == "verified"
    assert d["is_current"] is True


def test_ip_location_is_estimated_never_verified():
    ip = {"available": True, "is_precise": False, "lat": 32.0, "lon": 34.0,
          "timestamp": time.time(), "city": "תל אביב"}
    d = loc.describe_location(ip)
    assert d["confidence"] == "estimated"
    assert d["is_current"] is False


def test_unavailable_uses_last_known_with_age():
    unavailable = {"available": False,
                   "last_known": {"available": True, "is_precise": True, "lat": 1, "lon": 2,
                                  "timestamp": time.time() - 300}}
    d = loc.describe_location(unavailable)
    assert d["confidence"] == "stale"
    assert "אחרון" in d["label"]


def test_build_map_link_rejects_invalid_coords():
    assert loc.build_live_map_link(999, 999) is None
    assert loc.build_live_map_link("x", "y") is None
    assert loc.build_live_map_link(32.0, 34.0).startswith("https://")
