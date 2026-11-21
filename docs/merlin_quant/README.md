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

## Modes

`PAPER_MANUAL` (default): proposals execute only after `approve`. `RESEARCH_ONLY`: nothing can be approved.
`PAPER_AUTO`: executes eligible proposals after the risk gate; refused until the config is `OWNER_CONFIGURED`
(the shipped thresholds are labelled EXAMPLE, not approved settings) and `--validation-passed` is given.

## Checks before buying (`merlin_quant/guards.py`)

Three deterministic checks run on every proposal and again in the risk gate at execution. Each can only turn a BUY
into **WAIT** (the proposal becomes NO_ACTION, or HOLD if already held). Sales and trims are never held back. Every
proposal carries `guard_checks` (each check's PASS / FLAG / BLOCK with a plain reason, the action the policy alone
would have taken, and `held_back`). Thresholds live in the config's `guards` block; a config without it gets the
documented defaults. `"action": "flag"` on a check shows the warning without blocking; `"enabled": false` turns
every check into a flag.

| Check | Fires when (defaults) | Effect |
|---|---|---|
| `data_freshness` | the company filed an earnings 8-K (item 2.02) newer than its latest 10-Q/10-K in our SEC data, or the latest quarter is over 135 days old | Merlin is told and **capped at NEUTRAL, conviction <= 0.4**; BUY -> WAIT until fetch-data brings in the new quarter |
| `earnings_window` | a report is due within 5 sessions, or happened less than 3 sessions ago | BUY -> WAIT. Report dates: owner list `guards.earnings_dates`, else estimated from the company's 8-K history (same quarter last year + 364 days) |
| `market_regime` | SPY (or QQQ, when in the data) falls 2.5% in a session, 5% over five sessions, or sits 8% below its 20-session high; or the stock trails SPY by 10% over five sessions | BUY -> WAIT |

Replayed on the November 2026 NVDA data, the 16 Nov and 17 Nov cutoffs (the two BUY 6 calls) are held back by the
earnings window, the stale-data check and the stock's 11% five-day fall versus SPY; the 20 Nov cutoff by all three.
The market thresholds fired on about 4% of 2025-26 SPY sessions (March-April 2025, 10 Oct 2025, 5 Jun 2026,
19-20 Nov 2026). Lesson L-20261119-earnings_window (PR #9, prompt advice only) is now enforced in code here.

## Pre-registered calls (`merlin_quant/calls.py`)

Every `propose`/`research` run writes its call to `HOME/calls/calls.jsonl` (append-only, hash-chained) the moment
it is made: BUY, SELL, REDUCE, HOLD, NO_ACTION, or WAIT when a check held a BUY back. A call counts only if it was
written before the open of the first session after its data cutoff, and only the first such call per stock and
cutoff counts (reruns are kept, labelled, and never scored). Grades run from that open to the close 1, 5 and 10
sessions later, versus SPY, and are computed on demand, never written back.

    mq calls list                       # every call, counted / rerun / late
    mq calls grade                      # scorecard -> HOME/calls/scorecard.md
    mq calls verify                     # hash chain intact?
    mq --data ant/ant-data.json call ANT --call BUY --by Ed --note "IPO"   # ad-hoc owner call

An owner call on a stock with no SEC financial history (a new listing such as ANT) is registered in **LIMITED**
mode: no valuation, no Merlin or Quant run, only the price and market checks, and the scorecard keeps it apart.
Grade it with its own data file: `mq calls grade --extra-data ant/ant-data.json`.

## Not production-ready

Cloud containers are ephemeral: keep `MERLIN_QUANT_HOME` on durable storage. `init` warns when the home is a
temporary folder or the run is inside a Claude cloud container. Synthetic results say nothing about
real performance.
