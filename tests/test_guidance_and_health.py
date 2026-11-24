# -*- coding: utf-8 -*-
"""בדיקות מנוע ההנחיות (כללים מפורשים + מקורות) ובדיקות התקינות."""
import pytest

import guidance_engine as g
import emergency_numbers_module as en


def test_content_is_valid():
    content = g.load_content()
    assert g.validate_content(content) == []  # כל צעד עם מקור קיים


def test_every_step_has_a_source():
    content = g.load_content()
    for sc in content["scenarios"]:
        for step in sc.get("steps", []):
            assert step.get("source") in content["sources"]


def test_regional_variant_israel_earthquake():
    r = g.select_guidance("earthquake", {"country_code": "IL"})
    assert "regional_variant" in r["rule_trace"]
    # גרסה ישראלית מזכירה פיקוד העורף / ממ"ד
    assert any("ממ" in s["text"] or "פיקוד" in s["text"] for s in r["steps"])


def test_generic_variant_when_no_region():
    r = g.select_guidance("earthquake", {"country_code": "US"})
    assert "regional_variant" not in r["rule_trace"]
    assert any("רד" in s["text"] or "ברכ" in s["text"] for s in r["steps"])  # Drop/Cover/Hold


def test_silent_mode_suppresses_guidance():
    r = g.select_guidance("fire", {"silent_mode": True})
    assert r["suppressed"] is True
    assert r["steps"] == []


def test_call_numbers_populated_from_context():
    r = g.select_guidance("medical", {"emergency_numbers": en.get_emergency_numbers("IL")})
    kinds = {c["kind"] for c in r["calls"]}
    assert "ambulance" in kinds


def test_offline_note_added():
    r = g.select_guidance("medical", {"online": False})
    assert any("אינטרנט" in n for n in r["notes"])


def test_alerts_not_delivered_warning():
    r = g.select_guidance("physical_danger", {"alerts_delivered": False})
    assert any("לא נשלח" in n for n in r["notes"])


def test_unknown_scenario_raises():
    with pytest.raises(KeyError):
        g.select_guidance("nonexistent")


# ---------------- health_module ----------------

def test_health_delivery_no_contacts(temp_env):
    import health_module as h
    r = h.check_delivery_channels(None)
    assert r["status"] == h.UNAVAILABLE
    assert r["affects_emergency"] is True


def test_health_delivery_with_contacts_no_channel(user):
    import health_module as h
    r = h.check_delivery_channels(user)
    # בלי חיבור לשרת LifeGuard ההתראה לא מגיעה לאף אחד - חייב להיות "לא זמין", לא "תקין"
    assert r["status"] == h.UNAVAILABLE
    assert "שרת" in r["what"]


def test_health_summarize_levels():
    import health_module as h
    results = [
        {"status": h.OK, "affects_emergency": False},
        {"status": h.UNAVAILABLE, "affects_emergency": True},
    ]
    assert h.summarize(results) == h.UNAVAILABLE
    results2 = [{"status": h.LIMITED, "affects_emergency": False}]
    assert h.summarize(results2) == h.LIMITED
    results3 = [{"status": h.OK, "affects_emergency": True}]
    assert h.summarize(results3) == h.OK


def test_health_database_check(temp_env):
    import health_module as h
    r = h.check_databases()
    assert r["status"] in (h.OK, h.LIMITED, h.UNAVAILABLE)
