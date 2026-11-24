# -*- coding: utf-8 -*-
"""
bench_idle.py - (A2) N users each holding a long-poll (wait=25, re-issued) or an SSE stream.
Reports server threads / RSS / handles, connection errors during the ramp, and whether NEW requests are
still served (probe latency of GET /circle and alert delivery to one of the idle users).

    .venv/Scripts/python.exe tools/loadtest/bench_idle.py --levels 100,300,1000,2000 --mode longpoll
    .venv/Scripts/python.exe tools/loadtest/bench_idle.py --levels 100,300,1000 --mode sse
"""
import argparse
import asyncio
import threading
import time

from lt_common import AConn, Client, ServerProc, machine_context, save_result, summarize


async def holder(port, token, mode, stop, st, burst_sem):
    c = AConn(port)
    after = 10 ** 9 if mode == "longpoll" else 0
    try:
        async with burst_sem:
            await c.open()
        st["connected"] += 1
    except Exception as e:  # noqa: BLE001
        st["connect_err"][type(e).__name__] = st["connect_err"].get(type(e).__name__, 0) + 1
        return
    try:
        if mode == "sse":
            c.w.write((f"GET /v1/stream?after={after} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\n\r\n")
                      .encode())
            await c.w.drain()
            while not stop.is_set():
                line = await asyncio.wait_for(c.r.readline(), 60)
                if not line:
                    st["sse_closed"] += 1
                    return
                if line.startswith(b": connected"):
                    st["sse_ok"] += 1
        else:
            while not stop.is_set():
                s, js = await c.req("GET", f"/v1/notifications?after={after}&wait=25", token=token, timeout=60)
                st["polls"][s] = st["polls"].get(s, 0) + 1
    except Exception as e:  # noqa: BLE001
        st["hold_err"][type(e).__name__] = st["hold_err"].get(type(e).__name__, 0) + 1
    finally:
        await c.close()


def run_level(n, mode, burst, settle):
    srv = ServerProc(tick=1.0)
    try:
        users = [{"u": f"u{i}"} for i in range(n)] + [{"u": "owner", "contacts": ["u0"]}]
        tokens = srv.seed({"users": users})["tokens"]
        base = srv.stats()
        stop = threading.Event()
        st = {"connected": 0, "connect_err": {}, "hold_err": {}, "polls": {}, "sse_ok": 0, "sse_closed": 0}
        ready = threading.Event()

        def loop():
            async def main():
                astop = asyncio.Event()
                sem = asyncio.Semaphore(burst)
                tasks = [asyncio.ensure_future(holder(srv.port, tokens[f"u{i}"], mode, astop, st, sem))
                         for i in range(n)]
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(0.2)
                astop.set()
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            asyncio.run(main())
        th = threading.Thread(target=loop, daemon=True)
        t0 = time.perf_counter()
        th.start()
        ready.wait()
        # wait until connections are up
        while time.perf_counter() - t0 < settle and st["connected"] + sum(st["connect_err"].values()) < n:
            time.sleep(0.5)
        ramp = time.perf_counter() - t0
        time.sleep(3)
        s1 = srv.stats(reset=True)
        # probes while N connections are parked
        c = Client(srv.port, timeout=30)
        lat, errs = [], {}
        for _ in range(100):
            s, js, dt = c.req("GET", "/v1/circle", token=tokens["owner"])
            if s == 200:
                lat.append(dt)
            else:
                errs[s] = errs.get(s, 0) + 1
        # fresh connection per request (what a new phone does)
        lat_new = []
        for _ in range(30):
            cc = Client(srv.port, timeout=30)
            s, js, dt = cc.req("GET", "/v1/health")
            cc.close()
            if s == 200:
                lat_new.append(dt)
        # alert to u0 (one of the idle holders): POST SOS -> u0 notification row exists
        t_sos = time.time()
        s, js, dt_sos = c.req("POST", "/v1/incidents", {"client_id": f"idle{n}", "mode": "loud",
                                                         "location": {"lat": 32.08, "lon": 34.78}},
                              token=tokens["owner"])
        time.sleep(15 if mode == "sse" else 2)
        s2 = srv.stats()
        out = {"mode": mode, "n": n, "ramp_s": round(ramp, 1), "connected": st["connected"],
               "connect_errors": st["connect_err"], "hold_errors": dict(st["hold_err"]), "polls": dict(st["polls"]),
               "sse_ok": st["sse_ok"], "sse_closed": st["sse_closed"],
               "server_threads_before": base.get("threads_os"), "server_threads_parked": s1.get("threads_os"),
               "server_rss_mb_before": base.get("rss_mb"), "server_rss_mb_parked": s1.get("rss_mb"),
               "server_handles_parked": s1.get("handles"),
               "probe_keepalive_circle": summarize(lat), "probe_errors": errs,
               "probe_new_connection_health": summarize(lat_new),
               "sos_post_ms": round(dt_sos * 1000, 1), "sos_status": s,
               "lock_util_while_parked": s2.get("lock", {}).get("hold_utilization"),
               "lock_acq_per_s_while_parked": round(s2.get("lock", {}).get("acquisitions", 0)
                                                    / max(1e-9, s2.get("lock", {}).get("elapsed_s", 1)), 1)}
        stop.set()
        th.join(30)
        return out
    finally:
        srv.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="100,300,1000")
    ap.add_argument("--mode", default="longpoll", choices=["longpoll", "sse"])
    ap.add_argument("--burst", type=int, default=10000, help="max simultaneous connect() in flight")
    ap.add_argument("--settle", type=float, default=90)
    ap.add_argument("--reps", type=int, default=1)
    a = ap.parse_args()
    res = {"machine_before": machine_context(), "levels": []}
    print(res["machine_before"], flush=True)
    for rep in range(a.reps):
        for n in [int(x) for x in a.levels.split(",")]:
            r = run_level(n, a.mode, a.burst, a.settle)
            r["rep"] = rep
            res["levels"].append(r)
            print(r, flush=True)
    res["machine_after"] = machine_context()
    print("saved:", save_result(f"idle_{a.mode}", res))


if __name__ == "__main__":
    main()
