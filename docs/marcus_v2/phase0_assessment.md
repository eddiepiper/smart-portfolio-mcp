# Marcus V2 — Phase 0: Baseline discovery and technical assessment

Date: 2026-11-10. Author: Claude Code (engineering). Status: COMPLETE (with blockers below).

## 1. Existing system discovery

| Component | Where found | Status |
|---|---|---|
| Marcus (advisory lead) | Not in any GitHub repository the session can reach (75 repos searched for Marcus / Quant / Price Monitor / Dex / Grok). Lives on the Mac mini / Grok runtime. | **NOT ACCESSIBLE** — no computer folder is connected to this session. |
| TradingAgents | This repo: `.claude/skills/stock-analysis/` (runner, pinned v0.7.1 `9fa1196`). Also a 2025 archived fork `eddiepiper/TradingAgents`. | Reusable. Runs end to end up to the LLM call; **no LLM key in this environment**. |
| Quant | Not found. | **NOT ACCESSIBLE** — adapter contract defined, MOCKED in tests. |
| Price Monitor | Not found. Closest existing equivalent: `trader-alerts` skill + daily scheduled check in this repo. | Event adapter built against a JSON contract; real Price Monitor **NOT VERIFIED**. |
| Dex | Not found. | Handover via docs + PR. |
| Grok APIs / agent runtime / queues | Not found; no credentials. **Superseded 2026-11-10: owner chose Claude as the runtime.** | **NOT VERIFIED** — compatibility adapter is a plain JSON in/out CLI + Python API, which any tool-calling runtime can wrap. |
| Paper portfolio history | `trader/portfolio.json` (git-ignored, this container): cash 296.40, 4 BAC @ 50.90 (2026-11-05); config `budget: 500`, currency **USD**. | Imported read-only. **Discrepancy**: directive says original capital S$500; local record is US$500. Flagged, not reconciled automatically. |
| Existing repo code | `smart-portfolio-mcp`: DCM engine, bank-policy / rebalance agents, Telegram bot. | Preserved untouched; V2 lives in a separate `marcus_v2/` package. |
| Mac mini hardware | Not reachable. Build container: 4 vCPU, 15 GB RAM, Python 3.13, Docker (daemon started manually). | Mac limits **NOT VERIFIED**. |

Decision: build in this repository on `feature/marcus-v2-six-specialists`, as an isolated
`marcus_v2/` package with its own pinned requirements, so nothing existing changes.

## 2. Six GitHub projects (reference copies in an isolated scratch area, not vendored)

| Project | Commit pinned | Licence | Install here | Use |
|---|---|---|---|---|
| OpenBB (`OpenBB-finance/OpenBB`) | `ca1d11fe5c000d7c15939d744e004f84d8ba61e1` (2026-11-10). PyPI: `openbb-core 2.0.1`, `openbb-yfinance 2.1.0` | Apache-2.0 | OK | Market Intelligence provider layer (QueryExecutor → yfinance provider). Verified live: 28 BAC daily bars. |
| Pandera | `2a5d1d6997a2c2faa0d6c8d936d3a8d656d0d741`; PyPI `pandera 0.36.0` | MIT | OK | Data Guardian deterministic schemas. |
| VectorBT (free) | `07f5e85e83a5286c396f4ecfd55dfaa05f25442d`; PyPI `vectorbt 1.2.0` | Apache-2.0 **+ Commons Clause** | OK (numba 0.68.1) | Strategy Lab backtests. Internal use only: we may not sell a product/service whose value derives substantially from vectorbt. |
| AI Hedge Fund | `5c553b5222044f6a5527bd377b4104c50ee0ad28` (v2.7.2) | MIT | Not installed (by design) | Reference patterns only: point-in-time Signal(conviction, thesis), decide-at-close / fill-next-close, hash-chained ledger. |
| QuantConnect LEAN | `3afb212b30eff5e73013b5130887a95bf9e4cb4b` | Apache-2.0 | .NET absent; Docker image `quantconnect/lean` pulled for isolated backtests | Execution Specialist: LEAN-compatible fill semantics implemented deterministically in Python; optional LEAN-engine backtest cross-check in a network-less container. Live trading never exposed. |
| QuantStats | `d1a214fb906e76f974f45cdccf923b601c67a756`; PyPI `quantstats 0.0.86` | Apache-2.0 | OK | Portfolio Analyst metrics from the verified ledger. |

Note: the directive's OpenBB URL `github.com/openbq-org/OpenBB` is a look-alike of the official
`OpenBB-finance` organisation. It currently serves the same commit (`ca1d11f`), but only the
official source is used and pinned.

## 3. Security findings

- **AI Hedge Fund** ships an x402 module that signs USDC payments from `X402_WALLET_PRIVATE_KEY`
  (`hedge_fund/data/wallet.py`). Excluded: nothing from it is imported; no wallet key exists here.
- **LEAN** supports live brokerage. Only the backtest path is used, inside a container started
  with `--network none`; the adapter's command allowlist rejects `live`, and the deployed
  package contains no brokerage adapters or credentials (enforced by security tests).
- **OpenBB / yfinance** fall back without Yahoo cookies when `fc.yahoo.com` is blocked; history
  works, some fundamentals do not. Data is delayed and unofficial — Data Guardian classifies it.
- No third-party install scripts executed; all packages from PyPI into isolated virtualenvs,
  pinned in `requirements-marcus-v2.txt`.

## 4. Paid services / credentials needed

| Need | Status |
|---|---|
| LLM for Research Challenger narratives and TradingAgents | No key here. Deterministic checks run without it; LLM paths are MOCKED in tests. |
| OpenBB paid providers (FMP, Polygon, Intrinio…) | Not used. yfinance provider only (free, delayed). |
| Grok runtime | Not used (owner decision 2026-11-10: Claude is the runtime). |
| Brokerage | Prohibited. None configured. |

## 5. Blockers carried forward

1. Marcus, Quant, Price Monitor and Dex are not reachable: their integrations are contract
   adapters, **NOT VERIFIED** against the real system until run on the Mac mini.
2. RESOLVED 2026-11-10 — owner confirmed the original capital was S$500, raised to S$20,000. Previously: currency mismatch (US$500 local vs S$500 in directive): S$20,000 top-up
   is prepared as a pending, owner-approved contribution, not applied.
3. No LLM key: TradingAgents and LLM narrative paths cannot be demonstrated live.
