# -*- coding: utf-8 -*-
"""Global Street Intelligence - תשתית: ולידציה, הסכמה, ואיסור הצגת ציונים בלי ראיות."""
import time

import pytest

import street_intel as si


def _obs(**kw):
    base = dict(id="o1", kind="user_report", segment_id="s1", lat=32.0, lon=34.0, observed_at=time.time(),
                consent={"accepted": True})
    base.update(kw)
    return si.Observation(**base)


def test_user_observation_requires_explicit_consent():
    assert "תצפית של משתמש דורשת הסכמה מפורשת" in _obs(consent={}).validate()
    assert _obs().validate() == []


def test_user_media_must_be_anonymized_before_storage():
    errors = _obs(kind="user_image", attributes={}).validate()
    assert any("טשטוש" in e for e in errors)
    assert _obs(kind="user_image", attributes={"contains_faces": False}).validate() == []


def test_invalid_kind_and_coords_rejected():
    errors = _obs(kind="rumor", lat=200).validate()
    assert len(errors) == 2


def test_score_not_displayable_without_enough_independent_evidence():
    now = time.time()
    s = si.SafetyScore("s1", 0.7, 0.9, ["poor_lighting"], observation_count=50, independent_sources=1,
                       model_version="future", computed_at=now)
    assert not s.displayable(now)            # מקור אחד בלבד
    s.independent_sources = 3
    assert s.displayable(now)
    s.computed_at = now - 400 * 86400
    assert not s.displayable(now)            # ישן מדי
    assert not si.SafetyScore("s1", None, 0, [], 99, 9, "x", now).displayable(now)  # אין ציון


def test_no_scoring_engine_exists_yet():
    with pytest.raises(NotImplementedError):
        si.ScoringEngine().score(si.StreetSegment("s1", [(32, 34)], "osm"), [])


def test_overlay_is_explicit_no_data_even_offline():
    r = si.overlay_for(None, 32.0, 34.0, 1.0)
    assert r["status"] == "no_data" and r["segments"] == [] and "לא מציג" in r["message"]
