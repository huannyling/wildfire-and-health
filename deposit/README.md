# Data supporting Persistent gaps in wildfire-health evidence for vulnerable populations

## Overview

This deposit contains the minimal derived data required to reproduce the descriptive analyses, gap scores, research-to-fire-activity mismatch analyses, and bilingual human-validation summary reported in the associated manuscript. It deliberately excludes the full OpenAlex retrieval, article titles and abstracts, LLM prompt payloads, API credentials, and intermediate screening files.

## Files

`data/health_study_structured_labels.csv`
: Study-level, structured labels for the 525 LLM-confirmed wildfire-health studies. The file includes persistent study identifiers, DOI where available, publication year, language metadata, screening route, and controlled labels used in the analyses. It excludes article titles, abstracts, quotations, author-provided key findings, and other text-derived fields.

`data/corpus_language_summary.csv`
: Counts of the 3,916 structured records by OpenAlex metadata language, plus counts of the 525 LLM-confirmed health studies.

`data/rrmi_country.csv`
: Country-level fire-activity proxy and research-allocation values used to calculate the exploratory Research-to-Fire-Activity Mismatch Index (RRMI).

`data/ST17_rrmi_attribution_rule_country_results.csv`
: Country-level RRMI values under alternative research-attribution rules.

`data/ST18_rrmi_attribution_rule_robustness.csv`
: Rank concordance and deficit-overlap summaries across attribution rules.

`data/ST19_rrmi_attribution_rule_diagnostics.csv`
: Attribution diagnostics across rules.

`data/ST20_rrmi_attribution_rule_regions.csv`
: Regional RRMI summaries across attribution rules.

`data/ST21_bilingual_human_validation.csv`
: Aggregate dual-human and LLM-human agreement metrics for English- and Chinese-language validation records. Individual human annotations are not included.

`DRYAD_METADATA_DRAFT.md`
: Draft dataset title, description, keywords, methods and required metadata fields for completion in the Dryad submission form.

`CHECKSUMS.sha256`
: SHA-256 checksums for integrity verification.

## Methods summary

Records were retrieved from the OpenAlex Works API for 2000–2024 using wildland-fire queries in English, Chinese, Russian, Spanish, Portuguese, French and Indonesian. Candidate records underwent multilingual dictionary screening, fire-context screening, and structured LLM extraction. Analyses reported in the associated manuscript use 525 studies classified as genuine human-health studies. The RRMI is an exploratory comparison of each country's share of attributed research with its share of a composite fire-activity proxy. It is not a measure of population exposure, vulnerability, attributable disease burden or health risk.

## Reuse notes

The data are distributed for reproducibility of the associated study. The structured labels are derived from bibliographic metadata and title/abstract screening; they are not clinical data and contain no personal information. Users should cite both this dataset and the associated article. See the associated manuscript for definitions, validation limits, and interpretation boundaries.

## Software

The analysis workflow is available from the associated project repository: https://github.com/huannyling/wildfire-and-health. The deposit intentionally does not contain API keys or other credentials.
