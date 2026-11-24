# -*- coding: utf-8 -*-
"""
admin_improvements.py - "הערות לשיפור" בלוח הניהול: מה טוב ומה צריך שיפור באפליקציה ובמאגרי המידע כדי להגיע
לדיוק מרבי. כל הערה מחושבת בזמן הבקשה (קבצים, מסד, רישום המקורות, מסמכי המחקר) - לא עצה כללית.
כל הערה: status (good / improve / missing), evidence (מה נמדד), why (למה זה חשוב לדיוק), next_step, source.
סיכומים בלבד - בלי שמות, הודעות, מיקומים או טלפונים.
"""

import json
import os
import re
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MULTILINGUAL_DOC = os.path.join(HERE, "research", "multilingual_audio_eval.md")
CATALOG_DOC = os.path.join(HERE, "research", "DATA_CATALOG_HE.md")
LIGHTS_PATH = os.path.join(HERE, "models", "street_lights_grid.json.gz")      # context_risk.GRID_PATH
SAFE_PLACES_PATH = os.path.join(HERE, "safe_places", "safe_places_il.json")
STATUS_HE = {"good": "✓ טוב", "improve": "⚠ לשפר", "missing": "✗ חסר"}
LAYER_STALE_DAYS = 90          # שכבה סטטית ותיקה מזה - לשקול בנייה מחדש
NEWS_STALE_DAYS = 2            # תמונת מצב חדשות ותיקה מזה כבר לא "עדכנית"
DELIVERY_FAIL_OK = 0.05
RESPONSE_P90_OK_S = 120
UNCONFIRMED_SHARE_OK = 0.2
ACOUSTIC_RECALL_OK = 50.0      # % ב-0.5 התראות שווא לשעה


def note(nid, area, status, title, evidence, why, next_step, source):
    if status not in STATUS_HE:
        raise ValueError(status)
    return {"id": nid, "area": area, "status": status, "status_label": STATUS_HE[status], "title": title,
            "evidence": evidence, "why": why, "next_step": next_step, "source": source}


def _age_days(ts, now):
    return None if ts is None else round((now - ts) / 86400, 1)


def _file_age(path, now):
    return _age_days(os.path.getmtime(path), now) if os.path.exists(path) else None


def _health_notes(h):
    out = []
    a = h["audit_chain"]
    out.append(note("audit_chain", "app", "good" if a["ok"] else "missing", "שלמות יומן הביקורת",
                    f"שרשרת hash {'תקינה' if a['ok'] else 'שבורה באירוע ' + str(a['first_bad_event'])} · {a['events']} אירועים",
                    "יומן שלם הוא הראיה למי קיבל מיקום/הקלטה ומתי.",
                    "אין צורך בפעולה" if a["ok"] else "לשחזר מגיבוי ולחקור: python -m server.admin audit-verify",
                    "server/privacy.py: verify_audit"))
    db = h["database"]
    ok = bool(db.get("schema_up_to_date"))
    out.append(note("schema", "app", "good" if ok else "missing", "גרסת מסד הנתונים",
                    f"סכמה {db.get('schema_version')} מתוך {db.get('schema_expected')} · בדיקת שלמות: {db.get('integrity')}",
                    "מיגרציה חסרה = טבלאות/עמודות שהמנוע מצפה להן לא קיימות.",
                    "אין צורך בפעולה" if ok else "להפעיל מחדש את השרת (מיגרציות רצות באתחול)", "storage.py: connect_sqlite"))
    push = h["push"]
    missing = [n for n, good in (("FCM (השכמת אנדרואיד)", push.get("fcm") == "active"),
                                 ("VAPID subject (שרת הפוש של אפל עלול לדחות)", push.get("vapid_subject_set")),
                                 ("SMS (גיבוי לאיש קשר שלא אישר)", (h.get("sms") or {}).get("configured")),
                                 ("OSRM עצמי (זמני הגעה אמיתיים למתנדבים)", h["routing"]["osrm_configured"])) if not good]
    out.append(note("delivery_channels", "app", "improve" if missing else "good", "ערוצי התראה וניתוב",
                    ("לא מוגדרים: " + "; ".join(missing)) if missing else "כל הערוצים מוגדרים",
                    "כל ערוץ חסר מאריך את הזמן עד שמישהו רואה התראה, או מחליף זמן הגעה אמיתי בהערכה.",
                    "להגדיר לפי DEPLOY_HE.md (FCM, LIFEGUARD_VAPID_SUBJECT, SMS, LIFEGUARD_OSRM_URL)" if missing else
                    "אין צורך בפעולה", "server/lifeguard_server.py: health"))
    return out


def _layer_notes(svc, data, now):
    from server import news_layer as NL, risk_factors_layer as RF, safety_intel as SI
    out = []
    for nid, path, title, rel, why in (
            ("layer_safety", SI.LAYERS_PATH, "שכבות רשמיות: תאונות הולכי רגל + מקלטים", "models/safety_layers.json.gz",
             "מוקדי תאונות ומקלטים הם רכיב בניקוד המסלול ובחירום לאומי."),
            ("layer_lights", LIGHTS_PATH, "רשת עמודי תאורה", "models/street_lights_grid.json.gz",
             "'רחוב חשוך' הוא הרכיב הכבד בניקוד מסלול לילי (safety_intel.W_DARK_SEGMENT)."),
            ("layer_risk_factors", RF.GRID_PATH, "רשת גורמי סיכון (בילוי לילי / תחנות)", "models/risk_factors_grid.json.gz",
             "בלי הרשת - רכיבי nightlife/transit פשוט לא נספרים במסלול.")):
        age = _file_age(path, now)
        if age is None:
            out.append(note(nid, "data", "missing", title, "הקובץ לא קיים בשרת הזה", why,
                            "לבנות את השכבה (פקודת הבנייה בעמודת regenerate בקטלוג הנתונים)", rel))
        else:
            ok = age <= LAYER_STALE_DAYS
            out.append(note(nid, "data", "good" if ok else "improve", title, f"נבנה לפני {age} ימים (זמן שינוי הקובץ)",
                            why, "אין צורך בפעולה כרגע" if ok else f"לבנות מחדש - ותיק מ-{LAYER_STALE_DAYS} ימים", rel))

    src = {s["id"]: s for s in data["sources"]}
    if "streetlights_gis" in src:
        out.append(note("coverage_lights", "data", "improve", "כיסוי נתוני תאורה",
                        f"כיסוי לפי רישום המקורות: {str(src['streetlights_gis'].get('coverage')).replace('**', '')}",
                        "מחוץ לערים המכוסות אין אות 'רחוב חשוך' - המסלול מקבל מהימנות נמוכה ונראה בטוח יותר ממה שהוא.",
                        "להוסיף שכבות תאורה עירוניות נוספות (data.gov.il / GIS עירוני), או עמודים שזוהו ב-Mapillary",
                        "server/data_sources.py: streetlights_gis"))
    if "police_crime_statarea" in src:
        out.append(note("coverage_crime", "data", "improve", "נתוני פשיעה",
                        f"כיסוי לפי רישום המקורות: {str(src['police_crime_statarea'].get('coverage')).replace('**', '')}",
                        "פשיעה ברמת אזור סטטיסטי בלבד - אי אפשר לשייך לרחוב, ולכן היא לא נכנסת לניקוד המסלול.",
                        "לבקש נתון ברמת רחוב או גבולות אזורים סטטיסטיים; עד אז - הקשר בלבד",
                        "server/data_sources.py: police_crime_statarea"))

    news = svc._news()
    if not news.available:
        out.append(note("news_snapshot", "data", "missing", "שכבת חדשות (GDELT/RSS)",
                        "LIFEGUARD_NEWS_FILE לא מוגדר או שהקובץ לא נמצא - השכבה כבויה",
                        f"דיווח תקשורתי מוסיף עד {NL.NEWS_SEGMENT_CAP} נק' למקטע; בלעדיו אירועים טריים לא משתקפים.",
                        "להפיק תמונת מצב (research_tools) ולהגדיר LIFEGUARD_NEWS_FILE, עם רענון יומי",
                        "server/news_layer.py: NewsLayer.from_env"))
    else:
        age = _age_days(NL._ts(news.generated_at), now)
        ok = age is not None and age <= NEWS_STALE_DAYS
        out.append(note("news_snapshot", "data", "good" if ok else "improve", "שכבת חדשות (GDELT/RSS)",
                        f"{len(news.items)} פריטים שמישים · נוצרה לפני {age if age is not None else '?'} ימים",
                        f"משקל דיווח דועך עם הזמן ונעלם אחרי {NL.MAX_AGE_DAYS} ימים - תמונה ישנה = שכבה ריקה בפועל.",
                        "אין צורך בפעולה" if ok else "לרענן את תמונת המצב (משימה מתוזמנת יומית)", "server/news_layer.py"))

    if os.path.exists(SAFE_PLACES_PATH):
        with open(SAFE_PLACES_PATH, encoding="utf-8") as fh:
            sp = json.load(fh)
        places = sp.get("places") or []
        try:
            age = _age_days(time.mktime(time.strptime(sp.get("built_at") or "", "%Y-%m-%d")), now)
        except (ValueError, OverflowError):
            age = None
        types = {}
        for p in places:
            types[str(p.get("type"))] = types.get(str(p.get("type")), 0) + 1
        ok = age is not None and age <= LAYER_STALE_DAYS * 2
        out.append(note("safe_places", "data", "good" if ok else "improve", "רשימת מקומות בטוחים מובנית",
                        f"{len(places)} מקומות ({sum(1 for p in places if p.get('official'))} רשמיים) · "
                        + ", ".join(f"{k}: {v}" for k, v in sorted(types.items())) +
                        f" · כיסוי: {sp.get('coverage')} · נבנתה {sp.get('built_at')}",
                        "הרשימה היא הגיבוי כשאין רשת בזמן SOS; תחנה שנסגרה או עברה = הפניה שגויה.",
                        "לבנות מחדש כל חצי שנה" if ok else "לבנות מחדש את הרשימה (ותיקה)",
                        "safe_places/safe_places_il.json"))
    else:
        out.append(note("safe_places", "data", "missing", "רשימת מקומות בטוחים מובנית", "הקובץ לא קיים בשרת הזה",
                        "בלי רשימה מובנית חיפוש מקום בטוח תלוי לגמרי ברשת.",
                        "הרשימה נטענת באפליקציה; בשרת היא רק לתצוגה כאן", "safe_places/safe_places_il.json"))
    return out


def _source_notes(data):
    out = []
    sources = data["sources"]
    unconf = [s for s in sources if s.get("state") == "not_configured"]
    out.append(note("sources_unconfigured", "data", "improve" if unconf else "good", "מקורות שלא הוגדרו בשרת",
                    (f"{len(unconf)} מתוך {len(sources)}: " + ", ".join(s["id"] for s in unconf)) if unconf
                    else "כל המקורות מוגדרים",
                    "מקור לא מוגדר = שכבה שלא משתתפת (למשל Siren/מראת פיקוד העורף בחירום לאומי, Mapillary לתאורה).",
                    "להגדיר את משתני הסביבה החסרים (key_env/url_env בטבלת מאגרי המידע), רק למקורות שהתנאים שלהם מתירים"
                    if unconf else "אין צורך בפעולה", "server/data_sources.py: configured_state"))
    never = [s["id"] for s in sources if s.get("state") == "configured" and not s.get("last_check")]
    failed = [s["id"] for s in sources if s.get("state") == "unreachable"]
    out.append(note("sources_checked", "data", "missing" if failed else ("improve" if never else "good"),
                    "בדיקה חיה של המקורות",
                    (f"לא זמינים בבדיקה האחרונה: {', '.join(failed)}. " if failed else "")
                    + (f"מוגדרים ולא נבדקו מעולם: {', '.join(never)}" if never else "כל המקורות המוגדרים נבדקו"),
                    "מקור שנפל (למשל Overpass בחיפוש מקום בטוח) נופל בשקט לנתונים המקומיים בלבד.",
                    "להריץ בדיקה: POST /v1/admin/data-sources/check או python -m server.data_sources --check"
                    if failed or never else "אין צורך בפעולה", "server/data_sources.py: check_all"))
    ov = next((s for s in sources if s["id"] == "overpass"), None)
    if ov:
        lc = ov.get("last_check")
        out.append(note("overpass", "data", "improve" if not lc else ("good" if lc["ok"] else "missing"),
                        "Overpass (מקומות פתוחים בזמן אמת)",
                        "לא נבדק מעולם" if not lc else
                        f"{'זמין' if lc['ok'] else 'לא זמין'} בבדיקה מ-{time.strftime('%Y-%m-%d %H:%M', time.localtime(lc['checked_at']))}",
                        "מקומות פתוחים עדכניים מגיעים רק מ-Overpass; בלעדיו - הרשימה המובנית בלבד.",
                        "לבדוק זמינות; לשרת ייצור לשקול מראה Overpass עצמית" if not (lc and lc["ok"]) else "אין צורך בפעולה",
                        "server/data_sources.py: overpass"))
    return out


def _usage_notes(u):
    from server import safety_intel as SI
    out = []
    w30 = u["incidents"]["windows"]["30d"]
    tot, unc = w30["total"], w30["by_activation"].get("unconfirmed", 0)
    if tot:
        share = unc / tot
        bad = share > UNCONFIRMED_SHARE_OK
        out.append(note("unconfirmed_share", "engine", "improve" if bad else "good",
                        "הפעלות 'לא מאושר' (זיהוי אוטומטי בביטחון בינוני)", f"{unc} מתוך {tot} אירועים ב-30 ימים ({share:.0%})",
                        "בהפעלה לא מאושרת לא נשלחים מתנדבים עד אישור - שיעור גבוה = הגלאי לא בטוח בעצמו.",
                        "לבדוק ספי ביטחון מול תוצאות המבחנים ולשפר את הגלאי במכשיר" if bad else "אין צורך בפעולה",
                        "server/privacy.py: activation_for"))
    else:
        out.append(note("unconfirmed_share", "engine", "improve", "הפעלות 'לא מאושר' (זיהוי אוטומטי)",
                        "אין אירועים ב-30 הימים האחרונים - אין נתון", "בלי נתוני שטח אי אפשר לכייל את ספי הביטחון.",
                        "לאסוף נתונים מניסוי שטח (בהסכמה) לפני כיול", "server/privacy.py: CONF_REJECT/CONF_HIGH"))
    dl = u["delivery"]["recipients_30d"]
    if dl["sent"]:
        fail = 1 - dl["rate"]
        bad = fail > DELIVERY_FAIL_OK
        out.append(note("delivery_rate", "app", "improve" if bad else "good", "מסירת התראות (30 ימים)",
                        f"{dl['delivered']}/{dl['sent']} נמסרו ({dl['rate']:.1%}); לא אושרו {fail:.1%}",
                        "נמען שלא קיבל = עזרה שלא מגיעה. 'נמסר' = אישור מהאפליקציה, לא רק שליחה.",
                        "לבדוק מכשירים בלי Push/FCM ולהפעיל גיבוי SMS" if bad else "אין צורך בפעולה",
                        "recipients.delivered_at"))
    else:
        out.append(note("delivery_rate", "app", "improve", "מסירת התראות (30 ימים)", "לא נשלחו התראות ב-30 ימים",
                        "אין נתון אמיתי על אמינות המסירה.", "תרגיל תקופתי (התראת בדיקה) עם משתמשי ניסוי", "recipients"))
    fa = u["response"]["first_acceptance_any"]
    if isinstance(fa, dict) and fa.get("n"):
        bad = fa["p90_s"] > RESPONSE_P90_OK_S
        out.append(note("response_time", "engine", "improve" if bad else "good", "זמן עד קבלה ראשונה",
                        f"חציון {fa['median_s']} שנ' · p90 {fa['p90_s']} שנ' (n={fa['n']})",
                        "הזמן עד שמישהו לוקח אחריות הוא המדד המרכזי של המערכת.",
                        "לגייס מתנדבים באזורים חלשים; לבדוק זמני צלצול/אישור" if bad else "אין צורך בפעולה",
                        "incident_events (response)"))
    else:
        out.append(note("response_time", "engine", "improve", "זמן עד קבלה ראשונה", "אין עדיין אירוע שמישהו קיבל",
                        "בלי מדידה אי אפשר לדעת אם זמני הצלצול והאישור מתאימים.", "למדוד בתרגילי שטח",
                        "incident_events (response)"))
    v = u["volunteers"]
    backlog = v["by_verification"].get("pending", 0)
    qpend = sum(n for k, n in v["qualifications"].items() if k.endswith(":pending"))
    pend = backlog or qpend or v["review_flag"]
    out.append(note("verification_backlog", "app", "improve" if pend else "good", "תור אימות מתנדבים",
                    f"{backlog} בקשות אימות ו-{qpend} הכשרות ממתינות · {v['review_flag']} מסומנים לבדיקה",
                    "מתנדב מאומת עם הכשרה מתאימה מקבל עדיפות בשיבוץ - תור פתוח = שיבוץ פחות מדויק.",
                    "python -m server.admin list --status pending / quals" if pend else "אין צורך בפעולה",
                    "server/volunteer_match.py"))
    rep = u["street_reports"]["by_status"].get("unverified", 0)
    out.append(note("reports_backlog", "data", "improve" if rep else "good", "דיווחי רחוב שלא אומתו",
                    f"{rep} דיווחים לא מאומתים (משקל {SI.STATUS_WEIGHT['unverified']} במקום {SI.STATUS_WEIGHT['verified']})",
                    "דיווח מאומת משפיע על הניקוד במלואו; לא מאומת - חלקית בלבד.",
                    "לבדוק ולאשר: python -m server.admin report-verify <id>" if rep else "אין צורך בפעולה",
                    "server/safety_intel.py: STATUS_WEIGHT"))
    return out


def _model_notes(acoustic):
    out = []
    if acoustic:
        best = []
        for det in acoustic["detectors"]:
            r05 = next((r for r in det["rows"] if r["budget_fa_per_h"] == 0.5), None)
            if r05:
                try:
                    val = float(str(r05["recall"]).rstrip("%"))
                except ValueError:
                    continue
                best.append((val, det["detector"], r05["recall"]))
        best.sort(reverse=True)
        good = bool(best) and best[0][0] >= ACOUSTIC_RECALL_OK
        out.append(note("acoustic", "engine", "good" if good else "improve", "זיהוי מצוקה קולית",
                        "recall ב-0.5 התראות שווא לשעה (FSD50K): " + "; ".join(f"{n}: {r}" for _, n, r in best),
                        "recall נמוך = רוב הצעקות/הבכי לא יפעילו כלום - אסור להסתמך על הזיהוי האוטומטי לבד.",
                        "לאסוף אודיו אמיתי (בהסכמה) ולאמן ראש סיווג; ה-SOS הידני נשאר הנתיב הראשי" if not good else
                        "לאמת בשטח", "research/acoustic_benchmark_fsd50k.md"))
    else:
        out.append(note("acoustic", "engine", "missing", "זיהוי מצוקה קולית", "אין תוצאות מבחן זמינות בשרת הזה",
                        "בלי מבחן אין דרך לדעת כמה זיהויים מוחמצים.", "python -m research_tools.distress_benchmark",
                        "research/acoustic_benchmark_fsd50k.md"))
    if os.path.exists(MULTILINGUAL_DOC):
        with open(MULTILINGUAL_DOC, encoding="utf-8") as fh:
            lines = [ln.strip().strip("-*#|").strip() for ln in fh if "%" in ln][:3]
        out.append(note("multilingual", "engine", "improve", "הערכה רב-לשונית (אודיו)",
                        " · ".join(lines) or "המסמך קיים; לא נמצאו אחוזים לניתוח אוטומטי",
                        "מילות מצוקה בערבית/רוסית/אמהרית ועוד צריכות להיתפס כמו בעברית.",
                        "לקרוא את המסקנות במסמך ולטפל בפערים לפי שפה", "research/multilingual_audio_eval.md"))
    else:
        out.append(note("multilingual", "engine", "missing", "הערכה רב-לשונית (אודיו)",
                        "research/multilingual_audio_eval.md לא קיים", "לא נמדד דיוק הזיהוי בשפות שאינן עברית.",
                        "להריץ את research_tools/multilingual_eval.py ולשמור את הדוח", "research/multilingual_audio_eval.md"))
    out.append(note("weather_context", "engine", "good", "מזג אוויר כהקשר בלבד", "AFFECTS_SCORE=False - מוצג ולא נכלל בניקוד",
                    "אין ראיות טובות לכמה מזג אוויר משנה סיכון ברחוב - עדיף לא להמציא משקל.", "אין צורך בפעולה",
                    "server/weather.py"))
    return out


def _catalog_notes(cat):
    out = []
    if not cat or not cat.get("available"):
        out.append(note("catalog", "data", "missing", "קטלוג הנתונים", "קטלוג הנתונים עוד לא נבנה",
                        "בלי קטלוג אין תמונה של מה בשימוש, מה ישן ומה חסום ברישיון.",
                        "python tools/build_data_catalog.py", "research/data_catalog.csv"))
        return out
    rows = cat["rows"]
    for verdict, title, why in (("stale", "נתונים ישנים", "נתון ישן מציג מצב שכבר השתנה."),
                                ("redundant", "נתונים כפולים", "שני עותקים של אותו מקור עלולים לסתור זה את זה."),
                                ("unclear", "נתונים שמעמדם לא ברור", "לא ברור אם ואיך הם משפיעים על התוצאות."),
                                ("relevant_not_used", "נתונים רלוונטיים שלא בשימוש", "מידע שיכול לשפר דיוק ולא מנוצל.")):
        hit = [r for r in rows if r.get("verdict") == verdict]
        if hit:
            out.append(note("catalog_" + verdict, "data", "improve", f"{title} ({len(hit)})",
                            "; ".join(str(r.get("path")) for r in hit[:6]) + (" …" if len(hit) > 6 else ""), why,
                            " | ".join((r.get("recommendation") or "").strip()[:160] for r in hit[:3] if r.get("recommendation"))
                            or "לעבור על הפריטים בקטלוג", "research/data_catalog.csv (verdict)"))
    flagged = [r for r in rows if re.search(r"\bNC\b|NC-|unknown|unverified|blocks commercial", r.get("licence_flag") or "")]
    if flagged:
        out.append(note("catalog_licence", "data", "improve", f"סימוני רישיון ({len(flagged)})",
                        "; ".join(f"{r.get('path')}: {r.get('licence_flag')}" for r in flagged[:6])
                        + (" …" if len(flagged) > 6 else ""),
                        "נתון שרישיונו לא מאומת או לא-מסחרי עלול לחייב הסרה - ואז הדיוק יורד בבת אחת.",
                        "לאמת רישיונות (GIS ת\"א/חיפה) ולהחליף מקורות NC לפני הפצה מסחרית",
                        "research/data_catalog.csv (licence_flag)"))
    in_use = sum(1 for r in rows if r.get("verdict") == "in_use")
    out.append(note("catalog_doc", "data", "good" if os.path.exists(CATALOG_DOC) else "improve", "קטלוג הנתונים",
                    f"{len(rows)} פריטים מקוטלגים, {in_use} בשימוש" +
                    ("" if os.path.exists(CATALOG_DOC) else " · research/DATA_CATALOG_HE.md חסר"),
                    "קטלוג מלא מאפשר לדעת מה כל נתון משפיע ואיך לבנות אותו מחדש.",
                    "לעדכן אחרי כל בנייה מחדש של שכבה", "research/DATA_CATALOG_HE.md"))
    return out


def build(svc, dash, now, acoustic):
    notes = (_health_notes(dash["health"]) + _layer_notes(svc, dash["data"], now) + _source_notes(dash["data"])
             + _usage_notes(dash["usage"]) + _model_notes(acoustic) + _catalog_notes(dash["data"].get("catalog")))
    order = {"missing": 0, "improve": 1, "good": 2}
    notes.sort(key=lambda n: order[n["status"]])
    return {"notes": notes, "counts": {k: sum(1 for n in notes if n["status"] == k) for k in STATUS_HE},
            "labels": STATUS_HE,
            "note": "כל הערה מחושבת עכשיו מהשרת, מהקבצים ומהמסד (סיכומים בלבד). 'לשפר' = יש ראיה שהדיוק נפגע או "
                    "שחסר נתון כדי לדעת."}
