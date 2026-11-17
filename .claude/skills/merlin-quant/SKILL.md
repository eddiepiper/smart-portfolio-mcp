---
name: merlin-quant
description: >-
  Merlin + Quant paper-trading team (standalone, paper only, independent of Marcus). Merlin does fundamental
  research with Anthropic's financial-services skills; Quant runs TauricResearch/TradingAgents and independently
  challenges Merlin; a deterministic risk gate and ai-hedge-fund's paper broker execute approved simulated trades
  into one ledger. Use when the user asks Merlin or Quant to research, challenge, propose, approve or execute a
  paper trade, or asks about the Merlin/Quant paper portfolio, its history, backtest, performance, halt or resume.
argument-hint: "research|propose|approve|execute|portfolio|history|backtest|performance|halt|resume ..."
---

# merlin-quant

CLI: `~/.tradingagents-src/venv/bin/python -m merlin_quant ...` from the repository root (setup once:
`bash merlin_quant/setup.sh`). Full command list: `docs/merlin_quant/README.md`.

Rules:
- PAPER ONLY. Never connect a broker. Never approve a proposal without the user's explicit yes in this chat
  (`approve ... --by <their name>` records it). Do not switch to PAPER_AUTO.
- Use `--runtime claude_cli` for real research (costs money per run: report the `cost` block); `scripted` is a
  test double and must never be presented as research.
- Keep `MERLIN_QUANT_HOME` on durable storage; never delete or re-`init` an existing ledger.
- Report Merlin's and Quant's views separately, quote the challenge verdict, and keep disagreements visible.
- Real data: `python -m merlin_quant fetch-data --out FILE` (Yahoo + SEC EDGAR, needs `SEC_USER_AGENT`), then pass
  `--data FILE --cutoff <last session>`. The Claude cloud container cannot reach those hosts: run it on the user's
  machine. Without `--data` everything is the SYNTHETIC fixture and must be labelled as such.
- An owner config at `$MERLIN_QUANT_HOME/config.json` is used automatically; never write or edit thresholds without
  the user's explicit numbers.
- Say which data was synthetic or missing. Give times in SGT and New York time.
