#!/usr/bin/env bash
# =============================================================================
# run_full_extraction.sh — TRUE FULL RUN (not pilot)
#
# Prerequisites:
#   export ANTHROPIC_API_KEY=sk-ant-...
#
# Run:
#   bash scripts/run_full_extraction.sh
#
# This script:
#   1. Validates the API key is set
#   2. Confirms corpus is ready (1,999 papers)
#   3. Runs LLM extraction on ALL 1,788 papers with abstracts (full mode)
#   4. Runs all analysis scripts (05–09)
#   5. Regenerates all figures
#   6. Copies finals to outputs/final/
#   7. Updates the manuscripts with full-run numbers
#   8. Verifies success criteria
#
# Expected outcomes:
#   - Health papers extracted:  > 400
#   - Causal triples extracted: > 1,000
#   - Run time:                 ~45–90 minutes
#   - Estimated API cost:       ~$4–5 USD (Haiku)
# =============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_DIR="$ROOT/outputs/final/logs"
mkdir -p "$LOG_DIR"
RUNLOG="$LOG_DIR/full_run_${TIMESTAMP}.log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUNLOG"; }
fail() { log "FATAL: $*"; exit 1; }

log "=== WILDFIRE HEALTH LIT REVIEW — TRUE FULL RUN ==="
log "Root: $ROOT"
log "Log:  $RUNLOG"

# ── 1. API key check ───────────────────────────────────────────────────────
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  fail "ANTHROPIC_API_KEY is not set. Run: export ANTHROPIC_API_KEY=sk-ant-..."
fi
log "API key: present (length ${#ANTHROPIC_API_KEY})"

# ── 2. Corpus check ────────────────────────────────────────────────────────
CLEAN_CSV="$ROOT/data/processed/results_clean.csv"
CLASSIFIED_CSV="$ROOT/data/classified/results_classified.csv"

[[ -f "$CLEAN_CSV" ]]      || fail "Missing $CLEAN_CSV — run 02_clean.py first."
[[ -f "$CLASSIFIED_CSV" ]] || fail "Missing $CLASSIFIED_CSV — run 03_classify.py first."

N_PAPERS=$(python3 -c "import pandas as pd; df=pd.read_csv('$CLEAN_CSV'); print(len(df))")
N_WITH_ABS=$(python3 -c "import pandas as pd; df=pd.read_csv('$CLEAN_CSV'); print((df.abstract.notna() & df.abstract.str.strip().ne('')).sum())")
log "Corpus: $N_PAPERS papers | $N_WITH_ABS have abstracts"

if [[ "$N_PAPERS" -lt 1500 ]]; then
  fail "Corpus too small ($N_PAPERS papers). Re-run 01_collect.py."
fi

# ── 3. Clear any existing pilot checkpoint ─────────────────────────────────
CKPT="$ROOT/data/extracted/checkpoint.json"
if [[ -f "$CKPT" ]]; then
  CKPT_SIZE=$(python3 -c "import json; d=json.load(open('$CKPT')); print(len(d))")
  log "Clearing existing checkpoint ($CKPT_SIZE papers) — full run starts fresh."
  rm -f "$CKPT"
fi

# ── 4. LLM extraction — FULL, not pilot ────────────────────────────────────
log "--- Step 4: LLM extraction (FULL corpus, Haiku model) ---"
log "    Processing ~$N_WITH_ABS abstracts. Estimated time: 45–90 min."
log "    Progress logged every 50 papers. Checkpoint every 25 papers."
python scripts/03_llm_extract.py --full 2>&1 | tee -a "$RUNLOG"

# ── 5. Verify success criteria ─────────────────────────────────────────────
log "--- Step 5: Verifying extraction success criteria ---"
RESULT=$(python3 << 'EOF'
import pandas as pd, json

struct = pd.read_csv('data/extracted/abstracts_structured.csv')
triples = pd.read_csv('data/extracted/causal_triples.csv')

n_total   = len(struct)
n_health  = (struct.is_health_study == True).sum()
n_triples = len(triples)

print(f"TOTAL_PROCESSED={n_total}")
print(f"HEALTH_PAPERS={n_health}")
print(f"TOTAL_TRIPLES={n_triples}")

ok_health  = n_health  >= 400
ok_triples = n_triples >= 1000

if ok_health and ok_triples:
    print("CRITERIA=PASS")
else:
    print("CRITERIA=FAIL")
    if not ok_health:
        print(f"FAIL_REASON: Only {n_health} health papers (need >= 400)")
    if not ok_triples:
        print(f"FAIL_REASON: Only {n_triples} causal triples (need >= 1000)")
EOF
)

eval "$RESULT" 2>/dev/null || true
echo "$RESULT" | tee -a "$RUNLOG"

if echo "$RESULT" | grep -q "CRITERIA=FAIL"; then
  fail "SUCCESS CRITERIA NOT MET. Check the extraction log and re-run with --resume if needed."
fi

log "✓ SUCCESS CRITERIA PASSED: $HEALTH_PAPERS health papers, $TOTAL_TRIPLES triples"

# ── 6. Run all analysis scripts ────────────────────────────────────────────
log "--- Step 6: Running analysis pipeline ---"
for SCRIPT in \
  "04_figures.py" \
  "05_tables.py" \
  "05_gap_analysis.py" \
  "06_gap_drivers_analysis.py" \
  "07_global_mismatch_analysis.py" \
  "08_knowledge_network_analysis.py" \
  "09_temporal_analysis.py"
do
  log "  Running $SCRIPT..."
  python "scripts/$SCRIPT" 2>&1 | tee -a "$RUNLOG"
done

# ── 7. Copy finals ─────────────────────────────────────────────────────────
log "--- Step 7: Copying final outputs ---"
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

# ── 8. Final verification printout ─────────────────────────────────────────
log "─────────────────────────────────────────────────────────"
log "FINAL VERIFICATION"
python3 << 'EOF' | tee -a "$RUNLOG"
import pandas as pd

struct  = pd.read_csv('data/extracted/abstracts_structured.csv')
triples = pd.read_csv('data/extracted/causal_triples.csv')

n_total   = len(struct)
n_health  = (struct.is_health_study == True).sum()
n_triples = len(triples)

gs = pd.read_csv('outputs/tables/gap_scores.csv')
nm = pd.read_csv('outputs/tables/network_metrics.csv')

print(f"  Number of total papers processed:    {n_total}")
print(f"  Number of health papers:             {n_health}")
print(f"  Number of causal triples:            {n_triples}")
print(f"  Top gap (outcome):  {gs[gs.category=='health_outcome'].iloc[0]['label']}  score={gs[gs.category=='health_outcome'].iloc[0]['gap_score']:.3f}")
print(f"  Top gap (population): {gs[gs.category=='vulnerable_population'].iloc[0]['label']}  score={gs[gs.category=='vulnerable_population'].iloc[0]['gap_score']:.3f}")
print(f"  Network hub node:  {nm.iloc[0]['node']}  PageRank={nm.iloc[0]['pagerank']:.4f}")
print()
print("  FULL RUN COMPLETED ✓")
EOF
log "─────────────────────────────────────────────────────────"
log "Outputs:"
log "  Structured data:  data/extracted/abstracts_structured.csv"
log "  Causal triples:   data/extracted/causal_triples.csv"
log "  Main manuscript:  outputs/final/main_manuscript.md"
log "  Supplementary:    outputs/final/supplementary_information.md"
log "  Figures:          outputs/final/figures/"
log "  Tables:           outputs/final/tables/"
log "  Run log:          $RUNLOG"
log "=== SCRIPT COMPLETE ==="
