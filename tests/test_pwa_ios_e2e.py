# -*- coding: utf-8 -*-
"""
PWA (server/web) כפתרון לאייפון/אייפד - הרצה ב-Chrome אמיתי בהדמיית iPhone מול שרת LifeGuard אמיתי.

tests/web_e2e/run_ios_e2e.mjs (puppeteer-core): מסך 390x844 @3x, מגע, user-agent של Safari ב-iOS, בלי
navigator.vibrate (כמו ב-iOS), ו"אפליקציה ממסך הבית" מודמית ע"י navigator.standalone = true. נבדק:
כרטיס "הוסיפו למסך הבית" (מופיע בלשונית, נעלם במצב standalone), כפתור התראות דחיפה מושבת ומוסבר
כשאין תמיכה (לשונית Safari / iOS ישן), CSS של safe-area, אין גלילה אופקית ב-390px, שדות טקסט >= 16px,
פתיחה בלי אינטרנט מה-cache של ה-service worker עם מספרי החירום, SOS במגע, ואפס שגיאות קונסול/CSP.

חשוב: זה Chrome (Blink) שמתחזה לאייפון - לא Safari/WebKit. התנהגות Safari האמיתית (Add to Home Screen,
Web Push של אפל, הרשאות, נעילת מסך) לא נבדקת כאן ודורשת אייפון אמיתי.
דילוג (skip) אם אין node / Chrome / node_modules (התקנה: cd tests/web_e2e && npm install).
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
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "pwa_ios.db"), ack_timeout_seconds=60, tick_seconds=0.2)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd
    httpd.shutdown()
    httpd.service.close()


def test_pwa_iphone_emulation_in_real_chrome(live_server):
    node = _prereqs()
    url, _httpd = live_server
    env = dict(os.environ, CHROME_PATH=CHROME)
    proc = subprocess.run([node, "run_ios_e2e.mjs", url], cwd=E2E_DIR, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600)
    out = proc.stdout or ""
    summary_line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("E2E_SUMMARY ")), None)
    assert summary_line, f"no summary from node script\nstdout:\n{out[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
    summary = json.loads(summary_line[len("E2E_SUMMARY "):])
    report = "\n".join(f"{'PASS' if s['ok'] else 'FAIL'} {s['name']}: {s.get('error') or s.get('info') or ''}"
                       for s in summary["steps"])
    print(report)
    failed = [s for s in summary["steps"] if not s["ok"] and s.get("required", True)]
    assert proc.returncode == 0 and summary["ok"] and not failed, report
    assert not summary["page_errors"], summary["page_errors"]
    passed = " | ".join(s["name"] for s in summary["steps"] if s["ok"])
    for r in ("static: apple meta tags", "install card on login screen", "push button disabled and explained",
              "no horizontal scroll", "safe-area CSS", "offline: app shell", "home-screen app: no install card",
              "SOS by touch", "old iOS home-screen app", "zero console / CSP errors"):
        assert r in passed, f"required check did not run: {r}"
