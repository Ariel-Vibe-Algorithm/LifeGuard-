# -*- coding: utf-8 -*-
"""
כלי ניהול לשרת LifeGuard - אישור מתנדבים והשעיה. רץ על השרת עצמו, מול מסד הנתונים.

  python -m server.admin list [--status pending]
  python -m server.admin verify <username> [--note "..."]
  python -m server.admin reject <username> --note "סיבה"
  python -m server.admin suspend <username>
  python -m server.admin unsuspend <username>
  python -m server.admin quals [--status pending]          הכשרות שממתינות לאימות
  python -m server.admin qual-verify <username> <qual>     אחרי בדיקה מול הארגון המנפיק
  python -m server.admin qual-reject <username> <qual> --note "סיבה"
  python -m server.admin history <username>                היסטוריית שיבוצים ואמינות
  python -m server.admin report-verify <id> / report-reject <id>   דיווחי רחוב
  python -m server.admin national-on --title "..." --message "..." [--area "תל אביב"]...
  python -m server.admin national-off
  python -m server.admin guardian-verify <username>       הורה/אפוטרופוס אומת (אחרי בדיקה ידנית של זהות וקשר)
  python -m server.admin guardian-unverify <username>
  python -m server.admin audit-verify                     בדיקת שלמות יומן הביקורת (שרשרת hash)
  python -m server.admin set-admin-password --user <שם>   סיסמת כניסה לדף הניהול (/admin/). הסיסמה נקראת
                                                          בלי הד (getpass), או מ-stdin עם --password-stdin
  python -m server.admin remove-admin --user <שם>  /  list-admins

ב-Docker:  docker compose -f deploy/docker-compose.yml exec lifeguard python -m server.admin list --status pending
(יש גם נתיבי HTTP מקבילים תחת /v1/admin עם LIFEGUARD_ADMIN_TOKEN.)
אימות מתנדב = בדיקה ידנית מול הארגון (מד"א/איחוד הצלה...) לפני אישור. אל תאשר/י בלי בדיקה.
"""

import argparse
import getpass
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.lifeguard_server import LifeGuardService  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="LifeGuard admin")
    ap.add_argument("--db", default=os.environ.get("LIFEGUARD_SERVER_DB") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "server_data", "lifeguard_server.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--status", choices=("unverified", "pending", "verified", "rejected"))
    for name in ("verify", "reject", "suspend", "unsuspend"):
        p = sub.add_parser(name)
        p.add_argument("username")
        p.add_argument("--note", default="")
    p = sub.add_parser("quals")
    p.add_argument("--status", choices=("pending", "verified", "rejected"), default="pending")
    for name in ("qual-verify", "qual-reject"):
        p = sub.add_parser(name)
        p.add_argument("username")
        p.add_argument("qual")
        p.add_argument("--note", default="")
    p = sub.add_parser("history")
    p.add_argument("username")
    for name in ("report-verify", "report-reject"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.add_argument("--note", default="")
    p = sub.add_parser("national-on")
    p.add_argument("--title", default="מצב חירום לאומי")
    p.add_argument("--message", default="")
    p.add_argument("--area", action="append", default=[])
    sub.add_parser("national-off")
    for name in ("guardian-verify", "guardian-unverify"):
        p = sub.add_parser(name)
        p.add_argument("username")
    sub.add_parser("audit-verify")
    p = sub.add_parser("set-admin-password", help="יצירה/החלפה של סיסמת מנהל/ת לדף הניהול")
    p.add_argument("--user", required=True)
    p.add_argument("--password-stdin", action="store_true", help="קריאת הסיסמה מהשורה הראשונה ב-stdin")
    p = sub.add_parser("remove-admin")
    p.add_argument("--user", required=True)
    sub.add_parser("list-admins")
    a = ap.parse_args(argv)
    if a.cmd == "set-admin-password":
        return _set_admin_password(a)
    svc = LifeGuardService(a.db)
    try:
        if a.cmd == "list":
            for v in svc.admin_volunteers(a.status):
                req = time.strftime("%Y-%m-%d", time.localtime(v["verification_requested_at"])) \
                    if v["verification_requested_at"] else "-"
                fb = v["feedback"]
                print(f"{v['username']:<20} {v['verification']:<11} org={v['verification_org'] or '-'} "
                      f"ref={v['verification_ref'] or '-'} requested={req} suspended={bool(v['suspended'])} "
                      f"rating={fb['avg_rating']} ({fb['ratings']}) reports={fb['reports']}")
        elif a.cmd == "quals":
            for q in svc.admin_qualifications(a.status):
                print(f"{q['username']:<20} {q['qual']:<14} {q['status']:<9} org={q['org']} ref={q['ref']}")
        elif a.cmd in ("qual-verify", "qual-reject"):
            print(svc.admin_decide_qualification(a.username.lower(), a.qual, a.cmd == "qual-verify", a.note))
        elif a.cmd == "history":
            h = svc.volunteer_history(a.username.lower())
            print(h["stats"], h["reliability"])
            for x in h["assignments"]:
                print(time.strftime("%Y-%m-%d %H:%M", time.localtime(x["at"])), x["emergency_type"], x["state"],
                      x["response"] or "-", x["response_seconds"])
        elif a.cmd in ("report-verify", "report-reject"):
            print(svc.admin_decide_report(a.id, a.cmd == "report-verify", a.note))
        elif a.cmd == "national-on":
            print(svc.admin_national_mode(True, a.title, a.message, a.area))
        elif a.cmd == "national-off":
            print(svc.admin_national_mode(False))
        elif a.cmd in ("guardian-verify", "guardian-unverify"):
            print(svc.admin_guardian_verify(a.username.lower(), a.cmd == "guardian-verify"))
        elif a.cmd == "audit-verify":
            print(svc.verify_audit())
        elif a.cmd == "remove-admin":
            print(svc.admin_remove_account(a.user))
        elif a.cmd == "list-admins":
            for r in svc.admin_list_accounts():
                print(f"{r['username']:<20} updated={time.strftime('%Y-%m-%d %H:%M', time.localtime(r['updated_at']))}")
        elif a.cmd in ("verify", "reject"):
            print(svc.admin_verify(a.username.lower(), a.cmd == "verify", a.note))
        else:
            print(svc.admin_set_suspended(a.username.lower(), a.cmd == "suspend"))
    finally:
        svc.close()


def _weak(pw):
    """אזהרה בלבד (לא חוסם): קצרה, רק ספרות, או מעט תווים שונים."""
    return len(pw) < 12 or pw.isdigit() or len(set(pw)) < 6


def _set_admin_password(a):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")     # קונסולה/צינור בלי עברית לא מפיל את הפקודה
        except (AttributeError, ValueError):
            pass
    if a.password_stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
    else:
        pw = getpass.getpass("סיסמת מנהל/ת חדשה: ")
        if getpass.getpass("שוב, לאימות: ") != pw:
            print("הסיסמאות לא תואמות - לא נשמר.", file=sys.stderr)
            return 2
    svc = LifeGuardService(a.db)
    try:
        res = svc.admin_set_password(a.user, pw)
    except ValueError as e:
        print(f"לא נשמר: {e}", file=sys.stderr)
        return 2
    finally:
        svc.close()
    print(("עודכנה" if res["updated"] else "נוצרה") + f" סיסמת מנהל/ת עבור '{res['username']}' (נשמר hash בלבד; "
          "כניסות קודמות של החשבון נותקו).")
    if _weak(pw):
        print("אזהרה: הסיסמה חלשה (קצרה מ-12 תווים / רק ספרות / מעט תווים שונים). בשרת שחשוף לאינטרנט מומלץ מאוד "
              "סיסמה ארוכה ואקראית - הנעילה אחרי 5 ניסיונות מאטה ניחוש אך לא מונעת אותו לגמרי.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
