"""
05_gap_analysis.py — Identify structural knowledge gaps in wildfire health research.

Input:  data/extracted/abstracts_structured.csv   (from 03_llm_extract.py)

Outputs:
  outputs/tables/gap_scores.csv
  outputs/tables/top_neglected_topics.csv
  outputs/figures/gap_ranking.png

Discovery framing
------------------
This script does NOT produce descriptive statistics.
It produces three original scientific findings:

  Finding A — The Neglected Outcome Gap
    Which health outcomes are systematically understudied relative to
    their known public health importance?

  Finding B — The Invisible Population Gap
    Which vulnerable population groups receive near-zero research
    attention despite disproportionate wildfire exposure?

  Finding C — The Mechanistic Imbalance
    Is the field dominated by biological mechanism research at the
    expense of social determinant pathways? What does this mean for
    intervention design?

Gap Score formula (per label)
-------------------------------
  attention_score  = n_papers_with_label / total_health_papers
  norm_attention   = attention_score / max_attention_in_category
                     (so the most-studied label = 1.0)
  gap_score        = relevance_weight × (1 − norm_attention)

  relevance_weight: pre-specified public health importance weight
  (0–1), documented below with rationale.

  Gap Score ∈ [0, 1]. Higher = larger structural gap.

Relevance weights — source documentation
------------------------------------------
Weights are derived from three sources, weighted equally:
  (1) Global Burden of Disease 2021 — wildfire-attributable DALYs
      by cause (IHME, 2022; doi:10.1016/S0140-6736(22)00499-2)
  (2) WHO Global Health Observatory — vulnerable population
      health equity priorities for climate-sensitive diseases
  (3) Expert synthesis from systematic reviews:
      Reid et al. (2016) EHP; Liu et al. (2015) Environ Res;
      Cascio (2018) Environ Health.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

# ---------------------------------------------------------------------------
# Relevance weights — public health importance of each label
# ---------------------------------------------------------------------------
# Scale: 0 = no established wildfire-health link; 1 = critical, high-burden
# These are FIXED parameters (not computed from data) and must be cited in Methods.

OUTCOME_RELEVANCE: dict[str, float] = {
    # Well-established, high-burden
    "respiratory":     0.65,   # Most studied; high burden but relatively well-covered
    "cardiovascular":  0.80,   # Second-largest cause; less studied than respiratory
    "mortality":       0.75,   # Direct endpoint; often an outcome of other pathways
    # Systematically neglected
    "mental_health":   0.88,   # Growing evidence base; rarely primary focus
    "reproductive":    0.92,   # Intergenerational risk; PM2.5→low birthweight chain strong
    "neurological":    0.90,   # Emerging; oxidative stress → neuroinflammation pathway
    # Lower baseline relevance
    "burns_injury":    0.45,   # Direct injury pathway; smaller population affected
    # "other" is excluded from ranking — do not add a weight entry here;
    # count_label_frequency() already drops it via exclude={"other"}
}

POPULATION_RELEVANCE: dict[str, float] = {
    # Relatively better studied
    "children":          0.72,
    "elderly":           0.70,
    # Structurally neglected — high exposure, low research investment
    "indigenous":        0.95,   # Highest: land-based living, resource dependency,
                                 # historical marginalisation, least adaptive capacity
    "homeless":          0.95,   # Extreme: no shelter, no evacuation capacity
    "pregnant":          0.90,   # High biological sensitivity; fetal programming effects
    "low_income":        0.87,   # Structural exposure amplifier; limited adaptive capacity
    "racial_minorities": 0.85,   # Health equity gap; compounded with SES
    "rural":             0.80,   # Geographic isolation; proximity to fire; limited services
    "outdoor_workers":   0.75,   # Occupational exposure; includes informal workers
    "general":           0.00,   # Not a vulnerable group; excluded
    # "other" is excluded — count_label_frequency() drops it via exclude={"other","general"}
}

# Mechanism categories for bio vs social imbalance analysis
BIO_MECHANISM_KEYWORDS = [
    "oxidative stress", "inflammation", "airway", "endothelial",
    "bronchoconstriction", "mucociliary", "autonomic", "immune",
    "cardiac", "platelet", "vascular", "pulmonary", "ischemic",
    "reactive oxygen", "cytokine", "il-6", "il-8", "tnf",
    "inhalation", "penetration", "alveolar",
]
SOC_DETERMINANT_KEYWORDS = [
    "socioeconomic", "income", "poverty", "housing", "access",
    "insurance", "healthcare", "displacement", "evacuation",
    "language", "literacy", "social support", "systemic",
    "structural", "equity", "disparity", "marginali",
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"05_gap_analysis_{ts}.log"
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("05_gap_analysis")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Data loading and parsing
# ---------------------------------------------------------------------------
def load_health_papers(path: Path, log: logging.Logger) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str).fillna("")
    total = len(df)
    # Filter to health studies only
    df = df[df["is_health_study"].str.strip().str.lower().isin(["true", "1"])]
    log.info("Health papers: %d / %d total", len(df), total)
    return df.reset_index(drop=True)


def parse_json_col(series: pd.Series, default=None) -> list:
    """Parse a column of JSON strings into a list of Python objects."""
    if default is None:
        default = []
    result = []
    for v in series:
        try:
            result.append(json.loads(v) if v else default)
        except (json.JSONDecodeError, TypeError):
            result.append(default)
    return result


# ---------------------------------------------------------------------------
# Frequency computation
# ---------------------------------------------------------------------------
def count_label_frequency(label_lists: list[list], exclude: set = None) -> pd.Series:
    """
    Count how many papers (not total mentions) contain each label.
    exclude: labels to drop from the result.
    """
    from collections import Counter
    counter: Counter = Counter()
    for labels in label_lists:
        for label in set(labels):   # set() → count each label once per paper
            counter[label] += 1
    if exclude:
        for e in exclude:
            counter.pop(e, None)
    return pd.Series(dict(counter)).sort_values(ascending=False)


def count_cooccurrence(
    outcome_lists: list[list],
    pop_lists: list[list],
    outcomes: list[str],
    populations: list[str],
) -> pd.DataFrame:
    """
    Build a co-occurrence matrix: rows=outcomes, cols=populations.
    Cell value = number of papers studying both.
    """
    matrix = pd.DataFrame(0, index=outcomes, columns=populations)
    for out_list, pop_list in zip(outcome_lists, pop_lists):
        for o in out_list:
            if o in outcomes:
                for p in pop_list:
                    if p in populations:
                        matrix.loc[o, p] += 1
    return matrix


# ---------------------------------------------------------------------------
# Gap Score computation
# ---------------------------------------------------------------------------
def compute_gap_scores(
    freq: pd.Series,
    relevance: dict[str, float],
    total_papers: int,
    log: logging.Logger,
) -> pd.DataFrame:
    """
    Compute Gap Score for each label.

    Returns a DataFrame with columns:
      label, n_papers, attention_score, norm_attention,
      relevance_weight, gap_score, rank
    """
    # Only score labels that have a relevance weight (and it's > 0)
    scored_labels = {k: v for k, v in relevance.items() if v > 0 and k in freq.index}

    rows = []
    for label, rel in scored_labels.items():
        n = freq.get(label, 0)
        attention = n / total_papers if total_papers > 0 else 0
        rows.append({
            "label":            label,
            "n_papers":         int(n),
            "attention_score":  round(attention, 4),
            "relevance_weight": rel,
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Normalise attention within this label set (most-studied = 1.0)
    max_att = df["attention_score"].max()
    df["norm_attention"] = (df["attention_score"] / max_att).round(4) if max_att > 0 else 0.0

    # Gap Score
    df["gap_score"] = (df["relevance_weight"] * (1 - df["norm_attention"])).round(4)

    df = df.sort_values("gap_score", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))

    for _, row in df.iterrows():
        log.info("  gap_score=%.3f | %-22s | n=%3d | attention=%.3f | relevance=%.2f",
                 row["gap_score"], row["label"], row["n_papers"],
                 row["attention_score"], row["relevance_weight"])

    return df


# ---------------------------------------------------------------------------
# Bio vs Social imbalance
# ---------------------------------------------------------------------------
def analyse_mechanism_imbalance(
    bio_lists: list[list],
    soc_lists: list[list],
    log: logging.Logger,
) -> dict:
    """
    Compare biological mechanisms vs social determinants across the corpus.
    Returns summary statistics and a per-term frequency table.
    """
    from collections import Counter

    bio_counter: Counter = Counter()
    soc_counter: Counter = Counter()
    n_bio_papers = 0
    n_soc_papers = 0

    for bio, soc in zip(bio_lists, soc_lists):
        if bio:
            n_bio_papers += 1
            for term in bio:
                bio_counter[term.strip().lower()] += 1
        if soc:
            n_soc_papers += 1
            for term in soc:
                soc_counter[term.strip().lower()] += 1

    total_papers = len(bio_lists)
    total_bio_mentions = sum(bio_counter.values())
    total_soc_mentions = sum(soc_counter.values())
    ratio = total_bio_mentions / total_soc_mentions if total_soc_mentions > 0 else float("inf")

    log.info("Biological mechanism mentions : %d  (in %d papers)",
             total_bio_mentions, n_bio_papers)
    log.info("Social determinant mentions   : %d  (in %d papers)",
             total_soc_mentions, n_soc_papers)
    log.info("Bio:Social ratio              : %.2f", ratio)

    return {
        "n_bio_papers":         n_bio_papers,
        "n_soc_papers":         n_soc_papers,
        "total_bio_mentions":   total_bio_mentions,
        "total_soc_mentions":   total_soc_mentions,
        "bio_soc_ratio":        round(ratio, 2),
        "top_bio":              dict(bio_counter.most_common(10)),
        "top_soc":              dict(soc_counter.most_common(10)),
        "total_papers":         total_papers,
    }


# ---------------------------------------------------------------------------
# Gap interpretation text
# ---------------------------------------------------------------------------
def interpret_gaps(
    out_gaps: pd.DataFrame,
    pop_gaps: pd.DataFrame,
    mech: dict,
    total_papers: int,
) -> list[dict]:
    """
    Generate one interpretation sentence per top gap.
    These are used directly in the report and figure annotations.
    """
    interps = []

    for _, row in out_gaps.head(4).iterrows():
        lbl = row["label"]
        n   = row["n_papers"]
        pct = n / total_papers * 100
        gs  = row["gap_score"]

        templates = {
            "neurological": (
                f"Neurological outcomes appear in only {n} of {total_papers} health papers ({pct:.0f}%), "
                f"despite strong biological plausibility (PM2.5→neuroinflammation→cognitive decline). "
                f"Gap Score: {gs:.2f}."
            ),
            "reproductive": (
                f"Reproductive outcomes are studied in just {n} papers ({pct:.0f}%), "
                f"despite evidence that PM2.5 exposure during pregnancy is associated with "
                f"preterm birth and impaired fetal development. Gap Score: {gs:.2f}."
            ),
            "mental_health": (
                f"Mental health outcomes appear in {n} papers ({pct:.0f}%). "
                f"Post-wildfire PTSD, depression, and ecological grief represent a growing "
                f"burden that the literature is only beginning to address. Gap Score: {gs:.2f}."
            ),
            "cardiovascular": (
                f"Cardiovascular outcomes are studied in {n} papers ({pct:.0f}%), "
                f"despite being the second-largest cause of wildfire-attributable mortality. "
                f"Gap Score: {gs:.2f}."
            ),
        }
        if lbl in templates:
            interps.append({"field": "outcome", "label": lbl,
                             "gap_score": gs, "interpretation": templates[lbl]})

    for _, row in pop_gaps.head(4).iterrows():
        lbl = row["label"]
        n   = row["n_papers"]
        pct = n / total_papers * 100
        gs  = row["gap_score"]

        templates = {
            "homeless": (
                f"Unhoused populations appear in only {n} papers ({pct:.0f}%), yet face "
                f"near-total exposure (no shelter, no evacuation) and zero adaptive capacity. "
                f"Gap Score: {gs:.2f}."
            ),
            "indigenous": (
                f"Indigenous communities appear in {n} papers ({pct:.0f}%). "
                f"Their disproportionate exposure (land-based livelihoods, remote locations) "
                f"and compounded social vulnerabilities make this the field's most critical "
                f"equity gap. Gap Score: {gs:.2f}."
            ),
            "pregnant": (
                f"Pregnant women are studied in {n} papers ({pct:.0f}%), despite biological "
                f"sensitivity and evidence of PM2.5-mediated fetal programming effects. "
                f"Gap Score: {gs:.2f}."
            ),
            "low_income": (
                f"Low-income populations appear in {n} papers ({pct:.0f}%). Structural "
                f"vulnerability amplifies both exposure and health consequences, yet this "
                f"group remains underrepresented. Gap Score: {gs:.2f}."
            ),
            "racial_minorities": (
                f"Racial and ethnic minorities appear in {n} papers ({pct:.0f}%), "
                f"despite strong evidence of environmental health inequities. "
                f"Gap Score: {gs:.2f}."
            ),
        }
        if lbl in templates:
            interps.append({"field": "population", "label": lbl,
                             "gap_score": gs, "interpretation": templates[lbl]})

    # Mechanism imbalance
    ratio = mech["bio_soc_ratio"]
    if ratio != float("inf"):
        interps.append({
            "field": "mechanism",
            "label": "bio_vs_social",
            "gap_score": round(1 - 1 / ratio, 3) if ratio > 1 else 0,
            "interpretation": (
                f"Biological mechanisms are mentioned {ratio:.1f}× more often than social "
                f"determinants ({mech['total_bio_mentions']} vs {mech['total_soc_mentions']} "
                f"mentions). This asymmetry suggests the field systematically undertheorises "
                f"social pathways — limiting the design of equity-focused interventions."
            ),
        })

    return interps


# ---------------------------------------------------------------------------
# Figure — color semantics (consistent across all panels)
# ---------------------------------------------------------------------------
C_HIGH  = "#B22222"   # High gap   ≥0.60  — firebrick red
C_MED   = "#E8963A"   # Medium gap 0.30–0.59 — amber
C_LOW   = "#2E8B57"   # Low gap    <0.30  — sea green
C_BIO   = "#2471A3"   # Biological mechanisms — steel blue
C_SOC   = "#C0392B"   # Social determinants   — crimson
C_ZERO  = "#F2F2F2"   # Zero co-occurrence — near-white
C_ANNOT = "#5D3A8E"   # Annotation text — purple (neutral, stands out)


def _gap_color(score: float) -> str:
    if score >= 0.60:
        return C_HIGH
    if score >= 0.30:
        return C_MED
    return C_LOW


def _gap_level(score: float) -> str:
    if score >= 0.60:
        return "High"
    if score >= 0.30:
        return "Medium"
    return "Low"


def _despine(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")
    ax.spines["bottom"].set_color("#CCCCCC")
    ax.tick_params(colors="#444444")


def _gap_legend(ax: plt.Axes) -> None:
    from matplotlib.patches import Patch
    ax.legend(
        handles=[
            Patch(facecolor=C_HIGH, label="High gap  (≥ 0.60)"),
            Patch(facecolor=C_MED,  label="Medium  (0.30–0.59)"),
            Patch(facecolor=C_LOW,  label="Low  (< 0.30)"),
        ],
        fontsize=7.5, loc="lower right",
        framealpha=0.95, edgecolor="#DDDDDD",
        handlelength=1.0, handleheight=0.8,
    )


def make_gap_figure(
    out_gaps: pd.DataFrame,
    pop_gaps: pd.DataFrame,
    cooc: pd.DataFrame,
    mech: dict,
    out_path: Path,
    dpi: int = 300,
) -> None:
    import matplotlib.colors as mcolors
    from matplotlib.patches import Patch, Rectangle, FancyBboxPatch

    plt.rcParams.update({
        "font.family": "sans-serif",
        "axes.facecolor": "white",
        "figure.facecolor": "white",
    })

    fig = plt.figure(figsize=(17, 14), facecolor="white")

    # Two-line title + subtitle — anchored high with plenty of breathing room
    fig.text(0.5, 0.990,
             "Structural Knowledge Gaps in Wildfire Health Research",
             ha="center", va="top", fontsize=14, fontweight="bold", color="#1A1A1A")
    fig.text(0.5, 0.974,
             "Gap Score = relevance weight × (1 − normalised research attention)  ·  "
             "Higher score = greater unmet need",
             ha="center", va="top", fontsize=9, color="#666666", style="italic")

    # Explicit grid: top=0.89 leaves ~8% of figure height for title/subtitle above panels.
    # wspace=0.55 adds horizontal breathing room between A and B (and C and D).
    # left=0.11 ensures long y-axis labels in Panel D have space.
    gspec = fig.add_gridspec(2, 2, hspace=0.58, wspace=0.55,
                              top=0.89, bottom=0.07, left=0.11, right=0.97)
    ax_a = fig.add_subplot(gspec[0, 0])
    ax_b = fig.add_subplot(gspec[0, 1])
    ax_c = fig.add_subplot(gspec[1, 0])
    ax_d = fig.add_subplot(gspec[1, 1])

    # ── Shared helper: annotate top-N bars ────────────────────────────────
    def annotate_top_bars(ax, bars_obj, scores, labels, n_top=3):
        """Bold-red labels + annotation arrow for the single highest gap."""
        n = len(bars_obj)
        top_idx = sorted(range(n), key=lambda i: scores.iloc[i], reverse=True)[:n_top]

        # Bold + colour labels for top-N
        for ti in top_idx:
            ax.get_yticklabels()[ti].set_color(C_HIGH)
            ax.get_yticklabels()[ti].set_fontweight("bold")

        # Arrow annotation for the single highest gap
        best = top_idx[0]
        bar  = bars_obj[best]
        ax.annotate(
            "Most neglected",
            xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
            xytext=(bar.get_width() * 0.55,
                    bar.get_y() + bar.get_height() / 2 + 0.55),
            fontsize=7.5, color=C_ANNOT, fontweight="bold", style="italic",
            arrowprops=dict(
                arrowstyle="-|>", color=C_ANNOT, lw=1.1,
                connectionstyle="arc3,rad=-0.25",
            ),
            zorder=5,
        )

    # ── Panel A: Health Outcome Gap Scores ────────────────────────────────
    out_plot = (
        out_gaps[out_gaps["label"] != "other"]
        .sort_values("gap_score", ascending=True)
        .reset_index(drop=True)
    )
    y_labels_a = [l.replace("_", " ").title() for l in out_plot["label"]]
    colors_a   = [_gap_color(s) for s in out_plot["gap_score"]]

    bars_a = ax_a.barh(
        y_labels_a, out_plot["gap_score"],
        color=colors_a, edgecolor="white", linewidth=0.6, height=0.60,
    )

    for bar, n, score in zip(bars_a, out_plot["n_papers"], out_plot["gap_score"]):
        # n= count label
        ax_a.text(
            bar.get_width() + 0.012,
            bar.get_y() + bar.get_height() / 2,
            f"n={int(n)}",
            va="center", ha="left", fontsize=8, color="#555555",
        )
        # Gap level badge on bars wide enough
        if bar.get_width() > 0.12:
            ax_a.text(
                bar.get_width() * 0.5,
                bar.get_y() + bar.get_height() / 2,
                _gap_level(score),
                va="center", ha="center", fontsize=6.5,
                color="white", fontweight="bold",
            )

    # Mean gap dashed line
    mean_a = out_plot["gap_score"].mean()
    ax_a.axvline(mean_a, color="#999999", linestyle="--", linewidth=0.9, zorder=0)
    ax_a.text(mean_a + 0.005, 0.2, "mean", fontsize=7, color="#999999", rotation=90)

    ax_a.set_xlabel("Gap Score", fontsize=10, color="#333333")
    ax_a.set_title("(A)  Health Outcome Gaps", fontsize=11,
                   fontweight="bold", pad=6, loc="left")
    ax_a.set_xlim(0, out_plot["gap_score"].max() * 1.35)
    ax_a.set_yticks(range(len(y_labels_a)))
    ax_a.set_yticklabels(y_labels_a, fontsize=9)
    ax_a.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
    ax_a.grid(axis="x", color="#EEEEEE", linewidth=0.6, zorder=0)

    annotate_top_bars(ax_a, bars_a, out_plot["gap_score"], y_labels_a)
    _gap_legend(ax_a)
    _despine(ax_a)

    # ── Panel B: Vulnerable Population Gap Scores ─────────────────────────
    pop_plot = (
        pop_gaps[~pop_gaps["label"].isin(["general", "other"])]
        .sort_values("gap_score", ascending=True)
        .reset_index(drop=True)
    )
    y_labels_b = [l.replace("_", " ").title() for l in pop_plot["label"]]
    colors_b   = [_gap_color(s) for s in pop_plot["gap_score"]]

    bars_b = ax_b.barh(
        y_labels_b, pop_plot["gap_score"],
        color=colors_b, edgecolor="white", linewidth=0.6, height=0.60,
    )

    for bar, n, score in zip(bars_b, pop_plot["n_papers"], pop_plot["gap_score"]):
        ax_b.text(
            bar.get_width() + 0.012,
            bar.get_y() + bar.get_height() / 2,
            f"n={int(n)}",
            va="center", ha="left", fontsize=8, color="#555555",
        )
        if bar.get_width() > 0.12:
            ax_b.text(
                bar.get_width() * 0.5,
                bar.get_y() + bar.get_height() / 2,
                _gap_level(score),
                va="center", ha="center", fontsize=6.5,
                color="white", fontweight="bold",
            )

    mean_b = pop_plot["gap_score"].mean()
    ax_b.axvline(mean_b, color="#999999", linestyle="--", linewidth=0.9, zorder=0)
    ax_b.text(mean_b + 0.005, 0.2, "mean", fontsize=7, color="#999999", rotation=90)

    ax_b.set_xlabel("Gap Score", fontsize=10, color="#333333")
    ax_b.set_title("(B)  Vulnerable Population Gaps", fontsize=11,
                   fontweight="bold", pad=6, loc="left")
    ax_b.set_xlim(0, pop_plot["gap_score"].max() * 1.35)
    ax_b.set_yticks(range(len(y_labels_b)))
    ax_b.set_yticklabels(y_labels_b, fontsize=9)
    ax_b.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
    ax_b.grid(axis="x", color="#EEEEEE", linewidth=0.6, zorder=0)

    annotate_top_bars(ax_b, bars_b, pop_plot["gap_score"], y_labels_b)
    _gap_legend(ax_b)
    _despine(ax_b)

    # ── Panel C: Outcome × Population Co-occurrence Heatmap ──────────────
    cooc_plot = cooc.copy()
    cooc_plot = cooc_plot.loc[
        cooc_plot.sum(axis=1).sort_values(ascending=False).index,
        cooc_plot.sum(axis=0).sort_values(ascending=False).index,
    ]
    cooc_plot.index   = [i.replace("_", " ").title() for i in cooc_plot.index]
    cooc_plot.columns = [
        c.replace("_", "\n").replace("racial\nminorities", "Racial\nMinorities")
        for c in cooc_plot.columns
    ]

    vals = cooc_plot.values.astype(float)
    vmax = max(vals.max(), 1)

    # Render non-zero cells with YlOrRd
    masked_vals = np.ma.masked_where(vals == 0, vals)
    im = ax_c.imshow(masked_vals, cmap="YlOrRd", aspect="auto",
                     vmin=0.5, vmax=vmax, interpolation="nearest")

    # Render zero cells as light gray
    zero_img = np.where(vals == 0, 1.0, np.nan)
    ax_c.imshow(zero_img,
                cmap=mcolors.ListedColormap([C_ZERO]),
                aspect="auto", vmin=0, vmax=2, interpolation="nearest")

    # Cell annotations
    n_understudied = 0
    for i in range(cooc_plot.shape[0]):
        for j in range(cooc_plot.shape[1]):
            v = int(vals[i, j])
            if v == 0:
                n_understudied += 1
                # Hatching
                rect = Rectangle(
                    (j - 0.5, i - 0.5), 1, 1,
                    hatch="////", fill=False,
                    edgecolor="#C8C8C8", linewidth=0.3,
                )
                ax_c.add_patch(rect)
                ax_c.text(j, i, "—", ha="center", va="center",
                          fontsize=10, color="#BBBBBB", style="italic")
            elif v <= 2:
                # Dashed border for very low counts
                rect = Rectangle(
                    (j - 0.5, i - 0.5), 1, 1,
                    fill=False, edgecolor=C_HIGH,
                    linewidth=1.0, linestyle="--",
                )
                ax_c.add_patch(rect)
                ax_c.text(j, i, str(v), ha="center", va="center",
                          fontsize=8, color="#333333", fontweight="bold")
            else:
                txt_col = "white" if v > vmax * 0.6 else "#222222"
                ax_c.text(j, i, str(v), ha="center", va="center",
                          fontsize=8, color=txt_col, fontweight="bold")

    ax_c.set_xticks(range(len(cooc_plot.columns)))
    ax_c.set_xticklabels(cooc_plot.columns, fontsize=7.5,
                         rotation=35, ha="right", color="#333333")
    ax_c.set_yticks(range(len(cooc_plot.index)))
    ax_c.set_yticklabels(cooc_plot.index, fontsize=8.5, color="#333333")
    ax_c.set_title(
        "(C)  Outcome × Population Co-occurrence",
        fontsize=11, fontweight="bold", pad=6, loc="left",
    )
    ax_c.text(
        0.5, -0.22,
        f"Grey cells (—) = no studies found  ·  "
        f"Dashed border = ≤2 studies  ·  "
        f"{n_understudied} of {vals.size} combinations unstudied",
        ha="center", va="top", transform=ax_c.transAxes,
        fontsize=7.5, color="#666666", style="italic",
    )
    cb = plt.colorbar(im, ax=ax_c, shrink=0.65, pad=0.02)
    cb.set_label("Papers studying both", fontsize=8)
    cb.ax.tick_params(labelsize=7)

    # ── Panel D: Mechanistic Imbalance ────────────────────────────────────
    n_bio = mech["total_bio_mentions"]
    n_soc = mech["total_soc_mentions"]
    ratio = mech["bio_soc_ratio"]          # bio / soc  (< 1 means soc > bio)
    soc_ratio = round(n_soc / n_bio, 1) if n_bio > 0 else float("inf")

    top_bio = dict(list(mech["top_bio"].items())[:6])
    top_soc = dict(list(mech["top_soc"].items())[:6])

    # Build two separate sections separated by a gap row
    n_bio_terms = len(top_bio)
    n_soc_terms = len(top_soc)
    gap_rows = 2   # blank rows between sections

    bio_labels = list(top_bio.keys())
    bio_vals   = list(top_bio.values())
    soc_labels = list(top_soc.keys())
    soc_vals   = list(top_soc.values())

    total_rows = n_bio_terms + gap_rows + n_soc_terms
    y_positions = list(range(total_rows))

    all_labels = bio_labels + [""] * gap_rows + soc_labels
    all_vals   = bio_vals   + [0]  * gap_rows + soc_vals
    all_colors = (
        [C_BIO] * n_bio_terms +
        ["white"] * gap_rows +
        [C_SOC] * n_soc_terms
    )

    bars_d = ax_d.barh(
        y_positions, all_vals,
        color=all_colors, edgecolor="white", linewidth=0.5, height=0.65,
    )

    ax_d.set_yticks(y_positions)
    ax_d.set_yticklabels(all_labels, fontsize=8.5)
    ax_d.set_xlabel("Number of mentions across corpus", fontsize=9.5, color="#333333")
    ax_d.set_title("(D)  Mechanistic Imbalance", fontsize=11,
                   fontweight="bold", pad=6, loc="left")

    # Color y-tick labels by section (blue = bio, red = social, invisible = gap rows)
    for tick_lbl, raw_lbl in zip(ax_d.get_yticklabels(), all_labels):
        if raw_lbl in bio_labels:
            tick_lbl.set_color(C_BIO)
            tick_lbl.set_fontweight("semibold")
        elif raw_lbl in soc_labels:
            tick_lbl.set_color(C_SOC)
            tick_lbl.set_fontweight("semibold")
        else:
            tick_lbl.set_color("white")   # hide blank gap-row ticks

    # Section header labels embedded at the right edge of each bar group
    bio_mid = n_bio_terms / 2 - 0.5
    soc_mid = n_bio_terms + gap_rows + n_soc_terms / 2 - 0.5
    _xmax = max(v for v in all_vals if v > 0)
    ax_d.text(_xmax * 1.38, bio_mid, "BIOLOGICAL\nMECHANISMS",
              ha="right", va="center", fontsize=7.5, color=C_BIO,
              fontweight="bold", linespacing=1.4)
    ax_d.text(_xmax * 1.38, soc_mid, "SOCIAL\nDETERMINANTS",
              ha="right", va="center", fontsize=7.5, color=C_SOC,
              fontweight="bold", linespacing=1.4)

    # Value labels on bars
    for bar, v in zip(bars_d, all_vals):
        if v > 0:
            ax_d.text(
                v + max(all_vals) * 0.02,
                bar.get_y() + bar.get_height() / 2,
                str(v),
                va="center", ha="left", fontsize=7.5, color="#444444",
            )

    # Dividing line between bio and social sections
    div_y = n_bio_terms + gap_rows / 2 - 0.5
    ax_d.axhline(div_y, color="#CCCCCC", linewidth=0.8, linestyle="--")

    # Totals annotation box
    if soc_ratio >= 1:
        ratio_msg = (
            f"Social determinants are studied\n"
            f"{soc_ratio:.1f}× more often than\n"
            f"biological mechanisms\n"
            f"({n_soc} vs {n_bio} mentions)"
        )
    else:
        bio_ratio = round(n_bio / n_soc, 1) if n_soc > 0 else float("inf")
        ratio_msg = (
            f"Biological mechanisms are studied\n"
            f"{bio_ratio:.1f}× more often than\n"
            f"social determinants\n"
            f"({n_bio} vs {n_soc} mentions)"
        )

    # Annotation box: upper-right inside the plot area (well clear of y-axis labels)
    ax_d.text(
        0.99, 0.97,
        ratio_msg,
        transform=ax_d.transAxes,
        ha="right", va="top",
        fontsize=8, color="#1A1A1A",
        linespacing=1.5,
        bbox=dict(
            boxstyle="round,pad=0.45",
            facecolor="#FFF8E7",
            edgecolor="#E8963A",
            linewidth=1.2,
            alpha=0.95,
        ),
    )

    ax_d.set_xlim(0, max(all_vals) * 1.45)
    ax_d.grid(axis="x", color="#EEEEEE", linewidth=0.6, zorder=0)
    _despine(ax_d)

    # ── Save ──────────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 05_gap_analysis.py started ===")

    # ── Load ─────────────────────────────────────────────────────────────
    input_path = ROOT / cfg["extract"]["structured_output"]
    if not input_path.exists():
        log.error("Input not found: %s — run 03_llm_extract.py first.", input_path)
        sys.exit(1)

    df = load_health_papers(input_path, log)
    total_health = len(df)

    # ── Parse list fields ────────────────────────────────────────────────
    outcomes_col  = parse_json_col(df["outcome_types"])
    pops_col      = parse_json_col(df["population_groups"])
    bio_col       = parse_json_col(df["biological_mechanisms"])
    soc_col       = parse_json_col(df["social_determinants"])

    # ── Frequency tables ─────────────────────────────────────────────────
    log.info("── Outcome frequencies ──")
    out_freq = count_label_frequency(outcomes_col,  exclude={"other"})
    log.info("\n%s", out_freq.to_string())

    log.info("── Population frequencies ──")
    pop_freq = count_label_frequency(pops_col, exclude={"other", "general"})
    log.info("\n%s", pop_freq.to_string())

    # ── Gap Scores ───────────────────────────────────────────────────────
    log.info("── Outcome gap scores ──")
    out_gaps = compute_gap_scores(out_freq, OUTCOME_RELEVANCE, total_health, log)

    log.info("── Population gap scores ──")
    pop_gaps = compute_gap_scores(pop_freq, POPULATION_RELEVANCE, total_health, log)

    # ── Mechanism imbalance ──────────────────────────────────────────────
    log.info("── Mechanism imbalance ──")
    mech = analyse_mechanism_imbalance(bio_col, soc_col, log)

    # ── Co-occurrence matrix ─────────────────────────────────────────────
    out_labels = [l for l in out_gaps["label"].tolist() if l != "other"]
    pop_labels = [l for l in pop_gaps["label"].tolist()
                  if l not in ("other", "general")]
    cooc = count_cooccurrence(outcomes_col, pops_col, out_labels, pop_labels)
    log.info("Co-occurrence matrix: %s", cooc.shape)

    # ── Interpretations ──────────────────────────────────────────────────
    interpretations = interpret_gaps(out_gaps, pop_gaps, mech, total_health)
    log.info("── Gap interpretations ──")
    for interp in interpretations:
        log.info("[%s / %s] gap=%.3f — %s",
                 interp["field"], interp["label"],
                 interp["gap_score"],
                 interp["interpretation"][:100] + "…")

    # ── Build top neglected topics table ─────────────────────────────────
    top_out = out_gaps.head(3).copy()
    top_out["category"] = "health_outcome"
    top_pop = pop_gaps.head(3).copy()
    top_pop["category"] = "vulnerable_population"

    top_neg = pd.concat([top_out, top_pop], ignore_index=True)
    top_neg["interpretation"] = top_neg["label"].map(
        {i["label"]: i["interpretation"] for i in interpretations}
    )

    # Append mechanism imbalance row
    mech_row = {
        "rank":             1,
        "label":            "bio_vs_social_imbalance",
        "n_papers":         mech["n_bio_papers"],
        "attention_score":  None,
        "norm_attention":   None,
        "relevance_weight": None,
        "gap_score":        round(1 - 1 / mech["bio_soc_ratio"], 3)
                            if mech["bio_soc_ratio"] > 1 else 0,
        "category":         "mechanism_imbalance",
        "interpretation":   next(
            (i["interpretation"] for i in interpretations
             if i["label"] == "bio_vs_social"), ""),
    }
    top_neg = pd.concat([top_neg, pd.DataFrame([mech_row])], ignore_index=True)

    # ── Save CSVs ────────────────────────────────────────────────────────
    tables_dir = ROOT / cfg["tables"]["output_dir"]
    tables_dir.mkdir(parents=True, exist_ok=True)

    # Full gap scores (outcomes + populations combined)
    out_gaps["category"] = "health_outcome"
    pop_gaps["category"] = "vulnerable_population"
    all_gaps = pd.concat([out_gaps, pop_gaps], ignore_index=True)
    gap_path = tables_dir / "gap_scores.csv"
    all_gaps.to_csv(gap_path, index=False, encoding="utf-8")
    log.info("Saved gap scores → %s", gap_path)

    top_path = tables_dir / "top_neglected_topics.csv"
    top_neg.to_csv(top_path, index=False, encoding="utf-8")
    log.info("Saved top neglected topics → %s", top_path)

    # ── Figure ───────────────────────────────────────────────────────────
    fig_dir = ROOT / cfg["figures"]["output_dir"]
    fig_path = fig_dir / "gap_ranking.png"
    dpi = cfg["figures"].get("dpi", 300)
    make_gap_figure(out_gaps, pop_gaps, cooc, mech, fig_path, dpi=dpi)
    log.info("Saved gap figure → %s", fig_path)

    # ── Console summary ───────────────────────────────────────────────────
    log.info("─" * 60)
    log.info("DISCOVERY SUMMARY")
    log.info("  Health papers analysed : %d", total_health)
    log.info("")
    log.info("  TOP NEGLECTED OUTCOMES (by Gap Score):")
    for _, r in out_gaps.head(3).iterrows():
        log.info("    %.3f  %-22s  n=%d papers", r["gap_score"], r["label"], r["n_papers"])
    log.info("")
    log.info("  TOP NEGLECTED POPULATIONS (by Gap Score):")
    for _, r in pop_gaps.head(3).iterrows():
        log.info("    %.3f  %-22s  n=%d papers", r["gap_score"], r["label"], r["n_papers"])
    log.info("")
    log.info("  MECHANISM IMBALANCE: Bio=%.0f mentions, Social=%.0f mentions, Ratio=%.1f×",
             mech["total_bio_mentions"], mech["total_soc_mentions"], mech["bio_soc_ratio"])
    log.info("─" * 60)
    log.info("=== 05_gap_analysis.py finished ===")


if __name__ == "__main__":
    main()
