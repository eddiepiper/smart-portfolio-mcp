---
name: marcus-v2
description: >-
  Marcus V2 investment team (paper only, S$20,000 mandate). Runs the six
  specialists — Market Intelligence, Data Guardian, Strategy Lab, Research
  Challenger, Execution Specialist, Portfolio Analyst — through Marcus's
  workflows: daily monitoring of Price Monitor events, new-opportunity
  analysis ending in a recommendation for owner approval, portfolio review,
  approved paper execution, telemetry. Use when the user asks Marcus to
  review the portfolio, assess an opportunity end to end, process price
  alerts, check paper performance, approve a recommendation, or asks about
  the paper ledger, mandate or agent costs.
argument-hint: "[daily_monitoring | new_opportunity | portfolio_review | status | telemetry]"
---

# marcus-v2

Claude is the runtime for Marcus V2 (owner decision 2026-11-10; Grok is not used).
Every number comes from the specialists' deterministic tools; you coordinate and explain.
Paper only. Real-money trading does not exist in this system.

Run from the repository root with the Marcus V2 virtualenv:

```bash
M="python -m marcus_v2 --home ${MARCUS_V2_HOME:-var/marcus_v2}"
$M status                                          # ledger integrity, TradingAgents availability, telemetry
$M manifest                                        # tool definitions (Claude tool-use shape)
$M daily_monitoring  --payload '{"events": [...], "watchlist": ["US:BAC"]}'
$M new_opportunity   --payload '{"instrument_id": "US:PFE", "as_of": "YYYY-MM-DD", "plan": {"limit": "...", "stop": "...", "target": "..."}}'
$M portfolio_review  --payload '{"instruments": ["US:BAC"], "base_currency": "SGD"}'
$M telemetry
```

Owner-only (run ONLY after the owner types an explicit approval in the chat, quoting the item):

```bash
$M approve          --owner owner:<name> --payload '{"recommendation_id": "rec_..."}'
$M mandate_approve  --owner owner:<name> --payload '{"version": 1}'
$M import_legacy    --owner owner:<name> --payload '{"trader_dir": "trader", "capital_currency": "SGD"}'
$M apply_topup      --owner owner:<name> --payload '{"target": "20000", "currency": "SGD", "stated_original": ["500", "SGD"]}'
```

## How to report

- Lead with Marcus's recommendation (BUY / WAIT / HOLD / REDUCE / EXIT), its status
  (proposed / blocked) and whether owner approval is needed.
- Then each specialist's finding in one line, and **every disagreement** (Research Challenger,
  Strategy Lab, Quant vs TradingAgents) as listed — never merge them into a consensus.
- State limitations exactly as the tools report them (failed feeds, rejected data, MOCKED or
  NOT VERIFIED components). Never fill a gap with your own numbers.
- Times in SGT and New York time. Mention the US$15 minimum commission for small US trades.

## Never

- Never pass `--owner` on your own initiative or after an ambiguous reply.
- Never edit files under `$MARCUS_V2_HOME` (ledger, mandates, evidence) by hand.
- Never set broker credentials; the runtime refuses to start if any are present.
