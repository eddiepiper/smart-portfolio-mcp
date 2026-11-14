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
No live market-data adapter is enabled: from the Claude cloud container Yahoo, SEC EDGAR, Stooq and Alpha Vantage
are blocked by the network policy. On a machine with access, build a `MarketData` file from your provider and pass
`--data`. No paid data service is used or required.

## Modes

`PAPER_MANUAL` (default): proposals execute only after `approve`. `RESEARCH_ONLY`: nothing can be approved.
`PAPER_AUTO`: executes eligible proposals after the risk gate; refused until the config is `OWNER_CONFIGURED`
(the shipped thresholds are labelled EXAMPLE, not approved settings) and `--validation-passed` is given.

## Not production-ready

Cloud containers are ephemeral: keep `MERLIN_QUANT_HOME` on durable storage. Synthetic results say nothing about
real performance.
