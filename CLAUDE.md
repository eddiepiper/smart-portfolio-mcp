# CLAUDE.md

## Marcus V2 (paper investment team)

For portfolio reviews, end-to-end opportunity assessments, price-alert processing, paper
execution or ledger/mandate questions, use the `marcus-v2` skill. Claude is Marcus's runtime
(not Grok). Owner-only commands need the owner's explicit approval in the chat first.

## Anthropic finance agents (Finance Bridge)

For earnings reviews, post-earnings notes or market/sector research notes, use the `marcus-fsi`
skill. Marcus builds a free cited data pack, then the vendored `earnings-reviewer` or
`market-researcher` agent writes the note, and Marcus verifies every figure against SEC filings.
Research only: no rating, no action.

## Merlin (financial research and modelling)

When the user addresses Merlin ("Merlin, ...") or asks for company or sector research, an
earnings review, a fundamentals comparison, a DCF/comps/ratio analysis, a valuation check or a
macro investigation, use the `merlin` skill (spec: `merlin/spec/MERLIN.md`). Every number comes
from a cited source or `python -m merlin calc`; every report passes `python -m merlin validate`.
Research only: Merlin never trades, rates or sizes positions; Marcus decides and Quant challenges.
"Should I buy X" stays with `stock-analysis` (TradingAgents is the primary engine). Direct research
requests go to Merlin; `marcus-fsi` stays for Marcus's own pipeline (SEC data pack + verification).

## Stock questions

Whenever the user asks about a stock, ETF or ticker (should I buy X, analyze X, levels
for X, rate X), use the `stock-analysis` skill: it runs **both** TradingAgents
(TauricResearch multi-agent research) and the day-trader skill pack (`trader-*`), and
reports them side by side. Paper trading by default; research, not advice.

- Day-trader working files live in `trader/` (git-ignored: orders, paper portfolio, alerts).
- The user is in Singapore: give times in SGT as well as New York time.
- The user's broker charges a US$15 minimum commission per US trade; flag it whenever a
  real US trade is small.
