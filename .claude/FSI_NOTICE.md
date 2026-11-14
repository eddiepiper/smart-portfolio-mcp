# Vendored from anthropics/financial-services

Source: https://github.com/anthropics/financial-services at commit
`3e32c2287dcb637c184c01cc0ad82d6af7e9bb0f` (2026-11-12), Apache-2.0 (see FSI_LICENSE).
Reviewed 2026-11-14: Markdown instructions only, no scripts.

Vendored unmodified:
- agents: `.claude/agents/earnings-reviewer.md`, `.claude/agents/market-researcher.md`
- skills (earnings-reviewer): earnings-analysis, model-update, morning-note, audit-xls, earnings-preview, xlsx-author
- skills (market-researcher): sector-overview, competitive-analysis, comps-analysis, idea-generation, pptx-author

These agents expect FactSet / Daloopa / CapIQ MCP connectors (paid). In this repo the
`marcus-fsi` skill supplies a free, source-cited data pack instead (SEC XBRL filings +
Data-Guardian-validated prices) and ingests their output into Marcus after deterministic
verification. Upstream rule kept: nothing here is investment advice; outputs are drafts
for human review.
