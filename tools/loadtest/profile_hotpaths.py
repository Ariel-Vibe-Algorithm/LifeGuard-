# -*- coding: utf-8 -*-
"""
profile_hotpaths.py - (A4) in-process evidence for the bottlenecks (temp DB, no HTTP):

 1. commits (BEGIN/COMMIT = fsync in WAL+synchronous=FULL) per API operation (sqlite trace callback)
 2. run_escalation_tick / run_dispatch_queue time vs number of waiting incidents, with the 5 s retry backoff
    intact vs. cleared on every tick (what any PUT /me/presence or set_volunteer does: _wake_dispatch)
 3. cProfile of one "woken" tick, of create_incident under load, and of _view_for with many volunteers
 4. synchronous=FULL vs NORMAL for create_incident / add_message

    .venv/Scripts/python.exe tools/loadtest/profile_hotpaths.py
"""
import cProfile
import io
import os
import pstats
import random
import sys
import tempfile
import time

from lt_common import ROOT, TLV, machine_context, save_result, summarize

sys.path.insert(0, ROOT)
from server import lifeguard_server as LS  # noqa: E402

OUT = {}


def new_svc(sync=None):
    d = tempfile.mkdtemp(prefix="lg_prof_")
    svc = LS.LifeGuardService(os.path.join(d, "p.db"), ack_timeout_seconds=600, contact_ring_seconds=600)
    svc.push_sender = lambda *a, **k: (201, None)     # never network
    if sync:
        svc._con.execute(f"PRAGMA synchronous={sync}")
    return svc


def seed(svc, owners=0, contacts=3, vols=0, verified=False, extra_vol_rows=0):
    salt, h = LS._hash_pw("x" * 10)
    now = time.time()
    rng = random.Random(1)

    def tx(con):
        for i in range(owners):
            con.execute("INSERT INTO users(username, display_name, pw_salt, pw_hash, created_at, user_id) "
                        "VALUES (?,?,?,?,?,?)", (f"o{i}", f"O{i}", salt, h, now - 9e5, f"usr_o{i}"))
            for j in range(contacts):
                con.execute("INSERT INTO users(username, display_name, pw_salt, pw_hash, created_at, user_id) "
                            "VALUES (?,?,?,?,?,?)", (f"k{i}_{j}", "K", salt, h, now - 9e5, f"usr_k{i}_{j}"))
                con.execute("INSERT INTO relationships(owner, contact, status, created_at, responded_at) "
                            "VALUES (?,?,'accepted',?,?)", (f"o{i}", f"k{i}_{j}", now, now))
        for i in range(vols + extra_vol_rows):
            con.execute("INSERT INTO users(username, display_name, pw_salt, pw_hash, created_at, user_id) "
                        "VALUES (?,?,?,?,?,?)", (f"v{i}", f"V{i}", salt, h, now - 9e5, f"usr_v{i}"))
            far = i >= vols                                 # extra rows: volunteers in another city
            con.execute("INSERT INTO volunteers(username, available, radius_km, skills, lat, lon, is_precise, "
                        "loc_updated_at, updated_at, verification) VALUES (?,1,5,'[]',?,?,1,?,?,?)",
                        (f"v{i}", TLV[0] + (1.0 if far else rng.uniform(-0.01, 0.01)),
                         TLV[1] + rng.uniform(-0.01, 0.01), now, now, "verified" if verified else "unverified"))
    svc._tx(tx)


def count_tx(svc, fn):
    stmts = {"BEGIN": 0, "COMMIT": 0, "total": 0}

    def tr(s):
        stmts["total"] += 1
        u = s.strip().upper()
        for k in ("BEGIN", "COMMIT"):
            if u.startswith(k):
                stmts[k] += 1
    svc._con.set_trace_callback(tr)
    try:
        fn()
    finally:
        svc._con.set_trace_callback(None)
    return stmts


def prof(fn, lines=30, sort="cumulative"):
    p = cProfile.Profile()
    t0 = time.perf_counter()
    p.enable()
    fn()
    p.disable()
    dt = time.perf_counter() - t0
    s = io.StringIO()
    pstats.Stats(p, stream=s).sort_stats(sort).print_stats(lines)
    return round(dt * 1000, 1), s.getvalue()


def loc():
    return {"lat": TLV[0] + random.uniform(-0.005, 0.005), "lon": TLV[1] + random.uniform(-0.005, 0.005),
            "is_precise": True}


def part1_commits():
    svc = new_svc()
    seed(svc, owners=5, vols=30)
    r = {}
    r["create_incident(loud, 3 contacts, 30 vols)"] = count_tx(
        svc, lambda: svc.create_incident("o0", "c1", "loud", location=loc()))
    iid = svc._q("SELECT id FROM incidents", one=True)["id"]
    r["update_location"] = count_tx(svc, lambda: svc.update_location("o0", iid, loc()))
    r["add_message"] = count_tx(svc, lambda: svc.add_message("o0", iid, "hi"))
    k = svc._q("SELECT username FROM recipients WHERE incident_id=? AND state='notified' AND role='trusted'",
               (iid,), one=True)["username"]
    r["respond(accept) by ringing contact"] = count_tx(svc, lambda: svc.respond(k, iid, "accept"))
    r["get_incident (read only)"] = count_tx(svc, lambda: svc.get_incident("o0", iid))
    svc._wake_dispatch()
    r["run_escalation_tick (1 active incident)"] = count_tx(svc, lambda: svc.run_escalation_tick())
    OUT["1_statements_and_transactions_per_operation"] = r
    print("commits:", r, flush=True)
    svc.close()


def part2_tick_scaling():
    rows = []
    for n_inc in (50, 100, 200, 400):
        for verified in (False, True):
            svc = new_svc()
            seed(svc, owners=n_inc, contacts=0, vols=50, verified=verified)
            for i in range(n_inc):
                svc.create_incident(f"o{i}", f"c{i}", "loud", location=loc())
            # steady state: all 50 volunteers busy (notified), remaining incidents wait
            backoff, woken = [], []
            for _ in range(5):
                t0 = time.perf_counter()
                svc.run_escalation_tick()
                backoff.append(time.perf_counter() - t0)
            for _ in range(5):
                svc._wake_dispatch()                       # = any presence update / volunteer toggle
                t0 = time.perf_counter()
                svc.run_escalation_tick()
                woken.append(time.perf_counter() - t0)
            # create_incident cost with n_inc waiting (it runs the whole queue synchronously)
            svc._wake_dispatch()
            t0 = time.perf_counter()
            svc.create_incident("o0", "late", "loud", location=loc())
            ci = time.perf_counter() - t0
            row = {"waiting_incidents": n_inc, "volunteers": 50, "verified": verified,
                   "tick_backoff_ms": summarize(backoff)["p50"], "tick_after_wake_ms": summarize(woken)["p50"],
                   "create_incident_ms": round(ci * 1000, 1),
                   "recipients_rows": svc._q("SELECT COUNT(*) AS n FROM recipients", one=True)["n"]}
            rows.append(row)
            print("tick:", row, flush=True)
            svc.close()
    OUT["2_tick_scaling"] = rows


def part3_profiles():
    svc = new_svc()
    seed(svc, owners=200, contacts=0, vols=50)
    for i in range(200):
        svc.create_incident(f"o{i}", f"c{i}", "loud", location=loc())
    svc._wake_dispatch()
    ms, txt = prof(lambda: svc.run_escalation_tick(), 35)
    OUT["3a_profile_woken_tick_200_waiting_50_unverified_vols"] = {"ms": ms, "profile": txt}
    print("woken tick ms", ms, flush=True)
    ms, txt = prof(lambda: svc.run_escalation_tick(), 25, "tottime")
    OUT["3a2_profile_woken_tick_tottime"] = {"ms": ms, "profile": txt}
    svc._wake_dispatch()
    ms, txt = prof(lambda: svc.create_incident("o1", "late2", "loud", location=loc()), 30)
    OUT["3b_profile_create_incident_with_200_waiting"] = {"ms": ms, "profile": txt}
    svc.close()
    # _view_for with many volunteer rows (it loads the WHOLE volunteers table on every call)
    for extra in (0, 10000, 50000):
        svc = new_svc()
        seed(svc, owners=1, contacts=3, vols=10, extra_vol_rows=extra)
        inc = svc.create_incident("o0", "v", "loud", location=loc())
        for i in range(30):
            svc.add_message("o0", inc["id"], f"m{i}") if i < 19 else None
        k = svc._q("SELECT username FROM recipients WHERE incident_id=? AND role='trusted' AND state!='queued'",
                   (inc["id"],), one=True)["username"]
        times = []
        for _ in range(30):
            t0 = time.perf_counter()
            svc.get_incident(k, inc["id"])
            times.append(time.perf_counter() - t0)
        OUT.setdefault("3c_get_incident_vs_volunteer_table_size", []).append(
            {"volunteer_rows": 10 + extra, "get_incident_ms": summarize(times)})
        print("view_for", extra, summarize(times), flush=True)
        if extra == 50000:
            ms, txt = prof(lambda: svc.get_incident(k, inc["id"]), 20, "tottime")
            OUT["3d_profile_get_incident_50k_volunteers"] = {"ms": ms, "profile": txt}
        svc.close()


def part4_sync():
    rows = []
    for sync in ("FULL", "NORMAL"):
        for rep in range(3):
            svc = new_svc(sync)
            seed(svc, owners=60, vols=20)
            t0 = time.perf_counter()
            ids = [svc.create_incident(f"o{i}", f"s{i}", "loud", location=loc())["id"] for i in range(60)]
            ci = (time.perf_counter() - t0) / 60
            t0 = time.perf_counter()
            for i in range(60):
                svc.add_message(f"o{i}", ids[i], "x")
            am = (time.perf_counter() - t0) / 60
            t0 = time.perf_counter()
            for i in range(60):
                svc.update_location(f"o{i}", ids[i], loc())
            ul = (time.perf_counter() - t0) / 60
            rows.append({"synchronous": sync, "rep": rep, "create_incident_ms": round(ci * 1000, 2),
                         "add_message_ms": round(am * 1000, 2), "update_location_ms": round(ul * 1000, 2)})
            print("sync", rows[-1], flush=True)
            svc.close()
    OUT["4_synchronous_full_vs_normal_single_thread"] = rows


def part5_history_growth():
    """Old data never leaves the hot tables: closed incidents' location/event/message rows are scanned by
    per-tick and per-request queries that have no index on incident_id."""
    rows = []
    for hist in (0, 100_000, 400_000):
        for idx in (False, True):
            svc = new_svc()
            seed(svc, owners=20, contacts=0, vols=10, verified=True)
            now = time.time()
            if hist:
                svc._tx(lambda con: con.executemany(
                    "INSERT INTO incidents(id, owner, client_id, mode, status, created_at, updated_at, ended_at) "
                    "VALUES (?, 'o0', ?, 'loud', 'resolved', ?, ?, ?)",
                    ((f"inc_old{k}", f"old{k}", now - 86400, now - 86400, now - 86000) for k in range(hist // 50 + 1))))
                svc._tx(lambda con: con.executemany(
                    "INSERT INTO incident_locations(incident_id, lat, lon, is_precise, received_at) VALUES (?,?,?,1,?)",
                    ((f"inc_old{i // 50}", TLV[0], TLV[1], now - 86400) for i in range(hist))))
                svc._tx(lambda con: con.executemany(
                    "INSERT INTO incident_events(incident_id, ts, kind, text) VALUES (?,?,'x','old')",
                    ((f"inc_old{i // 20}", now - 86400) for i in range(hist))))
            if idx:
                from lt_server import PROPOSED_INDEXES
                svc._con.executescript(PROPOSED_INDEXES)
            ids = [svc.create_incident(f"o{i}", f"h{i}", "loud", location=loc())["id"] for i in range(20)]
            ticks, gets = [], []
            for _ in range(5):
                svc._wake_dispatch()
                t0 = time.perf_counter()
                svc.run_escalation_tick()
                ticks.append(time.perf_counter() - t0)
            for i in range(20):
                t0 = time.perf_counter()
                svc.get_incident(f"o{i}", ids[i])
                gets.append(time.perf_counter() - t0)
            row = {"historical_location_and_event_rows": hist, "proposed_indexes": idx, "active_incidents": 20,
                   "tick_ms_p50": summarize(ticks)["p50"], "get_incident_ms_p50": summarize(gets)["p50"]}
            rows.append(row)
            print("history:", row, flush=True)
            svc.close()
    OUT["5_history_growth"] = rows


if __name__ == "__main__":
    OUT["machine"] = machine_context()
    parts = sys.argv[1:] or ["1", "4", "2", "3", "5"]
    for p in parts:
        {"1": part1_commits, "2": part2_tick_scaling, "3": part3_profiles, "4": part4_sync,
         "5": part5_history_growth}[p]()
    print("saved:", save_result("profile_hotpaths", OUT))
