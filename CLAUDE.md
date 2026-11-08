# CLAUDE.md

## Stock questions

Whenever the user asks about a stock, ETF or ticker (should I buy X, analyze X, levels
for X, rate X), use the `stock-analysis` skill: it runs **both** TradingAgents
(TauricResearch multi-agent research) and the day-trader skill pack (`trader-*`), and
reports them side by side. Paper trading by default; research, not advice.

- Day-trader working files live in `trader/` (git-ignored: orders, paper portfolio, alerts).
- The user is in Singapore: give times in SGT as well as New York time.
- The user's broker charges a US$15 minimum commission per US trade; flag it whenever a
  real US trade is small.
