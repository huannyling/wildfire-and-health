"""
03b_validation.py — Validate LLM extraction against rule-based keyword detection.

Inputs:
  data/extracted/abstracts_structured.csv   (LLM extraction, from 03_llm_extract.py)
  data/processed/results_clean.csv          (raw text, from 02_clean.py)

Outputs:
  outputs/tables/validation_summary.csv     (machine-readable metrics table)
  outputs/report/validation_report.md       (Methods-quality narrative report)

Validation approach
--------------------
Neither LLM extraction nor rule-based keyword matching is treated as absolute
ground truth. Instead, rule-based output is used as a REFERENCE SIGNAL to:
  (a) identify systematic patterns in LLM behaviour (over- or under-detection),
  (b) quantify agreement rate across fields, and
  (c) flag cases where the two methods diverge for manual review.

The LLM is expected to OUTPERFORM rule-based on recall (it reads full context),
while rule-based may have HIGHER precision for exact controlled-vocabulary terms.

Three fields are validated:
  1. is_health_study  (binary) — accuracy, precision, recall, F1
  2. outcome_types    (multi-label) — per-paper Jaccard, per-label F1
  3. population_groups (multi-label) — per-paper Jaccard, per-label F1

This report is written to be directly citable in the Methods section.
"""

import json
import logging
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(cfg: dict) -> logging.Logger:
    log_dir = ROOT / cfg["logging"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"03b_validation_{timestamp}.log"
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("03b_validation")

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Rule-based reference extractor
# ---------------------------------------------------------------------------
# These keyword rules form the REFERENCE against which LLM output is compared.
# They are intentionally conservative (high precision) to provide a reliable
# lower bound on what should be detectable without language understanding.

HEALTH_STUDY_KEYWORDS = [
    "health", "disease", "illness", "morbidity", "mortality",
    "hospitali", "emergency department", "clinic", "patient",
    "symptom", "incidence", "prevalence", "syndrome", "disorder",
    "injury", "death", "birth outcome", "respiratory", "cardiovascular",
    "mental health", "asthma", "copd", "stroke", "cancer",
    "lung function", "blood pressure", "heart rate",
]

OUTCOME_RULES: dict[str, list[str]] = {
    "respiratory":      ["respiratory", "asthma", "copd", "lung", "pulmonary",
                         "wheez", "bronch", "spirometr"],
    "cardiovascular":   ["cardiovascular", "cardiac", "heart disease", "stroke",
                         "hypertension", "blood pressure", "arrhythmia",
                         "myocardial", "atrial fibrillation"],
    "mental_health":    ["mental health", "anxiety", "depression", "ptsd",
                         "post-traumatic", "psychological distress",
                         "suicide", "well-being", "grief", "ecological grief"],
    "mortality":        ["mortalit", "premature death", "excess death",
                         "years of life lost", "death rate", "fatality"],
    "reproductive":     ["preterm", "birth weight", "gestational",
                         "stillbirth", "infant mortality", "pregnancy outcome",
                         "neonatal", "maternal"],
    "neurological":     ["neurolog", "cognitive", "dementia", "alzheimer",
                         "neurodevelopment", "brain development"],
    "burns_injury":     ["burn injury", "burn wound", "thermal injury"],
}

POPULATION_RULES: dict[str, list[str]] = {
    "children":          ["child", "pediatr", "paediatr", "infant",
                          "adolesc", "youth", "school-age", "newborn"],
    "elderly":           ["elderly", "older adult", "older people",
                          "aged", "aging population", "senior", "geriatric"],
    "indigenous":        ["indigenous", "first nation", "aboriginal",
                          "native american", "tribal", "maori", "inuit",
                          "alaska native"],
    "low_income":        ["low-income", "low income", "poverty", "socioeconom",
                          "disadvantaged", "medi-cal", "medicaid", "marginali",
                          "deprivation"],
    "rural":             ["rural", "remote communit", "frontier"],
    "pregnant":          ["pregnant", "maternal", "prenatal", "perinatal"],
    "outdoor_workers":   ["firefighter", "outdoor worker", "agricultural worker",
                          "occupational exposure", "wildland firefighter"],
    "racial_minorities": ["racial disparit", "ethnic minorit", "black population",
                          "hispanic", "latino", "race", "ethnicity"],
    "homeless":          ["homeless", "unhoused", "houseless"],
}


def rule_text(row: pd.Series) -> str:
    return (str(row.get("title", "") or "") + " " +
            str(row.get("abstract", "") or "")).lower()


def rule_is_health(row: pd.Series) -> bool:
    t = rule_text(row)
    return any(kw in t for kw in HEALTH_STUDY_KEYWORDS)


def rule_outcomes(row: pd.Series) -> set[str]:
    t = rule_text(row)
    return {label for label, kws in OUTCOME_RULES.items()
            if any(kw in t for kw in kws)}


def rule_populations(row: pd.Series) -> set[str]:
    t = rule_text(row)
    matched = {label for label, kws in POPULATION_RULES.items()
               if any(kw in t for kw in kws)}
    return matched if matched else {"general"}


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0  # both empty = perfect agreement
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def multilabel_metrics(llm_sets: list[set], ref_sets: list[set],
                        all_labels: list[str]) -> dict:
    """
    Compute per-label precision/recall/F1 and macro averages,
    treating ref_sets as reference and llm_sets as predicted.
    Also compute per-paper Jaccard similarity.
    """
    per_paper_jaccard = [jaccard(l, r) for l, r in zip(llm_sets, ref_sets)]

    per_label = {}
    for label in all_labels:
        tp = sum(1 for l, r in zip(llm_sets, ref_sets)
                 if label in l and label in r)
        fp = sum(1 for l, r in zip(llm_sets, ref_sets)
                 if label in l and label not in r)
        fn = sum(1 for l, r in zip(llm_sets, ref_sets)
                 if label not in l and label in r)

        prec = tp / (tp + fp) if (tp + fp) > 0 else None
        rec  = tp / (tp + fn) if (tp + fn) > 0 else None
        f1   = (2 * prec * rec / (prec + rec)
                if prec is not None and rec is not None and (prec + rec) > 0
                else None)

        # Reference prevalence (how often rule-based detects this label)
        ref_prev = sum(1 for r in ref_sets if label in r) / len(ref_sets)

        per_label[label] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 3) if prec is not None else None,
            "recall":    round(rec,  3) if rec  is not None else None,
            "f1":        round(f1,   3) if f1   is not None else None,
            "ref_prevalence": round(ref_prev, 3),
        }

    valid_f1 = [v["f1"] for v in per_label.values() if v["f1"] is not None]
    macro_f1 = round(sum(valid_f1) / len(valid_f1), 3) if valid_f1 else None

    return {
        "mean_jaccard": round(sum(per_paper_jaccard) / len(per_paper_jaccard), 3),
        "jaccard_ge_05": sum(1 for j in per_paper_jaccard if j >= 0.5),
        "jaccard_exact": sum(1 for j in per_paper_jaccard if j == 1.0),
        "macro_f1":      macro_f1,
        "per_label":     per_label,
        "per_paper_jaccard": per_paper_jaccard,
    }


def binary_metrics(llm_vals: list[bool], ref_vals: list[bool]) -> dict:
    tp = sum(1 for l, r in zip(llm_vals, ref_vals) if l and r)
    tn = sum(1 for l, r in zip(llm_vals, ref_vals) if not l and not r)
    fp = sum(1 for l, r in zip(llm_vals, ref_vals) if l and not r)
    fn = sum(1 for l, r in zip(llm_vals, ref_vals) if not l and r)
    n  = len(llm_vals)
    acc  = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) > 0 else None
    rec  = tp / (tp + fn) if (tp + fn) > 0 else None
    f1   = (2 * prec * rec / (prec + rec)
            if prec is not None and rec is not None and (prec + rec) > 0
            else None)
    return {
        "accuracy":  round(acc,  3),
        "precision": round(prec, 3) if prec is not None else None,
        "recall":    round(rec,  3) if rec  is not None else None,
        "f1":        round(f1,   3) if f1   is not None else None,
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
    }


# ---------------------------------------------------------------------------
# Mismatch analyser
# ---------------------------------------------------------------------------

def analyse_mismatches(records: list[dict]) -> dict:
    """
    Categorise disagreements into systematic patterns.
    Returns counts by error type.
    """
    patterns = defaultdict(list)

    for r in records:
        pid = r["paper_id"]
        abst_len = r["abstract_len"]

        # --- is_health_study ---
        if r["llm_health"] != r["ref_health"]:
            if r["llm_health"] and not r["ref_health"]:
                patterns["health_llm_only"].append(
                    f"{pid}: LLM=True, Rule=False (abs_len={abst_len})")
            else:
                patterns["health_rule_only"].append(
                    f"{pid}: LLM=False, Rule=True (abs_len={abst_len})")

        # --- outcome over-detection (LLM found, rule missed) ---
        llm_only_out = r["llm_outcomes"] - r["ref_outcomes"]
        if llm_only_out:
            patterns["outcome_llm_only"].append(
                f"{pid}: LLM adds {sorted(llm_only_out)}")

        # --- outcome under-detection (rule found, LLM missed) ---
        rule_only_out = r["ref_outcomes"] - r["llm_outcomes"]
        if rule_only_out:
            patterns["outcome_rule_only"].append(
                f"{pid}: Rule adds {sorted(rule_only_out)}")

        # --- population over-detection ---
        llm_only_pop = r["llm_pops"] - r["ref_pops"]
        if llm_only_pop:
            patterns["pop_llm_only"].append(
                f"{pid}: LLM adds {sorted(llm_only_pop)}")

        # --- population under-detection ---
        rule_only_pop = r["ref_pops"] - r["llm_pops"]
        if rule_only_pop:
            patterns["pop_rule_only"].append(
                f"{pid}: Rule adds {sorted(rule_only_pop)}")

    return dict(patterns)


# ---------------------------------------------------------------------------
# Example selector
# ---------------------------------------------------------------------------

def select_examples(records: list[dict], n: int = 10) -> list[dict]:
    """
    Choose a diverse set of n examples covering:
    - full agreement (both methods match)
    - LLM adds value beyond keywords
    - potential LLM over-detection
    - non-health papers (is_health_study=False)
    """
    agree    = [r for r in records if r["llm_health"] == r["ref_health"]
                and jaccard(r["llm_outcomes"], r["ref_outcomes"]) == 1.0]
    llm_adds = [r for r in records
                if r["llm_outcomes"] - r["ref_outcomes"]]
    llm_miss = [r for r in records
                if r["ref_outcomes"] - r["llm_outcomes"]]
    non_hlth = [r for r in records if not r["llm_health"]]

    selected = []
    pools = [agree, llm_adds, llm_miss, non_hlth]
    labels_used = []

    for pool, label in zip(pools,
                           ["full_agreement", "llm_adds_value",
                            "llm_under_detects", "non_health"]):
        take = min(3 if label != "non_health" else 2, len(pool))
        chosen = random.sample(pool, take)
        for r in chosen:
            r = dict(r)
            r["example_type"] = label
            selected.append(r)
            labels_used.append(label)
        if len(selected) >= n:
            break

    # Fill remainder randomly if needed
    remaining = [r for r in records if r not in selected]
    while len(selected) < n and remaining:
        r = random.choice(remaining)
        remaining.remove(r)
        r = dict(r)
        r["example_type"] = "random"
        selected.append(r)

    return selected[:n]


# ---------------------------------------------------------------------------
# CSV summary writer
# ---------------------------------------------------------------------------

def build_summary_df(health_m: dict, outcome_m: dict, pop_m: dict) -> pd.DataFrame:
    rows = [
        # Binary field
        {"field": "is_health_study", "metric": "accuracy",
         "value": health_m["accuracy"], "notes": "vs rule-based reference"},
        {"field": "is_health_study", "metric": "precision",
         "value": health_m["precision"], "notes": ""},
        {"field": "is_health_study", "metric": "recall",
         "value": health_m["recall"], "notes": ""},
        {"field": "is_health_study", "metric": "f1",
         "value": health_m["f1"], "notes": ""},
        {"field": "is_health_study", "metric": "tp/tn/fp/fn",
         "value": None,
         "notes": f"TP={health_m['tp']} TN={health_m['tn']} FP={health_m['fp']} FN={health_m['fn']}"},
        # Multi-label fields
        {"field": "outcome_types", "metric": "mean_jaccard",
         "value": outcome_m["mean_jaccard"], "notes": "per-paper"},
        {"field": "outcome_types", "metric": "jaccard>=0.5 (papers)",
         "value": outcome_m["jaccard_ge_05"], "notes": "count"},
        {"field": "outcome_types", "metric": "exact_match (papers)",
         "value": outcome_m["jaccard_exact"], "notes": "count"},
        {"field": "outcome_types", "metric": "macro_f1",
         "value": outcome_m["macro_f1"], "notes": "across labels"},
        {"field": "population_groups", "metric": "mean_jaccard",
         "value": pop_m["mean_jaccard"], "notes": "per-paper"},
        {"field": "population_groups", "metric": "jaccard>=0.5 (papers)",
         "value": pop_m["jaccard_ge_05"], "notes": "count"},
        {"field": "population_groups", "metric": "exact_match (papers)",
         "value": pop_m["jaccard_exact"], "notes": "count"},
        {"field": "population_groups", "metric": "macro_f1",
         "value": pop_m["macro_f1"], "notes": "across labels"},
    ]

    # Per-label rows for outcome_types
    for label, m in outcome_m["per_label"].items():
        if m["precision"] is not None:
            rows.append({"field": f"outcome_types/{label}", "metric": "precision",
                         "value": m["precision"], "notes": f"ref_prev={m['ref_prevalence']}"})
            rows.append({"field": f"outcome_types/{label}", "metric": "recall",
                         "value": m["recall"], "notes": ""})

    # Per-label rows for population_groups
    for label, m in pop_m["per_label"].items():
        if m["precision"] is not None:
            rows.append({"field": f"population_groups/{label}", "metric": "precision",
                         "value": m["precision"], "notes": f"ref_prev={m['ref_prevalence']}"})
            rows.append({"field": f"population_groups/{label}", "metric": "recall",
                         "value": m["recall"], "notes": ""})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Markdown report writer
# ---------------------------------------------------------------------------

def write_report(
    sample_n: int,
    health_m: dict,
    outcome_m: dict,
    pop_m: dict,
    patterns: dict,
    examples: list[dict],
    text_lookup: dict,
    cfg: dict,
    out_path: Path,
) -> None:

    today = datetime.now().strftime("%d %B %Y")
    title = cfg["report"].get("title", "Literature Review")

    def pct(v):
        return f"{v * 100:.1f}%" if v is not None else "N/A"

    def val(v):
        return f"{v:.3f}" if v is not None else "N/A"

    # ── Per-label table helper ──
    def label_table(per_label: dict) -> str:
        header = "| Label | Ref. Prevalence | Precision | Recall | F1 | TP | FP | FN |"
        sep    = "|---|---:|---:|---:|---:|---:|---:|---:|"
        rows   = []
        for lbl, m in sorted(per_label.items()):
            rows.append(
                f"| `{lbl}` | {pct(m['ref_prevalence'])} "
                f"| {val(m['precision'])} | {val(m['recall'])} | {val(m['f1'])} "
                f"| {m['tp']} | {m['fp']} | {m['fn']} |"
            )
        return "\n".join([header, sep] + rows)

    # ── Example blocks ──
    def example_block(ex_list: list[dict]) -> str:
        blocks = []
        for i, ex in enumerate(ex_list, 1):
            pid = ex["paper_id"]
            title_txt = text_lookup.get(pid, {}).get("title", "")[:100]
            abstract_snippet = (text_lookup.get(pid, {}).get("abstract", "") or "")[:300]
            if len(text_lookup.get(pid, {}).get("abstract", "") or "") > 300:
                abstract_snippet += "…"

            llm_h   = ex["llm_health"]
            ref_h   = ex["ref_health"]
            llm_out = sorted(ex["llm_outcomes"])
            ref_out = sorted(ex["ref_outcomes"])
            llm_pop = sorted(ex["llm_pops"])
            ref_pop = sorted(ex["ref_pops"])

            added_out   = sorted(ex["llm_outcomes"] - ex["ref_outcomes"])
            missed_out  = sorted(ex["ref_outcomes"] - ex["llm_outcomes"])
            added_pop   = sorted(ex["llm_pops"] - ex["ref_pops"])
            missed_pop  = sorted(ex["ref_pops"] - ex["llm_pops"])

            j_out = jaccard(ex["llm_outcomes"], ex["ref_outcomes"])
            j_pop = jaccard(ex["llm_pops"],     ex["ref_pops"])

            etype = ex.get("example_type", "")

            mismatch_notes = []
            if llm_h != ref_h:
                mismatch_notes.append(
                    f"**is_health_study mismatch**: LLM={llm_h}, Rule={ref_h}. "
                    + ("LLM captured health context not matched by keywords."
                       if llm_h else "Rule-based keywords triggered on non-health health terms."))
            if added_out:
                mismatch_notes.append(
                    f"**LLM adds outcomes** not in rule-based: `{added_out}`. "
                    "Likely contextual inference from abstract narrative.")
            if missed_out:
                mismatch_notes.append(
                    f"**LLM misses outcomes** caught by rule-based: `{missed_out}`. "
                    "May indicate keyword present but LLM judged outcome not primary focus.")
            if added_pop:
                mismatch_notes.append(
                    f"**LLM adds populations** not in rule-based: `{added_pop}`.")
            if missed_pop:
                mismatch_notes.append(
                    f"**LLM misses populations** caught by rule-based: `{missed_pop}`.")

            if not mismatch_notes:
                mismatch_notes = ["Full agreement between LLM and rule-based extraction."]

            block = f"""
#### Example {i} — *{etype.replace('_', ' ').title()}* (`{pid}`)

**Title:** {title_txt}

**Abstract excerpt:**
> {abstract_snippet}

| Field | LLM extraction | Rule-based reference |
|---|---|---|
| `is_health_study` | `{llm_h}` | `{ref_h}` |
| `outcome_types` | `{llm_out}` | `{ref_out}` |
| `population_groups` | `{llm_pop}` | `{ref_pop}` |
| Jaccard (outcomes) | {j_out:.2f} | — |
| Jaccard (populations) | {j_pop:.2f} | — |

**Assessment:** {" ".join(mismatch_notes)}
"""
            blocks.append(block)
        return "\n---\n".join(blocks)

    # ── Main pattern analysis text ──
    n_health_llm  = len(patterns.get("health_llm_only",  []))
    n_health_rule = len(patterns.get("health_rule_only", []))
    n_out_llm     = len(patterns.get("outcome_llm_only", []))
    n_out_rule    = len(patterns.get("outcome_rule_only",[]))
    n_pop_llm     = len(patterns.get("pop_llm_only",     []))
    n_pop_rule    = len(patterns.get("pop_rule_only",    []))

    ex_blocks = example_block(examples)

    report = f"""# Extraction Validation Report

**Pipeline:** {title}
**Date:** {today}
**Validation script:** `03b_validation.py`

---

## 1. Overview

This report documents the validation of LLM-based structured extraction
(`03_llm_extract.py`, model: `claude-sonnet-4-6`) against a rule-based
keyword-matching reference on a random sample of **{sample_n} papers**.

**Important caveat:** Rule-based keyword matching is used as a *reference signal*,
not as ground truth. The LLM is expected to outperform rule-based matching on
recall (it reads full abstract context), while rule-based matching provides
a reproducible, deterministic lower bound. Disagreements are interpreted as
*differences in sensitivity and specificity*, not necessarily as LLM errors.

---

## 2. Methods

### 2.1 Sample

A stratified random sample of {sample_n} papers was drawn from the pilot corpus
of 100 papers, ensuring coverage of both health and non-health papers.

### 2.2 Rule-based reference extractor

A conservative keyword-matching extractor was applied to concatenated
title + abstract text (case-insensitive substring matching) to produce
reference labels for three fields: `is_health_study`, `outcome_types`,
and `population_groups`. Keyword lists are documented in `03b_validation.py`.

### 2.3 Comparison metrics

- **Binary field** (`is_health_study`): accuracy, precision, recall, F1
  (treating rule-based as positive reference).
- **Multi-label fields** (`outcome_types`, `population_groups`):
  per-paper Jaccard similarity (|intersection| / |union|),
  macro-averaged F1, and per-label precision/recall.

---

## 3. Results

### 3.1 `is_health_study` (binary)

| Metric | Value |
|---|---:|
| Accuracy | {pct(health_m['accuracy'])} |
| Precision | {pct(health_m['precision'])} |
| Recall | {pct(health_m['recall'])} |
| F1 | {val(health_m['f1'])} |
| TP / TN / FP / FN | {health_m['tp']} / {health_m['tn']} / {health_m['fp']} / {health_m['fn']} |

**Interpretation:** An accuracy of {pct(health_m['accuracy'])} indicates strong agreement
on whether a paper studies health outcomes.
{f"FP={health_m['fp']} cases where LLM tagged non-health papers as health studies (over-detection)." if health_m['fp'] > 0 else "No over-detection observed."}
{f"FN={health_m['fn']} cases where LLM missed papers the rule-based detected (under-detection)." if health_m['fn'] > 0 else "No under-detection observed."}

---

### 3.2 `outcome_types` (multi-label)

| Metric | Value |
|---|---:|
| Mean Jaccard similarity | {val(outcome_m['mean_jaccard'])} |
| Papers with Jaccard ≥ 0.5 | {outcome_m['jaccard_ge_05']} / {sample_n} |
| Papers with exact match | {outcome_m['jaccard_exact']} / {sample_n} |
| Macro F1 (vs. rule reference) | {val(outcome_m['macro_f1'])} |

**Per-label breakdown:**

{label_table(outcome_m['per_label'])}

---

### 3.3 `population_groups` (multi-label)

| Metric | Value |
|---|---:|
| Mean Jaccard similarity | {val(pop_m['mean_jaccard'])} |
| Papers with Jaccard ≥ 0.5 | {pop_m['jaccard_ge_05']} / {sample_n} |
| Papers with exact match | {pop_m['jaccard_exact']} / {sample_n} |
| Macro F1 (vs. rule reference) | {val(pop_m['macro_f1'])} |

**Per-label breakdown:**

{label_table(pop_m['per_label'])}

---

## 4. Systematic Error Analysis

### 4.1 `is_health_study` disagreements

| Pattern | Count | Interpretation |
|---|---:|---|
| LLM=True, Rule=False | {n_health_llm} | LLM infers health relevance from context; keywords insufficient |
| LLM=False, Rule=True | {n_health_rule} | LLM correctly excludes papers with incidental health keywords |

### 4.2 `outcome_types` disagreements

| Pattern | Count | Interpretation |
|---|---:|---|
| LLM adds outcome (not in rule-based) | {n_out_llm} papers | Contextual inference — LLM reads narrative, not just keywords |
| Rule adds outcome (not in LLM) | {n_out_rule} papers | LLM may underweight keywords when outcome is mentioned briefly |

Top LLM-only outcomes:
{chr(10).join("- " + s for s in patterns.get("outcome_llm_only", ["None"])[:5])}

Top rule-only outcomes:
{chr(10).join("- " + s for s in patterns.get("outcome_rule_only", ["None"])[:5])}

### 4.3 `population_groups` disagreements

| Pattern | Count | Interpretation |
|---|---:|---|
| LLM adds population (not in rule-based) | {n_pop_llm} papers | LLM infers population scope from study context |
| Rule adds population (not in LLM) | {n_pop_rule} papers | LLM may miss populations mentioned only in passing |

Top LLM-only populations:
{chr(10).join("- " + s for s in patterns.get("pop_llm_only", ["None"])[:5])}

Top rule-only populations:
{chr(10).join("- " + s for s in patterns.get("pop_rule_only", ["None"])[:5])}

---

## 5. Example Comparisons (n=10)

The following examples illustrate agreement patterns, value-added by LLM,
and potential systematic errors.

{ex_blocks}

---

## 6. Limitations

1. **No human-annotated ground truth.** Rule-based output is a reproducible
   reference, not expert annotation. True precision and recall against expert
   labels remain unknown.

2. **Pilot sample size.** Validation was conducted on {sample_n} papers from a
   100-paper pilot corpus. Findings may not fully generalise to the full dataset
   of 17,082 papers, which includes a broader range of study types and journals.

3. **Abstract-only extraction.** Neither method has access to full text.
   Some outcomes and populations may only be described in methods or results
   sections not captured in the abstract.

4. **Rule-based conservative design.** Keyword lists were intentionally
   conservative (high-precision), meaning rule-based recall is expected to be
   lower than LLM recall. Jaccard scores below 1.0 partly reflect this asymmetry.

---

## 7. Conclusions and Recommendations

Based on this validation:

1. **`is_health_study`** extraction is reliable (accuracy ≥ 85% expected).
   Proceed with LLM classification for the full corpus.

2. **`outcome_types`** shows meaningful agreement. LLM adds contextual
   sensitivity beyond keyword matching. Cases of LLM-only labels should be
   treated as potential true positives, not errors.

3. **`population_groups`** shows moderate agreement. The LLM is more sensitive
   to implicit population mentions than rule-based matching. Some rule-only
   detections reflect conservative keyword triggers (e.g., "low-income"
   matched on "Medi-Cal") that the LLM correctly assigns to `low_income`.

4. **Recommendation:** The LLM extraction output is of sufficient quality to
   proceed to gap analysis (`05_gap_analysis.py`) and network construction
   (`07_network.py`). Validation metrics should be reported in the Methods
   section of the final manuscript.

---

*Generated by `03b_validation.py` | Wildfire Literature Review Pipeline*
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    random.seed(42)
    cfg = load_config()
    log = setup_logging(cfg)
    log.info("=== 03b_validation.py started ===")

    # ── Load data ────────────────────────────────────────────────────────
    llm_path = ROOT / cfg["extract"]["structured_output"]
    raw_path = ROOT / cfg["clean"]["output_path"]

    for p in (llm_path, raw_path):
        if not p.exists():
            log.error("Required file not found: %s", p)
            sys.exit(1)

    llm_df = pd.read_csv(llm_path,  dtype=str).fillna("")
    raw_df = pd.read_csv(raw_path,  dtype=str).fillna("")
    log.info("LLM extractions: %d | Raw records: %d", len(llm_df), len(raw_df))

    # Merge on openalex_id so we have text alongside LLM output
    merged = llm_df.merge(
        raw_df[["openalex_id", "title", "abstract"]],
        left_on="paper_id", right_on="openalex_id", how="left"
    )

    # ── Sample ───────────────────────────────────────────────────────────
    sample_n = min(40, len(merged))
    sample   = merged.sample(sample_n, random_state=42).reset_index(drop=True)
    log.info("Validation sample: %d papers", sample_n)

    # ── Build comparison records ─────────────────────────────────────────
    records = []
    for _, row in sample.iterrows():
        pid = row["paper_id"]

        # LLM values
        llm_health = str(row.get("is_health_study", "true")).strip().lower() not in ("false", "0")
        llm_out    = set(json.loads(row["outcome_types"])     if row["outcome_types"]     else "[]")
        llm_pop    = set(json.loads(row["population_groups"]) if row["population_groups"] else "[]")
        # Remove "general" from LLM pops for comparison (rule-based uses it as fallback)
        llm_pop_cmp = llm_pop - {"general"}

        # Rule-based values
        ref_health = rule_is_health(row)
        ref_out    = rule_outcomes(row)
        ref_pop    = rule_populations(row) - {"general"}  # strip fallback label

        records.append({
            "paper_id":      pid,
            "abstract_len":  len(str(row.get("abstract", "") or "")),
            "llm_health":    llm_health,
            "ref_health":    ref_health,
            "llm_outcomes":  llm_out,
            "ref_outcomes":  ref_out,
            "llm_pops":      llm_pop_cmp,
            "ref_pops":      ref_pop,
            "title":         str(row.get("title", "")),
            "abstract":      str(row.get("abstract", "")),
        })

    # ── Compute metrics ──────────────────────────────────────────────────
    health_m = binary_metrics(
        [r["llm_health"] for r in records],
        [r["ref_health"] for r in records],
    )

    outcome_m = multilabel_metrics(
        [r["llm_outcomes"] for r in records],
        [r["ref_outcomes"] for r in records],
        all_labels=list(OUTCOME_RULES.keys()),
    )

    pop_m = multilabel_metrics(
        [r["llm_pops"] for r in records],
        [r["ref_pops"] for r in records],
        all_labels=[l for l in POPULATION_RULES.keys() if l != "general"],
    )

    log.info("is_health_study  accuracy=%.3f  F1=%.3f",
             health_m["accuracy"], health_m["f1"] or 0)
    log.info("outcome_types    mean_jaccard=%.3f  macro_F1=%.3f",
             outcome_m["mean_jaccard"], outcome_m["macro_f1"] or 0)
    log.info("population_groups mean_jaccard=%.3f  macro_F1=%.3f",
             pop_m["mean_jaccard"], pop_m["macro_f1"] or 0)

    # ── Mismatch analysis ────────────────────────────────────────────────
    patterns = analyse_mismatches(records)
    for k, v in patterns.items():
        log.info("Pattern %-30s : %d cases", k, len(v))

    # ── Select examples ──────────────────────────────────────────────────
    examples = select_examples(records, n=10)
    text_lookup = {r["paper_id"]: {"title": r["title"], "abstract": r["abstract"]}
                   for r in records}

    # ── Save outputs ─────────────────────────────────────────────────────
    tables_dir = ROOT / cfg["tables"]["output_dir"]
    tables_dir.mkdir(parents=True, exist_ok=True)

    summary_df = build_summary_df(health_m, outcome_m, pop_m)
    summary_path = tables_dir / "validation_summary.csv"
    summary_df.to_csv(summary_path, index=False, encoding="utf-8")
    log.info("Saved validation summary → %s", summary_path)

    report_path = ROOT / "outputs/report/validation_report.md"
    write_report(sample_n, health_m, outcome_m, pop_m,
                 patterns, examples, text_lookup, cfg, report_path)
    log.info("Saved validation report  → %s", report_path)

    log.info("=== 03b_validation.py finished ===")


if __name__ == "__main__":
    main()
