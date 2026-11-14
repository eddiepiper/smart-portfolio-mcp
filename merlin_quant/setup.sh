#!/usr/bin/env bash
# One-time setup for Merlin + Quant. Idempotent. Reuses the existing stock-analysis
# TradingAgents install (pinned v0.7.1) and adds a pinned copy of Anthropic's
# financial-services plugins for Merlin's headless runs.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
bash "$HERE/.claude/skills/stock-analysis/setup_tradingagents.sh"          # -> ~/.tradingagents-src/venv
"$HOME/.tradingagents-src/venv/bin/pip" install --quiet pytest
FSI="$HOME/.cache/merlin_quant/financial-services"
FSI_COMMIT="3e32c2287dcb637c184c01cc0ad82d6af7e9bb0f"
if [ ! -d "$FSI/.git" ]; then
  mkdir -p "$(dirname "$FSI")"
  git clone --quiet https://github.com/anthropics/financial-services "$FSI"
fi
git -C "$FSI" fetch --quiet --depth 1 origin "$FSI_COMMIT" 2>/dev/null || true
git -C "$FSI" checkout --quiet "$FSI_COMMIT"
echo "financial-services plugins pinned at $FSI_COMMIT in $FSI"
echo "run: ~/.tradingagents-src/venv/bin/python -m merlin_quant --help"
