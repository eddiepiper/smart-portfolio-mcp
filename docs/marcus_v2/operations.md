# Marcus V2 — Setup, Mac deployment, Claude integration, troubleshooting, rollback

**Deployment requires owner approval.** Nothing here is wired into the live Marcus workflow yet.

## 1. Install (Mac mini, isolated)

```bash
git clone https://github.com/eddiepiper/smart-portfolio-mcp && cd smart-portfolio-mcp
git checkout feature/marcus-v2-six-specialists
python3.13 -m venv .venv-marcus-v2            # Python 3.12+ required (numpy pin); 3.13 was tested
.venv-marcus-v2/bin/pip install -r requirements-marcus-v2.txt
export MARCUS_V2_HOME=$HOME/marcus_v2_state     # ledger, evidence, audit live here
.venv-marcus-v2/bin/python -m pytest -c pytest-marcus-v2.ini --rootdir .   # expect all pass
MARCUS_V2_LIVE=1 .venv-marcus-v2/bin/python -m pytest -c pytest-marcus-v2.ini --rootdir . tests/marcus_v2/integration
```

Network: `query1.finance.yahoo.com`, `query2.finance.yahoo.com` (market data). Optional: the LLM
provider host if narrative features or TradingAgents are used. No broker hosts. Apple-silicon note:
numba/vectorbt wheels exist for arm64; if `pip` builds from source, install Xcode CLT first.

Do **not** set any brokerage environment variables: the runtime refuses to start if it sees
`ALPACA_API_KEY`, `IB_ACCOUNT`, `X402_WALLET_PRIVATE_KEY` and similar.

## 2. First-run sequence (owner actions marked ★)

```bash
M=".venv-marcus-v2/bin/python -m marcus_v2"
$M mandate_propose --owner owner:<you>                    # writes v1 (S$20,000), status pending
$M mandate_approve --owner owner:<you> --payload '{"version":1}'           ★
$M import_legacy --owner owner:<you> --payload '{"trader_dir":"trader","capital_currency":"SGD","dry_run":true}'   # review first
$M import_legacy --owner owner:<you> --payload '{"trader_dir":"trader","capital_currency":"SGD"}'                 ★
$M plan_topup --payload '{"target":"20000","currency":"SGD","stated_original":["500","SGD"]}'
$M apply_topup --owner owner:<you> --payload '{"target":"20000","currency":"SGD","stated_original":["500","SGD"]}'  ★
```

**Owner decision (2026-11-10): the original capital was S$500, raised to S$20,000.** The import
records a S$500 contribution, then funds the BAC purchase (US$203.60 on 2026-11-05) with a dated
FX conversion at the validated USD/SGD close for that day (1.27873 → S$260.35), leaving S$239.65.
A missing rate stops the import; no rate is assumed. `apply_topup` then writes one dated S$19,500
contribution — a cash flow, never a return — so contributed capital is exactly S$20,000.
Reports are in SGD; USD holdings are converted at the latest validated USD/SGD close.

## 3. Claude integration guide (owner decision 2026-11-10: Claude, not Grok)

Claude is Marcus's runtime. Two ways to run it on the Mac mini:

1. **Connect the Mac mini to Claude (Remote Control)** so a Claude session can work in its folders:
   - Claude Desktop app on the Mac mini → **Settings → Claude Code → Remote Control** → add the
     folder that holds this repository (and the existing Marcus folder, if separate); or
   - in Terminal, `cd` to that folder and run `claude remote-control`.
   The folder then appears to Claude sessions you start from your account, which can ask to use it
   (you approve each request on the Mac mini). Not the home folder, nor a hidden folder under it.
2. **Run Claude Code locally on the Mac mini** in the repository folder.

Either way Claude uses the `marcus-v2` skill (`.claude/skills/marcus-v2/SKILL.md`), which calls the
JSON command line below. TradingAgents and the Research Challenger's narrative use the Anthropic
API (`ANTHROPIC_API_KEY` on the Mac mini).

- **Tool manifest:** `python -m marcus_v2 manifest` prints tool definitions in Claude's tool-use
  shape (`marcus_daily_monitoring`, `marcus_new_opportunity`, `marcus_portfolio_review`,
  `marcus_execute_approved_order`, `marcus_telemetry`).
- **Invocation:** each tool maps to `python -m marcus_v2 <command> --payload '<json>'`; stdout is
  JSON; exit 0 ok, 1 error (`{"error": ..., "detail": ...}`), 2 refused (owner-only / unknown).
- **Owner-only commands** need `--owner owner:<name>` and must be bound to a human confirmation
  step: the skill runs them only after the owner types an explicit approval in the chat.
- **Price Monitor:** send events as `{"event_id","instrument_id","type","occurred_at",...}` or raw
  trader-alerts lines `{"line": "ALERT BAC last $49.41: ...", "occurred_at": ...}` to
  `daily_monitoring`. Duplicates are ignored across runs.
- **Quant:** `FileQuantTransport` drops requests in `$MARCUS_V2_HOME/quant/inbox/<id>.json`; Quant
  answers in `outbox/<id>.json` with `{"verdict": "SUPPORTED|CHALLENGED|INSUFFICIENT_EVIDENCE", "findings": [...]}`.
  Replace with an HTTP transport implementing `review(request) -> dict` if Quant exposes one.
- **TradingAgents:** `bash .claude/skills/stock-analysis/setup_tradingagents.sh` once; set the
  provider key (e.g. `ANTHROPIC_API_KEY`); `TradingAgentsAdapter` calls the pinned runner.
- **Python API** (in-process): `build_runtime()` + `marcus_v2.marcus.team.build_team(rt)`.

## 3b. Running entirely in the Claude cloud (owner decision 2026-11-10)

Marcus, Quant and Price Monitor are rebuilt as cloud agents, so no Mac mini is required.
1. **Environment:** create a `smart-portfolio-mcp` cloud environment with the setup script in
   `docs/marcus_v2/cloud_setup.sh` and the allowed domains listed there.
2. **State:** create a PRIVATE repo (e.g. `marcus-state`) and set `MARCUS_V2_STATE_REPO` to its URL.
   Every run pulls the ledger first and pushes after changes. Never point it at a public repo.
3. **Schedule:** a Claude scheduled task runs `python -m marcus_v2 daily_run` after the US close
   (5:10 AM SGT) and `weekly_review` on Saturdays; each run posts the Markdown report.

## 4. Scheduling (cost control)

Keep the existing schedule. Suggested cron on the Mac mini (New York times):
- 16:20 Mon–Fri: `daily_monitoring` with the day's Price Monitor events (deterministic; no LLM).
- Sat 09:00 SGT: `portfolio_review` (weekly).
- `new_opportunity`: on demand only (it is the only path that may call TradingAgents / LLMs).
Strategy Lab sweeps run only from `new_opportunity` with a `strategy` payload — never per alert.

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `MandateError: no approved mandate` | mandate pending or effective date in the future | owner approves; check `effective_from` |
| recommendation `blocked`, "primary research unavailable" | TradingAgents has no LLM key / venv | run setup script, set key |
| DQ `REJECTED` with `freshness` | bars older than last completed session | rerun after 16:20 NY; check network |
| DQ `REJECTED` with `price_type` | today's provisional bar labelled as a close | expected; wait for the close |
| fill `UNVERIFIABLE` | missing/unvalidated bar between decision and fill | refresh data; order re-evaluates |
| `LedgerIntegrityError` on start | ledger file edited outside the system | restore from backup; never hand-edit |
| `LiveTradingForbidden` at start | broker credentials in the environment | remove them |
| telemetry cost 0 | prices not configured | set `MARCUS_V2_MODEL_PRICES` to a JSON price file |

## 6. Rollback

V2 is additive: new package `marcus_v2/`, new tests, docs, `requirements-marcus-v2.txt`,
`pytest-marcus-v2.ini`, one `.gitignore` line. No existing file's behaviour changed.
- Code: revert the merge commit, or check out the previous commit.
- State: `MARCUS_V2_HOME` is separate from the existing Marcus state; stop the cron entries and
  archive the folder. The legacy `trader/portfolio.json` is only ever read, never written.
- Ledger: append-only; to undo an approved event, append a reversing event with the owner's approval.
