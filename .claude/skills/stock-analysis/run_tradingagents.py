"""Run TradingAgents on one ticker and save a short summary plus the full reports.

Usage (with the TradingAgents virtualenv's python):
    python run_tradingagents.py TICKER [YYYY-MM-DD] [--analysts market,news,fundamentals,social]
                                [--portfolio trader/portfolio.json] [--out trader/ta]

The LLM provider and models come from TRADINGAGENTS_* environment variables
(TRADINGAGENTS_LLM_PROVIDER, TRADINGAGENTS_QUICK_THINK_LLM, TRADINGAGENTS_DEEP_THINK_LLM)
plus that provider's API key variable. Defaults here: anthropic, claude-opus-5-5 for
every agent (set TRADINGAGENTS_QUICK_THINK_LLM=claude-sonnet-5 for cheaper analysts).
"""
import argparse, datetime, json, os, sys

DEFAULTS = {
    "TRADINGAGENTS_LLM_PROVIDER": "anthropic",
    "TRADINGAGENTS_QUICK_THINK_LLM": "claude-opus-5-5",
    "TRADINGAGENTS_DEEP_THINK_LLM": "claude-opus-5-5",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS": "1",
    "TRADINGAGENTS_MAX_RISK_ROUNDS": "1",
}
for k, v in DEFAULTS.items():
    os.environ.setdefault(k, v)

# Send the user's own Anthropic key to Anthropic's public API, never to a base URL
# inherited from the surrounding session (e.g. a Claude Code runtime endpoint).
if os.environ.get("TRADINGAGENTS_LLM_PROVIDER", "").lower() == "anthropic":
    os.environ.pop("ANTHROPIC_BASE_URL", None)
    # Cloud sessions may withhold ANTHROPIC_API_KEY (the session's own Claude connection uses that
    # name). The owner can store the key as MARCUS_ANTHROPIC_API_KEY instead; it is mapped in-process.
    if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("MARCUS_ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = os.environ["MARCUS_ANTHROPIC_API_KEY"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set: add your own key from console.anthropic.com "
                 "(see the stock-analysis skill), or pick another provider.")

from tradingagents.default_config import build_default_config  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

REPORT_KEYS = ["market_report", "sentiment_report", "news_report", "fundamentals_report",
               "investment_plan", "trader_investment_plan", "final_trade_decision"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ticker")
    p.add_argument("date", nargs="?", default=datetime.date.today().isoformat())
    p.add_argument("--analysts", default="market,social,news,fundamentals")
    p.add_argument("--portfolio")
    p.add_argument("--out", default="trader/ta")
    a = p.parse_args()

    config = build_default_config()
    analysts = [x.strip() for x in a.analysts.split(",") if x.strip()]
    ta = TradingAgentsGraph(selected_analysts=analysts, debug=False, config=config)
    portfolio = None
    if a.portfolio and os.path.exists(a.portfolio):
        with open(a.portfolio) as f:
            portfolio = json.load(f)
    state, signal = ta.propagate(a.ticker.upper(), a.date, portfolio=portfolio) if portfolio else \
        ta.propagate(a.ticker.upper(), a.date)

    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, "%s-%s" % (a.ticker.upper(), a.date))
    with open(base + ".md", "w") as f:
        f.write("# TradingAgents: %s on %s\n\nRating: **%s**\n\n" % (a.ticker.upper(), a.date, signal))
        for k in REPORT_KEYS:
            v = state.get(k) if isinstance(state, dict) else None
            if v:
                f.write("## %s\n\n%s\n\n" % (k.replace("_", " ").title(), v if isinstance(v, str) else json.dumps(v, indent=2, default=str)))
    print("RATING:", signal)
    print("REPORT:", base + ".md")
    fd = state.get("final_trade_decision") if isinstance(state, dict) else None
    if fd:
        print("\nFINAL DECISION (excerpt):\n" + str(fd)[:2500])


if __name__ == "__main__":
    sys.exit(main())
