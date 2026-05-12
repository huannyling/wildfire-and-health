"""
06_gap_drivers_analysis.py — Explain WHY structural gaps exist in wildfire health research.

Inputs:
  data/extracted/abstracts_structured.csv   (from 03_llm_extract.py)
  data/classified/results_classified.csv    (from 03_classify.py)

Outputs:
  outputs/tables/gap_drivers_summary.csv    — structured driver metrics per gap category
  outputs/figures/gap_drivers_comparison.png — multi-panel comparison figure
  outputs/report/gap_drivers_interpretation.md — plain-language interpretation

Analytical framework
----------------------
For each gap category (neglected outcome or under-studied population), we profile the
surrounding literature along five structural dimensions:

  1. Study design mix       — observational dominance vs. experimental / review evidence
  2. Geographic concentration — US-centric vs. global coverage
  3. Methodological profile  — epidemiological vs. remote-sensing vs. modelling
  4. Journal disciplinary field — public health vs. environmental science vs. ecology
  5. Citation leverage       — median citations as proxy for community attention

Gap categories are split into two tiers:
  • Well-studied  (respiratory, cardiovascular, children, elderly)
  • Under-studied (neurological, reproductive, pregnant, indigenous, racial_minorities,
                   rural, low_income, mental_health)

Each driver dimension is then compared across tiers to surface systematic biases.
"""

import ast
import json
import logging
import sys
import textwrap
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

# ---------------------------------------------------------------------------
# Gap tiers — derived from 05_gap_analysis.py scores (Gap Score threshold 0.40)
# ---------------------------------------------------------------------------
WELL_STUDIED_OUTCOMES = {"respiratory", "cardiovascular"}
NEGLECTED_OUTCOMES    = {"neurological", "reproductive", "mental_health"}

WELL_STUDIED_POPS   = {"children", "elderly"}
NEGLECTED_POPS      = {"pregnant", "indigenous", "racial_minorities", "rural", "low_income"}

# ---------------------------------------------------------------------------
# Study design taxonomy — map raw LLM values → 4 broad design families
# ---------------------------------------------------------------------------
DESIGN_FAMILY: dict[str, str] = {
    "cohort":              "Epidemiological",
    "case_control":        "Epidemiological",
    "cross_sectional":     "Epidemiological",
    "time_series":         "Epidemiological",
    "case_crossover":      "Epidemiological",
    "randomised_control":  "Experimental",
    "qualitative":         "Qualitative",
    "systematic_review":   "Synthesis",
    "meta_analysis":       "Synthesis",
    "modelling":           "Modelling",
    "other":               "Other",
}

# ---------------------------------------------------------------------------
# Broad journal discipline heuristics (keyword-in-name matching)
# ---------------------------------------------------------------------------
JOURNAL_DOMAIN_RULES: list[tuple[str, str]] = [
    # Public health / medicine
    ("lancet",                "Public Health / Medicine"),
    ("health",                "Public Health / Medicine"),
    ("medicine",              "Public Health / Medicine"),
    ("clinical",              "Public Health / Medicine"),
    ("epidemiol",             "Public Health / Medicine"),
    # Environmental science
    ("environment",           "Environmental Science"),
    ("atmospheric",           "Environmental Science"),
    ("pollution",             "Environmental Science"),
    ("geohealth",             "Environmental Science"),
    # Ecology / natural science
    ("ecology",               "Ecology / Natural Science"),
    ("forest",                "Ecology / Natural Science"),
    ("bioscience",            "Ecology / Natural Science"),
    ("nature",                "Ecology / Natural Science"),
    ("science",               "Ecology / Natural Science"),
    ("proceedings",           "Ecology / Natural Science"),
    # Social science
    ("social",                "Social Science"),
    ("communit",              "Social Science"),
    ("society",               "Social Science"),
]

def classify_journal(journal_name: str) -> str:
    if pd.isna(journal_name) or not journal_name:
        return "Unknown"
    jl = journal_name.lower()
    for kw, domain in JOURNAL_DOMAIN_RULES:
        if kw in jl:
            return domain
    return "Other"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = log_dir / f"06_gap_drivers_{timestamp}.log"
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("06_gap_drivers")


def safe_parse(val) -> list:
    """Parse a JSON/Python-literal list stored as a string; return [] on failure."""
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


def pct(n: int, total: int) -> float:
    return round(100 * n / total, 1) if total > 0 else 0.0


# ---------------------------------------------------------------------------
# Data loading and joining
# ---------------------------------------------------------------------------
def load_merged(log: logging.Logger) -> pd.DataFrame:
    structured_path  = ROOT / "data/extracted/abstracts_structured.csv"
    classified_path  = ROOT / "data/classified/results_classified.csv"

    for p in (structured_path, classified_path):
        if not p.exists():
            log.error("Required file missing: %s", p)
            sys.exit(1)

    struct = pd.read_csv(structured_path, dtype=str)
    classif = pd.read_csv(classified_path, dtype=str)

    merged = struct.merge(
        classif[["openalex_id", "journal", "first_author_country",
                 "cited_by_count", "method_type", "is_open_access"]],
        left_on="paper_id", right_on="openalex_id", how="left",
    )
    merged["cited_by_count"] = pd.to_numeric(merged["cited_by_count"], errors="coerce")

    # Filter to health papers only
    health = merged[merged["is_health_study"].str.strip().str.lower() == "true"].copy()
    health = health.reset_index(drop=True)
    log.info("Health papers for driver analysis: %d", len(health))

    # Parse list columns
    health["_outcomes"]  = health["outcome_types"].apply(safe_parse)
    health["_pops"]      = health["population_groups"].apply(safe_parse)
    health["_bio"]       = health["biological_mechanisms"].apply(safe_parse)
    health["_soc"]       = health["social_determinants"].apply(safe_parse)

    # Classify journal domain
    health["journal_domain"] = health["journal"].apply(classify_journal)

    # Normalise study_design → design family
    health["design_family"] = health["study_design"].apply(
        lambda d: DESIGN_FAMILY.get(str(d).strip().lower(), "Other")
    )

    # Geographic concentration flag: US-authored?
    health["is_us"] = health["first_author_country"].str.upper().eq("US")

    return health


# ---------------------------------------------------------------------------
# Core computation: profile papers for a given label set
# ---------------------------------------------------------------------------
def profile_papers(
    df: pd.DataFrame,
    label_col: str,   # "_outcomes" or "_pops"
    label: str,
) -> dict:
    """Return driver metrics for papers that mention `label`."""
    mask = df[label_col].apply(lambda lst: label in lst)
    sub  = df[mask]
    n    = len(sub)
    if n == 0:
        return {"n": 0}

    # Design family distribution
    design_counts = sub["design_family"].value_counts().to_dict()

    # Geographic concentration
    us_pct   = pct(sub["is_us"].sum(), n)
    n_regions = sub["study_regions"].apply(safe_parse).apply(len).mean()

    # Geographic scope
    scope_counts = sub["geographic_scope"].value_counts().to_dict()

    # Journal domain
    domain_counts = sub["journal_domain"].value_counts().to_dict()

    # Mechanism depth: mean # bio mechanisms cited
    bio_depth = sub["_bio"].apply(len).mean()
    soc_depth = sub["_soc"].apply(len).mean()

    # Citation median
    median_cit = sub["cited_by_count"].median()

    # Open access share
    oa_pct = pct(
        sub["is_open_access"].str.strip().str.lower().isin(["true","1","yes"]).sum(),
        n,
    )

    # Stated gaps: how often do authors flag their own gaps?
    gap_flag_pct = pct(
        sub["gaps_stated"].apply(safe_parse).apply(len).gt(0).sum(),
        n,
    )

    return {
        "n":               n,
        "design_counts":   design_counts,
        "us_pct":          us_pct,
        "n_regions_mean":  round(n_regions, 2),
        "scope_counts":    scope_counts,
        "domain_counts":   domain_counts,
        "bio_depth":       round(bio_depth, 2),
        "soc_depth":       round(soc_depth, 2),
        "median_citations": round(median_cit, 1) if not pd.isna(median_cit) else None,
        "oa_pct":          oa_pct,
        "gap_flag_pct":    gap_flag_pct,
    }


# ---------------------------------------------------------------------------
# Build flat summary table
# ---------------------------------------------------------------------------
def build_summary_table(
    profiles: dict[str, dict],
    tier_map: dict[str, str],
) -> pd.DataFrame:
    rows = []
    design_families = ["Epidemiological", "Synthesis", "Modelling", "Qualitative", "Experimental", "Other"]
    domains         = ["Public Health / Medicine", "Environmental Science",
                       "Ecology / Natural Science", "Social Science", "Other", "Unknown"]

    for label, p in profiles.items():
        if p["n"] == 0:
            continue
        row = {
            "label":        label,
            "tier":         tier_map.get(label, "—"),
            "n_papers":     p["n"],
            "us_pct":       p["us_pct"],
            "median_citations": p["median_citations"],
            "oa_pct":       p["oa_pct"],
            "bio_depth":    p["bio_depth"],
            "soc_depth":    p["soc_depth"],
            "gap_flag_pct": p["gap_flag_pct"],
        }
        total = p["n"]
        for df_name in design_families:
            row[f"design_{df_name.lower().replace(' ','')}"] = pct(
                p["design_counts"].get(df_name, 0), total)
        for dom in domains:
            safe_dom = dom.lower().replace(" ","_").replace("/","_")
            row[f"journal_{safe_dom}"] = pct(
                p["domain_counts"].get(dom, 0), total)
        rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Driver inference: generate plain-language interpretation per label
# ---------------------------------------------------------------------------
DRIVER_TEMPLATES = {
    "epi_dominant": (
        "Research on {label} is dominated by observational epidemiology "
        "({design_pct:.0f}% of papers), with few experimental or mechanistic studies. "
        "This may reflect the difficulty of conducting controlled trials during wildfire events "
        "and reliance on health registry data."
    ),
    "us_concentrated": (
        "{label} research is geographically concentrated in the United States "
        "({us_pct:.0f}% of papers). This likely reflects US data infrastructure "
        "(EPA monitoring networks, ICD-coded hospital registries) unavailable "
        "in lower-income countries where wildfire burden is growing."
    ),
    "ph_dominated": (
        "Publications on {label} are clustered in public health and medical journals "
        "({ph_pct:.0f}%), suggesting limited cross-disciplinary integration with "
        "environmental science or social science — both essential for understanding "
        "structural drivers of vulnerability."
    ),
    "low_citation": (
        "Median citations for {label} papers ({med_cit:.0f}) are substantially below "
        "the corpus median, suggesting the sub-field has not yet attracted "
        "mainstream research attention or systematic review-level synthesis."
    ),
    "low_soc": (
        "Social determinant mechanisms are rarely cited in {label} research "
        "(mean {soc_depth:.1f} concepts/paper), indicating a predominant focus on "
        "biological pathways with limited attention to structural drivers such as "
        "housing quality, income, and healthcare access."
    ),
    "synthesis_absent": (
        "Fewer than 10% of {label} papers are systematic reviews or meta-analyses, "
        "limiting the field's ability to generate policy-actionable evidence summaries."
    ),
}


def infer_drivers(label: str, p: dict, corpus_median_cit: float) -> list[str]:
    """Return a list of driver interpretation strings for a given label profile."""
    drivers = []
    n = p["n"]
    if n == 0:
        return ["No papers found for this category."]

    total_design = sum(p["design_counts"].values())
    epi_pct      = pct(p["design_counts"].get("Epidemiological", 0), total_design)
    syn_pct      = pct(p["design_counts"].get("Synthesis", 0), total_design)
    ph_pct       = pct(p["domain_counts"].get("Public Health / Medicine", 0), n)
    med_cit      = p["median_citations"] or 0
    label_fmt    = label.replace("_", " ").title()

    if epi_pct >= 50:
        drivers.append(DRIVER_TEMPLATES["epi_dominant"].format(
            label=label_fmt, design_pct=epi_pct))
    if p["us_pct"] >= 55:
        drivers.append(DRIVER_TEMPLATES["us_concentrated"].format(
            label=label_fmt, us_pct=p["us_pct"]))
    if ph_pct >= 55:
        drivers.append(DRIVER_TEMPLATES["ph_dominated"].format(
            label=label_fmt, ph_pct=ph_pct))
    if med_cit < corpus_median_cit * 0.6 and med_cit > 0:
        drivers.append(DRIVER_TEMPLATES["low_citation"].format(
            label=label_fmt, med_cit=med_cit))
    if p["soc_depth"] < 0.5:
        drivers.append(DRIVER_TEMPLATES["low_soc"].format(
            label=label_fmt, soc_depth=p["soc_depth"]))
    if syn_pct < 10:
        drivers.append(DRIVER_TEMPLATES["synthesis_absent"].format(label=label_fmt))

    if not drivers:
        drivers.append(
            f"No single dominant structural driver identified for {label_fmt}. "
            f"The gap likely reflects a compound of factors requiring targeted investigation."
        )
    return drivers


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------
def write_interpretation(
    profiles:   dict[str, dict],
    tier_map:   dict[str, str],
    drivers:    dict[str, list[str]],
    out_path:   Path,
    log:        logging.Logger,
) -> None:
    lines: list[str] = []
    lines.append("# Gap Driver Analysis: Why Structural Gaps Exist\n")
    lines.append(
        "_Auto-generated by 06_gap_drivers_analysis.py — do not edit manually._\n"
    )
    lines.append(
        "## Overview\n\n"
        "This report explains the structural mechanisms behind each knowledge gap "
        "identified in `05_gap_analysis.py`. Five driver dimensions are assessed per "
        "gap category: study design bias, geographic concentration, journal disciplinary "
        "field, mechanistic depth (bio vs. social), and community attention (citations).\n"
    )

    for tier_label, tier_key in [
        ("Under-studied Outcomes", "Neglected Outcome"),
        ("Under-studied Populations", "Neglected Population"),
        ("Well-studied Topics (Reference)", "Well-studied"),
    ]:
        labels_in_tier = [l for l, t in tier_map.items() if t == tier_key and profiles.get(l, {}).get("n", 0) > 0]
        if not labels_in_tier:
            continue
        lines.append(f"---\n\n## {tier_label}\n")
        for label in sorted(labels_in_tier):
            p   = profiles[label]
            drs = drivers.get(label, [])
            label_fmt = label.replace("_", " ").title()
            lines.append(f"### {label_fmt}  (n = {p['n']} papers)\n")
            lines.append(f"**US-concentrated:** {p['us_pct']:.0f}%  |  "
                         f"**Median citations:** {p['median_citations'] or 'N/A'}  |  "
                         f"**OA share:** {p['oa_pct']:.0f}%\n")
            design_str = ", ".join(
                f"{k}: {v}" for k, v in sorted(
                    p["design_counts"].items(), key=lambda x: -x[1])
            )
            lines.append(f"**Study designs:** {design_str}\n")
            lines.append("\n**Inferred drivers:**\n")
            for d in drs:
                wrapped = textwrap.fill(d, width=110)
                for ln in wrapped.split("\n"):
                    lines.append(f"- {ln}\n" if ln == wrapped.split("\n")[0] else f"  {ln}\n")
            lines.append("\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(lines), encoding="utf-8")
    log.info("Saved interpretation → %s", out_path)


# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------
C_WELL      = "#2471A3"   # Well-studied — steel blue
C_NEGLECTED = "#B22222"   # Neglected — firebrick red
C_GRID      = "#EEEEEE"
FONT_T      = 11
FONT_L      = 9.5
FONT_AX     = 8.5


def _despine(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#BBBBBB")
    ax.spines["bottom"].set_color("#BBBBBB")


def _label_bars_h(ax, bars, fmt="{:.0f}%", pad_frac=0.02):
    xmax = ax.get_xlim()[1]
    for bar in bars:
        w = bar.get_width()
        ax.text(w + xmax * pad_frac,
                bar.get_y() + bar.get_height() / 2,
                fmt.format(w),
                va="center", ha="left", fontsize=7.5, color="#444444")


def make_driver_figure(
    profiles:  dict[str, dict],
    tier_map:  dict[str, str],
    out_path:  Path,
    log:       logging.Logger,
    dpi:       int = 300,
) -> None:
    """4-panel figure comparing well-studied vs neglected categories across driver dimensions."""

    plt.rcParams.update({
        "font.family": "sans-serif",
        "axes.facecolor": "white",
        "figure.facecolor": "white",
    })

    # ── Prepare aggregated tier-level metrics ─────────────────────────────
    def tier_profiles(tier_key: str) -> list[dict]:
        return [p for lbl, p in profiles.items()
                if tier_map.get(lbl) == tier_key and p.get("n", 0) > 0]

    def mean_metric(tier_key: str, metric: str) -> float:
        vals = [p[metric] for p in tier_profiles(tier_key)
                if p.get(metric) is not None]
        return float(np.mean(vals)) if vals else 0.0

    def tier_design_pct(tier_key: str, family: str) -> float:
        total_n = sum(p["n"] for p in tier_profiles(tier_key))
        total_fam = sum(p["design_counts"].get(family, 0) for p in tier_profiles(tier_key))
        return pct(total_fam, total_n)

    def tier_domain_pct(tier_key: str, domain: str) -> float:
        total_n = sum(p["n"] for p in tier_profiles(tier_key))
        total_dom = sum(p["domain_counts"].get(domain, 0) for p in tier_profiles(tier_key))
        return pct(total_dom, total_n)

    well_key = "Well-studied"
    neg_out  = "Neglected Outcome"
    neg_pop  = "Neglected Population"

    # ── Figure layout ─────────────────────────────────────────────────────
    fig = plt.figure(figsize=(17, 13), facecolor="white")
    fig.text(0.5, 0.990,
             "Structural Drivers of Knowledge Gaps in Wildfire Health Research",
             ha="center", va="top", fontsize=13, fontweight="bold", color="#1A1A1A")
    fig.text(0.5, 0.974,
             "Comparing well-studied topics vs. neglected outcomes and populations across "
             "five structural dimensions",
             ha="center", va="top", fontsize=9, color="#666666", style="italic")

    gspec = fig.add_gridspec(2, 2, hspace=0.55, wspace=0.52,
                              top=0.89, bottom=0.07, left=0.11, right=0.97)
    ax_a = fig.add_subplot(gspec[0, 0])
    ax_b = fig.add_subplot(gspec[0, 1])
    ax_c = fig.add_subplot(gspec[1, 0])
    ax_d = fig.add_subplot(gspec[1, 1])

    legend_patches = [
        mpatches.Patch(facecolor=C_WELL,      label="Well-studied"),
        mpatches.Patch(facecolor=C_NEGLECTED,  label="Neglected Outcomes"),
        mpatches.Patch(facecolor="#E8963A",    label="Neglected Populations"),
    ]

    C_NEG_POP = "#E8963A"

    # ── Panel A: Study Design Mix ─────────────────────────────────────────
    design_families = ["Epidemiological", "Synthesis", "Modelling", "Qualitative", "Other"]
    y_pos = np.arange(len(design_families))
    bar_h = 0.25

    vals_well   = [tier_design_pct(well_key, f) for f in design_families]
    vals_neg_o  = [tier_design_pct(neg_out,  f) for f in design_families]
    vals_neg_p  = [tier_design_pct(neg_pop,  f) for f in design_families]

    ax_a.barh(y_pos + bar_h, vals_well,  bar_h, color=C_WELL,      label="Well-studied", zorder=2)
    ax_a.barh(y_pos,         vals_neg_o, bar_h, color=C_NEGLECTED,  label="Neglected Outcomes", zorder=2)
    ax_a.barh(y_pos - bar_h, vals_neg_p, bar_h, color=C_NEG_POP,    label="Neglected Populations", zorder=2)

    ax_a.set_yticks(y_pos)
    ax_a.set_yticklabels(design_families, fontsize=FONT_AX)
    ax_a.set_xlabel("% of papers in tier", fontsize=FONT_L)
    ax_a.set_title("(A)  Study Design Mix", fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_a.set_xlim(0, max(vals_well + vals_neg_o + vals_neg_p) * 1.35 or 10)
    ax_a.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax_a.grid(axis="x", color=C_GRID, linewidth=0.6, zorder=0)
    ax_a.legend(handles=legend_patches, fontsize=7.5, loc="lower right",
                framealpha=0.95, edgecolor="#DDDDDD")
    _despine(ax_a)

    # ── Panel B: Geographic & Citation Profile ────────────────────────────
    metrics_labels = ["US-concentrated\n(% papers)", "Median\nCitations (÷10)",
                      "Open Access\n(%)", "Bio Mechanisms\n(mean per paper × 10)",
                      "Social Determinants\n(mean per paper × 10)"]

    def radar_vals(tier_key: str) -> list[float]:
        med_cit = mean_metric(tier_key, "median_citations")
        return [
            mean_metric(tier_key, "us_pct"),
            med_cit / 10,
            mean_metric(tier_key, "oa_pct"),
            mean_metric(tier_key, "bio_depth") * 10,
            mean_metric(tier_key, "soc_depth") * 10,
        ]

    vals_w = radar_vals(well_key)
    vals_o = radar_vals(neg_out)
    vals_p = radar_vals(neg_pop)

    x_pos = np.arange(len(metrics_labels))
    bar_h2 = 0.25

    ax_b.bar(x_pos + bar_h2, vals_w,  bar_h2, color=C_WELL,     zorder=2)
    ax_b.bar(x_pos,          vals_o,  bar_h2, color=C_NEGLECTED, zorder=2)
    ax_b.bar(x_pos - bar_h2, vals_p,  bar_h2, color=C_NEG_POP,   zorder=2)

    ax_b.set_xticks(x_pos)
    ax_b.set_xticklabels(metrics_labels, fontsize=7.5, ha="center")
    ax_b.set_ylabel("Metric value (normalised)", fontsize=FONT_L)
    ax_b.set_title("(B)  Structural Profile Comparison", fontsize=FONT_T,
                   fontweight="bold", pad=6, loc="left")
    ax_b.grid(axis="y", color=C_GRID, linewidth=0.6, zorder=0)
    ax_b.legend(handles=legend_patches, fontsize=7.5, loc="upper right",
                framealpha=0.95, edgecolor="#DDDDDD")
    _despine(ax_b)

    # ── Panel C: Journal Domain Distribution ──────────────────────────────
    domains = ["Public Health /\nMedicine", "Environmental\nScience",
               "Ecology /\nNatural Science", "Social\nScience", "Other /\nUnknown"]
    domains_raw = ["Public Health / Medicine", "Environmental Science",
                   "Ecology / Natural Science", "Social Science"]

    def dom_vals(tier_key: str) -> list[float]:
        out = []
        for d in domains_raw:
            out.append(tier_domain_pct(tier_key, d))
        # "Other / Unknown" = remainder
        out.append(max(0, 100 - sum(out)))
        return out

    dv_w = dom_vals(well_key)
    dv_o = dom_vals(neg_out)
    dv_p = dom_vals(neg_pop)

    x3 = np.arange(len(domains))
    ax_c.bar(x3 + bar_h2, dv_w, bar_h2, color=C_WELL,     label="Well-studied",          zorder=2)
    ax_c.bar(x3,          dv_o, bar_h2, color=C_NEGLECTED, label="Neglected Outcomes",    zorder=2)
    ax_c.bar(x3 - bar_h2, dv_p, bar_h2, color=C_NEG_POP,   label="Neglected Populations", zorder=2)

    ax_c.set_xticks(x3)
    ax_c.set_xticklabels(domains, fontsize=7.5, ha="center")
    ax_c.set_ylabel("% of papers in tier", fontsize=FONT_L)
    ax_c.set_title("(C)  Journal Disciplinary Field", fontsize=FONT_T,
                   fontweight="bold", pad=6, loc="left")
    ax_c.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax_c.grid(axis="y", color=C_GRID, linewidth=0.6, zorder=0)
    ax_c.legend(handles=legend_patches, fontsize=7.5, loc="upper right",
                framealpha=0.95, edgecolor="#DDDDDD")
    _despine(ax_c)

    # ── Panel D: Per-label citation & US concentration scatter ───────────
    label_order = (
        sorted(WELL_STUDIED_OUTCOMES | WELL_STUDIED_POPS) +
        sorted(NEGLECTED_OUTCOMES) +
        sorted(NEGLECTED_POPS)
    )
    label_order = [l for l in label_order if profiles.get(l, {}).get("n", 0) > 0]

    x_vals, y_vals, sizes, colors_d, labels_d = [], [], [], [], []
    for lbl in label_order:
        p = profiles[lbl]
        tier = tier_map.get(lbl, "")
        x_vals.append(p["us_pct"])
        y_vals.append(p["median_citations"] or 0)
        sizes.append(max(p["n"] * 12, 30))
        if tier == "Well-studied":
            colors_d.append(C_WELL)
        elif tier == "Neglected Outcome":
            colors_d.append(C_NEGLECTED)
        else:
            colors_d.append(C_NEG_POP)
        labels_d.append(lbl.replace("_", "\n").title())

    sc = ax_d.scatter(x_vals, y_vals, s=sizes, c=colors_d,
                      alpha=0.80, edgecolors="white", linewidths=0.8, zorder=3)

    # Annotate each point
    for xi, yi, lbl in zip(x_vals, y_vals, labels_d):
        ax_d.annotate(lbl, (xi, yi),
                      textcoords="offset points", xytext=(5, 5),
                      fontsize=6.5, color="#333333",
                      arrowprops=None)

    # Median reference lines
    med_x = np.median(x_vals) if x_vals else 50
    med_y = np.median(y_vals) if y_vals else 200
    ax_d.axvline(med_x, color="#AAAAAA", linewidth=0.8, linestyle="--", zorder=1)
    ax_d.axhline(med_y, color="#AAAAAA", linewidth=0.8, linestyle="--", zorder=1)
    ax_d.text(med_x + 0.5, ax_d.get_ylim()[1] if ax_d.get_ylim()[1] > 0 else 1,
              "median US%", fontsize=6.5, color="#AAAAAA", va="bottom")

    ax_d.set_xlabel("US-concentrated (% of papers)", fontsize=FONT_L)
    ax_d.set_ylabel("Median Citations", fontsize=FONT_L)
    ax_d.set_title("(D)  Geographic Concentration vs. Citation Impact",
                   fontsize=FONT_T, fontweight="bold", pad=6, loc="left")
    ax_d.grid(color=C_GRID, linewidth=0.6, zorder=0)
    ax_d.legend(handles=legend_patches, fontsize=7.5, loc="upper left",
                framealpha=0.95, edgecolor="#DDDDDD")
    _despine(ax_d)

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
    log.info("=== 06_gap_drivers_analysis.py started ===")

    health = load_merged(log)
    total_health = len(health)
    corpus_median_cit = health["cited_by_count"].median()
    log.info("Corpus median citations: %.0f", corpus_median_cit)

    # ── Build profiles for all gap categories ────────────────────────────
    all_outcome_labels = (
        WELL_STUDIED_OUTCOMES | NEGLECTED_OUTCOMES |
        {"mortality", "burns_injury"}   # include for completeness
    )
    all_pop_labels = (
        WELL_STUDIED_POPS | NEGLECTED_POPS
    )

    tier_map: dict[str, str] = {}
    for l in all_outcome_labels:
        tier_map[l] = "Well-studied" if l in WELL_STUDIED_OUTCOMES else "Neglected Outcome"
    for l in all_pop_labels:
        tier_map[l] = "Well-studied" if l in WELL_STUDIED_POPS else "Neglected Population"

    profiles: dict[str, dict] = {}
    for lbl in all_outcome_labels:
        profiles[lbl] = profile_papers(health, "_outcomes", lbl)
        log.info("[outcome/%s] n=%d  US=%.0f%%  med_cit=%s",
                 lbl, profiles[lbl]["n"], profiles[lbl]["us_pct"],
                 profiles[lbl]["median_citations"])
    for lbl in all_pop_labels:
        profiles[lbl] = profile_papers(health, "_pops", lbl)
        log.info("[pop/%s] n=%d  US=%.0f%%  med_cit=%s",
                 lbl, profiles[lbl]["n"], profiles[lbl]["us_pct"],
                 profiles[lbl]["median_citations"])

    # ── Driver inference ─────────────────────────────────────────────────
    drivers: dict[str, list[str]] = {}
    for lbl in {**{l: None for l in all_outcome_labels},
                **{l: None for l in all_pop_labels}}:
        drivers[lbl] = infer_drivers(lbl, profiles[lbl], corpus_median_cit)

    # ── Summary table ────────────────────────────────────────────────────
    summary_df = build_summary_table(profiles, tier_map)
    summary_df = summary_df.sort_values(["tier", "n_papers"], ascending=[True, False])

    tbl_dir = ROOT / "outputs/tables"
    tbl_dir.mkdir(parents=True, exist_ok=True)
    tbl_path = tbl_dir / "gap_drivers_summary.csv"
    summary_df.to_csv(tbl_path, index=False)
    log.info("Saved summary table → %s", tbl_path)

    # ── Figure ───────────────────────────────────────────────────────────
    fig_dir = ROOT / "outputs/figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    make_driver_figure(
        profiles, tier_map,
        fig_dir / "gap_drivers_comparison.png",
        log,
        dpi=cfg.get("figures", {}).get("dpi", 300),
    )

    # ── Interpretation report ─────────────────────────────────────────────
    rpt_dir = ROOT / "outputs/report"
    write_interpretation(
        profiles, tier_map, drivers,
        rpt_dir / "gap_drivers_interpretation.md",
        log,
    )

    # ── Console summary ───────────────────────────────────────────────────
    log.info("─" * 64)
    log.info("DRIVER SUMMARY")
    log.info("  Health papers analysed: %d", total_health)
    log.info("  Corpus median citations: %.0f", corpus_median_cit)
    log.info("\n  TOP STRUCTURAL DRIVERS IDENTIFIED:")

    # Identify the single most common driver pattern per neglected label
    neglected = sorted(
        [l for l in all_outcome_labels | all_pop_labels
         if tier_map.get(l, "").startswith("Neglected") and profiles[l]["n"] > 0],
        key=lambda l: profiles[l]["us_pct"], reverse=True,
    )
    for lbl in neglected:
        p = profiles[lbl]
        top_driver = drivers[lbl][0][:100] + "…" if drivers[lbl] else "—"
        log.info("  [%s] US=%.0f%% | cit=%.0f | driver: %s",
                 lbl, p["us_pct"], p["median_citations"] or 0, top_driver)

    log.info("─" * 64)
    log.info("=== 06_gap_drivers_analysis.py finished ===")


if __name__ == "__main__":
    main()
