#!/usr/bin/env bash
# =============================================================================
# 10_full_run.sh — Full pipeline runner
#
# Usage:
#   export ANTHROPIC_API_KEY=sk-...
#   bash scripts/10_full_run.sh
#
# Steps:
#   1. Collect full corpus from OpenAlex (no API key required)
#   2. Clean and deduplicate
#   3. Keyword classify
#   4. LLM extract (requires ANTHROPIC_API_KEY)
#   5–9. Run all analysis + figure scripts
#   10. Copy finals to outputs/final/
#
# Checkpointing: LLM extraction checkpoints every 25 papers.
# Resume: re-running this script will skip already-extracted papers.
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/outputs/final/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RUNLOG="$LOG_DIR/full_run_${TIMESTAMP}.log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUNLOG"; }

log "=== WILDFIRE HEALTH LIT REVIEW — FULL PIPELINE RUN ==="
log "Root: $ROOT"
log "Log: $RUNLOG"

# Check API key
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  log "ERROR: ANTHROPIC_API_KEY is not set. Export it before running:"
  log "  export ANTHROPIC_API_KEY=sk-..."
  exit 1
fi
log "API key: present (length ${#ANTHROPIC_API_KEY})"

cd "$ROOT"

# Step 1: Collect
log "--- Step 1: Collecting full corpus from OpenAlex ---"
python scripts/01_collect.py 2>&1 | tee -a "$RUNLOG"

# Step 2: Clean
log "--- Step 2: Cleaning and deduplicating ---"
python scripts/02_clean.py 2>&1 | tee -a "$RUNLOG"

# Step 3: Classify
log "--- Step 3: Keyword classification ---"
python scripts/03_classify.py 2>&1 | tee -a "$RUNLOG"

# Step 4: LLM extraction (full run, with checkpointing)
log "--- Step 4: LLM extraction (full corpus, Haiku model) ---"
log "    Checkpoint file: data/extracted/checkpoint.json"
log "    Remove checkpoint file to force fresh extraction."
python scripts/03_llm_extract.py 2>&1 | tee -a "$RUNLOG"

# Step 5-9: Analysis and figures
for SCRIPT in \
  "04_figures.py" \
  "05_tables.py" \
  "05_gap_analysis.py" \
  "06_gap_drivers_analysis.py" \
  "07_global_mismatch_analysis.py" \
  "08_knowledge_network_analysis.py" \
  "09_temporal_analysis.py"
do
  log "--- Running $SCRIPT ---"
  python "scripts/$SCRIPT" 2>&1 | tee -a "$RUNLOG"
done

# Step 10: Copy finals
log "--- Step 10: Copying final outputs ---"
mkdir -p outputs/final/figures outputs/final/tables outputs/final/logs

cp outputs/figures/gap_ranking.png            outputs/final/figures/Fig1_gap_analysis.png
cp outputs/figures/global_mismatch_map.png    outputs/final/figures/Fig2_global_mismatch.png
cp outputs/figures/network_graph.png          outputs/final/figures/Fig3_knowledge_network.png
cp outputs/figures/network_graph.pdf          outputs/final/figures/Fig3_knowledge_network.pdf
cp outputs/figures/topic_trends.png           outputs/final/figures/Fig4_temporal_evolution.png
cp outputs/figures/gap_drivers_comparison.png outputs/final/figures/EDFig5_gap_drivers.png
cp outputs/figures/weak_links.png             outputs/final/figures/EDFig6_pathway_coverage.png
cp outputs/figures/01_publications_per_year.png outputs/final/figures/EDFig1_publications_per_year.png
cp outputs/figures/02_theme_distribution.png  outputs/final/figures/EDFig2_theme_distribution.png
cp outputs/figures/03_top_journals.png        outputs/final/figures/EDFig3_top_journals.png
cp outputs/figures/04_top_countries.png       outputs/final/figures/EDFig4_top_countries.png

cp outputs/tables/gap_scores.csv             outputs/final/tables/ST1_gap_scores.csv
cp outputs/tables/global_mismatch.csv        outputs/final/tables/ST2_global_mismatch.csv
cp outputs/tables/network_metrics.csv        outputs/final/tables/ST3_network_metrics.csv
cp outputs/tables/validation_summary.csv     outputs/final/tables/ST4_validation_summary.csv
cp outputs/tables/year_by_theme.csv          outputs/final/tables/ST5_year_by_theme.csv
cp outputs/tables/top_journals.csv           outputs/final/tables/ST6_top_journals.csv
cp outputs/tables/gap_drivers_summary.csv    outputs/final/tables/ST7_gap_drivers.csv
cp outputs/tables/country_counts.csv         outputs/final/tables/ST8_country_counts.csv
cp outputs/tables/top_neglected_topics.csv   outputs/final/tables/ST9_top_neglected_topics.csv

cp "$RUNLOG" outputs/final/logs/

log "=== FULL PIPELINE COMPLETE ==="
log "Outputs:"
log "  Main manuscript:       outputs/final/main_manuscript.md"
log "  Supplementary info:    outputs/final/supplementary_information.md"
log "  Figures:               outputs/final/figures/"
log "  Tables:                outputs/final/tables/"
log "  Run log:               $RUNLOG"
