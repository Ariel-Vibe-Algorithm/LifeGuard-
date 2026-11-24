# -*- coding: utf-8 -*-
"""הפעלה אוטומטית של השרת המקומי: רק לכתובת מקומית, רק כשהוא לא עונה, וממתינים עד שהוא עונה."""
import local_server as LS


def test_already_running_does_nothing():
    calls = []
    ok, msg = LS.ensure_running("http://127.0.0.1:8765", popen=lambda *a, **k: calls.append(a), up=lambda u: True)
    assert ok and not calls


def test_remote_server_is_never_started_locally():
    calls = []
    ok, msg = LS.ensure_running("https://lifeguard.example.org", popen=lambda *a, **k: calls.append(a),
                                up=lambda u: False)
    assert not ok and not calls and "server_url.txt" in msg


def test_starts_local_server_in_background_and_waits_until_up(monkeypatch):
    calls, state = [], {"n": 0}

    def up(url):
        state["n"] += 1
        return state["n"] > 3          # עונה אחרי כמה בדיקות

    def popen(cmd, **kw):
        calls.append((cmd, kw))
    ok, msg = LS.ensure_running("http://127.0.0.1:8799", popen=popen, up=up, sleep=lambda s: None)
    assert ok and msg == "השרת המקומי הופעל"
    cmd, kw = calls[0]
    assert cmd[1:] == ["-m", "server.lifeguard_server", "--port", "8799"] and kw["cwd"] == LS.HERE


def test_started_but_never_answers_reports_log_path():
    ok, msg = LS.ensure_running("http://localhost:8765", popen=lambda *a, **k: None, up=lambda u: False,
                                sleep=lambda s: None, timeout=0.01)
    assert not ok and "server.log" in msg


def test_real_health_check_against_real_server(tmp_path):
    import threading
    from server.lifeguard_server import create_server
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "x.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert LS.is_up(url)
        assert LS.ensure_running(url) == (True, "השרת פועל")
    finally:
        httpd.shutdown()
        httpd.service.close()
    assert not LS.is_up("http://127.0.0.1:9", timeout=0.5)
