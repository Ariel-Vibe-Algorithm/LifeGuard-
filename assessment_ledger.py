# -*- coding: utf-8 -*-
"""
assessment_ledger.py - פנקס הערכות ותוצאות, ודו"ח טעויות ("חוקר הטעויות", שלב 9 בתוכנית).

למה: בלי רישום של "מה המערכת אמרה" מול "מה קרה בפועל" אי אפשר לדעת אם מקור ראיה עוזר או מזיק,
אי אפשר להשוות גרסאות מודל, ואי אפשר ללמוד. risk_feedback כבר לומד ספירות; כאן נשמרת ההערכה
עצמה, כדי שאפשר יהיה לחקור כל טעות בדיעבד.

מה נשמר (record): רמה, ניקוד, מקורות ראיה, גרסאות מודלים, איכות קלט, אירועי צליל (קטגוריה/ציון/סף),
ביטחון. **לא נשמר:** אודיו, embeddings, מיקום, טקסט התמלול או המילים שזוהו (רק כמה), נימוקים בטקסט חופשי.
התוכן מוצפן במנוחה (DPAPI, כמו ציר הזמן של האירוע), כפוף למדיניות השמירה, ונמחק עם האירוע.

תווית (מה קרה באמת) - **רק ממקור בלתי תלוי במודל**, ונגזרת בזמן הדו"ח מהאירוע עצמו:
- דירוג המשתמש/ת אחרי האירוע ("אירוע אמת" / "הרגשתי בסכנה" = אמת; "התראת שווא" = שווא) - תווית חזקה.
- ביטול בסיסמה / במילת ביטול בזמן ההערכה או הספירה - תווית **חלשה** ("בוטל"): אנשים מבטלים גם
  כשהסכנה חלפה. מוצגת בנפרד ולא משמשת ללמידה.
- ביטוי כפייה לא מתויג כ"בוטל" (האירוע ממשיך כמעקב שקט) - כדי שלא יילמד כהתראת שווא.
- אירוע שאומת בשרת (מגיב/ה בשטח): **אין עדיין מקור כזה** - הדו"ח אומר זאת במפורש.
המודל עצמו לעולם לא קובע את התווית.
"""

import math
import time
import uuid

import incident_store as S

SCHEMA = 1
POSITIVE_RATINGS = ("true_event", "felt_danger")
NEGATIVE_RATINGS = ("false_alarm",)
WEAK_NEGATIVE_OUTCOMES = ("cancelled_during_countdown", "cancelled_during_assessment")
ESCALATED = ("MEDIUM", "HIGH")
MIN_FOR_ESTIMATE = 5           # פחות מזה - מציגים ספירות, לא אחוזים


def _r(x, nd=4):
    try:
        return None if x is None else round(float(x), nd)
    except (TypeError, ValueError):
        return None


def summarize(result, trigger=None):
    """הערכת risk_module -> רשומה מצומצמת לפנקס (רשימה לבנה של שדות; בלי מידע מזהה או אודיו)."""
    result = result or {}
    audio = result.get("audio_intel") or {}
    ev = [{"category": e.get("category"), "severity_prior": e.get("severity_prior"), "score": _r(e.get("score")),
           "threshold": _r(e.get("threshold")), "margin": _r(e.get("margin")),
           "near_threshold": bool(e.get("near_threshold")), "detection_confidence": _r(e.get("detection_confidence")),
           "fa_per_hour_at_score": _r(e.get("fa_per_hour_at_score")), "model": e.get("model")}
          for e in (audio.get("events") or [])]
    d = audio.get("distress") or result.get("acoustic_ai") or {}
    conf = result.get("confidence") or {}
    ctx = result.get("context") or {}
    kws = result.get("keywords_found") or []
    sensors = sorted({s.get("signal") for s in (result.get("sensor_signals") or [])
                      if isinstance(s, dict) and s.get("signal")})
    return {
        "schema": SCHEMA,
        "trigger": trigger,
        "level": result.get("level"),
        "score": result.get("score"),
        "automatic_sos_eligible": bool(result.get("automatic_sos_eligible")),
        "independent_sources": list(result.get("independent_sources") or []),
        "confidence": {"value": conf.get("value"), "sources": list(conf.get("sources") or [])},
        "assessment_kind": result.get("assessment_kind"),
        "models": dict(result.get("models") or {}),
        "audio": {
            "quality": (audio.get("quality") or {}).get("status") if audio else None,
            "events": ev,
            "suspected": [s.get("category") for s in (audio.get("suspected_unvalidated") or [])],
            "anomaly": None if not audio.get("anomaly") else {"unusual": bool(audio["anomaly"].get("unusual")),
                                                              "score": _r(audio["anomaly"].get("score")),
                                                              "model": audio["anomaly"].get("model")},
            "distress": None if not d else {"detected": bool(d.get("detected")), "score": _r(d.get("score")),
                                            "probability": _r(d.get("probability"))},
        },
        "noise_rms": _r(result.get("noise_level")),
        "keywords": {"count": len(kws), "personal": any("זוהה בקול שלך" in k for k in kws)},
        "sensors": sensors,
        "context": {"phase": ctx.get("phase"), "lighting": ctx.get("lighting"), "score": ctx.get("score")},
    }


def record(owner, incident_id, result, trigger=None, ts=None):
    """נקרא מהמנוע אחרי כל הערכה. מחזיר מזהה רשומה. כשל כאן לעולם לא עוצר אירוע (המנוע עוטף)."""
    rec = summarize(result, trigger)
    rid = f"asm-{uuid.uuid4().hex[:16]}"
    with S._LOCK:
        con = S._conn()
        with con:
            con.execute("INSERT INTO assessments(id, owner, incident_id, ts, level, record) VALUES (?,?,?,?,?,?)",
                        (rid, S._key(owner), incident_id, ts or result.get("timestamp") or time.time(),
                         str(rec.get("level") or "UNKNOWN"), S._enc(rec)))
    return rid


def label_for(incident):
    """האירוע (מ-incident_store) -> (label, strength, source). label: 1 / 0 / None."""
    if not incident:
        return None, None, "האירוע נמחק או לא נשמר"
    rating = incident.get("rating")
    if rating in POSITIVE_RATINGS:
        return 1, "strong", f"דירוג המשתמש/ת: {rating}"
    if rating in NEGATIVE_RATINGS:
        return 0, "strong", "דירוג המשתמש/ת: התראת שווא"
    if incident.get("outcome") in WEAK_NEGATIVE_OUTCOMES:
        return 0, "weak", f"בוטל ({incident['outcome']}) - תווית חלשה, לא ללמידה"
    return None, None, "אין תווית (לא דורג)"


def entries(owner, since=None, limit=5000):
    """רשומות הפנקס של המשתמש/ת בלבד, עם התווית שנגזרה מהאירוע."""
    q = "SELECT a.*, i.rating AS i_rating, i.outcome AS i_outcome, i.id AS i_id FROM assessments a " \
        "LEFT JOIN incidents i ON i.id = a.incident_id AND i.owner = a.owner WHERE a.owner=?"
    args = [S._key(owner)]
    if since is not None:
        q += " AND a.ts >= ?"
        args.append(float(since))
    q += " ORDER BY a.ts DESC LIMIT ?"
    args.append(int(limit))
    with S._LOCK:
        rows = S._conn().execute(q, args).fetchall()
    out = []
    for r in rows:
        inc = None if r["i_id"] is None else {"rating": r["i_rating"], "outcome": r["i_outcome"]}
        y, strength, why = label_for(inc)
        rec = S._dec_json(r["record"])
        out.append({"id": r["id"], "incident_id": r["incident_id"], "ts": r["ts"], "level": r["level"],
                    "record": rec if isinstance(rec, dict) else {}, "label": y, "label_strength": strength,
                    "label_source": why})
    return out


def wilson(k, n, z=1.96):
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def classify_error(e):
    """רשומה מתויגת -> סוג תוצאה. "החמצה" = ההערכה לא הסלימה אירוע אמיתי (ה-SOS הידני עדיין נשלח)."""
    y, level = e["label"], e["level"]
    quality = ((e["record"].get("audio") or {}).get("quality"))
    if y is None:
        return "unlabeled"
    if y == 1:
        if level == "HIGH":
            return "true_high"
        if level == "MEDIUM":
            return "true_medium"
        return "miss_no_input" if (level == "UNKNOWN" or quality == "unavailable") else "miss"
    return "false_alarm" if level in ESCALATED else "true_low"


ERROR_HE = {"true_high": "אמת, הוסלם ל-HIGH", "true_medium": "אמת, חלון אימות (MEDIUM)",
            "miss": "החמצה: אמת אבל ההערכה LOW", "miss_no_input": "החמצה בגלל קלט חסר (מיקרופון לא זמין)",
            "false_alarm": "התראת שווא: הוסלם אבל לא היה אירוע", "true_low": "נכון: LOW ולא היה אירוע",
            "unlabeled": "ללא תווית"}


def _sources_of(e):
    rec = e["record"]
    srcs = set(rec.get("confidence", {}).get("sources") or [])
    for ev in (rec.get("audio") or {}).get("events") or []:
        srcs.add(f"audio:{ev.get('category')}")
    if ((rec.get("audio") or {}).get("anomaly") or {}).get("unusual"):
        srcs.add("audio:unusual")        # מידע בלבד בזמן ריצה - כאן נמדד אם הוא בכלל מנבא משהו
    return srcs


def report(owner, include_weak=False, since=None):
    """דו"ח טעויות. ברירת מחדל: רק תוויות חזקות (דירוג). include_weak=True מוסיף ביטולים כשווא חלש."""
    rows = entries(owner, since=since)
    used = [e for e in rows if e["label"] is not None and (include_weak or e["label_strength"] == "strong")]
    used_ids = {e["id"] for e in used}
    kinds = {}
    for e in rows:
        k = classify_error(e) if e["id"] in used_ids or e["label"] is None else "weak_" + classify_error(e)
        kinds[k] = kinds.get(k, 0) + 1

    def table(key_fn):
        acc = {}
        for e in used:
            for k in key_fn(e):
                a = acc.setdefault(k, {"fired": 0, "true": 0, "false": 0})
                a["fired"] += 1
                a["true" if e["label"] == 1 else "false"] += 1
        out = {}
        for k, a in sorted(acc.items()):
            lo, hi = wilson(a["true"], a["fired"])
            out[k] = dict(a, precision=None if a["fired"] < MIN_FOR_ESTIMATE else a["true"] / a["fired"],
                          ci95=None if a["fired"] < MIN_FOR_ESTIMATE else (lo, hi))
        return out

    by_source = table(_sources_of)
    by_model = table(lambda e: [f"{k}={v}" for k, v in sorted((e["record"].get("models") or {}).items()) if v])
    by_level = table(lambda e: [e["level"]])
    errors = [{"id": e["id"], "ts": e["ts"], "kind": classify_error(e), "level": e["level"],
               "sources": sorted(_sources_of(e)), "label_source": e["label_source"],
               "near_threshold": [ev["category"] for ev in (e["record"].get("audio") or {}).get("events") or []
                                  if ev.get("near_threshold")]}
              for e in used if classify_error(e) in ("false_alarm", "miss", "miss_no_input")]
    try:
        import hazard_alerts
        hazard = hazard_alerts.feedback_summary(owner)
    except Exception:  # noqa: BLE001
        hazard = {}
    return {"hazard_alerts": hazard, "total": len(rows), "labeled_used": len(used), "include_weak": include_weak,
            "outcomes": kinds, "by_source": by_source, "by_model": by_model, "by_level": by_level,
            "errors": errors,
            "limits": ["תוויות מגיעות רק מדירוג המשתמש/ת (ובבקשה - גם מביטול, כתווית חלשה). אין עדיין אימות "
                       "אירועים בשרת, ולכן אין תווית חיצונית לאירוע.",
                       f"פחות מ-{MIN_FOR_ESTIMATE} מקרים למקור - מוצגות ספירות בלבד, בלי אחוז.",
                       "אירועים שלא דורגו לא נכנסים לחישוב - ייתכן הטיה (מדרגים בעיקר שווא או בעיקר אמת).",
                       "רק הערכות שנפתחו בטריגר (כפתור/קול/חיישן) - אין כאן מדידה של אירועים שלא הפעילו טריגר."]}


def format_report(rep):
    """דו"ח טקסט בעברית (למסך / לייצוא)."""
    lines = [f"פנקס הערכות: {rep['total']} הערכות, {rep['labeled_used']} עם תווית שנכנסה לחישוב"
             + (" (כולל ביטולים כתווית חלשה)" if rep["include_weak"] else " (דירוגים בלבד)")]
    lines.append("")
    lines.append("תוצאות:")
    for k, n in sorted(rep["outcomes"].items(), key=lambda kv: -kv[1]):
        base = k[5:] if k.startswith("weak_") else k
        lines.append(f"  {ERROR_HE.get(base, base)}{' [תווית חלשה - לא בחישוב]' if k.startswith('weak_') else ''}: {n}")

    def tbl(title, t):
        lines.append("")
        lines.append(title)
        if not t:
            lines.append("  (אין עדיין נתונים מתויגים)")
        for k, a in t.items():
            p = "" if a["precision"] is None else \
                f" - דיוק {a['precision']:.0%} (95%: {a['ci95'][0]:.0%}–{a['ci95'][1]:.0%})"
            lines.append(f"  {k}: הופעל {a['fired']}, אמת {a['true']}, שווא {a['false']}{p}")
    tbl("לפי מקור ראיה (כשהמקור הופעל - כמה פעמים היה אירוע אמיתי):", rep["by_source"])
    tbl("לפי רמה:", rep["by_level"])
    tbl("לפי גרסת מודל:", rep["by_model"])
    lines.append("")
    lines.append(f"טעויות לחקירה ({len(rep['errors'])}):")
    for e in rep["errors"][:30]:
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(e["ts"]))
        near = f"; קרוב לסף: {', '.join(e['near_threshold'])}" if e["near_threshold"] else ""
        lines.append(f"  {when} - {ERROR_HE.get(e['kind'], e['kind'])} ({e['level']}); מקורות: "
                     f"{', '.join(e['sources']) or 'אין'}{near}")
    hz = rep.get("hazard_alerts") or {}
    if hz:
        lines.append("")
        lines.append("התראות סכנה מ-AI (משוב משתמש/ת - לא מאומת, לא משנה ספים אוטומטית):")
        for cat, c in sorted(hz.items()):
            reasons = ", ".join(f"{k} {v}" for k, v in c["reasons"].items())
            lines.append(f"  {cat}: {c['alerts']} אירועים ({c['shown']} הוצגו) · התראת שווא {c['false_alarm']}"
                         + (f" ({reasons})" if reasons else "") + f" · פעולה ננקטה {c['actions']}")
    lines.append("")
    lines.append("מגבלות:")
    lines.extend(f"  - {x}" for x in rep["limits"])
    return "\n".join(lines)
