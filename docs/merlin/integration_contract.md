# Merlin integration contract — Grokbot / Claude CLI (for Dex)

Status: **BUILT and TESTED** in a Claude Code cloud container (headless `claude -p`, CLI
2.1.325) on 2026-11-14. **Not yet deployed** on the Grokbot host: the host steps below are
PROPOSED until Dex runs them.

```
Grokbot ──request.json──► python -m merlin run ──► claude -p (Merlin spec + FSI skills/plugins,
                                                    structured output = merlin-research/v1)
        ◄──summary.json──                       ──► merlin.validate (+ tool-trace cross-check)
                                                ──► <out>/report.json · report.md · validation.json · tool_trace.json
Marcus  ◄── python -m merlin handoff <out>/report.json --to marcus --marcus-home $MARCUS_V2_HOME
Quant   ◄── python -m merlin handoff <out>/report.json --to quant        (challenge packet, stdout)
```

Claude Cloud conversations are **not** synchronised with Grokbot. The two environments stay
consistent because both load the same version-controlled files from this repo:
`merlin/spec/MERLIN.md` (instructions), `merlin/contracts.py` (output schema),
`merlin/calc.py` (arithmetic) and `merlin/validate.py` (acceptance rules). Pin a commit.

## 1. Host prerequisites

| Item | Requirement |
|---|---|
| Claude Code CLI | `claude` on PATH, version ≥ 2.1.325 (uses `-p`, `--json-schema`, `--output-format stream-json`, `--strict-mcp-config`, `--max-budget-usd`, `--permission-mode dontAsk`) |
| Auth | Whatever the CLI already uses on the host: `claude` login or `ANTHROPIC_API_KEY` in the service environment (Dex's secret store). Never in the repo, the request or the prompt. The runner never reads credentials. |
| Repo | `smart-portfolio-mcp` checked out at a pinned commit; run from the repo root |
| Python | 3.11+ with `pydantic==2.14.3` (`marcus_v2` requirements for the Marcus handoff) |
| SEC EDGAR | `SEC_USER_AGENT="<owner name> <owner email>"` in the environment (SEC policy). Set by the owner; Merlin never fills it in |
| FSI skills | Vendored and pinned in `.claude/skills` and `.claude/agents` (anthropics/financial-services @ `3e32c22`). Nothing else required |
| FSI plugins (optional) | `claude plugin marketplace add anthropics/financial-services` then `claude plugin install financial-analysis@claude-for-financial-services` (and `equity-research`, `market-researcher`, `earnings-reviewer`, `model-builder`). Verified working in the cloud container on 2026-11-14 |
| Licensed connectors (optional) | Only via an explicit MCP config Dex controls, passed with `--mcp-config`. `--strict-mcp-config` is always on, so broker or other host MCP servers are never loaded into Merlin |

## 2. Request (Grokbot → Merlin)

`request.json` — the `Request` model in `merlin/contracts.py`:

```json
{
  "task_type": "earnings_review",          // company_research | market_research | earnings_review | financial_model |
                                            // valuation_check | macro | comparison | question
  "subject": "NVDA",
  "question": "Analyse NVIDIA's latest reported quarter ...",
  "as_of": "2026-11-14",                   // the date the research is 'as of' (staleness is judged against it)
  "requested_by": "grokbot",               // owner | marcus | quant | grokbot | test
  "instrument_id": "US:NVDA",              // optional; Marcus instrument id
  "request_id": "mreq_..."                 // optional; generated if absent; echo it in your logs
}
```

Optional caller context (`--context file.txt`) is passed as *untrusted data*, never as
instructions.

## 3. Invocation

```bash
python -m merlin run --request request.json --out runs/<request_id> \
    --max-budget-usd 5 [--model <model id>] [--mcp-config dex/merlin.mcp.json] [--context ctx.txt]
```

The runner builds this CLI call (see `merlin/runner.py::build_command`):

```
claude -p "<assignment>" --append-system-prompt "<merlin/spec/MERLIN.md>"
       --output-format stream-json --verbose --json-schema "<merlin-research/v1 schema>"
       --allowedTools Read Glob Grep WebFetch WebSearch Skill Agent "Bash(python -m merlin:*)" "Bash(python3 -m merlin:*)"
       --disallowedTools "Bash(git push:*)" "Bash(curl:*)" "mcp__*__place*" "mcp__*__order*" "mcp__*__trade*" "mcp__*__transfer*"
       --permission-mode dontAsk --max-budget-usd 5 --no-session-persistence --strict-mcp-config
```

`dontAsk` means anything not allowed is denied, never prompted. Merlin cannot write files or
run shell commands other than its calculator in headless mode.

## 4. Response (Merlin → Grokbot)

stdout is one JSON object; exit code:

| Exit | `status` | Meaning | Grokbot action |
|---|---|---|---|
| 0 | `PASS` | Report valid, no warnings | Hand off |
| 0 | `WARN` | Report valid with warnings (e.g. stale source, uncited finding) | Hand off; surface warnings |
| 3 | `FAIL` | Report violates the contract (see `validation.issues`) | Do **not** hand off; retry once with the issues as context, then escalate to the owner |
| 4 | `RUN_FAILED` | CLI error, timeout, budget exhausted, no structured output | Retry with backoff (max 2) |
| 2 | — | Bad arguments | Fix the call |

```json
{"status": "PASS", "out_dir": "runs/mreq_...", "cost_usd": 1.84, "num_turns": 23,
 "tools_observed": {"WebFetch": 6, "Skill": 1, "Bash": 9}, "skills_observed": ["earnings-analysis"],
 "agents_observed": [], "plugins_loaded": ["financial-analysis", "..."],
 "validation": {"status": "PASS", "quality_status": "PROVISIONAL", "suggested_quality_status": "PROVISIONAL", "issues": []}}
```

Files in `out_dir`: `request.json`, `report.json` (merlin-research/v1), `report.md`,
`validation.json`, `tool_trace.json` (tools, skills, subagents, fetched URLs and plugins that
actually ran — the evidence behind every `genuine_execution: true`), `claude_stream.jsonl`.

## 5. Output contract `merlin-research/v1`

`python -m merlin schema` prints the JSON Schema. Required: `environment`, `request`,
`capabilities_used`, `executive_summary`, `key_findings`, `evidence`, `risks`, `unknowns`,
`implications`, `quality_status`, `quality_notes`. `boundaries` is fixed
(`paper_research_only: true`, `trade_instruction: false`, `binding_portfolio_decision: false`).
Evidence statuses: VERIFIED, PROVISIONAL, ASSUMPTION, UNKNOWN, STALE, FAILED. Quality statuses:
VERIFIED, PROVISIONAL, INCOMPLETE, FAILED.

Validation (deterministic, `merlin/validate.py`) fails a report for: schema errors; phantom or
duplicate ids; a figure stronger than its evidence; FAILED evidence used; VERIFIED evidence
without URL/date/retrieval time; synthetic data not marked ASSUMPTION; documents dated after
`as_of`; historical data presented as live; numeric confidence/probability language; rating,
order or sizing language; instruction-like injected text; a DCF that does not reproduce under
`merlin.calc`; a sensitivity whose centre is not the base case; a quality status above what the
evidence supports; and (runner only) a claimed skill/agent execution absent from the tool trace.

## 6. Handoff to Marcus and Quant

- **Marcus**: `python -m merlin handoff report.json --to marcus --marcus-home $MARCUS_V2_HOME`
  stores `ResearchReport(source="merlin", rating=None, proposed_action=None)` plus the Quant
  packet in Marcus's evidence store and logs `merlin_ingest` in the audit log. Refused for FAIL.
  TradingAgents remains the primary research input; Merlin is supplementary evidence.
- **Quant**: `python -m merlin handoff report.json --to quant` prints `merlin-quant-challenge/v1`
  (claims with their figures, assumptions, model inputs/outputs, sensitivity, scenarios,
  unknowns, questions). Quant challenges independently; Merlin never sees or edits the verdict.

## 7. Operating limits

- Budget: `--max-budget-usd` per run (default 5). Test runs on 2026-11-14 are recorded in
  `docs/merlin/demos/*/summary.json` with their actual cost.
- Timeout: 30 min per run (`timeout_s`). One run per request; runs are independent and can be parallel.
- No background process: Merlin runs only when Grokbot calls it.
- Logs: keep `out_dir` for audit; `claude_stream.jsonl` holds the full tool trace.

## 8. Alternative: Anthropic API (PROPOSED, not built)

The FSI repo ships Managed Agent templates (`managed-agent-cookbooks/`, `/v1/agents`) for the
same agents. A Managed Agent version of Merlin would upload `MERLIN.md` as the system prompt
and the vendored skills, and keep the same `merlin-research/v1` validation step on Grokbot's
side. Not built here because the CLI route already works and uses the same files.
