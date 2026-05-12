"""
05_tables.py — Build summary tables for the literature review report.

Input:  data/classified/results_classified.csv   (from 03_classify.py)
Output: outputs/tables/
          theme_counts.csv
          year_by_theme.csv
          top_journals.csv
          top_authors.csv
          country_counts.csv

Each CSV is self-contained and referenced by 06_report.py.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"05_tables_{timestamp}.log"
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("05_tables")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


THEME_LABELS = {
    "wildfire_exposure":      "Wildfire Exposure",
    "air_quality_pm25":       "Air Quality / PM2.5",
    "respiratory_health":     "Respiratory Health",
    "cardiovascular_health":  "Cardiovascular Health",
    "mental_health":          "Mental Health",
    "mortality":              "Mortality",
    "vulnerable_populations": "Vulnerable Populations",
    "burn_severity_ecology":  "Burn Severity / Ecology",
    "other":                  "Other / Unclassified",
}

COUNTRY_NAMES = {
    "US": "United States", "GB": "United Kingdom", "AU": "Australia",
    "CA": "Canada",        "CN": "China",          "DE": "Germany",
    "FR": "France",        "ES": "Spain",          "IT": "Italy",
    "BR": "Brazil",        "IN": "India",          "NL": "Netherlands",
    "SE": "Sweden",        "NZ": "New Zealand",    "PT": "Portugal",
    "GR": "Greece",        "ZA": "South Africa",   "MX": "Mexico",
    "JP": "Japan",         "KR": "South Korea",
}


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------

def build_theme_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Paper count and percentage per primary topic."""
    counts = df["primary_topic"].value_counts().reset_index()
    counts.columns = ["primary_topic", "count"]
    counts["label"] = counts["primary_topic"].map(lambda x: THEME_LABELS.get(x, x))
    counts["percentage"] = (counts["count"] / counts["count"].sum() * 100).round(1)
    return counts[["label", "count", "percentage"]].rename(
        columns={"label": "Theme", "count": "Papers (n)", "percentage": "Share (%)"}
    )


def build_year_by_theme(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-tabulation: publication year × primary topic."""
    df2 = df.copy()
    df2["publication_year"] = pd.to_numeric(df2["publication_year"], errors="coerce")
    df2["theme_label"] = df2["primary_topic"].map(lambda x: THEME_LABELS.get(x, x))
    ct = (
        df2.groupby(["publication_year", "theme_label"])
        .size()
        .unstack(fill_value=0)
        .astype(int)
    )
    ct.index = ct.index.astype(int)
    ct.index.name = "Year"
    ct["Total"] = ct.sum(axis=1)
    return ct.reset_index()


def build_top_journals(df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Top journals by paper count, with open-access rate."""
    df2 = df.copy()
    df2["is_open_access"] = df2["is_open_access"].map(
        lambda v: str(v).strip().lower() in ("true", "1", "yes")
    )
    grp = df2[df2["journal"].notna() & (df2["journal"] != "")].groupby("journal")
    result = grp.agg(
        Papers=("journal", "count"),
        OA_count=("is_open_access", "sum"),
    ).reset_index()
    result["OA Rate (%)"] = (result["OA_count"] / result["Papers"] * 100).round(1)
    result = result.nlargest(top_n, "Papers").drop(columns="OA_count")
    result.columns = ["Journal", "Papers (n)", "OA Rate (%)"]
    return result.reset_index(drop=True)


def build_top_authors(df: pd.DataFrame, top_n: int = 15) -> pd.DataFrame:
    """
    Top authors by paper count (first author only — derived from the
    authors field which stores all authors as a semicolon-separated string).
    """
    first_authors = (
        df["authors"]
        .dropna()
        .str.split(";")
        .str[0]
        .str.strip()
        .replace("", pd.NA)
        .dropna()
    )
    counts = first_authors.value_counts().head(top_n).reset_index()
    counts.columns = ["First Author", "Papers as First Author (n)"]
    return counts


def build_country_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Paper count per first-author country with full country names."""
    counts = (
        df["first_author_country"]
        .replace("", pd.NA)
        .dropna()
        .map(lambda c: COUNTRY_NAMES.get(str(c).strip().upper(), str(c).strip()))
        .value_counts()
        .reset_index()
    )
    counts.columns = ["Country", "Papers (n)"]
    counts["Share (%)"] = (counts["Papers (n)"] / counts["Papers (n)"].sum() * 100).round(1)
    return counts


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
TABLE_BUILDERS = {
    "theme_counts":  build_theme_counts,
    "year_by_theme": build_year_by_theme,
    "top_journals":  build_top_journals,
    "top_authors":   build_top_authors,
    "country_counts": build_country_counts,
}


def main() -> None:
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 05_tables.py started ===")

    input_path = ROOT / cfg["classify"]["output_path"]
    if not input_path.exists():
        log.error("Input file not found: %s — run 03_classify.py first.", input_path)
        sys.exit(1)

    df = pd.read_csv(input_path, dtype=str)
    log.info("Loaded %d records from %s", len(df), input_path)

    out_dir = ROOT / cfg["tables"]["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    top_n = cfg["figures"].get("top_n", 10)

    for name in cfg["tables"].get("summaries", list(TABLE_BUILDERS.keys())):
        if name not in TABLE_BUILDERS:
            log.warning("Unknown table '%s' in config — skipping.", name)
            continue
        try:
            fn = TABLE_BUILDERS[name]
            # Pass top_n only to functions that accept it
            import inspect
            sig = inspect.signature(fn)
            tbl = fn(df, top_n) if "top_n" in sig.parameters else fn(df)
            out_path = out_dir / f"{name}.csv"
            tbl.to_csv(out_path, index=False, encoding="utf-8")
            log.info("Saved %s → %s  (%d rows)", name, out_path, len(tbl))
        except Exception as exc:
            log.error("Failed to build table '%s': %s", name, exc, exc_info=True)

    log.info("=== 05_tables.py finished ===")


if __name__ == "__main__":
    main()
