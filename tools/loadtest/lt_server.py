# -*- coding: utf-8 -*-
"""
lt_server.py - runs the REAL LifeGuard server (server/lifeguard_server.create_server) on 127.0.0.1:<random port>
with a temporary DB, plus harness-only instrumentation routes under /v1/_lt/* (registered in THIS process only;
project source files are not modified):

  POST /v1/_lt/seed   bulk-create users/sessions/contacts/volunteers directly in SQL (fast setup)
  GET  /v1/_lt/stats  tick timings, per-method timings, lock wait/hold, threads, RSS, handles, DB size
  POST /v1/_lt/sql    read-only SELECT for verifying invariants
  POST /v1/_lt/call   call an allow-listed service method in-process
  POST /v1/_lt/profile {on: bool, target: "tick"|"requests"} -> cProfile text when turned off
  POST /v1/_lt/quit
"""
import argparse
import cProfile
import io
import json
import os
import pstats
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from server import lifeguard_server as LS  # noqa: E402

PW = "LoadTest!2026"

# indexes proposed in research/load_and_security_report_he.md (only applied to the TEMP db when --indexes)
PROPOSED_INDEXES = """
CREATE INDEX IF NOT EXISTS lt_idx_loc_incident ON incident_locations(incident_id, id);
CREATE INDEX IF NOT EXISTS lt_idx_events_incident ON incident_events(incident_id, id);
CREATE INDEX IF NOT EXISTS lt_idx_msgs_incident ON messages(incident_id, sender, created_at);
CREATE INDEX IF NOT EXISTS lt_idx_recip_user ON recipients(username, role, state);
CREATE INDEX IF NOT EXISTS lt_idx_recip_role_state ON recipients(role, state, notified_at);
CREATE INDEX IF NOT EXISTS lt_idx_inc_status ON incidents(status, created_at);
CREATE INDEX IF NOT EXISTS lt_idx_inv_status ON invitations(status, expires_at);
CREATE INDEX IF NOT EXISTS lt_idx_inv_inviter ON invitations(inviter, status);
CREATE INDEX IF NOT EXISTS lt_idx_obs_user ON street_observations(submitted_by, kind);
CREATE INDEX IF NOT EXISTS lt_idx_escort_status ON escorts(status, deadline);
CREATE INDEX IF NOT EXISTS lt_idx_notif_unreceived ON notifications(username, received_at);
"""


class InstrumentedRLock:
    """Drop-in for threading.RLock: measures wait-to-acquire and hold time of OUTERMOST acquisitions."""

    def __init__(self):
        self._l = threading.RLock()
        self._tls = threading.local()
        self.stats_lock = threading.Lock()
        self.reset()

    def reset(self):
        with getattr(self, "stats_lock", threading.Lock()):
            self.n = 0
            self.wait_total = self.hold_total = 0.0
            self.wait_max = self.hold_max = 0.0
            self.waits = []
            self.holds = []
            self.t_reset = time.perf_counter()

    def acquire(self, blocking=True, timeout=-1):
        depth = getattr(self._tls, "d", 0)
        if depth:
            ok = self._l.acquire(blocking, timeout)
            if ok:
                self._tls.d = depth + 1
            return ok
        t0 = time.perf_counter()
        ok = self._l.acquire(blocking, timeout)
        if ok:
            t1 = time.perf_counter()
            self._tls.d = 1
            self._tls.t = t1
            w = t1 - t0
            with self.stats_lock:
                self.n += 1
                self.wait_total += w
                self.wait_max = max(self.wait_max, w)
                if len(self.waits) < 200000:
                    self.waits.append(w)
        return ok

    def release(self):
        d = self._tls.d - 1
        self._tls.d = d
        if d == 0:
            h = time.perf_counter() - self._tls.t
            with self.stats_lock:
                self.hold_total += h
                self.hold_max = max(self.hold_max, h)
                if len(self.holds) < 200000:
                    self.holds.append(h)
        self._l.release()

    __enter__ = acquire

    def __exit__(self, *a):
        self.release()

    def snapshot(self):
        from lt_common import summarize
        with self.stats_lock:
            el = time.perf_counter() - self.t_reset
            return {"acquisitions": self.n, "elapsed_s": round(el, 2),
                    "hold_utilization": round(self.hold_total / el, 3) if el else None,
                    "wait_total_s": round(self.wait_total, 3), "hold_total_s": round(self.hold_total, 3),
                    "wait": summarize(self.waits), "hold": summarize(self.holds)}


class Timings:
    def __init__(self):
        self.lock = threading.Lock()
        self.d = {}

    def add(self, key, dt):
        with self.lock:
            self.d.setdefault(key, []).append(dt)

    def snapshot(self, reset=False):
        from lt_common import summarize
        with self.lock:
            out = {k: {**summarize(v), "total_s": round(sum(v), 3)} for k, v in self.d.items()}
            if reset:
                self.d = {}
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--tick", type=float, default=1.0)
    ap.add_argument("--ack", type=float, default=120.0)
    ap.add_argument("--ring", type=float, default=45.0)
    ap.add_argument("--instrument-lock", action="store_true")
    ap.add_argument("--synchronous", default="", help="experiment: PRAGMA synchronous=NORMAL|OFF (fix validation)")
    ap.add_argument("--indexes", action="store_true", help="experiment: add the indexes proposed in the report")
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    db = os.path.join(a.dir, "lt_server.db")
    assert os.path.abspath(db).startswith(os.path.abspath(a.dir))
    httpd = LS.create_server("127.0.0.1", 0, db, ack_timeout_seconds=a.ack, tick_seconds=a.tick,
                             contact_ring_seconds=a.ring)
    svc = httpd.service
    if a.synchronous:
        svc._con.execute(f"PRAGMA synchronous={a.synchronous}")
    if a.indexes:
        svc._con.executescript(PROPOSED_INDEXES)
    lock = None
    if a.instrument_lock:
        lock = InstrumentedRLock()
        svc._lock = lock
    T = Timings()
    prof = {"tick": None, "requests": None, "req_stats": None, "req_lock": threading.Lock()}

    # ---- method timing wrappers (instance attributes; class code untouched)
    def wrap(name):
        orig = getattr(svc, name)

        def w(*args, **kw):
            t0 = time.perf_counter()
            try:
                return orig(*args, **kw)
            finally:
                tag = "tick" if threading.current_thread().name == "LG-Escalation" else "req"
                T.add(f"{tag}:{name}", time.perf_counter() - t0)
        setattr(svc, name, w)
    for n in ("_expire_invitations", "_ring_tick", "signal_tick", "run_dispatch_queue", "features_tick",
              "_view_for", "_notify", "create_incident", "_dispatch_next_volunteer", "owner_abuse",
              "update_presence", "add_message", "get_incident", "relationships", "update_location"):
        wrap(n)

    orig_tick = svc.run_escalation_tick

    def tick(*args, **kw):
        p = prof["tick"]
        t0 = time.perf_counter()
        if p:
            p.enable()
        try:
            return orig_tick(*args, **kw)
        finally:
            if p:
                p.disable()
            T.add("TICK_TOTAL", time.perf_counter() - t0)
    svc.run_escalation_tick = tick

    # ---- request profiling (per-request Profile merged into one Stats)
    orig_dispatch = LS.Handler._dispatch

    def dispatch(self, method):
        if prof["requests"] and not self.path.startswith("/v1/_lt/"):
            p = cProfile.Profile()
            p.enable()
            try:
                return orig_dispatch(self, method)
            finally:
                p.disable()
                with prof["req_lock"]:
                    if prof["req_stats"] is None:
                        prof["req_stats"] = pstats.Stats(p)
                    else:
                        prof["req_stats"].add(p)
        return orig_dispatch(self, method)
    LS.Handler._dispatch = dispatch

    # ---- harness routes
    salt, pwh = LS._hash_pw(PW)

    @LS.route("POST", "/v1/_lt/seed")
    def _seed(h, query):
        spec = h._body_big()
        now = time.time()
        users = spec["users"]
        tokens = {}
        urows, srows, rrows, vrows, prows = [], [], [], [], []
        for u in users:
            name = u["u"]
            urows.append((name, u.get("display", name.title()), salt, pwh, now - 30 * 86400, "usr_" + uuid.uuid4().hex))
            tok = "lt_" + uuid.uuid4().hex + uuid.uuid4().hex
            tokens[name] = tok
            srows.append((LS._token_hash(tok), name, "loadtest", now, now + 86400))
        for u in users:
            for c in u.get("contacts", []):
                rrows.append((u["u"], c, now - 86400, now - 86400))
            if u.get("vol"):
                vrows.append((u["u"], u["vol"][0], u["vol"][1], now, now,
                              "verified" if u.get("verified") else "unverified", float(u.get("radius", 5))))
            if u.get("presence"):
                prows.append((u["u"], u["presence"][0], u["presence"][1], now))

        def tx(con):
            con.executemany("INSERT INTO users(username, display_name, pw_salt, pw_hash, created_at, user_id) "
                            "VALUES (?,?,?,?,?,?)", urows)
            con.executemany("INSERT INTO sessions VALUES (?,?,?,?,?)", srows)
            con.executemany("INSERT INTO relationships(owner, contact, status, created_at, responded_at) "
                            "VALUES (?,?,'accepted',?,?)", rrows)
            con.executemany("INSERT INTO volunteers(username, available, radius_km, skills, lat, lon, is_precise, "
                            "loc_updated_at, updated_at, verification) VALUES (?,1,?,'[]',?,?,1,?,?,?)",
                            [(r[0], r[6], r[1], r[2], r[3], r[4], r[5]) for r in vrows])
            con.executemany("INSERT INTO presence(username, lat, lon, is_precise, updated_at) VALUES (?,?,?,1,?)",
                            prows)
        svc._tx(tx)
        return {"tokens": tokens, "n": len(users)}

    def _body_big(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
    LS.Handler._body_big = _body_big

    @LS.route("GET", "/v1/_lt/stats")
    def _stats(h, query):
        out = {"timings": T.snapshot(reset=bool(query.get("reset"))), "threads_py": threading.active_count()}
        if lock:
            out["lock"] = lock.snapshot()
            if query.get("reset"):
                lock.reset()
        try:
            import psutil
            p = psutil.Process()
            mi = p.memory_info()
            out.update(rss_mb=round(mi.rss / 2 ** 20, 1), private_mb=round(getattr(mi, "private", 0) / 2 ** 20, 1),
                       threads_os=p.num_threads(), handles=getattr(p, "num_handles", lambda: None)(),
                       cpu_times=p.cpu_times()._asdict())
        except Exception as e:
            out["psutil_error"] = str(e)
        try:
            out["db_mb"] = round((os.path.getsize(db) + (os.path.getsize(db + "-wal") if os.path.exists(db + "-wal")
                                                        else 0)) / 2 ** 20, 2)
        except OSError:
            pass
        out["retry_at_size"] = len(svc._retry_at)
        out["wakeups_size"] = len(svc._wakeups)
        out["push_queue"] = svc._push_q.qsize()
        return out

    @LS.route("POST", "/v1/_lt/sql")
    def _sql(h, query):
        b = h._body_big()
        q = b["q"].strip()
        if not q.lower().startswith(("select", "with", "explain")):
            raise LS.ApiError(400, "ro", "SELECT only")
        return {"rows": svc._q(q, tuple(b.get("args") or ()))}

    ALLOWED = {"run_escalation_tick", "admin_verify", "owner_abuse", "run_dispatch_queue"}

    @LS.route("POST", "/v1/_lt/call")
    def _call(h, query):
        b = h._body_big()
        if b["name"] not in ALLOWED:
            raise LS.ApiError(400, "nope", "not allowed")
        return {"result": getattr(svc, b["name"])(**(b.get("kw") or {}))}

    @LS.route("POST", "/v1/_lt/profile")
    def _profile(h, query):
        b = h._body_big()
        tgt = b.get("target", "tick")
        if b.get("on"):
            if tgt == "tick":
                prof["tick"] = cProfile.Profile()
            else:
                prof["req_stats"] = None
                prof["requests"] = True
            return {"ok": True}
        s = io.StringIO()
        if tgt == "tick":
            p, prof["tick"] = prof["tick"], None
            st = pstats.Stats(p, stream=s) if p and p.getstats() else None
        else:
            prof["requests"] = None
            st = prof["req_stats"]
            if st:
                st.stream = s
        if st:
            st.sort_stats(b.get("sort", "cumulative")).print_stats(b.get("lines", 45))
        return {"text": s.getvalue()}

    @LS.route("POST", "/v1/_lt/quit")
    def _quit(h, query):
        threading.Thread(target=lambda: (time.sleep(0.2), httpd.shutdown()), daemon=True).start()
        return {"ok": True}

    # harness routes must win over generic patterns -> move them to the front
    lt = [r for r in LS.ROUTES if r[1].pattern.startswith("^/v1/_lt/")]
    LS.ROUTES[:] = lt + [r for r in LS.ROUTES if r not in lt]

    print(f"PORT {httpd.server_address[1]}", flush=True)
    try:
        httpd.serve_forever()
    finally:
        try:
            svc.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
