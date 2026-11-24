# -*- coding: utf-8 -*-
"""
דף הניהול (server/admin_dashboard.py + server/admin_web): כניסה בשם משתמש+סיסמה, נעילה, תוקף, יציאה,
והלוח עצמו - סיכומים בלבד (בלי שמות/הודעות/מיקומים של משתמשים) וערכי מנוע שזהים לקבועים בקוד.
שרת אמיתי (HTTP + SQLite זמני). הסיסמה כאן היא סיסמת בדיקה בלבד.
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request

import pytest

from server import admin as admin_cli
from server import admin_dashboard as AD
from server import dispatch, helpers, news_layer, privacy, ring, risk_factors_layer, safety_intel
from test_features_e2e import TLV, _c, _connect, _start, _vol

ADMIN = "מנהל"
TEST_PW = "test-only-Admin-pw-7Q!"       # סיסמת בדיקה; הסיסמה האמיתית נקבעת רק בשרת דרך ה-CLI


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEGUARD_ADMIN_TOKEN", raising=False)
    httpd, url = _start(tmp_path, public_url="https://lg.example")
    httpd.service.admin_set_password(ADMIN, TEST_PW)
    yield httpd, url
    httpd.shutdown()
    httpd.service.close()


def _req(url, path, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw), r
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), e
        except ValueError:
            return e.code, raw, e


def _login(url, user=ADMIN, pw=TEST_PW):
    return _req(url, "/v1/admin/login", "POST", {"username": user, "password": pw})


# ---------------------------------------------------------------- כניסה
def test_login_success_and_failure_and_audit(srv):
    httpd, url = srv
    st, body, _ = _login(url, pw="wrong-password")
    assert st == 401 and body["error"] == "bad_credentials"
    st2, body2, _ = _login(url, user="nobody-here", pw="whatever-pw")
    assert st2 == 401 and body2["message"] == body["message"]          # אותה הודעה כללית - לא חושף אם השם קיים
    st, body, _ = _login(url)
    assert st == 200 and body["token"].startswith(AD.ADMIN_TOKEN_PREFIX)
    assert body["expires_at"] - time.time() == pytest.approx(AD.ADMIN_SESSION_TTL_S, abs=30)
    assert body["ttl_s"] == 8 * 3600
    # הטוקן והסיסמה לא נשמרים בטקסט גלוי
    svc = httpd.service
    rows = svc._q("SELECT * FROM admin_sessions")
    assert rows and all(body["token"] not in json.dumps(r) for r in rows)
    acc = svc._q("SELECT * FROM admin_accounts", one=True)
    assert TEST_PW not in json.dumps(acc) and len(acc["pw_hash"]) == 64
    # כל כניסה/כישלון ביומן הביקורת, actor = admin:<שם>
    ev = svc._q("SELECT actor, action, result FROM audit_events WHERE action LIKE 'admin_%' ORDER BY seq")
    assert {"actor": "admin:" + ADMIN, "action": "admin_login", "result": "failed"} in ev
    assert {"actor": "admin:nobody-here", "action": "admin_login", "result": "failed"} in ev
    assert ev[-1] == {"actor": "admin:" + ADMIN, "action": "admin_login", "result": "completed"}
    assert svc.verify_audit()["ok"]


def test_lockout_after_five_failures(srv):
    httpd, url = srv
    for _ in range(AD.ADMIN_MAX_FAILURES):
        assert _login(url, pw="bad-guess-123")[0] == 401
    st, body, _ = _login(url)                                  # גם הסיסמה הנכונה חסומה בזמן הנעילה
    assert st == 429 and body["error"] == "locked"
    assert 0 < body["retry_after_s"] <= AD.ADMIN_FAIL_WINDOW_S
    # שם משתמש אחר מאותה כתובת עדיין לא נעול (הנעילה לפי שם+כתובת)
    assert _login(url, user="other-admin", pw="x" * 10)[0] == 401
    # אחרי שהחלון עובר - אפשר שוב
    httpd.service._x("UPDATE admin_login_failures SET ts=ts-?", (AD.ADMIN_FAIL_WINDOW_S + 1,))
    assert _login(url)[0] == 200


def test_session_expiry_and_logout(srv):
    httpd, url = srv
    tok = _login(url)[1]["token"]
    assert _req(url, "/v1/admin/dashboard", token=tok)[0] == 200
    assert _req(url, "/v1/admin/logout", "POST", {}, token=tok)[0] == 200
    st, body, _ = _req(url, "/v1/admin/dashboard", token=tok)
    assert st == 401 and body["error"] == "admin_session_expired"
    assert any(e["action"] == "admin_logout" for e in httpd.service._q("SELECT action FROM audit_events"))
    tok2 = _login(url)[1]["token"]
    httpd.service._x("UPDATE admin_sessions SET expires_at=?", (time.time() - 1,))
    assert _req(url, "/v1/admin/dashboard", token=tok2)[0] == 401
    # החלפת סיסמה מנתקת את כל הכניסות של החשבון
    tok3 = _login(url)[1]["token"]
    httpd.service.admin_set_password(ADMIN, TEST_PW + "-new")
    assert _req(url, "/v1/admin/dashboard", token=tok3)[0] == 401


def test_dashboard_requires_admin(srv):
    httpd, url = srv
    assert _req(url, "/v1/admin/dashboard")[0] == 404
    user = _c(url, "plainuser")                                  # טוקן של משתמש רגיל לא פותח ניהול
    assert _req(url, "/v1/admin/dashboard", token=user.token)[0] == 404
    assert _req(url, "/v1/admin/dashboard", token="lga_forged-token-value")[0] == 401
    # טוקן דף הניהול לא פותח את נתיבי הניהול האחרים (שמות משתמשים, פעולות) - "לא קיים", גם אם מזויף
    assert _req(url, "/v1/admin/volunteers", token="lga_forged-token-value")[0] == 404


def test_env_admin_token_still_works(srv, monkeypatch):
    httpd, url = srv
    monkeypatch.setenv("LIFEGUARD_ADMIN_TOKEN", "env-admin-token-for-tests")
    assert _req(url, "/v1/admin/dashboard", token="env-admin-token-for-tests")[0] == 200
    assert _req(url, "/v1/admin/volunteers", token="env-admin-token-for-tests")[0] == 200
    assert _req(url, "/v1/admin/audit/verify", token="wrong")[0] == 404
    # וגם כניסת מנהל/ת פותחת את נתיבי הניהול הקיימים
    tok = _login(url)[1]["token"]
    st, body, _ = _req(url, "/v1/admin/session", token=tok)
    assert st == 200 and body["actor"] == "admin:" + ADMIN
    assert _req(url, "/v1/admin/audit/verify", token=tok)[0] == 404      # רק לוח הבקרה, לא פעולות ניהול
    assert _req(url, "/v1/admin/volunteers", token=tok)[0] == 404


# ---------------------------------------------------------------- הלוח: סיכומים בלבד
def test_dashboard_aggregates_without_personal_data(srv):
    httpd, url = srv
    dana = _c(url, "danaprivate")
    mom = _c(url, "momprivate")
    _connect(dana, mom)
    _vol(url, "volprivate", {**TLV, "lat": 32.0858})
    inc = dana.create_incident("c1", location={**TLV, "lat": 32.08531, "lon": 34.78179})
    dana.send_message(inc["id"], "סוד-הודעה-פרטית-במקלט")
    mom.respond(inc["id"], "accept")
    dana.create_incident("c2", mode="silent", location=TLV)
    tok = _login(url)[1]["token"]
    st, d, raw = _req(url, "/v1/admin/dashboard", token=tok)
    assert st == 200
    text = json.dumps(d, ensure_ascii=False)
    for secret in ("danaprivate", "momprivate", "volprivate", "Danaprivate", "Momprivate", "Volprivate",
                   "סוד-הודעה-פרטית", "32.08531", "34.78179", "plainuser"):
        assert secret not in text
    u = d["usage"]
    assert u["users"]["total"] == 3
    assert u["incidents"]["all_time"]["total"] == 2 and u["incidents"]["active_now"] == 2
    assert u["incidents"]["windows"]["24h"]["by_mode"] == {"loud": 1, "silent": 1}
    assert u["incidents"]["all_time"]["by_activation"] == {"manual": 2}
    assert u["circle"]["relationships"] == 1
    assert u["volunteers"]["registered"] == 1 and u["volunteers"]["available_now"] == 1
    r = u["response"]["first_acceptance_any"]
    assert r["n"] == 1 and r["median_s"] >= 0
    assert u["response"]["first_acceptance_trusted"]["n"] == 1
    assert u["response"]["first_acceptance_volunteer"]["value"] is None and u["response"]["first_acceptance_volunteer"]["reason"]
    assert d["privacy_note"] == AD.PRIVACY_NOTE
    h = d["health"]
    assert h["audit_chain"]["ok"] is True
    assert h["database"]["schema_up_to_date"] is True
    assert h["server"]["uptime_s"] >= 0
    # מקורות נתונים: שם משתנה המפתח בלבד - אף ערך
    assert d["data"]["sources"] and all("key_configured" in s for s in d["data"]["sources"])
    assert "catalog" in d["data"]


def test_dashboard_never_leaks_key_values(srv, monkeypatch):
    httpd, url = srv
    monkeypatch.setenv("LIFEGUARD_SIREN_KEY", "pr_super-secret-key-value-123")
    tok = _login(url)[1]["token"]
    d = _req(url, "/v1/admin/dashboard", token=tok)[1]
    assert "super-secret-key-value" not in json.dumps(d)
    siren = next(s for s in d["data"]["sources"] if s["id"] == "siren")
    assert siren["key_env"] == "LIFEGUARD_SIREN_KEY" and siren["key_configured"] is True


def test_catalog_missing_note(monkeypatch, tmp_path):
    out = AD.read_catalog(str(tmp_path / "nope.csv"))
    assert out["available"] is False and out["note"] == "קטלוג הנתונים עוד לא נבנה"


# ---------------------------------------------------------------- איך המנוע עובד = הקבועים בקוד
def test_engine_values_equal_code_constants(srv):
    httpd, url = srv
    tok = _login(url)[1]["token"]
    eng = _req(url, "/v1/admin/dashboard", token=tok)[1]["engine"]
    items = {it["key"]: it["value"] for g in eng["groups"] for it in g["items"]}
    j = lambda v: json.loads(json.dumps(AD._jsonable(v)))  # noqa: E731
    expected = {
        "privacy.CONF_REJECT": privacy.CONF_REJECT, "privacy.CONF_HIGH": privacy.CONF_HIGH,
        "privacy.AUTO_TRIGGERS": privacy.AUTO_TRIGGERS, "privacy.MANUAL_TRIGGERS": privacy.MANUAL_TRIGGERS,
        "ring.RING_SECONDS": ring.RING_SECONDS, "dispatch.FANOUT": dispatch.FANOUT,
        "dispatch.LEVEL_BASE": dispatch.LEVEL_BASE, "dispatch.PREEMPT_MARGIN": dispatch.PREEMPT_MARGIN,
        "helpers.FANOUT": helpers.FANOUT, "helpers.LIAISON_AUTO_AFTER_S": helpers.LIAISON_AUTO_AFTER_S,
        "helpers.MIN_ACCOUNT_AGE_S": helpers.MIN_ACCOUNT_AGE_S,
        "risk_factors_layer.W_NIGHTLIFE": risk_factors_layer.W_NIGHTLIFE,
        "risk_factors_layer.W_TRANSIT": risk_factors_layer.W_TRANSIT, "news_layer.W_NEWS": news_layer.W_NEWS,
        "safety_intel.SAFEST_MAX_RATIO": safety_intel.SAFEST_MAX_RATIO,
        "safety_intel.BALANCED_MIN_PER_POINT": safety_intel.BALANCED_MIN_PER_POINT,
        "safety_intel.W_DARK_SEGMENT": safety_intel.W_DARK_SEGMENT, "safety_intel.W_REPORT": safety_intel.W_REPORT,
        "safety_intel.LEVEL_HIGH_PER_KM": safety_intel.LEVEL_HIGH_PER_KM,
        "service.ack_timeout": httpd.service.ack_timeout, "service.ring_seconds": httpd.service.ring_seconds,
    }
    for k, v in expected.items():
        assert items[k] == j(v), k
    import map_module
    assert items["map_module.OFFICIAL_PREFERRED_M"] == map_module.OFFICIAL_PREFERRED_M
    assert items["map_module.PUBLIC_RADIUS_M"] == map_module.PUBLIC_RADIUS_M
    with open(AD.SAFE_PLACES_PATH, encoding="utf-8") as fh:
        assert items["safe_places.count"] == len(json.load(fh)["places"])


def test_route_level_thresholds_used_by_scoring():
    assert safety_intel._level(safety_intel.LEVEL_HIGH_PER_KM) == "high"
    assert safety_intel._level(safety_intel.LEVEL_MEDIUM_PER_KM) == "medium"
    assert safety_intel._level(safety_intel.LEVEL_MEDIUM_PER_KM - 0.01) == "low"


# ---------------------------------------------------------------- הדף עצמו
def test_admin_page_served_with_csp(srv):
    httpd, url = srv
    st, body, resp = _req(url, "/admin/")
    assert st == 200 and "text/html" in resp.headers["Content-Type"]
    html = body.decode("utf-8")
    assert 'dir="rtl"' in html and "/admin/admin.js" in html and "<script>" not in html and "style=" not in html
    csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert resp.headers["Cache-Control"] == "no-store"
    for f in ("admin.js", "admin.css"):
        assert _req(url, "/admin/" + f)[0] == 200
    assert _req(url, "/admin/../lifeguard_server.py")[0] == 404
    assert _req(url, "/admin/secret.txt")[0] == 404
    js = _req(url, "/admin/admin.js")[1].decode("utf-8")
    assert "sessionStorage" in js and "localStorage" not in js and ".innerHTML" not in js
    with pytest.raises(urllib.error.HTTPError) as e:                       # /admin -> /admin/
        urllib.request.build_opener(_NoRedirect).open(url + "/admin")
    assert e.value.code == 302 and e.value.headers["Location"] == "/admin/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


# ---------------------------------------------------------------- CLI
def test_cli_set_admin_password_from_stdin(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "cli.db")
    monkeypatch.setattr(sys, "stdin", io.StringIO("short\n"))
    assert admin_cli.main(["--db", db, "set-admin-password", "--user", ADMIN, "--password-stdin"]) == 2
    monkeypatch.setattr(sys, "stdin", io.StringIO(TEST_PW + "\n"))
    assert admin_cli.main(["--db", db, "set-admin-password", "--user", ADMIN, "--password-stdin"]) == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO("11112222\n"))         # חלשה - נשמרת עם אזהרה
    assert admin_cli.main(["--db", db, "set-admin-password", "--user", "admin2", "--password-stdin"]) == 0
    assert "חלשה" in capsys.readouterr().err
    from server.lifeguard_server import LifeGuardService
    svc = LifeGuardService(db)
    try:
        assert svc.admin_login(ADMIN, TEST_PW, ip="10.0.0.1")["username"] == ADMIN
        assert TEST_PW not in json.dumps(svc._q("SELECT * FROM admin_accounts"))
    finally:
        svc.close()


# ---------------------------------------------------------------- הערות לשיפור
def test_improvement_notes_section(srv):
    httpd, url = srv
    dana = _c(url, "notesuser")
    dana.create_incident("n1", location={**TLV, "lat": 32.08532, "lon": 34.78177})
    tok = _login(url)[1]["token"]
    d = _req(url, "/v1/admin/dashboard", token=tok)[1]
    im = d["improvements"]
    notes = im["notes"]
    assert len(notes) >= 10
    for n in notes:
        assert n["status"] in ("good", "improve", "missing") and n["status_label"]
        for k in ("id", "area", "title", "evidence", "why", "next_step", "source"):
            assert isinstance(n[k], str) and n[k].strip(), (n["id"], k)
    assert sum(im["counts"].values()) == len(notes)
    ids = {n["id"] for n in notes}
    assert {"audit_chain", "schema", "delivery_rate", "response_time", "unconfirmed_share", "acoustic",
            "sources_unconfigured", "verification_backlog"} <= ids
    by = {n["id"]: n for n in notes}
    assert by["audit_chain"]["status"] == "good"
    # נמדד מהמסד: אירוע אחד ב-30 ימים, 0 'לא מאושר'
    assert "0 מתוך 1" in by["unconfirmed_share"]["evidence"]
    # מקור לא מוגדר מופיע בשמו (בלי ערך מפתח)
    assert "mapillary" in by["sources_unconfigured"]["evidence"]
    text = json.dumps(im, ensure_ascii=False)
    for secret in ("notesuser", "Notesuser", "32.08532", "34.78177"):
        assert secret not in text
    # הסדר: חסר -> לשפר -> טוב
    order = {"missing": 0, "improve": 1, "good": 2}
    assert [order[n["status"]] for n in notes] == sorted(order[n["status"]] for n in notes)


def test_improvement_notes_without_catalog(srv, monkeypatch):
    httpd, url = srv
    from server import admin_improvements as AI
    out = AI._catalog_notes({"available": False})
    assert out[0]["status"] == "missing" and out[0]["evidence"] == "קטלוג הנתונים עוד לא נבנה"
    with pytest.raises(ValueError):
        AI.note("x", "app", "great", "t", "e", "w", "n", "s")
