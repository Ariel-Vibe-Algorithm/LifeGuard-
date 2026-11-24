# -*- coding: utf-8 -*-
"""
bench_storm.py - (A3) emergency storm: N concurrent SOS (3 contacts each) + silent SOS without contacts
+ moving volunteers that long-poll and accept/decline/ignore like people do.

Measures: POST /incidents latency, end-to-end alert latency (POST start -> first contact's long-poll
returns the incident_new), time-to-first-volunteer for silent/no-contact SOS, tick duration (in-process
timing of run_escalation_tick and its phases), lock utilization, and dispatch correctness invariants.

    .venv/Scripts/python.exe tools/loadtest/bench_storm.py --incidents 200 --vols 50 --duration 75 --reps 2
"""
import argparse
import asyncio
import random
import threading
import time

from lt_common import TLV, AConn, Client, ServerProc, machine_context, save_result, summarize

EVENTS = []          # (t, user, type, incident_id, payload)
EV_LOCK = threading.Lock()


def seed(srv, a, rng):
    users = []
    for i in range(a.incidents):
        users.append({"u": f"s{i}", "contacts": [f"k{i}_{j}" for j in range(a.contacts)]})
        for j in range(a.contacts):
            users.append({"u": f"k{i}_{j}", "presence": [TLV[0] + rng.uniform(-0.05, 0.05),
                                                         TLV[1] + rng.uniform(-0.05, 0.05)]})
    for i in range(a.silent):
        users.append({"u": f"n{i}"})
    for i in range(a.vols):
        users.append({"u": f"v{i}", "vol": [TLV[0] + rng.uniform(-0.015, 0.015), TLV[1] + rng.uniform(-0.015, 0.015)],
                      "verified": i % 2 == 0})
    return srv.seed({"users": users})["tokens"]


async def poller(port, user, token, stop, behaviour, rng, stats):
    c = AConn(port)
    after = 0
    accepted = False
    while not stop.is_set():
        try:
            st, js = await c.req("GET", f"/v1/notifications?after={after}&wait=25", token=token, timeout=40)
        except Exception as e:  # noqa: BLE001
            stats["poll_errors"] = stats.get("poll_errors", 0) + 1
            await c.close()
            await asyncio.sleep(0.5)
            continue
        if st != 200:
            stats[f"poll_{st}"] = stats.get(f"poll_{st}", 0) + 1
            await asyncio.sleep(0.5)
            continue
        t = time.perf_counter()
        for n in js["notifications"]:
            after = max(after, n["id"])
            with EV_LOCK:
                EVENTS.append((t, user, n["type"], n.get("incident_id"), n["payload"]))
            if n["type"] == "incident_new":
                iid = n["incident_id"]
                if behaviour == "volunteer":
                    r = rng.random()
                    if not accepted and r < 0.6:
                        accepted = True
                        asyncio.ensure_future(respond(port, token, iid, "accept", rng.uniform(1, 4), stats))
                    elif r < 0.8:
                        asyncio.ensure_future(respond(port, token, iid, "decline", rng.uniform(1, 4), stats))
                elif behaviour == "contact" and n["payload"].get("ring") and rng.random() < 0.5:
                    asyncio.ensure_future(respond(port, token, iid, "accept", rng.uniform(2, 8), stats))
            # ACK like the apps do (marks 'delivered')
            asyncio.ensure_future(ack(port, token, n["id"], stats))
    await c.close()


async def ack(port, token, nid, stats):
    c = AConn(port)
    try:
        st, _ = await c.req("POST", f"/v1/notifications/{nid}/received", token=token, timeout=30)
        stats[f"ack_{st}"] = stats.get(f"ack_{st}", 0) + 1
    except Exception:  # noqa: BLE001
        stats["ack_err"] = stats.get("ack_err", 0) + 1
    finally:
        await c.close()


async def respond(port, token, iid, action, delay, stats):
    await asyncio.sleep(delay)
    c = AConn(port)
    try:
        st, _ = await c.req("POST", f"/v1/incidents/{iid}/respond", token=token, body={"action": action}, timeout=60)
        stats[f"respond_{action}_{st}"] = stats.get(f"respond_{action}_{st}", 0) + 1
    except Exception:  # noqa: BLE001
        stats["respond_err"] = stats.get("respond_err", 0) + 1
    finally:
        await c.close()


def run_pollers(port, who, stop, stats, ready):
    async def main():
        astop = asyncio.Event()
        rng = random.Random(7)
        tasks = [asyncio.ensure_future(poller(port, u, tok, astop, beh, rng, stats)) for u, tok, beh in who]
        ready.set()
        while not stop.is_set():
            await asyncio.sleep(0.2)
        astop.set()
        await asyncio.sleep(0.1)
        for t in tasks:
            t.cancel()
    asyncio.run(main())


def one_rep(a, rep):
    rng = random.Random(100 + rep)
    srv = ServerProc(tick=1.0, ack=a.ack, ring=a.ring, synchronous=a.synchronous, indexes=a.indexes)
    EVENTS.clear()
    try:
        tokens = seed(srv, a, rng)
        who = [(f"k{i}_{j}", tokens[f"k{i}_{j}"], "contact") for i in range(a.incidents) for j in range(a.contacts)]
        who += [(f"v{i}", tokens[f"v{i}"], "volunteer") for i in range(a.vols)]
        who += [(f"s{i}", tokens[f"s{i}"], "owner") for i in range(a.incidents)]
        stop, ready, pstats = threading.Event(), threading.Event(), {}
        th = threading.Thread(target=run_pollers, args=(srv.port, who, stop, pstats, ready), daemon=True)
        th.start()
        ready.wait()
        time.sleep(3)                                  # all long-polls parked
        idle = srv.stats(reset=True)
        print(f"rep {rep}: {len(who)} long-polls parked; server threads={idle.get('threads_os')} "
              f"rss={idle.get('rss_mb')}MB", flush=True)

        # ---- moving volunteers
        moves = {"n": 0, "lat": []}
        mv_lock = threading.Lock()

        def mover(k):
            c = Client(srv.port)
            pos = {}
            while not stop.is_set():
                for i in range(k, a.vols, 10):
                    p = pos.setdefault(i, [TLV[0] + rng.uniform(-0.015, 0.015), TLV[1] + rng.uniform(-0.015, 0.015)])
                    p[0] += rng.uniform(-0.0005, 0.0005)
                    p[1] += rng.uniform(-0.0005, 0.0005)
                    st, _, dt = c.req("PUT", "/v1/me/presence", {"lat": p[0], "lon": p[1], "is_precise": True},
                                      token=tokens[f"v{i}"])
                    with mv_lock:
                        moves["n"] += 1
                        moves["lat"].append(dt)
                time.sleep(a.move_interval)
        if not a.no_move:
            for k in range(10):
                threading.Thread(target=mover, args=(k,), daemon=True).start()

        # ---- the storm
        owners = [(f"s{i}", "loud" if i % 2 == 0 else "silent") for i in range(a.incidents)]
        owners += [(f"n{i}", "silent") for i in range(a.silent)]
        barrier = threading.Barrier(len(owners))
        posts = {}
        types = ["medical", "security", "physical_danger", "fire", "unknown"]

        def owner(idx):
            name, mode = owners[idx]
            c = Client(srv.port, timeout=300)
            here = {"lat": TLV[0] + rng.uniform(-0.01, 0.01), "lon": TLV[1] + rng.uniform(-0.01, 0.01),
                    "is_precise": True, "source": "GNSS"}
            barrier.wait()
            t0 = time.perf_counter()
            st, js, dt = c.req("POST", "/v1/incidents", {"client_id": f"storm{rep}-{idx}", "mode": mode,
                                                         "emergency_type": types[idx % len(types)],
                                                         "location": here}, token=tokens[name])
            posts[name] = {"t0": t0, "dt": dt, "status": st, "iid": js.get("id") if isinstance(js, dict) else None,
                           "mode": mode}
            locs = []
            while not stop.is_set():
                time.sleep(5)
                here["lat"] += rng.uniform(-0.0002, 0.0002)
                if posts[name]["iid"]:
                    st2, _, dt2 = c.req("POST", f"/v1/incidents/{posts[name]['iid']}/location", here, token=tokens[name])
                    locs.append(dt2)
            posts[name]["loc_lat"] = locs
        t_storm = time.perf_counter()
        ths = [threading.Thread(target=owner, args=(i,), daemon=True) for i in range(len(owners))]
        for t in ths:
            t.start()
        # sample tick timings during the window
        samples = []
        end = t_storm + a.duration
        while time.perf_counter() < end:
            time.sleep(5)
            s = srv.stats()
            tk = s["timings"].get("TICK_TOTAL", {})
            samples.append({"t": round(time.perf_counter() - t_storm, 1), "tick_n": tk.get("n"),
                            "tick_p50": tk.get("p50"), "tick_max": tk.get("max"),
                            "threads": s.get("threads_os"), "rss_mb": s.get("rss_mb"),
                            "lock_util": s.get("lock", {}).get("hold_utilization")})
            print("  ", samples[-1], flush=True)
        final = srv.stats()
        stop.set()
        for t in ths:
            t.join(15)
        th.join(10)

        # ---------------- analysis
        iid_owner = {p["iid"]: n for n, p in posts.items() if p["iid"]}
        first_contact, first_vol = {}, {}
        for t, user, ntype, iid, payload in sorted(EVENTS, key=lambda e: e[0]):
            if ntype != "incident_new" or iid not in iid_owner:
                continue
            if user.startswith("k") and iid not in first_contact:
                first_contact[iid] = t
            if user.startswith("v") and iid not in first_vol:
                first_vol[iid] = t
        alert = [first_contact[i] - posts[iid_owner[i]]["t0"] for i in first_contact]
        vol_loud = [first_vol[i] - posts[iid_owner[i]]["t0"] for i in first_vol if posts[iid_owner[i]]["mode"] == "loud"]
        nocontact = [i for i, o in iid_owner.items() if o.startswith("n")]
        vol_nc = [first_vol[i] - posts[iid_owner[i]]["t0"] for i in nocontact if i in first_vol]
        post_lat = [p["dt"] for p in posts.values() if p["status"] == 200]
        loc_lat = [x for p in posts.values() for x in p.get("loc_lat", [])]
        db_alert = srv.sql("""SELECT n.created_at - i.created_at AS d FROM incidents i JOIN notifications n
                              ON n.incident_id=i.id AND n.type='incident_new' AND n.id=(SELECT MIN(id) FROM notifications
                              WHERE incident_id=i.id AND type='incident_new' AND username LIKE 'k%')""")
        inv = {
            "volunteer_busy_in_more_than_one_active": srv.sql(
                """SELECT r.username, COUNT(*) AS n FROM recipients r JOIN incidents i ON i.id=r.incident_id
                   WHERE i.status='active' AND r.role='volunteer' AND r.state IN ('notified','delivered','accepted')
                   GROUP BY r.username HAVING COUNT(*)>1"""),
            "volunteer_accepted_more_than_one": srv.sql(
                """SELECT username, COUNT(*) AS n FROM recipients WHERE role='volunteer' AND state='accepted'
                   GROUP BY username HAVING COUNT(*)>1"""),
            "silent_nocontact_without_any_volunteer": srv.sql(
                """SELECT i.id FROM incidents i WHERE i.owner LIKE 'n%' AND NOT EXISTS(SELECT 1 FROM recipients r
                   WHERE r.incident_id=i.id AND r.role='volunteer')"""),
            "free_volunteers_at_end": srv.sql(
                """SELECT COUNT(*) AS n FROM volunteers v WHERE v.available=1 AND NOT EXISTS(
                   SELECT 1 FROM recipients r JOIN incidents i ON i.id=r.incident_id WHERE r.username=v.username
                   AND i.status='active' AND r.role='volunteer' AND r.state IN ('notified','delivered','accepted'))""")[0]["n"],
            "loud_incidents_without_accepted_volunteer": srv.sql(
                """SELECT COUNT(*) AS n FROM incidents i WHERE i.mode='loud' AND i.status='active' AND NOT EXISTS(
                   SELECT 1 FROM recipients r WHERE r.incident_id=i.id AND r.role='volunteer' AND r.state='accepted')""")[0]["n"],
            "ring_stuck_ringing_too_long": srv.sql(
                """SELECT COUNT(*) AS n FROM recipients r JOIN incidents i ON i.id=r.incident_id WHERE i.status='active'
                   AND r.ring_status='ringing' AND r.state IN ('notified','delivered') AND r.notified_at < ?""",
                (time.time() - a.ring - 10,))[0]["n"],
            "contacts_rung_per_incident": srv.sql(
                """SELECT n, COUNT(*) AS incidents FROM (SELECT incident_id, COUNT(*) AS n FROM recipients WHERE role='trusted'
                   AND ring_status IN ('ringing','rang','answered') GROUP BY incident_id) GROUP BY n"""),
            "volunteer_states": srv.sql("SELECT state, COUNT(*) AS n FROM recipients WHERE role='volunteer' GROUP BY state"),
        }
        tm = final["timings"]
        out = {
            "rep": rep, "params": vars(a),
            "posts_ok": sum(1 for p in posts.values() if p["status"] == 200),
            "posts_status": {s: sum(1 for p in posts.values() if p["status"] == s) for s in {p["status"] for p in posts.values()}},
            "post_incident_latency": summarize(post_lat),
            "location_update_latency": summarize(loc_lat),
            "presence_updates": moves["n"], "presence_latency": summarize(moves["lat"]),
            "alert_latency_first_contact_longpoll": summarize(alert),
            "alert_latency_first_contact_db": summarize([r["d"] for r in db_alert if r["d"] is not None]),
            "incidents_with_contacts_alerted": f"{len(first_contact)}/{a.incidents}",
            "first_volunteer_latency_loud": summarize(vol_loud),
            "first_volunteer_latency_silent_nocontact": summarize(vol_nc),
            "silent_nocontact_reached": f"{len(vol_nc)}/{len(nocontact)}",
            "tick": {k: v for k, v in tm.items() if k.startswith("tick:") or k == "TICK_TOTAL"},
            "req_timings": {k: v for k, v in tm.items() if k.startswith("req:")},
            "lock": final.get("lock"), "server": {k: final.get(k) for k in ("threads_os", "rss_mb", "handles", "db_mb")},
            "tick_samples": samples, "invariants": inv, "poller_stats": pstats,
            "server_errors": srv.errors[-30:],
        }
        return out
    finally:
        srv.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--incidents", type=int, default=200)
    ap.add_argument("--contacts", type=int, default=3)
    ap.add_argument("--silent", type=int, default=20, help="silent SOS from owners with NO contacts")
    ap.add_argument("--vols", type=int, default=50)
    ap.add_argument("--duration", type=float, default=75)
    ap.add_argument("--ring", type=float, default=15)
    ap.add_argument("--ack", type=float, default=30)
    ap.add_argument("--move-interval", type=float, default=2.0)
    ap.add_argument("--no-move", action="store_true")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--synchronous", default="")
    ap.add_argument("--indexes", action="store_true")
    ap.add_argument("--tag", default="storm")
    a = ap.parse_args()
    res = {"machine_before": machine_context(), "reps": []}
    print(res["machine_before"], flush=True)
    for rep in range(a.reps):
        r = one_rep(a, rep)
        res["reps"].append(r)
        for k in ("posts_ok", "post_incident_latency", "alert_latency_first_contact_longpoll",
                  "alert_latency_first_contact_db", "incidents_with_contacts_alerted", "first_volunteer_latency_loud",
                  "first_volunteer_latency_silent_nocontact", "silent_nocontact_reached", "location_update_latency",
                  "presence_updates", "presence_latency", "lock", "server"):
            print(f"  {k}: {r[k]}", flush=True)
        print("  TICK:", {k: (v.get('p50'), v.get('p95'), v.get('max'), v.get('n')) for k, v in r["tick"].items()})
        print("  invariants:", r["invariants"], flush=True)
        print("  pollers:", r["poller_stats"], "errors:", r["server_errors"][-5:], flush=True)
    res["machine_after"] = machine_context()
    print("saved:", save_result(a.tag, res))


if __name__ == "__main__":
    main()
