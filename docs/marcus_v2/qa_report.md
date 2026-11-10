# Marcus V2 — QA, security, cost and consolidated status report

Branch `feature/marcus-v2-six-specialists`. Evidence: `docs/marcus_v2/test_evidence.txt`
(89 passed, live providers included). This report is the engineering self-assessment; **independent
QA by Dex is still required** before any deployment decision.

## 1. Consolidated status

| Agent / component | Status | Verified against | Not verified / blocked |
|---|---|---|---|
| Market Intelligence (OpenBB) | **WORKING** | live OpenBB yfinance provider + Yahoo cross-check (VALID agreement, 30 days BAC) | macro indicators (no FRED key/host); paid OpenBB providers not used |
| Data Guardian (Pandera) | **WORKING** | real 13-month bars (BAC, PFE, SPY, GC=F); 13 rule tests | — |
| Strategy Lab (VectorBT) | **WORKING** | real SPY/BAC history; reproducibility, costs, leakage fixture | Quant's methodology review of its outputs is MOCKED |
| Research Challenger (AIHF patterns) | **WORKING (deterministic)**, LLM path **MOCKED** | real BAC context (it correctly contradicts an "uptrend" thesis) | live LLM counter-arguments: no API key |
| Execution Specialist (LEAN semantics) | **WORKING (deterministic engine)** | 12 scenario tests on real bars incl. the gold 3994/4005 case | LEAN engine Docker cross-check **BLOCKED** (image exceeds build disk allowance) |
| Portfolio Analyst (QuantStats) | **WORKING** | real legacy account; hand-computed TWR matches | FX-converted SGD reporting needs an FX series (not yet wired) |
| Marcus orchestrator (Workflows A–D) | **WORKING** with mocked TradingAgents/Quant | 6 end-to-end tests; live CLI demo | — |
| TradingAgents adapter | **PARTIAL** | output parsing; outage handling | live run **NOT VERIFIED**: no LLM key in this environment |
| Quant adapter | **CONTRACT ONLY** | file transport round trip | real Quant **NOT ACCESSIBLE** |
| Price Monitor adapter | **CONTRACT ONLY** | event + trader-alerts line parsing, de-duplication | real Price Monitor **NOT ACCESSIBLE** |
| Grok integration | **CONTRACT ONLY** | JSON CLI + tool manifest | Grok runtime **NOT ACCESSIBLE** |
| S$20,000 capital | **BLOCKED (by design)** | legacy import reproduces cash 296.40 + 4 BAC | currency discrepancy (US$500 recorded vs S$500 stated) needs owner decision |

## 2. Acceptance tests mapped to the directive

| Directive test | Test |
|---|---|
| MI: history, timestamps/units, missing access, delayed data, cache, inconsistency, DG before decisions | `test_mi_dg.py::test_history_units_timestamps_and_cache`, `test_missing_provider_access…`, `test_provider_discrepancy`, `test_strategy_lab.py::test_unvalidated_data_refused`, `test_workflows.py::test_unvalidated_market_data_cannot_reach_execution` |
| DG: provisional gold ≠ daily close; duplicates; units; stale; missing ≠ invented | `test_provisional_gold_cannot_masquerade…`, `test_duplicates_dropped…`, `test_wrong_units_and_currency_rejected`, `test_stale_prices_block_decisions`, `test_missing_data_never_becomes_data` |
| SL: reproduce, costs, leakage, benchmark, sensitivity, INSUFFICIENT DATA | `test_strategy_lab.py` (6 tests) |
| RC: unsupported claims, evidence-based counters, missing info, no manufactured consensus, no repeat calls | `test_research_challenger.py` (6 tests) |
| EX: gold 3994/4005, insufficient cash, duplicate ids, gap through stop, expiry, inconsistent timestamps, unavailable price, interrupted write, live access, reconciliation | `test_execution.py` (12 tests), `security/test_paper_only.py` |
| PA: contribution ≠ return, equity reconciles, fees, benchmark alignment, reproducible, missing flagged | `test_portfolio_analyst.py` (8 tests) |
| Regression vs existing workflow | `integration/test_shadow_legacy.py` (V2 reproduces the legacy BAC fill: 2026-11-05 @ 50.90; difference: V2 charges the USD 15 minimum the legacy record omitted) |

Notable real-data findings during QA:
- **Gold 3994/4005:** GC=F closed 4,073 on 2026-11-04 (trigger met) but the lowest price afterwards
  was 4,035.10 (Nov 6): a 4,005 limit buy **never filled**. Any record of a 4,005 paper buy in that
  window would be unsupported by the data.
- **BAC Monday close:** OpenBB 50.55 vs an earlier intraday read of 50.58 — provisional vs final;
  Data Guardian's price-type re-derivation handles this.

## 3. Security / paper-execution safety assessment

| Control | Enforcement | Test |
|---|---|---|
| No live trading path | `assert_no_live_trading` on order payloads and LEAN commands; LEAN adapter allows only `backtest`, `--network none` | `test_live_paths_refused`, `test_lean_adapter_cannot_build_live_command` |
| No broker SDKs | AST scan of the whole package for broker/wallet modules | `test_no_broker_sdk_imported_anywhere` |
| No broker credentials | runtime refuses to start if broker env vars exist | `test_runtime_refuses_to_start_with_broker_credentials` |
| Paper flags immutable | `Literal[True]` on `PaperOrder.paper`, `InvestmentMandate.paper_only` | `test_paper_flags_cannot_be_disabled` |
| Single ledger writer | permission policy: only `execution_specialist` has `append_ledger`; analyst gets a reader without write methods | `test_only_execution_specialist_can_write_ledger`, `test_analyst_is_read_only` |
| Owner approvals | mandates, capital, recommendations require `owner:` approver; agents refused | `test_mandate_versioning…`, `test_import_requires_owner`, e2e approval test, CLI test |
| Tamper evidence | SHA-256 hash chain, sequence checks, quarantine of torn writes | `test_ledger_tamper_detected`, `test_interrupted_write…` |
| Excluded third-party risk | AI Hedge Fund x402 wallet module not imported; OpenBB look-alike URL not used | Phase 0 §3 |

Residual risks: (1) Grok-side wiring must bind owner-only commands to a human step; (2) the
`--owner` flag is an assertion, not authentication — protect the Mac mini account; (3) Yahoo data
is unofficial and delayed.

## 4. Cost and performance

- **Model calls:** zero by default. LLMs are used only for Data Guardian explanations, Research
  Challenger counter-arguments and TradingAgents, and only when a key is configured. Research
  Challenger results are cached by content, so repeated equivalent requests cost nothing.
- **Telemetry:** every specialist call logs latency, status, tokens, model and cost
  (`python -m marcus_v2 telemetry`). Prices come from `MARCUS_V2_MODEL_PRICES`; none are
  hard-coded, so unconfigured costs show 0.
- **Compute (build container, 4 vCPU):** full suite 23 s; a 13-month VectorBT backtest with
  sensitivity grid ~2–4 s; Data Guardian on 270 bars < 0.1 s. No always-on services.
- **New paid subscriptions:** none.

## 5. Known defects / limitations

1. Pre-existing: the legacy repo test suite fails to import (`ModuleNotFoundError: data`) on `main`
   and on this branch identically — not caused by V2; Dex to triage separately.
2. NYSE calendar covers 2026–2027 only (same table as the trader skills); extend yearly.
3. Gold uses COMEX futures (GC=F) as its only free daily source — not spot XAU/USD and not OCBC's
   precious-metals quote.
4. SGD base-currency reporting needs an FX series (USD→SGD) wired into Portfolio Analyst.
5. LEAN engine cross-check not run (disk allowance in the build container); run on the Mac mini.

## 6. Phase reports

| Phase | Status | Commit |
|---|---|---|
| 0 Discovery & assessment | COMPLETE (existing system not accessible) | `ddcc6b7` |
| 1 Shared foundations | COMPLETE — 15 tests | `ddcc6b7` |
| 2–3 Market Intelligence, Data Guardian | COMPLETE — live cross-check passes | `8d616e3` |
| 4 Strategy Lab | COMPLETE | `f867404` |
| 5 Research Challenger | COMPLETE (LLM path mocked) | `b3e01b2` |
| 6 Execution Specialist | COMPLETE (LEAN engine cross-check blocked) | `370088e` |
| 7 Portfolio Analyst + capital | COMPLETE (S$ top-up blocked pending owner decision) | `2cebf2f` |
| 8 Marcus + integrations | COMPLETE with contract-only adapters for Quant / Price Monitor / Grok | `ece8f12` |
| 9 QA & docs | COMPLETE (self-QA); independent QA by Dex pending | this commit |
