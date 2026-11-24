# -*- coding: utf-8 -*-
"""
אפליקציית הנייד (PWA, server/web) - בדיקת קצה-לקצה בדפדפן Chrome אמיתי מול שרת LifeGuard אמיתי.

השרת עולה כאן בתהליך (HTTP + SQLite + thread ההסלמה + worker ה-Web Push האמיתי), וסקריפט Node
(tests/web_e2e/run_e2e.mjs, puppeteer-core) מפעיל שני מופעי Chrome headless = שני טלפונים: הרשמה,
מעגל ביטחון, SOS עם GPS מדומה, קבלת קריאה, ליווי הביתה, מידע רפואי ועוד - דרך הממשק, ובדיקה מול ה-API.

דילוג (skip) אם אין node / Chrome / node_modules (התקנה: cd tests/web_e2e && npm install).
LG_E2E_SKIP_OVERDUE=1 מדלג על בדיקת "הליווי הסתיים" (~70 שניות).
"""
import json
import os
import shutil
import subprocess
import threading

import pytest

from server.lifeguard_server import create_server

HERE = os.path.dirname(os.path.abspath(__file__))
E2E_DIR = os.path.join(HERE, "web_e2e")
CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def _prereqs():
    node = shutil.which("node")
    if not node:
        pytest.skip("node לא מותקן")
    if not os.path.isfile(CHROME):
        pytest.skip(f"Chrome לא נמצא ב-{CHROME}")
    if not os.path.isdir(os.path.join(E2E_DIR, "node_modules", "puppeteer-core")):
        pytest.skip("חסר puppeteer-core: cd tests/web_e2e && npm install")
    return node


@pytest.fixture
def live_server(tmp_path):
    # escort grace 150s: the overdue prompt (at 60s) is answered long before the server would escalate
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "pwa.db"), ack_timeout_seconds=60, tick_seconds=0.2,
                          escort_grace_seconds=150)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd
    httpd.shutdown()
    httpd.service.close()


def test_pwa_end_to_end_in_real_chrome(live_server):
    node = _prereqs()
    url, httpd = live_server
    env = dict(os.environ, CHROME_PATH=CHROME)
    proc = subprocess.run([node, "run_e2e.mjs", url], cwd=E2E_DIR, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600)
    out = proc.stdout or ""
    summary_line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("E2E_SUMMARY ")), None)
    assert summary_line, f"no summary from node script\nstdout:\n{out[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
    summary = json.loads(summary_line[len("E2E_SUMMARY "):])
    failed = [s for s in summary["steps"] if not s["ok"] and s.get("required", True)]
    report = "\n".join(f"{'PASS' if s['ok'] else 'FAIL'} {s['name']}: {s.get('error') or s.get('info') or ''}"
                       for s in summary["steps"])
    print(report)
    print("web push:", json.dumps(summary.get("push"), ensure_ascii=False))
    assert proc.returncode == 0 and summary["ok"] and not failed, report
    assert not summary["page_errors"], summary["page_errors"]
    required = {"SOS from the PWA", "B gets the alert", "B accepts", "walk-home escort", "medical info saved",
                "invite by phone shows one-time code", "invite by username"}
    passed = " | ".join(s["name"] for s in summary["steps"] if s["ok"])
    for r in required:
        assert r in passed, f"required flow did not run: {r}"
