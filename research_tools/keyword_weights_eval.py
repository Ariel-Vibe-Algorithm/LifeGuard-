# -*- coding: utf-8 -*-
"""
keyword_weights_eval.py - מה השינוי במילות המצוקה (המלצות 3-5 ב-research/multilingual_audio_eval.md) עושה
להתראות השווא, על אותם תמלולים בדיוק (FLEURS: דיבור רגיל, בלי מצוקה, 5 שפות, מודלי he+en כמו באפליקציה).

משווה: הרשימה הקודמת + 2 נקודות לכל מילה, מול הרשימה החדשה + משקלות (risk_module.keyword_points).
מדד: לכל שעת דיבור רגיל - כמה קטעים קיבלו נקודות ממילים, וכמה הגיעו ל-3+ (MEDIUM מהמילים לבדן).
**לא נמדד:** recall - אין דאטה של "הצילו" צעוק (דורש הקלטות בהסכמה).

שימוש: python -I research_tools/keyword_weights_eval.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
import risk_module  # noqa: E402
import speech_local as S  # noqa: E402
from research_tools import multilingual_eval as M  # noqa: E402

OUT = os.path.join(ROOT, "research", "keyword_weights_v1.md")
OLD_KEYWORDS = ["עזרה", "תעזרו לי", "משטרה", "תפסיק", "תפסיקי", "תעזוב אותי", "תעזבי אותי",
                "אל תיגע בי", "אל תיגעי בי", "תרד ממני", "לא לא לא", "מישהו יעזור",
                "help", "help me", "police", "stop", "let me go", "don't touch me"]
LANGS = ("he_il", "ar_eg", "ru_ru", "en_us", "am_et")
MODELS = ("he", "en")


def old_points(found):
    return min(2 * len(set(found)), 4)


def new_points(found):
    return risk_module.keyword_points(found)[0]


def run():
    rows, words = [], {"old": {}, "new": {}}
    for lang in LANGS:
        recs = [r for r in M._asr_records(lang) if all(("hyp_" + m) in r for m in MODELS)]
        if not recs:
            continue
        hours = sum(r["seconds"] for r in recs) / 3600
        c = {"old_any": 0, "old_med": 0, "new_any": 0, "new_med": 0}
        for r in recs:
            texts = [r.get("hyp_" + m) or "" for m in MODELS]
            for tag, kws, fn in (("old", OLD_KEYWORDS, old_points), ("new", config.DANGEROUS_KEYWORDS, new_points)):
                got = S.find_keywords(texts, kws)
                if not got:
                    continue
                p = fn(got)
                c[f"{tag}_any"] += 1
                c[f"{tag}_med"] += p >= config.RISK_THRESHOLD_MEDIUM
                for w in got:
                    words[tag][w] = words[tag].get(w, 0) + 1
        rows.append({"lang": lang, "clips": len(recs), "hours": hours, **c})
    tot_h = sum(r["hours"] for r in rows)
    tot = {k: sum(r[k] for r in rows) for k in ("old_any", "old_med", "new_any", "new_med")}
    L = ["# משקלות מילות מצוקה - לפני ואחרי", "",
         "נוצר ע\"י `research_tools/keyword_weights_eval.py`. דיבור רגיל (FLEURS, בלי מצוקה) - כל זיהוי הוא התראת שווא.",
         "מודלי ASR: he + en (כמו באפליקציה). **recall לא נמדד** (אין הקלטות \"הצילו\" צעוק).", "",
         "| שפה | שעות | קטעים עם מילה - לפני | אחרי | קטעים ש-MEDIUM מהמילים לבד - לפני | אחרי |",
         "|---|---|---|---|---|---|"]
    for r in rows:
        h = r["hours"]
        L.append(f"| {r['lang']} | {h:.2f} | {r['old_any']} ({r['old_any'] / h:.1f}/ש') | {r['new_any']} "
                 f"({r['new_any'] / h:.1f}/ש') | {r['old_med']} ({r['old_med'] / h:.2f}/ש') | {r['new_med']} "
                 f"({r['new_med'] / h:.2f}/ש') |")
    L.append(f"| **סה\"כ** | {tot_h:.2f} | {tot['old_any']} ({tot['old_any'] / tot_h:.1f}/ש') | {tot['new_any']} "
             f"({tot['new_any'] / tot_h:.1f}/ש') | {tot['old_med']} ({tot['old_med'] / tot_h:.2f}/ש') | "
             f"{tot['new_med']} ({tot['new_med'] / tot_h:.2f}/ש') |")
    L += ["", "**מילים שזוהו (לפני → אחרי):**", ""]
    for w in sorted(set(words["old"]) | set(words["new"]), key=lambda w: -(words["old"].get(w, 0))):
        L.append(f"- {w}: {words['old'].get(w, 0)} → {words['new'].get(w, 0)}")
    L += ["", "**הקשר:** המילים מוערכות רק אחרי טריגר, על חלון קצר - לא בהאזנה רציפה. הקצב לשעה הוא חסם עליון "
              "לשיעור ההערכות שבהן מילה שגויה מוסיפה נקודות."]
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    with open(OUT.replace(".md", ".json"), "w", encoding="utf-8") as fh:
        json.dump({"rows": rows, "total": tot, "hours": tot_h, "words": words}, fh, ensure_ascii=False, indent=1)
    print("\n".join(L))


if __name__ == "__main__":
    run()
