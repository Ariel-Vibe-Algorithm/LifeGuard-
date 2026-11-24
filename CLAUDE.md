# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## What this is

LifeGuard is a personal-safety app (Windows desktop prototype in Python/Tkinter, a Python server, an Android app)
built in a vibe-coding course at Ariel University. The course deliverable on top of it is a **data project**:
crime by statistical area in 10 Israeli cities (2024–2025), cleaned, explored, and shown in a Streamlit dashboard.

**Milestone 2 (current submission):** what is graded is `main` of the team repo in the course org
(`github.com/Ariel-Vibe-Algorithm/LifeGuard-`), deadline 2026-11-25 14:00. The deliverable is the **Jupyter notebook**
(`notebook/eda_notebook.ipynb`) + clean data + Data Card + README (problem, y, source, fixes, team table, one insight,
baseline). The Streamlit dashboard is a bonus, not required for this milestone. Every graph in the notebook must be
followed by a written conclusion, and the notebook must run top to bottom (Run All) from a fresh clone.
Every team member needs at least one commit under their own GitHub account.

## Course deliverable – where things are

| Path | What |
|---|---|
| `research/datasets/crime_by_stat_area_summary.csv` | Raw data (1,126 rows, 19 cols). **Never edit by hand**; it is the cleaning input |
| `dashboard/clean.py` | Raw → `dashboard/data/cleaned.csv` (530 rows). Every step logged; asserts fail loudly |
| `dashboard/data/cleaned.csv` | Cleaned data the dashboard reads. Regenerate with `python dashboard/clean.py`, don't hand-edit |
| `dashboard/app.py` | Streamlit dashboard, two separate parts: (A) retrospective 2025 – what happened; (B) prediction of 2025 from 2024 features only. 8 metric cards, 4 Plotly charts, filters, baseline table, data table |
| `dashboard/DATA_CARD.md` | Source, row counts, every cleaning fix + reason, open issues |
| `dashboard/requirements.txt` | Dashboard deps only (streamlit, pandas, plotly) |
| `notebook/eda_plan.md`, `notebook/eda_notebook.ipynb` | EDA plan (7 sections) and the notebook that executes it, reading the raw CSV |
| `notebook/requirements.txt` | Notebook deps (pandas, numpy, matplotlib, jupyter) |
| `INSIGHTS.md` | The insight + baseline comparison, with numbers |

Commands (from the repo root):
```
pip install -r dashboard/requirements.txt
python dashboard/clean.py
streamlit run dashboard/app.py
```

## Analysis conventions (do not break these)

- **Answer column:** `high_risk_2025` = 1 if the 2025 rate per 1,000 residents is ≥ 2× the median of the same city in 2025.
- **Features come from 2024 only.** Any column computed from both years (`cases_total`, `rate_per_1000`,
  `ratio_city_median`, all `category_*`, flags `high_ratio_check_land_use` / `low_case_count`) is **leakage** and must not be used.
- **Zero cases in a populated area = missing, not safe.** Never present a dropped area as "safe".
- Work with **rate per resident / ratio to city median**, not raw counts (counts track area size, r ≈ 0.98 between years).
- The persistence-rule threshold (2.0) is fixed by the definition, not tuned on the test year.
- City medians are per city **and per year**, computed on the retained 530-row cohort (not on the 1,126 raw rows).
- Row `3000:2311+2312+2312` is **kept and marked** (`dup_stat_area_flag = 1`), not corrected: its population may be double-counted and cannot be verified. Don't describe it as fixed.
- Paths in `clean.py` / `app.py` are resolved from `Path(__file__)`, never the working directory. No hardcoded numbers in the dashboard.
- Numbers quoted in `INSIGHTS.md` / `DATA_CARD.md` must match `python dashboard/clean.py` output and the dashboard.
- User-facing text (dashboard, docs, notebook) is in **Hebrew**; code, identifiers and comments in English.

## The rest of the repo (existing LifeGuard app – leave unchanged unless asked)

- Root `*.py`: desktop app (`main.py` entry point, `engine.py` state machine, `ui_theme.py`, `*_ui.py` screens, `*_module.py` services).
- `server/`: backend (alerts, community, safety intel); `deploy/`: Docker/Caddy (`.env.example` only — **never commit `deploy/.env` or `deploy/secrets/`**).
- `android/`: Android + Wear OS app (Gradle). `tests/`: pytest + web e2e.
- `research/`, `research_tools/`: research reports and the scripts that built the data (`research_tools/israel_layer.py` built the raw CSV).
- `first_aid/`, `safe_places/`, `content/`, `models/`, `design/`, `tools/`: app content, models and build helpers.
- Root `requirements.txt` is for the desktop app — **don't add dashboard deps there**.

## Repo hygiene

- `.gitignore` ignores every `data/` folder (user data); `dashboard/data/` is explicitly re-included. Keep it that way.
- No secrets, local databases (`*.db`), `__pycache__`, build output or `node_modules` in commits.
