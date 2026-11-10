# Strategy Lab (`strategy_lab`)

**Foundation:** VectorBT 1.2.0 (free edition; Apache-2.0 + Commons Clause, internal use only)
**Mission:** Test investment hypotheses, entry/exit rules and risk assumptions with reproducible historical evidence.

| | |
|---|---|
| Input contract | BacktestRequest (strategy, instrument, period, data version, rules, sizing, cash, fees, slippage, benchmark) + a Data-Guardian-validated MarketIntelligenceReport |
| Output contract | BacktestResult (STRATEGY_EVIDENCE_REPORT): OK | INSUFFICIENT_DATA | LEAKAGE_DETECTED | FAILED |
| Tools | `run_strategy_backtest`, `run_fee_sensitivity`, `run_slippage_sensitivity`, `test_parameter_robustness`, `run_walk_forward_validation` |
| Schemas | `marcus_v2/core/contracts.py` |
| Code | `agent.py` |
| Tests | `tests/marcus_v2/unit/` |
| LLM | none |

## Invocation policy
- new strategy proposal
- significant entry/exit change
- material allocation proposal
- Quant requests validation
- scheduled strategy review (never per price alert)

## Must not
- trade
- deploy strategies
- approve risk controls
- use future information
- claim future profitability

## Failure handling
- unvalidated data -> DataNotValidated raised
- < 120 bars or < 3 trades -> INSUFFICIENT_DATA
- signal changes when later data added -> LEAKAGE_DETECTED
- fragile parameters -> concern recorded
