"""
03_classify.py — Classify cleaned literature records into research themes.

Input:  data/processed/results_clean.csv   (from 02_clean.py)
Output: data/classified/results_classified.csv
        outputs/tables/classification_summary.csv

Classification fields added
----------------------------
  primary_topic             : dominant theme (string)
  has_health_component      : True/False
  health_outcomes           : semicolon-separated list of matched outcomes
  mentions_vulnerable_pop   : True/False
  vulnerable_pop_type       : semicolon-separated list of matched groups
  mentions_genomics         : True/False
  study_region              : semicolon-separated list of detected regions/countries
  method_type               : semicolon-separated list of detected study designs

Two-layer approach
------------------
Layer 1 (always runs):  keyword/rule-based matching on title + abstract.
Layer 2 (optional):     LLM-based classification via Anthropic API.
                        Enable with  classify.use_llm: true  in config.yaml.
                        When enabled, the LLM re-evaluates every record and
                        its output overwrites Layer 1 results for that record.
                        Requires ANTHROPIC_API_KEY in the environment.
"""

import json
import logging
import os
import re
import sys
import time
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
    log_file = log_dir / f"03_classify_{timestamp}.log"
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("03_classify")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Rule dictionaries
# ---------------------------------------------------------------------------
# These are defined here (not in config.yaml) because they are detailed and
# rarely need changing. The theme keywords in config.yaml drive primary_topic;
# the dicts below drive the remaining classification fields.

HEALTH_OUTCOME_RULES: dict[str, list[str]] = {
    "respiratory": [
        "respiratory", "asthma", "COPD", "lung function", "bronchitis",
        "pneumonia", "wheeze", "wheezi", "pulmonary", "spirometry",
        "forced expiratory", "FEV", "FVC",
    ],
    "cardiovascular": [
        "cardiovascular", "heart disease", "cardiac arrest", "myocardial",
        "stroke", "hypertension", "blood pressure", "arrhythmia", "atrial fibrillation",
    ],
    "mental_health": [
        "mental health", "anxiety", "depression", "PTSD", "post-traumatic stress",
        "psychological distress", "suicide", "well-being", "wellbeing",
        "stress disorder", "grief",
    ],
    "mortality": [
        "mortality", "premature death", "excess death", "years of life lost",
        "YLL", "death rate", "fatality", "all-cause mortality",
    ],
    "reproductive": [
        "preterm birth", "birth weight", "low birth weight", "gestational",
        "stillbirth", "infant mortality", "neonatal", "pregnancy",
    ],
    "neurological": [
        "neurological", "cognitive", "dementia", "Alzheimer", "Parkinson",
        "neurodevelopment", "brain",
    ],
    "cancer": [
        "cancer", "carcinogen", "tumor", "tumour", "malignancy", "leukemia",
        "lymphoma", "mesothelioma",
    ],
    "skin_eye": [
        "skin irritation", "dermatitis", "eye irritation", "conjunctivitis",
        "ocular", "burns", "burn injury",
    ],
    "heat_related": [
        "heat stroke", "heat exhaustion", "hyperthermia", "heat-related illness",
        "heat stress",
    ],
}

VULNERABLE_POP_RULES: dict[str, list[str]] = {
    "children": [
        "children", "child", "pediatric", "paediatric", "infant", "adolescent",
        "youth", "school-age", "newborn", "neonate",
    ],
    "elderly": [
        "elderly", "older adults", "older people", "aged", "aging population",
        "seniors", "geriatric",
    ],
    "indigenous": [
        "Indigenous", "First Nations", "Aboriginal", "Native American",
        "Tribal", "Metis", "Inuit", "Alaska Native", "Maori",
    ],
    "low_income": [
        "low-income", "low income", "poverty", "socioeconomic", "disadvantaged",
        "marginalized", "marginalised", "deprivation", "food insecurity",
    ],
    "rural": [
        "rural", "remote communit", "frontier", "non-urban", "rural-urban",
    ],
    "racial_ethnic_minorities": [
        "racial disparities", "ethnic minorities", "Black", "Hispanic", "Latino",
        "Asian American", "race", "ethnicity", "minority population",
    ],
    "pregnant_women": [
        "pregnant women", "maternal", "pregnancy", "prenatal", "perinatal",
    ],
    "outdoor_workers": [
        "outdoor worker", "farmworker", "agricultural worker", "firefighter",
        "occupational exposure",
    ],
    "homeless": [
        "homeless", "unhoused", "houseless", "shelter",
    ],
}

GENOMICS_RULES: list[str] = [
    "genomic", "genome", "genetic", "gene expression", "epigenetic",
    "DNA methylation", "SNP", "single nucleotide", "biomarker",
    "transcriptom", "proteom", "metabolom", "GWAS", "genome-wide",
    "oxidative stress marker", "inflammatory marker", "cytokine",
]

# Regions: each key is a label; values are substrings to match (case-insensitive).
# Matched against title + abstract + (if present) journal name.
REGION_RULES: dict[str, list[str]] = {
    "United States": [
        "United States", "USA", "U.S.A", "California", "Oregon", "Washington State",
        "Montana", "Colorado", "Arizona", "New Mexico", "Idaho", "Wyoming",
        "Nevada", "Utah", "Alaska",
    ],
    "Canada": [
        "Canada", "Canadian", "British Columbia", "Alberta", "Ontario", "Quebec",
        "Saskatchewan", "Manitoba", "Fort McMurray",
    ],
    "Australia": [
        "Australia", "Australian", "New South Wales", "Victoria", "Queensland",
        "Western Australia", "South Australia", "Black Summer",
    ],
    "Mediterranean_Europe": [
        "Portugal", "Spain", "Greece", "Italy", "France", "Mediterranean",
        "Southern Europe",
    ],
    "South_America": [
        "Brazil", "Amazon", "Chile", "Argentina", "Colombia", "Peru",
        "Bolivia", "South America",
    ],
    "Southeast_Asia": [
        "Indonesia", "Malaysia", "Borneo", "Singapore", "Thailand", "Vietnam",
        "Philippines", "Southeast Asia",
    ],
    "South_Asia": [
        "India", "Pakistan", "Bangladesh", "South Asia",
    ],
    "Africa": [
        "Africa", "Sub-Saharan", "Nigeria", "Kenya", "South Africa",
        "Ethiopia", "Mozambique",
    ],
    "Global_Multi_Country": [
        "global", "multi-country", "worldwide", "international", "cross-national",
        "low- and middle-income countries", "LMIC",
    ],
}

METHOD_RULES: dict[str, list[str]] = {
    "systematic_review_meta_analysis": [
        "systematic review", "meta-analysis", "meta analysis", "literature review",
        "scoping review", "narrative review",
    ],
    "cohort_study": [
        "cohort study", "prospective cohort", "retrospective cohort", "longitudinal study",
        "follow-up study",
    ],
    "case_control": [
        "case-control", "case control",
    ],
    "cross_sectional": [
        "cross-sectional", "cross sectional", "survey", "prevalence study",
    ],
    "randomised_controlled_trial": [
        "randomized controlled trial", "randomised controlled trial", "RCT",
        "clinical trial", "randomized trial",
    ],
    "time_series_ecological": [
        "time series", "time-series", "ecological study", "interrupted time series",
        "panel data",
    ],
    "satellite_remote_sensing": [
        "satellite", "remote sensing", "MODIS", "Landsat", "VIIRS", "NDVI",
        "burned area", "fire perimeter", "geospatial",
    ],
    "atmospheric_modelling": [
        "atmospheric model", "air quality model", "chemical transport model",
        "dispersion model", "CMAQ", "WRF", "fire weather",
    ],
    "epidemiological_modelling": [
        "epidemiological model", "health impact assessment", "exposure-response",
        "dose-response", "attributable fraction", "burden of disease",
    ],
    "qualitative": [
        "qualitative", "interview", "focus group", "ethnograph", "grounded theory",
        "thematic analysis",
    ],
    "mixed_methods": [
        "mixed method", "mixed-method",
    ],
}


# ---------------------------------------------------------------------------
# Core keyword matcher
# ---------------------------------------------------------------------------
def match_keywords(text: str, rules: dict[str, list[str]]) -> list[str]:
    """
    Return a list of keys from `rules` whose keywords appear in `text`.
    Matching is case-insensitive; whole-string containment (no word boundary needed).
    """
    text_lower = text.lower()
    matched = []
    for label, keywords in rules.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                matched.append(label)
                break  # one match per label is enough
    return matched


def count_theme_hits(text: str, themes: dict[str, list[str]]) -> dict[str, int]:
    """Count how many keywords from each theme appear in text."""
    text_lower = text.lower()
    counts: dict[str, int] = {}
    for theme, keywords in themes.items():
        counts[theme] = sum(1 for kw in keywords if kw.lower() in text_lower)
    return counts


# ---------------------------------------------------------------------------
# Rule-based classifier (Layer 1)
# ---------------------------------------------------------------------------
def classify_rule_based(row: pd.Series, themes: dict[str, list[str]]) -> dict:
    """
    Classify a single record using keyword rules.
    `row` must have at least 'title' and 'abstract' columns.
    Returns a dict of classification fields.
    """
    title    = str(row.get("title", "") or "")
    abstract = str(row.get("abstract", "") or "")
    text     = f"{title} {abstract}"

    # ── primary_topic ──────────────────────────────────────────────────────
    theme_hits = count_theme_hits(text, themes)
    if any(v > 0 for v in theme_hits.values()):
        primary_topic = max(theme_hits, key=lambda k: theme_hits[k])
    else:
        primary_topic = "other"

    # ── health outcomes ────────────────────────────────────────────────────
    health_outcomes = match_keywords(text, HEALTH_OUTCOME_RULES)
    has_health = len(health_outcomes) > 0

    # ── vulnerable populations ────────────────────────────────────────────
    vuln_types = match_keywords(text, VULNERABLE_POP_RULES)
    mentions_vuln = len(vuln_types) > 0

    # ── genomics ──────────────────────────────────────────────────────────
    text_lower = text.lower()
    mentions_genomics = any(kw.lower() in text_lower for kw in GENOMICS_RULES)

    # ── study region ──────────────────────────────────────────────────────
    # Also check journal name if available
    journal = str(row.get("journal", "") or "")
    region_text = f"{text} {journal}"
    regions = match_keywords(region_text, REGION_RULES)
    if not regions:
        regions = ["unspecified"]

    # ── method type ───────────────────────────────────────────────────────
    methods = match_keywords(text, METHOD_RULES)
    if not methods:
        methods = ["unspecified"]

    return {
        "primary_topic":           primary_topic,
        "has_health_component":    has_health,
        "health_outcomes":         "; ".join(health_outcomes) if health_outcomes else "",
        "mentions_vulnerable_pop": mentions_vuln,
        "vulnerable_pop_type":     "; ".join(vuln_types) if vuln_types else "",
        "mentions_genomics":       mentions_genomics,
        "study_region":            "; ".join(regions),
        "method_type":             "; ".join(methods),
        "classification_source":   "rule_based",
    }


# ---------------------------------------------------------------------------
# LLM-based classifier (Layer 2, optional)
# ---------------------------------------------------------------------------
LLM_SYSTEM_PROMPT = """You are a research librarian classifying academic papers about
wildfires, health, and vulnerable populations. For each paper, return ONLY a valid JSON
object with exactly these keys and value types:

{
  "primary_topic": string (one of: wildfire_exposure | air_quality_pm25 | respiratory_health |
                   cardiovascular_health | mental_health | mortality | vulnerable_populations |
                   burn_severity_ecology | other),
  "has_health_component": boolean,
  "health_outcomes": list of strings (from: respiratory | cardiovascular | mental_health |
                     mortality | reproductive | neurological | cancer | skin_eye | heat_related),
  "mentions_vulnerable_pop": boolean,
  "vulnerable_pop_type": list of strings (from: children | elderly | indigenous | low_income |
                         rural | racial_ethnic_minorities | pregnant_women | outdoor_workers | homeless),
  "mentions_genomics": boolean,
  "study_region": list of strings (use ISO country names or region labels such as
                  "United States", "Canada", "Australia", "Global_Multi_Country", etc.),
  "method_type": list of strings (from: systematic_review_meta_analysis | cohort_study |
                 case_control | cross_sectional | randomised_controlled_trial |
                 time_series_ecological | satellite_remote_sensing | atmospheric_modelling |
                 epidemiological_modelling | qualitative | mixed_methods | unspecified)
}

Respond with ONLY the JSON object, no markdown fences, no explanation."""


def classify_with_llm(
    records: list[dict],
    llm_cfg: dict,
    log: logging.Logger,
) -> list[dict]:
    """
    Send each record to the Anthropic API and return updated classification dicts.
    Falls back to the existing rule-based result on any API error.
    """
    try:
        import anthropic
    except ImportError:
        log.error("Package 'anthropic' is not installed. Run: pip install anthropic")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.error("ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    model  = llm_cfg.get("model", "claude-opus-4-6")
    max_tokens = llm_cfg.get("max_tokens", 512)
    input_fields = llm_cfg.get("input_fields", ["title", "abstract"])

    results = []
    for i, rec in enumerate(records):
        # Build user message
        parts = []
        for field in input_fields:
            val = rec.get(field, "") or ""
            if val:
                parts.append(f"{field.upper()}: {val}")
        user_msg = "\n".join(parts)

        if not user_msg.strip():
            log.warning("Record %d has no text; skipping LLM call.", i)
            results.append(rec)
            continue

        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=LLM_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = response.content[0].text.strip()
            parsed = json.loads(raw)

            # Flatten list fields to semicolon-separated strings
            def flatten(val):
                if isinstance(val, list):
                    return "; ".join(str(v) for v in val)
                return str(val)

            rec.update({
                "primary_topic":           parsed.get("primary_topic", rec["primary_topic"]),
                "has_health_component":    bool(parsed.get("has_health_component", rec["has_health_component"])),
                "health_outcomes":         flatten(parsed.get("health_outcomes", rec["health_outcomes"])),
                "mentions_vulnerable_pop": bool(parsed.get("mentions_vulnerable_pop", rec["mentions_vulnerable_pop"])),
                "vulnerable_pop_type":     flatten(parsed.get("vulnerable_pop_type", rec["vulnerable_pop_type"])),
                "mentions_genomics":       bool(parsed.get("mentions_genomics", rec["mentions_genomics"])),
                "study_region":            flatten(parsed.get("study_region", rec["study_region"])),
                "method_type":             flatten(parsed.get("method_type", rec["method_type"])),
                "classification_source":   "llm",
            })

        except (json.JSONDecodeError, KeyError, Exception) as exc:
            log.warning("LLM classification failed for record %d (%s); keeping rule-based result.", i, exc)
            rec["classification_source"] = "rule_based_fallback"

        results.append(rec)

        # Polite delay to avoid rate-limiting
        if i < len(records) - 1:
            time.sleep(0.3)

        if (i + 1) % 50 == 0:
            log.info("LLM classification progress: %d / %d", i + 1, len(records))

    return results


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------
def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a wide classification summary table:
    one row per field, with value counts as columns.
    """
    rows = []

    # primary_topic distribution
    for val, count in df["primary_topic"].value_counts().items():
        rows.append({"field": "primary_topic", "value": val, "count": count})

    # Boolean fields
    for col in ["has_health_component", "mentions_vulnerable_pop", "mentions_genomics"]:
        for val, count in df[col].value_counts().items():
            rows.append({"field": col, "value": str(val), "count": count})

    # Multi-label fields: explode on "; "
    for col in ["health_outcomes", "vulnerable_pop_type", "study_region", "method_type"]:
        exploded = (
            df[col]
            .dropna()
            .str.split("; ")
            .explode()
            .str.strip()
            .replace("", pd.NA)
            .dropna()
        )
        for val, count in exploded.value_counts().items():
            rows.append({"field": col, "value": val, "count": count})

    # Classification source
    for val, count in df["classification_source"].value_counts().items():
        rows.append({"field": "classification_source", "value": val, "count": count})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 03_classify.py started ===")

    # --- Load cleaned data ---
    input_path = ROOT / cfg["clean"]["output_path"]
    if not input_path.exists():
        log.error("Input file not found: %s", input_path)
        log.error("Run 02_clean.py first.")
        sys.exit(1)

    df = pd.read_csv(input_path, dtype=str)
    log.info("Loaded %d records from %s", len(df), input_path)

    themes = cfg["classify"]["themes"]

    # ── Layer 1: rule-based ───────────────────────────────────────────────
    log.info("Running rule-based classification…")
    rule_results = [classify_rule_based(row, themes) for _, row in df.iterrows()]
    rule_df = pd.DataFrame(rule_results)

    # Attach classification columns to main dataframe
    df = pd.concat([df.reset_index(drop=True), rule_df.reset_index(drop=True)], axis=1)
    log.info("Rule-based classification complete.")

    # ── Layer 2: LLM (optional) ───────────────────────────────────────────
    use_llm = cfg["classify"].get("use_llm", False)
    if use_llm:
        log.info("LLM classification enabled — sending records to Anthropic API…")
        llm_cfg = cfg["classify"]["llm"]
        # Build list of dicts for LLM (include classification fields + text fields)
        llm_input_fields = llm_cfg.get("input_fields", ["title", "abstract"])
        records_for_llm = []
        for _, row in df.iterrows():
            rec = {col: row[col] for col in llm_input_fields if col in df.columns}
            rec.update({col: row[col] for col in rule_df.columns})
            records_for_llm.append(rec)

        updated = classify_with_llm(records_for_llm, llm_cfg, log)
        llm_df = pd.DataFrame(updated)[list(rule_df.columns) + ["classification_source"]]

        # Overwrite classification columns with LLM results
        for col in llm_df.columns:
            df[col] = llm_df[col].values

        n_llm = (df["classification_source"] == "llm").sum()
        n_fallback = (df["classification_source"] == "rule_based_fallback").sum()
        log.info("LLM classified: %d  |  fell back to rules: %d", n_llm, n_fallback)
    else:
        log.info("LLM classification disabled (set classify.use_llm: true to enable).")

    # ── Save classified CSV ───────────────────────────────────────────────
    output_path = ROOT / cfg["classify"]["output_path"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
    log.info("Saved classified data → %s", output_path)

    # ── Save classification summary ───────────────────────────────────────
    summary_df = build_summary(df)
    summary_dir = ROOT / cfg["tables"]["output_dir"]
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / "classification_summary.csv"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8")
    log.info("Saved classification summary → %s", summary_path)

    # ── Console overview ──────────────────────────────────────────────────
    log.info("\n--- primary_topic distribution ---\n%s",
             df["primary_topic"].value_counts().to_string())
    log.info("\n--- has_health_component ---\n%s",
             df["has_health_component"].value_counts().to_string())
    log.info("\n--- mentions_vulnerable_pop ---\n%s",
             df["mentions_vulnerable_pop"].value_counts().to_string())

    log.info("=== 03_classify.py finished ===")


if __name__ == "__main__":
    main()
