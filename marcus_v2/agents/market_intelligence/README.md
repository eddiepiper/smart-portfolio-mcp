# Market Intelligence (`market_intelligence`)

**Foundation:** OpenBB Platform (openbb-core 2.0.1 + openbb-yfinance 2.1.0)
**Mission:** Provide reliable, source-attributed market information to TradingAgents, Marcus and authorised specialists.

| | |
|---|---|
| Input contract | instrument_id, period (start/end), interval, category, requesting_agent, correlation_id, max_age_s |
| Output contract | MarketIntelligenceReport (MARKET_INTELLIGENCE_REPORT) |
| Tools | `get_instrument_metadata`, `get_historical_prices`, `get_market_snapshot`, `get_market_context`, `compare_data_providers`, `check_data_availability`, `get_macro_indicators` |
| Schemas | `marcus_v2/core/contracts.py` |
| Code | `agent.py` |
| Tests | `tests/marcus_v2/unit/` |
| LLM | none |

## Invocation policy
- scheduled refresh
- Marcus requests context
- TradingAgents needs inputs
- new approved instrument
- material market event
- provider discrepancy

## Must not
- recommend investments
- execute trades
- change allocations
- fabricate provider data
- enrol in paid providers

## Failure handling
- provider error -> status FAILED with the error in missing_information; no bars invented
- missing close -> status PARTIAL
- no currency from provider -> registry value used and recorded as a limitation
- stale cache -> re-fetched once max_age_s passes
