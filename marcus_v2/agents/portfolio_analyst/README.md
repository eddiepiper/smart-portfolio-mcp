# Portfolio Analyst (`portfolio_analyst`)

**Foundation:** QuantStats 0.0.86
**Mission:** Accurate portfolio performance, risk and exposure, independent of Marcus's narrative.

| | |
|---|---|
| Input contract | ledger (read-only), validated price observations, optional FX series, benchmark observations, period |
| Output contract | PortfolioAnalysisReport (PORTFOLIO_ANALYSIS_REPORT) |
| Tools | `generate_performance_report`, `reconcile_cash_flows`, `calculate_portfolio_equity`, `detect_performance_anomalies` |
| Schemas | `marcus_v2/core/contracts.py` |
| Code | `agent.py` |
| Tests | `tests/marcus_v2/unit/` |
| LLM | none |

## Invocation policy
- weekly review
- monthly report
- material portfolio change
- significant drawdown
- Marcus request
- ledger reconciliation exception

## Must not
- write to the ledger
- modify history
- approve allocations
- manufacture returns

## Failure handling
- holding without validated price -> left unvalued, equity not reported, exception listed
- no FX for other-currency amounts -> excluded with a warning
- < 20 daily returns -> no volatility/Sharpe claims
- ledger hash break -> reported as reconciliation exception
