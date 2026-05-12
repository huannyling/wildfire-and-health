"""
03_llm_extract.py — LLM-based structured extraction from paper abstracts.

Input:  data/processed/results_clean.csv       (from 02_clean.py)
Output: data/extracted/abstracts_structured.csv
        data/extracted/causal_triples.csv
        data/extracted/checkpoint.json          (resumable progress)

Usage
-----
  python 03_llm_extract.py --pilot      # process first 100 papers (sonnet)
  python 03_llm_extract.py              # full corpus (haiku, cost-efficient)
  python 03_llm_extract.py --resume     # continue an interrupted run

Requires:
  ANTHROPIC_API_KEY environment variable

Extraction schema (per paper)  — v2
--------------------------------------
Boolean:
  is_health_study     bool  True if the paper studies human or animal health outcomes.
                            False for ecology, atmospheric science, climate modelling, etc.
                            All downstream health analyses filter on is_health_study=True.

Categorical (controlled vocabulary):
  exposure_type       list  wildfire_smoke | PM2.5 | direct_fire | fire_weather |
                            displacement | heat_stress | other
                            NOTE: use "wildfire_smoke" as the primary source exposure.
                            "PM2.5" only if the study measures PM2.5 without attributing
                            it to a specific fire (e.g. ambient air quality studies).
  outcome_types       list  respiratory | cardiovascular | mental_health | mortality |
                            reproductive | neurological | burns_injury | other
  population_groups   list  general | children | elderly | indigenous | low_income |
                            rural | pregnant | outdoor_workers | racial_minorities |
                            homeless | other
  study_design        str   cohort | cross_sectional | case_control | case_crossover |
                            time_series | systematic_review | meta_analysis | modelling |
                            experimental | qualitative | other
                            NOTE: case_crossover is a distinct design (self-controlled,
                            short exposure window); do not conflate with case_control.
  study_regions       list  full country or region names (e.g. "United States", "Brazil")
  geographic_scope    str   local | regional | national | global | not_specified
                            local    = city / county / single fire event area
                            regional = multi-state / province / sub-national region
                            national = one whole country
                            global   = multiple countries or worldwide synthesis
                            not_specified = scope cannot be determined from the text
  vulnerable_focus    str   explicit  (paper's primary aim is a vulnerable subgroup)
                            implicit  (vulnerable group mentioned but not primary focus)
                            none      (no vulnerable subgroup discussed)
  confidence          str   high | medium | low
                            high   = full abstract available, clear methods and findings
                            medium = short abstract or ambiguous design
                            low    = title only, or non-English / methods paper

Free-text (for network and gap analyses):
  biological_mechanisms  list  Short phrases (≤6 words) for BIOLOGICAL or PHYSIOLOGICAL
                               mechanisms only. Examples: "oxidative stress",
                               "airway inflammation", "endothelial dysfunction",
                               "autonomic dysfunction", "systemic inflammation".
  social_determinants    list  Short phrases (≤6 words) for SOCIAL, STRUCTURAL, or
                               CONTEXTUAL factors that modify health risk. Examples:
                               "low socioeconomic status", "limited healthcare access",
                               "poor housing quality", "occupational exposure",
                               "evacuation stress", "language barrier".
  causal_triples         list  {subject, relation, object} — directed causal links.
                               CRITICAL RULES FOR TRIPLES:
                               1. The ROOT node of any chain must be "wildfire smoke"
                                  or "wildfire event" — NEVER "PM2.5" as the first subject.
                               2. PM2.5 appears only as an INTERMEDIATE node
                                  (object of wildfire_smoke → causes → PM2.5, then
                                   subject of PM2.5 → causes → next mechanism).
                               3. Include only chains explicitly stated or very strongly
                                  implied. Do not hallucinate.
                               4. Normalise concept names: always "PM2.5" not
                                  "fine particulate matter"; always "oxidative stress"
                                  not "ROS"; always "airway inflammation" not
                                  "respiratory inflammation".
                               relation must be one of:
                               causes | increases_risk_of | mediates |
                               exacerbates | protects_against | is_associated_with
  gaps_stated            list  Knowledge gaps the AUTHORS THEMSELVES acknowledge in the
                               paper (≤10 words each). Do not infer gaps; only extract
                               explicitly stated limitations or future directions.
                               Field-level gaps are computed separately in 05_gap_analysis.py.
  key_finding            str   One sentence (≤30 words) summarising the main result.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

# ---------------------------------------------------------------------------
# Schema constants — single source of truth for prompt and validator
# ---------------------------------------------------------------------------
EXPOSURE_TYPES = [
    "wildfire_smoke", "PM2.5", "direct_fire", "fire_weather",
    "displacement", "heat_stress", "other",
]
OUTCOME_TYPES = [
    "respiratory", "cardiovascular", "mental_health", "mortality",
    "reproductive", "neurological", "burns_injury", "other",
]
POPULATION_GROUPS = [
    "general", "children", "elderly", "indigenous", "low_income",
    "rural", "pregnant", "outdoor_workers", "racial_minorities", "homeless", "other",
]
STUDY_DESIGNS = [
    "cohort", "cross_sectional", "case_control", "case_crossover",   # ← added case_crossover
    "time_series", "systematic_review", "meta_analysis", "modelling",
    "experimental", "qualitative", "other",
]
GEOGRAPHIC_SCOPES       = ["local", "regional", "national", "global", "not_specified"]
VULNERABLE_FOCUS_VALUES = ["explicit", "implicit", "none"]
CONFIDENCE_VALUES       = ["high", "medium", "low"]
RELATION_TYPES = [
    "causes", "increases_risk_of", "mediates",
    "exacerbates", "protects_against", "is_associated_with",
]

# Concept names that must be normalised in triples (raw → canonical)
CONCEPT_ALIASES: dict[str, str] = {
    "fine particulate matter":   "PM2.5",
    "particulate matter":        "PM2.5",
    "pm 2.5":                    "PM2.5",
    "pm2.5":                     "PM2.5",
    "reactive oxygen species":   "oxidative stress",
    "ros":                       "oxidative stress",
    "ros generation":            "oxidative stress",
    "respiratory inflammation":  "airway inflammation",
    "bronchial inflammation":    "airway inflammation",
    "lung inflammation":         "airway inflammation",
    "heart disease":             "cardiovascular disease",
    "cardiac disease":           "cardiovascular disease",
    "wildfire":                  "wildfire smoke",
    "forest fire smoke":         "wildfire smoke",
    "bushfire smoke":            "wildfire smoke",
}

# ---------------------------------------------------------------------------
# Prompt  (v2 — updated schema)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = f"""You are a biomedical research analyst specialising in wildfire and \
environmental health. You extract structured information from scientific paper abstracts.

For each paper, return ONLY a single valid JSON object — no markdown fences, \
no explanation, no text outside the JSON.

JSON schema (all fields required):
{{
  "is_health_study":         true or false — true ONLY if the paper studies human or \
animal health outcomes. False for ecology, atmospheric science, fire behaviour, \
climate modelling, or remote sensing papers without a health component,

  "exposure_type":           list, values from {json.dumps(EXPOSURE_TYPES)},

  "outcome_types":           list, values from {json.dumps(OUTCOME_TYPES)} — \
use [] if is_health_study is false,

  "population_groups":       list, values from {json.dumps(POPULATION_GROUPS)},

  "study_design":            one string from {json.dumps(STUDY_DESIGNS)},

  "study_regions":           list of full country or region names \
(e.g. "United States", "Australia", "Brazil") — use [] if not specified,

  "geographic_scope":        one string from {json.dumps(GEOGRAPHIC_SCOPES)}
                             local=city/county/single-fire-area, \
regional=multi-state/province, national=one-country, \
global=multi-country/worldwide, not_specified=cannot determine,

  "vulnerable_focus":        one string from {json.dumps(VULNERABLE_FOCUS_VALUES)},

  "biological_mechanisms":   list of short phrases (≤6 words each) for BIOLOGICAL or \
PHYSIOLOGICAL mechanisms ONLY — e.g. "oxidative stress", "airway inflammation", \
"endothelial dysfunction". Do NOT include social or structural factors here,

  "social_determinants":     list of short phrases (≤6 words each) for SOCIAL, STRUCTURAL, \
or CONTEXTUAL factors that modify health risk — e.g. "low socioeconomic status", \
"limited healthcare access", "poor housing quality". Do NOT include biological mechanisms here,

  "causal_triples":          list of objects, each with keys subject/relation/object.
                             CRITICAL RULES:
                             (1) The ROOT subject must always be "wildfire smoke" or \
"wildfire event" — NEVER start a chain with "PM2.5".
                             (2) PM2.5 appears only as an INTERMEDIATE node: \
wildfire smoke → causes → PM2.5, then PM2.5 → causes → next step.
                             (3) Only include what is explicitly stated or very strongly \
implied. Do not hallucinate.
                             (4) Normalise names: always "PM2.5" not "fine particulate \
matter"; always "oxidative stress" not "ROS"; always "airway inflammation" not \
"respiratory inflammation".
                             relation must be one of: {json.dumps(RELATION_TYPES)},

  "gaps_stated":             list of knowledge gaps the AUTHORS THEMSELVES state \
(≤10 words each). Extract only explicit author statements, not your own inference,

  "key_finding":             one sentence (≤30 words) summarising the main result,

  "confidence":              one string from {json.dumps(CONFIDENCE_VALUES)}
                             high=full abstract + clear methods/findings, \
medium=short or ambiguous abstract, low=title only or methods-only paper
}}

General rules:
- Use ONLY the allowed values for categorical fields; never invent new ones.
- Use "other" when no allowed value fits a list field.
- Return [] for any list field with no relevant content.
- If is_health_study is false, still fill study_design, study_regions, \
geographic_scope, and exposure_type if determinable; set outcome_types=[].
"""


def build_user_message(title: str, abstract: str) -> str:
    parts = [f"TITLE: {title.strip()}"]
    if abstract and abstract.strip():
        parts.append(f"ABSTRACT: {abstract.strip()}")
    else:
        parts.append("ABSTRACT: [not available]")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(cfg: dict, pilot: bool) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode_tag  = "pilot" if pilot else "full"
    log_file  = log_dir / f"03_llm_extract_{mode_tag}_{timestamp}.log"
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("03_llm_extract")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Checkpoint management
# ---------------------------------------------------------------------------
def load_checkpoint(path: Path) -> dict:
    """Load existing checkpoint dict {paper_id: extracted_record}."""
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_checkpoint(checkpoint: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Triple normalisation helpers
# ---------------------------------------------------------------------------

def normalise_concept(name: str) -> str:
    """Map known aliases to canonical concept names."""
    return CONCEPT_ALIASES.get(name.strip().lower(), name.strip())


def enforce_pm25_root_rule(triples: list[dict], exposure_types: list[str]) -> list[dict]:
    """
    If wildfire_smoke is an exposure but the first triple's subject is PM2.5,
    prepend the canonical wildfire smoke → PM2.5 triple so the chain is complete.
    This corrects the common LLM error of starting the causal chain at PM2.5.
    """
    if not triples:
        return triples
    first_subject = triples[0].get("subject", "").lower()
    if "pm2.5" in first_subject and "wildfire_smoke" in exposure_types:
        prefix = {"subject": "wildfire smoke", "relation": "causes", "object": "PM2.5"}
        # Avoid duplicate if it somehow already exists
        if triples[0] != prefix:
            triples = [prefix] + triples
    return triples


# ---------------------------------------------------------------------------
# JSON schema validator  (v2)
# ---------------------------------------------------------------------------

def validate_and_repair(parsed: dict, paper_id: str, log: logging.Logger) -> dict:
    """
    Ensure all required fields are present and values are within allowed sets.
    Repairs minor issues in-place rather than discarding the whole record.
    New in v2: is_health_study, geographic_scope, biological_mechanisms,
               social_determinants; PM2.5 root-node enforcement.
    """
    def coerce_list(val, allowed=None):
        if not isinstance(val, list):
            val = [val] if val else []
        if allowed:
            val = [v for v in val if v in allowed]
        return val

    def coerce_str(val, allowed, default="other"):
        if val not in allowed:
            log.debug("[%s] Invalid enum '%s' → '%s'", paper_id, val, default)
            return default
        return val

    def coerce_bool(val, default=True):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() not in ("false", "0", "no")
        return default

    exposure  = coerce_list(parsed.get("exposure_type",     []), EXPOSURE_TYPES)
    is_health = coerce_bool(parsed.get("is_health_study", True))

    result = {
        "paper_id":               paper_id,
        "is_health_study":        is_health,
        "exposure_type":          exposure,
        "outcome_types":          coerce_list(parsed.get("outcome_types",     []), OUTCOME_TYPES),
        "population_groups":      coerce_list(parsed.get("population_groups", []), POPULATION_GROUPS),
        "study_design":           coerce_str( parsed.get("study_design",  "other"), STUDY_DESIGNS),
        "study_regions":          coerce_list(parsed.get("study_regions",     [])),
        "geographic_scope":       coerce_str( parsed.get("geographic_scope", "not_specified"), GEOGRAPHIC_SCOPES),
        "vulnerable_focus":       coerce_str( parsed.get("vulnerable_focus",  "none"), VULNERABLE_FOCUS_VALUES),
        "biological_mechanisms":  coerce_list(parsed.get("biological_mechanisms", [])),
        "social_determinants":    coerce_list(parsed.get("social_determinants",   [])),
        "gaps_stated":            coerce_list(parsed.get("gaps_stated",           [])),
        "key_finding":            str(parsed.get("key_finding", ""))[:300],
        "confidence":             coerce_str( parsed.get("confidence", "low"), CONFIDENCE_VALUES),
        "causal_triples":         [],
        "extraction_status":      "success",
    }

    # ── Validate and normalise causal triples ────────────────────────────
    raw_triples = parsed.get("causal_triples", [])
    if isinstance(raw_triples, list):
        clean = []
        for t in raw_triples:
            if not (isinstance(t, dict) and all(k in t for k in ("subject", "relation", "object"))):
                continue
            clean.append({
                "subject":  normalise_concept(str(t["subject"])[:80]),
                "relation": t["relation"] if t["relation"] in RELATION_TYPES else "is_associated_with",
                "object":   normalise_concept(str(t["object"])[:80]),
            })
        # Enforce PM2.5 root rule
        clean = enforce_pm25_root_rule(clean, exposure)
        result["causal_triples"] = clean

    return result


def make_failed_record(paper_id: str, error_msg: str) -> dict:
    """Return a minimal record flagging extraction failure (v2 schema)."""
    return {
        "paper_id":               paper_id,
        "is_health_study":        True,   # conservative default
        "exposure_type":          [],
        "outcome_types":          [],
        "population_groups":      [],
        "study_design":           "other",
        "study_regions":          [],
        "geographic_scope":       "not_specified",
        "vulnerable_focus":       "none",
        "biological_mechanisms":  [],
        "social_determinants":    [],
        "causal_triples":         [],
        "gaps_stated":            [],
        "key_finding":            "",
        "confidence":             "low",
        "extraction_status":      f"failed: {error_msg[:120]}",
    }


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------
def extract_one(
    client,
    model: str,
    max_tokens: int,
    title: str,
    abstract: str,
    paper_id: str,
    log: logging.Logger,
) -> dict:
    """
    Call the Anthropic API for one paper.
    Returns a validated extraction dict.
    Tries once; on failure returns a failed record (no retry to avoid cost blowout).
    """
    user_msg = build_user_message(title, abstract)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw_text = response.content[0].text.strip()

        # Strip accidental markdown fences
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        parsed = json.loads(raw_text)
        record = validate_and_repair(parsed, paper_id, log)
        return record

    except json.JSONDecodeError as exc:
        log.warning("[%s] JSON parse error: %s", paper_id, exc)
        return make_failed_record(paper_id, f"json_parse: {exc}")
    except Exception as exc:
        log.warning("[%s] API error: %s", paper_id, exc)
        return make_failed_record(paper_id, str(exc))


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------
def records_to_structured_df(records: list[dict]) -> pd.DataFrame:
    """
    Flatten extraction records to a DataFrame.
    List and complex fields are stored as JSON strings for reproducibility
    (downstream scripts reload them with json.loads).
    Column order follows the schema definition for readability.
    """
    # Fields that are lists or complex objects → serialise to JSON string
    LIST_FIELDS = [
        "exposure_type", "outcome_types", "population_groups",
        "study_regions", "biological_mechanisms", "social_determinants",
        "gaps_stated", "causal_triples",
    ]
    # Desired column order
    COLUMN_ORDER = [
        "paper_id", "is_health_study",
        "exposure_type", "outcome_types", "population_groups",
        "study_design", "study_regions", "geographic_scope",
        "vulnerable_focus",
        "biological_mechanisms", "social_determinants",
        "causal_triples", "gaps_stated",
        "key_finding", "confidence", "extraction_status",
    ]
    rows = []
    for r in records:
        row = dict(r)
        for field in LIST_FIELDS:
            row[field] = json.dumps(row.get(field, []), ensure_ascii=False)
        rows.append(row)

    df = pd.DataFrame(rows)
    # Reorder columns: keep declared order, then any extras at the end
    ordered = [c for c in COLUMN_ORDER if c in df.columns]
    extras  = [c for c in df.columns if c not in COLUMN_ORDER]
    return df[ordered + extras]


def records_to_triples_df(records: list[dict]) -> pd.DataFrame:
    """Explode causal_triples into a long-format DataFrame (one row per triple)."""
    rows = []
    for r in records:
        triples = r.get("causal_triples", [])
        if isinstance(triples, str):          # already serialised — re-parse
            triples = json.loads(triples)
        for t in triples:
            rows.append({
                "paper_id": r["paper_id"],
                "subject":  t.get("subject", ""),
                "relation": t.get("relation", ""),
                "object":   t.get("object",  ""),
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["paper_id", "subject", "relation", "object"]
    )


def flush_to_disk(checkpoint: dict, cfg: dict, log: logging.Logger) -> None:
    """Write current checkpoint state to both CSVs and the JSON checkpoint file."""
    ext_cfg = cfg["extract"]
    records  = list(checkpoint.values())

    # Structured CSV
    struct_path = ROOT / ext_cfg["structured_output"]
    struct_path.parent.mkdir(parents=True, exist_ok=True)
    records_to_structured_df(records).to_csv(struct_path, index=False, encoding="utf-8")

    # Triples CSV
    triples_path = ROOT / ext_cfg["triples_output"]
    records_to_triples_df(records).to_csv(triples_path, index=False, encoding="utf-8")

    # JSON checkpoint
    ckpt_path = ROOT / ext_cfg["checkpoint_path"]
    save_checkpoint(checkpoint, ckpt_path)

    log.info("Checkpoint flushed: %d records | structured→%s | triples→%s",
             len(records), struct_path.name, triples_path.name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM-based structured extraction from paper abstracts."
    )
    parser.add_argument(
        "--pilot", action="store_true",
        help="Process only the first pilot_n papers using the higher-quality model.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume an interrupted run by skipping already-checkpointed papers.",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Full run: process ALL papers, clear any existing checkpoint first.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args   = parse_args()
    cfg    = load_config()
    log    = setup_logging(cfg, pilot=args.pilot)
    ext_cfg = cfg["extract"]

    if args.full and args.pilot:
        print("ERROR: --full and --pilot are mutually exclusive.")
        sys.exit(1)

    mode = "PILOT" if args.pilot else "FULL"
    log.info("=== 03_llm_extract.py started (mode: %s | resume: %s | full: %s) ===",
             mode, args.resume, args.full)

    # --- Anthropic client ---
    try:
        import anthropic
    except ImportError:
        log.error("Package 'anthropic' not installed. Run: pip install anthropic")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.error("ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    client    = anthropic.Anthropic(api_key=api_key)
    model     = ext_cfg["model_pilot"] if args.pilot else ext_cfg["model_full"]
    max_tokens = ext_cfg.get("max_tokens", 1024)
    delay      = ext_cfg.get("request_delay", 0.5)
    ckpt_every = ext_cfg.get("checkpoint_every", 25)
    pilot_n    = ext_cfg.get("pilot_n", 100)

    log.info("Model: %s | checkpoint every %d papers | delay %.1fs per call",
             model, ckpt_every, delay)

    # --- Load input data ---
    input_path = ROOT / cfg["clean"]["output_path"]
    if not input_path.exists():
        log.error("Input not found: %s — run 02_clean.py first.", input_path)
        sys.exit(1)

    df = pd.read_csv(input_path, dtype=str).fillna("")
    # Drop rows with no abstract for the full run to avoid wasting calls
    df_with_abstract = df[df["abstract"].str.strip().ne("")]
    df_no_abstract   = df[df["abstract"].str.strip().eq("")]
    log.info("Loaded %d records: %d have abstracts, %d title-only",
             len(df), len(df_with_abstract), len(df_no_abstract))

    # Pilot: restrict to first N rows (title-only papers included to match pilot behaviour)
    if args.pilot:
        df = df.head(pilot_n)
        log.info("Pilot mode: restricted to %d records.", len(df))
    else:
        # Full run: only process papers with abstracts (title-only = low-value extractions)
        df = df_with_abstract.copy()
        log.info("Full run: processing %d papers with abstracts.", len(df))

    # Estimate cost (Haiku: $0.80/M input + $4.00/M output tokens, ~900+300 tokens/paper)
    n_todo_est = len(df)
    cost_est = n_todo_est * (900 * 0.80 + 300 * 4.00) / 1_000_000
    log.info("Estimated API cost: ~$%.2f USD for %d papers (Haiku rates)", cost_est, n_todo_est)

    # --- Load or reset checkpoint ---
    ckpt_path = ROOT / ext_cfg["checkpoint_path"]

    if args.full:
        # Explicit full run: wipe pilot checkpoint so we start from zero
        log.info("--full flag: clearing existing checkpoint (%s).", ckpt_path)
        if ckpt_path.exists():
            ckpt_path.unlink()
        checkpoint = {}
    elif args.resume:
        checkpoint = load_checkpoint(ckpt_path)
        log.info("Resuming: %d records already in checkpoint.", len(checkpoint))
    else:
        checkpoint = {}

    already_done = set(checkpoint.keys())
    # Papers still to process
    todo = df[~df["openalex_id"].isin(already_done)]
    log.info("Papers remaining to process: %d", len(todo))

    if todo.empty:
        log.info("Nothing to do — all records already extracted.")
    else:
        since_last_flush = 0
        failed_count     = 0
        n_health_so_far  = 0
        n_triples_so_far = 0
        import json as _json

        for i, (_, row) in enumerate(todo.iterrows(), start=1):
            paper_id = row.get("openalex_id", f"unknown_{i}")
            title    = row.get("title",    "")
            abstract = row.get("abstract", "")

            record = extract_one(
                client, model, max_tokens,
                title, abstract, paper_id, log,
            )

            checkpoint[paper_id] = record
            since_last_flush += 1

            if record["extraction_status"] != "success":
                failed_count += 1
            if record.get("is_health_study"):
                n_health_so_far += 1
            try:
                triples = record.get("causal_triples", [])
                if isinstance(triples, str):
                    triples = _json.loads(triples)
                n_triples_so_far += len(triples)
            except Exception:
                pass

            # Progress log every 50 papers
            if i % 50 == 0 or i == len(todo):
                total_done = len(already_done) + i
                success_rate = (i - failed_count) / i * 100
                log.info(
                    "PROGRESS %d/%d (total done: %d) | success %.1f%% | "
                    "health so far: %d | triples so far: %d",
                    i, len(todo), total_done,
                    success_rate, n_health_so_far, n_triples_so_far,
                )

            # Checkpoint flush every ckpt_every papers
            if since_last_flush >= ckpt_every:
                flush_to_disk(checkpoint, cfg, log)
                since_last_flush = 0

            # Rate-limit courtesy delay
            if i < len(todo):
                time.sleep(delay)

        # Final flush
        flush_to_disk(checkpoint, cfg, log)
        log.info("Extraction loop complete: %d processed | %d failed",
                 len(todo), failed_count)

    # --- Final summary and success-criteria check ---
    all_records = list(checkpoint.values())
    total = len(all_records)
    if total > 0:
        def _parse_list(val):
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    return []
            return val or []

        n_success    = sum(1 for r in all_records if r.get("extraction_status") == "success")
        n_health     = sum(1 for r in all_records if r.get("is_health_study") is True)
        n_high       = sum(1 for r in all_records if r.get("confidence") == "high")
        n_with_vuln  = sum(1 for r in all_records if r.get("vulnerable_focus") != "none")
        n_triples    = sum(len(_parse_list(r.get("causal_triples", []))) for r in all_records)
        n_bio_mechs  = sum(len(_parse_list(r.get("biological_mechanisms", []))) for r in all_records)
        n_soc_dets   = sum(len(_parse_list(r.get("social_determinants",   []))) for r in all_records)

        log.info("─" * 60)
        log.info("EXTRACTION SUMMARY (v2 schema)")
        log.info("  Total records processed: %d", total)
        log.info("  Successful extractions:  %d (%.1f%%)", n_success, n_success / total * 100)
        log.info("  Health studies:          %d (%.1f%%)", n_health,  n_health  / total * 100)
        log.info("  High confidence:         %d (%.1f%%)", n_high,    n_high    / total * 100)
        log.info("  Vulnerable pop. focus:   %d papers (%.1f%%)", n_with_vuln, n_with_vuln / total * 100)
        log.info("  Causal triples total:    %d", n_triples)
        log.info("  Biological mechanisms:   %d mentions", n_bio_mechs)
        log.info("  Social determinants:     %d mentions", n_soc_dets)
        log.info("─" * 60)
        log.info("  Structured CSV → %s", ROOT / ext_cfg["structured_output"])
        log.info("  Triples CSV    → %s", ROOT / ext_cfg["triples_output"])
        log.info("  Checkpoint     → %s", ckpt_path)

        # ── SUCCESS CRITERIA CHECK ──────────────────────────────────────────
        log.info("─" * 60)
        log.info("SUCCESS CRITERIA CHECK:")
        ok_health  = n_health  >= 400
        ok_triples = n_triples >= 1000
        log.info("  Health papers ≥ 400 :  %s  (got %d)", "✓ PASS" if ok_health  else "✗ FAIL", n_health)
        log.info("  Causal triples ≥ 1000: %s  (got %d)", "✓ PASS" if ok_triples else "✗ FAIL", n_triples)

        if ok_health and ok_triples:
            log.info("  ✓ ALL CRITERIA MET — FULL RUN COMPLETED")
        else:
            log.error("  ✗ CRITERIA NOT MET — check corpus and re-run if needed")
            if not ok_health:
                log.error("    → Only %d health papers found (need ≥ 400). "
                           "Consider widening search or checking is_health_study logic.", n_health)
            if not ok_triples:
                log.error("    → Only %d triples extracted (need ≥ 1000). "
                           "Consider reviewing causal_triples prompt rules.", n_triples)
        log.info("─" * 60)

    log.info("=== 03_llm_extract.py finished ===")


if __name__ == "__main__":
    main()
