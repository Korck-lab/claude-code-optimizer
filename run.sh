#!/usr/bin/env bash
#
# cc-optimizer — run the full quota-waste levantamento over a Claude Code
# session corpus. Deterministic, model-token cost = 0 for the analysis itself.
#
# ZERO-CONFIG: automatically detects Claude Code logs at ~/.claude/projects
# Override with: ./run.sh [SESSIONS_DIR]
#
# Produces optimizer/out/report.md + findings.json. The optional LLM refinement
# layer (the cc-optimizer workflow) is a separate, enhancement step — see README.
#
set -euo pipefail
cd "$(dirname "$0")"

SESSIONS="${1:-}"
OUT="optimizer/out"
KB="optimizer/knowledge-base.json"

# Auto-detect ~/.claude/projects if no argument provided
if [ -z "$SESSIONS" ]; then
  CLAUDE_PROJECTS="$HOME/.claude/projects"
  if [ -d "$CLAUDE_PROJECTS" ]; then
    SESSIONS="$CLAUDE_PROJECTS"
    echo "=> Detected Claude Code projects at $SESSIONS"
  else
    SESSIONS="sessions"
  fi
fi

if [ ! -d "$SESSIONS" ]; then
  echo "error: sessions dir '$SESSIONS' not found. Either:"
  echo "       1. Have Claude Code installed with session history (~/.claude/projects exists)"
  echo "       2. Copy your Claude Code logs to ./sessions (one subdir per project, *.jsonl inside)"
  echo "       3. Pass a path: ./run.sh /path/to/sessions"
  exit 1
fi
if [ ! -f "$KB" ]; then
  echo "error: $KB missing. It is built from fresh official docs by the cc-cost-research"
  echo "       workflow. Without it, pricing/knobs are unknown (we never hardcode them)."
  exit 1
fi

mkdir -p "$OUT"
echo "==> 1/5 analyze (deterministic aggregate)"
python3 optimizer/analyze.py   "$SESSIONS" "$OUT/raw-stats.json"
echo "==> 2/5 cost (weight by fresh-docs pricing)"
python3 optimizer/cost.py      "$OUT/raw-stats.json" "$KB" "$OUT/cost-stats.json" 5
echo "==> 3/5 recommend (rules engine -> actionable findings)"
python3 optimizer/recommend.py "$OUT/raw-stats.json" "$KB" "$OUT/findings.json" --floor 30 --top-n 5
echo "==> 4/5 briefs (compact per-project inputs for optional LLM refinement)"
python3 optimizer/briefs.py    "$OUT/raw-stats.json" "$OUT/cost-stats.json" "$OUT/findings.json" "$KB" "$OUT"
echo "==> 5/5 report"
python3 optimizer/report.py    "$OUT/findings.json" "$OUT/raw-stats.json" "$KB" "$OUT/report.md"

echo
echo "Deterministic levantamento ready -> $OUT/report.md"
echo "Optional: run the 'cc-optimizer' workflow for LLM-refined per-project findings,"
echo "then: python3 optimizer/merge.py $OUT/findings.json <refine-result.json> $OUT/findings-final.json"
echo "      python3 optimizer/report.py $OUT/findings-final.json $OUT/raw-stats.json $KB $OUT/report-final.md"
