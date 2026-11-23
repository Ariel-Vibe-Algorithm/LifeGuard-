# -*- coding: utf-8 -*-
"""
lifeguard_agent.py
סוכן רקע של LifeGuard ל-Windows: מקבל התראות חירום מהשרת גם כשהאפליקציה הראשית סגורה,
ומציג אותן כהודעת Windows (Toast). לחיצה על ההודעה פותחת את האפליקציה על האירוע (lifeguard://).

למה תהליך משתמש ולא Windows Service: שירות Windows רץ ב-session 0 בתור SYSTEM, מבודד משולחן העבודה
של המשתמש - הוא *לא יכול* להציג Toast למשתמש המחובר. לכן הסוכן הוא תהליך רגיל של המשתמש (pythonw,
בלי חלון) שמופעל בכניסה ל-Windows דרך HKCU\\...\\Run - בלי הרשאות מנהל.

התנהגות:
- מופע יחיד (mutex Local\\LifeGuard.Agent).
- בלי סשן סוכן (האפליקציה לא התחברה אף פעם במחשב הזה) -> יוצא בשקט.
- כשהאפליקציה הראשית רצה (mutex Local\\LifeGuard.App) - הסוכן לא מתחבר לזרם בכלל; האפליקציה
  מציגה התראות בעצמה. זה כולל מצב "מעקב שקט"/כפייה: האפליקציה רצה ומחזיקה את ה-mutex, ולכן הסוכן
  לא מציג *שום דבר* - אחרת Toast היה חושף את המעקב.
- כשהאפליקציה סגורה: BackendClient עם הטוקן הנפרד של הסוכן, זרם SSE (+long-poll), כל התראה מאושרת
  לשרת (= "נמסר") ומוצגת לפי הכללים ב-_toast_for().
- הסמן (_last_id) נשמר ב-<DATA_DIR>/agent_state.json - הפעלה מחדש לא מקפיצה שוב התראות ישנות.
  בהפעלה ראשונה, ובכל פעם אחרי שהאפליקציה רצה (היא כבר הציגה את מה שהגיע), מתחילים בסמנטיקת
  sync_cursor: רק התראות שלא התקבלו באף מכשיר.
- 401 (הטוקן בוטל/פג) -> מוחקים את הסשן, הודעה אחת "יש להתחבר מחדש", ויוצאים.
- לוג: <DATA_DIR>/agent.log (מוגבל בגודל). לעולם לא נכתבים טוקנים או תוכן התראות - רק סוג ומספר.

הרצה: pythonw lifeguard_agent.py [--data-dir DIR] [--server URL]
(או משתני סביבה LIFEGUARD_AGENT_DATA_DIR / LIFEGUARD_AGENT_SERVER) - לבדיקות בלבד.
"""

import argparse
import hashlib
import logging
import logging.handlers
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import storage  # noqa: E402
import agent_bridge  # noqa: E402
import windows_notify  # noqa: E402
from backend_client import BackendClient, BackendError, BackendUnavailable  # noqa: E402

RELOGIN_TITLE = "LifeGuard: יש להתחבר מחדש באפליקציה כדי לקבל התראות"
RELOGIN_BODY = "קבלת ההתראות ברקע הופסקה. פתח/י את LifeGuard והתחבר/י מחדש."
URGENT_TYPES = {"incident_new", "escort_alarm", "escort_overdue"}

log = logging.getLogger("lifeguard.agent")


def setup_logging(data_dir):
    log.setLevel(logging.INFO)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    try:
        os.makedirs(data_dir, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(os.path.join(data_dir, "agent.log"), maxBytes=256 * 1024,
                                                 backupCount=1, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    except OSError:
        log.addHandler(logging.NullHandler())
    log.propagate = False


def _tag(ident):
    return (ident or "lg")[-16:]


def _toast_for(ntype, p):
    """(title, body, urgent, tag, launch) או None. מינימלי בכוונה - בלי מיקום ובלי מידע רפואי."""
    if ntype == "incident_new":
        iid = p.get("id")
        dist = f" · {p['distance_km']:.1f} ק\"מ ממך" if isinstance(p.get("distance_km"), (int, float)) else ""
        return (f"🚨 {p.get('owner_display') or 'מישהו'} צריך/ה עזרה",
                f"פתח/י את LifeGuard כדי לקבל או לדחות{dist}", True, _tag(iid),
                agent_bridge.open_uri("incident", iid))
    if ntype == "escort_alarm":
        iid = p.get("incident_id")
        return ("🚨 לא אושרה הגעה", p.get("text") or "נפתחה קריאת חירום. פתח/י את LifeGuard",
                True, _tag(iid), agent_bridge.open_uri("incident", iid))
    if ntype == "escort_overdue":
        eid = p.get("escort_id")
        return ("⏰ זמן הליווי הסתיים", "הכל בסדר? פתח/י את LifeGuard ואשר/י הגעה", True, _tag(eid),
                agent_bridge.open_uri("escort", eid))
    if ntype == "relationship_invite":
        return ("📨 הזמנה למעגל הביטחון", p.get("text") or "התקבלה הזמנה חדשה", False,
                _tag(p.get("id") or "invite"), agent_bridge.open_uri())
    if ntype in ("readiness_check", "test"):
        return ("🧪 בדיקת LifeGuard", p.get("text") or "בדיקת קבלת התראות", False, _tag(ntype),
                agent_bridge.open_uri())
    return None


class Agent:
    """הליבה - ניתנת לבדיקה בתוך התהליך: step() אחד = בדיקת מצב אחת. run(stop_event) = לולאה."""

    def __init__(self, data_dir=None, server=None, is_app_running=None, clock=time.monotonic,
                 poll_seconds=3.0, retry_seconds=10.0, exit_if_unprovisioned=True):
        self.data_dir = data_dir or config.DATA_DIR
        self.server = server
        self.is_app_running = is_app_running or agent_bridge.is_app_running
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.retry_seconds = retry_seconds
        self.exit_if_unprovisioned = exit_if_unprovisioned
        self.client = None
        self.username = None
        self.stopped = False
        self.stop_reason = None
        self._token = None
        self._next_try = 0.0
        self._saved_cursor = None
        self._had_session = False
        self._resync_marked = False
        self._lock = threading.Lock()

    # ---------- state (cursor) ----------
    def _base_url(self, session):
        return (self.server or session["base_url"]).rstrip("/")

    def _load_cursor(self, session):
        try:
            st = storage.load_json(agent_bridge.state_path(self.data_dir), {})
        except storage.StorageError:
            return 0
        if (isinstance(st, dict) and st.get("username") == session.get("username")
                and st.get("base_url") == self._base_url(session)):
            try:
                return max(0, int(st.get("last_id") or 0))
            except (TypeError, ValueError):
                return 0
        return 0

    def _save_cursor(self, last_id):
        if self.client is None or last_id == self._saved_cursor:
            return
        try:
            storage.atomic_write_json(agent_bridge.state_path(self.data_dir), {
                "username": self.username, "base_url": self.client.base_url, "last_id": int(last_id),
                "updated_at": time.time()})
            self._saved_cursor = last_id
        except storage.StorageError as e:
            log.error("cursor save failed: %s", type(e).__name__)

    # ---------- streaming ----------
    def _stop_client(self):
        c, self.client = self.client, None
        if c is not None:
            c.stop_stream()
            self._save_cursor_for(c)
            log.info("stream stopped")

    def _save_cursor_for(self, c):
        try:
            storage.atomic_write_json(agent_bridge.state_path(self.data_dir), {
                "username": self.username, "base_url": c.base_url, "last_id": int(c._last_id),
                "updated_at": time.time()})
        except storage.StorageError:
            pass

    def _mark_resync(self):
        """האפליקציה רצה ומציגה התראות בעצמה. כשתיסגר - מתחילים מ"מה שלא התקבל באף מכשיר"
        (sync_cursor) ולא מהסמן הישן, אחרת היינו מקפיצים שוב את מה שהאפליקציה כבר הציגה."""
        if self._resync_marked:
            return
        path = agent_bridge.state_path(self.data_dir)
        try:
            st = storage.load_json(path, None)
            if isinstance(st, dict) and st.get("last_id"):
                st["last_id"] = 0
                storage.atomic_write_json(path, st)
            self._saved_cursor = 0
            self._resync_marked = True
        except storage.StorageError:
            pass

    def _start_client(self, session):
        client = BackendClient(self._base_url(session), token=session["token"], username=session.get("username"))
        me = client.me()  # מאמת את הטוקן לפני הזרם (401 -> התחברות מחדש)
        self.username = me.get("username") or session.get("username")
        client.username = self.username
        client._last_id = self._load_cursor(session)
        self._saved_cursor = client._last_id
        self._resync_marked = False
        self.client, self._token = client, session["token"]
        client.start_stream(lambda row, c=client: self._on_notification(c, row))
        log.info("stream started (cursor=%s)", client._last_id or "sync")

    def _on_notification(self, client, row):
        with self._lock:
            if client is not self.client or self.stopped:
                return
            try:
                self._handle(client, row)
            except Exception as e:  # noqa: BLE001
                log.error("notification %s handling failed: %s", row.get("id"), type(e).__name__)
            self._save_cursor(client._last_id)

    def _handle(self, client, row):
        ntype, p = row.get("type"), row.get("payload") or {}
        log.info("notification id=%s type=%s", row.get("id"), ntype)
        if self.is_app_running():
            return  # האפליקציה נפתחה בינתיים (כולל מעקב שקט) - היא מציגה בעצמה / חייבים לשתוק
        if ntype == "incident_closed":
            iid = p.get("incident_id")
            if iid:
                windows_notify.remove(_tag(iid))
            return
        if ntype == "incident_new" and p.get("owner") == self.username:
            return
        if ntype == "escort_alarm" and not p.get("text"):
            # הליווי *שלי* הסלים. במצב שקט אסור להקפיץ אזעקה על המסך שלי (מי שלידי יראה).
            try:
                inc = client.get_incident(p.get("incident_id"))
            except BackendError:
                return
            if inc.get("mode") == "silent":
                return
        spec = _toast_for(ntype, p)
        if spec is None:
            return
        title, body, urgent, tag, launch = spec
        windows_notify.notify(title, body, urgent=urgent, tag=tag, launch=launch)

    def _unauthorized(self):
        log.warning("session rejected by server (401) - clearing agent session")
        self._stop_client()
        agent_bridge.clear_session(self.data_dir)
        windows_notify.notify(RELOGIN_TITLE, RELOGIN_BODY, urgent=False, tag="lg-relogin",
                              launch=agent_bridge.open_uri())
        self.stopped, self.stop_reason = True, "unauthorized"

    # ---------- main loop ----------
    def step(self):
        """בדיקת מצב אחת. מחזיר False כשהסוכן צריך לצאת."""
        if self.stopped:
            return False
        if self.is_app_running():
            self._stop_client()
            self._mark_resync()
            return True
        session = agent_bridge.load_session(self.data_dir)
        if session is None:
            self._stop_client()
            if not self._had_session and self.exit_if_unprovisioned:
                log.info("agent not provisioned - exiting")
                self.stopped, self.stop_reason = True, "not_provisioned"
                return False
            return True  # התנתקות באפליקציה: ממתינים להתחברות מחדש
        self._had_session = True
        if self.client is not None and session["token"] != self._token:
            log.info("agent session changed - reconnecting")
            self._stop_client()
        if self.client is not None:
            t = self.client._stream_thread
            if t is not None and not t.is_alive():
                # _stream_loop יוצא רק על 401 (או עצירה שלנו) - מאמתים
                try:
                    self.client.me()
                    log.warning("stream thread ended unexpectedly - restarting")
                    self._stop_client()
                except BackendError as e:
                    if e.status == 401:
                        self._unauthorized()
                        return False
                    self._stop_client()
            else:
                self._save_cursor(self.client._last_id)
                return True
        if self.clock() < self._next_try:
            return True
        try:
            self._start_client(session)
        except BackendUnavailable:
            self._next_try = self.clock() + self.retry_seconds
            log.info("server unavailable - retry in %ss", self.retry_seconds)
        except BackendError as e:
            if e.status == 401:
                self._unauthorized()
                return False
            self._next_try = self.clock() + self.retry_seconds
            log.error("connect failed: status=%s code=%s", e.status, e.code)
        return True

    def run(self, stop_event=None):
        stop_event = stop_event or threading.Event()
        log.info("agent started")
        try:
            while not stop_event.is_set():
                try:
                    if not self.step():
                        break
                except Exception as e:  # noqa: BLE001 - הסוכן לא נופל
                    log.error("step failed: %s", type(e).__name__)
                stop_event.wait(self.poll_seconds)
        finally:
            self.shutdown()
            log.info("agent exiting (%s)", self.stop_reason or "stopped")

    def shutdown(self):
        self._stop_client()


def _agent_mutex_name(data_dir):
    """מופע יחיד לכל תיקיית נתונים: ברירת המחדל Local\\LifeGuard.Agent; תיקייה אחרת (בדיקות) - שם נפרד."""
    if os.path.normcase(os.path.abspath(data_dir)) == os.path.normcase(os.path.abspath(config.DATA_DIR)):
        return agent_bridge.AGENT_MUTEX
    return agent_bridge.AGENT_MUTEX + "." + hashlib.sha1(os.path.abspath(data_dir).lower().encode()).hexdigest()[:10]


def main(argv=None):
    ap = argparse.ArgumentParser(description="LifeGuard background agent")
    ap.add_argument("--data-dir", default=os.environ.get("LIFEGUARD_AGENT_DATA_DIR"))
    ap.add_argument("--server", default=os.environ.get("LIFEGUARD_AGENT_SERVER"))
    ap.add_argument("--app-mutex", default=os.environ.get("LIFEGUARD_AGENT_APP_MUTEX", agent_bridge.APP_MUTEX),
                    help=argparse.SUPPRESS)  # לבדיקות בלבד
    args = ap.parse_args(argv)
    data_dir = os.path.abspath(args.data_dir) if args.data_dir else config.DATA_DIR
    mutex_name = _agent_mutex_name(data_dir)
    if args.data_dir:
        config.DATA_DIR = data_dir
    setup_logging(data_dir)
    handle = agent_bridge.acquire_single_instance(mutex_name)
    if handle is None:
        log.info("another agent instance is running - exiting")
        return 0
    app_mutex = args.app_mutex
    agent = Agent(data_dir=data_dir, server=args.server,
                  is_app_running=lambda: agent_bridge.is_app_running(app_mutex))
    agent.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        log.error("agent crashed: %s", type(exc).__name__)
        sys.exit(1)
