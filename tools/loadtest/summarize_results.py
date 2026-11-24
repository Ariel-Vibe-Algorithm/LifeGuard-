# -*- coding: utf-8 -*-
"""summarize_results.py <baseline_json> [...] - min/median/max across reps per (endpoint, concurrency)."""
import json
import statistics
import sys


def main():
    for path in sys.argv[1:]:
        d = json.load(open(path, encoding="utf-8"))
        m = d.get("machine_before", {})
        print(f"\n== {path}\n   machine busy={m.get('cpu_percent_busy')}% cores={m.get('cpu_count')} "
              f"time={m.get('time')} reps={len(d['reps'])}")
        agg = {}
        for rep in d["reps"]:
            for r in rep["results"]:
                key = (r["endpoint"], r["concurrency"])
                agg.setdefault(key, []).append(r)
        print(f"   {'endpoint':52s} {'c':>3s} {'ok/s (min/med/max)':>22s} {'p50':>7s} {'p95':>7s} {'p99':>7s} "
              f"{'cpu':>5s} {'lock':>5s}")
        for (ep, c), rs in agg.items():
            rps = [r.get("ok_rps") for r in rs if r.get("ok_rps") is not None]
            med = lambda k: statistics.median([r["latency_ms"].get(k) for r in rs if r["latency_ms"].get(k) is not None]
                                              or [float("nan")])
            cpu = [r.get("server_cpu_cores_used") for r in rs if r.get("server_cpu_cores_used") is not None]
            lk = [r.get("server_lock_hold_utilization") for r in rs if r.get("server_lock_hold_utilization") is not None]
            rr = f"{min(rps):.0f}/{statistics.median(rps):.0f}/{max(rps):.0f}" if rps else "-"
            print(f"   {ep:52s} {c:3d} {rr:>22s} {med('p50'):7.1f} {med('p95'):7.1f} {med('p99'):7.1f} "
                  f"{(statistics.median(cpu) if cpu else float('nan')):5.2f} "
                  f"{(statistics.median(lk) if lk else float('nan')):5.2f}")


if __name__ == "__main__":
    main()
