"""
04_figures.py — Generate publication-quality figures for the literature review.

Input:  data/classified/results_classified.csv   (from 03_classify.py)
Output: outputs/figures/*.png

Figures produced
----------------
  01_publications_per_year.png  — annual publication trend (bar + line)
  02_theme_distribution.png     — primary topic breakdown (horizontal bar)
  03_top_journals.png           — top N journals by paper count (horizontal bar)
  04_top_countries.png          — top N first-author countries (horizontal bar)
  05_open_access_share.png      — open-access vs. closed (pie)

Style
-----
All figures follow a clean academic style suitable for top journals:
  - White background, minimal spines (top/right removed)
  - Muted, colourblind-friendly palette (ColorBrewer Set2 / custom)
  - 300 DPI PNG, tight layout
  - Consistent font sizes: title 13pt, axis labels 11pt, tick labels 10pt
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe for scripting
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import seaborn as sns
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

# ---------------------------------------------------------------------------
# Global style — applied once at module level
# ---------------------------------------------------------------------------
FONT_TITLE  = 13
FONT_LABEL  = 11
FONT_TICK   = 10
FONT_ANNOT  = 9

# Colourblind-friendly palette (ColorBrewer Set2, 8 colours, then extended)
PALETTE_QUAL = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2",
    "#59A14F", "#EDC948", "#B07AA1", "#FF9DA7",
    "#9C755F", "#BAB0AC",
]

def apply_global_style() -> None:
    plt.rcParams.update({
        "figure.facecolor":   "white",
        "axes.facecolor":     "white",
        "axes.edgecolor":     "#333333",
        "axes.linewidth":     0.8,
        "axes.spines.top":    False,
        "axes.spines.right":  False,
        "axes.grid":          True,
        "axes.grid.axis":     "x",       # horizontal grid only (for bar charts)
        "grid.color":         "#E5E5E5",
        "grid.linewidth":     0.6,
        "font.family":        "sans-serif",
        "font.size":          FONT_TICK,
        "xtick.labelsize":    FONT_TICK,
        "ytick.labelsize":    FONT_TICK,
        "legend.fontsize":    FONT_TICK,
        "legend.frameon":     False,
        "savefig.dpi":        300,
        "savefig.bbox":       "tight",
        "savefig.facecolor":  "white",
    })


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = log_dir / f"04_figures_{timestamp}.log"
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("04_figures")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def save_fig(fig: plt.Figure, path: Path, log: logging.Logger) -> None:
    fig.savefig(path)
    plt.close(fig)
    log.info("Saved → %s", path)


def label_bar(ax: plt.Axes, orientation: str = "h", fmt: str = "{:.0f}") -> None:
    """Annotate bar ends with their value."""
    for patch in ax.patches:
        if orientation == "h":
            x = patch.get_width()
            y = patch.get_y() + patch.get_height() / 2
            ax.text(x + ax.get_xlim()[1] * 0.01, y, fmt.format(x),
                    va="center", ha="left", fontsize=FONT_ANNOT, color="#333333")
        else:
            x = patch.get_x() + patch.get_width() / 2
            y = patch.get_height()
            ax.text(x, y + ax.get_ylim()[1] * 0.01, fmt.format(y),
                    va="bottom", ha="center", fontsize=FONT_ANNOT, color="#333333")


# ---------------------------------------------------------------------------
# Figure 1 — Publications per year
# ---------------------------------------------------------------------------
def plot_publications_per_year(df: pd.DataFrame, cfg: dict, out_dir: Path, log: logging.Logger) -> None:
    year_counts = (
        df["publication_year"]
        .dropna()
        .astype(int)
        .value_counts()
        .sort_index()
    )

    fig, ax = plt.subplots(figsize=(9, 4.5))

    # Bar chart
    bars = ax.bar(
        year_counts.index, year_counts.values,
        color=PALETTE_QUAL[0], alpha=0.75, width=0.7, zorder=2,
    )

    # Smoothed trend line (5-year rolling mean)
    if len(year_counts) >= 5:
        rolling = year_counts.rolling(window=5, center=True).mean()
        ax.plot(rolling.index, rolling.values,
                color=PALETTE_QUAL[2], linewidth=2, zorder=3,
                label="5-year rolling mean")
        ax.legend(loc="upper left", fontsize=FONT_ANNOT)

    ax.set_xlabel("Publication Year", fontsize=FONT_LABEL)
    ax.set_ylabel("Number of Publications", fontsize=FONT_LABEL)
    ax.set_title("Annual Publication Trend: Wildfire, Health & Vulnerable Populations",
                 fontsize=FONT_TITLE, fontweight="bold", pad=12)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=12))
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.set_xlim(year_counts.index.min() - 0.5, year_counts.index.max() + 0.5)
    ax.grid(axis="y", color="#E5E5E5", linewidth=0.6)
    ax.grid(axis="x", visible=False)

    fig.tight_layout()
    save_fig(fig, out_dir / "01_publications_per_year.png", log)


# ---------------------------------------------------------------------------
# Figure 2 — Theme distribution
# ---------------------------------------------------------------------------

# Human-readable labels for primary_topic values
THEME_LABELS: dict[str, str] = {
    "wildfire_exposure":       "Wildfire Exposure",
    "air_quality_pm25":        "Air Quality / PM2.5",
    "respiratory_health":      "Respiratory Health",
    "cardiovascular_health":   "Cardiovascular Health",
    "mental_health":           "Mental Health",
    "mortality":               "Mortality",
    "vulnerable_populations":  "Vulnerable Populations",
    "burn_severity_ecology":   "Burn Severity / Ecology",
    "other":                   "Other / Unclassified",
}

def plot_theme_distribution(df: pd.DataFrame, cfg: dict, out_dir: Path, log: logging.Logger) -> None:
    counts = df["primary_topic"].value_counts()
    counts.index = [THEME_LABELS.get(i, i) for i in counts.index]
    counts = counts.sort_values(ascending=True)  # ascending so largest is at top

    colours = PALETTE_QUAL[:len(counts)]

    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.barh(counts.index, counts.values, color=colours, edgecolor="white",
                   linewidth=0.5, zorder=2)
    label_bar(ax, orientation="h")

    ax.set_xlabel("Number of Publications", fontsize=FONT_LABEL)
    ax.set_title("Distribution of Papers by Primary Research Theme",
                 fontsize=FONT_TITLE, fontweight="bold", pad=12)
    ax.set_xlim(0, counts.max() * 1.15)
    ax.grid(axis="x", color="#E5E5E5", linewidth=0.6)
    ax.grid(axis="y", visible=False)

    fig.tight_layout()
    save_fig(fig, out_dir / "02_theme_distribution.png", log)


# ---------------------------------------------------------------------------
# Figure 3 — Top journals
# ---------------------------------------------------------------------------
def plot_top_journals(df: pd.DataFrame, cfg: dict, out_dir: Path, log: logging.Logger) -> None:
    top_n = cfg["figures"].get("top_n", 10)
    counts = (
        df["journal"]
        .replace("", pd.NA)
        .dropna()
        .value_counts()
        .head(top_n)
        .sort_values(ascending=True)
    )

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.barh(counts.index, counts.values,
            color=PALETTE_QUAL[1], edgecolor="white", linewidth=0.5, zorder=2)
    label_bar(ax, orientation="h")

    ax.set_xlabel("Number of Publications", fontsize=FONT_LABEL)
    ax.set_title(f"Top {top_n} Journals by Publication Count",
                 fontsize=FONT_TITLE, fontweight="bold", pad=12)
    ax.set_xlim(0, counts.max() * 1.18)
    ax.grid(axis="x", color="#E5E5E5", linewidth=0.6)
    ax.grid(axis="y", visible=False)

    # Truncate long journal names (FixedLocator required before set_yticklabels)
    labels = [n[:55] + "…" if len(n) > 55 else n for n in counts.index]
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=FONT_TICK)

    fig.tight_layout()
    save_fig(fig, out_dir / "03_top_journals.png", log)


# ---------------------------------------------------------------------------
# Figure 4 — Top countries
# ---------------------------------------------------------------------------

# Map ISO 2-letter codes to readable country names where useful
COUNTRY_NAMES: dict[str, str] = {
    "US": "United States", "GB": "United Kingdom", "AU": "Australia",
    "CA": "Canada",        "CN": "China",          "DE": "Germany",
    "FR": "France",        "ES": "Spain",          "IT": "Italy",
    "BR": "Brazil",        "IN": "India",          "NL": "Netherlands",
    "SE": "Sweden",        "NZ": "New Zealand",    "PT": "Portugal",
    "GR": "Greece",        "ZA": "South Africa",   "MX": "Mexico",
    "JP": "Japan",         "KR": "South Korea",
}

def plot_top_countries(df: pd.DataFrame, cfg: dict, out_dir: Path, log: logging.Logger) -> None:
    top_n = cfg["figures"].get("top_n", 10)
    counts = (
        df["first_author_country"]
        .replace("", pd.NA)
        .dropna()
        .map(lambda c: COUNTRY_NAMES.get(str(c).strip().upper(), str(c).strip()))
        .value_counts()
        .head(top_n)
        .sort_values(ascending=True)
    )

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.barh(counts.index, counts.values,
            color=PALETTE_QUAL[3], edgecolor="white", linewidth=0.5, zorder=2)
    label_bar(ax, orientation="h")

    ax.set_xlabel("Number of Publications", fontsize=FONT_LABEL)
    ax.set_title(f"Top {top_n} Countries by First-Author Affiliation",
                 fontsize=FONT_TITLE, fontweight="bold", pad=12)
    ax.set_xlim(0, counts.max() * 1.18)
    ax.grid(axis="x", color="#E5E5E5", linewidth=0.6)
    ax.grid(axis="y", visible=False)

    fig.tight_layout()
    save_fig(fig, out_dir / "04_top_countries.png", log)


# ---------------------------------------------------------------------------
# Figure 5 — Open-access share (pie)
# ---------------------------------------------------------------------------
def plot_open_access_share(df: pd.DataFrame, cfg: dict, out_dir: Path, log: logging.Logger) -> None:
    # is_open_access may be stored as string "True"/"False" after CSV round-trip
    oa_series = df["is_open_access"].map(
        lambda v: str(v).strip().lower() in ("true", "1", "yes")
    )
    oa_counts = oa_series.value_counts()
    n_open   = oa_counts.get(True,  0)
    n_closed = oa_counts.get(False, 0)
    total    = n_open + n_closed

    labels = ["Open Access", "Closed Access"]
    sizes  = [n_open, n_closed]
    colours = [PALETTE_QUAL[4], PALETTE_QUAL[8]]
    explode = (0.04, 0)   # slightly pop out the OA slice

    fig, ax = plt.subplots(figsize=(6, 6))
    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=None,          # we build a custom legend instead
        colors=colours,
        explode=explode,
        autopct=lambda p: f"{p:.1f}%\n({int(round(p * total / 100)):,})",
        pctdistance=0.72,
        startangle=90,
        wedgeprops={"edgecolor": "white", "linewidth": 1.5},
    )

    for at in autotexts:
        at.set_fontsize(FONT_TICK)
        at.set_color("white")
        at.set_fontweight("bold")

    ax.legend(
        wedges, labels,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.06),
        ncol=2,
        fontsize=FONT_LABEL,
        frameon=False,
    )
    ax.set_title("Open Access vs. Closed Access Publications",
                 fontsize=FONT_TITLE, fontweight="bold", pad=16)

    fig.tight_layout()
    save_fig(fig, out_dir / "05_open_access_share.png", log)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
PLOT_FUNCTIONS = {
    "publications_per_year": plot_publications_per_year,
    "theme_distribution":    plot_theme_distribution,
    "top_journals":          plot_top_journals,
    "top_countries":         plot_top_countries,
    "open_access_share":     plot_open_access_share,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 04_figures.py started ===")

    apply_global_style()

    # --- Load classified data ---
    input_path = ROOT / cfg["classify"]["output_path"]
    if not input_path.exists():
        log.error("Input file not found: %s", input_path)
        log.error("Run 03_classify.py first.")
        sys.exit(1)

    df = pd.read_csv(input_path, dtype=str)
    log.info("Loaded %d records from %s", len(df), input_path)

    # --- Output directory ---
    out_dir = ROOT / cfg["figures"]["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Run each requested plot ---
    requested = cfg["figures"].get("plots", list(PLOT_FUNCTIONS.keys()))
    for plot_name in requested:
        if plot_name not in PLOT_FUNCTIONS:
            log.warning("Unknown plot '%s' in config — skipping.", plot_name)
            continue
        log.info("Generating: %s", plot_name)
        try:
            PLOT_FUNCTIONS[plot_name](df, cfg, out_dir, log)
        except Exception as exc:
            log.error("Failed to generate '%s': %s", plot_name, exc, exc_info=True)

    log.info("=== 04_figures.py finished — figures saved to %s ===", out_dir)


if __name__ == "__main__":
    main()
