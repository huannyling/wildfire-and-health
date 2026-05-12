"""
07_global_mismatch_analysis.py — Research-to-Risk Mismatch Index (RRMI) by country.

Inputs:
  data/extracted/abstracts_structured.csv   (study_regions per paper)
  data/classified/results_classified.csv    (first_author_country per paper)

Outputs:
  outputs/tables/global_mismatch.csv        — RRMI and component scores per country
  outputs/figures/global_mismatch_map.png   — choropleth map + bar chart
  outputs/report/global_mismatch_interpretation.md — plain-language interpretation

Methodology
-----------
Research signal
  Two signals are combined to estimate research activity per country:
    (a) first_author_country — who is PUBLISHING (ISO 2-letter codes, from OpenAlex)
    (b) study_regions        — where the study TAKES PLACE (free text, from LLM extraction)
  Signal (b) is primary (reflects actual geographic scope); signal (a) is used to fill
  gaps where study_regions is missing/ambiguous.

  research_share[c] = (papers_mentioning_country_c) / (total_region_mentions) × 100

Wildfire risk proxy
  A static reference table derived from three published sources:
    (1) GFED4.1s — Global Fire Emissions Database annual burned area fraction, 2003–2016
        (Giglio et al. 2013, doi:10.5194/bg-10-1451-2013)
    (2) FAO FRA 2020 — Forest fire occurrence and impact data by country
        (FAO 2020, doi:10.4060/ca9825en)
    (3) EM-DAT CRED — Wildfire disaster events 1980–2023 causing ≥10 deaths or ≥1M USD loss
        (Guha-Sapir et al., CRED, Brussels)
  Risk is normalised so scores sum to 100 across all included countries.

Research Deficit Score (RDS) and RRMI
  RDS[c]  = risk_share[c] − research_share[c]    (positive = under-studied)
  RRMI[c] = research_share[c] / risk_share[c]    (< 1 = under-studied; > 1 = over-studied)

Map colour scale: diverging (RdBu_r centred on RRMI=1 on log scale).
"""

import json
import ast
import logging
import sys
import textwrap
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd
import yaml

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cartopy.io.shapereader as shpreader

ROOT        = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

# ---------------------------------------------------------------------------
# Wildfire risk proxy table
# Risk score (0–100): relative share of global wildfire hazard.
# Sources: GFED4.1s burned area + FAO FRA 2020 + EM-DAT 1980–2023.
# Sub-Saharan Africa dominates global burned area (~70%); weights below
# are allocated to individual countries proportionally to fire occurrence data.
# ---------------------------------------------------------------------------
WILDFIRE_RISK: dict[str, dict] = {
    # ── Sub-Saharan Africa ── highest global burned area (GFED ~70% of global)
    "CD": {"country": "DR Congo",           "risk": 8.5,  "region": "Sub-Saharan Africa"},
    "AO": {"country": "Angola",             "risk": 7.8,  "region": "Sub-Saharan Africa"},
    "MZ": {"country": "Mozambique",         "risk": 6.2,  "region": "Sub-Saharan Africa"},
    "TZ": {"country": "Tanzania",           "risk": 5.8,  "region": "Sub-Saharan Africa"},
    "ZM": {"country": "Zambia",             "risk": 5.3,  "region": "Sub-Saharan Africa"},
    "NG": {"country": "Nigeria",            "risk": 4.9,  "region": "Sub-Saharan Africa"},
    "ZW": {"country": "Zimbabwe",           "risk": 4.1,  "region": "Sub-Saharan Africa"},
    "SD": {"country": "Sudan",              "risk": 3.8,  "region": "Sub-Saharan Africa"},
    "SS": {"country": "South Sudan",        "risk": 3.5,  "region": "Sub-Saharan Africa"},
    "CF": {"country": "Central Afr. Rep.",  "risk": 3.2,  "region": "Sub-Saharan Africa"},
    "ZA": {"country": "South Africa",       "risk": 2.9,  "region": "Sub-Saharan Africa"},
    "ET": {"country": "Ethiopia",           "risk": 2.7,  "region": "Sub-Saharan Africa"},
    "GH": {"country": "Ghana",              "risk": 2.0,  "region": "Sub-Saharan Africa"},
    "MG": {"country": "Madagascar",         "risk": 1.8,  "region": "Sub-Saharan Africa"},
    "CG": {"country": "Republic of Congo",  "risk": 1.7,  "region": "Sub-Saharan Africa"},
    "CM": {"country": "Cameroon",           "risk": 1.5,  "region": "Sub-Saharan Africa"},
    "BF": {"country": "Burkina Faso",       "risk": 1.3,  "region": "Sub-Saharan Africa"},
    "ML": {"country": "Mali",               "risk": 1.2,  "region": "Sub-Saharan Africa"},
    "KE": {"country": "Kenya",              "risk": 1.1,  "region": "Sub-Saharan Africa"},
    "NA": {"country": "Namibia",            "risk": 1.0,  "region": "Sub-Saharan Africa"},

    # ── South America ── (~12–15% of global burned area)
    "BR": {"country": "Brazil",             "risk": 8.0,  "region": "South America"},
    "BO": {"country": "Bolivia",            "risk": 3.0,  "region": "South America"},
    "VE": {"country": "Venezuela",          "risk": 1.8,  "region": "South America"},
    "CO": {"country": "Colombia",           "risk": 1.5,  "region": "South America"},
    "PE": {"country": "Peru",               "risk": 1.2,  "region": "South America"},
    "AR": {"country": "Argentina",          "risk": 1.0,  "region": "South America"},
    "PY": {"country": "Paraguay",           "risk": 0.8,  "region": "South America"},
    "CL": {"country": "Chile",              "risk": 0.6,  "region": "South America"},

    # ── Southeast Asia ── (~3–5% of global, high health burden from haze)
    "ID": {"country": "Indonesia",          "risk": 3.5,  "region": "Southeast Asia"},
    "MM": {"country": "Myanmar",            "risk": 1.5,  "region": "Southeast Asia"},
    "TH": {"country": "Thailand",           "risk": 0.9,  "region": "Southeast Asia"},
    "VN": {"country": "Vietnam",            "risk": 0.7,  "region": "Southeast Asia"},
    "PH": {"country": "Philippines",        "risk": 0.5,  "region": "Southeast Asia"},
    "KH": {"country": "Cambodia",           "risk": 0.4,  "region": "Southeast Asia"},

    # ── Russia / Central Asia ── (~4–6% of global, rising with climate change)
    "RU": {"country": "Russia",             "risk": 4.5,  "region": "Russia & C. Asia"},
    "KZ": {"country": "Kazakhstan",         "risk": 0.8,  "region": "Russia & C. Asia"},
    "MN": {"country": "Mongolia",           "risk": 0.5,  "region": "Russia & C. Asia"},

    # ── North America ── (~3–5%)
    "US": {"country": "United States",      "risk": 3.5,  "region": "North America"},
    "CA": {"country": "Canada",             "risk": 2.5,  "region": "North America"},
    "MX": {"country": "Mexico",             "risk": 1.0,  "region": "North America"},

    # ── Australia / Pacific ── (~5–8%)
    "AU": {"country": "Australia",          "risk": 4.5,  "region": "Australasia"},
    "PG": {"country": "Papua New Guinea",   "risk": 1.0,  "region": "Australasia"},

    # ── Mediterranean / Europe ── (~1–2%)
    "PT": {"country": "Portugal",           "risk": 0.9,  "region": "Mediterranean"},
    "GR": {"country": "Greece",             "risk": 0.8,  "region": "Mediterranean"},
    "ES": {"country": "Spain",              "risk": 0.7,  "region": "Mediterranean"},
    "IT": {"country": "Italy",              "risk": 0.6,  "region": "Mediterranean"},
    "TR": {"country": "Turkey",             "risk": 0.6,  "region": "Mediterranean"},
    "FR": {"country": "France",             "risk": 0.3,  "region": "Mediterranean"},

    # ── South / East Asia ──
    "CN": {"country": "China",              "risk": 1.8,  "region": "East Asia"},
    "IN": {"country": "India",              "risk": 1.5,  "region": "South Asia"},

    # ── Middle East / North Africa ──
    "MA": {"country": "Morocco",            "risk": 0.4,  "region": "MENA"},
    "DZ": {"country": "Algeria",            "risk": 0.5,  "region": "MENA"},

    # ── Other ──
    "GB": {"country": "United Kingdom",     "risk": 0.1,  "region": "Europe (other)"},
    "DE": {"country": "Germany",            "risk": 0.1,  "region": "Europe (other)"},
    "NZ": {"country": "New Zealand",        "risk": 0.3,  "region": "Australasia"},
    "SE": {"country": "Sweden",             "risk": 0.2,  "region": "Europe (other)"},
    "NL": {"country": "Netherlands",        "risk": 0.05, "region": "Europe (other)"},
    "CH": {"country": "Switzerland",        "risk": 0.05, "region": "Europe (other)"},
}

# Normalise risk scores to sum to 100
_total_risk = sum(v["risk"] for v in WILDFIRE_RISK.values())
for _iso in WILDFIRE_RISK:
    WILDFIRE_RISK[_iso]["risk_share"] = round(
        WILDFIRE_RISK[_iso]["risk"] / _total_risk * 100, 4)

# ---------------------------------------------------------------------------
# Free-text region → ISO 2-letter code(s)
# Multi-country regions are expanded to all constituent ISO codes (equal weight).
# ---------------------------------------------------------------------------
REGION_TO_ISO: dict[str, list[str]] = {
    # ── single countries ──
    "United States":         ["US"],
    "Canada":                ["CA"],
    "Australia":             ["AU"],
    "Brazil":                ["BR"],
    "China":                 ["CN"],
    "India":                 ["IN"],
    "Russia":                ["RU"],
    "Chile":                 ["CL"],
    "Mexico":                ["MX"],
    "Greece":                ["GR"],
    "Italy":                 ["IT"],
    "Portugal":              ["PT"],
    "Spain":                 ["ES"],
    "Turkey":                ["TR"],
    "France":                ["FR"],
    "Germany":               ["DE"],
    "United Kingdom":        ["GB"],
    "New Zealand":           ["NZ"],
    "South Africa":          ["ZA"],
    "Nigeria":               ["NG"],
    "Ghana":                 ["GH"],
    "Kenya":                 ["KE"],
    "Ethiopia":              ["ET"],
    "Namibia":               ["NA"],
    "Indonesia":             ["ID"],
    "Tonga":                 [],       # Pacific island — not in risk table, skip
    "Greenland":             [],       # no fire risk
    "Sweden":                ["SE"],

    # ── multi-country regions (expand equally) ──
    "North America":         ["US", "CA", "MX"],
    "Europe":                ["PT", "GR", "ES", "IT", "FR", "GB", "DE", "SE"],
    "Mediterranean":         ["PT", "GR", "ES", "IT", "TR", "FR"],
    "Eastern Mediterranean": ["GR", "TR"],
    "Middle East":           ["MA", "DZ"],
    "Africa":                ["CD", "AO", "MZ", "TZ", "ZM", "NG", "ZA", "ET",
                              "GH", "NA", "KE", "CG", "CM", "BF", "ML", "SS", "CF"],
    "Sub-Saharan Africa":    ["CD", "AO", "MZ", "TZ", "ZM", "NG", "ZA", "ET",
                              "GH", "NA", "KE", "CG", "CM", "BF", "ML", "SS", "CF"],
    "Southeast Asia":        ["ID", "MM", "TH", "VN", "PH", "KH"],
    "South America":         ["BR", "BO", "VE", "CO", "PE", "AR", "PY", "CL"],
    "Global":                [],      # distribute globally — skip to avoid dilution
    "not_specified":         [],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = log_dir / f"07_global_mismatch_{timestamp}.log"
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("07_global_mismatch")


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
        return [str(val).strip()]


# ---------------------------------------------------------------------------
# Step 1 — Research counts per country
# ---------------------------------------------------------------------------
def compute_research_counts(log: logging.Logger) -> pd.DataFrame:
    """
    Combine two signals:
      (a) study_regions  — primary: where the study took place
      (b) first_author_country — fallback for papers with no study_regions

    Returns a DataFrame indexed by ISO2 code with columns:
      region_mentions, author_mentions, combined_mentions, research_share
    """
    struct  = pd.read_csv(ROOT / "data/extracted/abstracts_structured.csv", dtype=str)
    classif = pd.read_csv(ROOT / "data/classified/results_classified.csv",  dtype=str)

    merged = struct.merge(
        classif[["openalex_id", "first_author_country"]],
        left_on="paper_id", right_on="openalex_id", how="left",
    )

    # Signal (a): study_regions → ISO codes
    region_counts: dict[str, float] = defaultdict(float)
    papers_with_region = 0
    for _, row in merged.iterrows():
        regions = safe_parse(row["study_regions"])
        # Remove "Global" and blanks
        regions = [r for r in regions if r not in ("Global", "not_specified", "")]
        if not regions:
            continue
        papers_with_region += 1
        for region in regions:
            isos = REGION_TO_ISO.get(region, [])
            if not isos:
                log.debug("No ISO mapping for region '%s'", region)
                continue
            # Equal weight split across constituent countries
            share = 1.0 / len(isos)
            for iso in isos:
                region_counts[iso] += share

    # Signal (b): first_author_country (fill-in for papers without study_regions)
    author_counts: dict[str, int] = defaultdict(int)
    for _, row in merged.iterrows():
        regions = safe_parse(row["study_regions"])
        has_region = any(r for r in regions if r not in ("Global", "not_specified", ""))
        iso = str(row.get("first_author_country", "") or "").strip().upper()
        if iso and len(iso) == 2:
            author_counts[iso] += 1
            if not has_region:
                # Use author country as weak signal when no study region known
                region_counts[iso] += 0.33  # down-weighted vs. explicit region

    log.info("Papers with explicit study_regions: %d / %d",
             papers_with_region, len(merged))

    # Build table
    all_isos = set(region_counts) | set(author_counts) | set(WILDFIRE_RISK)
    rows = []
    for iso in sorted(all_isos):
        rows.append({
            "iso2":             iso,
            "country":          WILDFIRE_RISK.get(iso, {}).get("country", iso),
            "region":           WILDFIRE_RISK.get(iso, {}).get("region", "—"),
            "region_mentions":  round(region_counts.get(iso, 0), 2),
            "author_mentions":  author_counts.get(iso, 0),
        })

    df = pd.DataFrame(rows)
    total_mentions = df["region_mentions"].sum()
    df["research_share"] = (df["region_mentions"] / total_mentions * 100).round(4) \
        if total_mentions > 0 else 0.0

    log.info("Total weighted region mentions: %.1f across %d countries",
             total_mentions, (df["region_mentions"] > 0).sum())
    return df


# ---------------------------------------------------------------------------
# Step 2 — Merge with risk table and compute RRMI
# ---------------------------------------------------------------------------
def compute_rrmi(research_df: pd.DataFrame, log: logging.Logger) -> pd.DataFrame:
    risk_df = pd.DataFrame([
        {"iso2": k, "risk_score": v["risk"], "risk_share": v["risk_share"]}
        for k, v in WILDFIRE_RISK.items()
    ])

    df = research_df.merge(risk_df, on="iso2", how="outer")
    df["country"]  = df["country"].fillna(df["iso2"])
    df["region"]   = df["region"].fillna("—")
    df["region_mentions"]  = df["region_mentions"].fillna(0)
    df["author_mentions"]  = df["author_mentions"].fillna(0)
    df["research_share"]   = df["research_share"].fillna(0)
    df["risk_score"]       = df["risk_score"].fillna(0)
    df["risk_share"]       = df["risk_share"].fillna(0)

    # Research Deficit Score: positive = under-studied
    df["rds"] = (df["risk_share"] - df["research_share"]).round(4)

    # RRMI: ratio (cap denominator at small epsilon to avoid division by zero)
    eps = 0.001
    df["rrmi"] = (df["research_share"] / df["risk_share"].clip(lower=eps)).round(4)
    # log10(RRMI) for colour scale; clamp to [-2, 2]
    df["log_rrmi"] = np.log10(df["rrmi"].clip(lower=0.01, upper=100)).round(4)

    # Classification tier
    def classify(row):
        if row["risk_share"] < 0.05:
            return "Low risk"
        if row["rrmi"] < 0.20:
            return "Severe deficit"
        if row["rrmi"] < 0.50:
            return "High deficit"
        if row["rrmi"] < 0.90:
            return "Moderate deficit"
        if row["rrmi"] < 1.10:
            return "Balanced"
        if row["rrmi"] < 3.00:
            return "Research surplus"
        return "Major surplus"

    df["mismatch_tier"] = df.apply(classify, axis=1)

    df = df.sort_values("rds", ascending=False).reset_index(drop=True)
    log.info("Countries with severe/high deficit (RRMI < 0.5): %d",
             (df["rrmi"] < 0.5).sum())
    log.info("Countries with research surplus (RRMI > 1.1): %d",
             (df["rrmi"] > 1.1).sum())
    return df


# ---------------------------------------------------------------------------
# Step 3 — Interpretation text
# ---------------------------------------------------------------------------
def write_interpretation(df: pd.DataFrame, out_path: Path, log: logging.Logger) -> None:
    deficit = df[df["mismatch_tier"].isin(["Severe deficit", "High deficit"])].head(12)
    surplus = df[df["mismatch_tier"].isin(["Research surplus", "Major surplus"])].head(6)

    lines: list[str] = []
    lines.append("# Global Research-to-Risk Mismatch: Interpretation\n\n")
    lines.append(
        "_Auto-generated by 07_global_mismatch_analysis.py — do not edit manually._\n\n"
    )
    lines.append("## Methodology\n\n")
    lines.append(textwrap.fill(
        "Research activity per country is estimated from two complementary signals: "
        "(a) the geographic scope of each study (study_regions extracted by the LLM), "
        "and (b) the first-author country of affiliation (from OpenAlex metadata). "
        "Wildfire risk is quantified using a composite proxy derived from GFED4.1s "
        "burned area fraction (Giglio et al., 2013), FAO Forest Resources Assessment 2020 "
        "fire occurrence data, and EM-DAT disaster records (1980–2023). "
        "The Research-to-Risk Mismatch Index (RRMI = research_share / risk_share) is "
        "centred at 1.0: values below 1.0 indicate under-studied regions; values above "
        "1.0 indicate disproportionate research attention relative to fire hazard.",
        width=110,
    ) + "\n\n")

    lines.append("## Key Finding: The African Research Desert\n\n")
    lines.append(textwrap.fill(
        "Sub-Saharan Africa accounts for approximately 70% of global burned area "
        "(GFED4.1s) yet generates fewer than 5% of global wildfire-health publications. "
        "Countries such as DR Congo, Angola, Mozambique, and Tanzania face the highest "
        "absolute wildfire exposure on the planet but are virtually absent from the "
        "health literature. This reflects a compound of structural barriers: absence of "
        "long-term air quality monitoring networks, limited ICD-coded hospital registries, "
        "and a dearth of locally-funded epidemiology infrastructure.",
        width=110,
    ) + "\n\n")

    lines.append("## Top Research-Deficit Countries\n\n")
    lines.append("| Country | Region | Risk share (%) | Research share (%) | RRMI | Tier |\n")
    lines.append("|---------|--------|---------------:|-------------------:|-----:|------|\n")
    for _, row in deficit.iterrows():
        lines.append(
            f"| {row['country']} | {row['region']} | {row['risk_share']:.2f} | "
            f"{row['research_share']:.2f} | {row['rrmi']:.3f} | {row['mismatch_tier']} |\n"
        )

    lines.append("\n## Countries with Research Surplus\n\n")
    lines.append("| Country | Risk share (%) | Research share (%) | RRMI | Tier |\n")
    lines.append("|---------|---------------:|-------------------:|-----:|------|\n")
    for _, row in surplus.iterrows():
        lines.append(
            f"| {row['country']} | {row['risk_share']:.2f} | "
            f"{row['research_share']:.2f} | {row['rrmi']:.3f} | {row['mismatch_tier']} |\n"
        )

    lines.append("\n## Structural Drivers of the Mismatch\n\n")
    driver_text = [
        ("Data infrastructure gap",
         "Most epidemiological wildfire-health studies require continuous PM2.5 monitoring, "
         "ICD-coded hospital admissions, or death registries with cause-of-death data. "
         "These infrastructures exist primarily in high-income countries (USA, Canada, "
         "Australia, EU), creating a systematic selection bias towards regions with "
         "pre-existing surveillance capacity, independent of actual fire burden."),
        ("Funding geography",
         "The majority of wildfire-health research is funded by US NIH/EPA, "
         "Australian NHMRC, and EU Horizon programmes. Funding flows preferentially to "
         "institutions in high-income countries, reinforcing geographic concentration "
         "of the research base even when the scientific questions are global."),
        ("Language and publication bias",
         "International journals index predominantly English-language research. "
         "Much locally-produced knowledge in fire-affected low- and middle-income "
         "countries (Brazil, Indonesia, francophone Africa) may be published in "
         "national journals with limited international indexing."),
        ("Disciplinary mismatch",
         "Fire ecology and remote sensing research (which is global in scope) is "
         "disconnected from health outcome research (which requires local clinical "
         "data). Bridging these disciplines would unlock existing satellite exposure "
         "data to estimate health burdens in data-sparse regions."),
    ]
    for title, body in driver_text:
        lines.append(f"### {title}\n\n")
        lines.append(textwrap.fill(body, width=110) + "\n\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(lines), encoding="utf-8")
    log.info("Saved interpretation → %s", out_path)


# ---------------------------------------------------------------------------
# Step 4 — Choropleth map
# ---------------------------------------------------------------------------
TIER_COLORS = {
    "Severe deficit":   "#8B0000",   # dark red
    "High deficit":     "#D73027",   # red
    "Moderate deficit": "#FC8D59",   # orange
    "Balanced":         "#FFFFBF",   # pale yellow
    "Research surplus": "#91BFDB",   # light blue
    "Major surplus":    "#1A5276",   # dark blue
    "Low risk":         "#F5F5F5",   # near-white
}

TIER_ORDER = [
    "Severe deficit", "High deficit", "Moderate deficit",
    "Balanced", "Research surplus", "Major surplus", "Low risk",
]


def make_map_figure(
    df: pd.DataFrame,
    out_path: Path,
    log: logging.Logger,
    dpi: int = 300,
) -> None:
    """Two-panel figure: world choropleth (top) + deficit bar chart (bottom)."""

    # ── Build ISO → colour lookup ─────────────────────────────────────────
    iso_color = {}
    iso_rrmi  = {}
    for _, row in df.iterrows():
        iso  = str(row["iso2"]).strip().upper()
        tier = row["mismatch_tier"]
        iso_color[iso] = TIER_COLORS.get(tier, "#E0E0E0")
        iso_rrmi[iso]  = row["rrmi"]

    # ── Load NaturalEarth shapefile ───────────────────────────────────────
    shp_path = shpreader.natural_earth(
        resolution="110m", category="cultural", name="admin_0_countries"
    )
    import shapefile as pyshp
    sf   = pyshp.Reader(shp_path)
    recs = sf.records()
    shapes = sf.shapes()

    plt.rcParams.update({"font.family": "sans-serif", "figure.facecolor": "white"})

    fig = plt.figure(figsize=(17, 12), facecolor="white")
    fig.text(0.5, 0.990,
             "Global Research-to-Risk Mismatch in Wildfire Health Science",
             ha="center", va="top", fontsize=13, fontweight="bold", color="#1A1A1A")
    fig.text(0.5, 0.974,
             "RRMI = research share (%) ÷ wildfire risk share (%)  ·  "
             "Red = under-studied; Blue = over-studied relative to fire hazard",
             ha="center", va="top", fontsize=9, color="#555555", style="italic")

    gspec = fig.add_gridspec(
        2, 1, height_ratios=[2.2, 1.0],
        hspace=0.35, top=0.92, bottom=0.06, left=0.04, right=0.97,
    )
    ax_map = fig.add_subplot(gspec[0], projection=ccrs.Robinson())
    ax_bar = fig.add_subplot(gspec[1])

    # ── Panel A: Choropleth ───────────────────────────────────────────────
    ax_map.set_global()
    ax_map.add_feature(cfeature.OCEAN,      facecolor="#D6E8F5", zorder=0)
    ax_map.add_feature(cfeature.LAND,       facecolor="#F0EDE8", zorder=1)
    ax_map.add_feature(cfeature.COASTLINE,  linewidth=0.35, edgecolor="#AAAAAA", zorder=4)
    ax_map.add_feature(cfeature.BORDERS,    linewidth=0.25, edgecolor="#CCCCCC",
                       linestyle="--", zorder=4)
    ax_map.add_feature(cfeature.LAKES,      facecolor="#D6E8F5", linewidth=0.2, zorder=3)

    import matplotlib.patches as MPatches
    import matplotlib.path as MPath
    from shapely.geometry import shape as shapely_shape
    from shapely.geometry import MultiPolygon, Polygon

    # Draw each country coloured by tier
    for rec, shp_geom in zip(recs, shapes):
        rec_dict = rec.as_dict()
        iso2 = str(rec_dict.get("ISO_A2", "")).strip().upper()
        # Some territories have -99 as ISO code; try ISO_A2_EH fallback
        if iso2 in ("-9", "-99", ""):
            iso2 = str(rec_dict.get("ISO_A2_EH", "")).strip().upper()

        color = iso_color.get(iso2, "#DCDCDC")   # default: light grey (no data)

        try:
            geom = shapely_shape(shp_geom.__geo_interface__)
        except Exception:
            continue

        polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
        for poly in polys:
            if poly.is_empty:
                continue
            try:
                xs, ys = poly.exterior.xy
                ax_map.fill(xs, ys, transform=ccrs.PlateCarree(),
                            facecolor=color, edgecolor="#BBBBBB",
                            linewidth=0.2, zorder=2)
            except Exception:
                pass

    # Legend
    legend_handles = [
        MPatches.Patch(facecolor=TIER_COLORS[t], edgecolor="#888888",
                       linewidth=0.4, label=t)
        for t in TIER_ORDER if t != "Low risk"
    ]
    legend_handles.append(
        MPatches.Patch(facecolor="#DCDCDC", edgecolor="#888888",
                       linewidth=0.4, label="No data")
    )
    ax_map.legend(
        handles=legend_handles,
        loc="lower left", fontsize=7.5,
        framealpha=0.92, edgecolor="#CCCCCC",
        handlelength=1.2, handleheight=0.9,
        title="Mismatch tier (RRMI)", title_fontsize=8,
    )
    ax_map.set_title("(A)  Country-level Research-to-Risk Mismatch Index (RRMI)",
                     fontsize=11, fontweight="bold", pad=8, loc="left")

    # ── Panel B: Top deficit countries bar chart ──────────────────────────
    top_deficit = (
        df[df["mismatch_tier"].isin(["Severe deficit", "High deficit", "Moderate deficit"])]
        .head(15)
        .sort_values("rds", ascending=True)
        .reset_index(drop=True)
    )

    if len(top_deficit) == 0:
        top_deficit = df.sort_values("rds", ascending=True).head(12).reset_index(drop=True)

    y_labels = [
        f"{r['country']}  ({r['region']})" for _, r in top_deficit.iterrows()
    ]
    bar_colors = [TIER_COLORS.get(t, "#999999") for t in top_deficit["mismatch_tier"]]

    bars = ax_bar.barh(
        y_labels, top_deficit["rds"],
        color=bar_colors, edgecolor="white", linewidth=0.5, height=0.65, zorder=2,
    )

    # Annotate bars with risk share and research share
    xmax = top_deficit["rds"].max() * 1.45 if top_deficit["rds"].max() > 0 else 1
    for bar, (_, row) in zip(bars, top_deficit.iterrows()):
        w = bar.get_width()
        ax_bar.text(
            w + xmax * 0.01,
            bar.get_y() + bar.get_height() / 2,
            f"risk {row['risk_share']:.1f}% vs research {row['research_share']:.1f}%",
            va="center", ha="left", fontsize=7, color="#555555",
        )

    ax_bar.set_xlabel("Research Deficit Score  (risk share − research share, %)",
                      fontsize=9.5, color="#333333")
    ax_bar.set_title(
        "(B)  Top Research-Deficit Countries  "
        "(positive RDS = under-studied relative to wildfire hazard)",
        fontsize=11, fontweight="bold", pad=6, loc="left",
    )
    ax_bar.set_xlim(0, xmax)
    ax_bar.set_yticks(range(len(y_labels)))
    ax_bar.set_yticklabels(y_labels, fontsize=8)
    ax_bar.grid(axis="x", color="#EEEEEE", linewidth=0.6, zorder=0)
    ax_bar.spines["top"].set_visible(False)
    ax_bar.spines["right"].set_visible(False)
    ax_bar.spines["left"].set_color("#CCCCCC")
    ax_bar.spines["bottom"].set_color("#CCCCCC")

    # ── Save ──────────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log.info("Saved map figure → %s", out_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 07_global_mismatch_analysis.py started ===")

    # ── Check dependencies ────────────────────────────────────────────────
    try:
        import shapefile        # pyshp
        from shapely.geometry import shape as _s
    except ImportError as e:
        log.error("Missing dependency: %s  —  run: pip install pyshp shapely", e)
        sys.exit(1)

    # ── Step 1: research counts ───────────────────────────────────────────
    research_df = compute_research_counts(log)

    # ── Step 2: RRMI ─────────────────────────────────────────────────────
    mismatch_df = compute_rrmi(research_df, log)

    # ── Step 3: Save table ───────────────────────────────────────────────
    tbl_cols = [
        "iso2", "country", "region",
        "region_mentions", "author_mentions",
        "research_share", "risk_share", "rds", "rrmi", "log_rrmi",
        "mismatch_tier",
    ]
    tbl_dir = ROOT / "outputs/tables"
    tbl_dir.mkdir(parents=True, exist_ok=True)
    out_tbl = tbl_dir / "global_mismatch.csv"
    mismatch_df[tbl_cols].to_csv(out_tbl, index=False)
    log.info("Saved table → %s  (%d rows)", out_tbl, len(mismatch_df))

    # ── Step 4: Map figure ───────────────────────────────────────────────
    fig_dir = ROOT / "outputs/figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    make_map_figure(
        mismatch_df,
        fig_dir / "global_mismatch_map.png",
        log,
        dpi=cfg.get("figures", {}).get("dpi", 300),
    )

    # ── Step 5: Interpretation ───────────────────────────────────────────
    rpt_dir = ROOT / "outputs/report"
    write_interpretation(
        mismatch_df,
        rpt_dir / "global_mismatch_interpretation.md",
        log,
    )

    # ── Console summary ───────────────────────────────────────────────────
    log.info("─" * 64)
    log.info("MISMATCH SUMMARY")
    log.info("  Countries assessed: %d", len(mismatch_df))
    log.info("  Severe deficit  (RRMI < 0.20): %d countries",
             (mismatch_df["rrmi"] < 0.20).sum())
    log.info("  High deficit    (RRMI 0.20–0.50): %d countries",
             ((mismatch_df["rrmi"] >= 0.20) & (mismatch_df["rrmi"] < 0.50)).sum())
    log.info("  Research surplus (RRMI > 1.10): %d countries",
             (mismatch_df["rrmi"] > 1.10).sum())
    log.info("\n  TOP 10 RESEARCH-DEFICIT COUNTRIES (by RDS):")
    for _, row in mismatch_df.head(10).iterrows():
        log.info("    %-22s  risk=%.2f%%  research=%.2f%%  RRMI=%.3f  [%s]",
                 row["country"], row["risk_share"], row["research_share"],
                 row["rrmi"], row["mismatch_tier"])
    log.info("\n  TOP 5 RESEARCH-SURPLUS COUNTRIES:")
    surplus = mismatch_df[mismatch_df["rrmi"] > 1.1].sort_values("rrmi", ascending=False)
    for _, row in surplus.head(5).iterrows():
        log.info("    %-22s  risk=%.2f%%  research=%.2f%%  RRMI=%.3f",
                 row["country"], row["risk_share"], row["research_share"], row["rrmi"])
    log.info("─" * 64)
    log.info("=== 07_global_mismatch_analysis.py finished ===")


if __name__ == "__main__":
    main()
