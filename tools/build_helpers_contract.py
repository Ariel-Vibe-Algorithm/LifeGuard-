# -*- coding: utf-8 -*-
"""
build_helpers_contract.py - קובץ חוזה לבדיקות ה-JVM של אנדרואיד (HelpersContractTest).

1. מריץ את השרת האמיתי (server/lifeguard_server.py + server/helpers.py) על מסד נתונים זמני (תיקייה זמנית -
   לא data/) ושומר את התשובות האמיתיות: תצוגות מתנדב/ת (לפני/אחרי קבלה, מיקום ישן), תצוגת בעלים, התראות
   incident_new (ring), בקשת שיחה (כולל resumed), תפקידי מתנדב/ת.
2. מריץ את הלוגיקה של אפליקציית Windows על אותם נתונים: main.SosWindow.helpers_summary,
   support_ui.support_status - והטקסט/הטון שיצאו הם התוצאה הצפויה באנדרואיד.
3. קבועים: HOTLINES, LIAISON_ACTIONS, ROLE_HE, GUIDANCE (server/helpers.py), ROLE_ROWS/LANG_ROWS (features_ui.py).

    python -m tools.build_helpers_contract

לא פונה לרשת. הפלט: android/app/src/test/resources/helpers_contract.json
"""

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import helpers as HP  # noqa: E402
from server.lifeguard_server import LifeGuardService  # noqa: E402

OUT = os.path.join(ROOT, "android", "app", "src", "test", "resources", "helpers_contract.json")
PW = "StrongPass!2026"
LOC = {"lat": 32.08, "lon": 34.78, "is_precise": True}


def tone(fg):
    import ui_theme as T
    return {T.RED: "ERROR", T.GREEN: "OK", T.YELLOW: "WARN"}.get(fg, "MUTED")


def notif(s, user, iid, ntype="incident_new"):
    rows = s._q("SELECT payload FROM notifications WHERE username=? AND incident_id=? AND type=? ORDER BY id DESC",
                (user, iid, ntype))
    return json.loads(rows[0]["payload"]) if rows else None


def scenario(s):
    v = {}
    for u in ("dana", "talker", "talker2", "liaison", "newbie"):
        s.register(u, PW, display_name={"dana": "דנה כהן"}.get(u))
    s._x("UPDATE users SET created_at=?", (time.time() - 10 * 86400,))
    s._x("UPDATE users SET created_at=? WHERE username='newbie'", (time.time(),))
    for u in ("talker", "talker2", "newbie"):
        s.set_volunteer(u, True, 5)
        s.set_volunteer_roles(u, ["remote_support"], ["he"])
    s.set_volunteer("liaison", True, 5)
    v["roles_liaison_unverified"] = s.set_volunteer_roles("liaison", ["authority_liaison", "on_scene"], ["he", "ar"])
    v["roles_newbie"] = s.volunteer_roles("newbie")
    v["roles_none"] = s.volunteer_roles("dana")
    s.request_verification("liaison", "אימות זהות ידני", "123")
    s.admin_verify("liaison", True)
    v["roles_liaison_verified"] = s.volunteer_roles("liaison")

    # ---- בקשת שיחה (לא חירום)
    sup = s.create_support_request("dana", "קשה לי הערב", "he")
    v["support_created"] = sup
    sid = sup["id"]
    offered = sorted(r["username"] for r in s._q("SELECT username FROM helpers WHERE incident_id=?", (sid,)))
    first = offered[0]
    v["support_offer_notification"] = notif(s, first, sid)
    v["support_helper_before_accept"] = s.get_incident(first, sid)
    v["support_owner_notified"] = s.get_incident("dana", sid)
    v["support_helper_accepted"] = s.helper_respond(first, sid, "accept")
    s.add_message(first, sid, "אני כאן איתך")
    s.add_message("dana", sid, "תודה")
    v["support_helper_chat"] = s.get_incident(first, sid)
    v["support_owner_accepted"] = s.get_incident("dana", sid)
    v["support_resumed"] = s.create_support_request("dana", "עוד משהו", "he")
    s.helper_respond(first, sid, "done")
    v["support_owner_done"] = s.get_incident("dana", sid)
    v["support_owner_closed"] = s.update_incident("dana", sid, {"status": "resolved"})

    # ---- הזעקת רשויות בתוך אירוע חירום (מצוקה שקטה)
    inc = s.create_incident("dana", "e1", mode="silent", location=LOC)
    iid = inc["id"]
    v["owner_request_liaison"] = s.request_helper("dana", iid, "authority_liaison", "לא יכולה לדבר")
    v["liaison_offer_notification"] = notif(s, "liaison", iid)
    v["liaison_before_accept"] = s.get_incident("liaison", iid)
    v["liaison_accepted"] = s.helper_respond("liaison", iid, "accept")
    v["liaison_after_action"] = s.helper_respond("liaison", iid, "called_police")
    v["owner_liaison_accepted"] = s.get_incident("dana", iid)
    v["owner_request_talk"] = s.request_helper("dana", iid, "remote_support")
    s._x("UPDATE incident_locations SET received_at=? WHERE incident_id=?", (time.time() - 900, iid))
    v["liaison_stale_location"] = s.get_incident("liaison", iid)
    v["inbox_liaison"] = s.inbox("liaison")
    return v


def summary_cases(views):
    import main
    import support_ui
    hs = main.SosWindow.helpers_summary
    owner = views["owner_liaison_accepted"]["helpers"]
    talk = views["owner_request_talk"]["helpers"]
    notified = [h for h in talk if h["kind"] == "remote_support"]
    cases = [
        ("empty_nothing_requested", [], []),
        ("empty_requested_liaison", [], ["authority_liaison"]),
        ("empty_requested_both", [], ["authority_liaison", "remote_support"]),
        ("liaison_notified", views["owner_request_liaison"]["helpers"], ["authority_liaison"]),
        ("liaison_accepted_with_action", owner, ["authority_liaison"]),
        ("liaison_accepted_talk_notified", talk, ["authority_liaison", "remote_support"]),
        ("talk_notified_only", notified, ["remote_support"]),
        ("talk_requested_none_available", owner, ["authority_liaison", "remote_support"]),
        ("support_done", views["support_owner_done"]["helpers"], ["remote_support"]),
        ("support_accepted", views["support_owner_accepted"]["helpers"], []),
    ]
    out = []
    for cid, helpers, requested in cases:
        r = hs(helpers, set(requested))
        out.append({"id": cid, "helpers": helpers, "requested": requested, "text": r["text"], "tone": tone(r["fg"])})
    sup = []
    for key in ("support_created", "support_owner_notified", "support_owner_accepted", "support_owner_done",
                "support_owner_closed"):
        text, fg = support_ui.support_status(views[key])
        sup.append({"view": key, "text": text, "tone": tone(fg)})
    no_helpers = dict(views["support_created"], helpers=[])
    text, fg = support_ui.support_status(no_helpers)
    sup.append({"view": "inline:no_helpers", "inline": no_helpers, "text": text, "tone": tone(fg)})
    return out, sup


def main():
    with tempfile.TemporaryDirectory() as tmp:
        s = LifeGuardService(os.path.join(tmp, "contract.db"))
        try:
            views = scenario(s)
        finally:
            s.close()
    summaries, supports = summary_cases(views)
    from features_ui import VolunteerTrustSection as VT
    out = {
        "generated_by": "tools/build_helpers_contract.py (real LifeGuardService on a temp DB + Windows UI logic)",
        "constants": {
            "hotlines": HP.HOTLINES, "hotlines_note": HP.HOTLINES_NOTE,
            "liaison_actions": [[k, lbl] for k, lbl in HP.LIAISON_ACTIONS.items()],
            "role_he": HP.ROLE_HE, "vol_roles": list(HP.VOL_ROLES), "langs": list(HP.LANGS),
            "guidance": HP.GUIDANCE,
            "role_rows": [list(r) for r in VT.ROLE_ROWS], "lang_rows": [list(r) for r in VT.LANG_ROWS],
        },
        "views": views,
        "helpers_summary": summaries,
        "support_status": supports,
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    print(f"wrote {OUT}: {len(views)} server views, {len(summaries)} summary cases, {len(supports)} support cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
