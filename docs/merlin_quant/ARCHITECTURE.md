# Merlin + Quant: architecture and technical decisions

Standalone and paper only. Package `merlin_quant/` imports nothing from `marcus_v2`, `dcm`, `agents`,
`interface` or any other system in this repository, and no code path can reach a broker.

```
USER REQUEST (CLI: python -m merlin_quant ...)
        |
RESEARCH COORDINATOR (workflow.Team)  -- sequences stages, never edits a lead's report
        |
   +----+-----------------------------+
   |                                  |
MERLIN (merlin.py)                 QUANT (quant.py)            Stage A: separate execution contexts,
 calc engine: metrics, DCF,         TauricResearch/TradingAgents   same instrument/cutoff/verified data.
 scenarios, 5x5 sensitivity,        graph v0.7.1 (market, social,  Quant's input bundle never contains
 peers  +  narrative by LLM         news analysts, bull/bear       Merlin's output (digest recorded).
 runtime with Anthropic FSI         debate, research manager,
 plugins (financial-analysis,       trader, risk debaters,
 equity-research)                   portfolio manager)
   |                                  |
   +-----------> QUANT CHALLENGE <----+                         Stage B: deterministic checks over
                 (quant.challenge)                               Quant's independent evidence; verdict
                       |                                         by fixed rules; reasoning by LLM.
                 JOINT ASSESSMENT (decision.synthesize)          Stage C: both leads quoted verbatim;
                       |                                         disagreements listed, never averaged.
                 PAPER TRADE PROPOSAL (decision.build_proposal)  typed contract; integer sizing in code
                       |
                 DETERMINISTIC RISK GATE (risk.evaluate)         PASS / REJECT / BLOCKED, 19 checks
                       |
                 APPROVAL (PAPER_MANUAL)  or auto (PAPER_AUTO, disabled)
                       |
                 PAPER EXECUTION ENGINE (engine.PaperEngine)     ai-hedge-fund PaperBroker + TradingCosts
                       |
                 PERSISTENT LEDGER (ledger.Ledger)               hash-chained, idempotent, append-only
                       |
                 PERFORMANCE & DECISION REVIEW (performance.py, backtest.py)
```

## Upstream foundations (inspected 2026-11-14)

| Upstream | Commit | How it is used |
|---|---|---|
| anthropics/financial-services | `3e32c2287dcb637c184c01cc0ad82d6af7e9bb0f` | Merlin's `claude_cli` runtime loads the `financial-analysis` and `equity-research` plugins with `--plugin-dir` and invokes `equity-research:earnings-analysis`, `financial-analysis:dcf-model`, `financial-analysis:comps-analysis`, `equity-research:thesis-tracker` through the Skill tool. Invocations are captured from the CLI event stream into `fsi_skills_used`. The plugins are **not** enabled on the claude.ai account; they are loaded per run from a pinned clone (`merlin_quant/setup.sh`). The agent plugins (market-researcher, earnings-reviewer, model-builder) bundle the same skills; their agents target interactive Excel/deck work, so they are not invoked. The plugins' licensed data MCP connectors (FactSet, S&P, LSEG, Morningstar, Daloopa, ...) are not authenticated and fail to connect: no paid data is used. |
| TauricResearch/TradingAgents | `9fa1196fffbcfd9de5146d2dd2869df84ab99838` (v0.7.1) | Quant runs the unmodified `TradingAgentsGraph.propagate`. Integration points are TradingAgents' own seams: the LLM tier factory (`create_tier_client`), the data vendor router and snapshot tool, the sentiment pre-fetchers, instrument identity, and memory settlement (disabled: it fetches live prices). Same pinned commit and venv as the existing `stock-analysis` skill (reused, not reinstalled). |
| virattt/ai-hedge-fund | `5c553b5222044f6a5527bd377b4104c50ee0ad28` (aihf 2.7.2) | Vendored, unmodified apart from import paths: `brokers/{models,costs,sim,paper}.py` and `paths.write_atomic` (MIT). Investor personas, signals, LLM layer and pipeline are not used. |

## Decision: one paper portfolio engine

TradingAgents has no paper broker or ledger. Its `PortfolioContext` is a read-only description of a book given to
its agents, and `tradingagents/backtest.py` states it "is not a portfolio simulator, and must not grow one". So
there is nothing in TradingAgents that owns cash or holdings, and no risk of two engines.

ai-hedge-fund's paper stack was evaluated:

| Component | Verdict |
|---|---|
| `PaperBroker` / `SimBroker` | **Reused.** Deterministic complete fills, persisted atomically, a second book for reconciliation. |
| `TradingCosts` | **Reused.** Half-spread + square-root-law impact from ADV and volatility; the broker's commission schedule (percentage of value + per share, with a minimum per order, each side) is added by subclass (`engine.PaperCosts`); SimBroker is patched to pass the fill price to `commission()`. |
| `paper/ledger.py` (session-chained ledger) | **Design reused, code not.** It is tied to aihf's `FundState`/`SessionRecord` session pipeline (one record per fund session). Our decisions are per proposal, so the same idea (hash chain, state rebuilt by folding, kill switch, broker-vs-ledger reconciliation) is implemented in ~200 lines in `ledger.py`. |
| `risk/limits.py`, `risk/exposures.py` | Not reused: long/short gross/net/beta limits built on aihf's data client. The spec's checks (cash, duplicates, freshness, calendar, halts, drawdown...) are a different set; `risk.py` implements them once. |
| backtester / scorecard | Not reused: bound to aihf's signal pipeline. `backtest.py` reuses `SimBroker` + `TradingCosts` for its fills. |
| Installation | aihf pins pandas ^2, numpy ^1, langchain-anthropic 0.3.5, which conflict with TradingAgents 0.7.1 (pandas >= 3, langchain-anthropic >= 1.7). Vendoring four dependency-light modules avoids a second environment. |

Source of truth: the ledger. The broker book must agree with it at every start (`reconcile()`); the only tolerated
difference is one interrupted fill that write-ahead logging can prove (ORDER_INTENT without FILL), which is either
recovered (book shows the fill) or abandoned (book does not).

## Separation and independence

- Merlin and Quant run concurrently in separate threads; on `claude_cli` every model call is a separate `claude -p`
  process in a fresh empty directory with no project settings, MCP servers or session persistence.
- Quant's inputs are the point-in-time view and the ledger's book. Its `inputs_digest` is identical whether or not a
  Merlin report exists (test 4), and `saw_merlin_report` is the literal `False` in the schema.
- Quant's fundamentals analyst is off (Merlin owns fundamentals). Quant keeps independent evidence: raw filed
  revenue growth, best filed FCF margin and P/E from the filings, plus price/volume indicators.

## Decision contract and risk

- `contracts.TradeProposal` is the single decision contract (BUY / SELL / HOLD / REDUCE / NO_ACTION), with
  `extra="forbid"`, closed enums and cross-field validation (sizes vs action, no reference price after the cutoff).
- The only model-derived inputs to a proposal are closed-enum stances, convictions and the challenge verdict.
  Quantities are integer arithmetic on ledger NAV and the policy's target weights.
- `risk.evaluate` checks: engine halt, mode, eligibility, executable action, data available, price not stale,
  data freshness (cutoff = last completed session), reference price matches data, trading calendar (fill at the
  next NYSE session open), instrument halt, order size, existing position (no shorting), cash after buffer, max
  position weight, max gross exposure, sector concentration, duplicate execution, orders per session, drawdown brake.
- Thresholds come only from the config file (`config/merlin_quant/portfolio.example.json`, labelled EXAMPLE).
  `PAPER_AUTO` is refused unless `config_status` is `OWNER_CONFIGURED` and validation has passed.

## Data integrity

`data.MarketData.view(cutoff)` returns only what was knowable at the cutoff close: bars up to the cutoff, splits
applied only from their ex-date, filings by **filing date** (not period end), news before 16:00 ET on the cutoff.
Only the execution engine reads the fill-session bar, which must be strictly after the cutoff. Dividends are
credited on the pay date to shares held before the ex-date; splits rescale holdings on the ex-date.

## Persistence

State lives in `--home` (default `var/merlin_quant`, git-ignored):
`ledger/events.jsonl` (truth), `broker/book.json` (AIHF PaperBroker), `records/<kind>/<id>.json` (research,
proposals, risk results, executions, snapshots, config versions), `reports/*.md` (human-readable).

**Not production-ready for cross-session persistence in Claude cloud containers**: they are ephemeral. Point
`MERLIN_QUANT_HOME` at durable storage (a folder on your Mac, or a synced drive). No durable store was approved for
this project, so none is wired in; the ledger never silently resets (`init` refuses an existing ledger, and
`open` refuses a missing broker book).

## Cost control

Quick-think analysts and debaters on `claude-haiku-5-5`, deep-think managers and Merlin on `claude-sonnet-5-5`;
one debate round and one risk round; three tool rounds per analyst; no recurring execution is scheduled. Each
record carries `RunMeta` (runtime, models, calls, failed calls, tokens, reported USD, wall time).

## Limitations

Synthetic data is not market history. A handful of simulated trades proves nothing about profitability. Survivorship
bias (fixed universe), data revisions (restatements not modelled) and LLM run-to-run variability apply. Fills are
simulated: no market fill occurs.
