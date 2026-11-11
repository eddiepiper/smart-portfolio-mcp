# Claude cloud environment setup script for smart-portfolio-mcp (runs from the folder holding the checkout).
# Allowed domains: query1.finance.yahoo.com query2.finance.yahoo.com feeds.finance.yahoo.com fc.yahoo.com
#   finance.yahoo.com data.sec.gov www.sec.gov www.nasdaqtrader.com api.stocktwits.com www.reddit.com
if [ -f smart-portfolio-mcp/requirements-marcus-v2.txt ]; then
  (cd smart-portfolio-mcp && python3 -m venv .venv-marcus-v2 && .venv-marcus-v2/bin/pip install -q --upgrade pip && .venv-marcus-v2/bin/pip install -q -r requirements-marcus-v2.txt)
fi
if [ -f smart-portfolio-mcp/.claude/skills/stock-analysis/setup_tradingagents.sh ]; then
  bash smart-portfolio-mcp/.claude/skills/stock-analysis/setup_tradingagents.sh
fi
