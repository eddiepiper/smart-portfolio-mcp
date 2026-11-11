# Research Challenger (`research_challenger`)

**Foundation:** Patterns from virattt/ai-hedge-fund v2.7.2 (evidence-carrying signals, point-in-time honesty); no code imported
**Mission:** Challenge the TradingAgents thesis: contradictions, unsupported assumptions, alternative explanations, downside scenarios.

| | |
|---|---|
| Input contract | ResearchReport (TradingAgents), validated market-context report, portfolio context, proposed action |
| Output contract | ResearchChallengeReport (RESEARCH_CHALLENGE_REPORT) |
| Tools | `review_investment_thesis`, `identify_unsupported_claims`, `analyse_downside_scenarios`, `identify_missing_evidence` |
| Schemas | `marcus_v2/core/contracts.py` |
| Code | `agent.py` |
| Tests | `tests/marcus_v2/unit/` |
| LLM | narrative only (explanations / counter-arguments); never decides numbers |

## Invocation policy
- material new opportunity
- significant thesis change
- major reallocation
- conflicting research
- Marcus requests a second perspective

## Must not
- replace TradingAgents
- override Quant
- allocate capital
- execute paper trades
- invent evidence
- claim independence when paraphrasing the thesis

## Failure handling
- no market data -> assertions marked unverifiable, nothing inferred
- no LLM -> deterministic counter-arguments only, stated in limitations
- LLM paraphrases thesis -> discarded, independence_ok=False
