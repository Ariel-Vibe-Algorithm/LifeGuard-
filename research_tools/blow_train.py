# -*- coding: utf-8 -*-
"""
blow_train.py - גלאי "מכה / אגרוף" (blow) + כלל "קטטה אפשרית" (מכה + קול מצוקה/כאב בתוך שניות).

נתונים:
- חיובי: FoleySet PunchHit (אפקטים של אגרופים ומכות שהוקלטו לקולנוע - **לא קטטות אמיתיות**). פיצול לפי מעלה.
- שליליים: שאר FoleySet (דפיקות בדלת, טריקות, צעדים, חפצים) במשקל ×3 לדומים, + חלונות FSD50K/SONYC מהמטמון.
- רישוי: קטעי FoleySet בלי רישיון ידוע לכל קטע -> המודל מסומן "מחקר/פיתוח בלבד" עד אימות (ראו הכרטיס).
סף (validation בלבד): מקסימום זיהוי בתנאי שווא ≤ 0.05% מחלונות הרחוב (SONYC val) ו-≤ 0.1% מחלונות הפנים (FSD val).
מבחן: FoleySet test (מעלים אחרים), שווא על FSD50K eval / SONYC test / TAU (טלפונים) / UFRB-ESD.

כלל "קטטה אפשרית" (fight): בתוך רצף של ≤ 6 חלונות (~9 ש'): חלון "כמעט-מכה" (ציון ≥ סף נמוך) **וגם** חלון קול
"כמעט-מצוקה" (ראש המצוקה או צרחה ≥ ספי כלל הרצף). שני רמזים בלתי תלויים -> חומרה גבוהה, אבל עדיין רק התראה.
השווא של הכלל נמדד על רקע אמיתי; **הזיהוי שלו לא נמדד על קטטות אמיתיות** - אין מאגר פתוח כזה.

שימוש: python -I research_tools/blow_train.py extract | train
"""
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from research_tools import hv2_data as D  # noqa: E402
from research_tools import hv2_features as F  # noqa: E402
from research_tools import hv2_train as TR  # noqa: E402

EXTRA = os.path.join(F.OUT, "extra")
OUT_MODEL = os.path.join(ROOT, "models", "blow_v1.npz")
CANDIDATE = os.path.join(F.OUT, "blow_candidate.npz")
FIGHT_MODEL = os.path.join(ROOT, "models", "fight_rule_v1.npz")
FIGHT_BG_MAX, FIGHT_COMPOSE_MIN = 0.001, 0.40     # קבלת כלל הקטטה (נקבע לפני ההרצה)
OUT_MD = os.path.join(ROOT, "research", "blow_fight_v1.md")
STREET_BUDGET, INDOOR_BUDGET = 0.0005, 0.001
HARD = {"door_knock", "impact", "drums_music", "construction", "foley_other", "glass_break"}


def log(m):
    print(m, flush=True)


def extract():
    rows = [r for r in D.load_manifest() if r["source"] in ("foleyset", "ufrb_esd")
            and r["split"] in ("train", "val", "test")]
    os.makedirs(EXTRA, exist_ok=True)
    done = {f[:-5] for f in os.listdir(EXTRA) if f.endswith(".done")}
    rows.sort(key=lambda r: r["id"])
    tasks = []
    for i in range(0, len(rows), 150):
        chunk = rows[i:i + 150]
        key = f"extra_{F._h('|'.join(r['id'] for r in chunk), 10 ** 12)}"
        if key not in done:
            plan = {r["id"]: ["far_street", "indoor_tv"] for r in chunk if r["split"] == "train" and "blow" in r["tags"]}
            tasks.append((key, (chunk, plan)))
    log(f"extra: {len(rows)} קטעים, {len(tasks)} חבילות")
    t0 = time.time()
    with Pool(F.WORKERS, initializer=F._init) as pool:
        jobs = [(k, pool.apply_async(F.task_clips, (p,))) for k, p in tasks]
        for n, (k, j) in enumerate(jobs, 1):
            F._save_shard(os.path.join(EXTRA, k), j.get())
            if n % 10 == 0 or n == len(jobs):
                log(f"  {n}/{len(jobs)} ({time.time() - t0:.0f}s)")


def load_extra():
    d = EXTRA
    arrs = {k: [] for k in ("s1", "cur", "spec", "v1", "zs", "rms")}
    meta = []
    for f in sorted(os.listdir(d)):
        if f.endswith(".npz"):
            z = np.load(os.path.join(d, f), allow_pickle=False)
            for k in arrs:
                arrs[k].append(z[k])
            meta += [json.loads(m) for m in z["meta"]]
    return {k: np.concatenate(v) for k, v in arrs.items()}, meta


def concat(a, b):
    return {k: np.concatenate([a[k], b[k]]) for k in a}


def labels(meta, idx):
    y = np.zeros(len(meta), np.int8)
    hard = np.zeros(len(meta), bool)
    split = []
    env = []
    for i, m in enumerate(meta):
        r = idx.get(m["id"]) or {}
        tags = set(r.get("tags") or [])
        if "blow" in tags:
            y[i] = 1
        elif r.get("split") in ("excluded_ambiguous", "excluded_overlap"):
            y[i] = -1
        hard[i] = bool(tags & HARD)
        split.append(r.get("split"))
        src = r.get("source")
        env.append({"fsd50k": "indoor_mixed", "sonyc_ust": "street"}.get(src, r.get("env", src)) if src != "tau2020_mobile"
                   else "phone_" + r.get("env", ""))
    return y, hard, np.array(split), np.array(env)


def train():
    t0 = time.time()
    idx = TR._manifest_index()
    ex, ex_meta = load_extra()
    tr, tr_meta = F.load_split("train")
    va, va_meta = F.load_split("val")
    te, te_meta = F.load_split("test")
    rng = np.random.default_rng(5)
    y_ex, hard_ex, sp_ex, env_ex = labels(ex_meta, idx)
    # אימון: FoleySet train (כולל מועשר) + דגימה ממטמון האימון הכללי (שליליים)
    m_tr = sp_ex == "train"
    y_tr, hard_tr, _, _ = labels(tr_meta, idx)
    neg_pool = np.flatnonzero(y_tr == 0)
    take = rng.choice(neg_pool, size=min(60_000, len(neg_pool)), replace=False)
    X = np.concatenate([ex["s1"][m_tr], tr["s1"][take]]).astype(np.float32)
    Y = np.concatenate([(y_ex[m_tr] == 1), np.zeros(len(take), bool)]).astype(np.float32)
    H = np.concatenate([hard_ex[m_tr], hard_tr[take]])
    keep = np.concatenate([y_ex[m_tr] >= 0, np.ones(len(take), bool)])
    X, Y, H = X[keep], Y[keep], H[keep]
    h1 = TR.Head().fit(X, Y, np.where(H & (Y == 0), TR.HARD_WEIGHT, 1.0))
    log(f"שלב 1: {int(Y.sum())} חלונות חיוביים, {int((1 - Y).sum())} שליליים ({time.time() - t0:.0f}s)")
    # שלב 2 על המועמדים
    trd = {k: np.concatenate([ex[k][m_tr][keep[:m_tr.sum()]], tr[k][take]]) for k in ("s1", "cur", "spec")}
    l1 = h1.logit(trd["s1"].astype(np.float32))
    t_cand = float(np.quantile(l1[Y == 1], 0.03))
    cand = l1 >= t_cand
    h2 = TR.Head().fit(TR.verifier_matrix(l1, trd)[cand], Y[cand], np.where(H[cand] & (Y[cand] == 0), TR.HARD_WEIGHT, 1.0))

    def scores(d):
        a = h1.logit(d["s1"].astype(np.float32))
        return a, h2.logit(TR.verifier_matrix(a, d))

    # בחירת סף על val: FoleySet val (חיובי + דומים) ושליליים מהמטמון הכללי
    m_va = sp_ex == "val"
    l1x, l2x = scores(ex)
    l1v, l2v = scores(va)
    y_va, _, _, env_va = labels(va_meta, idx)
    street, indoor = (env_va == "street") & (y_va == 0), (env_va == "indoor_mixed") & (y_va == 0)
    pos_va = m_va & (y_ex == 1)
    best = None
    for t1 in np.unique(np.quantile(l1x[pos_va], np.linspace(0, 0.8, 30))):
        c_v = l1v >= t1
        for t2 in np.unique(np.quantile(l2v[c_v], np.linspace(0, 0.995, 60))) if c_v.any() else [np.inf]:
            det_v = c_v & (l2v >= t2)
            if det_v[street].mean() > STREET_BUDGET or det_v[indoor].mean() > INDOOR_BUDGET:
                continue
            det_x = (l1x >= t1) & (l2x >= t2)
            rec = _clip_recall(ex_meta, pos_va, det_x)
            key = (rec[0], -int(det_v[street | indoor].sum()))
            if best is None or key > best["key"]:
                best = {"key": key, "t1": float(t1), "t2": float(t2), "val_recall": rec,
                        "val_false": {"street": int(det_v[street].sum()), "street_n": int(street.sum()),
                                      "indoor": int(det_v[indoor].sum()), "indoor_n": int(indoor.sum())}}
    best.pop("key")
    from research_tools import hazard_audio_train as T
    a, b = T.platt(np.concatenate([l2x[pos_va], l2v[street | indoor][:20000]]),
                   np.concatenate([np.ones(pos_va.sum()), np.zeros(min(20000, int((street | indoor).sum())))]))
    best["platt"] = [float(a), float(b)]
    log(f"סף: {best} ({time.time() - t0:.0f}s)")
    # מבחן
    res = test(best, scores, ex, ex_meta, y_ex, sp_ex, te, te_meta, idx)
    fight = fight_rule(best, scores, te, te_meta, va, va_meta, idx)
    fight["composition"] = composition(best, fight["t_soft"], scores, ex, ex_meta, y_ex, sp_ex, te, te_meta, idx)
    worst = max(k / max(1, n) for k, n in fight["background"].values())
    comp = fight["composition"]["rate"]
    fight["accepted"] = bool(worst <= FIGHT_BG_MAX and comp >= FIGHT_COMPOSE_MIN)
    fight["criteria"] = (f"שווא ≤ {100 * FIGHT_BG_MAX:.1f}% מקטעי הרקע בכל סביבה, וזיהוי ≥ {100 * FIGHT_COMPOSE_MIN:.0f}% "
                         "במבחן ההרכבה (נקבע לפני ההרצה)")
    card = {"version": "blow_v1", "created": time.strftime("%Y-%m-%d"), "t1": best["t1"], "t2": best["t2"],
            "platt": best["platt"], "license_status": "research_dev_only: FoleySet per-clip licences unverified",
            "data": "FoleySet PunchHit (staged foley, not real fights)", "val": best, "test": res,
            "fight_rule": fight, "accepted": res["accepted"]}
    arrays = {**h1.arrays("blow__s1"), **h2.arrays("blow__s2"), "card": np.array([json.dumps(card, ensure_ascii=False,
                                                                                             default=float)])}
    np.savez_compressed(CANDIDATE, **arrays)
    if res["accepted"]:
        np.savez_compressed(OUT_MODEL, **arrays)
        log(f"נשמר: {OUT_MODEL}")
    if fight["accepted"]:
        # גלאי המכות נכנס **רק** בתוך כלל הקטטה (מכה + קול), לא כגלאי עצמאי
        fcard = {"version": "fight_rule_v1", "t1": best["t1"], "t_soft": fight["t_soft"], "k_windows": 6,
                 "vocal": "shout_sequence thresholds", "license_status": card["license_status"],
                 "report": "research/blow_fight_v1.md"}
        np.savez_compressed(FIGHT_MODEL, **{**h1.arrays("blow__s1"), **h2.arrays("blow__s2"),
                                            "card": np.array([json.dumps(fcard, ensure_ascii=False)])})
        log(f"נשמר: {FIGHT_MODEL}")
    elif os.path.exists(FIGHT_MODEL):
        os.remove(FIGHT_MODEL)
    report(card)
    return card


def _clip_recall(meta, mask, det):
    groups = {}
    for i in np.flatnonzero(mask):
        groups.setdefault((meta[i]["id"], meta[i]["variant"]), []).append(i)
    hit = sum(bool(det[ii].any()) for ii in groups.values())
    return hit, len(groups)


def test(op, scores, ex, ex_meta, y_ex, sp_ex, te, te_meta, idx):
    l1x, l2x = scores(ex)
    det_x = (l1x >= op["t1"]) & (l2x >= op["t2"])
    orig = np.array([m["variant"] == "orig" for m in ex_meta])
    m_te = (sp_ex == "test") & orig
    src = np.array([(idx.get(m["id"]) or {}).get("source") for m in ex_meta])
    rec = _clip_recall(ex_meta, m_te & (y_ex == 1) & (src == "foleyset"), det_x)
    out = {"foley_blow_recall": rec}
    for name, tags in (("foley_door_knock", {"door_knock"}), ("foley_door_slam", {"impact"}),
                       ("foley_glass", {"glass_break"}), ("foley_other", {"foley_other"})):
        mm = m_te & np.array([bool(set((idx.get(m["id"]) or {}).get("tags") or []) & tags) for m in ex_meta])
        out[name + "_false_clips"] = _clip_recall(ex_meta, mm, det_x)
    for cls in ("grito", "colisao", "tiro", "explosao", "outros", "buzina"):
        mm = (src == "ufrb_esd") & np.array([(idx.get(m["id"]) or {}).get("category") == cls for m in ex_meta])
        out[f"ufrb_{cls}_flagged"] = _clip_recall(ex_meta, mm, det_x)
    l1t, l2t = scores(te)
    det_t = (l1t >= op["t1"]) & (l2t >= op["t2"])
    y_t, _, _, env_t = labels(te_meta, idx)
    out["background"] = {}
    for e in np.unique(env_t):
        m = (env_t == e) & (y_t == 0)
        k, n = int(det_t[m].sum()), int(m.sum())
        out["background"][str(e)] = {"false": k, "n": n, "rate": k / max(1, n), "ci": TR_wilson(k, n)}
    worst = max(v["rate"] for e, v in out["background"].items() if v["n"] >= 1000)
    out["accepted"] = bool(rec[0] / max(1, rec[1]) >= 0.3 and worst <= 3 * INDOOR_BUDGET)
    out["criteria"] = "זיהוי מכות ב-FoleySet test ≥ 30% וגם שווא ≤ 0.3% מהחלונות בכל סביבת רקע (נקבע מראש)"
    return out


def TR_wilson(k, n):
    from research_tools import danger_audio_external as E
    return E.wilson(k, n)


def fight_rule(op, scores, te, te_meta, va, va_meta, idx):
    """כיול סף "כמעט-מכה" על val (רחוב + פנים) לכלל המשולב, ומדידת שווא על test. רמז הקול: ספי shout_sequence."""
    import json as _j
    seq = _j.load(open(os.path.join(ROOT, "models", "shout_sequence.json"), encoding="utf-8"))
    ci_d, ci_s = D.TARGETS.index("distress_voice"), D.TARGETS.index("scream")

    def clips(d, meta):
        l1, l2 = scores(d)
        vocal = (d["v1"][:, ci_d] >= seq["t_low"]) | (d["v1"][:, ci_s] >= seq["s_low"])
        out = {}
        for i, m in enumerate(meta):
            if m["variant"] == "orig":
                out.setdefault(m["id"], []).append((l2[i] if l1[i] >= op["t1"] else -1e9, bool(vocal[i])))
        return out

    def fires(ws, t_soft):
        ws = ws[:6]
        return any(b >= t_soft for b, _ in ws) and any(v for _, v in ws)
    cv = clips(va, va_meta)
    neg_v = {c: w for c, w in cv.items() if not set((idx.get(c) or {}).get("tags") or []) & {"distress_voice", "scream"}}
    t_soft = op["t2"]
    for t in np.linspace(op["t2"], op["t2"] - 6, 25):
        if sum(fires(w, t) for w in neg_v.values()) <= 0.001 * len(neg_v):
            t_soft = float(t)
        else:
            break
    ct = clips(te, te_meta)
    res = {"t_soft": t_soft, "background": {}}
    for c, w in ct.items():
        r = idx.get(c) or {}
        if set(r.get("tags") or []) & {"distress_voice", "scream", "blow"}:
            continue
        e = {"fsd50k": "indoor_mixed", "sonyc_ust": "street"}.get(r.get("source"), "phone_" + r.get("env", ""))
        b = res["background"].setdefault(e, [0, 0])
        b[0] += int(fires(w, t_soft))
        b[1] += 1
    return res


def composition(op, t_soft, scores, ex, ex_meta, y_ex, sp_ex, te, te_meta, idx, n_pairs=400):
    """מבחן הרכבה (לא הוכחה): חלונות מכה מ-FoleySet test ואחריהם חלונות קול מצוקה אמיתיים מ-test (FSD50K eval /
    Nonspeech7k / SONYC / UFRB). בודק את לוגיקת הכלל על צלילים אמיתיים שלא נראו - לא קטטה אמיתית."""
    import json as _j
    seq = _j.load(open(os.path.join(ROOT, "models", "shout_sequence.json"), encoding="utf-8"))
    rng = np.random.default_rng(11)
    l1x, l2x = scores(ex)
    soft_x = np.where(l1x >= op["t1"], l2x, -1e9) >= t_soft
    blows = {}
    for i, m in enumerate(ex_meta):
        if m["variant"] == "orig" and sp_ex[i] == "test" and y_ex[i] == 1:
            blows.setdefault(m["id"], []).append(bool(soft_x[i]))
    ci_d, ci_s = D.TARGETS.index("distress_voice"), D.TARGETS.index("scream")
    voc_t = (te["v1"][:, ci_d] >= seq["t_low"]) | (te["v1"][:, ci_s] >= seq["s_low"])
    vocals = {}
    for i, m in enumerate(te_meta):
        r = idx.get(m["id"]) or {}
        if m["variant"] == "orig" and "distress_voice" in (r.get("tags") or []):
            vocals.setdefault(m["id"], []).append(bool(voc_t[i]))
    bk, vk = sorted(blows), sorted(vocals)
    hits = 0
    for _ in range(n_pairs):
        b, v = blows[bk[rng.integers(len(bk))]][:2], vocals[vk[rng.integers(len(vk))]][:4]
        hits += int(any(b) and any(v))
    return {"pairs": n_pairs, "hits": hits, "rate": hits / n_pairs,
            "blow_clips_soft": sum(any(w[:2]) for w in blows.values()), "blow_clips": len(blows),
            "vocal_clips_soft": sum(any(w[:4]) for w in vocals.values()), "vocal_clips": len(vocals)}


def report(c):
    t = c["test"]
    p = lambda hn: f"{hn[0]}/{hn[1]} ({100 * hn[0] / max(1, hn[1]):.0f}%)"  # noqa: E731
    L = ["# גלאי מכות (blow_v1) וכלל \"קטטה אפשרית\"", "",
         f"נוצר ע\"י `research_tools/blow_train.py` ({c['created']}). כל מספר מהריצה.", "",
         f"**רישוי:** {c['license_status']}. **נתונים:** {c['data']}.", "",
         "## מבחן (מעלים שלא נראו באימון)", "",
         f"- **מכות / אגרופים (FoleySet test):** {p(t['foley_blow_recall'])} זוהו.",
         f"- **דומים שסומנו בטעות:** דפיקה בדלת {p(t['foley_door_knock_false_clips'])}, טריקת דלת "
         f"{p(t['foley_door_slam_false_clips'])}, שבירת זכוכית {p(t['foley_glass_false_clips'])}, "
         f"פולי אחר {p(t['foley_other_false_clips'])}.",
         "- **UFRB-ESD (מאגר חיצוני, 1 ש' לקטע) - כמה סומנו \"מכה\":** " + ", ".join(
             f"{k.split('_')[1]} {p(v)}" for k, v in t.items() if k.startswith("ufrb_")), "",
         "| סביבת רקע | חלונות שסומנו מכה בטעות | 95% |", "|---|---|---|"]
    for e, v in t["background"].items():
        L.append(f"| {e} | {v['false']}/{v['n']} ({100 * v['rate']:.2f}%) | {100 * (v['ci'][0] or 0):.2f}–{100 * (v['ci'][1] or 0):.2f}% |")
    f = c["fight_rule"]
    L += ["", f"**קבלה (נקבע מראש):** {t['criteria']}: **{'התקבל' if t['accepted'] else 'לא התקבל'}**.", "",
          "## כלל \"קטטה אפשרית\" (מכה + קול מצוקה/כאב בתוך ~9 ש')", "",
          f"סף \"כמעט-מכה\": {f['t_soft']:.2f} (כויל על validation). רמז הקול: ספי כלל רצף הצעקות.", "",
          "| סביבת רקע (קטעים של ~9 ש') | התרעות שווא |", "|---|---|"]
    for e, (k, n) in f["background"].items():
        L.append(f"| {e} | {k}/{n} ({100 * k / max(1, n):.2f}%) |")
    cp = f["composition"]
    L += ["", f"**מבחן הרכבה (לא הוכחה):** {cp['hits']}/{cp['pairs']} ({100 * cp['rate']:.0f}%) זוגות \"מכה ואחריה קול "
              f"מצוקה\" זוהו. רמז המכה הופיע ב-{cp['blow_clips_soft']}/{cp['blow_clips']} קטעי מכה; רמז הקול ב-"
              f"{cp['vocal_clips_soft']}/{cp['vocal_clips']} קטעי קול מצוקה.",
          f"**קבלת הכלל:** {f['criteria']}: **{'התקבל' if f['accepted'] else 'לא התקבל'}**.",
          "", "**לא נמדד:** זיהוי קטטות אמיתיות - אין מאגר פתוח ברישיון מתאים. המכות כאן הן אפקטים מוקלטים לקולנוע; "
              "כמה הם דומים למכה אמיתית בחדר או ברחוב - לא ידוע. לכן: התראה לבחירת פעולה בלבד."]
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    log(f"דו\"ח: {OUT_MD}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"extract": extract, "train": train}[sys.argv[1]]()
