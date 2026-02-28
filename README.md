# TrustChain Agent OS

[![PyPI](https://img.shields.io/pypi/v/trustchain-agent-os.svg)](https://pypi.org/project/trustchain-agent-os/)
[![CI](https://github.com/levvlad/trustchain-agent-os/actions/workflows/ci.yml/badge.svg)](https://github.com/levvlad/trustchain-agent-os/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Trust-native protocol layer for AI agents.**

Every agent protocol (MCP, A2A, ACP, ANP) handles communication. None handle trust. TrustChain Agent OS is the missing layer underneath all of them — a gateway and a set of framework adapters that bring bilateral signed interaction records, NetFlow Sybil resistance, and automatic trust scoring to LangGraph, CrewAI, AutoGen, OpenAI Agents, Google ADK, and ElizaOS. 179 tests.

Built on [trustchain-sdk](https://github.com/levvlad/trustchain-sdk) and the [trustchain](https://github.com/levvlad/trustchain) Rust node.

## Key Features

- **Framework adapters** — drop-in trust layer for LangGraph, CrewAI, AutoGen, OpenAI Agents, Google ADK, and ElizaOS; no agent code changes required beyond initialization
- **MCP gateway** — FastAPI server that exposes downstream MCP tool servers behind a trust middleware; every tool call is recorded as a bilateral interaction
- **Trust-gated services** — `@service` decorator enforces `min_trust` thresholds before any call reaches agent business logic
- **TrustAgent primitive** — lightweight agent abstraction with built-in identity, trust tracking, and service registry
- **Automatic trust accumulation** — interaction history builds over time; trust scores improve as parties transact honestly
- **Fraud resistance** — double-spend detection and hard-zero scoring propagate across the interaction graph

## Installation

```bash
pip install trustchain-agent-os
```

### Optional extras

```bash
pip install trustchain-agent-os[gateway]   # FastAPI + uvicorn for the MCP gateway
pip install trustchain-agent-os[viz]       # Streamlit + Plotly trust graph visualizations
pip install trustchain-agent-os[dev]       # pytest + pytest-asyncio
```

Requires Python 3.11+. Depends on `trustchain-sdk>=2.0` and `fastmcp>=3.0`.

## Quick Start

### TrustAgent (minimal)

```python
import asyncio
from agent_os import TrustAgent, TrustContext

buyer = TrustAgent(name="buyer")
seller = TrustAgent(name="seller")

@seller.service("compute", min_trust=0.0)
async def compute(data: dict, ctx: TrustContext) -> dict:
    return {"result": data["x"] ** 2}

async def main():
    for i in range(1, 11):
        ok, reason, result = await buyer.call_service(seller, "compute", {"x": i})
        print(
            f"Round {i}: {i}^2 = {result['result']}"
            f"  buyer={buyer.trust_score:.3f}  seller={seller.trust_score:.3f}"
        )

asyncio.run(main())
```

Trust scores grow with every completed interaction. After a few rounds the seller can raise `min_trust` to gate access to higher-value services.

### Trust-gated service

```python
@seller.service("premium_analysis", min_trust=0.7)
async def premium_analysis(data: dict, ctx: TrustContext) -> dict:
    # Only reachable after the buyer has established sufficient trust history
    return {"analysis": "..."}
```

### MCP gateway

```python
# gateway/server.py — run with: uvicorn gateway.server:app
from gateway import create_gateway

app = create_gateway(
    upstream_servers=[
        {"name": "tools", "url": "http://localhost:3000/mcp"},
    ],
    trust_threshold=0.5,   # minimum trust score to call any tool
)
```

```bash
pip install trustchain-agent-os[gateway]
uvicorn gateway.server:app --port 8080
```

Every tool call arriving at the gateway is checked against the caller's trust score. The result is recorded as a bilateral interaction block, building the caller's trust history over time.

## Framework Adapters

Each adapter wraps a framework's native agent/crew/graph abstraction to add TrustChain identity and bilateral interaction recording. Adapters share a common interface through `tc_frameworks.base.TrustChainAdapter`.

### LangGraph

```python
from tc_frameworks.adapters.langgraph_adapter import LangGraphTrustAdapter

adapter = LangGraphTrustAdapter(agent_name="my-langgraph-agent")
result = await adapter.invoke({"messages": [{"role": "user", "content": "hello"}]})
```

### CrewAI

```python
from tc_frameworks.adapters.crewai_adapter import CrewAITrustAdapter

adapter = CrewAITrustAdapter(agent_name="my-crew")
result = await adapter.invoke({"task": "summarize recent news"})
```

### AutoGen

```python
from tc_frameworks.adapters.autogen_adapter import AutoGenTrustAdapter

adapter = AutoGenTrustAdapter(agent_name="my-autogen-agent")
result = await adapter.invoke({"message": "analyze this dataset"})
```

### OpenAI Agents SDK

```python
from tc_frameworks.adapters.openai_agents_adapter import OpenAIAgentsTrustAdapter

adapter = OpenAIAgentsTrustAdapter(agent_name="my-openai-agent")
result = await adapter.invoke({"input": "draft an email"})
```

### Google ADK

```python
from tc_frameworks.adapters.google_adk_adapter import GoogleADKTrustAdapter

adapter = GoogleADKTrustAdapter(agent_name="my-adk-agent")
result = await adapter.invoke({"query": "search for recent papers"})
```

### ElizaOS

```python
from tc_frameworks.adapters.elizaos_adapter import ElizaOSTrustAdapter

adapter = ElizaOSTrustAdapter(agent_name="my-eliza-agent")
result = await adapter.invoke({"message": "hello"})
```

All adapters are cached — the underlying agent/crew/graph is built once on first invocation and reused across calls.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Your Agent (LangGraph / CrewAI / AutoGen / OpenAI / ADK / ...) │
├──────────────────────────┬──────────────────────────────────────┤
│  tc_frameworks adapters  │  agent_os.TrustAgent + decorators    │
│  (per-framework wrappers)│  (lightweight agent primitive)       │
├──────────────────────────┴──────────────────────────────────────┤
│  gateway/                                                        │
│  FastAPI MCP gateway · trust middleware · interaction recorder  │
│  peer registry · trust_tools (MCP tool wrappers)               │
├─────────────────────────────────────────────────────────────────┤
│  trustchain-sdk  (Python)                                        │
│  Identity · HalfBlock · BlockStore · TrustEngine · NetFlow      │
├─────────────────────────────────────────────────────────────────┤
│  trustchain-node  (Rust sidecar, optional)                       │
│  QUIC P2P · SQLite WAL · transparent proxy :8203                │
└─────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
trustchain-agent-os/
├── agent_os/
│   ├── agent.py          TrustAgent: identity, service registry, call_service
│   ├── context.py        TrustContext: per-call trust metadata
│   └── decorators.py     @service decorator with min_trust enforcement
│
├── gateway/
│   ├── server.py         FastAPI application factory (create_gateway)
│   ├── middleware.py     Trust enforcement middleware
│   ├── recorder.py       Bilateral interaction recording
│   ├── registry.py       Peer and upstream server registry
│   ├── node.py           TrustChain node lifecycle management
│   ├── config.py         Gateway configuration (UpstreamServer, GatewayConfig)
│   └── trust_tools.py    MCP tool wrappers with trust metadata
│
├── tc_frameworks/
│   ├── base.py           TrustChainAdapter base class
│   ├── adapters/         Real framework adapters (6)
│   │   ├── langgraph_adapter.py
│   │   ├── crewai_adapter.py
│   │   ├── autogen_adapter.py
│   │   ├── openai_agents_adapter.py
│   │   ├── google_adk_adapter.py
│   │   └── elizaos_adapter.py
│   └── mock/             Mock adapters for testing (6, mirror structure above)
│
├── examples/             Runnable examples
│   ├── hello_trust.py    Minimal TrustAgent demo
│   ├── marketplace.py    Multi-agent marketplace simulation
│   ├── network.py        P2P network simulation
│   ├── trust_gate.py     Trust-gated service demo
│   ├── llm_agents.py     LLM-backed agents with trust
│   └── demo_gateway.py   MCP gateway demo
│
└── tests/
    ├── integration/      126 integration tests
    └── smoke/            45 smoke, e2e, and stress tests
```

## Why TrustChain vs. API Keys

| Problem | API Keys / OAuth | TrustChain |
|---------|-----------------|------------|
| Agent A calls Agent B | Credential exchange, shared secrets | Bilateral signed proof; no shared secrets |
| Sybil attacks | Trivially circumvented with new accounts | Max-flow graph analysis — fake identities cannot create real transaction paths |
| "Who do I trust?" | Centralized registries | Each agent computes trust from its own chain view |
| Accountability | Server logs (mutable, unilateral) | Append-only chains with hash links — tampering is cryptographically detectable |
| Cold start | Credentials granted upfront | Bootstrap interactions, then earn trust through real history |
| Discovery | Registry must be trusted | Any discovery source returns `(endpoint, pubkey)`; trust is ground truth from the bilateral ledger |

## Development

```bash
git clone https://github.com/levvlad/trustchain-agent-os.git
cd trustchain-agent-os
pip install -e ".[dev]"
pytest tests/ -v
```

The CI pipeline checks out `trustchain-sdk` from its sibling repository before install.

## Related Projects

- [trustchain](https://github.com/levvlad/trustchain) — Rust node: production sidecar binary, 4 crates, QUIC P2P, MCP server, 181 tests
- [trustchain-sdk](https://github.com/levvlad/trustchain-sdk) — Python SDK: zero-config `trustchain.init()`, full protocol bindings, 290 tests

## License

MIT
