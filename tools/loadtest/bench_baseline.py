# -*- coding: utf-8 -*-
"""
bench_baseline.py - (A1) baseline latency p50/p95/p99 + throughput per endpoint and concurrency level.

    .venv/Scripts/python.exe tools/loadtest/bench_baseline.py --reps 3 --seconds 6

Server: real create_server() in a child process on 127.0.0.1 with a temp DB (lt_server.py).
"""
import argparse
import random
import threading
import time

from lt_common import PW, TLV, Client, ServerProc, machine_context, save_result, summarize

try:
    import psutil
except ImportError:
    psutil = None


def loc(rng, spread=0.02):
    return {"lat": TLV[0] + rng.uniform(-spread, spread), "lon": TLV[1] + rng.uniform(-spread, spread),
            "is_precise": True, "source": "GNSS", "accuracy_m": 8}


def seed(srv, n_owners, n_vols):
    rng = random.Random(1)
    users = []
    for i in range(n_owners):
        users.append({"u": f"o{i}", "contacts": [f"c{(i + k) % n_owners}" for k in range(3)]})
    for i in range(n_owners):
        la, lo = TLV[0] + rng.uniform(-0.05, 0.05), TLV[1] + rng.uniform(-0.05, 0.05)
        users.append({"u": f"c{i}", "presence": [la, lo]})
    for i in range(n_vols):
        users.append({"u": f"v{i}", "vol": [TLV[0] + rng.uniform(-0.02, 0.02), TLV[1] + rng.uniform(-0.02, 0.02)],
                      "verified": i % 2 == 0})
    return srv.seed({"users": users})["tokens"]


class Runner:
    def __init__(self, srv, tokens):
        self.srv, self.tokens = srv, tokens
        self.proc = psutil.Process(srv.pid) if psutil else None

    def level(self, name, conc, seconds, op, setup=None, max_ops=None):
        """op(worker_state, client) -> (status, dt). Runs `conc` threads for `seconds`."""
        lat, statuses = [], {}
        lk = threading.Lock()
        stop = time.perf_counter() + seconds
        states = [setup(i) if setup else {"i": i} for i in range(conc)]
        cpu0 = self.srv.stats(reset=True).get("cpu_times")
        t_start = time.perf_counter()

        def w(i):
            st = states[i]
            c = Client(self.srv.port, timeout=120)
            n = 0
            while time.perf_counter() < stop and (max_ops is None or n < max_ops):
                status, dt = op(st, c)
                n += 1
                with lk:
                    statuses[status] = statuses.get(status, 0) + 1
                    if status == 200:
                        lat.append(dt)
            c.close()
        ths = [threading.Thread(target=w, args=(i,), daemon=True) for i in range(conc)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        wall = time.perf_counter() - t_start
        sst = self.srv.stats()
        out = {"endpoint": name, "concurrency": conc, "wall_s": round(wall, 2),
               "ok_rps": round(len(lat) / wall, 1), "latency_ms": summarize(lat), "statuses": statuses,
               "server_lock_hold_utilization": sst.get("lock", {}).get("hold_utilization"),
               "server_lock_wait_p95_ms": sst.get("lock", {}).get("wait", {}).get("p95"),
               "server_threads": sst.get("threads_os"), "server_rss_mb": sst.get("rss_mb")}
        cpu1 = sst.get("cpu_times")
        if cpu0 and cpu1:
            out["server_cpu_cores_used"] = round(((cpu1["user"] + cpu1["system"]) - (cpu0["user"] + cpu0["system"]))
                                                 / wall, 2)
        tick = sst["timings"].get("TICK_TOTAL")
        if tick:
            out["tick_ms"] = {k: tick[k] for k in ("n", "p50", "p95", "max")}
        print(f"  {name:28s} c={conc:<3d} {out['ok_rps']:8.1f} ok/s  p50={out['latency_ms'].get('p50')} "
              f"p95={out['latency_ms'].get('p95')} p99={out['latency_ms'].get('p99')} ms  "
              f"cpu={out.get('server_cpu_cores_used')} lock={out['server_lock_hold_utilization']} {statuses}",
              flush=True)
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=6)
    ap.add_argument("--owners", type=int, default=300)
    ap.add_argument("--vols", type=int, default=30)
    ap.add_argument("--levels", default="1,16,64")
    ap.add_argument("--only", default="")
    ap.add_argument("--synchronous", default="")
    ap.add_argument("--indexes", action="store_true")
    ap.add_argument("--tag", default="baseline")
    a = ap.parse_args()
    levels = [int(x) for x in a.levels.split(",")]
    result = {"machine_before": machine_context(), "params": vars(a), "reps": []}
    print("machine:", result["machine_before"], flush=True)
    for rep in range(a.reps):
        srv = ServerProc(tick=1.0, ack=120, ring=45, synchronous=a.synchronous, indexes=a.indexes)
        try:
            tokens = seed(srv, a.owners, a.vols)
            R = Runner(srv, tokens)
            rng = random.Random(rep)
            res = []
            print(f"rep {rep + 1}/{a.reps}  server pid={srv.pid}", flush=True)
            only = set(a.only.split(",")) if a.only else None

            def want(n):
                return only is None or n in only

            # ---- login (PBKDF2 200k)
            if want("login"):
                for conc in levels:
                    def op(st, c):
                        s, js, dt = c.req("POST", "/v1/auth/login",
                                          {"username": f"o{rng.randrange(a.owners)}", "password": PW, "device": "lt"})
                        return s, dt
                    res.append(R.level("POST /auth/login", conc, a.seconds, op))

            # ---- GET /circle
            if want("circle"):
                for conc in levels:
                    def op(st, c):
                        s, js, dt = c.req("GET", "/v1/circle", token=tokens[f"o{st['i'] % a.owners}"])
                        return s, dt
                    res.append(R.level("GET /circle", conc, a.seconds, op))

            # ---- POST /incidents (loud, with location; 3 contacts each -> ring chain; volunteers dispatched)
            created = []
            if want("incidents"):
                for conc in levels:
                    def op(st, c):
                        owner = f"o{(st['i'] + st.setdefault('k', 0) * 64) % a.owners}"
                        st["k"] += 1
                        s, js, dt = c.req("POST", "/v1/incidents",
                                          {"client_id": f"r{rep}-{conc}-{st['i']}-{st['k']}-{time.time_ns()}",
                                           "mode": "loud", "emergency_type": "medical", "location": loc(rng)},
                                          token=tokens[owner])
                        if s == 200:
                            created.append((owner, js["id"]))
                        return s, dt
                    res.append(R.level("POST /incidents", conc, a.seconds, op))
                    nact = srv.sql("SELECT COUNT(*) AS n FROM incidents WHERE status='active'")[0]["n"]
                    res[-1]["active_incidents_after"] = nact
                    # close them (outside the timing) so each level starts from the same state
                    cc = Client(srv.port)
                    for owner, iid in created:
                        cc.req("PATCH", f"/v1/incidents/{iid}", {"status": "resolved"}, token=tokens[owner])
                    created.clear()

            # one active incident per owner for the next tests (loud, contacts ringing, volunteers notified)
            inc_of = {}
            cc = Client(srv.port)
            for i in range(min(a.owners, max(levels))):
                s, js, _ = cc.req("POST", "/v1/incidents", {"client_id": f"base{rep}-{i}", "mode": "loud",
                                                            "location": loc(rng)}, token=tokens[f"o{i}"])
                inc_of[f"o{i}"] = js["id"]
            print(f"  ({len(inc_of)} active incidents for the next tests)", flush=True)

            if want("location"):
                for conc in levels:
                    def op(st, c):
                        o = f"o{st['i']}"
                        s, js, dt = c.req("POST", f"/v1/incidents/{inc_of[o]}/location", loc(rng), token=tokens[o])
                        return s, dt
                    res.append(R.level("POST /incidents/{id}/location", conc, a.seconds, op))

            if want("get"):
                for conc in levels:
                    def op(st, c):
                        o = f"o{st['i']}"
                        s, js, dt = c.req("GET", f"/v1/incidents/{inc_of[o]}", token=tokens[o])
                        return s, dt
                    res.append(R.level("GET /incidents/{id} (owner)", conc, a.seconds, op))
                for conc in levels:
                    # first contact (rings first or not - trusted has access once notified); find a notified one
                    def setup(i):
                        o = f"o{i}"
                        rows = srv.sql("SELECT username FROM recipients WHERE incident_id=? AND role='trusted' "
                                       "AND state!='queued' LIMIT 1", (inc_of[o],))
                        return {"i": i, "tok": tokens[rows[0]["username"]], "iid": inc_of[o]}

                    def op(st, c):
                        s, js, dt = c.req("GET", f"/v1/incidents/{st['iid']}", token=st["tok"])
                        return s, dt
                    res.append(R.level("GET /incidents/{id} (contact)", conc, a.seconds, op, setup=setup))

            if want("messages"):
                for conc in levels:
                    def setup(i):
                        return {"i": i, "n": 0, "iid": inc_of[f"o{i}"]}

                    def op(st, c):
                        o = f"o{st['i']}"
                        if st["n"] and st["n"] % 19 == 0:     # 20 msgs/min/incident limit -> fresh incident
                            s, js, _ = Client(srv.port).req("POST", "/v1/incidents", {
                                "client_id": f"m{rep}-{conc}-{st['i']}-{st['n']}", "mode": "silent",
                                "location": loc(rng)}, token=tokens[o])
                            st["iid"] = js["id"]
                        st["n"] += 1
                        s, js, dt = c.req("POST", f"/v1/incidents/{st['iid']}/messages",
                                          {"text": "בדיקת עומס " + str(st["n"])}, token=tokens[o])
                        return s, dt
                    res.append(R.level("POST /incidents/{id}/messages", conc, a.seconds, op, setup=setup))

            if want("poll"):
                for conc in levels:
                    def op(st, c):
                        s, js, dt = c.req("GET", "/v1/notifications?after=0&wait=0",
                                          token=tokens[f"c{st['i'] % a.owners}"])
                        return s, dt
                    res.append(R.level("GET /notifications wait=0", conc, a.seconds, op))

                # long-poll delivery latency: contact waits (wait=25), owner posts a message
                for conc in [x for x in levels if x <= 64]:
                    lat = []
                    lk = threading.Lock()

                    def pair(i):
                        o = f"o{i}"
                        rows = srv.sql("SELECT username FROM recipients WHERE incident_id=? AND role='trusted' "
                                       "AND state!='queued' LIMIT 1", (inc_of[o],))
                        ct = tokens[rows[0]["username"]]
                        cpoll, cpost = Client(srv.port, timeout=60), Client(srv.port)
                        s, js, _ = cpoll.req("GET", "/v1/notifications/cursor", token=ct)
                        last = js["last_id"]
                        # fresh incident to avoid the 20/min limit across reps
                        s, js, _ = cpost.req("POST", "/v1/incidents", {"client_id": f"lp{rep}-{conc}-{i}",
                                                                      "mode": "silent", "location": loc(rng)},
                                             token=tokens[o])
                        iid = js["id"]
                        rows = srv.sql("SELECT username FROM recipients WHERE incident_id=? AND role='trusted' "
                                       "AND state!='queued' LIMIT 1", (iid,))
                        ct = tokens[rows[0]["username"]]
                        s, js, _ = cpoll.req("GET", "/v1/notifications/cursor", token=ct)
                        last = js["last_id"]
                        for k in range(8):
                            box = {}

                            def poller():
                                after = last
                                while True:
                                    s2, js2, _ = cpoll.req("GET", f"/v1/notifications?after={after}&wait=25", token=ct)
                                    if s2 != 200:
                                        return
                                    rows2 = js2["notifications"]
                                    if rows2:
                                        after = rows2[-1]["id"]
                                    if any(r["payload"].get("event") == "message" and r.get("incident_id") == iid for r in rows2):
                                        box["t"] = time.perf_counter()
                                        box["last"] = after
                                        return
                            th = threading.Thread(target=poller)
                            th.start()
                            time.sleep(0.3)
                            t0 = time.perf_counter()
                            cpost.req("POST", f"/v1/incidents/{iid}/messages", {"text": f"ping {k}"}, token=tokens[o])
                            th.join(30)
                            if "t" in box:
                                with lk:
                                    lat.append(box["t"] - t0)
                                last = box["last"]
                    ths = [threading.Thread(target=pair, args=(i,)) for i in range(conc)]
                    for t in ths:
                        t.start()
                    for t in ths:
                        t.join()
                    out = {"endpoint": "long-poll delivery (POST msg -> waiting GET returns)", "concurrency": conc,
                           "latency_ms": summarize(lat)}
                    print(f"  long-poll delivery c={conc}: {out['latency_ms']}", flush=True)
                    res.append(out)
            final = srv.stats()
            result["reps"].append({"rep": rep, "results": res, "server_final": {
                k: final.get(k) for k in ("rss_mb", "threads_os", "handles", "db_mb")},
                "server_errors": srv.errors[-20:]})
        finally:
            srv.stop()
    result["machine_after"] = machine_context()
    print("saved:", save_result(a.tag, result))


if __name__ == "__main__":
    main()
