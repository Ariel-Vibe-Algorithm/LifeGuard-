# -*- coding: utf-8 -*-
"""
שלב 1 - אמינות ומודיעין חירום:
- ביטחון בזיהוי נפרד מחומרה, ולמידה ממשוב מאומת (risk_feedback)
- השרת: ביטחון/אותות מסוננים, השפעה מוגבלת על התור, /me/setup
- רשומת ראיות חתומה (Ed25519) + יומן גישה בשרשרת hash; זיוף מתגלה
- חבילת ראיות במחשב: מדיה + ציר זמן + רשומת שרת + SHA256SUMS; verify_package מגלה שינוי
- בדיקת מערכת (diagnostics): הערכה טהורה של כל בדיקה
"""
import json
import os
import time
from unittest import mock

import numpy as np
import pytest

import config
import diagnostics as D
import risk_feedback as RF
from server import dispatch, evidence
from server.lifeguard_server import LifeGuardService, create_server
from backend_client import BackendClient

PW = "StrongPass!2026"


# ---------------------------------------------------------------- ביטחון ולמידה
@pytest.fixture
def fb_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    return tmp_path


def _res(noise=None, ai=False, kws=(), srcs=()):
    return {"noise_level": noise, "acoustic_ai": {"detected": ai, "probability": 0.9} if ai else None,
            "keywords_found": list(kws), "independent_sources": list(srcs)}


def test_confidence_is_separate_axis_and_learns_from_verified_feedback(fb_dir):
    loud = _res(noise=config.LOUD_NOISE_RMS_THRESHOLD + 0.1, srcs=["acoustic_anomaly"])
    c0 = RF.confidence(loud, username="u")
    assert c0["sources"] == ["loud_noise"] and c0["value"] < 0.2 and "ערך התחלתי" in c0["explanation"][0]
    both = RF.confidence(_res(ai=True, kws=["הצילו"], srcs=["acoustic_anomaly", "speech_keyword"]), username="u")
    assert set(both["sources"]) == {"acoustic_ai", "speech_keyword"}
    assert both["value"] > RF.confidence(_res(ai=True), username="u")["value"]          # noisy-OR: שני מקורות
    assert RF.confidence(_res(ai=True, noise=1.0), username="u")["sources"] == ["acoustic_ai"]   # מיקרופון = מקור אחד
    assert RF.confidence({}, username="u")["value"] is None                                       # אין מקור = לא ידוע
    for _ in range(8):
        RF.record("u", ["loud_noise"], "false_alarm")
    c_low = RF.confidence(loud, username="u")
    assert c_low["value"] < c0["value"] and "נלמד מ-8" in c_low["explanation"][0]
    for _ in range(20):
        RF.record("u", ["loud_noise"], "true_event")
    assert RF.confidence(loud, username="u")["value"] > c_low["value"]
    assert RF.confidence(loud, username="other")["value"] == c0["value"]                     # למשתמש/ת בלבד
    assert not RF.record("u", ["loud_noise"], "not_rated")
    assert RF.confidence({}, trigger="button", username="u")["value"] >= 0.8                  # הפעלה ידנית
    assert RF.confidence(_res(ai=True), username="u")["value"] <= RF.MAX_CONFIDENCE


def test_assess_risk_reports_confidence_without_changing_severity(fb_dir):
    import risk_module
    with mock.patch.object(risk_module, "record_audio", lambda *a, **k: (np.full(16000, 0.9, np.float32), 16000)), \
         mock.patch.object(risk_module, "classify_distress_audio", return_value=None), \
         mock.patch.object(risk_module, "personal_keywords", return_value=[]), \
         mock.patch.object(risk_module, "detect_dangerous_keywords", return_value=[]):
        before = risk_module.assess_risk(sensor_signals=[], username="u", trigger="sensor")
        for _ in range(30):
            RF.record("u", ["loud_noise"], "false_alarm")
        after = risk_module.assess_risk(sensor_signals=[], username="u", trigger="sensor")
    assert before["level"] == after["level"] and before["score"] == after["score"]   # למידה לא משנה חומרה
    assert after["confidence"]["value"] < before["confidence"]["value"]


# ---------------------------------------------------------------- שרת: ביטחון, setup, ראיות
@pytest.fixture
def svc(tmp_path):
    s = LifeGuardService(str(tmp_path / "s.db"))
    for u in ("dana", "mom", "eve"):
        s.register(u, PW)
    s._x("INSERT INTO relationships(owner, contact, status, created_at, responded_at) "
         "VALUES ('dana','mom','accepted',0,0)")
    yield s
    s.close()


def test_clean_confidence_and_signals():
    assert dispatch.clean_confidence({"confidence": 1.7}) == 1.0
    assert dispatch.clean_confidence({"confidence": "x"}) is None
    assert dispatch.clean_confidence({"confidence": float("nan")}) is None
    assert dispatch.clean_confidence({}) is None
    sig = dispatch.clean_signals({"signals": [{"source": "fall", "confidence": "0.9"}, {"source": "rm -rf"},
                                              "junk", {"source": "struggle", "at": 5}] + [{"source": "fall"}] * 20})
    assert sig[0] == {"source": "fall", "confidence": 0.9} and {"source": "rm -rf"} not in sig and len(sig) <= 8


def test_confidence_stored_shown_to_responders_and_bounded_in_priority(svc):
    loc = {"lat": 31.25, "lon": 34.79, "is_precise": True}
    hi = svc.create_incident("dana", "a", location=loc, risk={"level": "MEDIUM", "score": 3, "confidence": 0.95,
                                                             "reasons": ["x"], "signals": [{"source": "fall"}]})
    lo = svc.create_incident("eve", "b", location=loc, risk={"level": "MEDIUM", "score": 3, "confidence": 0.05,
                                                            "reasons": ["x"]})
    svc._x("UPDATE incidents SET created_at=?", (time.time(),))
    p_hi, p_lo = (svc._incident_priority(svc._incident_row(i["id"])) for i in (hi, lo))
    assert 0 < p_hi - p_lo <= dispatch.CONFIDENCE_POINTS          # משפיע, אבל לא דוחק אירוע לגמרי
    v = svc.get_incident("mom", hi["id"])
    assert v["risk"]["confidence"] == 0.95
    assert "risk" not in svc.get_incident("dana", hi["id"])       # מי שבמצוקה לא רואה מד סכנה/ביטחון


def test_setup_status(svc):
    s = svc.setup_status("dana")
    assert s["contacts_accepted"] == 1 and s["contacts_pending"] == 0 and s["last_readiness"] is None
    assert s["sms"]["configured"] is False and "mom" not in json.dumps(s)          # בלי פרטי אנשי הקשר
    svc.readiness_check("dana")
    assert svc.setup_status("dana")["last_readiness"]["total"] == 1


def test_signed_evidence_record_verifies_and_detects_tampering(svc):
    inc = svc.create_incident("dana", "evt-1", location={"lat": 32.08, "lon": 34.78, "is_precise": True},
                              risk={"level": "HIGH", "score": 6, "confidence": 0.8, "reasons": ["צעקה"]})
    svc.respond("mom", inc["id"], "accept")
    svc.add_message("mom", inc["id"], quick="on_my_way")
    d = svc.evidence_record("dana", inc["id"])
    assert evidence.verify_record(d) == (True, "תקין")
    rec = d["record"]
    assert rec["locations"][0]["lat"] == 32.08 and rec["risk"]["confidence"] == 0.8
    assert any(r["state"] == "accepted" and r["responded_at"] for r in rec["recipients"])
    assert rec["messages"] and rec["access_log"][0]["action"] == "export"
    # כל שינוי - תוכן, חתימה, מפתח זר, יומן גישה - מתגלה
    t = json.loads(json.dumps(d)); t["record"]["locations"][0]["lat"] = 32.5
    assert not evidence.verify_record(t)[0]
    t = json.loads(json.dumps(d)); t["signature"] = "00" * 64
    assert not evidence.verify_record(t)[0]
    other = LifeGuardService(os.path.join(os.path.dirname(svc.db_path), "o.db"))
    try:
        assert not evidence.verify_record(d, other.evidence_public_key()["public_key"])[0]
    finally:
        other.close()
    d2 = svc.evidence_record("dana", inc["id"])
    assert len(d2["record"]["access_log"]) == 2 and evidence.verify_chain(d2["record"]["access_log"])
    log = d2["record"]["access_log"]
    assert not evidence.verify_chain([log[1]])                      # מחיקת רשומה מהשרשרת
    # המפתח יציב בין הפעלות (נשמר מוצפן בשרת)
    pk = svc.evidence_public_key()["public_key"]
    svc._ev_key = None
    assert svc.evidence_public_key()["public_key"] == pk
    # רק בעל/ת האירוע
    with pytest.raises(Exception):
        svc.evidence_record("mom", inc["id"])
    with pytest.raises(Exception):
        svc.evidence_record("eve", inc["id"])
    assert svc.evidence_by_client("dana", "evt-1")["record"]["incident"]["id"] == inc["id"]


def test_unaccepted_volunteer_is_anonymous_in_evidence(svc):
    svc.register("vol", PW)
    svc.set_volunteer("vol", True, 5)
    svc.update_presence("vol", 31.251, 34.791, is_precise=True)
    inc = svc.create_incident("eve", "x", location={"lat": 31.25, "lon": 34.79, "is_precise": True})
    rec = svc.evidence_record("eve", inc["id"])["record"]
    assert rec["recipients"] and rec["recipients"][0]["role"] == "volunteer"
    assert all(r["who"] != "vol" and r["who"] != svc._display("vol") for r in rec["recipients"])


def test_evidence_and_setup_over_http(tmp_path):
    import threading
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "h.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        c = BackendClient(url)
        c.login_or_register("dana", PW)
        assert c.setup()["contacts_accepted"] == 0
        inc = c.create_incident("lg-dana-1", "loud", "unknown", "unknown", None, {})
        d = c.evidence_by_client("lg-dana-1")
        assert d["record"]["incident"]["id"] == inc["id"] and evidence.verify_record(d)[0]
        assert c.evidence_public_key()["public_key"] == d["public_key"]
    finally:
        httpd.shutdown()
        httpd.service.close()


# ---------------------------------------------------------------- חבילת ראיות במחשב
def test_evidence_package_export_and_independent_verification(tmp_path, svc):
    cv2 = pytest.importorskip("cv2")
    import evidence_vault as EV
    v = EV.EvidenceVault(str(tmp_path / "vault"))
    img = str(tmp_path / "face_1.jpg")
    cv2.imwrite(img, (np.random.default_rng(1).random((60, 80, 3)) * 255).astype(np.uint8))
    wav = str(tmp_path / "chunk_1.wav")
    with open(wav, "wb") as fh:
        fh.write(b"RIFF" + os.urandom(2000))
    v.seal(img, "evt-1", "face")
    v.seal(wav, "evt-1", "audio")
    inc = svc.create_incident("dana", "evt-1", location={"lat": 32.08, "lon": 34.78, "is_precise": True})
    rec = svc.evidence_by_client("dana", "evt-1")
    out = str(tmp_path / "pkg")
    res = v.export_package("evt-1", out, timeline={"events": [{"ts": 1, "message": "SOS"}]}, server_record=rec,
                           actor="dana")
    assert res["media"] == 2 and res["server_record"]["verified"] is True
    assert set(os.listdir(out)) >= {"media", "timeline.json", "server_record.json", "manifest.json",
                                    "SHA256SUMS.txt", "VERIFY_HE.txt"}
    assert EV.verify_package(out)["ok"]
    assert v.verify("evt-1")["ok"]
    assert [e["action"] for e in v.manifest("evt-1")["access_log"]] == ["export_package", "verify"]
    # שינוי בקובץ מדיה אחרי הייצוא -> מתגלה
    with open(os.path.join(out, "media", "face_1.jpg"), "r+b") as fh:
        fh.seek(200)
        b = fh.read(1)
        fh.seek(200)
        fh.write(bytes([b[0] ^ 0xFF]))
    r = EV.verify_package(out)
    assert not r["ok"] and any("face_1.jpg" in p for p in r["problems"])
    # מחיקת רשומה מיומן הגישה בכספת -> verify נכשל, ולא "מכסה" עם רשומה חדשה
    m = v.manifest("evt-1")
    m["access_log"].pop(0)
    v._save_manifest("evt-1", m)
    vr = v.verify("evt-1")
    assert not vr["ok"] and not vr["access_log_ok"]
    assert len(v.manifest("evt-1")["access_log"]) == 1


# ---------------------------------------------------------------- בדיקת מערכת
def test_diagnostics_evaluation_rules():
    now = 1_000_000.0
    assert D.eval_location({"sources": {"gps_fix": {"lat": 1}}}, now)["status"] == D.OK
    assert D.eval_location({"sources": {}, "last": {"lat": 1, "lon": 2, "is_precise": False, "timestamp": now}},
                           now)["status"] == D.WARN
    assert D.eval_location({"sources": {"ip_fallback_enabled": False}, "last": None}, now)["status"] == D.FAIL
    assert D.eval_microphone({"installed": True, "device": "Mic", "channels": 1})["status"] == D.OK
    assert D.eval_microphone({"installed": True, "device": None})["status"] == D.FAIL
    assert D.eval_camera(None)["status"] == D.UNAVAILABLE                  # לא נפתחת בלי בקשה
    assert D.eval_power({"available": True, "has_battery": True, "saver": True, "percent": 80})["status"] == D.WARN
    assert D.eval_power({"available": True, "has_battery": True, "percent": 10, "ac": False})["status"] == D.WARN
    assert D.eval_agent({"running": False, "autostart": False})["status"] == D.FAIL
    srv = D.eval_server({"configured": False})
    assert srv[0]["status"] == D.FAIL
    ok = D.eval_server({"configured": True, "reachable": True, "latency_ms": 80, "logged_in": True,
                        "setup": {"contacts_accepted": 0, "contacts_pending": 2, "last_readiness": None,
                                  "server_time": now}})
    by = {r["key"]: r for r in ok}
    assert by["server"]["status"] == D.OK and by["contacts"]["status"] == D.WARN and "2 הזמנות" in by["contacts"]["detail"]
    assert by["readiness"]["status"] == D.WARN
    partial = D.eval_server({"configured": True, "reachable": True, "latency_ms": 80, "logged_in": True,
                             "setup": {"contacts_accepted": 3, "server_time": now,
                                       "last_readiness": {"created_at": now - 86400, "total": 3, "confirmed": 2}}})
    assert {r["key"]: r for r in partial}["readiness"]["detail"].startswith("בבדיקה האחרונה 2/3")


def test_diagnostics_run_all_with_fake_probes_never_crashes():
    def boom():
        raise RuntimeError("driver crashed")
    probes = {"location": boom, "microphone": lambda: {"installed": True, "device": "Mic", "channels": 2},
              "power": lambda: {"available": True, "has_battery": False}, "agent": lambda: {"running": True},
              "notifications": lambda: {"available": True, "status": {}},
              "server": lambda c: {"configured": True, "reachable": False, "error": "timeout"}}
    res = D.run_all(None, probes=probes)
    assert res[0]["status"] == D.FAIL                                       # כשלים קודם
    keys = {r["key"] for r in res}
    assert {"location", "microphone", "camera", "power", "agent", "notifications", "server"} <= keys
    st, text = D.summary(res)
    assert st == D.FAIL and "שרת" in text


def test_diagnostics_card_and_window_render(monkeypatch):
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    posted = []

    class App:
        client = None
        def __init__(self):
            self.root = root
        def _post(self, fn, *a):
            posted.append((fn, a))

    import features_ui
    monkeypatch.setattr(D, "DEFAULT_PROBES", {
        "location": lambda: {"sources": {}, "last": None}, "microphone": lambda: {"installed": False},
        "camera": lambda: {"installed": True, "opened": True}, "power": lambda: {"available": False},
        "agent": lambda: {"running": True}, "notifications": lambda: {"available": True, "status": {}}})
    try:
        frame = tk.Frame(root)
        frame.pack()
        card = features_ui.DiagnosticsCard(frame, App())
        card.refresh()
        end = time.time() + 10
        while not posted and time.time() < end:
            root.update()
            time.sleep(0.02)
        for fn, a in posted:
            fn(*a)
        assert "❌" in card.status.cget("text")                     # שרת לא מחובר + מיקרופון חסר
        card.open_details()
        root.update()
        texts = []

        def walk(w):
            for ch in w.winfo_children():
                if isinstance(ch, tk.Label):
                    texts.append(ch.cget("text"))
                walk(ch)
        walk(card.list_frame)
        assert any("שרת LifeGuard" in t for t in texts) and any("מיקרופון" in t for t in texts)
        assert any(t.startswith("🔧") for t in texts)                 # הוראת תיקון לכל בעיה
    finally:
        root.destroy()
