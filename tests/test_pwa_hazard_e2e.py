# -*- coding: utf-8 -*-
"""
PWA - התראות סכנה מ-AI (server/web/hazard.js) בדפדפן Chrome אמיתי מול שרת LifeGuard אמיתי.

tests/web_e2e/run_hazard_e2e.mjs (puppeteer-core): A (עם B כאיש קשר מאושר) ו-B כשני "טלפונים".
זיהויים מוזרקים דרך test hook (לא דרך המיקרופון), פרט לשלב אחד שמריץ את מסלול הלכידה האמיתי על המיקרופון המדומה של Chrome.
נבדק: אין הורדת מודל בטעינה ראשונה; הגדרות (ברירות מחדל, שמירה אחרי טעינה מחדש); תוכן החלון; SOS רק אחרי אישור
(דרך זרימת ה-SOS של ה-PWA); אין חלון בזמן SOS פעיל; קיבוץ / הסלמה / הגבלת קצב / שקט; אנשי קשר - הצלחה מול השרת
(sent_to + אישור קבלה מהאפליקציה של B) ושגיאה אמיתית כשאין חיבור; חיוג - tel: אחרי אישור; התראת שווא + סיבה ביומן
המקומי; מקום בטוח - כנה (אין חיפוש כזה ב-PWA); חלונות כבויים / מתחת לחומרה; אנגלית LTR; YAMNet בדפדפן == TFLite;
אפס שגיאות CSP / קונסול.

מה לא נבדק כאן ודורש אייפון אמיתי: חלון ההרשאה למיקרופון ב-Safari, השהיית המיקרופון ברקע / במסך נעול, ביצועי
המודל במכשיר, ו-Web Notification (ב-iOS הדף מושהה ברקע ולכן לא יכול להציג אותה).
דילוג (skip) אם אין node / Chrome / node_modules.
"""
import json
import os
import secrets
import shutil
import subprocess
import threading

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
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "pwa_hazard.db"), ack_timeout_seconds=120, tick_seconds=0.2)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd
    httpd.shutdown()
    httpd.service.close()


def test_pwa_hazard_alerts_in_real_chrome(live_server):
    node = _prereqs()
    url, httpd = live_server
    svc = httpd.service
    run = secrets.token_hex(3)
    users = {"A": {"username": f"hzowner_{run}", "display": "אורית בדיקה"},
             "B": {"username": f"hzmom_{run}", "display": "אמא בדיקה"}}
    for u in users.values():
        svc.register(u["username"], PW, u["display"])
    a, b = users["A"]["username"], users["B"]["username"]
    svc.respond_invitation(b, svc.invite(a, b)["id"], True)          # B is A's accepted contact

    env = dict(os.environ, CHROME_PATH=CHROME, LG_HAZARD_USERS=json.dumps(users, ensure_ascii=False), LG_E2E_PW=PW)
    proc = subprocess.run([node, "run_hazard_e2e.mjs", url], cwd=E2E_DIR, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600)
    out = proc.stdout or ""
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("E2E_SUMMARY ")), None)
    assert line, f"no summary from node script\nstdout:\n{out[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
    summary = json.loads(line[len("E2E_SUMMARY "):])
    report = "\n".join(f"{'PASS' if s['ok'] else ('WARN' if not s.get('required', True) else 'FAIL')} {s['name']}: "
                       f"{s.get('error') or s.get('info') or ''}" for s in summary["steps"])
    print(report)
    failed = [s for s in summary["steps"] if not s["ok"] and s.get("required", True)]
    assert proc.returncode == 0 and summary["ok"] and not failed, report
    assert not summary["page_errors"], summary["page_errors"]
    passed = " | ".join(s["name"] for s in summary["steps"] if s["ok"])
    for r in ("first load downloads no model", "settings: defaults", "SOS from the alert only after confirmation",
              "no popup during an active SOS", "grouping / escalation", "alert contacts: confirm", "alert contacts offline",
              "call emergency", "false alarm with a reason", "find safe place", "English (LTR)", "in-browser YAMNet",
              "foreground listening"):
        assert r in passed, f"required flow did not run: {r}"
    # server side: the contacts message went out once, as an unverified AI detection
    notes = svc._q("SELECT payload FROM notifications WHERE username=? AND type='info'", (b,))
    texts = [json.loads(n["payload"])["text"] for n in notes]
    assert any("לא אישור לסכנה" in t for t in texts), texts
