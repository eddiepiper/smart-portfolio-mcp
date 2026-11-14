# CLAUDE.md

## Marcus V2 (paper investment team)

For portfolio reviews, end-to-end opportunity assessments, price-alert processing, paper
execution or ledger/mandate questions, use the `marcus-v2` skill. Claude is Marcus's runtime
(not Grok). Owner-only commands need the owner's explicit approval in the chat first.

## Merlin + Quant (standalone paper-trading team)

For Merlin/Quant research, challenges, paper proposals, approvals, the Merlin/Quant paper ledger, backtests or
performance, use the `merlin-quant` skill. It is independent of Marcus: never mix their ledgers or mandates.

## Stock questions

Whenever the user asks about a stock, ETF or ticker (should I buy X, analyze X, levels
for X, rate X), use the `stock-analysis` skill: it runs **both** TradingAgents
(TauricResearch multi-agent research) and the day-trader skill pack (`trader-*`), and
reports them side by side. Paper trading by default; research, not advice.

- Day-trader working files live in `trader/` (git-ignored: orders, paper portfolio, alerts).
- The user is in Singapore: give times in SGT as well as New York time.
- The user's broker charges a US$15 minimum commission per US trade; flag it whenever a
  real US trade is small.
