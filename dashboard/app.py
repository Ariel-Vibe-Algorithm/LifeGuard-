"""LifeGuard - crime-by-area dashboard (Streamlit).

Run from anywhere (all paths are resolved relative to this file):
    pip install -r dashboard/requirements.txt
    python dashboard/clean.py          # builds dashboard/data/cleaned.csv
    streamlit run dashboard/app.py

Every number on the page is computed from the data at runtime.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "cleaned.csv"
RAW = HERE.parent / "research" / "datasets" / "crime_by_stat_area_summary.csv"
HIGH_RISK_RATIO = 2.0  # definition of the answer column (see clean.py)
TEAL, ORANGE, GREY = "#1f8a99", "#e8702a", "#9aa5ab"

HE = {"city": "עיר", "unit": "קוד אזור", "area_name": "שם האזור", "pop": "תושבים",
      "cases_2024": "תיקים 2024", "cases_2025": "תיקים 2025",
      "rate_2024": "שיעור לאלף 2024", "rate_2025": "שיעור לאלף 2025",
      "ratio_2024": "יחס לחציון 2024", "ratio_2025": "יחס לחציון 2025",
      "is_visitor_area": "אזור מבקרים", "dup_stat_area_flag": "סימון: תת־אזור כפול",
      "high_risk_2025": "סיכון גבוה 2025"}

st.set_page_config(page_title="LifeGuard – סיכון לפי אזור", page_icon="🛡️", layout="wide")
st.markdown("""
<style>
  .stApp { direction: rtl; }
  [data-testid="stMarkdownContainer"], [data-testid="stMetric"], [data-testid="stSidebar"],
  [data-testid="stCaptionContainer"], [data-testid="stWidgetLabel"], h1, h2, h3, h4 { text-align: right; }
  [data-testid="stMetricValue"], [data-testid="stMetricDelta"] { direction: ltr; justify-content: flex-end; }
  .js-plotly-plot, [data-testid="stDataFrame"] { direction: ltr; }
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load() -> pd.DataFrame:
    return pd.read_csv(DATA, encoding="utf-8-sig")


@st.cache_data
def raw_row_count() -> int | None:
    return len(pd.read_csv(RAW)) if RAW.exists() else None


def pct(v: float) -> str:
    return "—" if pd.isna(v) else f"{v:.0%}"


def evaluate(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Predict high_risk_2025 from 2024 data only. Baselines vs. the persistence rule."""
    y = df["high_risk_2025"].to_numpy()
    p = y.mean() if len(y) else np.nan

    def row(name, pred):
        tp = int(((pred == 1) & (y == 1)).sum())
        return {"מודל": name, "אזורים שסומנו": int(pred.sum()),
                "דיוק (accuracy)": (pred == y).mean() if len(y) else np.nan,
                "כמה מצאנו (recall)": tp / y.sum() if y.sum() else np.nan,
                "כמה צדקנו (precision)": tp / pred.sum() if pred.sum() else np.nan}

    rows = [row('קו בסיס: תמיד "לא"', np.zeros_like(y))]
    # Random guess at the base rate: expected values, computed analytically (no randomness on the page)
    rows.append({"מודל": "קו בסיס: ניחוש אקראי", "אזורים שסומנו": int(round(p * len(y))) if len(y) else 0,
                 "דיוק (accuracy)": p * p + (1 - p) * (1 - p), "כמה מצאנו (recall)": p, "כמה צדקנו (precision)": p})
    rows.append(row(f"כלל התמדה: יחס 2024 ≥ {threshold:g}", (df["ratio_2024"].to_numpy() >= threshold).astype(int)))
    return pd.DataFrame(rows).set_index("מודל")


if not DATA.exists():
    st.error("לא נמצא הקובץ dashboard/data/cleaned.csv. הריצו קודם: python dashboard/clean.py")
    st.stop()

data = load()

# ---------- Sidebar filters ----------
st.sidebar.header("סינון")
cities = sorted(data["city"].unique())
chosen = st.sidebar.multiselect("ערים", cities, default=cities)
min_pop = st.sidebar.slider("מינימום תושבים באזור", 1000, 10000, 1000, step=500)
kind = st.sidebar.radio("סוג אזור", ["הכול", "אזורי מבקרים בלבד (מע\"ר, שוק, תעשייה)", "אזורי מגורים בלבד"])
st.sidebar.divider()
threshold = st.sidebar.slider("סף החיזוי (יחס 2024 לחציון העירוני)", 1.0, 4.0, HIGH_RISK_RATIO, step=0.25,
                              help="משפיע רק על חלק החיזוי: אזור מסומן כמסוכן ל־2025 אם היחס שלו ב־2024 גבוה מהסף. "
                                   f"ההגדרה של 'סיכון גבוה' עצמה קבועה: פי {HIGH_RISK_RATIO:g} מהחציון ב־2025.")

df = data[data["city"].isin(chosen) & (data["pop"] >= min_pop)]
if kind.startswith("אזורי מבקרים"):
    df = df[df["is_visitor_area"] == 1]
elif kind.startswith("אזורי מגורים"):
    df = df[df["is_visitor_area"] == 0]

# ---------- Header ----------
n_raw = raw_row_count()
st.title("🛡️ LifeGuard – האם אזור מסוכן נשאר מסוכן?")
st.caption(f"תיקי משטרה לפי אזור סטטיסטי ב־{data['city'].nunique()} ערים, 2024–2025. "
           f"{len(data):,} אזורים אחרי ניקוי" + (f" (מתוך {n_raw:,} בקובץ הגולמי)" if n_raw else "") + ". "
           f"סיכון גבוה = שיעור תיקים לאלף תושבים של פי {HIGH_RISK_RATIO:g} ומעלה מהחציון של אותה עיר באותה שנה.")

if df.empty:
    st.warning("אין אזורים שמתאימים לסינון. הרחיבו את הבחירה בסרגל הצד.")
    st.stop()

# =====================================================================
# Part 1 - retrospective: what actually happened in 2025
# =====================================================================
with st.container(border=True):
    st.header("חלק א׳ · מה קרה בפועל ב־2025 (מבט לאחור)")
    st.caption("כאן משתמשים בנתוני 2025 עצמם. זה לא חיזוי, אלא התשובה האמיתית שמולה בודקים את החיזוי בחלק ב׳.")

    change = (df["ratio_2025"] - df["ratio_2024"]).mean()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("אזורים מוצגים", f"{len(df):,}", help=f"מתוך {len(data):,} אזורים אחרי ניקוי")
    c2.metric("אזורים בסיכון גבוה ב־2025", f"{int(df['high_risk_2025'].sum()):,}")
    c3.metric("אחוז האזורים בסיכון גבוה", pct(df["high_risk_2025"].mean()))
    c4.metric("שינוי ממוצע ביחס לחציון, 2024→2025", f"{change:+.3f}",
              help="ממוצע של (יחס 2025 פחות יחס 2024). קרוב לאפס = האזורים שמרו על מקומם היחסי.")

    left, right = st.columns(2)
    with left:
        st.subheader("אחוז האזורים בסיכון גבוה לפי עיר")
        by_city = (df.groupby("city")["high_risk_2025"].agg(["mean", "sum", "size"]).reset_index().sort_values("mean"))
        fig = px.bar(by_city, x="mean", y="city", orientation="h", text=by_city["mean"].map("{:.0%}".format),
                     hover_data={"sum": True, "size": True}, color_discrete_sequence=[TEAL],
                     labels={"mean": "% אזורים בסיכון גבוה", "city": "", "sum": "בסיכון גבוה", "size": "אזורים"})
        fig.update_xaxes(tickformat=".0%")
        fig.update_layout(height=420, margin=dict(t=10, b=10))
        st.plotly_chart(fig, width="stretch")
        st.caption("הסף יחסי לחציון של כל עיר, ובכל זאת יש ערים שבהן הפשיעה מרוכזת בכמה מוקדים, ויש ערים שבהן היא מפוזרת.")

    with right:
        top_n = min(15, len(df))
        st.subheader(f"{top_n} האזורים עם היחס הגבוה ביותר ב־2025")
        top = df.nlargest(top_n, "ratio_2025").assign(
            label=lambda t: t["area_name"].str.slice(0, 32) + " · " + t["city"],
            kind=lambda t: t["is_visitor_area"].map({1: "אזור מבקרים", 0: "אזור מגורים"}))
        fig = px.bar(top.sort_values("ratio_2025"), x="ratio_2025", y="label", orientation="h",
                     color="kind", color_discrete_map={"אזור מבקרים": ORANGE, "אזור מגורים": TEAL},
                     hover_data={"pop": True, "rate_2025": ":.1f", "kind": False, "label": False},
                     labels={"ratio_2025": "יחס לחציון העירוני 2025", "label": "", "pop": "תושבים",
                             "rate_2025": "תיקים לאלף"})
        fig.add_vline(x=HIGH_RISK_RATIO, line_dash="dot", line_color=GREY)
        fig.update_layout(height=420, margin=dict(t=10, b=10), legend=dict(orientation="h", y=1.08, title=""))
        st.plotly_chart(fig, width="stretch")
        vis_share = top["is_visitor_area"].mean()
        st.caption(f"{vis_share:.0%} מהאזורים ברשימה הם אזורי מבקרים (כתום): שם השיעור לתושב מנופח, כי רוב האנשים לא גרים שם.")

# =====================================================================
# Part 2 - prediction from 2024 data only
# =====================================================================
res = evaluate(df, threshold)
base, rule = res.iloc[0], res.iloc[-1]
with st.container(border=True):
    st.header("חלק ב׳ · חיזוי 2025 מנתוני 2024 בלבד")
    st.caption("המודל רואה רק את היחס לחציון העירוני ב־2024. עמודות שמחושבות משתי השנים יחד הוסרו בניקוי (דליפה). "
               "את התחזית משווים לתשובה האמיתית מחלק א׳.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("אזורים שהמודל סימן", f"{int(rule['אזורים שסומנו']):,}")
    c2.metric("כמה מצאנו (recall)", pct(rule["כמה מצאנו (recall)"]), help="מכל האזורים שהיו באמת בסיכון גבוה ב־2025, כמה סומנו מראש")
    c3.metric("כמה צדקנו (precision)", pct(rule["כמה צדקנו (precision)"]), help="מכל האזורים שסומנו, כמה באמת היו בסיכון גבוה")
    c4.metric("דיוק (accuracy)", pct(rule["דיוק (accuracy)"]),
              f"קו בסיס 'תמיד לא': {pct(base['דיוק (accuracy)'])}", delta_color="off")

    left, right = st.columns(2)
    with left:
        st.subheader("2024 מול 2025: אותו מקום, שנה אחרי")
        sc = df.assign(**{"סיכון גבוה 2025": df["high_risk_2025"].map({1: "כן", 0: "לא"})})
        fig = px.scatter(sc, x="ratio_2024", y="ratio_2025", color="סיכון גבוה 2025", log_x=True, log_y=True,
                         color_discrete_map={"כן": ORANGE, "לא": GREY},
                         hover_data={"city": True, "area_name": True, "pop": True, "ratio_2024": ":.2f", "ratio_2025": ":.2f"},
                         labels={"ratio_2024": "יחס לחציון העירוני 2024 (מה שהמודל רואה)",
                                 "ratio_2025": "יחס לחציון העירוני 2025 (התשובה)",
                                 "city": "עיר", "area_name": "אזור", "pop": "תושבים"})
        fig.add_vline(x=threshold, line_dash="dash", line_color=TEAL, annotation_text=f"סף החיזוי {threshold:g}")
        fig.add_hline(y=HIGH_RISK_RATIO, line_dash="dot", line_color=ORANGE)
        fig.update_layout(height=420, margin=dict(t=10, b=10), legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, width="stretch")
        r = df["ratio_2024"].corr(df["ratio_2025"]) if len(df) > 2 else np.nan
        st.caption(f"כל נקודה היא אזור. הקורלציה בין 2024 ל־2025 היא {r:.2f}: "
                   "נקודות מימין לקו המקווקו מסומנות כמסוכנות, והכתומות הן אלה שבאמת היו מסוכנות.")

    with right:
        st.subheader("קו בסיס מול כלל ההתמדה")
        long = res.drop(columns="אזורים שסומנו").reset_index().melt(id_vars="מודל", var_name="מדד", value_name="ערך")
        fig = px.bar(long, x="מודל", y="ערך", color="מדד", barmode="group",
                     color_discrete_sequence=[GREY, ORANGE, TEAL], text=long["ערך"].map(pct))
        fig.update_yaxes(range=[0, 1.12], tickformat=".0%", title="")
        fig.update_layout(height=420, margin=dict(t=10, b=10), xaxis_title="", legend=dict(orientation="h", y=1.1, title=""))
        st.plotly_chart(fig, width="stretch")
        st.caption('"תמיד לא" מקבל דיוק גבוה ולא מוצא אף אזור מסוכן. לכן מודדים גם כמה מצאנו וכמה צדקנו.')

    st.dataframe(res.style.format({"דיוק (accuracy)": "{:.1%}", "כמה מצאנו (recall)": "{:.1%}",
                                   "כמה צדקנו (precision)": "{:.1%}"}, na_rep="—"), width="stretch")

# ---------- Insight ----------
st.header("התובנה")
pos_share = df["high_risk_2025"].mean()
corr_y = df["ratio_2024"].corr(df["high_risk_2025"]) if df["high_risk_2025"].nunique() > 1 else np.nan
n_dropped = (n_raw - len(data)) if n_raw else None
st.markdown(f"""
**הפשיעה נשארת באותם מקומות.** באזורים המוצגים, הקורלציה בין היחס לחציון ב־2024 לבין היחס ב־2025 היא
**{df['ratio_2024'].corr(df['ratio_2025']):.2f}**, והקורלציה בין היחס של 2024 לעמודת התשובה היא **{corr_y:.2f}**.
לכן הכלל הפשוט "תסמן את מי שהיה גבוה בשנה שעברה" מוצא **{pct(rule['כמה מצאנו (recall)'])}** מהאזורים המסוכנים
וצודק ב־**{pct(rule['כמה צדקנו (precision)'])}** מהסימונים שלו.

**למה צריך קו בסיס:** רק **{pct(pos_share)}** מהאזורים מסוכנים. לכן מודל שתמיד עונה "לא מסוכן" מקבל דיוק של
**{pct(base['דיוק (accuracy)'])}** בלי למצוא אף אזור. דיוק לבדו מטעה, ולכן מודדים גם recall ו־precision.
נסו להזיז את סף החיזוי בסרגל הצד: סף נמוך מוצא יותר אזורים, אבל עם יותר התראות שווא.

**הסתייגויות:** רק שתי שנים; השיעור לפי תושבים ולכן מנופח באזורי מבקרים;
{f"{n_dropped:,} אזורים" if n_dropped is not None else "חלק מהאזורים"} הוסרו בגלל נתונים חסרים, ואסור להציג אותם כ"בטוחים".
פירוט מלא: `INSIGHTS.md` ו־`dashboard/DATA_CARD.md`.
""")

# ---------- Data table ----------
st.header("טבלת הנתונים")
risk = st.radio("רמת סיכון ב־2025", ["הכול", "סיכון גבוה בלבד", "לא בסיכון גבוה"], horizontal=True)
tdf = df if risk == "הכול" else df[df["high_risk_2025"] == (1 if risk == "סיכון גבוה בלבד" else 0)]
table = (tdf[list(HE)].sort_values("ratio_2025", ascending=False)
         .assign(is_visitor_area=lambda t: t["is_visitor_area"].map({1: "כן", 0: "לא"}),
                 dup_stat_area_flag=lambda t: t["dup_stat_area_flag"].map({1: "⚠️ כן", 0: ""}),
                 high_risk_2025=lambda t: t["high_risk_2025"].map({1: "כן", 0: "לא"}))
         .rename(columns=HE))
st.caption(f"{len(table):,} שורות. ממוין לפי היחס לחציון ב־2025, מהגבוה לנמוך.")
st.dataframe(table, width="stretch", hide_index=True, height=420)
st.download_button("הורדת הטבלה המסוננת (CSV)", table.to_csv(index=False).encode("utf-8-sig"),
                   file_name="lifeguard_filtered.csv", mime="text/csv")
