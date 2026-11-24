# -*- coding: utf-8 -*-
"""
hotspot_v2_eval.py - מדידה של כלל מוקדי התאונות **כפי שהוא רץ בשרת** (server.safety_intel.hotspot_cells),
מול הכלל הקודם (עשירון עליון של סכום 3x3), על שנים עתידיות.

- לכל שנת מבחן Y: ההיסטוריה = כל השנים לפני Y בלבד (אין דליפה). מבחן = תאונות Y עם עיגון מדויק.
- הכלל הקודם משוחזר ב-research_tools/accident_forecast.app_hotspot (נבדק שהוא זהה לקוד השרת הישן).
- השוואה זוגית באותו שטח: v2 עם תקציב שטח = מספר התאים שהכלל הקודם סימן.
- רווחי סמך: cluster bootstrap על ריבועי 0.01° (~1 ק"מ) של תאונות המבחן.
- קריטריון קבלה (נקבע מראש): ב-2024, v2 תופס יותר מהכלל הקודם באותו שטח, עם גבול תחתון חיובי, וגם בשטח
  התקציב שלו (2.5%) - hit rate לא נמוך מהקודם בשטח שלא גדול ממנו ביותר מ-10%.

שימוש: python -I research_tools/hotspot_v2_eval.py
"""
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from research_tools import accident_forecast as AF  # noqa: E402
from server import safety_intel as SI  # noqa: E402

OUT_REPORT = os.path.join(ROOT, "research", "hotspot_v2.md")
OUT_JSON = os.path.join(ROOT, "research", "hotspot_v2.json")
TEST_YEARS = (2022, 2023, 2024)
N_BOOT = 2000
SEED = 11
CELL_KM2 = 0.1113 * 0.0944        # תא 0.001° בקו רוחב ~32° (ק"מ²) - לתצוגה בלבד


def cells_from(acc):
    cy, cx = AF._app_keys(acc["lat"], acc["lon"])
    cells = {}
    for y, x, sev, night in zip(cy.tolist(), cx.tolist(), acc["severe"].tolist(), acc["night"].tolist()):
        c = cells.setdefault((y, x), [0, 0, 0])
        c[0] += 1
        c[1] += int(sev)
        c[2] += int(night)
    return cells


def considered_cells(cells, block=SI.HOTSPOT_BLOCK):
    return len({(y // block, x // block) for (y, x) in cells}) * block * block


def hits(flagged, test):
    cy, cx = AF._app_keys(test["lat"], test["lon"])
    return np.array([(y, x) in flagged for y, x in zip(cy.tolist(), cx.tolist())], float)


def boot_blocks(test, rng, n=N_BOOT):
    cy, cx = AF._app_keys(test["lat"], test["lon"])
    blk = (cy // 10) * 100_000 + (cx // 10)
    _, inv = np.unique(blk, return_inverse=True)
    nb = inv.max() + 1
    return inv, [rng.integers(0, nb, nb) for _ in range(n)]


def boot_ci(vals, inv, draws):
    """רווח סמך 95% ל-hit rate (או להפרש) בדגימה חוזרת של ריבועים שלמים."""
    sums = np.bincount(inv, weights=vals)
    cnts = np.bincount(inv)
    est = []
    for d in draws:
        c = cnts[d].sum()
        est.append(sums[d].sum() / c if c else np.nan)
    lo, hi = np.nanpercentile(est, [2.5, 97.5])
    return float(lo), float(hi)


def evaluate(acc_precise, acc_all, year, rng):
    hist = AF.take(acc_precise, acc_precise["year"] < year)
    test = AF.take(acc_precise, acc_precise["year"] == year)
    cells = cells_from(hist)
    total = considered_cells(cells)
    fy, fx, thr = AF.app_hotspot(hist["lat"], hist["lon"])
    legacy = set(zip(fy.tolist(), fx.tolist()))
    v2 = SI.hotspot_cells(cells)
    v2_eq = SI.hotspot_cells(cells, frac=len(legacy) / total)
    v2_min2 = SI.hotspot_cells(cells, min_count=2)          # התצורה הראשונה שנבדקה (נכשלה) - נשמרת בדו"ח
    # רגישות: v2 כשההיסטוריה כוללת גם עיגון לא מדויק (כמו הקובץ הישן)
    hist_all = AF.take(acc_all, acc_all["year"] < year)
    v2_allstatus = SI.hotspot_cells(cells_from(hist_all))
    inv, draws = boot_blocks(test, rng)
    h = {k: hits(f, test) for k, f in (("legacy", legacy), ("v2", v2), ("v2_equal_area", v2_eq),
                                       ("v2_all_status_history", v2_allstatus), ("v2_min2_rejected", v2_min2))}
    out = {"year": year, "history_years": [int(hist["year"].min()), int(hist["year"].max())],
           "n_test": int(len(test["lat"])), "n_history": int(len(hist["lat"])), "considered_cells": total,
           "legacy_threshold_3x3": thr, "rules": {}}
    sizes = {"legacy": len(legacy), "v2": len(v2), "v2_equal_area": len(v2_eq),
             "v2_all_status_history": len(v2_allstatus), "v2_min2_rejected": len(v2_min2)}
    for k, v in h.items():
        out["rules"][k] = {"cells": sizes[k], "km2": sizes[k] * CELL_KM2, "area_fraction": sizes[k] / total,
                           "hit_rate": float(v.mean()), "ci95": boot_ci(v, inv, draws)}
    for a in ("v2_equal_area", "v2"):
        d = h[a] - h["legacy"]
        out[f"diff_{a}_minus_legacy"] = {"pp": float(d.mean()), "ci95": boot_ci(d, inv, draws)}
    out["v2_min_count_in_hotspots"] = min((cells[k][0] for k in v2), default=None)
    out["overlap_v2_legacy_cells"] = len(v2 & legacy)
    return out


def run():
    t0 = time.time()
    rng = np.random.default_rng(SEED)
    acc = AF.load_accidents()
    precise = AF.take(acc, np.isin(acc["status"], AF.MAIN_STATUS))
    res = [evaluate(precise, acc, y, rng) for y in TEST_YEARS]
    r24 = next(r for r in res if r["year"] == 2024)
    eq = r24["diff_v2_equal_area_minus_legacy"]
    v2, lg = r24["rules"]["v2"], r24["rules"]["legacy"]
    checks = {"equal_area_gain_every_year": (min(r["diff_v2_equal_area_minus_legacy"]["pp"] for r in res),
                                             all(r["diff_v2_equal_area_minus_legacy"]["ci95"][0] > 0 for r in res)),
              "equal_area_gain_2024": (eq["pp"], eq["ci95"][0] > 0),
              "budget_rule_not_worse_2024": (v2["hit_rate"] - lg["hit_rate"],
                                             v2["hit_rate"] >= lg["hit_rate"] and v2["cells"] <= 1.1 * lg["cells"])}
    out = {"created": time.strftime("%Y-%m-%d"), "rule": SI.HOTSPOT_RULE,
           "params": {"area_fraction": SI.HOTSPOT_AREA_FRACTION, "min_count": SI.HOTSPOT_MIN_COUNT,
                      "block": SI.HOTSPOT_BLOCK},
           "results": res, "checks": {k: {"value": v, "pass": ok} for k, (v, ok) in checks.items()},
           "accepted": all(ok for _, ok in checks.values())}
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    report(out)
    print(f"accepted={out['accepted']} ({time.time() - t0:.0f}s) -> {OUT_REPORT}", flush=True)
    return out


def _p(x):
    return f"{100 * x:.1f}%"


def _ci(c):
    return f"{100 * c[0]:.1f}–{100 * c[1]:.1f}"


def report(out, path=OUT_REPORT):
    names = {"legacy": "כלל קודם (עשירון עליון, סכום 3x3)", "v2": "v2 (תא עצמו, תקציב 2.5%)",
             "v2_equal_area": "v2 באותו שטח כמו הקודם", "v2_all_status_history": "v2 בלי סינון עיגון (רגישות)",
             "v2_min2_rejected": "נדחה: v2 עם מינימום 2 תאונות לתא"}
    L = ["# מוקדי תאונות הולכי רגל - כלל v2 בשרת", "",
         f"נוצר אוטומטית ע\"י `research_tools/hotspot_v2_eval.py` ({out['created']}). מודד את הפונקציה שרצה בשרת "
         "(`server/safety_intel.hotspot_cells`), לא העתק שלה.", "",
         "## הכלל",
         f"- דירוג תאים של 0.001° (~100 מ') לפי מספר התאונות **בתא עצמו** (שובר שוויון: קשות, ואז שכנים).",
         f"- מוקד = התאים העליונים עד {_p(out['params']['area_fraction'])} מהאזור הנבחן (ריבועי ~1 ק\"מ עם תאונה), "
         f"ורק תא עם {out['params']['min_count']}+ תאונות.",
         "- רק תאונות עם עיגון מדויק (STATUS_IGUN=1).",
         "- הניסוח באפליקציה: \"בין המקומות עם הכי הרבה תאונות\" + המספר בפועל - תיעוד עבר, לא תחזית; "
         "היעדר סימון ≠ בטוח.", "",
         "## היסטוריית ההחלטה (בגלוי)",
         "- **תצורה ראשונה (נדחתה):** מינימום 2 תאונות לתא. נכשלה בקריטריון שנקבע מראש: בגלל המינימום התקציב לא מתמלא "
         "(ב-2024 סומנו 1.1% מהשטח במקום 2.5%), כך שההשוואה 'באותו שטח' לא הייתה באותו שטח; ב-2022 וב-2023 הפסידה "
         "לכלל הקודם בבירור. התוצאות שלה מופיעות בשורה 'נדחה'.",
         "- **תצורה שנבחרה:** בלי מינימום - ההמלצה המקורית מניסוי החיזוי (`accident_forecast_v1.md`). אין בה פרמטר "
         "שכוונן על השנים האלה (תקציב השטח נקבע להיות כמו הכלל הקודם). **הסתייגות:** 2024 כבר נצפה לפני הבחירה, "
         "ולכן הוא לא holdout נקי עבורה; לכן הקריטריון דורש ניצחון **בכל** שלוש השנים.", "",
         "## תוצאות (היסטוריה = רק שנים לפני שנת המבחן; תאונות מבחן עם עיגון מדויק)", ""]
    for r in out["results"]:
        L += [f"### {r['year']} (היסטוריה {r['history_years'][0]}–{r['history_years'][1]}, {r['n_test']} תאונות מבחן)", "",
              "| כלל | תאים | קמ\"ר | % מהאזור | נתפסו (95%) |", "|---|---|---|---|---|"]
        for k in ("legacy", "v2", "v2_equal_area", "v2_all_status_history", "v2_min2_rejected"):
            x = r["rules"][k]
            L.append(f"| {names[k]} | {x['cells']} | {x['km2']:.1f} | {_p(x['area_fraction'])} | "
                     f"{_p(x['hit_rate'])} ({_ci(x['ci95'])}) |")
        d, e = r["diff_v2_equal_area_minus_legacy"], r["diff_v2_minus_legacy"]
        L += ["", f"- v2 מול הקודם **באותו שטח:** {100 * d['pp']:+.1f} נקודות אחוז (95%: {100 * d['ci95'][0]:+.1f} עד "
                  f"{100 * d['ci95'][1]:+.1f}).",
              f"- v2 בתקציב שלו מול הקודם: {100 * e['pp']:+.1f} נ\"א (95%: {100 * e['ci95'][0]:+.1f} עד "
              f"{100 * e['ci95'][1]:+.1f}). המינימום בתא מסומן: {r['v2_min_count_in_hotspots']} תאונות; "
              f"חפיפה עם הקודם: {r['overlap_v2_legacy_cells']} תאים.", ""]
    L += ["## קריטריון קבלה (נקבע מראש)", ""]
    for k, c in out["checks"].items():
        L.append(f"- {k}: {100 * c['value']:+.1f} נ\"א - {'✅' if c['pass'] else '❌'}")
    L += ["", f"**החלטה:** {'v2 נכנס לשרת.' if out['accepted'] else 'v2 לא עומד בקריטריון - לא להפעיל.'}", "",
          "## מגבלות",
          "- תאונות עם נפגעים בלבד; בלי נתוני חשיפה (מוקד יכול לשקף פשוט הרבה הולכי רגל).",
          "- כמחצית מהתאונות בשנה נתונה קורות בתאים בלי היסטוריה - לכן שום שכבת מוקדים לא אומרת \"בטוח\".",
          "- ההשוואה בין שנים שונות משתמשת באורך היסטוריה שונה (2–4 שנים)."]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    run()
