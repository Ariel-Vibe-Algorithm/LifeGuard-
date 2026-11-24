"""Clean the raw crime-by-statistical-area file into dashboard/data/cleaned.csv.

Run from the project root or from dashboard/:
    python dashboard/clean.py

Every fix below is documented, with its reason, in dashboard/DATA_CARD.md.
The script prints a cleaning log; the numbers in DATA_CARD.md come from that log.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "research" / "datasets" / "crime_by_stat_area_summary.csv"
OUT = Path(__file__).resolve().parent / "data" / "cleaned.csv"

HIGH_RISK_RATIO = 2.0  # "high risk" = at least 2x the median rate of the same city

# Columns computed from 2024+2025 together. They contain the 2025 answer, so they are dropped (leakage).
LEAKY = ["cases_total", "cases_mean_annual", "rate_per_1000", "city_median_rate", "ratio_city_median",
         "category_default", "category_with_around_median", "ratio_national_median",
         "category_vs_national_median"]


def dedupe_unit(unit: str) -> str:
    """'3000:2311+2312+2312' -> '3000:2311+2312'. Used only to DETECT repeated sub-area codes."""
    city, _, parts = unit.partition(":")
    seen: list[str] = []
    for p in parts.split("+"):
        if p not in seen:
            seen.append(p)
    return f"{city}:{'+'.join(seen)}"


def clean(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[tuple[str, int, int]]]:
    log: list[tuple[str, int, int]] = []  # (step, rows removed, rows left)
    df = raw.copy()
    log.append(("raw file", 0, len(df)))

    # 1. Rows with no population (no census match): a rate per resident cannot be computed.
    n = len(df); df = df[df["status"] != "no_census_match"]
    log.append(("drop no_census_match (population missing)", n - len(df), len(df)))

    # 2. Rows with 0 cases in both years despite 1,000-51,000 residents: the area was never
    #    matched in the police data. This is a missing value, not a safe area.
    n = len(df); df = df[df["status"] != "no_police_records"]
    log.append(("drop no_police_records (0 cases = missing, not safe)", n - len(df), len(df)))

    # 3. Areas under 1,000 residents: one case moves the rate too much.
    n = len(df); df = df[df["status"] != "pop_below_min"]
    log.append(("drop pop_below_min (< 1,000 residents, unstable rate)", n - len(df), len(df)))

    assert (df["status"] == "ok").all(), "unexpected status value left after cleaning"
    df = df.copy()

    # 4. Repeated sub-area code inside a merged unit (2312 listed twice in Isawiya/a-Tur, Jerusalem).
    #    Keep and mark: the population of that sub-area may have been counted twice, and the source
    #    aggregation cannot be verified here, so nothing is corrected. The row keeps its original code
    #    and gets dup_stat_area_flag = 1; DATA_CARD.md lists it as an open issue.
    df["dup_stat_area_flag"] = (df["unit"].map(dedupe_unit) != df["unit"]).astype(int)
    log.append((f"flag repeated sub-area code ({int(df['dup_stat_area_flag'].sum())} row kept and marked, not corrected)",
                0, len(df)))

    # 5. Text: trim stray spaces; fill the 3 missing area names with a visible placeholder.
    df["area_name"] = df["area_name"].str.strip().fillna("(ללא שם)")
    df["yeshuv_name"] = df["yeshuv_name"].str.strip()

    # 6. Consistency checks (no rows removed; the script fails loudly if they break).
    assert df["unit"].is_unique, "duplicate unit codes"
    assert not df.duplicated().any(), "exact duplicate rows"
    assert (df[["cases_2024", "cases_2025"]] >= 0).all().all(), "negative counts"
    assert (raw["cases_total"] == raw["cases_2024"] + raw["cases_2025"]).all(), "cases_total mismatch"

    # 7. Drop leakage columns, derive per-year features and the answer column.
    df = df.drop(columns=LEAKY)
    flags = df["flags"].fillna("")
    out = pd.DataFrame({
        "city": df["yeshuv_name"],
        "yeshuv": df["yeshuv"],
        "unit": df["unit"],
        "area_name": df["area_name"],
        "pop": df["pop"].astype(int),
        "cases_2024": df["cases_2024"],
        "cases_2025": df["cases_2025"],
    })
    # Medians are per city and per year, computed on the retained (post-cleaning) cohort only.
    for y in (2024, 2025):
        out[f"rate_{y}"] = (out[f"cases_{y}"] / out["pop"] * 1000).round(2)
        out[f"city_median_{y}"] = out.groupby("city")[f"rate_{y}"].transform("median").round(2)
        out[f"ratio_{y}"] = (out[f"rate_{y}"] / out[f"city_median_{y}"]).round(3)
    out["is_visitor_area"] = flags.str.contains("visitor_traffic").astype(int)
    out["is_small_pop"] = flags.str.contains("small_population").astype(int)
    out["dup_stat_area_flag"] = df["dup_stat_area_flag"].values
    out["high_risk_2025"] = (out["ratio_2025"] >= HIGH_RISK_RATIO).astype(int)
    log.append(("drop 9 leakage columns, add rate/ratio per year + answer column", 0, len(out)))
    return out.reset_index(drop=True), log


def main() -> None:
    raw = pd.read_csv(RAW)
    cleaned, log = clean(raw)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(OUT, index=False, encoding="utf-8-sig")  # utf-8-sig: Hebrew opens correctly in Excel

    print(f"raw: {RAW.relative_to(ROOT)}  ->  {OUT.relative_to(ROOT)}")
    for step, removed, left in log:
        print(f"  {step:<75} removed {removed:>4}   rows left {left:>5}")
    print(f"columns: {raw.shape[1]} -> {cleaned.shape[1]}")
    print(f"high_risk_2025: {cleaned['high_risk_2025'].sum()} of {len(cleaned)} ({cleaned['high_risk_2025'].mean():.1%})")


if __name__ == "__main__":
    main()
