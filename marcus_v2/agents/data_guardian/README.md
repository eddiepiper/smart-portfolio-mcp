# Data Guardian (`data_guardian`)

**Foundation:** Pandera 0.36.0 (DataFrameSchema) + deterministic business rules
**Mission:** Protect the investment workflow from bad market data.

| | |
|---|---|
| Input contract | MarketIntelligenceReport or observations, purpose (research|decision|execution), optional reference price, previously seen event ids |
| Output contract | DataQualityReport (DATA_QUALITY_REPORT): VALID | WARNING | REJECTED with every issue, severity and evidence |
| Tools | `validate_report`, `validate_observations`, `compare_provider_observations`, `generate_data_quality_report`, `explain_exceptions` |
| Schemas | `marcus_v2/core/contracts.py` |
| Code | `agent.py` |
| Tests | `tests/marcus_v2/unit/` |
| LLM | narrative only (explanations / counter-arguments); never decides numbers |

## Invocation policy
- every incoming market-data batch (deterministic)
- LLM only to explain exceptions

## Must not
- override a deterministic rejection
- repair prices
- alter investment decisions
- modify the source record

## Failure handling
- empty input -> REJECTED (critical)
- schema failure -> REJECTED
- provisional bar used for a decision -> excluded with a warning
- label says official close but bar is provisional -> REJECTED
- stale for decision/execution -> REJECTED; for research -> WARNING
