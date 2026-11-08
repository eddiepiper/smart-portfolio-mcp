#!/usr/bin/env bash
# Install TradingAgents (TauricResearch/TradingAgents) into a local virtualenv.
# Idempotent: re-running reuses an existing install.
set -euo pipefail
TA_HOME="${TA_HOME:-$HOME/.tradingagents-src}"
TA_COMMIT="9fa1196fffbcfd9de5146d2dd2869df84ab99838"   # v0.7.1, reviewed 2026-11-08
if [ ! -d "$TA_HOME/repo/.git" ]; then
  mkdir -p "$TA_HOME"
  git clone --quiet https://github.com/TauricResearch/TradingAgents "$TA_HOME/repo"
fi
git -C "$TA_HOME/repo" fetch --quiet --depth 1 origin "$TA_COMMIT" 2>/dev/null || true
git -C "$TA_HOME/repo" checkout --quiet "$TA_COMMIT"
if [ ! -x "$TA_HOME/venv/bin/python" ]; then
  python3 -m venv "$TA_HOME/venv"
  "$TA_HOME/venv/bin/pip" install --quiet --upgrade pip
fi
"$TA_HOME/venv/bin/pip" install --quiet "$TA_HOME/repo"
"$TA_HOME/venv/bin/python" -c "import tradingagents; print('TradingAgents', tradingagents.__version__, 'ready at', '$TA_HOME/venv')"
