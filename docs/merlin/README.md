# Merlin — financial research and modelling agent

Merlin is the investment team's research and modelling specialist. It investigates companies,
sectors, earnings, valuation and macro from primary evidence, computes every number with a
deterministic calculator, and hands a validated report to Marcus (who decides) and Quant (who
challenges). Merlin never trades, rates or sizes positions.

| Piece | Path |
|---|---|
| Specification (identity, boundaries, evidence standards, output, workflow) | `merlin/spec/MERLIN.md` |
| Output contract `merlin-research/v1` | `merlin/contracts.py` (`python -m merlin schema`) |
| Deterministic calculator `merlin-calc/1.0` | `merlin/calc.py` (`python -m merlin calc …`) |
| Validator | `merlin/validate.py` (`python -m merlin validate report.json`) |
| Markdown renderer | `merlin/render.py` |
| Marcus / Quant handoff | `merlin/handoff.py` (`python -m merlin handoff …`) |
| Headless runner for Grokbot / Claude CLI | `merlin/runner.py` (`python -m merlin run …`) |
| Data-source register | `merlin/spec/data_sources.json` |
| Modelling conventions | `docs/merlin/modelling_conventions.md` |
| Grokbot / Dex integration contract | `docs/merlin/integration_contract.md` |
| Claude Code skill and subagent | `.claude/skills/merlin/SKILL.md`, `.claude/agents/merlin.md` |
| Anthropic FSI agents and skills (vendored, pinned `3e32c22`) | `.claude/agents/{market-researcher,earnings-reviewer,model-builder}.md`, `.claude/skills/…` (`.claude/FSI_NOTICE.md`) |
| Demonstration runs | `docs/merlin/demos/` |
| Tests | `tests/merlin_suite/` (`python -m pytest -c pytest-merlin.ini`) |

## Environment A — Claude Cloud (this project)

Open the *Trading Agents* project and ask, for example:

- "Merlin, analyse NVIDIA's latest earnings."
- "Merlin, compare the fundamentals of Microsoft, Alphabet and Amazon."
- "Merlin, construct a DCF valuation using the latest verified financial statements."
- "Merlin, investigate how real yields, DXY and central-bank buying influence gold prices."

The project's cloud sessions clone this repo, so the `merlin` skill, the Merlin subagent and the
vendored FSI skills load automatically (CLAUDE.md routes "Merlin, …" requests to the skill).
Reports are written to `research/merlin/<date>-<slug>/` in the session and can be committed or
attached to the thread. A project conversation is not an always-on process: Merlin runs only
when asked.

### Official Anthropic plugins

- **Claude Code CLI / cloud container** (verified 2026-11-14):
  ```bash
  claude plugin marketplace add anthropics/financial-services
  claude plugin install financial-analysis@claude-for-financial-services
  claude plugin install equity-research@claude-for-financial-services
  claude plugin install market-researcher@claude-for-financial-services
  claude plugin install earnings-reviewer@claude-for-financial-services
  claude plugin install model-builder@claude-for-financial-services
  ```
  These installs live in the container's user profile and disappear with it; the vendored
  copies in `.claude/` are what every cloud session gets without installing anything.
- **claude.ai / Cowork account plugins** (owner action): Settings → Plugins → Anthropic
  Directory → enable *equity-research*, *market-researcher*, *earnings-reviewer* (and
  *financial-analysis* / *model-builder* if listed). On 2026-11-14 the directory listed the first
  three as available and none were enabled on the account.

### Data connectors

None of the licensed connectors referenced by the FSI plugins (FactSet, S&P/Kensho, LSEG,
Morningstar, Daloopa, Moody's, PitchBook, Aiera, MT Newswires) is installed. Merlin therefore
uses free primary sources (company IR releases, SIA/WSTS, BIS, SEC EDGAR where reachable) and
marks consensus estimates, transcripts and licensed data as UNKNOWN. Adding a connector is an
owner decision (most are paid) — see `merlin/spec/data_sources.json`.

## Environment B — Grokbot / Claude CLI

See `docs/merlin/integration_contract.md`. One command per research request:

```bash
python -m merlin run --request request.json --out runs/<id> --max-budget-usd 5
```

## Quality gates

Every report passes `python -m merlin validate` before anyone relies on it. The validator
rejects uncited or inflated figures, synthetic data presented as fact, historical data presented
as live, invented confidence percentages, trade or rating language, injected instructions,
non-reproducible DCFs, off-centre sensitivity tables and inflated quality status; the headless
runner also rejects claims that a plugin skill or agent ran when the tool trace shows it did not.
