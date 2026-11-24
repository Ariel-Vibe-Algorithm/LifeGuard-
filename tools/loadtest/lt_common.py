# -*- coding: utf-8 -*-
"""
lt_common.py - shared helpers for the LifeGuard load / security harness.

SAFETY: everything here talks ONLY to a server process started by this harness on 127.0.0.1,
with a fresh temporary database (never data/, never the default server_data DB).
"""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PY = sys.executable
PW = "LoadTest!2026"
TLV = (32.0853, 34.7818)


# ------------------------------------------------------------------ stats
def pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(vals, scale=1000.0, unit="ms"):
    """latencies in seconds -> dict in ms (default)."""
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "p50": round(pct(vals, 50) * scale, 1), "p95": round(pct(vals, 95) * scale, 1),
            "p99": round(pct(vals, 99) * scale, 1), "max": round(max(vals) * scale, 1),
            "mean": round(sum(vals) / len(vals) * scale, 1), "unit": unit}


def machine_context(sample_s=1.0):
    out = {"cpu_count": os.cpu_count(), "python": sys.version.split()[0], "platform": sys.platform,
           "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        import psutil
        out["cpu_percent_busy"] = psutil.cpu_percent(interval=sample_s)
        out["cpu_physical"] = psutil.cpu_count(logical=False)
        vm = psutil.virtual_memory()
        out["mem_total_gb"] = round(vm.total / 2 ** 30, 1)
        out["mem_available_gb"] = round(vm.available / 2 ** 30, 1)
        procs = []
        for p in psutil.process_iter(["name", "cpu_times"]):
            try:
                ct = p.info["cpu_times"]
                procs.append((p, ct.user + ct.system if ct else 0))
            except Exception:
                pass
        # top CPU consumers right now (sample)
        for p, _ in procs:
            try:
                p.cpu_percent(None)
            except Exception:
                pass
        time.sleep(0.5)
        top = []
        for p, _ in procs:
            try:
                top.append((p.cpu_percent(None), p.info["name"]))
            except Exception:
                pass
        top.sort(reverse=True)
        out["top_processes_cpu_percent"] = [(n, round(c)) for c, n in top[:5]]
    except ImportError:
        out["psutil"] = "missing"
    return out


# ------------------------------------------------------------------ HTTP client
class Client:
    """Keep-alive JSON client (one per thread). Never follows redirects, never leaves 127.0.0.1."""

    def __init__(self, port, token=None, timeout=60):
        self.port, self.token, self.timeout = port, token, timeout
        self.conn = None

    def _c(self):
        if self.conn is None:
            self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        return self.conn

    def req(self, method, path, body=None, token=None, headers=None, timeout=None):
        tok = token if token is not None else self.token
        h = {"Content-Type": "application/json"}
        if tok:
            h["Authorization"] = "Bearer " + tok
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        for attempt in (0, 1):
            c = self._c()
            if timeout is not None:
                c.timeout = timeout
                if c.sock:
                    c.sock.settimeout(timeout)
            t0 = time.perf_counter()
            try:
                c.request(method, path, body=data, headers=h)
                r = c.getresponse()
                raw = r.read()
                dt = time.perf_counter() - t0
                try:
                    js = json.loads(raw) if raw else None
                except ValueError:
                    js = raw.decode("utf-8", "replace")
                if r.getheader("Connection", "").lower() == "close":
                    self.close()
                return r.status, js, dt
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError,
                    ConnectionAbortedError) as e:
                self.close()
                if attempt:
                    return -1, {"error": type(e).__name__}, time.perf_counter() - t0
            except (TimeoutError, OSError) as e:
                self.close()
                return -2, {"error": type(e).__name__}, time.perf_counter() - t0

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None


# ------------------------------------------------------------------ server process
class ServerProc:
    """Starts tools/loadtest/lt_server.py (LifeGuard create_server on 127.0.0.1:0, temp DB)."""

    def __init__(self, tick=1.0, ack=120.0, ring=45.0, instrument_lock=True, extra_env=None, workdir=None,
                 synchronous="", indexes=False):
        self.dir = workdir or tempfile.mkdtemp(prefix="lg_loadtest_")
        args = [PY, os.path.join(HERE, "lt_server.py"), "--dir", self.dir, "--tick", str(tick),
                "--ack", str(ack), "--ring", str(ring)]
        if instrument_lock:
            args.append("--instrument-lock")
        if synchronous:
            args += ["--synchronous", synchronous]
        if indexes:
            args.append("--indexes")
        env = dict(os.environ)
        env.pop("LIFEGUARD_DATA_KEY", None)
        env.pop("LIFEGUARD_OSRM_URL", None)
        env.pop("LIFEGUARD_BEHIND_PROXY", None)
        env.pop("LIFEGUARD_ADMIN_TOKEN", None)
        for k in list(env):
            if k.startswith("LIFEGUARD_TWILIO"):
                env.pop(k)
        env.update(extra_env or {})
        env["PYTHONIOENCODING"] = "utf-8"
        self.p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, env=env,
                                  text=True, encoding="utf-8", errors="replace")
        line = self.p.stdout.readline()
        if not line.startswith("PORT "):
            raise RuntimeError("server failed: " + line + self.p.stderr.read()[:2000])
        self.port = int(line.split()[1])
        self.pid = self.p.pid
        self._errs = []
        threading.Thread(target=self._drain, daemon=True).start()
        self.admin = Client(self.port)

    def _drain(self):
        for line in self.p.stderr:
            self._errs.append(line.rstrip())

    @property
    def errors(self):
        return list(self._errs)

    def seed(self, spec):
        st, js, _ = Client(self.port).req("POST", "/v1/_lt/seed", spec, timeout=600)
        if st != 200:
            raise RuntimeError(f"seed failed {st} {js}")
        return js

    def stats(self, reset=False):
        st, js, _ = Client(self.port).req("GET", "/v1/_lt/stats" + ("?reset=1" if reset else ""), timeout=120)
        return js

    def sql(self, q, args=()):
        st, js, _ = Client(self.port).req("POST", "/v1/_lt/sql", {"q": q, "args": list(args)}, timeout=120)
        if st != 200:
            raise RuntimeError(js)
        return js["rows"]

    def call(self, name, **kw):
        st, js, _ = Client(self.port).req("POST", "/v1/_lt/call", {"name": name, "kw": kw}, timeout=600)
        if st != 200:
            raise RuntimeError(js)
        return js["result"]

    def stop(self):
        try:
            self.admin.req("POST", "/v1/_lt/quit", {}, timeout=5)
        except Exception:
            pass
        try:
            self.p.wait(10)
        except Exception:
            self.p.kill()


def run_threads(n, fn, *args):
    """runs fn(i, *args) in n threads, returns list of results."""
    out = [None] * n
    ths = []

    def w(i):
        out[i] = fn(i, *args)
    for i in range(n):
        t = threading.Thread(target=w, args=(i,), daemon=True)
        t.start()
        ths.append(t)
    for t in ths:
        t.join()
    return out


def save_result(name, data):
    d = os.path.join(HERE, "results")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + "_" + time.strftime("%Y%m%d_%H%M%S") + ".json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
    return p


# ------------------------------------------------------------------ asyncio mini HTTP/1.1 client (many idle conns)
import asyncio  # noqa: E402


class AConn:
    """One keep-alive connection driven by asyncio (for hundreds/thousands of concurrent long-polls)."""

    def __init__(self, port):
        self.port = port
        self.r = self.w = None

    async def open(self):
        self.r, self.w = await asyncio.open_connection("127.0.0.1", self.port)

    async def req(self, method, path, token=None, body=None, timeout=60):
        if self.w is None:
            await self.open()
        data = json.dumps(body).encode() if body is not None else b""
        head = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {len(data)}\r\n"
                f"Content-Type: application/json\r\n" + (f"Authorization: Bearer {token}\r\n" if token else "")
                + "\r\n").encode()
        self.w.write(head + data)
        await self.w.drain()
        return await asyncio.wait_for(self._read(), timeout)

    async def _read(self):
        status_line = await self.r.readline()
        if not status_line:
            raise ConnectionResetError("closed")
        status = int(status_line.split()[1])
        length, close = 0, False
        while True:
            line = await self.r.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            k, _, v = line.decode("latin-1").partition(":")
            if k.lower() == "content-length":
                length = int(v.strip())
            elif k.lower() == "connection" and v.strip().lower() == "close":
                close = True
        body = await self.r.readexactly(length) if length else b""
        if close:
            await self.close()
        try:
            return status, json.loads(body) if body else None
        except ValueError:
            return status, body

    async def close(self):
        if self.w is not None:
            try:
                self.w.close()
            except Exception:
                pass
        self.r = self.w = None
