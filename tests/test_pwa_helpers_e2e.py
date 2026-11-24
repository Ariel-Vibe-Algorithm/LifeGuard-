# -*- coding: utf-8 -*-
"""
PWA (server/web) - מתנדבים שלא חייבים להיות מד"א, בדפדפן Chrome אמיתי מול שרת LifeGuard אמיתי.

שלושה מופעי Chrome headless = שלושה טלפונים (tests/web_e2e/run_helpers_e2e.mjs, puppeteer-core):
- A: "צריך/ה לדבר עם מישהו?" (בקשת שיחה, לא חירום) -> צ'אט + סטטוס + קווי סיוע -> סיום השיחה;
  ואחר כך SOS: "שמישהו יזעיק משטרה בשבילי" + "מישהו לדבר איתו עכשיו" + שחרור מתנדב/ת.
- B: מתנדב/ת שיחת תמיכה מרחוק (בוחר/ת תפקיד בפרופיל) - רואה הנחיות, בלי שם ובלי מיקום, צ'אט רק אחרי קבלה.
- C: מתנדב/ת הזעקת רשויות (מאומת/ת ע"י מנהל/ת) - אחרי קבלה רואה שם + מיקום ומדווח/ת "הזעקתי משטרה".

החשבונות נוצרים כאן דרך השירות (כמו tests/test_helpers.py): "גיל" החשבון (3 ימים לשיחת תמיכה) ואימות
המנהל/ת הם פעולות שרת שאין להן ממשק ב-PWA. כל השאר - דרך הממשק, ונבדק מול ה-API.
דילוג (skip) אם אין node / Chrome / node_modules (התקנה: cd tests/web_e2e && npm install).
"""
import json
import os
import secrets
import shutil
import subprocess
import threading
import time

import pytest

from server.lifeguard_server import create_server

HERE = os.path.dirname(os.path.abspath(__file__))
E2E_DIR = os.path.join(HERE, "web_e2e")
CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PW = "E2e-Pass-2026!"


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
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "pwa_helpers.db"), ack_timeout_seconds=180, tick_seconds=0.2)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd
    httpd.shutdown()
    httpd.service.close()


def test_pwa_helpers_in_real_chrome(live_server):
    node = _prereqs()
    url, httpd = live_server
    svc = httpd.service
    run = secrets.token_hex(3)
    users = {"A": {"username": f"asker_{run}", "display": "אביגיל פונה"},
             "B": {"username": f"talker_{run}", "display": "בני תומך"},
             "C": {"username": f"liaison_{run}", "display": "כרמל מקשרת"}}
    for u in users.values():
        svc.register(u["username"], PW, u["display"])
    # שיחת תמיכה מרחוק: חשבון בן 3 ימים לפחות (helpers.MIN_ACCOUNT_AGE_S)
    svc._x("UPDATE users SET created_at=?", (time.time() - 10 * 86400,))
    # הזעקת רשויות: אימות זהות ידני ע"י מנהל/ת (לא חייב ארגון)
    c = users["C"]["username"]
    svc.request_verification(c, "אימות זהות ידני", "ת.ז נבדקה")
    svc.admin_verify(c, True, "נבדקה תעודה מזהה")

    env = dict(os.environ, CHROME_PATH=CHROME, LG_HELPERS_USERS=json.dumps(users, ensure_ascii=False), LG_E2E_PW=PW)
    proc = subprocess.run([node, "run_helpers_e2e.mjs", url], cwd=E2E_DIR, env=env, capture_output=True,
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
    for r in ("roles B", "roles C", "support request from A", "B accepts and chats", "A ends the conversation",
              "liaison", "remote support inside SOS", "liaison done"):
        assert r in passed, f"required flow did not run: {r}"

    # מצב השרת בסוף - לא רק מה שהממשק הציג
    b, a = users["B"]["username"], users["A"]["username"]
    hist = svc.privacy_history(a)["events"]
    actions = {e["action"] for e in hist}
    assert {"support_requested", "helper_requested", "helper_accepted", "authority_contacted",
            "helper_released"} <= actions, actions
    assert any(e["action"] == "location_access" and e.get("via") == "authority_liaison" for e in hist)
    # B שוחרר/ה מהחירום, ובקשת השיחה הסתיימה: אין לו/לה גישה פעילה לשום אירוע
    assert not svc._q("SELECT 1 FROM helpers h JOIN incidents i ON i.id=h.incident_id WHERE h.username=? "
                      "AND i.status='active' AND h.state IN ('notified','accepted')", (b,))
    assert svc.inbox(b) == []
