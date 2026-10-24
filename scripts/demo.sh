#!/usr/bin/env bash
# Offline demo: run the eval cases against the recorded model transcript (no API key, no network).
# Needs LibreOffice (`soffice`): every workbook is recalculated and verified before it is delivered.
#   bash scripts/demo.sh [output dir for the generated workbooks, default: demo-output]
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-demo-output}"
mkdir -p "$OUT"
export SPREAD_REPLAY_TRANSCRIPT=evals/transcripts/spread.jsonl
export COGNITIA_OPENROUTER_API_KEY=replay-no-key   # placeholder, not a secret
export SPREAD_MODEL=recorded/scripted
export SPREAD_CHECKPOINT_DB="$OUT/checkpoints.sqlite"
export SPREAD_OUTPUT_DIR="$OUT/workbooks"
export SPREAD_MAX_TOKENS_PER_RUN=200000
PYTHONPATH=. python evals/run_evals.py --threshold 0.9
echo "Workbooks written to $SPREAD_OUTPUT_DIR"
