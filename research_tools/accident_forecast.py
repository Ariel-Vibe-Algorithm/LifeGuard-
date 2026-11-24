# -*- coding: utf-8 -*-
"""
accident_forecast.py - ניסוי הוגן לחיזוי גיאוגרפי של תאונות הולכי רגל (שלב 6 בתוכנית מנוע ה-AI).

השאלה: האם "במקום הזה היו תאונות בעבר" מנבא איפה יהיו תאונות הולכי רגל בשנה הבאה, כמה טוב,
והאם משהו פשוט אחר עדיף על baseline ההתמדה ("אותו מקום כמו בעבר").

הפרדה בזמן (בלי דליפה):
- **בחירה (validation):** מתאימים על 2020-2022, בוחרים גודל תא / דעיכה / רוחב החלקה / חוזק prior על 2023.
- **מבחן (test):** מתאימים מחדש על 2020-2023 עם הבחירות שנקבעו, ומודדים פעם אחת על 2024.
- **rolling origin:** אותו תהליך עבור Y=2022,2023,2024 (בחירה על Y-1 מתוך <=Y-2, מבחן על Y).
  הפונקציה tune() מקבלת רק שורות משנים שלפני שנת המבחן (נאכף ב-assert ונבדק בטסטים).

רשת: מטרים ב-ITM (itm_x/itm_y), תאים של 100/250/500 מ'. "האזור הנבחן" (מכנה השטח ל-PAI) = כל ריבועי
1 ק"מ שהייתה בהם לפחות תאונה אחת בשנות האימון - קירוב לשטח המיושב/הסלול שנגזר מנתוני העבר בלבד.
תאונה בשנת המבחן מחוץ לאזור נספרת כהחטאה לכל המודלים.

מודלים (כולם פשוטים ושקופים): אחיד; התמדה (ספירת עבר); שנה קודמת; ספירה משוקללת-עדכניות (דעיכה
מעריכית); החלקה גאוסית (KDE); סכום 3x3 שכנים (הציון של האפליקציה); רגרסיית פואסון על ספירות העבר
(numpy, IRLS); והכלל הבינארי של האפליקציה (server/safety_intel.py: תאי 0.001°, סכום 3x3, סף = העשירון
העליון, מינימום 3). לספירות: קצב לתא = (S + a*m)/(n + a) - ממוצע אחורי גמא-פואסון עם prior אחיד.

מדדים: hit rate ו-PAI ב-1%/5%/10% משטח האזור, log-likelihood ו-deviance פואסון לתא, כיול לפי עשירונים,
ושיעור תאונות המבחן בתאים בלי היסטוריה (התקרה של כל שיטה מבוססת-היסטוריה). רווחי סמך 95% ב-bootstrap
של ריבועי 1 ק"מ (cluster bootstrap - שמרני יותר מ-bootstrap של תאונות בודדות, כי תאונות סמוכות תלויות).

שימוש:
  python -I research_tools/accident_forecast.py run      # -> research/accident_forecast_v1.md + .json
"""

import argparse
import csv
import datetime as _dt
import gzip
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CSV_PATH = os.path.join(ROOT, "research", "samples", "israel", "derived", "pedestrian_accidents_2020-2024.csv")
RISK_GRID = os.path.join(ROOT, "models", "risk_factors_grid.json.gz")
OUT_REPORT = os.path.join(ROOT, "research", "accident_forecast_v1.md")
OUT_JSON = os.path.join(ROOT, "research", "accident_forecast_v1.json")
VERSION = "accident_forecast_v1"

FIRST_YEAR = 2020
FINAL_TEST_YEAR = 2024
ROLLING_TEST_YEARS = (2022, 2023, 2024)
MAIN_STATUS = (1,)                  # STATUS_IGUN=1 = עיגון מדויק (השערה, ראו data_inventory_israel.md 2.1)
CELL_SIZES_M = (100, 250, 500)
BLOCK_M = 1000                      # ריבוע האזור הנבחן + יחידת ה-bootstrap
BBOX = (125_000, 375_000, 285_000, 805_000)   # x0, y0, x1, y1 ב-ITM (מכסה את כל הנתונים + שוליים)
COVERAGES = (0.01, 0.05, 0.10)
DECAYS = (1.0, 0.75, 0.5, 0.25, 0.0)          # 1 = התמדה, 0 = שנה אחרונה בלבד (סדר = עדיפות בשוויון)
BANDWIDTHS_M = (100, 150, 250, 400, 600, 1000)  # סטיית תקן של הגרעין הגאוסי; רק >= חצי תא
PRIOR_YEARS = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0)  # a = "שנים מדומות" של prior אחיד לקצב
KDE_TRUNCATE = 3.0
APP_CELL_DEG = 0.001                # כמו CELL_DEG ב-build_safety_layers / safety_intel
APP_PCT = 90
APP_MIN_COUNT = 3
N_BOOT = 2000
SEED = 20240601
COV_CELL_M = 250                    # ניתוח המשתנים המסבירים: תא 250 מ' (רשת המקור ~100 מ' במעלות)
COV_GROUPS = {"bus_stop": ("bus_stop",), "commercial": ("commercial_poi",), "rail": ("rail_station",),
              "night_venues": ("bar", "nightclub", "late_venue", "alcohol_shop")}
COV_EXCLUDED = {"ped_acc": "דליפה - כולל את תאונות 2020-2024 עצמן (גם שנת המבחן)",
                "lights": "רק ת\"א ובאר שבע", "bar_licensed_tlv": "ת\"א בלבד", "atm_tlv": "ת\"א בלבד",
                "bus_stop_tlv": "ת\"א בלבד", "park_tlv": "ת\"א בלבד", "night_zone_close": "ת\"א בלבד"}
CITY_HE = {5000: "תל אביב-יפו", 3000: "ירושלים", 4000: "חיפה", 9000: "באר שבע"}

FAMILIES = ("uniform", "persistence", "last_year", "recency", "neighbor3x3", "kde", "glm")
FAMILY_HE = {
    "uniform": "אחיד (אקראי)",
    "persistence": "התמדה - ספירת עבר",
    "last_year": "שנה קודמת בלבד",
    "recency": "ספירה עם דעיכה",
    "neighbor3x3": "סכום 3x3 שכנים (ציון האפליקציה)",
    "kde": "החלקה גאוסית (KDE)",
    "glm": "רגרסיית פואסון",
    "app_rule": "כלל המוקד של האפליקציה",
}


def log(*a):
    print(*a, flush=True)


# ================================================================= נתונים

def load_accidents(path=CSV_PATH):
    cols = {k: [] for k in ("year", "x", "y", "lat", "lon", "status", "severe", "night")}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            cols["year"].append(int(r["year"]))
            cols["x"].append(float(r["itm_x"]))
            cols["y"].append(float(r["itm_y"]))
            cols["lat"].append(float(r["lat"]))
            cols["lon"].append(float(r["lon"]))
            cols["status"].append(int(r["status_igun"] or -1))
            cols["severe"].append(str(r["severity_code"]).strip() in ("1", "2"))
            cols["night"].append(r["day_night"].strip() == "לילה")
    acc = {k: np.asarray(v) for k, v in cols.items()}
    acc["year"] = acc["year"].astype(np.int64)
    acc["status"] = acc["status"].astype(np.int64)
    return acc


def take(acc, mask):
    return {k: v[mask] for k, v in acc.items()}


def data_stats(acc):
    by = {}
    for y in sorted(set(acc["year"].tolist())):
        m = acc["year"] == y
        by[str(y)] = {str(s): int((acc["status"][m] == s).sum()) for s in sorted(set(acc["status"].tolist()))}
    dup = {}
    for s in sorted(set(acc["status"].tolist())):
        m = acc["status"] == s
        xy = acc["x"][m] * 1e7 + acc["y"][m]
        u, c = np.unique(xy, return_counts=True)
        dup[str(s)] = {"rows": int(m.sum()), "unique_xy": int(len(u)), "rows_on_shared_xy": int(c[c > 1].sum()),
                       "max_rows_same_xy": int(c.max()) if len(c) else 0}
    return {"rows": int(len(acc["year"])), "by_year_status": by, "coords_by_status": dup,
            "severe": int(acc["severe"].sum()), "night": int(acc["night"].sum())}


# ================================================================= רשת ואזור

class Grid:
    def __init__(self, cell_m, bbox=None):
        x0, y0, x1, y1 = bbox or BBOX
        assert BLOCK_M % cell_m == 0 and (x1 - x0) % BLOCK_M == 0 and (y1 - y0) % BLOCK_M == 0
        self.cell, self.x0, self.y0 = int(cell_m), x0, y0
        self.nx, self.ny = (x1 - x0) // cell_m, (y1 - y0) // cell_m
        self.nbx = (x1 - x0) // BLOCK_M
        self.k = BLOCK_M // cell_m

    def _ij(self, x, y, size):
        ix = np.floor((np.asarray(x, float) - self.x0) / size).astype(np.int64)
        iy = np.floor((np.asarray(y, float) - self.y0) / size).astype(np.int64)
        return ix, iy

    def cell_index(self, x, y):
        ix, iy = self._ij(x, y, self.cell)
        if len(ix) and ((ix < 0) | (ix >= self.nx) | (iy < 0) | (iy >= self.ny)).any():
            raise ValueError("נקודה מחוץ ל-BBOX")
        return iy * self.nx + ix

    def block_index(self, x, y):
        bx, by = self._ij(x, y, BLOCK_M)
        return by * self.nbx + bx

    def dense(self, x, y, weights=None):
        idx = self.cell_index(x, y)
        return np.bincount(idx, weights=weights, minlength=self.nx * self.ny).reshape(self.ny, self.nx).astype(float)

    def block_cells(self, blocks):
        blocks = np.asarray(blocks, np.int64)
        by, bx = np.divmod(blocks, self.nbx)
        off = np.arange(self.k)
        iy = by[:, None, None] * self.k + off[None, :, None]
        ix = bx[:, None, None] * self.k + off[None, None, :]
        flat = (iy * self.nx + ix).reshape(len(blocks), -1)
        return flat.ravel(), np.repeat(blocks, self.k * self.k)


class Region:
    """האזור הנבחן: כל התאים בריבועי 1 ק"מ שיש בהם תאונה בשנות האימון. נגזר מההיסטוריה בלבד."""

    def __init__(self, grid, blocks):
        self.grid = grid
        self.blocks = np.unique(np.asarray(blocks, np.int64))
        flat, blk = grid.block_cells(self.blocks)
        order = np.argsort(flat, kind="stable")
        self.flat, self.block = flat[order], blk[order]
        self.n = len(self.flat)
        self.area_km2 = len(self.blocks) * (BLOCK_M / 1000.0) ** 2

    def locate(self, flat_idx):
        flat_idx = np.asarray(flat_idx, np.int64)
        if self.n == 0:
            return np.full(len(flat_idx), -1)
        pos = np.searchsorted(self.flat, flat_idx)
        pc = np.clip(pos, 0, self.n - 1)
        return np.where(self.flat[pc] == flat_idx, pc, -1)

    def counts(self, loc):
        loc = np.asarray(loc)
        return np.bincount(loc[loc >= 0], minlength=self.n).astype(float)


def region_from(grid, hist):
    return Region(grid, grid.block_index(hist["x"], hist["y"]))


# ================================================================= שכבות היסטוריה (לכל שנת אימון)

def _integral(D):
    I = np.zeros((D.shape[0] + 1, D.shape[1] + 1))
    I[1:, 1:] = D.cumsum(0).cumsum(1)
    return I


def _window(I, iy, ix, r):
    ny, nx = I.shape[0] - 1, I.shape[1] - 1
    y0, y1 = np.clip(iy - r, 0, ny), np.clip(iy + r + 1, 0, ny)
    x0, x1 = np.clip(ix - r, 0, nx), np.clip(ix + r + 1, 0, nx)
    return I[y1, x1] - I[y0, x1] - I[y1, x0] + I[y0, x0]


def box_sum(D, r):
    """סכום חלון (2r+1)x(2r+1) לכל תא (אפסים מחוץ לרשת)."""
    iy, ix = np.indices(D.shape)
    return _window(_integral(D), iy, ix, r)


def _conv_last_axis(a, k):
    r = (len(k) - 1) // 2
    out = np.zeros_like(a)
    for j, w in enumerate(k):
        s = j - r
        if s == 0:
            out += w * a
        elif s > 0:
            out[..., s:] += w * a[..., :-s]
        else:
            out[..., :s] += w * a[..., -s:]
    return out


def gaussian_smooth(D, sigma_cells, truncate=KDE_TRUNCATE):
    """החלקה גאוסית ספרבילית (numpy בלבד). שומרת מסה (חוץ מהשוליים של ה-BBOX)."""
    r = max(1, int(math.ceil(truncate * sigma_cells)))
    t = np.arange(-r, r + 1, dtype=float)
    k = np.exp(-0.5 * (t / sigma_cells) ** 2)
    k /= k.sum()
    a = np.asarray(D, np.float32)
    a = _conv_last_axis(a, k.astype(np.float32))
    a = _conv_last_axis(a.T.copy(), k.astype(np.float32)).T
    return a.astype(float)


def build_layers(grid, hist, train_years, region, sigmas=()):
    """לכל שנת אימון: ספירה גולמית, סכומי 3x3 ו-7x7, והחלקות גאוסיות - בערכים לתאי האזור.
    מקבלת **רק** את שורות ההיסטוריה; שנה שלא ב-train_years = שגיאה."""
    years = sorted(int(y) for y in train_years)
    present = set(np.unique(hist["year"]).tolist())
    if not present <= set(years):
        raise AssertionError(f"build_layers קיבלה שנים מחוץ לאימון: {sorted(present - set(years))}")
    iy, ix = np.divmod(region.flat, grid.nx)
    L = {"years": years, "raw": {}, "box3": {}, "box7": {}, "g": {s: {} for s in sigmas}, "block_years": {}}
    for y in years:
        m = hist["year"] == y
        D = grid.dense(hist["x"][m], hist["y"][m])
        L["raw"][y] = D.ravel()[region.flat]
        I = _integral(D)
        L["box3"][y] = _window(I, iy, ix, 1)
        L["box7"][y] = _window(I, iy, ix, 3)
        del I
        for s in sigmas:
            L["g"][s][y] = gaussian_smooth(D, s / grid.cell).ravel()[region.flat]
        L["block_years"][y] = np.unique(grid.block_index(hist["x"][m], hist["y"][m]))
    return L


# ================================================================= מודלים

def _combine(layer, years, decay):
    last = years[-1]
    S, n_eff = 0.0, 0.0
    for y in years:
        w = float(decay) ** (last - y)
        S = S + w * layer[y]
        n_eff += w
    return np.asarray(S, float), n_eff


def poisson_irls(X, y, ridge=1e-6, iters=60, tol=1e-9):
    beta = np.zeros(X.shape[1])
    beta[0] = math.log(max(y.mean(), 1e-12))
    for _ in range(iters):
        eta = np.clip(X @ beta, -30, 20)
        mu = np.exp(eta)
        z = eta + (y - mu) / mu
        H = X.T @ (mu[:, None] * X) + ridge * np.eye(X.shape[1])
        new = np.linalg.solve(H, X.T @ (mu * z))
        if np.max(np.abs(new - beta)) < tol:
            beta = new
            break
        beta = new
    return beta


GLM_FEATURES = ("intercept", "log1p(cell)", "log1p(ring3x3)", "log1p(ring7x7)", "log1p(last_year)")


def glm_design(L, past, mask=None, extra=None):
    n = len(past)
    D = sum(L["raw"][y] for y in past) / n
    B3 = sum(L["box3"][y] for y in past) / n
    B7 = sum(L["box7"][y] for y in past) / n
    X = np.column_stack([np.ones_like(D), np.log1p(D), np.log1p(np.maximum(B3 - D, 0) / 8),
                         np.log1p(np.maximum(B7 - B3, 0) / 40), np.log1p(L["raw"][max(past)])])
    if extra is not None:
        X = np.column_stack([X, extra])
    return X if mask is None else X[mask]


def fit_glm(L, region, extra=None, row_mask=None):
    """פואסון על ספירות העבר: לכל שנת יעד t בתוך שנות האימון, תכונות משנים < t בלבד."""
    years = L["years"]
    if len(years) < 2:
        return None
    Xs, ys = [], []
    for t in years[1:]:
        past = [y for y in years if y < t]
        blocks = np.unique(np.concatenate([L["block_years"][y] for y in past]))
        mask = np.isin(region.block, blocks)
        if row_mask is not None:
            mask &= row_mask
        Xs.append(glm_design(L, past, mask, extra))
        ys.append(L["raw"][t][mask])
    return poisson_irls(np.vstack(Xs), np.concatenate(ys))


def fit_predict(spec, L, region, extra=None, row_mask=None):
    """מחזיר ציון לדירוג (score) ופונקציית קצב צפוי לשנה (lam(a)). משתמש רק בשכבות ההיסטוריה."""
    fam = spec["family"]
    years = L["years"]
    N = region.n if row_mask is None else int(row_mask.sum())
    sel = slice(None) if row_mask is None else row_mask
    if fam == "uniform":
        raw, n_eff = _combine(L["raw"], years, 1.0)
        m = raw[sel].sum() / n_eff / max(N, 1)
        return {"score": np.zeros(N), "lam": lambda a=None: np.full(N, m), "m": m}
    if fam == "glm":
        beta = fit_glm(L, region, extra, row_mask)
        if beta is None:
            return None
        lam = np.exp(np.clip(glm_design(L, years, None, extra)[sel] @ beta, -30, 20))
        return {"score": lam, "lam": lambda a=None: lam, "beta": beta.tolist(), "m": float(lam.mean())}
    if fam in ("persistence", "last_year", "recency"):
        S, n_eff = _combine(L["raw"], years, spec["decay"])
    elif fam == "kde":
        S, n_eff = _combine(L["g"][spec["sigma_m"]], years, spec["decay"])
    elif fam == "neighbor3x3":
        S, n_eff = _combine(L["box3"], years, 1.0)
        S = S / 9.0
    else:
        raise ValueError(fam)
    S = S[sel]
    m = S.sum() / n_eff / max(N, 1)
    return {"score": S, "lam": lambda a: (S + a * m) / (n_eff + a), "m": m}


def candidate_specs(cell):
    out = [{"family": "uniform"}, {"family": "persistence", "decay": 1.0}, {"family": "last_year", "decay": 0.0}]
    out += [{"family": "recency", "decay": d} for d in DECAYS]
    out += [{"family": "neighbor3x3"}]
    out += [{"family": "kde", "decay": d, "sigma_m": s} for s in BANDWIDTHS_M if s >= cell / 2 for d in DECAYS]
    out += [{"family": "glm"}]
    return out


# ================================================================= כלל האפליקציה (server/safety_intel.py)

def _app_keys(lat, lon):
    cy = np.floor(np.asarray(lat, float) / APP_CELL_DEG).astype(np.int64)
    cx = np.floor(np.asarray(lon, float) / APP_CELL_DEG).astype(np.int64)
    return cy, cx


def app_hotspot(lat, lon, pct=APP_PCT, min_count=APP_MIN_COUNT):
    """שחזור של Layers._hotspot_threshold + accidents_near: תא מסומן אם סכום 3x3 >= סף, כשהסף הוא
    max(min_count, vals[int(len*pct/100)]) על כל התאים המאוכלסים והשכנים שלהם. מחזיר (cy, cx, thr)."""
    cy, cx = _app_keys(lat, lon)
    if len(cy) == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), None
    code = cy * 10_000_000 + cx
    occ, cnt = np.unique(code, return_counts=True)
    oy, ox = np.divmod(occ, 10_000_000)
    d = (-1, 0, 1)
    ky = np.concatenate([oy + dy for dy in d for dx in d])
    kx = np.concatenate([ox + dx for dy in d for dx in d])
    keys = np.unique(ky * 10_000_000 + kx)
    kyy, kxx = np.divmod(keys, 10_000_000)
    vals = np.zeros(len(keys))
    for dy in d:
        for dx in d:
            c = (kyy + dy) * 10_000_000 + (kxx + dx)
            pos = np.clip(np.searchsorted(occ, c), 0, len(occ) - 1)
            vals += np.where(occ[pos] == c, cnt[pos], 0)
    sv = np.sort(vals)
    thr = max(min_count, sv[int(len(sv) * pct / 100)])
    f = vals >= thr
    return kyy[f], kxx[f], float(thr)


def fit_latlon_to_itm(lat, lon, x, y):
    """פולינום ממעלה 3 (lat,lon)->ITM, מותאם על נקודות האימון. משמש רק להמרת גיאומטריה."""
    def design(la, lo):
        a, b = np.asarray(la, float) - 31.5, np.asarray(lo, float) - 35.0
        return np.column_stack([np.ones_like(a), a, b, a * a, a * b, b * b, a ** 3, a * a * b, a * b * b, b ** 3])
    A = design(lat, lon)
    cx, *_ = np.linalg.lstsq(A, x, rcond=None)
    cy, *_ = np.linalg.lstsq(A, y, rcond=None)
    res = np.hypot(A @ cx - x, A @ cy - y)
    fn = lambda la, lo: (design(la, lo) @ cx, design(la, lo) @ cy)  # noqa: E731
    return fn, {"rms_m": float(np.sqrt((res ** 2).mean())), "p99_m": float(np.percentile(res, 99)),
                "max_m": float(res.max()), "n": int(len(res))}


def app_rule_eval(hist, test, region, to_itm):
    """הכלל הבינארי של האפליקציה, מאומן על ההיסטוריה בלבד. שטח התאים המסומנים מחושב ב-ITM."""
    fy, fx, thr = app_hotspot(hist["lat"], hist["lon"])
    flagged = fy * 10_000_000 + fx
    ty, tx = _app_keys(test["lat"], test["lon"])
    hit = np.isin(ty * 10_000_000 + tx, flagged).astype(float)
    # שטח: ארבע פינות כל תא -> ITM -> שרוך (shoelace)
    la0, lo0 = fy * APP_CELL_DEG, fx * APP_CELL_DEG
    corners = [(la0, lo0), (la0, lo0 + APP_CELL_DEG), (la0 + APP_CELL_DEG, lo0 + APP_CELL_DEG), (la0 + APP_CELL_DEG, lo0)]
    pts = [to_itm(a, b) for a, b in corners]
    area = np.zeros(len(fy))
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        area += x1 * y2 - x2 * y1
    area = np.abs(area) / 2.0
    cxm, cym = to_itm(la0 + APP_CELL_DEG / 2, lo0 + APP_CELL_DEG / 2)
    in_region = np.isin(region.grid.block_index(cxm, cym), region.blocks)
    total_km2 = area.sum() / 1e6
    return {"threshold": thr, "flagged_cells": int(len(fy)), "flagged_km2": float(total_km2),
            "frac_of_region": float(total_km2 / region.area_km2),
            "share_flagged_area_in_region": float(area[in_region].sum() / max(area.sum(), 1e-9)),
            "mean_cell_m2": float(area.mean()) if len(area) else None, "hit": hit}


# ================================================================= מדדים

def capture_weights(score, frac):
    """משקל הכללה לכל תא כשמכסים frac משטח האזור לפי ציון יורד. שוויון בגבול = חלוקה יחסית
    (התוחלת של שבירת שוויון אקראית), כך שהתוצאה דטרמיניסטית ולא תלויה בסדר התאים."""
    s = np.asarray(score, float)
    n = len(s)
    if n == 0:
        return s
    budget = frac * n
    _, inv, cnt = np.unique(-s, return_inverse=True, return_counts=True)
    before = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    wg = np.clip((budget - before) / cnt, 0.0, 1.0)
    return wg[inv.ravel()]


def hit_rate(score, y_region, n_total, frac):
    """שיעור תאונות המבחן (מכל התאונות, כולל מחוץ לאזור) שנפלו בשטח שנבחר."""
    return float(capture_weights(score, frac) @ np.asarray(y_region, float)) / max(n_total, 1)


def pai(hr, frac):
    """Predictive Accuracy Index = (hits/n) / (area/total_area). אחיד בתוך האזור ~ 1."""
    return hr / frac


_LGAMMA = {}


def _lgamma1(y):
    y = np.asarray(y, np.int64)
    top = int(y.max()) if len(y) else 0
    if top not in _LGAMMA:
        _LGAMMA[top] = np.array([math.lgamma(k + 1) for k in range(top + 1)])
    return _LGAMMA[top][y]


def poisson_ll_cells(lam, y):
    lam = np.maximum(np.asarray(lam, float), 1e-300)
    y = np.asarray(y, float)
    return y * np.log(lam) - lam - _lgamma1(y)


def poisson_dev_cells(lam, y):
    lam = np.maximum(np.asarray(lam, float), 1e-300)
    y = np.asarray(y, float)
    t = np.where(y > 0, y * np.log(np.where(y > 0, y, 1) / lam), 0.0)
    return 2 * (t - (y - lam))


def calibration(lam, y, nbins=10):
    """כיול לפי עשירונים של הקצב החזוי. תאים עם אותו קצב חזוי (למשל כל התאים בלי היסטוריה) נכנסים
    כולם לאותו סל - לפי הדרגה הממוצעת שלהם - כדי שהסלים לא יהיו פרוסות גיאוגרפיות שרירותיות.
    לכן חלק מהעשירונים יכולים להתמזג (הסל מסומן בטווח, למשל D1-D9)."""
    lam, y = np.asarray(lam, float), np.asarray(y, float)
    n = len(lam)
    _, inv, cnt = np.unique(lam, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    start = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    mid_rank = (start + (cnt - 1) / 2.0)[inv]
    b = np.minimum((mid_rank * nbins / max(n, 1)).astype(int), nbins - 1)
    rows = []
    # מיזוג סלים: עשירון בלי תאים (כי קבוצת שוויון גדולה "בלעה" אותו) מתמזג בתווית עם הסל הבא
    labels, last = {}, 0
    for i in range(nbins):
        if (b == i).any():
            labels[i] = f"D{last + 1}" + (f"-D{i + 1}" if i > last else "")
            last = i + 1
    for i, lab in labels.items():
        m = b == i
        rows.append({"bin": lab, "cells": int(m.sum()), "pred_sum": float(lam[m].sum()),
                     "obs_sum": float(y[m].sum()), "pred_mean": float(lam[m].mean()), "obs_mean": float(y[m].mean())})
    for name, q in (("top1%", 0.01), ("top0.1%", 0.001)):
        k = max(1, int(round(n * q)))
        w = capture_weights(lam, k / max(n, 1))          # שוויון בגבול = חלוקה יחסית
        rows.append({"bin": name, "cells": k, "pred_sum": float(w @ lam), "obs_sum": float(w @ y),
                     "pred_mean": float(w @ lam / k), "obs_mean": float(w @ y / k)})
    return rows


class Boot:
    """Cluster bootstrap על ריבועי 1 ק"מ. אותן דגימות לכל המודלים (השוואות זוגיות)."""

    def __init__(self, acc_block, region_block, n_boot=N_BOOT, seed=SEED):
        rng = np.random.default_rng(seed)
        self.ag, self.ainv = np.unique(acc_block, return_inverse=True)
        self.ainv = self.ainv.ravel()
        self.Ca = rng.multinomial(len(self.ag), np.full(len(self.ag), 1.0 / len(self.ag)), size=n_boot).astype(float)
        self.rg, self.rinv = np.unique(region_block, return_inverse=True)
        self.rinv = self.rinv.ravel()
        self.Cr = rng.multinomial(len(self.rg), np.full(len(self.rg), 1.0 / len(self.rg)), size=n_boot).astype(float)

    @staticmethod
    def _group(inv, n, W):
        out = np.zeros((n, W.shape[1]))
        np.add.at(out, inv, W)
        return out

    def ratio_acc(self, W, mask=None):
        """W: (n_accidents, k) - משקל פגיעה לכל תאונה. מחזיר est, lo, hi לכל עמודה."""
        W = np.asarray(W, float).reshape(len(self.ainv), -1)
        m = np.ones(len(W), bool) if mask is None else np.asarray(mask, bool)
        num = self._group(self.ainv[m], len(self.ag), W[m])
        den = np.bincount(self.ainv[m], minlength=len(self.ag)).astype(float)
        est = W[m].sum(0) / max(m.sum(), 1)
        bs = (self.Ca @ num) / np.maximum(self.Ca @ den, 1e-12)[:, None]
        lo, hi = np.percentile(bs, [2.5, 97.5], axis=0)
        return est, lo, hi

    def mean_region(self, V):
        """V: (n_cells, k) - ערך לתא (למשל LL). ממוצע לתא עם רווח סמך."""
        V = np.asarray(V, float).reshape(len(self.rinv), -1)
        num = self._group(self.rinv, len(self.rg), V)
        den = np.bincount(self.rinv, minlength=len(self.rg)).astype(float)
        est = V.mean(0)
        bs = (self.Cr @ num) / (self.Cr @ den)[:, None]
        lo, hi = np.percentile(bs, [2.5, 97.5], axis=0)
        return est, lo, hi


def _ci(est, lo, hi, i=0):
    return {"est": float(est[i]), "lo": float(lo[i]), "hi": float(hi[i])}


# ================================================================= בחירה (validation)

def tune(acc_pre, val_year, cells=CELL_SIZES_M, first_year=FIRST_YEAR):
    """בחירת היפר-פרמטרים: מתאים על [first_year, val_year) ומודד על val_year.
    acc_pre חייב להכיל רק שנים <= val_year (כלומר לפני שנת המבחן)."""
    if len(acc_pre["year"]) and int(acc_pre["year"].max()) > val_year:
        raise AssertionError("tune קיבלה נתונים משנה שאחרי שנת הבחירה")
    train_years = list(range(first_year, val_year))
    hist = take(acc_pre, acc_pre["year"] < val_year)
    val = take(acc_pre, acc_pre["year"] == val_year)
    rows, chosen, best_obj = [], {}, {}
    for cell in cells:
        t0 = time.time()
        grid = Grid(cell)
        region = region_from(grid, hist)
        sig = [s for s in BANDWIDTHS_M if s >= cell / 2]
        L = build_layers(grid, hist, train_years, region, sig)
        loc = region.locate(grid.cell_index(val["x"], val["y"]))
        yv = region.counts(loc)
        n_val = len(val["year"])
        fam_best = {}
        for spec in candidate_specs(cell):
            fp = fit_predict(spec, L, region)
            if fp is None:
                continue
            hrs = [hit_rate(fp["score"], yv, n_val, c) for c in COVERAGES]
            obj = float(np.mean(hrs))
            rows.append({"cell_m": cell, **spec, "objective": obj, **{f"hr@{c:g}": h for c, h in zip(COVERAGES, hrs)}})
            b = fam_best.get(spec["family"])
            if b is None or obj > b[0] + 1e-12:
                fam_best[spec["family"]] = (obj, spec, fp)
        chosen[cell] = {}
        for fam, (obj, spec, fp) in fam_best.items():
            spec = dict(spec)
            if fam not in ("uniform", "glm"):
                lls = {a: float(poisson_ll_cells(fp["lam"](a), yv).mean()) for a in PRIOR_YEARS}
                spec["prior_years"] = max(PRIOR_YEARS, key=lambda a: (lls[a], -a))
                spec["val_ll_by_prior"] = lls
            spec["val_objective"] = obj
            chosen[cell][fam] = spec
        if "glm" not in chosen[cell]:      # שנת אימון אחת: אין על מה לאמן GLM בבחירה; אין לו היפר-פרמטרים
            chosen[cell]["glm"] = {"family": "glm", "val_objective": None}
        best_obj[cell] = max(v["val_objective"] for f, v in chosen[cell].items()
                             if f != "uniform" and v["val_objective"] is not None)
        log(f"   tune val={val_year} cell={cell}m region={region.area_km2:.0f}km2 "
            f"best={best_obj[cell]:.4f} ({time.time() - t0:.1f}s)")
    primary = max(cells, key=lambda c: (best_obj[c], -c))
    return {"val_year": val_year, "train_years": train_years, "chosen": chosen, "primary_cell_m": primary,
            "best_objective_by_cell": best_obj, "grid": rows}


# ================================================================= מבחן

def evaluate_cell(cell, hist, train_years, test, chosen, boot, app=None, full=True):
    grid = Grid(cell)
    region = region_from(grid, hist)
    sig = sorted({s["sigma_m"] for s in chosen.values() if s.get("sigma_m")})
    L = build_layers(grid, hist, train_years, region, sig)
    loc = region.locate(grid.cell_index(test["x"], test["y"]))
    y = region.counts(loc)
    n = len(test["year"])
    past = sum(L["raw"][yy] for yy in L["years"])
    zero_hist = (loc < 0) | (past[np.maximum(loc, 0)] == 0)
    out = {"cell_m": cell, "region_km2": region.area_km2, "region_cells": region.n, "n_test": n,
           "test_in_region": int((loc >= 0).sum()),
           "share_test_outside_region": float((loc < 0).mean()) if n else None,
           "share_test_zero_history": float(zero_hist.mean()) if n else None,
           "share_region_cells_with_history": float((past > 0).mean()),
           "models": {}}
    fams = [f for f in FAMILIES if f in chosen]
    fits = {}
    for f in fams:
        fp = fit_predict(chosen[f], L, region)
        if fp is not None:
            fits[f] = fp
    fams = list(fits)
    covs = list(COVERAGES) + ([app["frac_of_region"]] if app is not None else [])
    W = np.zeros((n, len(fams), len(covs)))
    for j, f in enumerate(fams):
        for k, c in enumerate(covs):
            w = capture_weights(fits[f]["score"], c)
            W[:, j, k] = np.where(loc >= 0, w[np.maximum(loc, 0)], 0.0)
    flat = W.reshape(n, -1)
    est, lo, hi = boot.ratio_acc(flat)
    lam = {f: fits[f]["lam"](chosen[f].get("prior_years")) for f in fams}
    LL = np.column_stack([poisson_ll_cells(lam[f], y) for f in fams])
    DV = np.column_stack([poisson_dev_cells(lam[f], y) for f in fams])
    lle, llo, llh = boot.mean_region(LL)
    i_u = fams.index("uniform")
    gain = LL - LL[:, [i_u]]
    ge, glo, ghi = boot.mean_region(gain)
    y_sum = max(y.sum(), 1.0)
    for j, f in enumerate(fams):
        m = {"params": {k: v for k, v in chosen[f].items() if k not in ("family", "val_ll_by_prior")},
             "hit_rate": {}, "pai": {}}
        for k, c in enumerate(COVERAGES):
            i = j * len(covs) + k
            m["hit_rate"][f"{c:g}"] = _ci(est, lo, hi, i)
            m["pai"][f"{c:g}"] = {kk: v / c for kk, v in _ci(est, lo, hi, i).items()}
        if app is not None:
            m["hit_rate_at_app_area"] = _ci(est, lo, hi, j * len(covs) + len(COVERAGES))
        m["loglik_per_cell"] = _ci(lle, llo, llh, j)
        m["deviance_per_cell"] = float(DV[:, j].mean())
        m["ll_gain_vs_uniform_per_accident"] = {k: v * region.n / y_sum for k, v in _ci(ge, glo, ghi, j).items()}
        m["pred_total"] = float(lam[f].sum())
        m["obs_total_in_region"] = float(y.sum())
        if "beta" in fits[f]:
            m["glm_beta"] = dict(zip(GLM_FEATURES, fits[f]["beta"]))
        out["models"][f] = m
    out["_W"], out["_fams"], out["_covs"], out["_lam"], out["_y"], out["_LL"] = W, fams, covs, lam, y, LL
    if full:
        out["calibration"] = {f: calibration(lam[f], y) for f in fams if f in ("persistence", "kde", "glm", "recency")}
    return out


def paired(boot, W, fams, covs, a, b, k, mask=None):
    d = W[:, fams.index(a), k] - W[:, fams.index(b), k]
    est, lo, hi = boot.ratio_acc(d[:, None], mask)
    return {"a": a, "b": b, "coverage": covs[k], "diff_hr": _ci(est, lo, hi)}


def run_pipeline(acc, test_year, cells=CELL_SIZES_M, full=True, first_year=FIRST_YEAR, label=""):
    """בחירה על test_year-1 (רק נתונים < test_year), ואז מבחן יחיד על test_year."""
    t0 = time.time()
    log(f"== pipeline {label} test={test_year}")
    acc_pre = take(acc, acc["year"] < test_year)
    sel = tune(acc_pre, test_year - 1, cells, first_year)
    train_years = list(range(first_year, test_year))
    hist = acc_pre
    test = take(acc, acc["year"] == test_year)
    assert int(hist["year"].max()) < test_year and (test["year"] == test_year).all()
    to_itm, fit_info = fit_latlon_to_itm(hist["lat"], hist["lon"], hist["x"], hist["y"])
    g1 = Grid(BLOCK_M)
    region_b = region_from(g1, hist)
    app =app_rule_eval(hist, test, region_b, to_itm)
    eval_cells = cells if full else (sel["primary_cell_m"],)
    res = {"test_year": test_year, "train_years": train_years, "selection": sel, "latlon_to_itm_fit": fit_info,
           "n_test": int(len(test["year"])), "n_train": int(len(hist["year"])),
           "region_km2": region_b.area_km2, "by_cell": {}}
    for cell in eval_cells:
        t1 = time.time()
        grid = Grid(cell)
        region = region_from(grid, hist)
        boot_c = Boot(g1.block_index(test["x"], test["y"]), region.block)
        r = evaluate_cell(cell, hist, train_years, test, sel["chosen"][cell], boot_c, app, full)
        r["boot"] = boot_c
        res["by_cell"][cell] = r
        log(f"   test={test_year} cell={cell}m done ({time.time() - t1:.1f}s)")
    pc = sel["primary_cell_m"]
    P = res["by_cell"][pc]
    W, fams, covs, bc = P["_W"], P["_fams"], P["_covs"], P["boot"]
    a_est, a_lo, a_hi = bc.ratio_acc(app["hit"][:, None])
    app_out = {k: v for k, v in app.items() if k != "hit"}
    app_out["hit_rate"] = _ci(a_est, a_lo, a_hi)
    app_out["pai"] = {k: v / app["frac_of_region"] for k, v in app_out["hit_rate"].items()}
    # השוואה זוגית: כלל האפליקציה מול כל מודל באותו שטח
    k_app = len(covs) - 1
    app_cmp = {}
    for f in fams:
        d = W[:, fams.index(f), k_app] - app["hit"]
        e, lo, hi = bc.ratio_acc(d[:, None])
        app_cmp[f] = _ci(e, lo, hi)
    app_out["diff_model_minus_app_at_same_area"] = app_cmp
    res["app_rule"] = app_out
    comps = []
    for f in fams:
        if f != "persistence":
            for k in range(len(COVERAGES)):
                comps.append(paired(bc, W, fams, covs, f, "persistence", k))
    res["comparisons_vs_persistence"] = comps
    # תתי-קבוצות במבחן: קשות/קטלניות, לילה
    subs = {}
    for name, mask in (("severe", test["severe"]), ("night", test["night"])):
        e, lo, hi = bc.ratio_acc(W.reshape(len(W), -1), mask)
        ae, alo, ahi = bc.ratio_acc(app["hit"][:, None], mask)
        subs[name] = {"n": int(mask.sum()),
                      "models": {f: {f"{c:g}": _ci(e, lo, hi, j * len(covs) + k) for k, c in enumerate(COVERAGES)}
                                 for j, f in enumerate(fams)},
                      "app_rule": _ci(ae, alo, ahi)}
    res["test_subsets"] = subs
    log(f"== pipeline {label} test={test_year} done ({time.time() - t0:.1f}s)")
    return res


# ================================================================= משתנים מסבירים (4 ערים)

def covariate_analysis(acc, test_year, chosen_250, first_year=FIRST_YEAR, path=RISK_GRID, cell=COV_CELL_M):
    if not os.path.exists(path):
        return {"available": False, "reason": "models/risk_factors_grid.json.gz חסר"}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        d = json.load(fh)
    fields = d["fields"]
    fi = {f: i for i, f in enumerate(fields)}
    keys = list(d["cells"].keys())
    vals = np.array([d["cells"][k] for k in keys], float)
    cy = np.array([int(k.split(":")[0]) for k in keys])
    cx = np.array([int(k.split(":")[1]) for k in keys])
    hist = take(acc, acc["year"] < test_year)
    test = take(acc, acc["year"] == test_year)
    train_years = list(range(first_year, test_year))
    to_itm, fit_info = fit_latlon_to_itm(hist["lat"], hist["lon"], hist["x"], hist["y"])
    gx, gy = to_itm((cy + 0.5) * d["cell_deg"], (cx + 0.5) * d["cell_deg"])
    grid = Grid(cell)
    gidx = grid.cell_index(gx, gy)
    region = region_from(grid, hist)
    rloc = region.locate(gidx)
    covered = np.zeros(region.n, bool)
    covered[rloc[rloc >= 0]] = True
    feats = []
    for g, members in COV_GROUPS.items():
        v = sum(vals[:, fi[m]] for m in members)
        agg = np.bincount(rloc[rloc >= 0], weights=v[rloc >= 0], minlength=region.n)
        feats.append(np.log1p(agg))
    city = np.zeros(region.n)
    city[rloc[rloc >= 0]] = vals[rloc >= 0, fi["city"]]
    cities = sorted(int(c) for c in set(vals[:, fi["city"]].tolist()))
    dummies = [(city == c).astype(float) for c in cities[1:]]
    X_city = np.column_stack(dummies) if dummies else np.zeros((region.n, 0))
    X_cov = np.column_stack([X_city] + feats)
    sig = sorted({s["sigma_m"] for s in chosen_250.values() if s.get("sigma_m")})
    L = build_layers(grid, hist, train_years, region, sig)
    tloc = region.locate(grid.cell_index(test["x"], test["y"]))
    # תאונות מבחן בתוך הערים המכוסות (גם תאים מכוסים מחוץ לאזור): מכנה ה-hit rate
    covered_all = np.unique(gidx)
    t_in_city = np.isin(grid.cell_index(test["x"], test["y"]), covered_all)
    y = region.counts(np.where(t_in_city, tloc, -1))[covered]
    n_city = int(t_in_city.sum())
    models = {}
    specs = {"uniform": {"family": "uniform"}, "persistence": chosen_250["persistence"],
             "kde": chosen_250["kde"], "glm_past": {"family": "glm"}, "glm_past_cov": {"family": "glm"}}
    lam_all = {}
    for name, spec in specs.items():
        extra = X_cov if name == "glm_past_cov" else (X_city if name == "glm_past" and X_city.shape[1] else None)
        fp = fit_predict(spec, L, region, extra=extra, row_mask=covered)
        lam = fp["lam"](spec.get("prior_years"))
        lam_all[name] = lam
        models[name] = {"hit_rate": {f"{c:g}": hit_rate(fp["score"], y, n_city, c) for c in COVERAGES},
                        "loglik_per_cell": float(poisson_ll_cells(lam, y).mean())}
        if "beta" in fp:
            names = list(GLM_FEATURES) + [f"city_{c}" for c in cities[1:]] * (extra is not None) + \
                (list(COV_GROUPS) if name == "glm_past_cov" else [])
            models[name]["beta"] = dict(zip(names, fp["beta"]))
    # bootstrap זוגי ל-hit rate בתוך הערים: glm_past_cov מול glm_past ומול persistence
    blk = grid.block_index(test["x"], test["y"])[t_in_city]
    sub_loc = np.full(region.n, -1)
    sub_loc[np.where(covered)[0]] = np.arange(covered.sum())
    tl = tloc[t_in_city]
    tl_sub = np.where(tl >= 0, sub_loc[np.maximum(tl, 0)], -1)
    boot = Boot(blk, region.block[covered])
    Wc = {}
    for name, spec in specs.items():
        sc = lam_all[name] if name.startswith("glm") else None
        if sc is None:
            extra = None
            sc = fit_predict(spec, L, region, extra=extra, row_mask=covered)["score"]
        w = capture_weights(sc, 0.05)
        Wc[name] = np.where(tl_sub >= 0, w[np.maximum(tl_sub, 0)], 0.0)
    cmp = {}
    for a, b in (("glm_past_cov", "glm_past"), ("glm_past_cov", "persistence"), ("kde", "persistence")):
        e, lo, hi = boot.ratio_acc((Wc[a] - Wc[b])[:, None])
        cmp[f"{a}-{b}@5%"] = _ci(e, lo, hi)
    llc = boot.mean_region(np.column_stack([poisson_ll_cells(lam_all["glm_past_cov"], y)
                                            - poisson_ll_cells(lam_all["glm_past"], y)]))
    return {"available": True, "cell_m": cell, "cities": [CITY_HE.get(c, str(c)) for c in cities],
            "covered_region_cells": int(covered.sum()), "covered_region_km2": float(covered.sum() * cell * cell / 1e6),
            "n_test_in_cities": n_city, "osm_snapshot": {k: v.get("osm_base") for k, v in d.get("coverage", {}).items()},
            "features": {g: list(m) for g, m in COV_GROUPS.items()}, "excluded": COV_EXCLUDED,
            "latlon_to_itm_fit": fit_info, "models": models, "paired_5pct": cmp,
            "ll_gain_cov_vs_past_per_cell": _ci(*llc)}


# ================================================================= ריצה מלאה

def _strip(o):
    if isinstance(o, dict):
        return {str(k): _strip(v) for k, v in o.items() if not str(k).startswith("_") and k != "boot"}
    if isinstance(o, (list, tuple)):
        return [_strip(v) for v in o]
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return None if not math.isfinite(v) else round(v, 6)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return _strip(o.tolist())
    return o


def run_all(csv_path=CSV_PATH, out_json=OUT_JSON, out_report=OUT_REPORT):
    t0 = time.time()
    log(f"[{VERSION}] loading {csv_path}")
    acc_all = load_accidents(csv_path)
    stats = data_stats(acc_all)
    acc = take(acc_all, np.isin(acc_all["status"], MAIN_STATUS))
    log(f"   rows={stats['rows']} main(status in {MAIN_STATUS})={len(acc['year'])}")
    final = run_pipeline(acc, FINAL_TEST_YEAR, CELL_SIZES_M, full=True, label="main")
    rolling = {FINAL_TEST_YEAR: final}
    for y in ROLLING_TEST_YEARS:
        if y != FINAL_TEST_YEAR:
            rolling[y] = run_pipeline(acc, y, CELL_SIZES_M, full=False, label="rolling")
    sens = run_pipeline(acc_all, FINAL_TEST_YEAR, CELL_SIZES_M, full=False, label="all-status")
    log("== covariates")
    cov = covariate_analysis(acc, FINAL_TEST_YEAR, final["selection"]["chosen"][COV_CELL_M])
    roll_out = {}
    for y, r in sorted(rolling.items()):
        pc = r["selection"]["primary_cell_m"]
        P = r["by_cell"][pc]
        roll_out[str(y)] = {
            "val_year": r["selection"]["val_year"], "train_years": r["train_years"], "primary_cell_m": pc,
            "n_test": r["n_test"], "share_test_zero_history": P["share_test_zero_history"],
            "share_test_outside_region": P["share_test_outside_region"],
            "chosen": {f: {k: v for k, v in s.items() if k != "val_ll_by_prior"}
                       for f, s in r["selection"]["chosen"][pc].items()},
            "hit_rate": {f: m["hit_rate"] for f, m in P["models"].items()},
            "loglik_per_cell": {f: m["loglik_per_cell"] for f, m in P["models"].items()},
            "app_rule": {"frac_of_region": r["app_rule"]["frac_of_region"], "hit_rate": r["app_rule"]["hit_rate"],
                         "threshold": r["app_rule"]["threshold"]},
            "comparisons_vs_persistence": r["comparisons_vs_persistence"]}
    spc = sens["selection"]["primary_cell_m"]
    sens_out = {"statuses": "all", "n_test": sens["n_test"], "n_train": sens["n_train"], "primary_cell_m": spc,
                "share_test_zero_history": sens["by_cell"][spc]["share_test_zero_history"],
                "hit_rate": {f: m["hit_rate"] for f, m in sens["by_cell"][spc]["models"].items()},
                "app_rule": {k: v for k, v in sens["app_rule"].items() if k != "diff_model_minus_app_at_same_area"},
                "comparisons_vs_persistence": sens["comparisons_vs_persistence"]}
    final_out = {k: v for k, v in final.items()}
    final_out["selection"] = {k: v for k, v in final["selection"].items()}
    out = {
        "version": VERSION, "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "runtime_s": round(time.time() - t0, 1),
        "config": {"main_status": list(MAIN_STATUS), "cell_sizes_m": list(CELL_SIZES_M), "block_m": BLOCK_M,
                   "coverages": list(COVERAGES), "decays": list(DECAYS), "bandwidths_m": list(BANDWIDTHS_M),
                   "prior_years": list(PRIOR_YEARS), "kde_truncate_sigma": KDE_TRUNCATE,
                   "app_rule": {"cell_deg": APP_CELL_DEG, "pct": APP_PCT, "min_count": APP_MIN_COUNT},
                   "n_boot": N_BOOT, "seed": SEED, "final_test_year": FINAL_TEST_YEAR,
                   "selection_objective": "mean hit rate over coverages (validation year)",
                   "prior_selection": "validation Poisson log-likelihood"},
        "data": stats,
        "main": final_out,
        "rolling": roll_out,
        "sensitivity_all_status": sens_out,
        "covariates": cov,
    }
    out = _strip(out)
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    report(out, out_report)
    log(f"[{VERSION}] wrote {out_json} and {out_report} ({time.time() - t0:.0f}s)")
    return out


# ================================================================= דו"ח

def pct(x, d=1):
    return "—" if x is None else f"{100 * x:.{d}f}%"


def ci_pct(c, d=1):
    return f"{pct(c['est'], d)} ({pct(c['lo'], d)}–{pct(c['hi'], d)})"


def ci_num(c, d=2):
    return f"{c['est']:.{d}f} ({c['lo']:.{d}f}–{c['hi']:.{d}f})"


def ci_pp(c, d=1):
    s = lambda v: f"{100 * v:+.{d}f}"  # noqa: E731
    return f"{s(c['est'])} נ\"א ({s(c['lo'])} עד {s(c['hi'])})"


def params_he(f, p):
    if not p:
        return ""
    parts = []
    if f in ("recency", "kde") and "decay" in p:
        parts.append(f"דעיכה={p['decay']:g}")
    if f == "kde":
        parts.append(f"σ={p['sigma_m']:g} מ'")
    if p.get("prior_years") is not None:
        parts.append(f"a={p['prior_years']:g}")
    return ", ".join(parts)


def _sig(c):
    return c["lo"] > 0 or c["hi"] < 0


def report(o, path=OUT_REPORT):
    M = o["main"]
    sel = M["selection"]
    pc = sel["primary_cell_m"]
    P = M["by_cell"][str(pc)]
    mods = P["models"]
    covs = [f"{c:g}" for c in o["config"]["coverages"]]
    app = M["app_rule"]
    st = o["data"]
    L = []
    w = L.append
    w(f"# ניסוי חיזוי גיאוגרפי של תאונות הולכי רגל - {o['version']}")
    w("")
    w(f"_דו\"ח שנוצר אוטומטית ע\"י `research_tools/accident_forecast.py run` ב-{o['generated_at']} "
      f"(זמן ריצה {o['runtime_s']:.0f} ש'). כל המספרים כאן מגיעים מהריצה; הנתונים המלאים ב-`research/accident_forecast_v1.json`._")
    w("")
    # ---------------- תקציר
    w("## 1. השורה התחתונה")
    w("")
    hr = {f: mods[f]["hit_rate"] for f in mods}
    cmp ={(c["a"], f"{c['coverage']:g}"): c["diff_hr"] for c in M["comparisons_vs_persistence"]}
    w(f"- **התמדה עובדת, אבל רק בחלק מהמקומות.** בתא של {pc} מ' (נבחר על 2023), ספירת התאונות של 2020–2023 "
      f"תופסת {ci_pct(hr['persistence']['0.01'])} מתאונות 2024 ב-1% מהשטח הנבחן "
      f"(PAI {ci_num(mods['persistence']['pai']['0.01'], 1)}), ו-{ci_pct(hr['persistence']['0.05'])} ב-5% מהשטח. "
      f"בחירה אקראית תופסת {pct(hr['uniform']['0.01']['est'])} ו-{pct(hr['uniform']['0.05']['est'])} בהתאמה.")
    w(f"- **התקרה של כל שיטה מבוססת-היסטוריה נמוכה:** {pct(P['share_test_zero_history'])} מתאונות 2024 קרו בתא של "
      f"{pc} מ' שלא הייתה בו אף תאונה ב-2020–2023 (מתוכן {pct(P['share_test_outside_region'])} מחוץ לאזור הנבחן כולו).")
    d_app = app["diff_model_minus_app_at_same_area"]
    w(f"- **כלל המוקד של האפליקציה** (עשירון עליון של סכום 3x3 ≈ 300 מ', סף {app['threshold']:g} תאונות) מסמן "
      f"{app['flagged_km2']:.1f} קמ\"ר = {pct(app['frac_of_region'], 2)} מהאזור הנבחן, ותופס {ci_pct(app['hit_rate'])} "
      f"מתאונות 2024 (PAI {ci_num(app['pai'], 1)}). **באותו שטח בדיוק**, דירוג לפי ספירת העבר בתא של {pc} מ' עצמו תופס "
      f"{ci_pct(mods['persistence']['hit_rate_at_app_area'])} (הפרש {ci_pp(d_app['persistence'])}"
      f"{', מובהק' if _sig(d_app['persistence']) else ', לא מובהק'}). סכום 3x3 ברשת ITM נותן "
      f"{ci_pct(mods['neighbor3x3']['hit_rate_at_app_area'])} - כמו הכלל - כלומר ההחלקה ל-~300 מ' היא שמדללת את האות.")
    for c in covs:
        better = [f for f in mods if (f, c) in cmp and _sig(cmp[(f, c)]) and cmp[(f, c)]["est"] > 0]
        worse = [f for f in mods if (f, c) in cmp and _sig(cmp[(f, c)]) and cmp[(f, c)]["est"] < 0 and f != "uniform"]
        if better:
            txt = "; ".join(f"{FAMILY_HE[f]} {ci_pp(cmp[(f, c)])}" for f in better)
            w(f"- **ב-{pct(float(c), 0)} משטח** עקפו את ההתמדה באופן מובהק: {txt}.")
        else:
            w(f"- **ב-{pct(float(c), 0)} משטח שום מודל לא עקף את ההתמדה באופן מובהק**"
              + (f"; גרועים ממנה באופן מובהק: {', '.join(FAMILY_HE[f] for f in worse)}." if worse else "."))
    w(f"- **למה:** רק {pct(P['share_region_cells_with_history'])} מתאי האזור הם עם היסטוריה. עד שהשטח הנבחר ממצה אותם, "
      "ספירת העבר בתא היא הדירוג הטוב ביותר; מעבר לזה (כשצריך לבחור בין תאים בלי היסטוריה) החלקה/רגרסיה עוזרות.")
    if "recency" in mods:
        rp = mods["recency"]["params"].get("decay")
        rs = any(_sig(cmp[("recency", c)]) for c in covs if ("recency", c) in cmp)
        w(f"- **דעיכה בזמן:** נבחרה דעיכה {rp:g}; ההפרש מול התמדה " + ("מובהק." if rs else "לא מובהק באף רמת שטח.")
          + f" \"שנה קודמת בלבד\" גרועה בהרבה ({ci_pp(cmp[('last_year', '0.05')])} ב-5%) - מקומות התאונות יציבים לאורך שנים.")
    ll_best = max(mods, key=lambda f: mods[f]["loglik_per_cell"]["est"])
    w(f"- **תחזית ספירות (log-likelihood פואסון לתא):** הטוב ביותר - {FAMILY_HE[ll_best]}; רווח מול אחיד "
      f"{ci_num(mods[ll_best]['ll_gain_vs_uniform_per_accident'], 3)} nats לתאונה. התמדה עם prior: "
      f"{ci_num(mods['persistence']['ll_gain_vs_uniform_per_accident'], 3)}.")
    ro = o["rolling"]
    w("- **יותר שנים = יותר כיסוי:** ב-rolling origin, HR@5% של התמדה: " + "; ".join(
        f"{y} ({len(r['train_years'])} שנות אימון) {pct(r['hit_rate']['persistence']['0.05']['est'])}, "
        f"בלי היסטוריה {pct(r['share_test_zero_history'])}" for y, r in ro.items())
      + ". (שנות המבחן שונות, כך שזה רמז ולא הוכחה.)")
    w("- **מה זה לא אומר:** אלה תאונות דרכים עם נפגעים הולכי רגל - לא אלימות ולא פשיעה. לא ידועה החשיפה "
      "(כמה הולכי רגל עוברים), ולכן \"מוקד\" יכול להיות פשוט \"מקום שיש בו הרבה הולכי רגל\".")
    w("")
    # ---------------- שיטה
    w("## 2. שאלה ושיטה")
    w("")
    w("**שאלה:** האם \"היו כאן תאונות\" מנבא איפה יהיו תאונות הולכי רגל בשנה הבאה, כמה טוב, והאם משהו פשוט עוקף "
      "את ה-baseline של \"אותו מקום כמו בעבר\"? (שלב 6 ב-`design/AI_ENGINE_PLAN_HE.md`.)")
    w("")
    w("**הפרדה בזמן (בלי דליפה):**")
    w(f"- בחירה: מתאימים על {sel['train_years'][0]}–{sel['train_years'][-1]}, בוחרים על {sel['val_year']}. "
      "כל בחירה (גודל תא, דעיכה, רוחב החלקה, חוזק ה-prior) נעשית רק כאן.")
    w(f"- מבחן: מתאימים מחדש על {M['train_years'][0]}–{M['train_years'][-1]} עם הבחירות הקבועות, ומודדים **פעם אחת** על "
      f"{M['test_year']}. הפונקציה `tune()` מקבלת רק שורות לפני שנת המבחן (assert + בדיקה אוטומטית).")
    w("- rolling origin: אותו תהליך לשנות מבחן " + ", ".join(o["rolling"].keys()) + " (סעיף 8).")
    w("")
    w("**רשת ואזור נבחן:** רשת ITM במטרים (itm_x/itm_y), תאים של "
      + "/".join(str(c) for c in o["config"]["cell_sizes_m"]) + " מ'. "
      f"**מכנה השטח** ל-PAI ול\"אחוז שטח\" = כל ריבועי {o['config']['block_m'] // 1000} ק\"מ שהייתה בהם לפחות תאונה אחת "
      f"בשנות האימון ({M['region_km2']:.0f} קמ\"ר במבחן). הצדקה: (1) זה קירוב לשטח המיושב/הסלול שבו הולכי רגל נפגעים "
      "בכלל - שטח ישראל כולו (כולל מדבר ושטחים פתוחים) היה מנפח את ה-PAI באופן מלאכותי; (2) הוא נגזר מנתוני האימון "
      f"בלבד; (3) הוא זהה לכל גדלי התא, כך שאחוז שטח = אותם קמ\"ר. תאונת מבחן מחוץ לאזור נספרת כהחטאה לכל המודלים "
      f"({pct(P['share_test_outside_region'])} מתאונות {M['test_year']}). כדי להמיר: 1% מהאזור = "
      f"{M['region_km2'] / 100:.1f} קמ\"ר.")
    w("")
    w("**מודלים** (כולם שקופים, numpy בלבד):")
    w("- אחיד: כל התאים שווים (= בחירה אקראית; מחושב כתוחלת).")
    w("- התמדה: סכום התאונות בתא בכל שנות האימון.")
    w("- שנה קודמת: רק השנה האחרונה.")
    w("- ספירה עם דעיכה: Σ דעיכה^(גיל) × ספירה; דעיכה נבחרת מ-" + ", ".join(f"{d:g}" for d in o["config"]["decays"]) + ".")
    w("- סכום 3x3 שכנים: הציון שהאפליקציה מחשבת (`accidents_near`), כאן ברשת ITM כדירוג רציף.")
    w("- החלקה גאוסית (KDE): ספירות עם דעיכה, מוחלקות בגרעין גאוסי; σ נבחר מ-"
      + ", ".join(str(b) for b in o["config"]["bandwidths_m"]) + " מ' (רק σ ≥ חצי תא).")
    w("- רגרסיית פואסון (GLM): log(קצב) ~ log1p(ספירה בתא), log1p(טבעת 3x3), log1p(טבעת 7x7), log1p(שנה אחרונה). "
      "אימון: לכל שנת יעד t בתוך שנות האימון, תכונות משנים < t בלבד (stacked). IRLS.")
    w(f"- כלל המוקד של האפליקציה: שחזור מדויק של `server/safety_intel.py` - תאי {o['config']['app_rule']['cell_deg']}° "
      f"(lat/lon), סכום 3x3, סף = max({o['config']['app_rule']['min_count']}, ערך העשירון ה-{o['config']['app_rule']['pct']} "
      "על התאים המאוכלסים ושכניהם). מאומן כאן על שנות האימון בלבד (באפליקציה: על כל 5 השנים). השטח שלו מחושב "
      f"ב-ITM (פולינום lat/lon→ITM, RMS {M['latlon_to_itm_fit']['rms_m']:.2f} מ').")
    w("- לתחזית ספירות: קצב לתא = (S + a·m)/(n + a) - ממוצע אחורי גמא-פואסון עם prior אחיד m (קצב ממוצע באזור), "
      "a = \"שנים מדומות\". בלי prior (a→0) להתמדה יש קצב 0 בתאים ריקים ו-log-likelihood של מינוס אינסוף.")
    w("")
    w("**מדדים:** hit rate = שיעור תאונות שנת המבחן (מכל התאונות) שנפלו בשטח שהמודל בחר; PAI = hit rate / אחוז השטח "
      "(Chainey et al. 2008). שוויון ציונים בגבול השטח מחולק יחסית (תוחלת של שבירה אקראית). log-likelihood ו-deviance "
      "פואסון ממוצעים לתא באזור; כיול: סכום חזוי מול נצפה לפי עשירוני התחזית. "
      f"רווחי סמך 95%: cluster bootstrap ({o['config']['n_boot']} חזרות, seed {o['config']['seed']}) על ריבועי 1 ק\"מ - "
      "שמרני יותר מ-bootstrap של תאונות בודדות כי תאונות סמוכות תלויות. השוואות זוגיות על אותן דגימות. "
      "קריטריון הבחירה: ממוצע ה-hit rate ב-1%/5%/10% בשנת הבחירה; חוזק ה-prior: log-likelihood בשנת הבחירה.")
    w("")
    # ---------------- נתונים
    w("## 3. נתונים")
    w("")
    w(f"`research/samples/israel/derived/pedestrian_accidents_2020-2024.csv` - {st['rows']:,} תאונות הולכי רגל עם נפגעים "
      "(הלמ\"ס PUF). לכל השורות יש קואורדינטות ITM.")
    w("")
    statuses = sorted({s for y in st["by_year_status"].values() for s in y}, key=int)
    w("| שנה | " + " | ".join(f"STATUS_IGUN={s}" for s in statuses) + " | סה\"כ |")
    w("|---|" + "---|" * (len(statuses) + 1))
    for y, d in st["by_year_status"].items():
        w(f"| {y} | " + " | ".join(f"{d.get(s, 0):,}" for s in statuses) + f" | {sum(d.values()):,} |")
    w("")
    w("| סטטוס | שורות | נקודות ייחודיות | שורות על נקודה משותפת | מקסימום שורות בנקודה אחת |")
    w("|---|---|---|---|---|")
    for s, d in st["coords_by_status"].items():
        w(f"| {s} | {d['rows']:,} | {d['unique_xy']:,} | {d['rows_on_shared_xy']:,} | {d['max_rows_same_xy']} |")
    w("")
    w("**טיפול בסטטוס העיגון:** משמעות הקודים לא אומתה (`research/data_inventory_israel.md` §2.1 - השערה: 1 = עיגון מדויק, "
      "2–3 = מרכז רחוב/יישוב; קוד 4 לא מתועד כלל). בסטטוס 2 רוב השורות יושבות על נקודות משותפות - סימן לעיגון למרכז. "
      "נקודה כזו \"חוזרת\" כל שנה באותו מקום מסיבות טכניות ומנפחת את ההתמדה באופן מלאכותי. לכן **הניתוח הראשי = "
      f"STATUS_IGUN=1 בלבד** (גם באימון וגם במבחן), וניתוח רגישות עם כל הסטטוסים (כמו השכבה באפליקציה) בסעיף 9.")
    w("")
    # ---------------- בחירה
    w("## 4. בחירת היפר-פרמטרים (על שנת הבחירה בלבד)")
    w("")
    w(f"אימון {sel['train_years'][0]}–{sel['train_years'][-1]} → בחירה על {sel['val_year']}. ערך = ממוצע hit rate "
      "ב-1%/5%/10% משטח.")
    w("")
    w("| גודל תא | " + " | ".join(FAMILY_HE[f] for f in FAMILIES) + " |")
    w("|---|" + "---|" * len(FAMILIES))
    for c, ch in sel["chosen"].items():
        cells = []
        for f in FAMILIES:
            if f in ch:
                p = params_he(f, ch[f])
                cells.append(f"{pct(ch[f]['val_objective'])}" + (f" ({p})" if p else ""))
            else:
                cells.append("—")
        w(f"| {c} מ' | " + " | ".join(cells) + " |")
    w("")
    w(f"**גודל התא הראשי שנבחר: {pc} מ'** (הערך הגבוה ביותר בשנת הבחירה, בלי לראות את {M['test_year']}). "
      "תוצאות המבחן לשאר הגדלים מוצגות בסעיף 5.2 לשקיפות בלבד - לא נבחר מהן דבר. "
      "קריטריון הבחירה (ממוצע של 1%/5%/10%) נקבע לפני הריצה ולא שונה אחריה; הוא נותן משקל גם ל-10%, "
      "ולכן יכול להעדיף החלקה/רגרסיה שטובות דווקא בשטח רחב. שינוי שלו עכשיו, אחרי שראינו את 2024, היה דליפה.")
    w("")
    # ---------------- תוצאות
    w(f"## 5. תוצאות על {M['test_year']} (מבחן יחיד)")
    w("")
    w(f"### 5.1 תא ראשי {pc} מ'")
    w("")
    w(f"תאונות מבחן: {P['n_test']:,} (בתוך האזור: {P['test_in_region']:,}). אזור: {P['region_km2']:.0f} קמ\"ר, "
      f"{P['region_cells']:,} תאים; {pct(P['share_region_cells_with_history'])} מהתאים עם היסטוריה.")
    w("")
    w("| מודל | פרמטרים | HR@1% | PAI@1% | HR@5% | PAI@5% | HR@10% | PAI@10% |")
    w("|---|---|---|---|---|---|---|---|")
    for f in FAMILIES:
        if f in mods:
            m = mods[f]
            w(f"| {FAMILY_HE[f]} | {params_he(f, m['params'])} | " + " | ".join(
                f"{ci_pct(m['hit_rate'][c])} | {ci_num(m['pai'][c], 1)}" for c in covs) + " |")
    w("")
    w(f"{FAMILY_HE['app_rule']} (כלל בינארי - שטח קבוע {pct(app['frac_of_region'], 2)} מהאזור): HR {ci_pct(app['hit_rate'])}, "
      f"PAI {ci_num(app['pai'], 1)}. השוואה באותו שטח - סעיף 6.1.")
    w("")
    w("סוגריים = רווח סמך 95% (cluster bootstrap). HR = hit rate.")
    w("")
    w("**תחזית ספירות (תא ראשי):**")
    w("")
    w("| מודל | LL לתא | deviance לתא | רווח LL מול אחיד (nats לתאונה) | סכום חזוי | נצפה באזור |")
    w("|---|---|---|---|---|---|")
    for f in FAMILIES:
        if f in mods:
            m = mods[f]
            w(f"| {FAMILY_HE[f]} | {ci_num(m['loglik_per_cell'], 4)} | {m['deviance_per_cell']:.4f} | "
              f"{ci_num(m['ll_gain_vs_uniform_per_accident'], 3)} | {m['pred_total']:.0f} | {m['obs_total_in_region']:.0f} |")
    w("")
    w("### 5.2 כל גדלי התא (לשקיפות; הבחירה נעשתה בסעיף 4)")
    w("")
    w("| תא | תאונות בתא בלי היסטוריה | " + " | ".join(f"{FAMILY_HE[f]} HR@5%" for f in ("uniform", "persistence", "kde", "glm", "neighbor3x3")) + " |")
    w("|---|---|" + "---|" * 5)
    for c, r in M["by_cell"].items():
        w(f"| {c} מ' | {pct(r['share_test_zero_history'])} | " + " | ".join(
            ci_pct(r["models"][f]["hit_rate"]["0.05"]) if f in r["models"] else "—"
            for f in ("uniform", "persistence", "kde", "glm", "neighbor3x3")) + " |")
    w("")
    w("| תא | " + " | ".join(f"{FAMILY_HE[f]} HR@1%" for f in ("persistence", "kde", "glm")) + " |")
    w("|---|" + "---|" * 3)
    for c, r in M["by_cell"].items():
        w(f"| {c} מ' | " + " | ".join(ci_pct(r["models"][f]["hit_rate"]["0.01"]) if f in r["models"] else "—"
                                     for f in ("persistence", "kde", "glm")) + " |")
    w("")
    # ---------------- השוואות
    w("## 6. השוואות זוגיות מול התמדה (תא ראשי)")
    w("")
    w("הפרש ב-hit rate (נקודות אחוז), רווח סמך 95% זוגי. מובהק = רווח הסמך לא כולל 0.")
    w("")
    w("| מודל | " + " | ".join(f"Δ ב-{pct(float(c), 0)}" for c in covs) + " |")
    w("|---|" + "---|" * len(covs))
    for f in FAMILIES:
        if f != "persistence" and (f, covs[0]) in cmp:
            w(f"| {FAMILY_HE[f]} | " + " | ".join(ci_pp(cmp[(f, c)]) + (" ✱" if _sig(cmp[(f, c)]) else "") for c in covs) + " |")
    w("")
    w("✱ = מובהק.")
    w("")
    w(f"### 6.1 כלל המוקד של האפליקציה מול מודלים באותו שטח ({pct(app['frac_of_region'], 2)} מהאזור)")
    w("")
    w(f"- הכלל מסמן {app['flagged_cells']:,} תאים של 0.001° ({app['flagged_km2']:.1f} קמ\"ר; "
      f"{pct(app['share_flagged_area_in_region'])} מהשטח המסומן בתוך האזור הנבחן), ותופס {ci_pct(app['hit_rate'])}.")
    w("")
    w("| מודל | HR באותו שטח | הפרש מול כלל האפליקציה |")
    w("|---|---|---|")
    for f in FAMILIES:
        if f in mods:
            d = app["diff_model_minus_app_at_same_area"][f]
            w(f"| {FAMILY_HE[f]} | {ci_pct(mods[f]['hit_rate_at_app_area'])} | {ci_pp(d)}{' ✱' if _sig(d) else ''} |")
    w("")
    # ---------------- כיול
    w("## 7. כיול תחזית הספירות (תא ראשי)")
    w("")
    w("עשירונים לפי הקצב החזוי (D1 = הנמוך). חזוי/נצפה = סכום תאונות לשנת המבחן בסל. תאים עם קצב חזוי זהה "
      "(למשל כל התאים בלי היסטוריה) נכנסים לאותו סל, ולכן עשירונים מתמזגים (למשל D1-D9). "
      "העניין העיקרי בסלים העליונים ובזנב (1% ו-0.1% העליונים). הסכום החזוי לא מתוקן למגמה ארצית.")
    w("")
    for f, rows in P.get("calibration", {}).items():
        p = params_he(f, mods[f]["params"])
        w(f"**{FAMILY_HE[f]}**" + (f" ({p})" if p else ""))
        w("")
        w("| סל | תאים | חזוי | נצפה | יחס נצפה/חזוי |")
        w("|---|---|---|---|---|")
        for r in rows:
            ratio = r["obs_sum"] / r["pred_sum"] if r["pred_sum"] > 0 else float("nan")
            w(f"| {r['bin']} | {r['cells']:,} | {r['pred_sum']:.1f} | {r['obs_sum']:.0f} | {ratio:.2f} |")
        tp, to = mods[f]["pred_total"], mods[f]["obs_total_in_region"]
        w(f"| סה\"כ באזור | {P['region_cells']:,} | {tp:.1f} | {to:.0f} | {to / tp:.2f} |")
        w("")
        dec = [r for r in rows if r["bin"].startswith("D")]
        if f == "glm" and dec and dec[-1]["pred_sum"] > 0 and dec[-1]["obs_sum"] / dec[-1]["pred_sum"] > 1.3:
            w("רגרסיית הפואסון \"שטוחה\" מדי: מנבאת יותר מדי בתאים הקרים ופחות מדי בסל העליון. השערה (לא נבדקה): "
              "היא אומנה על יעדים עם היסטוריה קצרה יותר (stacking - שנה אחת או שתיים של תכונות), ושם הספירות רועשות "
              "יותר והמקדמים מתכווצים; בתחזית היא מקבלת ממוצע של ארבע שנים.")
            w("")
    w("כשהיחס הכולל נמוך מ-1, חלק מה\"תחזית-יתר\" בסלים העליונים הוא פשוט ירידה ארצית במספר התאונות בשנת המבחן; "
      "השאר הוא רגרסיה לממוצע (תא עם הרבה תאונות בעבר מקבל פחות בשנה הבאה).")
    w("")
    # ---------------- rolling
    w("## 8. Rolling origin (בחירה על Y-1, מבחן על Y)")
    w("")
    w("| שנת מבחן | אימון | תא שנבחר | בלי היסטוריה | אחיד HR@5% | התמדה HR@5% | KDE HR@5% | GLM HR@5% | התמדה HR@1% | KDE HR@1% | כלל האפליקציה (שטח, HR) |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for y, r in o["rolling"].items():
        h = r["hit_rate"]
        g = lambda f, c: ci_pct(h[f][c]) if f in h else "—"  # noqa: E731
        w(f"| {y} | {r['train_years'][0]}–{r['train_years'][-1]} | {r['primary_cell_m']} מ' | "
          f"{pct(r['share_test_zero_history'])} | {g('uniform', '0.05')} | {g('persistence', '0.05')} | {g('kde', '0.05')} | "
          f"{g('glm', '0.05')} | {g('persistence', '0.01')} | {g('kde', '0.01')} | "
          f"{pct(r['app_rule']['frac_of_region'], 2)}, {ci_pct(r['app_rule']['hit_rate'])} |")
    w("")
    w("פרמטרים שנבחרו בכל שנה: " + "; ".join(
        f"{y}: KDE ({params_he('kde', r['chosen'].get('kde', {}))}), ספירה עם דעיכה "
        f"({params_he('recency', r['chosen'].get('recency', {}))})"
        for y, r in o["rolling"].items()) + ".")
    w("")
    w("הפרש KDE − התמדה ב-5%: " + "; ".join(
        f"{y}: " + next((ci_pp(c["diff_hr"]) for c in r["comparisons_vs_persistence"]
                         if c["a"] == "kde" and abs(c["coverage"] - 0.05) < 1e-9), "—")
        for y, r in o["rolling"].items()) + ".")
    w("")
    # ---------------- רגישות
    w("## 9. רגישות")
    w("")
    S = o["sensitivity_all_status"]
    sh = S["hit_rate"]
    w(f"**כל סטטוסי העיגון (כמו השכבה באפליקציה):** אימון {S['n_train']:,}, מבחן {S['n_test']:,}, תא {S['primary_cell_m']} מ', "
      f"בלי היסטוריה {pct(S['share_test_zero_history'])}. HR@5%: " + "; ".join(
          f"{FAMILY_HE[f]} {ci_pct(sh[f]['0.05'])}" for f in ("uniform", "persistence", "kde", "glm") if f in sh)
      + f". HR@1%: " + "; ".join(f"{FAMILY_HE[f]} {ci_pct(sh[f]['0.01'])}" for f in ("persistence", "kde", "glm") if f in sh)
      + f". כלל האפליקציה: שטח {pct(S['app_rule']['frac_of_region'], 2)}, סף {S['app_rule']['threshold']:g}, "
      f"HR {ci_pct(S['app_rule']['hit_rate'])}.")
    w("")
    for name, he in (("severe", "קשות/קטלניות בלבד"), ("night", "לילה בלבד")):
        sub = M["test_subsets"][name]
        w(f"**מבחן על {he}** (n={sub['n']}; אותם מודלים, מאומנים על כל התאונות): HR@5%: " + "; ".join(
            f"{FAMILY_HE[f]} {ci_pct(sub['models'][f]['0.05'])}" for f in ("uniform", "persistence", "kde", "glm")
            if f in sub["models"]) + f"; כלל האפליקציה {ci_pct(sub['app_rule'])}.")
        w("")
    # ---------------- משתנים מסבירים
    w("## 10. משתנים מסבירים מ-OSM (4 ערים)")
    w("")
    C = o["covariates"]
    if not C.get("available"):
        w(f"לא בוצע: {C.get('reason')}.")
    else:
        w(f"`models/risk_factors_grid.json.gz` מכסה רק את {', '.join(C['cities'])}. תא {C['cell_m']} מ' (רשת המקור היא "
          f"0.001° ≈ 100 מ'; מרכזי התאים הומרו ל-ITM, RMS {C['latlon_to_itm_fit']['rms_m']:.2f} מ', ולכן תא של 250 מ' "
          "מקטין שגיאות שיוך). משתנים: " + ", ".join(f"{g} ({'+'.join(m)})" for g, m in C["features"].items())
          + ", ועוד אפקט קבוע לעיר. **הוצאו:** " + "; ".join(f"{k} - {v}" for k, v in C["excluded"].items()) + ".")
        w("")
        w(f"אזור מכוסה: {C['covered_region_km2']:.0f} קמ\"ר; תאונות {M['test_year']} בערים: {C['n_test_in_cities']}. "
          "אחוזי השטח כאן הם מתוך האזור המכוסה.")
        w("")
        w("| מודל | HR@1% | HR@5% | HR@10% | LL לתא |")
        w("|---|---|---|---|---|")
        names = {"uniform": "אחיד", "persistence": "התמדה", "kde": "KDE", "glm_past": "GLM עבר + עיר",
                 "glm_past_cov": "GLM עבר + עיר + OSM"}
        for k, m in C["models"].items():
            w(f"| {names.get(k, k)} | " + " | ".join(pct(m["hit_rate"][c]) for c in covs) + f" | {m['loglik_per_cell']:.4f} |")
        w("")
        w("השוואות זוגיות ב-5%: " + "; ".join(f"{k}: {ci_pp(v)}{' ✱' if _sig(v) else ''}" for k, v in C["paired_5pct"].items())
          + f". רווח LL לתא של OSM מעבר לעבר: {ci_num(C['ll_gain_cov_vs_past_per_cell'], 5)}.")
        if "glm_past_cov" in C["models"] and "beta" in C["models"]["glm_past_cov"]:
            b = C["models"]["glm_past_cov"]["beta"]
            w("")
            w("מקדמי ה-GLM עם OSM: " + ", ".join(f"{k}={v:+.3f}" for k, v in b.items()) + ".")
        w("")
        w("**הסתייגות דליפה:** תמונת ה-OSM היא מ-" + ", ".join(sorted({v for v in C["osm_snapshot"].values() if v}))
          + " - אחרי שנת המבחן. תחנות אוטובוס ועסקים משתנים לאט, אבל זו עדיין \"הצצה לעתיד\" קטנה; "
          "התוצאה כאן היא חסם עליון לתרומת OSM, לא הערכה נקייה.")
    w("")
    # ---------------- מגבלות
    w("## 11. מגבלות")
    w("")
    w("- **רק תאונות עם נפגעים הולכי רגל** שדווחו לשטרה ונכנסו ל-PUF של הלמ\"ס. תאונות בלי נפגעים, \"כמעט תאונות\" "
      "ותאונות שלא דווחו - לא כאן. ייתכן תת-דיווח לא אחיד בין אזורים.")
    w("- **עיגון:** הניתוח הראשי מסתמך על STATUS_IGUN=1, שמשמעותו לא אומתה רשמית. שגיאות מיקום של עשרות מטרים "
      "פוגעות בעיקר בתא של 100 מ'.")
    w("- **אין חשיפה:** לא ידוע כמה הולכי רגל עוברים בכל תא. \"מוקד\" יכול לשקף פשוט נפח הולכי רגל/תנועה גבוה, "
      "לא מקום מסוכן יותר להולך רגל בודד. אי אפשר להסיק מכאן סיכון אישי לעבור ברחוב.")
    w("- **תאונות דרכים, לא אלימות:** אין כאן שום טענה לגבי חיזוי פשיעה או אלימות; ספרות ה-predictive policing "
      "שימשה רק למדדים (hit rate, PAI).")
    w("- **חמש שנים בלבד** (כולל 2020–2021 עם שינויי תנועה בתקופת הקורונה); ארבע שנות אימון לכל היותר. "
      "המודלים לא מתקנים מגמה ארצית (מספר התאונות משתנה משנה לשנה), ולכן הסכום החזוי לא תואם בדיוק את הנצפה.")
    w("- **מכנה השטח** נגזר מהנתונים עצמם (ריבועי 1 ק\"מ עם תאונות באימון). מכנה אחר (למשל שטח בנוי) ישנה את ה-PAI "
      "אבל לא את ההשוואה בין המודלים, כי כולם נמדדים על אותו שטח.")
    w("- תאונה יחידה בשנה בתא היא רוב המקרים - האות דליל מאוד, ורווחי הסמך רחבים בהתאם.")
    w("")
    # ---------------- המלצות
    w("## 12. המלצות לשכבת המוקדים באפליקציה")
    w("")
    for line in recommendations(o):
        w(f"- {line}")
    w("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return path


def recommendations(o):
    """המלצות שנגזרות מהתוצאות (כל מספר מגיע מהריצה)."""
    M = o["main"]
    pc = M["selection"]["primary_cell_m"]
    P = M["by_cell"][str(pc)]
    mods = P["models"]
    app = M["app_rule"]
    cmp = {(c["a"], f"{c['coverage']:g}"): c["diff_hr"] for c in M["comparisons_vs_persistence"]}
    out = []
    d_app = app["diff_model_minus_app_at_same_area"]
    cand = [f for f in mods if f != "uniform"]
    best_app = max(cand, key=lambda f: mods[f]["hit_rate_at_app_area"]["est"])
    simple = "persistence" if best_app in ("persistence", "recency") else best_app
    d = d_app[simple]
    if _sig(d) and d["est"] > 0:
        out.append(
            f"**להחליף את סכום ה-3x3 (~300 מ') בדירוג לפי {FAMILY_HE[simple]} בתא של {pc} מ', באותו תקציב שטח:** "
            f"באותו שטח שהכלל הנוכחי מסמן ({pct(app['frac_of_region'], 2)} מהאזור) זה תופס "
            f"{ci_pct(mods[simple]['hit_rate_at_app_area'])} מול {ci_pct(app['hit_rate'])} - הפרש {ci_pp(d)}. "
            "בפועל: ספירה ברשת ITM של 100 מ' (או תא ה-0.001° הקיים בלי לסכום שכנים), ו\"מוקד\" = התאים העליונים עד תקציב "
            "שטח קבוע, במקום עשירון של סכום שכנים.")
    else:
        out.append("**לא לשנות את מנגנון הדירוג רק בשביל ביצועים:** באותו שטח אף מודל לא עקף את הכלל הנוכחי באופן מובהק.")
    k10 = [f for f in ("glm", "kde") if (f, "0.1") in cmp and _sig(cmp[(f, "0.1")]) and cmp[(f, "0.1")]["est"] > 0]
    k1 = [f for f in k10 if (f, "0.01") in cmp and cmp[(f, "0.01")]["est"] < 0]
    if k10:
        f = k10[0]
        out.append(
            f"**החלקה רק לשכבה רחבה ומשנית, לא למוקדים:** המודל {FAMILY_HE[f]} עקף את ההתמדה רק ב-10% משטח "
            f"({ci_pp(cmp[(f, '0.1')])})" + (f" והיה גרוע ממנה ב-1% ({ci_pp(cmp[(f, '0.01')])})" if f in k1 else "")
            + ". אם רוצים שכבת \"זהירות\" רחבה (תאים בלי היסטוריה ליד תאים עם היסטוריה) - להשתמש בו שם; "
            "לסימון מוקד נקודתי - בספירה הגולמית.")
    rec_sig = any(_sig(cmp[("recency", c)]) for c in ("0.01", "0.05", "0.1") if ("recency", c) in cmp)
    if not rec_sig:
        out.append(
            "**לא להוסיף דעיכה בזמן; להשתמש בכל השנים במשקל שווה ולעדכן את השכבה כל שנה:** דעיכה לא שיפרה, ו\"שנה "
            f"אחרונה בלבד\" גרועה בהרבה ({ci_pp(cmp[('last_year', '0.05')])} ב-5%). כדאי לבדוק בניסוי הזה (גרסה הבאה) "
            "גם הוספת 2017–2019 (קיימות ב-data.gov.il לפי data_inventory_israel.md) - ב-rolling origin שיעור התאונות "
            "בתאים בלי היסטוריה ירד ככל שנוספו שנים.")
    out.append(
        f"**לנסח את המוקד כ\"היו כאן תאונות הולכי רגל\" ולא כתחזית, ולא לרמוז ש\"לא מסומן = בטוח\":** "
        f"{pct(P['share_test_zero_history'])} מתאונות {M['test_year']} קרו בתאי {pc} מ' בלי שום היסטוריה, והכלל הנוכחי "
        f"תופס רק {ci_pct(app['hit_rate'])} מכלל תאונות השנה הבאה. השכבה היא \"סיכון תנועתי להולכי רגל\" בלי נתוני חשיפה - במשקל נמוך, "
        "ובנפרד מכל ציון של בטיחות אישית/אלימות.")
    out.append(
        "**לבדוק סינון של עיגון לא מדויק (STATUS_IGUN≠1) מהשכבה:** בסטטוס 2 רוב השורות על נקודות משותפות (כנראה מרכז "
        "רחוב/יישוב), ו\"מוקד\" שנוצר משם הוא ארטיפקט עיגון. ה-HR הגבוה יותר כשכוללים את כל הסטטוסים "
        f"({ci_pct(o['sensitivity_all_status']['hit_rate']['persistence']['0.05'])} ב-5%) צפוי בדיוק בגלל נקודות שחוזרות, "
        "ולכן אינו ראיה לאיכות.")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="ניסוי חיזוי גיאוגרפי הוגן לתאונות הולכי רגל")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--csv", default=CSV_PATH)
    r.add_argument("--json", default=OUT_JSON)
    r.add_argument("--report", default=OUT_REPORT)
    rp = sub.add_parser("report", help="בונה מחדש את ה-md מה-JSON הקיים (בלי לחשב מחדש)")
    rp.add_argument("--json", default=OUT_JSON)
    rp.add_argument("--report", default=OUT_REPORT)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run_all(a.csv, a.json, a.report)
    elif a.cmd == "report":
        with open(a.json, encoding="utf-8") as fh:
            print(report(json.load(fh), a.report))


if __name__ == "__main__":
    main()
