# Finance Bridge: Marcus × Anthropic financial-services agents

Anthropic's financial-services agents ("finance agents", anthropics/financial-services, pinned at
`3e32c22`) are vendored unmodified under `.claude/agents/` and `.claude/skills/`. See
`.claude/FSI_NOTICE.md`, which is Apache-2.0.

They expect paid MCP connectors such as FactSet, Daloopa and CapIQ. The **Finance Bridge** agent
(`marcus_v2/agents/finance_bridge`) lets them work on free, cited data and checks what they produce.

```
fsi_prepare ──► data pack (SEC XBRL quarters + DG-validated prices, each figure with a source_id,
                gaps listed as unavailable) ──► Earnings Reviewer / Market Researcher (Claude)
            ──► note.md + output.json (contract marcus-fsi/v1)
fsi_ingest  ──► verify: pack identity · phantom sources · figures vs filing (±0.5%, EPS ±1%,
                $bn/$mn scaling) · injection/advice text ──► VERIFIED / PARTIAL / REJECTED
            ──► ResearchReport(source="fsi:<agent>", rating=None, action=None) ──► Research Challenger
```

## Guarantees

- **Permissions:** `read_market_data` and `read_research` only. The bridge has no ledger, execution or recommendation rights.
- **No invented data:** consensus, transcripts and broker research are always listed as unavailable. So are SEC filings when `SEC_USER_AGENT` is unset, and prices when the feed fails or Data Guardian rejects them.
- **Rejected output is not stored as research.** Every ingest result is stored as evidence (`fsiin_<pack_id>`) and recorded in the audit log.
- **Supplementary only:** TradingAgents stays the primary research input for Marcus.

## Setup

- Live SEC data needs `SEC_USER_AGENT="Your Name you@example.com"` in the environment (SEC policy). The owner sets it; the bridge never fills it in.
- Tests run offline using `tests/marcus_v2/fixtures/sec/`.
- Workflow: the `marcus-fsi` skill.
