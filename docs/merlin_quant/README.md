# Merlin + Quant: AI paper-trading research team

**PAPER ONLY.** Synthetic capital, simulated fills, no broker connection. Research, not financial advice.
Standalone: independent of Marcus, Grokbot and every other system in this repository.

Two independent research leads and one deterministic paper engine:

- **Merlin** (Financial Research Lead): fundamentals, earnings, DCF, scenarios, comps; Anthropic financial-services skills.
- **Quant** (Trading Analysis & Independent Challenge Lead): the real TauricResearch/TradingAgents multi-agent graph,
  then an explicit challenge of Merlin's finished report (AGREE / PARTIALLY_AGREE / DISAGREE / INCONCLUSIVE).
- **Engine**: one typed decision contract, one deterministic risk gate, ai-hedge-fund's PaperBroker and cost model,
  one hash-chained ledger as the source of truth.

Architecture and the technical decisions (including why ai-hedge-fund's broker is used but not its ledger code):
[ARCHITECTURE.md](ARCHITECTURE.md). Example runs: [example_run_scripted.md](example_run_scripted.md) (deterministic)
and [example_run_live.md](example_run_live.md) (headless Claude, synthetic data).

## Setup (once per machine)

```bash
bash merlin_quant/setup.sh            # reuses stock-analysis' pinned TradingAgents venv; pins financial-services plugins
PY=~/.tradingagents-src/venv/bin/python
export MERLIN_QUANT_HOME=~/merlin-quant-paper    # durable location for the ledger (default: ./var/merlin_quant)
```

## Commands

Global options go before the command: `--home`, `--config` (default: the EXAMPLE config), `--data synthetic|FILE`,
`--runtime scripted|claude_cli|anthropic_api`, `--cutoff YYYY-MM-DD` (completed session), `--now ISO-TIME`.

| Task | Command |
|---|---|
| Build a real-data file (Yahoo + SEC EDGAR) | `SEC_USER_AGENT="Your Name you@example.com" $PY -m merlin_quant fetch-data --out ~/merlin-quant-paper/data.json` |
| Create the paper portfolio | `$PY -m merlin_quant init` |
| Research an instrument (all stages + proposal) | `$PY -m merlin_quant --runtime claude_cli research AAPL` |
| Run Merlin only | `$PY -m merlin_quant --runtime claude_cli merlin AAPL` |
| Run Quant only | `$PY -m merlin_quant --runtime claude_cli quant AAPL` |
| Independent joint analysis + paper-trade proposal | `$PY -m merlin_quant --runtime claude_cli propose AAPL` |
| Approve a simulated trade | `$PY -m merlin_quant approve PROPOSAL_ID --by Ed` |
| Reject a proposal | `$PY -m merlin_quant reject PROPOSAL_ID --by Ed --reason "..."` |
| Risk-check and execute an approved paper trade | `$PY -m merlin_quant execute PROPOSAL_ID` |
| View the portfolio (marks, NAV snapshot) | `$PY -m merlin_quant portfolio --as-of 2026-11-13` |
| View paper-trading history (ledger) | `$PY -m merlin_quant history` |
| Verify ledger chain + broker reconciliation | `$PY -m merlin_quant verify` |
| Run a backtest | `$PY -m merlin_quant backtest --start 2025-11-03 --end 2026-11-13 --every 5` |
| Performance metrics + research scoring | `$PY -m merlin_quant performance --horizon 5` |
| Halt paper trading | `$PY -m merlin_quant halt --reason "..."` |
| Resume (named authoriser) | `$PY -m merlin_quant resume --by Ed` |
| Change mode | `$PY -m merlin_quant mode RESEARCH_ONLY` (PAPER_AUTO is refused with EXAMPLE config) |
| End-to-end demo | `$PY -m merlin_quant.demo [--runtime claude_cli]` |
| Automated tests | `$PY -m pytest -c pytest-merlin-quant.ini -q` |
| Live-runtime tests (costs money) | `MERLIN_QUANT_LIVE=1 $PY -m pytest -c pytest-merlin-quant.ini -q -m live` |

Reproducible synthetic walk-through (decision on the 2026-11-06 close, evaluated that evening):

```bash
H=/tmp/mq; C="--home $H --cutoff 2026-11-06 --now 2026-11-06T21:00:00-05:00"
$PY -m merlin_quant --home $H init
$PY -m merlin_quant $C propose AAPL          # prints the proposal id
$PY -m merlin_quant $C approve <PROPOSAL_ID> --by Ed
$PY -m merlin_quant $C execute <PROPOSAL_ID> # fills at the 2026-11-09 open + costs
$PY -m merlin_quant --home $H portfolio --as-of 2026-11-13
```

## Runtimes and data

| Runtime | Needs | Use |
|---|---|---|
| `scripted` | nothing | deterministic test double; tests and wiring checks |
| `claude_cli` | a signed-in `claude` CLI | real Claude in a separate headless process per call; no Python API key |
| `anthropic_api` | `ANTHROPIC_API_KEY` or `MERLIN_QUANT_ANTHROPIC_API_KEY` | Anthropic SDK |

Data: `synthetic` (default, deterministic, labelled synthetic everywhere) or a `MarketData` JSON file (`--data`).

`fetch-data` builds that file from free public sources, no key and no paid service: Yahoo Finance chart JSON for daily
bars, splits and dividends (turned back into raw bars plus split actions), Yahoo search for headlines, and SEC EDGAR
XBRL company facts and submissions for quarterly fundamentals and 8-K filings. SEC asks for a User-Agent with your
name and email (`--sec-user-agent` or `SEC_USER_AGENT`). It keeps the point-in-time rules: each quarter is the value
first reported, dated by the filing that first reported it (restatements never rewrite history), cash-flow quarters are
de-cumulated from year-to-date figures, a Q4 from the 10-K is dated by the 10-K, 8-Ks carry their SEC acceptance time
and an unfinished session is dropped. The file records when it was downloaded (`fetched_at`, UTC), so anyone can check that its last session was complete. Re-running into the same file keeps the headlines already collected, so run it
after each close to build a news history (Yahoo only serves recent items). Dividends are credited on the ex-date
because Yahoo has no pay date. Symbols default to the config universe; others are fetched but cannot be traded until
added to the config.

The Claude cloud container cannot reach Yahoo or SEC (network policy), so run `fetch-data` on your own machine:

```bash
export MERLIN_QUANT_HOME=~/merlin-quant-paper SEC_USER_AGENT="Your Name you@example.com"
$PY -m merlin_quant fetch-data --out $MERLIN_QUANT_HOME/data.json      # prints the --cutoff to use
$PY -m merlin_quant --data $MERLIN_QUANT_HOME/data.json --cutoff <LAST_SESSION> --runtime claude_cli propose AAPL
```

## Owner config

Copy `config/merlin_quant/portfolio.example.json` to `$MERLIN_QUANT_HOME/config.json` and set your own universe,
sectors and thresholds. The CLI uses that file automatically (an explicit `--config` still wins) and the ledger
records a `CONFIG` event whenever the config changes. Leave `config_status` as `EXAMPLE` until you have chosen the
numbers yourself; only `OWNER_CONFIGURED` can ever enable `PAPER_AUTO`.

## Fees (broker commission)

Paper fills pay the owner's online staff preferential rate, set in the config's `costs` block:

| Market | Rate per side | Minimum per order |
|---|---|---|
| US (this portfolio) | 0.06% (`commission_rate: 0.0006`) | USD 15 (`min_commission: 15`) |
| SG | 0.06% | SGD 0 |
| HK | 0.06% | HKD 80 |

Commission = max(0.06% x trade value, minimum), charged on the buy AND on the sell. Below about $25,000 a side the
USD 15 minimum is what you pay, so a round trip costs $30: 1.9% of a $1,560 order, 3.2% of a ~$940 (6 x NVDA) order.

- Proposals carry `estimated_costs` (commission each way, round trip, round trip as % of the order, and the
  break-even move including the modelled spread); the report and `propose`/`approve` output show it.
- Sizing is fee-aware: shares + the buy commission stay within the target weight's dollars.
- The risk gate's cash check includes the commission, and `commission_drag` reports the round-trip fee on every
  order. Set `risk.max_round_trip_fee_pct` (e.g. `0.02`) to reject buys where fees eat more than that; unset, it
  only reports. Sells are never blocked on fees.
- `portfolio` shows `fees_paid` (already charged) and `estimated_exit_fees` (what selling everything would cost).
  Realised P&L is net of both commissions; unrealised P&L is net of the buy commission.
- History is never re-priced: each FILL stores the commission actually charged, and a fee change is recorded as a
  `CONFIG` event. A config without `commission_rate` keeps its old fees (rate 0).

## Modes

`PAPER_MANUAL` (default): proposals execute only after `approve`. `RESEARCH_ONLY`: nothing can be approved.
`PAPER_AUTO`: executes eligible proposals after the risk gate; refused until the config is `OWNER_CONFIGURED`
(the shipped thresholds are labelled EXAMPLE, not approved settings) and `--validation-passed` is given.

## Not production-ready

Cloud containers are ephemeral: keep `MERLIN_QUANT_HOME` on durable storage. `init` warns when the home is a
temporary folder or the run is inside a Claude cloud container. Synthetic results say nothing about
real performance.
