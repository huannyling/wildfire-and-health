"""
02_clean.py — Clean and deduplicate raw literature metadata.

Input:  data/raw/results_raw.csv          (from 01_collect.py)
Output: data/processed/results_clean.csv
        outputs/tables/cleaning_summary.csv

Cleaning steps (in order)
--------------------------
1. Load raw CSV
2. Drop records with missing title
3. Normalise key fields (strip whitespace, standardise DOI format)
4. Deduplicate by DOI (where available)
5. Deduplicate by OpenAlex ID (catches records without DOI)
6. Filter by publication year range (from config.yaml)
7. Flag records with missing abstract (kept, but flagged)
8. Save cleaned CSV and a summary of rows removed at each step
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"02_clean_{timestamp}.log"

    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("02_clean")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------
def normalise_doi(doi: str) -> str:
    """
    Standardise DOI to lowercase bare form (no URL prefix).
    e.g. 'https://doi.org/10.1000/xyz' → '10.1000/xyz'
    """
    if not isinstance(doi, str):
        return ""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi


def normalise_openalex_id(oa_id: str) -> str:
    """Strip URL wrapper from OpenAlex ID, e.g. 'https://openalex.org/W123' → 'W123'."""
    if not isinstance(oa_id, str):
        return ""
    oa_id = oa_id.strip()
    if "/" in oa_id:
        oa_id = oa_id.rsplit("/", 1)[-1]
    return oa_id


# ---------------------------------------------------------------------------
# Step-by-step cleaning with audit trail
# ---------------------------------------------------------------------------
def run_cleaning(df: pd.DataFrame, cfg: dict, log: logging.Logger) -> tuple[pd.DataFrame, list[dict]]:
    """
    Apply cleaning steps in sequence.
    Returns the cleaned DataFrame and a list of audit dicts (one per step).
    """
    clean_cfg = cfg["clean"]
    audit: list[dict] = []

    def record(step: str, before: int, after: int, note: str = "") -> None:
        removed = before - after
        log.info("%-40s  before=%d  removed=%d  after=%d  %s",
                 step, before, removed, after, f"({note})" if note else "")
        audit.append({
            "step": step,
            "rows_before": before,
            "rows_removed": removed,
            "rows_after": after,
            "note": note,
        })

    # ── Step 1: Normalise DOI and OpenAlex ID ─────────────────────────────
    df["doi"] = df["doi"].apply(normalise_doi)
    df["openalex_id"] = df["openalex_id"].apply(normalise_openalex_id)

    # ── Step 2: Drop missing title ─────────────────────────────────────────
    before = len(df)
    df = df[df["title"].notna() & (df["title"].str.strip() != "")]
    record("Drop missing title", before, len(df))

    # ── Step 3: Normalise title (strip whitespace, collapse internal spaces) ─
    df["title"] = df["title"].str.strip().str.replace(r"\s+", " ", regex=True)

    # ── Step 4: Deduplicate by DOI ────────────────────────────────────────
    before = len(df)
    has_doi = df["doi"] != ""
    # Keep first occurrence; records without DOI are untouched by this step.
    df_with_doi = df[has_doi].drop_duplicates(subset="doi", keep="first")
    df_no_doi   = df[~has_doi]
    df = pd.concat([df_with_doi, df_no_doi], ignore_index=True)
    record("Deduplicate by DOI", before, len(df),
           note=f"{has_doi.sum()} had DOI, {(~has_doi).sum()} did not")

    # ── Step 5: Deduplicate by OpenAlex ID ───────────────────────────────
    before = len(df)
    has_oa = df["openalex_id"] != ""
    df_with_oa = df[has_oa].drop_duplicates(subset="openalex_id", keep="first")
    df_no_oa   = df[~has_oa]
    df = pd.concat([df_with_oa, df_no_oa], ignore_index=True)
    record("Deduplicate by OpenAlex ID", before, len(df))

    # ── Step 6: Filter by publication year ───────────────────────────────
    before = len(df)
    year_min = clean_cfg.get("year_min", 2000)
    year_max = clean_cfg.get("year_max", 2024)
    df["publication_year"] = pd.to_numeric(df["publication_year"], errors="coerce")
    df = df[df["publication_year"].between(year_min, year_max, inclusive="both")]
    record("Filter by year range", before, len(df),
           note=f"{year_min}–{year_max}")

    # ── Step 7: Flag missing abstract (keep rows, add flag column) ────────
    df["has_abstract"] = df["abstract"].notna() & (df["abstract"].str.strip() != "")
    missing_abstract = (~df["has_abstract"]).sum()
    log.info("%-40s  %d records have no abstract (kept, flagged)",
             "Flag missing abstract", missing_abstract)
    audit.append({
        "step": "Flag missing abstract",
        "rows_before": len(df),
        "rows_removed": 0,
        "rows_after": len(df),
        "note": f"{missing_abstract} flagged (not removed)",
    })

    # ── Step 8: Reset index cleanly ───────────────────────────────────────
    df = df.reset_index(drop=True)

    return df, audit


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 02_clean.py started ===")

    # --- Load raw data ---
    input_path = ROOT / cfg["collect"]["output_path"]
    if not input_path.exists():
        log.error("Input file not found: %s", input_path)
        log.error("Run 01_collect.py first.")
        sys.exit(1)

    log.info("Loading raw data from %s", input_path)
    df_raw = pd.read_csv(input_path, dtype=str)  # dtype=str to avoid silent coercions
    log.info("Raw records loaded: %d", len(df_raw))

    # --- Clean ---
    df_clean, audit = run_cleaning(df_raw, cfg, log)
    log.info("Records after cleaning: %d", len(df_clean))

    # --- Save cleaned CSV ---
    output_path = ROOT / cfg["clean"]["output_path"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df_clean.to_csv(output_path, index=False, encoding="utf-8")
    log.info("Saved cleaned data → %s", output_path)

    # --- Save cleaning summary ---
    summary_dir = ROOT / cfg["tables"]["output_dir"]
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / "cleaning_summary.csv"

    summary_df = pd.DataFrame(audit)
    # Add a total row
    total_removed = df_raw.shape[0] - df_clean.shape[0]
    total_row = pd.DataFrame([{
        "step": "TOTAL",
        "rows_before": df_raw.shape[0],
        "rows_removed": total_removed,
        "rows_after": df_clean.shape[0],
        "note": f"{total_removed / df_raw.shape[0] * 100:.1f}% removed overall",
    }])
    summary_df = pd.concat([summary_df, total_row], ignore_index=True)
    summary_df.to_csv(summary_path, index=False, encoding="utf-8")
    log.info("Saved cleaning summary → %s", summary_path)

    # --- Print summary table to console ---
    log.info("\n%s", summary_df.to_string(index=False))
    log.info("=== 02_clean.py finished ===")


if __name__ == "__main__":
    main()
