# -*- coding: utf-8 -*-
"""
guidance_engine.py
מנוע הנחיות חירום מבוסס-כללים (rule-based), מפורש וניתן לבדיקה. *לא* אבחון רפואי ולא AI.

- התוכן נטען מ-content/emergency_guidance_he.json (קל לעדכון בלי לגעת בקוד).
- כל הנחיה חייבת להפנות למקור מוגדר; validate_content() נכשל אם לא (נבדק בבדיקות).
- select_guidance() היא פונקציה טהורה: בלי רשת ובלי המתנה - ולכן לעולם לא מעכבת SOS.
  היא נקראת רק אחרי שה-SOS כבר פעיל, מתוך הממשק.
- כל כלל שהופעל מתועד ב-rule_trace, כדי שאפשר יהיה להסביר *למה* הוצגה כל הנחיה.
"""

import json
import os
import threading

CONTENT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content",
                            "emergency_guidance_he.json")

_CACHE = {"path": None, "mtime": None, "content": None}
_LOCK = threading.Lock()

CALL_LABELS = {"police": "משטרה", "ambulance": "אמבולנס", "fire": "כבאות"}


class ContentError(Exception):
    pass


def validate_content(content):
    """מחזיר רשימת שגיאות (ריקה = תקין). כל צעד חייב id, text ומקור קיים."""
    errors = []
    sources = content.get("sources", {})
    for sid, src in sources.items():
        if src.get("verification") not in content.get("verification_levels", {}):
            errors.append(f"source {sid}: verification level לא מוכר")
        if not src.get("retrieved"):
            errors.append(f"source {sid}: חסר תאריך")

    def _check_steps(where, steps):
        seen = set()
        for step in steps or []:
            if not step.get("id") or not step.get("text"):
                errors.append(f"{where}: צעד ללא id/טקסט")
            if step.get("id") in seen:
                errors.append(f"{where}: id כפול {step.get('id')}")
            seen.add(step.get("id"))
            if step.get("source") not in sources:
                errors.append(f"{where}/{step.get('id')}: מקור '{step.get('source')}' לא מוגדר")

    ids = set()
    for sc in content.get("scenarios", []):
        if sc.get("id") in ids:
            errors.append(f"scenario id כפול: {sc.get('id')}")
        ids.add(sc.get("id"))
        _check_steps(f"scenario {sc.get('id')}", sc.get("steps"))
        for call in sc.get("call", []):
            if call not in CALL_LABELS:
                errors.append(f"scenario {sc.get('id')}: call לא מוכר {call}")
        for region, variant in (sc.get("variants") or {}).items():
            _check_steps(f"scenario {sc.get('id')}[{region}]", variant.get("steps"))
            _check_steps(f"scenario {sc.get('id')}[{region}]+", variant.get("prepend_steps"))
    for cl in content.get("preparedness", []):
        _check_steps(f"checklist {cl.get('id')}", cl.get("items"))
    return errors


def load_content(path=None):
    """טוען + מאמת + שומר במטמון (נטען מחדש אם הקובץ השתנה). זורק ContentError אם פגום."""
    path = path or CONTENT_PATH
    with _LOCK:
        try:
            mtime = os.path.getmtime(path)
        except OSError as exc:
            raise ContentError(f"קובץ ההנחיות לא נמצא: {path}") from exc
        if _CACHE["path"] == path and _CACHE["mtime"] == mtime:
            return _CACHE["content"]
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = json.load(f)
        except ValueError as exc:
            raise ContentError(f"קובץ ההנחיות פגום: {exc}") from exc
        errors = validate_content(content)
        if errors:
            raise ContentError("קובץ ההנחיות לא תקין: " + "; ".join(errors[:5]))
        _CACHE.update({"path": path, "mtime": mtime, "content": content})
        return content


def list_scenarios(content=None):
    content = content or load_content()
    return [{"id": s["id"], "title": s["title"], "icon": s.get("icon", "")} for s in content["scenarios"]]


def get_checklists(content=None):
    content = content or load_content()
    return content.get("preparedness", [])


def source_info(source_id, content=None):
    content = content or load_content()
    return content["sources"].get(source_id)


# ==================== הכללים ====================
# כל כלל: id, תיאור, ופונקציה שמקבלת (scenario, ctx, result) ומשנה את result. סדר הרצה קבוע.

def _rule_silent_mode(sc, ctx, res):
    if ctx.get("silent_mode"):
        res["steps"] = []
        res["notes"] = []
        res["suppressed"] = True
        return True
    return False


def _rule_regional_variant(sc, ctx, res):
    cc = (ctx.get("country_code") or "").upper()
    variant = (sc.get("variants") or {}).get(cc)
    if not variant:
        return False
    if variant.get("steps"):
        res["steps"] = list(variant["steps"])
    if variant.get("prepend_steps"):
        res["steps"] = list(variant["prepend_steps"]) + res["steps"]
    if variant.get("source_note"):
        res["notes"].append(variant["source_note"])
    return True


def _rule_location_uncertain(sc, ctx, res):
    if ctx.get("location_confidence") in ("verified",):
        return False
    res["steps"].insert(0, {
        "id": "describe_location",
        "text": "המיקום שנשלח משוער או לא זמין - תאר/י למוקד היכן את/ה (רחוב, ציון דרך).",
        "source": "lifeguard_app",
    })
    return True


def _rule_offline(sc, ctx, res):
    if ctx.get("online") is not False:
        return False
    res["notes"].append("אין אינטרנט: התקשר/י בשיחה קולית רגילה. התראות אוטומטיות לא נשלחו ויישלחו רק כשהחיבור יחזור.")
    return True


def _rule_alerts_not_delivered(sc, ctx, res):
    if ctx.get("alerts_delivered") is not False:
        return False
    res["notes"].insert(0, "ההתראה לאנשי הקשר לא נשלחה - אל תסמוך/י עליה. התקשר/י בעצמך.")
    return True


def _rule_meeting_points(sc, ctx, res):
    points = [p.get("name") for p in (ctx.get("meeting_points") or []) if p.get("name")]
    if sc["id"] not in ("lost", "physical_danger", "earthquake", "fire", "flood_weather") or not points:
        return False
    res["steps"].append({"id": "my_meeting_points",
                         "text": "נקודות המפגש שלך: " + " · ".join(points[:3]),
                         "source": "lifeguard_app"})
    return True


def _rule_call_numbers(sc, ctx, res):
    numbers = ctx.get("emergency_numbers") or {}
    calls = []
    for kind in sc.get("call", []):
        number = numbers.get(kind) or numbers.get("general")
        if number:
            calls.append({"kind": kind, "label": CALL_LABELS[kind], "number": str(number)})
    res["calls"] = calls
    return bool(calls)


RULES = [
    ("silent_mode", "במעקב שקט לא מוצגות הנחיות (אסור לחשוף מצב שקט)", _rule_silent_mode),
    ("regional_variant", "גרסה אזורית (למשל הנחיות פיקוד העורף בישראל) גוברת על הכללית", _rule_regional_variant),
    ("location_uncertain", "מיקום לא מאומת -> הנחיה למסור מיקום בעל-פה", _rule_location_uncertain),
    ("offline", "אין אינטרנט -> הבהרה שההתראות לא נשלחו", _rule_offline),
    ("alerts_not_delivered", "ההתראה לא התקבלה אצל אף ספק -> אזהרה בראש", _rule_alerts_not_delivered),
    ("meeting_points", "תרחישי פינוי/התמצאות -> הצגת נקודות המפגש של המשתמש", _rule_meeting_points),
    ("call_numbers", "מספרי חירום מקומיים לפי סוג התרחיש והמדינה", _rule_call_numbers),
]


def select_guidance(scenario_id, context=None, content=None):
    """
    context (הכל אופציונלי): country_code, location_confidence ('verified'/'estimated'/'stale'/
    'unavailable'), online (bool/None), alerts_delivered (bool/None), meeting_points (list),
    emergency_numbers (dict מ-emergency_numbers_module), silent_mode (bool).
    מחזיר dict: scenario, steps (כל אחד עם source_info), notes, calls, rule_trace, disclaimer.
    """
    content = content or load_content()
    ctx = context or {}
    sc = next((s for s in content["scenarios"] if s["id"] == scenario_id), None)
    if sc is None:
        raise KeyError(f"תרחיש לא מוכר: {scenario_id}")
    res = {"scenario": {"id": sc["id"], "title": sc["title"], "icon": sc.get("icon", "")},
           "steps": list(sc.get("steps", [])), "notes": [], "calls": [], "rule_trace": [],
           "suppressed": False, "disclaimer": content.get("disclaimer", ""),
           "content_version": content.get("content_version"),
           "review_status": content.get("review_status")}
    for rule_id, _desc, fn in RULES:
        if fn(sc, ctx, res):
            res["rule_trace"].append(rule_id)
        if res["suppressed"]:
            break
    res["steps"] = [dict(s) for s in res["steps"]]  # לא לשנות את התוכן שבמטמון
    for step in res["steps"]:
        src =content["sources"].get(step.get("source"), {})
        step["source_info"] = {
            "org": src.get("org"), "title": src.get("title"), "url": src.get("url"),
            "retrieved": src.get("retrieved"), "verification": src.get("verification"),
            "verification_label": content.get("verification_levels", {}).get(src.get("verification"), ""),
            "note": src.get("note", ""),
        }
    return res
