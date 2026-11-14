# Quant agent configuration

Quant runs the unmodified TauricResearch/TradingAgents graph (v0.7.1, commit 9fa1196) as its research engine.
The agents' prompts are TradingAgents' own; this package does not rewrite them.

| Setting | Value | Why |
|---|---|---|
| Analysts | market, social (sentiment), news | Fundamentals belong to Merlin; Quant keeps raw filed revenue/margin history as independent evidence instead of a second fundamentals engine |
| Bull/Bear debate rounds | 1 | cost control (spec §17) |
| Risk debate rounds | 1 | cost control |
| Max tool rounds per analyst | 3 | cost control |
| Quick-think model (analysts, debaters) | `claude-haiku-5-5` | routine work |
| Deep-think model (Research Manager, Portfolio Manager) | `claude-sonnet-5-5` | synthesis of conflicting evidence |
| Data | the run's point-in-time view, through TradingAgents' own tool layer | no tool can return data after the cutoff |
| Portfolio | read-only `PortfolioContext` built from the ledger | TradingAgents owns no cash or holdings |
| Memory-log settlement | disabled (it fetches live prices) | point-in-time integrity and the blocked network |

The challenge stage prompt is `quant_challenge.md`.
