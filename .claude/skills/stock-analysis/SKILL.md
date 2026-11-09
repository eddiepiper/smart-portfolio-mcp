---
name: stock-analysis
description: >-
  Stock Analysis. Runs BOTH systems on any stock question: TradingAgents
  (TauricResearch multi-agent LLM research: market, sentiment, news and
  fundamentals analysts, bull/bear debate, trader, risk team, portfolio
  manager rating Buy/Overweight/Hold/Underweight/Sell) AND the day-trader
  skill pack (price and trend levels, headlines, entry trigger, stop and
  target, sizing and risk check), then compares them side by side. Use
  whenever the user asks about a stock or ETF: "should I buy X", "what do
  you think of X", "analyze X", "is X a buy", "levels for X", "rate X",
  "run both agents on X", or names a ticker and asks what to do.
argument-hint: "TICKER [YYYY-MM-DD]"
---

# stock-analysis

One question about a stock gets two independent views, and the user sees
where they agree and where they don't. Neither view is advice; both are
research. Paper first.

`${CLAUDE_SKILL_DIR}` is this skill's folder. The day-trader tools live in
`.claude/skills/trader-*/`. Working files go in `trader/` (git-ignored).

## 1. TradingAgents (the slow, deep view)

Setup, once per machine (idempotent, pinned to the reviewed v0.7.1 commit):

```bash
bash "${CLAUDE_SKILL_DIR}/setup_tradingagents.sh"     # -> ~/.tradingagents-src/venv
```

Run (takes several minutes; start it in the background, then do step 2):

```bash
~/.tradingagents-src/venv/bin/python "${CLAUDE_SKILL_DIR}/run_tradingagents.py" TICKER [DATE] \
    --portfolio trader/portfolio.json --out trader/ta
```

- Provider and models come from env vars. Defaults: `TRADINGAGENTS_LLM_PROVIDER=anthropic`,
  `claude-opus-5-5` for every agent (set `TRADINGAGENTS_QUICK_THINK_LLM=claude-sonnet-5` for cheaper analysts). Any provider TradingAgents
  supports works (openai, google, openrouter, ...) if its key variable is set
  (e.g. `OPENROUTER_API_KEY`); a key saved as a network secret for that provider's host
  is injected by the proxy, so pass a dummy value in the variable.
- DATE defaults to today; use the last completed US session's date (New York time).
- Output: the rating line, and the full report in `trader/ta/TICKER-DATE.md`.
- If it cannot run (no LLM key, host blocked), say exactly that in one line and continue
  with step 2. Never invent a TradingAgents rating.
- Data it needs: query1/query2.finance.yahoo.com, fc.yahoo.com and finance.yahoo.com
  (Yahoo login for fundamentals), data.sec.gov and www.sec.gov (filings), api.stocktwits.com
  and www.reddit.com (sentiment). A blocked source degrades that analyst's report: say which
  feeds failed, as the rating then rests on less evidence.

## 2. Day-trader view (fast, levels)

```bash
python3 .claude/skills/trader-alerts/alerts.py quote TICKER --write
python3 .claude/skills/trader-news/news.py --tickers TICKER --hours 48 --no-sec --dry-run
python3 .claude/skills/trader-scanner/scan.py daily --symbols TICKER --no-write
```

Plus, from Yahoo daily bars (1y): close, 20/50/200-day averages and their slope, RSI(14),
ATR(14), 10-day low, 20- and 60-day highs. From those, write the plan the user has been
using:

- Trend: above/below each average; uptrend = price > MA50 > MA200 with MA50 rising.
- Entry: buy only after a **daily close above** a trigger (reclaim of a broken level or
  average), then a next-session **limit** a few cents above; or a pullback limit to support
  in an uptrend.
- Stop: **sell after a daily close below** a level under the nearest support.
- Target: the next resistance (recent high, MA50/MA200), at least **1.5x** the risk.
- Size: whole shares so the loss at the stop stays within the user's max loss per trade
  (`trader/config.json`, $10 on the $500 paper account).
- Optional: `/trader-timemachine` for the odds once a plan exists.

## 3. Report: one compact answer

| | TradingAgents | Day-trader view |
|---|---|---|
| Verdict | rating | buy now / wait for trigger / avoid |
| Why | 2-3 points from the reports | trend, RSI, key levels |

Then: **where they agree / disagree** (state disagreements, don't average them), the
**levels** (trigger, limit, stop, target, shares, $ risk and reward), and costs:
the user's broker (OCBC) charges a US$15 minimum per US trade, so small US trades lose
money even when right: say so and suggest paper, an SGX listing, or a low-fee broker.

## Rules

- Research, not financial advice. Never place a real order. Paper trades are recorded only
  after the user's explicit yes (`trader-broker/ticket.py fill`).
- Say which data was missing or stale. Never fill a gap with a made-up number.
- Quote times in Singapore time as well as New York time (the user is in Singapore).
