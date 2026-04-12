# Smart Portfolio MCP

> A multi-agent investment portfolio rebalancing system built on Model Context Protocol (MCP) — with a Telegram interface, dynamic strategy selection, and bank policy enforcement.

[Insert screenshot: Telegram conversation with portfolio rebalancing output]
[Insert screenshot: architecture diagram — agent flow]

---

## What This Is

An AI agent system that manages investment portfolio rebalancing decisions through a coordinated team of specialized agents. The core is a **Model Context Protocol engine** that tracks strategy performance over time, recommends the best approach for current conditions, and adapts as results come in.

Designed around how actual banking operations think: policy constraints, risk parameters, auditability, and strategy evolution — not just raw AI calls.

---

## The Problem It Solves

Most AI portfolio tools are glorified chatbots. They take a question, call an LLM, return a suggestion. There's no memory of what worked before, no enforcement of institutional constraints, no separation between strategy recommendation and trade execution.

This system separates those concerns. The MCP engine evolves its strategy recommendations based on performance history. A controller coordinates agents. Policy constraints are enforced by a dedicated agent before execution reaches the trade layer.

---

## Architecture

```
User (Telegram)
     ↓
Conversational Agent
     ↓
Execution Controller (lightweight orchestrator)
     ↓
┌────────────────────────────────────────────┐
│  MCP Engine                                │
│  - Reads capabilities.json (strategies)    │
│  - Scores based on performance_log.json    │
│  - Recommends: "Use Risk Parity"           │
│  - Deactivates: "Equal Weight" (underperf) │
└────────────────────────────────────────────┘
     ↓
Agent Dispatch:
  - Portfolio Analysis Agent   → current holdings assessment
  - Bank Policy Agent          → regulatory/policy constraints
  - Rebalance Strategy Agent   → executes chosen strategy
  - Trade Execution Agent      → dispatches/simulates trades
```

[Insert screenshot: MCP capabilities.json showing strategy scoring]

---

## Agents

| Agent | Role |
|-------|------|
| Conversational Interface | Handles Telegram I/O, parses user intent |
| MCP Engine | Recommends and evolves portfolio strategies |
| Portfolio Analysis | Assesses current holdings and distribution |
| Bank Policy | Applies regulatory and institutional constraints |
| Rebalance Strategy | Executes the selected rebalancing logic |
| Trade Execution | Simulates or dispatches trade instructions |

---

## Key Features

- **MCP-driven strategy evolution** — strategies are scored and rotated based on performance history
- **Policy-aware execution** — bank policy constraints validated before any trade reaches execution
- **Telegram interface** — conversational access, no dashboard required
- **Modular agent design** — each agent has one role, swappable independently
- **Persistent strategy state** — capabilities and performance logged to JSON for audit trail

---

## Project Structure

```
smart-portfolio-mcp/
├── agents/
│   ├── conversational_agent.py
│   ├── analysis_agent.py
│   ├── bank_policy_agent.py
│   ├── rebalance_agent.py
│   └── trade_execution_agent.py
├── mcp/
│   ├── mcp_engine.py
│   ├── capabilities.json       # Strategy registry with scores
│   └── performance_log.json    # Historical performance data
├── controller/
│   └── execution_controller.py
├── main.py
├── .env.example
└── requirements.txt
```

---

## Setup

```bash
git clone https://github.com/eddiepiper/smart-portfolio-mcp.git
cd smart-portfolio-mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add: OPENAI_API_KEY, TELEGRAM_BOT_TOKEN
python main.py
```

---

## Roadmap

- [ ] Claude API integration as drop-in alternative to OpenAI
- [ ] Real broker API integration (Alpaca / Interactive Brokers)
- [ ] Live performance scoring from market data
- [ ] Web dashboard for strategy monitoring
- [ ] Risk parity and factor-based strategy expansion

---

## Who This Is For

- Engineers building multi-agent financial systems
- Banking technologists exploring MCP architectures
- Product teams evaluating AI for wealth management or treasury operations

---

## Differentiation

Most portfolio AI demos are single-model wrappers. This is a **coordinated agent system** where concerns are separated, state is tracked, and institutional constraints are first-class — not an afterthought. The MCP engine means strategies improve as the system runs, rather than giving the same recommendation regardless of history.

---

## License

MIT
