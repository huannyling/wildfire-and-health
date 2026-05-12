"""
09_temporal_analysis.py — Track how research focus has evolved over time.

Inputs:
  data/extracted/abstracts_structured.csv   (outcome_types, population_groups, mechanisms)
  data/classified/results_classified.csv    (publication_year)

Output:
  outputs/figures/topic_trends.png          — 6-panel temporal analysis figure

Temporal binning
-----------------
Year bands are chosen to balance sample size and interpretability:
  ≤2012      early era       (n ≈ 6 health papers)
  2013–2016  growth phase    (n ≈ 8)
  2017–2019  expansion       (n ≈ 7)
  2020–2022  acceleration    (n ≈ 19)   ← Australian Black Summer + COVID era
  2023–2024  current front   (n ≈ 7)

Emergence classification
-------------------------
For each topic t, we compute:
  rate_early  = mentions(t) / health_papers  for periods ≤2016
  rate_recent = mentions(t) / health_papers  for periods ≥2020
  emergence_delta = rate_recent − rate_early

Classification:
  Established    delta < 0.05, rate_early ≥ 0.30  (consistently high)
  Emerging       delta ≥ 0.10 and rate_recent ≥ 0.15
  Declining      delta ≤ −0.10
  Neglected      rate_recent < 0.10 and abs(delta) < 0.10
"""

import ast
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
import yaml

ROOT        = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OUTCOMES = [
    "respiratory", "cardiovascular", "mortality", "mental_health",
    "reproductive", "neurological", "burns_injury",
]
POPS = [
    "children", "elderly", "low_income", "indigenous",
    "racial_minorities", "pregnant", "outdoor_workers", "rural",
]

OUTCOME_LABELS = {
    "respiratory":      "Respiratory",
    "cardiovascular":   "Cardiovascular",
    "mortality":        "Mortality",
    "mental_health":    "Mental Health",
    "reproductive":     "Reproductive",
    "neurological":     "Neurological",
    "burns_injury":     "Burns / Injury",
}
POP_LABELS = {
    "children":         "Children",
    "elderly":          "Elderly",
    "low_income":       "Low-income",
    "indigenous":       "Indigenous",
    "racial_minorities":"Racial minorities",
    "pregnant":         "Pregnant",
    "outdoor_workers":  "Outdoor workers",
    "rural":            "Rural",
}

PERIODS      = ["≤2012", "2013–2016", "2017–2019", "2020–2022", "2023–2024"]
PERIOD_SHORT = ["≤2012", "2013\n–2016", "2017\n–2019", "2020\n–2022", "2023\n–2024"]

# Colours
OUTCOME_COLORS = [
    "#2980B9", "#E91E63", "#2C3E50", "#16A085",
    "#27AE60", "#8E44AD", "#E67E22",
]
POP_COLORS = [
    "#3498DB", "#E67E22", "#E74C3C", "#1ABC9C",
    "#9B59B6", "#F39C12", "#2ECC71", "#95A5A6",
]
C_HEALTH     = "#C0392B"
C_NONHEALTH  = "#BDC3C7"
C_BIO        = "#2471A3"
C_SOC        = "#C0392B"
C_GRID       = "#EEEEEE"

EMERGE_COLORS = {
    "Established": "#2471A3",
    "Emerging":    "#27AE60",
    "Declining":   "#E67E22",
    "Neglected":   "#B22222",
}

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    lf  = log_dir / f"09_temporal_{ts}.log"
    lvl = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[logging.FileHandler(lf), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("09_temporal")


def safe_parse(val) -> list:
    if pd.isna(val) or str(val).strip() in ("", "[]", "null"):
        return []
    try:
        return json.loads(val)
    except Exception:
        pass
    try:
        return ast.literal_eval(val)
    except Exception:
        return []


def bin_year(y) -> str | None:
    if pd.isna(y):
        return None
    y = int(y)
    if y <= 2012:      return "≤2012"
    if y <= 2016:      return "2013–2016"
    if y <= 2019:      return "2017–2019"
    if y <= 2022:      return "2020–2022"
    return "2023–2024"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data(log: logging.Logger) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (all_df, health_df) merged with publication_year."""
    struct  = pd.read_csv(ROOT / "data/extracted/abstracts_structured.csv", dtype=str)
    classif = pd.read_csv(ROOT / "data/classified/results_classified.csv",  dtype=str)

    df = struct.merge(
        classif[["openalex_id", "publication_year", "cited_by_count"]],
        left_on="paper_id", right_on="openalex_id", how="left",
    )
    df["year"]   = pd.to_numeric(df["publication_year"], errors="coerce")
    df["period"] = df["year"].apply(bin_year)
    df["cited_by_count"] = pd.to_numeric(df["cited_by_count"], errors="coerce")

    df["_outcomes"] = df["outcome_types"].apply(safe_parse)
    df["_pops"]     = df["population_groups"].apply(safe_parse)
    df["_bio"]      = df["biological_mechanisms"].apply(safe_parse)
    df["_soc"]      = df["social_determinants"].apply(safe_parse)
    df["_is_health"] = df["is_health_study"].str.strip().str.lower() == "true"

    all_df    = df.copy()
    health_df = df[df["_is_health"]].copy().reset_index(drop=True)

    log.info("All papers: %d  |  Health papers: %d", len(all_df), len(health_df))
    log.info("Year range: %.0f – %.0f",
             all_df["year"].min(), all_df["year"].max())
    for p in PERIODS:
        n = (all_df["period"] == p).sum()
        nh = (health_df["period"] == p).sum()
        log.info("  %s : %d total  (%d health)", p, n, nh)

    return all_df, health_df


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------
def period_rates(
    health_df: pd.DataFrame,
    label_col: str,     # "_outcomes" or "_pops"
    labels: list[str],
) -> pd.DataFrame:
    """
    Return DataFrame: rows = periods, columns = labels.
    Values = proportion of health papers in that period that mention the label.
    """
    rows = {}
    for p in PERIODS:
        sub = health_df[health_df["period"] == p]
        n   = len(sub)
        row = {}
        for lbl in labels:
            if n == 0:
                row[lbl] = 0.0
            else:
                row[lbl] = sub[label_col].apply(
                    lambda lst: lbl in lst
                ).sum() / n
        rows[p] = row
    return pd.DataFrame(rows).T   # periods × labels


def classify_emergence(rates_df: pd.DataFrame, label: str) -> str:
    """Classify a topic as Established / Emerging / Declining / Neglected."""
    early_periods  = [p for p in ["≤2012", "2013–2016"] if p in rates_df.index]
    recent_periods = [p for p in ["2020–2022", "2023–2024"] if p in rates_df.index]

    rate_early  = rates_df.loc[early_periods, label].mean()  if early_periods  else 0.0
    rate_recent = rates_df.loc[recent_periods, label].mean() if recent_periods else 0.0
    delta = rate_recent - rate_early

    if rate_early >= 0.30 and abs(delta) < 0.12:
        return "Established"
    if delta >= 0.10 and rate_recent >= 0.12:
        return "Emerging"
    if delta <= -0.10:
        return "Declining"
    return "Neglected"


def mechanism_depth_by_period(health_df: pd.DataFrame) -> pd.DataFrame:
    rows = {}
    for p in PERIODS:
        sub = health_df[health_df["period"] == p]
        if len(sub) == 0:
            rows[p] = {"bio": 0.0, "soc": 0.0, "n": 0}
        else:
            rows[p] = {
                "bio": sub["_bio"].apply(len).mean(),
                "soc": sub["_soc"].apply(len).mean(),
                "n":   len(sub),
            }
    return pd.DataFrame(rows).T


# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------
FONT_T  = 11
FONT_L  = 9
FONT_AX = 8

def _despine(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#BBBBBB")
    ax.spines["bottom"].set_color("#BBBBBB")


def make_figure(
    all_df:    pd.DataFrame,
    health_df: pd.DataFrame,
    out_path:  Path,
    log:       logging.Logger,
    dpi:       int = 300,
) -> None:

    plt.rcParams.update({"font.family": "sans-serif",
                         "figure.facecolor": "white",
                         "axes.facecolor": "white"})

    out_rates  = period_rates(health_df, "_outcomes", OUTCOMES)
    pop_rates  = period_rates(health_df, "_pops",     POPS)
    mech_depth = mechanism_depth_by_period(health_df)

    # Emergence classification
    out_class = {lbl: classify_emergence(out_rates, lbl) for lbl in OUTCOMES}
    pop_class = {lbl: classify_emergence(pop_rates, lbl) for lbl in POPS}
    log.info("Outcome emergence classes: %s", out_class)
    log.info("Population emergence classes: %s", pop_class)

    fig = plt.figure(figsize=(18, 14), facecolor="white")
    fig.text(0.5, 0.990,
             "Temporal Evolution of Wildfire Health Research Focus",
             ha="center", va="top", fontsize=13, fontweight="bold", color="#1A1A1A")
    fig.text(0.5, 0.974,
             "Based on LLM-extracted topics from 47 health papers (2008–2024)  ·  "
             "Proportions normalised within each time period",
             ha="center", va="top", fontsize=9, color="#666666", style="italic")

    gspec = fig.add_gridspec(
        2, 3, hspace=0.52, wspace=0.42,
        top=0.90, bottom=0.07, left=0.07, right=0.97,
    )
    ax_a = fig.add_subplot(gspec[0, 0])
    ax_b = fig.add_subplot(gspec[0, 1])
    ax_c = fig.add_subplot(gspec[0, 2])
    ax_d = fig.add_subplot(gspec[1, 0])
    ax_e = fig.add_subplot(gspec[1, 1])
    ax_f = fig.add_subplot(gspec[1, 2])

    # ── Panel A: Annual publication volume ────────────────────────────────
    year_all    = all_df["year"].dropna().astype(int).value_counts().sort_index()
    year_health = health_df["year"].dropna().astype(int).value_counts().sort_index()
    years = sorted(set(year_all.index) | set(year_health.index))

    all_counts    = [year_all.get(y, 0)    for y in years]
    health_counts = [year_health.get(y, 0) for y in years]
    nonhealth_counts = [a - h for a, h in zip(all_counts, health_counts)]

    x_a = np.arange(len(years))
    ax_a.bar(x_a, health_counts, color=C_HEALTH,    alpha=0.85, label="Health papers",     zorder=2)
    ax_a.bar(x_a, nonhealth_counts, bottom=health_counts,
             color=C_NONHEALTH, alpha=0.75, label="Non-health papers", zorder=2)

    # 3-year rolling trend on total
    total_series = pd.Series(all_counts, index=years)
    if len(total_series) >= 3:
        rolling = total_series.rolling(3, center=True).mean()
        ax_a.plot(x_a, rolling.values, color="#2C3E50", linewidth=2.0,
                  linestyle="--", label="3-yr trend", zorder=3)

    # Annotate Black Summer 2019-20
    idx_2019 = years.index(2019) if 2019 in years else None
    idx_2020 = years.index(2020) if 2020 in years else None
    if idx_2020 is not None:
        ax_a.annotate("Black Summer\n& COVID era",
                      xy=(idx_2020, all_counts[idx_2020]),
                      xytext=(idx_2020 - 2.5, all_counts[idx_2020] + 1.2),
                      fontsize=6.5, color="#8B0000", style="italic",
                      arrowprops=dict(arrowstyle="-|>", color="#8B0000",
                                      lw=0.9, connectionstyle="arc3,rad=0.3"))

    ax_a.set_xticks(x_a[::2])
    ax_a.set_xticklabels([str(y) for y in years[::2]], fontsize=FONT_AX, rotation=45, ha="right")
    ax_a.set_ylabel("Number of papers", fontsize=FONT_L)
    ax_a.set_title("(A)  Annual Publication Volume",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_a.legend(fontsize=7.5, loc="upper left", framealpha=0.90,
                edgecolor="#DDDDDD")
    ax_a.grid(axis="y", color=C_GRID, linewidth=0.6, zorder=0)
    ax_a.set_xlim(-0.5, len(years) - 0.5)
    _despine(ax_a)

    # ── Panel B: Outcome topic prevalence over periods (line chart) ───────
    for lbl, color in zip(OUTCOMES, OUTCOME_COLORS):
        vals = [out_rates.loc[p, lbl] * 100 if p in out_rates.index else 0
                for p in PERIODS]
        cls  = out_class[lbl]
        lw   = 2.2 if cls in ("Emerging", "Established") else 1.3
        ls   = "solid" if cls != "Neglected" else "dotted"
        marker = "o" if cls == "Emerging" else "s" if cls == "Established" else "^"
        ax_b.plot(range(len(PERIODS)), vals,
                  color=color, linewidth=lw, linestyle=ls,
                  marker=marker, markersize=5,
                  label=OUTCOME_LABELS[lbl], zorder=2)

    # Period sample-size rug
    ns = [len(health_df[health_df["period"] == p]) for p in PERIODS]
    for i, n in enumerate(ns):
        ax_b.text(i, -8, f"n={n}", ha="center", fontsize=6.5, color="#999999")

    ax_b.set_xticks(range(len(PERIODS)))
    ax_b.set_xticklabels(PERIOD_SHORT, fontsize=FONT_AX)
    ax_b.set_ylabel("% of health papers in period", fontsize=FONT_L)
    ax_b.set_ylim(-12, 105)
    ax_b.set_title("(B)  Health Outcome Topics Over Time",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_b.legend(fontsize=7.0, loc="upper left", framealpha=0.90,
                edgecolor="#DDDDDD", ncol=2, handlelength=1.2)
    ax_b.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax_b.grid(color=C_GRID, linewidth=0.6, zorder=0)
    _despine(ax_b)

    # ── Panel C: Population focus over periods (line chart) ───────────────
    for lbl, color in zip(POPS, POP_COLORS):
        vals = [pop_rates.loc[p, lbl] * 100 if p in pop_rates.index else 0
                for p in PERIODS]
        cls  = pop_class[lbl]
        lw   = 2.2 if cls in ("Emerging", "Established") else 1.3
        ls   = "solid" if cls != "Neglected" else "dotted"
        marker = "o" if cls == "Emerging" else "s" if cls == "Established" else "^"
        ax_c.plot(range(len(PERIODS)), vals,
                  color=color, linewidth=lw, linestyle=ls,
                  marker=marker, markersize=5,
                  label=POP_LABELS[lbl], zorder=2)

    for i, n in enumerate(ns):
        ax_c.text(i, -8, f"n={n}", ha="center", fontsize=6.5, color="#999999")

    ax_c.set_xticks(range(len(PERIODS)))
    ax_c.set_xticklabels(PERIOD_SHORT, fontsize=FONT_AX)
    ax_c.set_ylabel("% of health papers in period", fontsize=FONT_L)
    ax_c.set_ylim(-12, 85)
    ax_c.set_title("(C)  Vulnerable Population Focus Over Time",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_c.legend(fontsize=7.0, loc="upper left", framealpha=0.90,
                edgecolor="#DDDDDD", ncol=2, handlelength=1.2)
    ax_c.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax_c.grid(color=C_GRID, linewidth=0.6, zorder=0)
    _despine(ax_c)

    # ── Panel D: Mechanism depth shift — bio vs. social ───────────────────
    periods_with_data = [p for p in PERIODS if mech_depth.loc[p, "n"] > 0]
    x_d    = np.arange(len(periods_with_data))
    bio_d  = [mech_depth.loc[p, "bio"] for p in periods_with_data]
    soc_d  = [mech_depth.loc[p, "soc"] for p in periods_with_data]

    ax_d2 = ax_d.twinx()
    ln1 = ax_d.plot(x_d, bio_d, color=C_BIO, linewidth=2.2, marker="o",
                    markersize=6, label="Biological mechanisms", zorder=3)
    ln2 = ax_d2.plot(x_d, soc_d, color=C_SOC, linewidth=2.2, marker="s",
                     markersize=6, linestyle="--", label="Social determinants", zorder=3)

    # Shade the gap between the two
    ax_d.fill_between(x_d, bio_d, [s * (ax_d.get_ylim()[1] / max(soc_d + [1]))
                                     for s in soc_d],
                      alpha=0.08, color="#888888")

    ax_d.set_xticks(x_d)
    short_p = [p.replace("2013–2016", "2013\n–2016").replace("2017–2019", "2017\n–2019")
                 .replace("2020–2022", "2020\n–2022").replace("2023–2024", "2023\n–2024")
               for p in periods_with_data]
    ax_d.set_xticklabels(short_p, fontsize=FONT_AX)
    ax_d.set_ylabel("Bio mechanisms\n(mean # concepts / paper)", fontsize=FONT_L, color=C_BIO)
    ax_d2.set_ylabel("Social determinants\n(mean # concepts / paper)", fontsize=FONT_L, color=C_SOC)
    ax_d.tick_params(axis="y", labelcolor=C_BIO)
    ax_d2.tick_params(axis="y", labelcolor=C_SOC)
    ax_d.set_title("(D)  Mechanism Focus Shift: Bio vs. Social",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")

    lns = ln1 + ln2
    labs = [l.get_label() for l in lns]
    ax_d.legend(lns, labs, fontsize=7.5, loc="upper left",
                framealpha=0.90, edgecolor="#DDDDDD")
    ax_d.grid(color=C_GRID, linewidth=0.6, zorder=0)
    ax_d.spines["top"].set_visible(False)
    ax_d2.spines["top"].set_visible(False)
    ax_d.spines["bottom"].set_color("#BBBBBB")

    # ── Panel E: Emergence classification — delta bar chart ───────────────
    all_topics    = list(OUTCOME_LABELS.items()) + list(POP_LABELS.items())
    all_classes   = {**out_class, **pop_class}
    all_rates_df  = {}
    for lbl in OUTCOMES:
        all_rates_df[lbl] = out_rates
    for lbl in POPS:
        all_rates_df[lbl] = pop_rates

    deltas = []
    for lbl, nice in all_topics:
        df_r  = all_rates_df[lbl]
        early = [p for p in ["≤2012", "2013–2016"] if p in df_r.index]
        recent= [p for p in ["2020–2022", "2023–2024"] if p in df_r.index]
        r_e   = df_r.loc[early,  lbl].mean() * 100 if early  else 0.0
        r_r   = df_r.loc[recent, lbl].mean() * 100 if recent else 0.0
        deltas.append({
            "label":      nice,
            "delta":      r_r - r_e,
            "rate_early": r_e,
            "rate_recent":r_r,
            "class":      all_classes[lbl],
        })

    deltas_df = pd.DataFrame(deltas).sort_values("delta", ascending=True)

    bar_colors_e = [EMERGE_COLORS[c] for c in deltas_df["class"]]
    y_e = np.arange(len(deltas_df))
    bars_e = ax_e.barh(y_e, deltas_df["delta"], color=bar_colors_e,
                       edgecolor="white", linewidth=0.5, height=0.65, zorder=2)

    # Annotate bars
    for bar, (_, row) in zip(bars_e, deltas_df.iterrows()):
        x_ann = bar.get_width()
        ha    = "left"  if x_ann >= 0 else "right"
        pad   = 0.5     if x_ann >= 0 else -0.5
        ax_e.text(x_ann + pad, bar.get_y() + bar.get_height() / 2,
                  f"{x_ann:+.0f}pp", va="center", ha=ha,
                  fontsize=7, color="#444444")

    ax_e.axvline(0, color="#888888", linewidth=0.9, zorder=1)
    ax_e.set_yticks(y_e)
    ax_e.set_yticklabels(deltas_df["label"], fontsize=7.5)
    ax_e.set_xlabel("Change in research share (percentage points, recent vs. early)",
                    fontsize=FONT_L)
    ax_e.set_title("(E)  Topic Emergence: Recent (2020–2024) vs. Early (≤2016)",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_e.grid(axis="x", color=C_GRID, linewidth=0.6, zorder=0)
    _despine(ax_e)

    # Legend
    emerge_legend = [
        mpatches.Patch(facecolor=c, label=s, edgecolor="#AAAAAA", linewidth=0.4)
        for s, c in EMERGE_COLORS.items()
    ]
    ax_e.legend(handles=emerge_legend, fontsize=7.5, loc="lower right",
                framealpha=0.92, edgecolor="#DDDDDD")

    # ── Panel F: First-appearance bubble chart ────────────────────────────
    # For each topic: find the earliest period with rate ≥ 5%; size = rate_recent
    first_period_idx = {}
    for lbl in OUTCOMES:
        for pi, p in enumerate(PERIODS):
            if p in out_rates.index and out_rates.loc[p, lbl] >= 0.05:
                first_period_idx[lbl] = pi
                break
        else:
            first_period_idx[lbl] = len(PERIODS) - 1   # never appeared

    for lbl in POPS:
        for pi, p in enumerate(PERIODS):
            if p in pop_rates.index and pop_rates.loc[p, lbl] >= 0.05:
                first_period_idx[lbl] = pi
                break
        else:
            first_period_idx[lbl] = len(PERIODS) - 1

    # Build scatter data
    scatter_x, scatter_y, scatter_s, scatter_c, scatter_lbl = [], [], [], [], []
    y_jitter_counter = {}   # avoid overlap
    for row_i, (lbl, nice) in enumerate(all_topics):
        fp_idx = first_period_idx[lbl]
        df_r   = all_rates_df[lbl]
        recent_ps = [p for p in ["2020–2022", "2023–2024"] if p in df_r.index]
        r_recent   = df_r.loc[recent_ps, lbl].mean() * 100 if recent_ps else 0.0
        cls        = all_classes[lbl]

        # y: row index (topic), x: period index of first appearance
        scatter_x.append(fp_idx)
        scatter_y.append(row_i)
        scatter_s.append(max(r_recent * 18, 30))
        scatter_c.append(EMERGE_COLORS[cls])
        scatter_lbl.append(nice)

    ax_f.scatter(scatter_x, scatter_y, s=scatter_s, c=scatter_c,
                 alpha=0.80, edgecolors="white", linewidths=0.8, zorder=3)

    for xi, yi, lbl in zip(scatter_x, scatter_y, scatter_lbl):
        ax_f.text(xi + 0.08, yi, lbl, va="center", ha="left",
                  fontsize=6.8, color="#333333")

    ax_f.set_xticks(range(len(PERIODS)))
    ax_f.set_xticklabels(PERIOD_SHORT, fontsize=FONT_AX)
    ax_f.set_yticks([])
    ax_f.set_xlim(-0.3, len(PERIODS) - 0.3)
    ax_f.set_xlabel("Period of first significant appearance (≥5% of papers)",
                    fontsize=FONT_L)
    ax_f.set_title("(F)  Research Timeline: When Each Topic First Emerged\n"
                   "(bubble size = recent prevalence, colour = trajectory)",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_f.grid(axis="x", color=C_GRID, linewidth=0.6, zorder=0)
    _despine(ax_f)
    ax_f.spines["left"].set_visible(False)

    # Size legend
    size_handles = [
        mpatches.Circle((0, 0), radius=np.sqrt(s / np.pi) / 10,
                        facecolor="#AAAAAA", edgecolor="white", label=f"{lbl}")
        for s, lbl in [(50, "3%"), (200, "11%"), (500, "28%")]
    ]
    ax_f.legend(handles=[
        mpatches.Patch(facecolor=c, label=s) for s, c in EMERGE_COLORS.items()
    ], fontsize=7, loc="lower right", framealpha=0.92, edgecolor="#DDDDDD",
                title="Trajectory", title_fontsize=7)

    # ── Save ──────────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("Saved figure → %s", out_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 09_temporal_analysis.py started ===")

    all_df, health_df = load_data(log)

    fig_dir = ROOT / "outputs/figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    dpi = cfg.get("figures", {}).get("dpi", 300)

    make_figure(all_df, health_df, fig_dir / "topic_trends.png", log, dpi=dpi)

    # Console summary
    out_rates = period_rates(health_df, "_outcomes", OUTCOMES)
    pop_rates = period_rates(health_df, "_pops",     POPS)
    out_class = {lbl: classify_emergence(out_rates, lbl) for lbl in OUTCOMES}
    pop_class = {lbl: classify_emergence(pop_rates, lbl) for lbl in POPS}

    log.info("─" * 60)
    log.info("TEMPORAL SUMMARY")
    log.info("  OUTCOME trajectories:")
    for lbl, cls in sorted(out_class.items(), key=lambda x: x[1]):
        log.info("    %-18s  %s", lbl, cls)
    log.info("  POPULATION trajectories:")
    for lbl, cls in sorted(pop_class.items(), key=lambda x: x[1]):
        log.info("    %-22s  %s", lbl, cls)

    # Most neglected
    log.info("\n  PERSISTENTLY NEGLECTED (rate < 10%% in all periods):")
    for lbl in OUTCOMES + POPS:
        df_r = out_rates if lbl in OUTCOMES else pop_rates
        if lbl in df_r.columns:
            max_rate = df_r[lbl].max() * 100
            if max_rate < 10:
                log.info("    %s (peak rate %.1f%%)", lbl, max_rate)

    log.info("─" * 60)
    log.info("=== 09_temporal_analysis.py finished ===")


if __name__ == "__main__":
    main()
