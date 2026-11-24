# -*- coding: utf-8 -*-
"""
פרטיות, הקלטות, הורה-ילד, הפעלת חירום לפי ביטחון ויומן ביקורת עמיד לשינוי (server/privacy.py),
סנכרון מושהה מהמחשב (privacy_sync.py), ו-SOS ידני שלא ממתין ל-AI (engine).
"""
import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
from unittest import mock

import pytest

from server import evidence
from server import privacy as PV
from server.lifeguard_server import LifeGuardService, create_server
from backend_client import BackendClient, BackendError

PW = "StrongPass!2026"
LOC = {"lat": 32.08, "lon": 34.78, "is_precise": True}


@pytest.fixture
def svc(tmp_path):
    s = LifeGuardService(str(tmp_path / "p.db"))
    for u in ("dana", "mom", "dad", "eve", "vol", "kid"):
        s.register(u, PW)
    s._x("INSERT INTO relationships(owner, contact, status, created_at, responded_at) VALUES "
         "('dana','mom','accepted',0,0), ('dana','dad','accepted',0,0)")
    yield s
    s.close()


def _vol(s):
    s.set_volunteer("vol", True, 5)
    s.update_presence("vol", 32.081, 34.781, is_precise=True)


def _vols(s, iid):
    return [r["username"] for r in s._q("SELECT username FROM recipients WHERE incident_id=? AND role='volunteer'",
                                        (iid,))]


def _first_ringing(s, iid):
    return s._q("SELECT username FROM recipients WHERE incident_id=? AND ring_status='ringing'", (iid,), one=True)[
        "username"]


# ---------------------------------------------------------------- 1. הפעלה לפי ביטחון
def test_emergency_activation_thresholds(svc):
    _vol(svc)
    m = svc.create_incident("dana", "m1", location=LOC)                      # ידני - מיד ובמלואו
    assert m["activation"] if "activation" in m else True
    assert svc._incident_row(m["id"])["activation"] == "manual" and _vols(svc, m["id"]) == ["vol"]
    svc.update_incident("dana", m["id"], {"status": "cancelled"})
    with pytest.raises(Exception) as e:
        svc.create_incident("dana", "a1", location=LOC, trigger="sensor", risk={"level": "LOW", "confidence": 0.2})
    assert getattr(e.value, "status", None) == 422
    assert not svc._q("SELECT 1 FROM incidents WHERE client_id='a1'")           # לא נוצר כלום
    mid = svc.create_incident("dana", "a2", location=LOC, trigger="wellness_no_response",
                              risk={"level": "MEDIUM", "confidence": 0.55})
    assert svc._incident_row(mid["id"])["activation"] == "unconfirmed"
    assert _vols(svc, mid["id"]) == []                                       # בלי מתנדבים
    svc.run_dispatch_queue()
    assert _vols(svc, mid["id"]) == []
    assert svc._q("SELECT 1 FROM recipients WHERE incident_id=? AND role='trusted'", (mid["id"],))   # אנשי קשר כן
    assert svc.get_incident(_first_ringing(svc, mid["id"]), mid["id"])["activation"] == "unconfirmed"
    svc.confirm_incident("dana", mid["id"])
    assert _vols(svc, mid["id"]) == ["vol"]
    svc.update_incident("dana", mid["id"], {"status": "resolved"})
    hi = svc.create_incident("dana", "a3", location=LOC, trigger="acoustic", risk={"level": "HIGH", "confidence": 0.85})
    assert svc._incident_row(hi["id"])["activation"] == "confirmed_high" and _vols(svc, hi["id"]) == ["vol"]
    acts = [e["details"].get("activation") for e in svc.privacy_history("dana", kind="emergency_activated")["events"]]
    assert sorted(a for a in acts if a) == ["confirmed_high", "manual", "unconfirmed"]
    assert svc.privacy_history("dana", kind="emergency_cancelled")["count"] == 1


# ---------------------------------------------------------------- 2. שלושת מצבי המיקום
def test_location_modes_always_emergency_off(svc):
    svc.set_location_mode("dana", "mom", "always")
    svc.set_location_mode("dana", "dad", "off")
    assert svc.put_live_location("dana", {"lat": 32.08, "lon": 34.78, "accuracy_m": 12, "is_precise": True,
                                          "captured_at": time.time()})["stored"]
    live = svc.contact_location("mom", "dana")
    assert live["available"] and live["accuracy_m"] == 12 and not live["stale"] and live["label"] == "חי"
    raw = svc._q("SELECT enc FROM live_locations WHERE username='dana'", one=True)["enc"]
    assert "32.08" not in raw                                               # מוצפן במסד
    for viewer in ("dad", "eve"):
        with pytest.raises(Exception):
            svc.contact_location(viewer, "dana")                            # off / לא איש קשר
    svc._x("UPDATE live_locations SET received_at=? WHERE username='dana'", (time.time() - 900,))
    old = svc.contact_location("mom", "dana")
    assert old["stale"] and "לא חי" in old["label"]                          # ישן לעולם לא "חי"
    inc = svc.create_incident("dana", "e1", location=LOC)
    first = _first_ringing(svc, inc["id"])
    svc.respond(first, inc["id"], "accept")
    assert "location" in svc.get_incident("mom", inc["id"])
    dv = svc.get_incident("dad", inc["id"])
    assert "location" not in dv and dv["location_withheld"]                 # off: התראה בלי מיקום
    svc.set_location_mode("dana", "mom", "emergency")
    with pytest.raises(Exception):
        svc.contact_location("mom", "dana")                                 # emergency: אין שיתוף רציף
    assert "location" in svc.get_incident("mom", inc["id"])                 # אבל באירוע - כן
    esc = svc.start_escort("dana", 30, "בית", location=LOC)
    assert svc.get_escort("dad", esc["id"]).get("location_withheld")
    assert "location" in svc.get_escort("mom", esc["id"])


def test_pause_revokes_immediately_and_put_without_viewers(svc):
    assert svc.put_live_location("dana", LOC) == {"stored": False, "reason": "no_always_contacts"}
    svc.set_location_mode("dana", "mom", "always")
    svc.put_live_location("dana", LOC)
    svc.pause_sharing("dana", True)
    assert not svc._q("SELECT 1 FROM live_locations WHERE username='dana'")  # נמחק מיד
    assert svc.put_live_location("dana", LOC) == {"stored": False, "reason": "paused"}
    with pytest.raises(Exception):
        svc.contact_location("mom", "dana")
    p = svc.privacy_permissions("dana")
    assert p["sharing_paused"] and p["who_can_see_location_now"] == []
    assert {c["username"]: c["effective_now"] for c in p["contacts"]}["mom"] == "paused"
    svc.pause_sharing("dana", False)
    svc.put_live_location("dana", LOC)
    assert svc.contact_location("mom", "dana")["available"]


def test_unauthorized_access_is_blocked_and_audited(svc):
    _vol(svc)
    inc = svc.create_incident("dana", "u1", location=LOC)
    v = svc.get_incident("vol", inc["id"])
    assert v["location"]["approximate"] and v["owner"] is None              # לפני קבלה: אזור משוער בלבד
    svc.respond("vol", inc["id"], "accept")
    assert not svc.get_incident("vol", inc["id"])["location"].get("approximate")
    svc.update_incident("dana", inc["id"], {"status": "resolved"})
    with pytest.raises(Exception):
        svc.get_incident("vol", inc["id"])                                  # אחרי סיום - אין גישה
    with pytest.raises(Exception):
        svc.get_incident("eve", inc["id"])
    with pytest.raises(Exception):
        svc.contact_location("eve", "dana")
    fails = [e for e in svc.privacy_history("dana", kind="location_access")["events"] if e["result"] == "failed"]
    assert fails and fails[0]["actor"] == "eve"
    vias = {e["via"] for e in svc.privacy_history("dana", kind="location_access")["events"] if e["result"] == "completed"}
    assert {"volunteer_nearby_approx", "volunteer_assigned"} <= vias


# ---------------------------------------------------------------- 3. הקלטות
def _audio(n=2000, seed=b"x"):
    return hashlib.sha256(seed).digest() * (n // 32)


def _upload(s, user, iid, cid="seg1", data=None):
    data = data or _audio()
    return s.upload_recording(user, iid, {"client_id": cid, "data_b64": base64.b64encode(data).decode(),
                                          "sha256": hashlib.sha256(data).hexdigest(), "mime": "audio/wav",
                                          "started_at": time.time() - 60, "ended_at": time.time()})


def test_recording_restrictions_recipients_playback_and_deletion(svc):
    inc = svc.create_incident("dana", "r1", location=LOC)
    with pytest.raises(Exception) as e:
        _upload(svc, "dana", inc["id"])
    assert e.value.code == "recording_disabled"                             # ברירת מחדל: אין העלאה
    svc.set_record_on_emergency("dana", True)
    svc.set_recording_access("dana", "mom", True)
    up = _upload(svc, "dana", inc["id"])
    assert up["recipients"] == [svc._display("mom")]
    assert _upload(svc, "dana", inc["id"])["duplicate"]                      # אותו קטע שוב - לא כפול
    first = _first_ringing(svc, inc["id"])
    svc.respond(first, inc["id"], "accept")
    got = svc.get_recording("mom", inc["id"], up["id"])
    assert base64.b64decode(got["data_b64"]) == _audio()
    with open(svc._q("SELECT path FROM recordings WHERE id=?", (up["id"],), one=True)["path"], encoding="ascii") as fh:
        assert base64.b64encode(_audio()).decode()[:40] not in fh.read()    # מוצפן בדיסק
    with pytest.raises(Exception):
        svc.get_recording("dad", inc["id"], up["id"])                       # בלי הרשאת הקלטות
    assert svc.list_recordings("dad", inc["id"])["recordings"] == []
    hist = svc.privacy_history("dana", kind="recording")["events"]
    assert any(h["action"] == "recording_access" and h["result"] == "completed" and h["actor"] == "mom" for h in hist)
    assert any(h["action"] == "recording_access" and h["result"] == "failed" and h["actor"] == "dad" for h in hist)
    with pytest.raises(Exception):
        svc.upload_recording("dana", inc["id"], {"client_id": "big", "data_b64": base64.b64encode(
            b"0" * (PV.RECORDING_MAX_BYTES + 1)).decode()})
    with pytest.raises(Exception):
        svc.upload_recording("dana", inc["id"], {"client_id": "bad", "data_b64": base64.b64encode(b"abc").decode(),
                                                 "sha256": "0" * 64})
    path = svc._q("SELECT path FROM recordings WHERE id=?", (up["id"],), one=True)["path"]
    svc.delete_recording("dana", up["id"])
    assert not os.path.exists(path)
    with pytest.raises(Exception):
        svc.get_recording("mom", inc["id"], up["id"])
    up2 = _upload(svc, "dana", inc["id"], "seg2")
    svc._x("UPDATE recordings SET uploaded_at=? WHERE id=?", (time.time() - 31 * 86400, up2["id"]))
    svc.privacy_tick()
    assert svc._q("SELECT deleted_at FROM recordings WHERE id=?", (up2["id"],), one=True)["deleted_at"]
    assert any(e["details"].get("reason") == "retention_expired"
               for e in svc.privacy_history("dana", kind="recording_deleted")["events"])


def test_recording_not_allowed_for_unconfirmed_or_old_incidents(svc):
    svc.set_record_on_emergency("dana", True)
    mid = svc.create_incident("dana", "x1", location=LOC, trigger="sensor", risk={"level": "MEDIUM", "confidence": 0.5})
    with pytest.raises(Exception) as e:
        _upload(svc, "dana", mid["id"])
    assert e.value.code == "not_confirmed"
    inc = svc.create_incident("dana", "x2", location=LOC)
    svc.update_incident("dana", inc["id"], {"status": "resolved"})
    assert _upload(svc, "dana", inc["id"], "late")["id"]                       # העלאה מאוחרת (אחרי ניתוק) מותרת
    svc._x("UPDATE incidents SET ended_at=? WHERE id=?", (time.time() - 49 * 3600, inc["id"]))
    with pytest.raises(Exception) as e:
        _upload(svc, "dana", inc["id"], "too_late")
    assert e.value.code == "too_late"
    with pytest.raises(Exception):
        _upload(svc, "mom", inc["id"], "notmine")


# ---------------------------------------------------------------- 4. הורה-ילד
def test_child_parent_dual_approval_and_discreet_recording(svc):
    link = svc.guardian_invite("dana", "kid", "guardian", child_age=12)
    assert link["status"] == "pending" and link["guardian_approved"] and not link["child_approved"]
    with pytest.raises(Exception):
        svc.guardian_recording_consent("dana", link["id"], True)              # לפני אישור הילד/ה
    with pytest.raises(Exception):
        svc.guardian_approve("eve", link["id"])                               # צד שלישי
    assert svc.guardian_approve("kid", link["id"])["status"] == "active"
    svc.guardian_recording_consent("dana", link["id"], True)
    assert not svc.recording_policy("kid")["discreet"]                        # רק הורה הסכים
    svc.guardian_recording_consent("kid", link["id"], True)
    assert not svc.recording_policy("kid")["discreet"]                        # ההורה עוד לא אומת
    svc.admin_guardian_verify("dana")
    pol = svc.recording_policy("kid")
    assert pol["discreet"] and pol["record_on_emergency"] and svc._display("dana") in pol["recipients"]
    inc = svc.create_incident("kid", "k1", location=LOC)                    # אירוע מאושר -> הקלטה בלי אינטראקציה
    up = _upload(svc, "kid", inc["id"])
    assert svc._q("SELECT discreet FROM recordings WHERE id=?", (up["id"],), one=True)["discreet"] == 1
    svc.guardian_revoke("kid", link["id"])                                    # הילד/ה יכול/ה לבטל
    assert not svc.recording_policy("kid")["discreet"]
    assert svc.my_guardian_links("dana")["links"] == []
    acts = [e["action"] for e in svc.privacy_history("kid")["events"]]
    assert {"guardian_link_requested", "guardian_link_approved", "permission_discreet_recording",
            "guardian_link_revoked", "recording_uploaded"} <= set(acts)
    with pytest.raises(Exception):
        svc.guardian_invite("dana", "kid", "guardian", child_age=30)


def test_no_remote_microphone_or_live_listening_api():
    from server import lifeguard_server as LS
    paths = " ".join(rx.pattern for _, rx, _ in LS.ROUTES).lower()
    for word in ("microphone", "listen", "live-audio", "stream-audio", "remote-record"):
        assert word not in paths


# ---------------------------------------------------------------- 5. יומן ביקורת
def test_audit_tamper_resistance_and_accuracy(svc, tmp_path):
    svc.set_location_mode("dana", "mom", "always")
    svc.put_live_location("dana", LOC)
    for _ in range(5):
        svc.contact_location("mom", "dana")                                  # polling = מפגש אחד
    ev = [e for e in svc.privacy_history("dana", kind="location_access")["events"] if e["result"] == "completed"]
    assert len(ev) == 1 and ev[0]["access_period"]["requests"] == 5 and ev[0]["via"] == "always"
    assert "לא מאשר שמישהו הסתכל" in ev[0]["details"]["note"]
    assert svc.verify_audit()["ok"]
    with pytest.raises(sqlite3.DatabaseError):
        svc._x("UPDATE audit_events SET result='failed'")
    with pytest.raises(sqlite3.DatabaseError):
        svc._x("DELETE FROM audit_events")
    con = sqlite3.connect(svc.db_path)                                        # גם עקיפה ישירה במסד מתגלה
    con.execute("DROP TRIGGER audit_no_update")
    con.execute("UPDATE audit_events SET recipient='someone_else' WHERE action='location_access'")
    con.commit()
    con.close()
    v = svc.verify_audit()
    assert not v["ok"] and v["first_bad_event"]


def test_device_audit_offline_events_idempotent_with_device_time(svc):
    t = time.time() - 3600
    evs = [{"event_id": "abc12345", "device_ts": t, "action": "sos_activated_offline", "result": "completed"},
           {"event_id": "bad", "device_ts": t, "action": "sos_activated_offline"},
           {"event_id": "zzz99999", "action": "delete_everything"}]
    assert svc.device_audit("dana", evs)["accepted"] == 1
    assert svc.device_audit("dana", evs[:1])["accepted"] == 1                  # נשלח שוב - לא כפול
    rows = svc.privacy_history("dana", kind="sos_activated_offline")["events"]
    assert len(rows) == 1 and rows[0]["device_ts"] == pytest.approx(t) and rows[0]["source"] == "device"
    assert rows[0]["server_ts"] > rows[0]["device_ts"]


def test_history_filters_delivery_status_and_signed_export(svc):
    svc.set_record_on_emergency("dana", True)
    svc.set_recording_access("dana", "mom", True)
    inc = svc.create_incident("dana", "h1", location=LOC)
    _upload(svc, "dana", inc["id"])
    n = [e for e in svc.privacy_history("dana", kind="recording_recipient_notified")["events"]][0]
    assert n["result_effective"] == "pending" and n["delivery"] == "ממתין לאישור קבלה"   # לא "נמסר" בלי אישור
    nid = n["details"]["notification_id"]
    svc._x("UPDATE notifications SET received_at=? WHERE id=?", (time.time(), nid))
    n2 = svc.privacy_history("dana", kind="recording_recipient_notified")["events"][0]
    assert n2["result_effective"] == "completed"
    assert all(e["recipient"] == "mom" or e["actor"] == "mom" for e in svc.privacy_history("dana", contact="mom")["events"])
    assert svc.privacy_history("dana", since=time.time() + 100)["count"] == 0
    rep = svc.privacy_export("dana")
    assert evidence.verify_record(rep)[0]
    rep["record"]["history"][0]["action"] = "x"
    assert not evidence.verify_record(rep)[0]


# ---------------------------------------------------------------- 6. סנכרון מושהה מהמחשב + HTTP
def test_offline_queue_then_delayed_sync_over_http(tmp_path, temp_env, user):
    import incident_store
    import privacy_sync
    import evidence_vault as EV
    privacy_sync.queue_audit(user, "sos_activated_offline", details={"trigger": "button"})
    assert privacy_sync.sync(None, None, user)["audit_sent"] == 0              # אין חיבור - נשאר בתור
    vault = EV.EvidenceVault(str(tmp_path / "vault"))
    wav = tmp_path / "chunk.wav"
    wav.write_bytes(b"RIFF" + os.urandom(3000))
    vault.seal(str(wav), "lg-ev-1", "audio")
    privacy_sync.queue_recording(user, "lg-ev-1", vault.manifest("lg-ev-1")["items"][-1])
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "s.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = BackendClient(f"http://127.0.0.1:{httpd.server_address[1]}")
        c.login_or_register(user, PW)
        r = privacy_sync.sync(c, vault, user)
        assert r["audit_sent"] == 1 and r["kept_local"] == 1 and r["uploaded"] == 0   # הקלטה: אין הרשאה -> מקומי
        st = {i["kind"]: i["status"] for i in incident_store.list_outbox(user)}
        assert st["recording_upload"] == incident_store.OUTBOX_UNDELIVERABLE
        c.set_record_on_emergency(True)
        c.create_incident("lg-ev-2", "loud", "unknown", "unknown", None, {})
        vault.seal(str((lambda p: (p.write_bytes(b"RIFF" + os.urandom(3000)), p)[1])(tmp_path / "c2.wav")),
                   "lg-ev-2", "audio")
        privacy_sync.queue_recording(user, "lg-ev-2", vault.manifest("lg-ev-2")["items"][-1])
        r2 = privacy_sync.sync(c, vault, user)
        assert r2["uploaded"] == 1
        inc_id = c.incident_by_client("lg-ev-2")["id"]
        assert len(c.list_recordings(inc_id)["recordings"]) == 1
        hist = c.privacy_history(type="sos_activated_offline")["events"]
        assert hist and hist[0]["device_ts"] and hist[0]["source"] == "device"
        with pytest.raises(BackendError) as e:
            c.contact_location("nobody")
        assert e.value.status == 403
    finally:
        httpd.shutdown()
        httpd.service.close()


# ---------------------------------------------------------------- 7. SOS ידני לא ממתין ל-AI
def test_manual_sos_starts_countdown_without_waiting_for_ai(temp_env, user):
    import engine as E
    import risk_module
    started = threading.Event()
    release = threading.Event()

    def slow_assess(**kw):
        started.set()
        release.wait(5)
        return {"level": "LOW", "score": 0, "reasons": [], "automatic_sos_eligible": False}
    eng = E.LifeGuardEngine(username=user)
    try:
        with mock.patch.object(risk_module, "assess_risk", side_effect=slow_assess), \
             mock.patch.object(E.voice_trigger_module, "wait_for_mic_release", lambda *a: None):
            t0 = time.time()
            eng.trigger(source="button")
            assert eng.state.name == "COUNTDOWN" and time.time() - t0 < 0.5   # מיד, לפני שההערכה הסתיימה
            assert started.wait(3)
            release.set()
            eng.cancel_with_password(PW)
    finally:
        release.set()
        eng.shutdown()


def test_privacy_window_renders_history_permissions_and_guardian(tmp_path):
    import tkinter as tk
    from tests.test_first_aid import _texts, _tk_root
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "w.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    root = _tk_root()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        dana, mom = BackendClient(url), BackendClient(url)
        dana.login_or_register("dana", PW)
        mom.login_or_register("mom", PW)
        dana.invite("mom")
        mom.respond_invite("dana", True)
        dana.set_location_mode("mom", "always")
        dana.put_live_location({"lat": 32.08, "lon": 34.78, "accuracy_m": 9, "is_precise": True})
        mom.contact_location("dana")
        queue = []

        class App:
            client = dana

            def __init__(self):
                self.root = root

            def _post(self, fn, *a):
                queue.append((fn, a))

        from privacy_ui import PrivacyWindow

        def pump():
            for _ in range(60):
                root.update()
                time.sleep(0.03)
                while queue:
                    fn, a = queue.pop(0)
                    fn(*a)
        w = PrivacyWindow(root, App())
        pump()
        flat = "\n".join(_texts(w.list))
        assert "גישה למיקום" in flat and "נמסר לאפליקציה" in flat and "תמיד" in flat
        w.show("perms")
        pump()
        flat = "\n".join(_texts(w.perm_frame))
        assert "מקבלים עכשיו את המיקום החי שלך" in flat and "השהיה מיידית" in flat
        w.show("guardian")
        pump()
        assert any("קישור הורה-ילד" in t for t in _texts(w.body))
        w.destroy()
    finally:
        root.destroy()
        httpd.shutdown()
        httpd.service.close()


def test_contract_additions_for_android(svc):
    from server import dispatch
    assert dispatch.clean_signals({"signals": [{"source": "no_response", "confidence": 1}]})[0]["source"] == "no_response"
    svc.device_audit("dana", [{"event_id": "rej12345", "device_ts": time.time(), "action": "auto_activation_rejected",
                               "details": {"reason": "low_confidence"}}])
    ev = svc.privacy_history("dana", kind="auto_activation_rejected")["events"][0]
    assert ev["client_event_id"] == "rej12345"
    svc.set_location_mode("dana", "mom", "always")
    theirs = {r["username"]: r for r in svc.relationships("mom")["i_am_contact_for"]}
    assert theirs["dana"]["shares_location_live"] is True
    svc.pause_sharing("dana", True)
    assert svc.relationships("mom")["i_am_contact_for"][0]["shares_location_live"] is False
    assert svc.relationships("dad")["i_am_contact_for"][0]["shares_location_live"] is False   # emergency-only
