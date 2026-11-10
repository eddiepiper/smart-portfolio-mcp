# Execution Specialist (`execution_specialist`)

**Foundation:** QuantConnect LEAN fill semantics (daily resolution), re-implemented deterministically; LEAN engine only as isolated backtest cross-check
**Mission:** Decide whether an authorised paper order is eligible, simulate it, and record it accurately.

| | |
|---|---|
| Input contract | PaperOrder (id, instrument, side, qty, type, limit/stop, trigger, planned entry, mandate version, decision time, expiry) + validated bars + approved recommendation + portfolio equity |
| Output contract | PAPER_EXECUTION_REPORT with PaperFill: FILLED | NO_FILL | UNVERIFIABLE | REJECTED | EXPIRED |
| Tools | `simulate_execution`, `check_available_cash`, `estimate_transaction_cost`, `check_order_status`, `reconcile_paper_fill`, `explain_execution_exception` |
| Schemas | `marcus_v2/core/contracts.py` |
| Code | `agent.py` + `engine.py` |
| Tests | `tests/marcus_v2/unit/` |
| LLM | narrative only (explanations / counter-arguments); never decides numbers |

## Invocation policy
- paper-order events
- order lifecycle changes (re-evaluation of NO_FILL/UNVERIFIABLE when new data arrives)

## Must not
- hold or use broker credentials
- route real orders
- run LEAN live
- substitute a convenient price
- fill without owner approval and an active mandate

## Failure handling
- missing/unvalidated bar -> UNVERIFIABLE
- duplicate order id -> REJECTED, no second ledger event
- crash during ledger write -> torn write quarantined on restart, reconcile_paper_fill reports state, order re-evaluable
- insufficient cash / limits -> REJECTED
