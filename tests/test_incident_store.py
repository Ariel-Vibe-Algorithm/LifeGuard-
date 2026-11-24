# -*- coding: utf-8 -*-
"""בדיקות היסטוריית אירועים, הרשאות (owner isolation), outbox, מדיניות שמירה, ותוכנית בטיחות."""
import os
import time

import pytest

import incident_store as store


def test_owner_isolation_get(temp_env):
    iid = store.create_incident("alice", "SOS", "button")
    store.add_event("alice", iid, "location", "סודי: 32.0, 34.0")
    assert store.get_incident("alice", iid) is not None
    # משתמש אחר לא יכול לקרוא את האירוע גם אם ניחש את המזהה
    assert store.get_incident("bob", iid) is None


def test_owner_isolation_add_event(temp_env):
    iid = store.create_incident("alice", "SOS", "button")
    # bob לא יכול להוסיף אירוע לתיק של alice
    assert store.add_event("bob", iid, "note", "זיוף") is False
    inc = store.get_incident("alice", iid)
    assert len(inc["events"]) == 0


def test_owner_isolation_delete(temp_env):
    iid = store.create_incident("alice", "SOS", "button")
    ok, _ = store.delete_incident("bob", iid)
    assert ok is False
    assert store.get_incident("alice", iid) is not None


def test_provenance_recorded(temp_env):
    iid = store.create_incident("alice", "SOS", "button")
    store.add_event("alice", iid, "user_action", "לחיצה", store.PROVENANCE_CONFIRMED)
    store.add_event("alice", iid, "location", "מיקום", store.PROVENANCE_AUTOMATIC)
    events = store.get_incident("alice", iid)["events"]
    assert events[0]["provenance"] == "confirmed"
    assert events[1]["provenance"] == "automatic"


def test_retention_purges_old_ended_incidents(temp_env):
    old = store.create_incident("alice", "SOS", "button", started_at=time.time() - 400 * 86400)
    store.update_incident("alice", old, ended_at=time.time() - 400 * 86400, outcome="done")
    recent = store.create_incident("alice", "SOS", "button")
    store.update_incident("alice", recent, ended_at=time.time(), outcome="done")
    store.set_retention_days("alice", 180)
    removed = store.purge_expired("alice")
    assert removed == 1
    ids = [i["id"] for i in store.list_incidents("alice")]
    assert old not in ids and recent in ids


def test_retention_zero_keeps_forever(temp_env):
    iid = store.create_incident("alice", "SOS", "button", started_at=time.time() - 9999 * 86400)
    store.update_incident("alice", iid, ended_at=time.time() - 9999 * 86400, outcome="done")
    store.set_retention_days("alice", 0)
    assert store.purge_expired("alice") == 0


def test_outbox_expire_on_incident_end(temp_env):
    iid = store.create_incident("alice", "SOS", "button")
    store.enqueue("alice", "sos_message", {"x": 1}, idempotency_key="k1", incident_id=iid)
    assert len(store.pending_outbox("alice", iid)) == 1
    expired = store.expire_outbox_for_incident("alice", iid)
    assert expired == 1
    assert len(store.pending_outbox("alice", iid)) == 0


def test_outbox_idempotency(temp_env):
    iid = store.create_incident("alice", "SOS", "button")
    assert store.enqueue("alice", "m", {"a": 1}, idempotency_key="same", incident_id=iid) is True
    assert store.enqueue("alice", "m", {"a": 2}, idempotency_key="same", incident_id=iid) is False
    assert len(store.pending_outbox("alice", iid)) == 1


def test_export_report(temp_env, tmp_path):
    iid = store.create_incident("alice", "SOS", "button")
    store.add_event("alice", iid, "user_action", "בדיקה", store.PROVENANCE_CONFIRMED)
    out = tmp_path / "report.txt"
    ok, _ = store.export_incident("alice", iid, str(out))
    assert ok and out.exists()
    content = out.read_text(encoding="utf-8")
    assert "מאושר" in content and "בדיקה" in content
    # ייצוא של אירוע שלא שייך למשתמש נכשל
    assert store.export_incident("bob", iid, str(tmp_path / "x.txt"))[0] is False


def test_safety_plan_roundtrip(temp_env):
    plan = {"meeting_points": [{"name": "גן העיר", "address": "רחוב 1", "notes": ""}],
            "fallback_destinations": [], "important_instructions": "לקחת תרופות",
            "medical_notes": "אלרגיה לפניצילין", "household_contacts": "אמא 050"}
    store.save_safety_plan("alice", plan)
    loaded = store.get_safety_plan("alice")
    assert loaded["meeting_points"][0]["name"] == "גן העיר"
    assert loaded["important_instructions"] == "לקחת תרופות"
    # בידוד: bob לא רואה את התוכנית של alice
    assert store.get_safety_plan("bob")["meeting_points"] == []


def test_checklist_state(temp_env):
    store.set_checklist_item("alice", "emergency_kit", "water", True)
    state = store.get_checklist_state("alice", "emergency_kit")
    assert state["water"]["done"] is True
    assert store.get_checklist_state("bob", "emergency_kit") == {}


def test_delete_media_only_inside_data_dir(temp_env):
    """מחיקת מדיה לא תמחק קובץ מחוץ לתיקיית הנתונים (הגנה מפני path traversal)."""
    import config
    outside = os.path.join(os.path.dirname(config.DATA_DIR), "important.txt")
    with open(outside, "w") as f:
        f.write("do not delete")
    inside = os.path.join(config.AUDIO_DIR, "chunk.wav")
    with open(inside, "w") as f:
        f.write("audio")
    iid = store.create_incident("alice", "SOS", "button")
    store.add_event("alice", iid, "media", "חוץ", data={"path": outside, "type": "audio"})
    store.add_event("alice", iid, "media", "פנים", data={"path": inside, "type": "audio"})
    ok, removed = store.delete_incident("alice", iid, delete_media=True)
    assert ok
    assert os.path.exists(outside)       # קובץ חיצוני לא נגעו בו
    assert not os.path.exists(inside)    # קובץ בתוך data נמחק
    assert removed == 1
