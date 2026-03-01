# TrustChain Agent OS — Integration Guide

This guide covers every integration path for the TrustChain Agent OS: from a
bare-minimum two-agent example to full production gateway deployments, with
step-by-step instructions for all 12 supported framework adapters.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Choosing Your LLM Provider](#choosing-your-llm-provider)
3. [Quick Start — TrustAgent with No Framework](#quick-start--trustagent-with-no-framework)
4. [Core Concepts](#core-concepts)
5. [Adding TrustChain to Your Framework](#adding-trustchain-to-your-framework)
   - [1. LangGraph](#1-langgraph)
   - [2. CrewAI](#2-crewai)
   - [3. AutoGen / AG2](#3-autogen--ag2)
   - [4. OpenAI Agents SDK](#4-openai-agents-sdk)
   - [5. Google ADK](#5-google-adk)
   - [6. Claude (Anthropic)](#6-claude-anthropic)
   - [7. Smolagents (HuggingFace)](#7-smolagents-huggingface)
   - [8. PydanticAI](#8-pydanticai)
   - [9. Semantic Kernel](#9-semantic-kernel)
   - [10. Agno (ex-Phidata)](#10-agno-ex-phidata)
   - [11. LlamaIndex](#11-llamaindex)
   - [12. ElizaOS](#12-elizaos)
6. [Trust-Gated Services](#trust-gated-services)
7. [MCP Gateway Integration](#mcp-gateway-integration)
8. [Calling Adapters via MCP](#calling-adapters-via-mcp)
9. [Common Patterns](#common-patterns)

---

## Prerequisites

**Python version:** 3.11 or later is required.

**Core packages:**

```bash
pip install trustchain-py trustchain-agent-os
```

`trustchain-py` provides the identity, bilateral ledger, and trust computation
primitives. `trustchain-agent-os` provides `TrustAgent`, the gateway, and all
12 framework adapters.

**Framework-specific packages** are listed under each adapter section. You only
need to install the packages for the frameworks you intend to use. If you want
everything at once:

```bash
pip install "trustchain-agent-os[all-frameworks]"
```

---

## Choosing Your LLM Provider

**TrustChain is LLM-agnostic.** Trust happens at the protocol layer — it is
based on bilateral signed interaction records, not on which model powers the
agent. You can swap LLM providers without affecting any trust semantics.

Most adapters default to OpenAI (set `OPENAI_API_KEY`). Two adapters are
framework-native to a single provider: Google ADK (Gemini only) and the Claude
adapter (Anthropic only). All other adapters support multiple providers via
their framework's native routing.

| Provider | Environment variable | Notes |
|----------|---------------------|-------|
| OpenAI | `OPENAI_API_KEY` | Default for most adapters |
| Anthropic | `ANTHROPIC_API_KEY` | Required for Claude adapter; optional for others |
| Google Gemini | `GOOGLE_API_KEY` | Required for Google ADK adapter; optional for others |
| HuggingFace | `HF_TOKEN` | Used by Smolagents `model_type="hf"` |

Provider-specific install instructions and model strings are shown in each
adapter section below.

---

## Quick Start — TrustAgent with No Framework

This five-minute path demonstrates the core TrustChain primitive: two agents
exchanging signed interactions and building bilateral trust history.

### Minimal two-agent example

```python
# hello_trust.py
import asyncio
from agent_os import TrustAgent, TrustContext

# Create two agents. Each gets a fresh Ed25519 keypair on construction.
buyer  = TrustAgent(name="buyer")
seller = TrustAgent(name="seller")

# Expose a service on the seller. min_trust=0.0 means anyone can call it.
@seller.service("compute", min_trust=0.0)
async def compute(data: dict, ctx: TrustContext) -> dict:
    x = data["x"]
    return {"result": x ** 2, "status": "completed"}

async def main():
    print(f"buyer  pubkey: {buyer.pubkey[:16]}...")
    print(f"seller pubkey: {seller.pubkey[:16]}...")

    for i in range(1, 11):
        # call_service returns (accepted: bool, reason: str, result: Any)
        ok, reason, result = await buyer.call_service(
            seller, "compute", {"x": i}
        )
        print(
            f"Round {i:2d}: {i}^2 = {result['result']:3d}  "
            f"| buyer={buyer.trust_score:.3f}  seller={seller.trust_score:.3f}"
        )

    print()
    print(f"buyer  interactions: {buyer.interaction_count}")
    print(f"seller interactions: {seller.interaction_count}")
    print(f"buyer  integrity:    {buyer.chain_integrity():.3f}")
    print(f"seller integrity:    {seller.chain_integrity():.3f}")

asyncio.run(main())
```

Run it:

```bash
python hello_trust.py
```

You will see trust scores rise from 0.0 toward ~0.4 after 10 successful
interactions.

### call_service return value

`call_service` always returns a three-tuple:

| Position | Type    | Meaning |
|----------|---------|---------|
| `ok`     | `bool`  | `True` if the provider accepted and executed the call |
| `reason` | `str`   | Human-readable outcome string |
| `result` | `Any`   | The value returned by the handler, or `None` if denied |

### Querying trust

```python
# Trust score for this agent on the shared ledger (0.0–1.0)
score = buyer.trust_score

# Trust score of another agent as seen from buyer's ledger
peer_score = buyer.check_trust(seller.pubkey)

# Fraction of blocks in this agent's chain that are valid (0.0–1.0)
integrity = buyer.chain_integrity()

# Raw interaction count (number of blocks on the chain)
count = buyer.interaction_count
```

---

## Core Concepts

### TrustAgent

`TrustAgent` is the central object. Each instance:

- Holds an **Ed25519 identity** (keypair, generated or loaded from disk).
- Maintains a **bilateral ledger** of signed interaction records with every peer.
- Exposes **services** via the `@agent.service(name, min_trust)` decorator.
- Can call services on other agents with `await agent.call_service(...)`.
- Can export all services as a **FastMCP server** via `agent.as_mcp_server()`.

Constructor signature:

```python
TrustAgent(
    name: str,
    store: Optional[RecordStore] = None,      # in-memory if None
    identity_path: Optional[str] = None,      # persist key to disk
    store_path: Optional[str] = None,         # persist records to disk
    min_trust_threshold: float = 0.15,        # global default gate
    bootstrap_interactions: int = 3,          # free-pass window for new callers
    node=None,                                # v2 TrustChainNode (optional)
)
```

### TrustContext

Every service handler receives a `TrustContext` as its second argument:

```python
@agent.service("my_service", min_trust=0.2)
async def handler(data: dict, ctx: TrustContext) -> dict:
    print(ctx.caller_pubkey)    # hex Ed25519 public key of the caller
    print(ctx.caller_trust)     # float trust score of the caller
    print(ctx.caller_history)   # int number of past interactions with caller
    print(ctx.is_trusted)       # True if caller_trust > 0.0
    print(ctx.is_bootstrap)     # True if caller_history < bootstrap_interactions
    # Query trust for any third party:
    third_party_score = ctx.check_trust(some_pubkey)
    return {"ok": True}
```

### Bootstrap mode

The first `bootstrap_interactions` calls (default: 3) from any new caller are
always allowed regardless of their trust score. This lets new agents enter the
network without needing a pre-existing reputation. After the bootstrap window
closes, the `min_trust` threshold on each service is enforced.

### Pattern: TrustAgent + FrameworkAdapter

Every framework integration follows the same four-step pattern:

1. Create a **framework adapter** configured with your LLM provider and model.
2. Call `adapter.create_mcp_server()` to get a **FastMCP server** instance.
3. Inside a `@agent.service(...)` handler, call `await mcp.call_tool(tool_name, args)` to invoke the framework.
4. Wrap the whole thing in a `TrustAgent` so all inter-agent calls are
   bilaterally recorded.

The adapter's MCP server is a local in-process object — no network socket is
opened unless you explicitly call `mcp.run()`. This means `call_tool()` is a
direct async function call, which makes it fast and easy to test.

---

## Adding TrustChain to Your Framework

### 1. LangGraph

**Install:**

```bash
pip install langgraph                # base
pip install langchain-openai         # + OpenAI provider
pip install langchain-google-genai   # + Google Gemini provider
pip install langchain-anthropic      # + Anthropic provider
```

**Import:**

```python
from tc_frameworks.adapters.langgraph_adapter import LangGraphAdapter
```

**Constructor:**

```python
LangGraphAdapter(
    tools: list = [],                  # LangChain @tool-decorated callables
    model_name: str = "gpt-4o-mini",   # model identifier for the provider
    model_provider: str = "openai",    # "openai" | "anthropic" | "google"
    api_key: Optional[str] = None,     # passed as google_api_key for Google
)
```

**Supported providers:**

| Provider | `model_provider` | `model_name` | Install |
|----------|-----------------|--------------|---------|
| OpenAI | `"openai"` | `"gpt-4o-mini"` | `langchain-openai` |
| Anthropic | `"anthropic"` | `"claude-sonnet-4-20250514"` | `langchain-anthropic` |
| Google Gemini | `"google"` | `"gemini-2.5-flash"` | `langchain-google-genai` |

- `"openai"` uses `ChatOpenAI` from `langchain-openai`.
- `"anthropic"` uses `ChatAnthropic` from `langchain-anthropic`.
- `"google"` uses `ChatGoogleGenerativeAI` from `langchain-google-genai`.

**MCP tool name:** `react_agent_invoke` — takes `message: str`.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.langgraph_adapter import LangGraphAdapter

# Build the LangGraph ReAct agent adapter
adapter = LangGraphAdapter(
    model_name="gpt-4o-mini",
    model_provider="openai",
)
mcp = adapter.create_mcp_server()

# Wrap in a TrustAgent
researcher = TrustAgent(name="researcher")

@researcher.service("research", min_trust=0.0)
async def research_handler(data: dict, ctx: TrustContext) -> dict:
    topic = data.get("topic", "")
    result = await mcp.call_tool("react_agent_invoke", {"message": topic})
    return {"findings": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        researcher, "research", {"topic": "What is bilateral trust in AI agents?"}
    )
    print(f"Accepted: {ok}")
    print(f"Findings: {result['findings']}")
    print(f"Client trust score: {client.trust_score:.3f}")

asyncio.run(main())
```

---

### 2. CrewAI

**Install:**

```bash
pip install crewai
```

**Import:**

```python
from tc_frameworks.adapters.crewai_adapter import CrewAIAdapter
```

**Constructor:**

```python
CrewAIAdapter(
    crew_config: dict,                        # see schema below
    llm_model: str = "openai/gpt-4o-mini",   # LiteLLM routing string
    llm_base_url: Optional[str] = None,       # override API base URL
    llm_api_key: Optional[str] = None,        # API key for the LLM
)
```

**`crew_config` schema:**

```python
{
    "agents": [
        {
            "role": "Researcher",
            "goal": "Research topics thoroughly",
            "backstory": "Expert researcher with 10 years experience",
            "allow_delegation": False,   # optional, default False
        }
    ],
    "tasks": [
        {
            "description": "Research the topic: {message}",
            "expected_output": "A concise 3-point summary",
            "agent_role": "Researcher",  # must match an agent's role
        }
    ]
}
```

**Supported providers** (LiteLLM routing strings):

| Provider | `llm_model` | Environment variable |
|----------|------------|---------------------|
| OpenAI | `"openai/gpt-4o-mini"` | `OPENAI_API_KEY` |
| Anthropic | `"anthropic/claude-sonnet-4-20250514"` | `ANTHROPIC_API_KEY` |
| Google Gemini | `"gemini/gemini-2.5-flash"` | `GOOGLE_API_KEY` |

**MCP tool name:** `crew_kickoff` — takes `inputs: dict` (not a plain message
string). Pass your variables as dict keys that match `{placeholders}` in your
task descriptions.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.crewai_adapter import CrewAIAdapter

crew_config = {
    "agents": [
        {
            "role": "Analyst",
            "goal": "Analyze topics and produce clear insights",
            "backstory": "Senior analyst with expertise in emerging technology",
        }
    ],
    "tasks": [
        {
            "description": "Analyze the following topic: {message}. Provide 3 key insights.",
            "expected_output": "Three numbered insights, each 1-2 sentences.",
            "agent_role": "Analyst",
        }
    ]
}

adapter = CrewAIAdapter(
    crew_config=crew_config,
    llm_model="openai/gpt-4o-mini",  # default
)
mcp = adapter.create_mcp_server()

analyst = TrustAgent(name="analyst")

@analyst.service("analyze", min_trust=0.0)
async def analyze_handler(data: dict, ctx: TrustContext) -> dict:
    topic = data.get("topic", "")
    # crew_kickoff takes inputs: dict, not message: str
    result = await mcp.call_tool("crew_kickoff", {"inputs": {"message": topic}})
    return {"analysis": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        analyst, "analyze", {"topic": "Decentralized trust in AI systems"}
    )
    print(f"Analysis: {result['analysis']}")

asyncio.run(main())
```

---

### 3. AutoGen / AG2

**Install:**

```bash
# ag2 is the maintained fork of AutoGen
pip install "ag2[openai]"
```

**Import:**

```python
from tc_frameworks.adapters.autogen_adapter import AutoGenAdapter
```

**Constructor:**

```python
AutoGenAdapter(
    agents_config: list[dict] = [...],   # list of agent definitions
    llm_config: dict = {...},            # LLM configuration dict
)
```

**`agents_config` schema:**

```python
[
    {"name": "planner", "system_message": "You create concise plans."},
    {"name": "executor", "system_message": "You carry out plans."},
]
```

**Supported providers** (`llm_config` examples):

| Provider | `llm_config` | Environment variable |
|----------|-------------|---------------------|
| OpenAI | `{"model": "gpt-4o-mini", "api_key": "sk-..."}` | `OPENAI_API_KEY` |
| Google Gemini | `{"api_type": "google", "model": "gemini-2.5-flash", "api_key": "..."}` | `GOOGLE_API_KEY` |

For Gemini, the adapter uses `GeminiLLMConfigEntry` internally.

**MCP tool name:** `group_chat_run` — takes `message: str` and
`max_turns: int` (default 3).

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.autogen_adapter import AutoGenAdapter

adapter = AutoGenAdapter(
    agents_config=[
        {
            "name": "assistant",
            "system_message": (
                "You are a helpful assistant. Answer questions concisely "
                "in 2-3 sentences."
            ),
        }
    ],
    llm_config={"model": "gpt-4o-mini"},  # default (OpenAI)
)
mcp = adapter.create_mcp_server()

ag2_agent = TrustAgent(name="ag2-assistant")

@ag2_agent.service("chat", min_trust=0.0)
async def chat_handler(data: dict, ctx: TrustContext) -> dict:
    message = data.get("message", "")
    result = await mcp.call_tool(
        "group_chat_run", {"message": message, "max_turns": 2}
    )
    return {"response": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        ag2_agent, "chat", {"message": "Explain trust in 2 sentences."}
    )
    print(f"Response: {result['response']}")

asyncio.run(main())
```

---

### 4. OpenAI Agents SDK

**Install:**

```bash
pip install openai-agents
```

**Import:**

```python
from tc_frameworks.adapters.openai_agents_adapter import OpenAIAgentsAdapter
```

**Constructor:**

```python
OpenAIAgentsAdapter(
    agent_name: str = "assistant",
    instructions: str = "You are a helpful assistant.",
    tools: list = [],                # callables (wrapped with function_tool automatically)
    model: Any = "gpt-4o-mini",      # str model name OR OpenAIChatCompletionsModel object
)
```

The `model` parameter accepts either a plain string (for standard OpenAI
endpoints) or an `OpenAIChatCompletionsModel` object, which allows you to point
the agent at any OpenAI-compatible API.

**Supported providers:**

| Provider | `model` value | Notes |
|----------|--------------|-------|
| OpenAI | `"gpt-4o-mini"` (string) | Default — set `OPENAI_API_KEY` |
| Gemini via compat endpoint | `OpenAIChatCompletionsModel(model="gemini-2.5-flash", openai_client=client)` | Uses Gemini's OpenAI-compatible API |

**For Gemini via OpenAI-compatible endpoint:**

```python
import openai
from agents import OpenAIChatCompletionsModel

client = openai.AsyncOpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=os.environ["GEMINI_API_KEY"],
)
model = OpenAIChatCompletionsModel(model="gemini-2.5-flash", openai_client=client)
adapter = OpenAIAgentsAdapter(model=model, instructions="...")
```

**MCP tool name:** `agent_run` — takes `message: str`.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.openai_agents_adapter import OpenAIAgentsAdapter

adapter = OpenAIAgentsAdapter(
    agent_name="assistant",
    instructions="You are a helpful assistant. Be concise.",
    model="gpt-4o-mini",
)
mcp = adapter.create_mcp_server()

oai_agent = TrustAgent(name="oai-agent")

@oai_agent.service("ask", min_trust=0.0)
async def ask_handler(data: dict, ctx: TrustContext) -> dict:
    message = data.get("message", "")
    result = await mcp.call_tool("agent_run", {"message": message})
    return {"answer": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        oai_agent, "ask", {"message": "What is TrustChain?"}
    )
    print(f"Answer: {result['answer']}")

asyncio.run(main())
```

---

### 5. Google ADK

Google ADK is Gemini-native. It uses Google's Agent Development Kit and
requires a Google API key. Gemini is the only supported model backend for
this adapter.

**Install:**

```bash
pip install google-adk
```

**Set environment variable:**

```bash
export GOOGLE_API_KEY=your-gemini-api-key
```

**Import:**

```python
from tc_frameworks.adapters.google_adk_adapter import GoogleADKAdapter
```

**Constructor:**

```python
GoogleADKAdapter(
    agent_name: str = "assistant",
    model: str = "gemini-2.0-flash",
    instruction: str = "You are a helpful assistant.",
    tools: list = [],                   # ADK-compatible tool callables
)
```

The adapter uses `asyncio.Lock` to protect session initialization, making it
safe when multiple concurrent tool calls arrive before the session is ready.
Sessions persist across calls so conversation context is maintained within a
process lifetime.

**MCP tool name:** `adk_invoke` — takes `message: str`.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.google_adk_adapter import GoogleADKAdapter

# GOOGLE_API_KEY must be set in the environment
adapter = GoogleADKAdapter(
    agent_name="coordinator",
    model="gemini-2.5-flash",
    instruction=(
        "You are a project coordinator. Given a topic, create a clear "
        "3-step action plan. Be concise."
    ),
)
mcp = adapter.create_mcp_server()

coordinator = TrustAgent(name="coordinator")

@coordinator.service("plan", min_trust=0.0)
async def plan_handler(data: dict, ctx: TrustContext) -> dict:
    topic = data.get("topic", "")
    result = await mcp.call_tool("adk_invoke", {"message": f"Plan for: {topic}"})
    return {"plan": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        coordinator, "plan", {"topic": "Deploying agents with TrustChain"}
    )
    print(f"Plan:\n{result['plan']}")

asyncio.run(main())
```

---

### 6. Claude (Anthropic)

The Claude adapter is Anthropic-native. It uses the Anthropic SDK directly and
requires an Anthropic API key.

**Install:**

```bash
pip install anthropic
```

**Set environment variable:**

```bash
export ANTHROPIC_API_KEY=your-api-key
```

**Import:**

```python
from tc_frameworks.adapters.claude_agent_adapter import ClaudeAgentAdapter
```

**Constructor:**

```python
ClaudeAgentAdapter(
    model: str = "claude-sonnet-4-20250514",
    instructions: str = "You are a helpful assistant.",
    max_tokens: int = 1024,
    api_key: Optional[str] = None,    # falls back to ANTHROPIC_API_KEY env var
)
```

The adapter calls the synchronous Anthropic SDK inside `asyncio.to_thread()` so
it integrates cleanly with async code without blocking the event loop.

**MCP tool name:** `claude_query` — takes `message: str`.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.claude_agent_adapter import ClaudeAgentAdapter

adapter = ClaudeAgentAdapter(
    model="claude-sonnet-4-20250514",
    instructions=(
        "You are a technical writer. Write clear, structured summaries. "
        "Keep responses to 4-5 sentences."
    ),
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
)
mcp = adapter.create_mcp_server()

writer = TrustAgent(name="writer")

@writer.service("draft", min_trust=0.0)
async def draft_handler(data: dict, ctx: TrustContext) -> dict:
    content = data.get("content", "")
    prompt = f"Write an executive summary of:\n{content}"
    result = await mcp.call_tool("claude_query", {"message": prompt})
    return {"draft": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        writer, "draft", {"content": "TrustChain builds bilateral reputation between AI agents."}
    )
    print(f"Draft:\n{result['draft']}")

asyncio.run(main())
```

---

### 7. Smolagents (HuggingFace)

**Install:**

```bash
pip install smolagents              # base (HuggingFace Hub models)
pip install "smolagents[litellm]"   # + LiteLLM for OpenAI/Gemini/Anthropic routing
```

**Import:**

```python
from tc_frameworks.adapters.smolagents_adapter import SmolagentsAdapter
```

**Constructor:**

```python
SmolagentsAdapter(
    model_id: str = "Qwen/Qwen2.5-Coder-32B-Instruct",
    tools: list = [],
    agent_type: str = "code",           # "code" (CodeAgent) or "tool" (ToolCallingAgent)
    model_type: str = "hf",             # "hf" (HfApiModel) or "litellm" (LiteLLMModel)
    api_key: Optional[str] = None,      # HuggingFace token or LiteLLM provider key
)
```

**Supported providers:**

| Provider | `model_type` | `model_id` | Environment variable |
|----------|-------------|------------|---------------------|
| HuggingFace Hub | `"hf"` | `"Qwen/Qwen2.5-Coder-32B-Instruct"` | `HF_TOKEN` |
| OpenAI (via LiteLLM) | `"litellm"` | `"openai/gpt-4o"` | `OPENAI_API_KEY` |
| Google Gemini (via LiteLLM) | `"litellm"` | `"gemini/gemini-2.5-flash"` | `GOOGLE_API_KEY` |
| Anthropic (via LiteLLM) | `"litellm"` | `"anthropic/claude-sonnet-4-20250514"` | `ANTHROPIC_API_KEY` |

- `model_type="litellm"` routes through LiteLLM. Use LiteLLM model strings.
- `model_type="hf"` uses HuggingFace Inference API. Pass your HF token as
  `api_key`.
- The Smolagents `run()` method is synchronous; the adapter wraps it in
  `asyncio.to_thread()`.

**MCP tool name:** `smolagent_run` — takes `message: str`.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.smolagents_adapter import SmolagentsAdapter

adapter = SmolagentsAdapter(
    model_id="Qwen/Qwen2.5-Coder-32B-Instruct",
    model_type="hf",
    api_key=os.environ.get("HF_TOKEN"),
    agent_type="code",
)
mcp = adapter.create_mcp_server()

smol_agent = TrustAgent(name="smol-agent")

@smol_agent.service("run", min_trust=0.0)
async def run_handler(data: dict, ctx: TrustContext) -> dict:
    message = data.get("message", "")
    result = await mcp.call_tool("smolagent_run", {"message": message})
    return {"output": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        smol_agent, "run", {"message": "Write a Python function to compute Fibonacci numbers."}
    )
    print(f"Output:\n{result['output']}")

asyncio.run(main())
```

---

### 8. PydanticAI

**Install:**

```bash
pip install pydantic-ai
```

**Import:**

```python
from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter
```

**Constructor:**

```python
PydanticAIAdapter(
    model: str = "openai:gpt-4o-mini",   # provider:model-name string
    system_prompt: str = "You are a helpful assistant.",
    tools: list = [],                     # plain callables, registered via agent.tool_plain()
)
```

**Supported providers** (model string format):

| Provider | `model` string | Environment variable |
|----------|---------------|---------------------|
| OpenAI | `"openai:gpt-4o-mini"` | `OPENAI_API_KEY` |
| Google Gemini | `"google-gla:gemini-2.5-flash"` | `GEMINI_API_KEY` |
| Anthropic | `"anthropic:claude-sonnet-4-20250514"` | `ANTHROPIC_API_KEY` |

**MCP tool name:** `pydantic_ai_run` — takes `message: str`.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter

adapter = PydanticAIAdapter(
    model="openai:gpt-4o-mini",
    system_prompt=(
        "You are a data analyst. Identify patterns, draw conclusions, "
        "and make recommendations. Be concise: 3-4 sentences."
    ),
)
mcp = adapter.create_mcp_server()

analyst = TrustAgent(name="pydantic-analyst")

@analyst.service("analyze", min_trust=0.0)
async def analyze_handler(data: dict, ctx: TrustContext) -> dict:
    findings = data.get("findings", "")
    result = await mcp.call_tool(
        "pydantic_ai_run",
        {"message": f"Analyze these findings:\n{findings}"}
    )
    return {"analysis": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        analyst, "analyze",
        {"findings": "Agents with more interactions receive higher trust scores."}
    )
    print(f"Analysis: {result['analysis']}")

asyncio.run(main())
```

---

### 9. Semantic Kernel

**Install:**

```bash
pip install semantic-kernel
```

**Import:**

```python
from tc_frameworks.adapters.semantic_kernel_adapter import SemanticKernelAdapter
```

**Constructor:**

```python
SemanticKernelAdapter(
    service_id: str = "chat",
    model: str = "gpt-4o-mini",
    provider: str = "openai",          # "openai" or "google"
    plugins: list = [],                # SK plugin objects
    api_key: Optional[str] = None,
)
```

**Supported providers:**

| Provider | `provider` | `model` | Environment variable |
|----------|-----------|---------|---------------------|
| OpenAI | `"openai"` | `"gpt-4o-mini"` | `OPENAI_API_KEY` |
| Google Gemini | `"google"` | `"gemini-2.5-flash"` | `GOOGLE_API_KEY` |

- `provider="openai"` uses `OpenAIChatCompletion` with `ai_model_id`.
- `provider="google"` uses `GoogleAIChatCompletion` with `gemini_model_id`.
- The `kernel_invoke` tool calls `get_chat_message_contents()` with an
  explicit `PromptExecutionSettings` argument (required by SK).

**MCP tool name:** `kernel_invoke` — takes `message: str`.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.semantic_kernel_adapter import SemanticKernelAdapter

adapter = SemanticKernelAdapter(
    service_id="chat",
    model="gpt-4o-mini",
    provider="openai",
    api_key=os.environ.get("OPENAI_API_KEY"),
)
mcp = adapter.create_mcp_server()

reviewer = TrustAgent(name="sk-reviewer")

@reviewer.service("review", min_trust=0.0)
async def review_handler(data: dict, ctx: TrustContext) -> dict:
    draft = data.get("draft", "")
    prompt = (
        f"Review this draft for completeness and clarity. "
        f"Rate 1-10, give brief feedback:\n{draft}"
    )
    result = await mcp.call_tool("kernel_invoke", {"message": prompt})
    return {"review": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        reviewer, "review",
        {"draft": "TrustChain provides bilateral signed records for all agent interactions."}
    )
    print(f"Review: {result['review']}")

asyncio.run(main())
```

---

### 10. Agno (ex-Phidata)

**Install:**

```bash
pip install agno
```

**Import:**

```python
from tc_frameworks.adapters.agno_adapter import AgnoAdapter
```

**Constructor:**

```python
AgnoAdapter(
    agent_name: str = "assistant",
    model_provider: str = "openai",    # "openai" or "google"
    model_id: str = "gpt-4o-mini",
    instructions: str = "You are a helpful assistant.",
    tools: list = [],
    api_key: Optional[str] = None,
)
```

**Supported providers:**

| Provider | `model_provider` | `model_id` | Environment variable |
|----------|-----------------|------------|---------------------|
| OpenAI | `"openai"` | `"gpt-4o-mini"` | `OPENAI_API_KEY` |
| Google Gemini | `"google"` | `"gemini-2.5-flash"` | `GOOGLE_API_KEY` |

- `model_provider="openai"` uses `agno.models.openai.OpenAIChat`.
- `model_provider="google"` uses `agno.models.google.Gemini`.
- Agno's `.run()` is synchronous; the adapter calls it in `asyncio.to_thread()`.
- The `Agent` constructor receives `instructions` as a list (wrapped automatically).

**MCP tool name:** `agno_run` — takes `message: str`.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.agno_adapter import AgnoAdapter

adapter = AgnoAdapter(
    agent_name="agno-assistant",
    model_provider="openai",
    model_id="gpt-4o-mini",
    instructions="You are a helpful assistant. Respond concisely in 2-3 sentences.",
    api_key=os.environ.get("OPENAI_API_KEY"),
)
mcp = adapter.create_mcp_server()

agno_agent = TrustAgent(name="agno-agent")

@agno_agent.service("ask", min_trust=0.0)
async def ask_handler(data: dict, ctx: TrustContext) -> dict:
    message = data.get("message", "")
    result = await mcp.call_tool("agno_run", {"message": message})
    return {"response": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        agno_agent, "ask", {"message": "What makes a good trust primitive?"}
    )
    print(f"Response: {result['response']}")

asyncio.run(main())
```

---

### 11. LlamaIndex

**Install:**

```bash
pip install llama-index                    # base
pip install llama-index-llms-openai        # + OpenAI provider
pip install llama-index-llms-gemini        # + Google Gemini provider
```

**Import:**

```python
from tc_frameworks.adapters.llamaindex_adapter import LlamaIndexAdapter
```

**Constructor:**

```python
LlamaIndexAdapter(
    model: str = "gpt-4o-mini",
    provider: str = "openai",           # "openai" or "google"
    tools: list = [],                   # plain callables, wrapped as FunctionTool
    system_prompt: Optional[str] = None,
    api_key: Optional[str] = None,
)
```

**Supported providers:**

| Provider | `provider` | `model` | Install | Environment variable |
|----------|-----------|---------|---------|---------------------|
| OpenAI | `"openai"` | `"gpt-4o-mini"` | `llama-index-llms-openai` | `OPENAI_API_KEY` |
| Google Gemini | `"google"` | `"models/gemini-2.5-flash"` | `llama-index-llms-gemini` | `GOOGLE_API_KEY` |

- `provider="openai"` uses `llama_index.llms.openai.OpenAI`.
- `provider="google"` uses `llama_index.llms.gemini.Gemini`. The model string
  must use the `"models/"` prefix: `"models/gemini-2.5-flash"`.
- **Important:** `ReActAgent.from_tools()` was removed in LlamaIndex v0.14. The
  adapter uses the `ReActAgent()` constructor directly.

**MCP tool name:** `llamaindex_chat` — takes `message: str`.

**Full example:**

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.llamaindex_adapter import LlamaIndexAdapter

adapter = LlamaIndexAdapter(
    model="gpt-4o-mini",
    provider="openai",
    api_key=os.environ.get("OPENAI_API_KEY"),
    system_prompt="You are a helpful assistant. Answer concisely.",
)
mcp = adapter.create_mcp_server()

llama_agent = TrustAgent(name="llama-agent")

@llama_agent.service("chat", min_trust=0.0)
async def chat_handler(data: dict, ctx: TrustContext) -> dict:
    message = data.get("message", "")
    result = await mcp.call_tool("llamaindex_chat", {"message": message})
    return {"response": result.content[0].text}

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        llama_agent, "chat", {"message": "Describe the TrustChain sidecar model."}
    )
    print(f"Response: {result['response']}")

asyncio.run(main())
```

---

### 12. ElizaOS

ElizaOS is a TypeScript-first framework. The adapter bridges into it over its
REST API — no Python-native runtime is involved.

**Start ElizaOS (separate terminal):**

```bash
npm install -g @elizaos/cli
elizaos start
# ElizaOS runs at http://localhost:3000 by default
```

**Import:**

```python
from tc_frameworks.adapters.elizaos_adapter import ElizaOSAdapter
```

**Constructor:**

```python
ElizaOSAdapter(
    base_url: str = "http://localhost:3000",
    agent_id: Optional[str] = None,      # specific agent ID within ElizaOS
    server_id: str = "trustchain",       # server/channel identifier for messages
)
```

**MCP tool names:**

| Tool name            | Arguments                                              | Description |
|----------------------|--------------------------------------------------------|-------------|
| `eliza_send_message` | `content: str`, `room_id: str`, `user_id: str`        | Send a message to an ElizaOS agent via REST |
| `eliza_list_agents`  | (none)                                                 | List all agents in the running ElizaOS instance |

The adapter uses `httpx.AsyncClient` for all HTTP calls.

**Full example:**

```python
import asyncio
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.elizaos_adapter import ElizaOSAdapter

# Requires elizaos running at localhost:3000
adapter = ElizaOSAdapter(base_url="http://localhost:3000")
mcp = adapter.create_mcp_server()

eliza_bridge = TrustAgent(name="eliza-bridge")

@eliza_bridge.service("message", min_trust=0.0)
async def message_handler(data: dict, ctx: TrustContext) -> dict:
    content = data.get("content", "")
    room_id = data.get("room_id", "default")

    # List agents first
    agents_result = await mcp.call_tool("eliza_list_agents", {})
    agents_info = agents_result.content[0].text

    # Send the message
    send_result = await mcp.call_tool(
        "eliza_send_message",
        {
            "content": content,
            "room_id": room_id,
            "user_id": "trustchain-client",
        }
    )
    return {
        "sent": send_result.content[0].text,
        "agents": agents_info,
    }

client = TrustAgent(name="client")

async def main():
    ok, reason, result = await client.call_service(
        eliza_bridge, "message",
        {"content": "Hello from TrustChain!", "room_id": "general"}
    )
    if ok:
        print(f"Message sent: {result['sent']}")
        print(f"Available agents: {result['agents']}")
    else:
        print(f"Failed (is ElizaOS running?): {reason}")

asyncio.run(main())
```

---

## Trust-Gated Services

The `@agent.service(name, min_trust)` decorator enforces trust thresholds on
every incoming call. Combine multiple service tiers on a single agent to create
a progressive trust ladder.

### Tiered service example

```python
import asyncio
from agent_os import TrustAgent, TrustContext

# bootstrap_interactions=1 means only 1 free call per caller, then score-gated
provider = TrustAgent(name="provider", bootstrap_interactions=1)

@provider.service("basic", min_trust=0.0)
async def basic_handler(data: dict, ctx: TrustContext) -> dict:
    """Anyone can call this. Use it to build a track record."""
    return {"echo": data.get("msg", "hello")}

@provider.service("compute", min_trust=0.36)
async def compute_handler(data: dict, ctx: TrustContext) -> dict:
    """Requires an established track record (~10 successful basic calls)."""
    return {"result": data["x"] ** 2}

@provider.service("premium", min_trust=0.40)
async def premium_handler(data: dict, ctx: TrustContext) -> dict:
    """Requires deep trust history (~20+ successful interactions)."""
    return {"secret": "the answer is 42"}

caller = TrustAgent(name="new-caller")

async def main():
    print("Building trust on 'basic' tier...")
    for i in range(20):
        await caller.call_service(provider, "basic", {"msg": f"ping-{i}"})

    score = caller.check_trust(provider.pubkey)
    print(f"Trust score after 20 interactions: {score:.3f}")

    # Try each tier
    ok, reason, result = await caller.call_service(provider, "compute", {"x": 7})
    print(f"compute: {'OK' if ok else 'BLOCKED'} — {reason}")

    ok, reason, result = await caller.call_service(provider, "premium", {})
    print(f"premium: {'OK' if ok else 'BLOCKED'} — {reason}")

asyncio.run(main())
```

### How the gate works

When `call_service` is invoked:

1. The provider checks whether the caller is in bootstrap mode
   (`caller_history < bootstrap_interactions`).
2. If in bootstrap mode, the call is always allowed regardless of trust score.
3. If not in bootstrap mode and `caller_trust < min_trust`, the call is blocked.
   `call_service` returns `(False, "Trust gate denied...", None)`.
4. Bilateral interaction records are always written — including for denied calls
   (recorded as outcome `"denied"`).

### Using `TrustContext` for fine-grained control

Inside any handler you can inspect the caller and make runtime decisions:

```python
@provider.service("dynamic", min_trust=0.1)
async def dynamic_handler(data: dict, ctx: TrustContext) -> dict:
    # Is this caller brand new?
    if ctx.is_bootstrap:
        return {"mode": "onboarding", "data": limited_response(data)}

    # High-trust caller gets premium data
    if ctx.caller_trust >= 0.5:
        return {"mode": "premium", "data": full_response(data)}

    # Normal trusted caller
    return {"mode": "standard", "data": standard_response(data)}
```

### `bootstrap_interactions` notes

- Default value is `3`. Adjust per-agent via the `TrustAgent` constructor.
- Set it to `0` to disable bootstrap and enforce trust from the very first call.
- Set it to a high value (e.g. `100`) to build a long history before engaging
  gates — useful for warm-up phases in multi-agent demos.

---

## MCP Gateway Integration

The **MCP Gateway** is a FastMCP proxy that sits in front of one or more
upstream MCP servers. Every tool call is trust-gated and bilaterally recorded
automatically. The gateway exposes its own trust query tools
(`trustchain_check_trust`, `trustchain_list_servers`, etc.) so callers can
inspect the state of the trust ledger.

### create_gateway

```python
from gateway.config import GatewayConfig, UpstreamServer
from gateway.server import create_gateway

config = GatewayConfig(
    server_name="My TrustChain Gateway",

    # Persist the gateway's own Ed25519 identity across restarts
    identity_path="./data/gateway.key",

    # Persist interaction records across restarts
    store_path="./data/records.json",

    # Directory to persist identities for each upstream server
    upstream_identity_dir="./data/identities",

    # Default threshold (0.0 = allow all during bootstrap)
    default_trust_threshold=0.0,

    # Number of free-pass interactions for new upstreams
    bootstrap_interactions=3,

    upstreams=[
        # stdio-based MCP server (e.g. npx-launched)
        UpstreamServer(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            namespace="fs",
            trust_threshold=0.0,
        ),

        # HTTP-based MCP server
        UpstreamServer(
            name="my-api",
            url="http://localhost:3001/mcp",
            namespace="api",
            trust_threshold=0.3,
        ),
    ],
)

gateway = create_gateway(config)
gateway.run()  # starts as stdio MCP server
```

### create_gateway_from_dict

For configuration-file-driven deployments:

```python
from gateway.server import create_gateway_from_dict

config_dict = {
    "server_name": "Production TrustChain Gateway",
    "identity_path": "./data/gateway.key",
    "store_path": "./data/records.json",
    "upstream_identity_dir": "./data/identities",
    "default_trust_threshold": 0.0,
    "bootstrap_interactions": 3,
    "use_v2": False,
    "upstreams": [
        {
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "namespace": "fs",
            "trust_threshold": 0.0,
        },
        {
            "name": "code-interpreter",
            "url": "http://localhost:4000/mcp",
            "namespace": "code",
            "trust_threshold": 0.2,
        },
    ],
}

gateway = create_gateway_from_dict(config_dict)
gateway.run()
```

### Using the gateway as an MCP server for Claude Code

Create a `.mcp.json` file in your project root:

```json
{
    "mcpServers": {
        "trustchain-gateway": {
            "command": "python",
            "args": ["examples/run_gateway.py"]
        }
    }
}
```

Set optional environment variables for persistent storage:

```bash
export TRUSTCHAIN_STORE_PATH=./data/records.json
export TRUSTCHAIN_IDENTITY_PATH=./data/gateway.key
export TRUSTCHAIN_IDENTITY_DIR=./data/identities
```

### Gateway-native trust tools

The gateway registers these MCP tools automatically:

| Tool name                  | Description |
|----------------------------|-------------|
| `trustchain_list_servers`  | List all upstream servers and their trust scores |
| `trustchain_check_trust`   | Get trust score + threshold for a named server |
| `trustchain_get_history`   | Get recent interaction history for a server |
| `trustchain_verify_chain`  | Verify blockchain integrity for a server |
| `trustchain_trust_score`   | Get detailed trust breakdown (chain/netflow/statistical) |
| `trustchain_crawl`         | Detect tampering in a server's chain |

### Mounting a framework adapter behind the gateway

```python
from gateway.config import GatewayConfig, UpstreamServer
from gateway.server import create_gateway
from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter

# Build the adapter and run it as an HTTP MCP server on port 4000
adapter = PydanticAIAdapter(
    model="openai:gpt-4o-mini",
    system_prompt="You are a helpful assistant.",
)
adapter_mcp = adapter.create_mcp_server()
# adapter_mcp.run(transport="streamable-http", host="0.0.0.0", port=4000)

# Point the gateway at it
config = GatewayConfig(
    server_name="Gateway with PydanticAI backend",
    upstreams=[
        UpstreamServer(
            name="pydantic-backend",
            url="http://localhost:4000/mcp",
            namespace="ai",
            trust_threshold=0.0,
        )
    ],
)
gateway = create_gateway(config)
```

---

## Calling Adapters via MCP

All 12 adapters follow the same calling pattern. Call `create_mcp_server()` to
get a `FastMCP` instance, then use `await mcp.call_tool(name, args)`.

### Return value

`mcp.call_tool()` returns a `ToolResult` object. Access the text response via:

```python
result = await mcp.call_tool("tool_name", {"message": "..."})
text = result.content[0].text
```

If the tool returns nothing or the result is empty, `result.content` will be an
empty list — always guard with:

```python
text = result.content[0].text if result.content else "No response"
```

### Reference table

| Framework        | MCP Tool Name       | Arguments                               |
|------------------|---------------------|-----------------------------------------|
| LangGraph        | `react_agent_invoke`| `message: str`                          |
| CrewAI           | `crew_kickoff`      | `inputs: dict`                          |
| AutoGen / AG2    | `group_chat_run`    | `message: str`, `max_turns: int`        |
| OpenAI Agents    | `agent_run`         | `message: str`                          |
| Google ADK       | `adk_invoke`        | `message: str`                          |
| Claude           | `claude_query`      | `message: str`                          |
| Smolagents       | `smolagent_run`     | `message: str`                          |
| PydanticAI       | `pydantic_ai_run`   | `message: str`                          |
| Semantic Kernel  | `kernel_invoke`     | `message: str`                          |
| Agno             | `agno_run`          | `message: str`                          |
| LlamaIndex       | `llamaindex_chat`   | `message: str`                          |
| ElizaOS          | `eliza_send_message`| `content: str`, `room_id`, `user_id`   |
| ElizaOS          | `eliza_list_agents` | (none)                                  |

### CrewAI difference

CrewAI's `crew_kickoff` takes `inputs: dict` rather than `message: str`. Your
dict keys must match the `{placeholder}` names in your task descriptions:

```python
# Task description: "Research the topic: {topic} with angle: {angle}"
result = await mcp.call_tool("crew_kickoff", {
    "inputs": {"topic": "AI trust", "angle": "security implications"}
})
```

### Getting tool names programmatically

Every adapter has a `get_tool_names()` method:

```python
adapter = LangGraphAdapter(...)
names = adapter.get_tool_names()   # ["react_agent_invoke"]
tool_name = names[0]
result = await mcp.call_tool(tool_name, {"message": "..."})
```

---

## Common Patterns

### Pattern 1: Wrapping any adapter as a TrustAgent service

This is the canonical integration pattern used throughout the examples:

```python
import asyncio
from agent_os import TrustAgent, TrustContext

def make_trust_agent(name: str, adapter, tool_name: str) -> TrustAgent:
    """Create a TrustAgent whose service delegates to a framework adapter."""
    agent = TrustAgent(name=name)
    mcp_server = adapter.create_mcp_server()

    @agent.service("process", min_trust=0.0)
    async def handler(data: dict, ctx: TrustContext) -> dict:
        message = data.get("message", "")

        # Most adapters take message: str; CrewAI takes inputs: dict
        if tool_name == "crew_kickoff":
            args = {"inputs": {"message": message}}
        else:
            args = {"message": message}

        result = await mcp_server.call_tool(tool_name, args)
        text = result.content[0].text if result.content else "No response"
        return {"response": text, "framework": name}

    return agent

# Example usage:
from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter

adapter = PydanticAIAdapter(model="openai:gpt-4o-mini")
pydantic_agent = make_trust_agent("pydantic", adapter, "pydantic_ai_run")

client = TrustAgent(name="orchestrator")

async def main():
    ok, reason, result = await client.call_service(
        pydantic_agent, "process", {"message": "What is bilateral trust?"}
    )
    print(result["response"])

asyncio.run(main())
```

### Pattern 2: Multi-framework orchestration

Call different framework agents in sequence, passing output from one to the
next. Trust gates enforce quality — lower-tier agents must prove themselves
before their output is used by later stages.

```python
import asyncio
import os
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.google_adk_adapter import GoogleADKAdapter
from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter
from tc_frameworks.adapters.claude_agent_adapter import ClaudeAgentAdapter

# Build three specialized agents with different frameworks
# Google ADK is Gemini-native; PydanticAI uses OpenAI; Claude uses Anthropic
_planner_adapter  = GoogleADKAdapter(model="gemini-2.5-flash", instruction="Create concise 3-step plans.")
_planner_mcp      = _planner_adapter.create_mcp_server()

_analyst_adapter  = PydanticAIAdapter(model="openai:gpt-4o-mini", system_prompt="Analyze and find patterns.")
_analyst_mcp      = _analyst_adapter.create_mcp_server()

_writer_adapter   = ClaudeAgentAdapter(model="claude-sonnet-4-20250514", instructions="Write clear summaries.")
_writer_mcp       = _writer_adapter.create_mcp_server()

planner  = TrustAgent(name="planner")
analyst  = TrustAgent(name="analyst")
writer   = TrustAgent(name="writer")
pipeline = TrustAgent(name="pipeline")   # orchestrator

@planner.service("plan", min_trust=0.0)
async def plan_handler(data: dict, ctx: TrustContext) -> dict:
    topic = data.get("topic", "")
    result = await _planner_mcp.call_tool("adk_invoke", {"message": f"Plan for: {topic}"})
    return {"plan": result.content[0].text if result.content else ""}

@analyst.service("analyze", min_trust=0.2)
async def analyze_handler(data: dict, ctx: TrustContext) -> dict:
    plan = data.get("plan", "")
    result = await _analyst_mcp.call_tool("pydantic_ai_run", {"message": f"Analyze: {plan}"})
    return {"analysis": result.content[0].text if result.content else ""}

@writer.service("write", min_trust=0.2)
async def write_handler(data: dict, ctx: TrustContext) -> dict:
    analysis = data.get("analysis", "")
    result = await _writer_mcp.call_tool("claude_query", {"message": f"Summarize: {analysis}"})
    return {"summary": result.content[0].text if result.content else ""}

async def run_pipeline(topic: str):
    # Stage 1: Plan (always open)
    ok, _, plan_result = await pipeline.call_service(planner, "plan", {"topic": topic})
    plan = plan_result.get("plan", "") if plan_result else ""
    print(f"Plan: {plan[:100]}...")

    # Stage 2: Analyze (gated at 0.2 — needs track record)
    ok, reason, analysis_result = await pipeline.call_service(
        analyst, "analyze", {"plan": plan}
    )
    if not ok:
        print(f"Analysis blocked (building trust): {reason}")
        return
    analysis = analysis_result.get("analysis", "") if analysis_result else ""
    print(f"Analysis: {analysis[:100]}...")

    # Stage 3: Write (gated at 0.2)
    ok, reason, write_result = await pipeline.call_service(
        writer, "write", {"analysis": analysis}
    )
    if not ok:
        print(f"Writing blocked (building trust): {reason}")
        return
    summary = write_result.get("summary", "") if write_result else ""
    print(f"Summary: {summary[:100]}...")

async def main():
    topics = [
        "How bilateral trust enables secure agent economies",
        "Sybil resistance mechanisms in decentralized networks",
        "The future of agent-to-agent economic transactions",
        "TrustChain as universal trust primitive",
        "Comparing MCP, A2A, ACP agent protocols",
    ]
    for topic in topics:
        print(f"\nTopic: {topic}")
        print("-" * 60)
        await run_pipeline(topic)
        print(f"  pipeline trust scores — planner: {pipeline.check_trust(planner.pubkey):.3f}, "
              f"analyst: {pipeline.check_trust(analyst.pubkey):.3f}, "
              f"writer: {pipeline.check_trust(writer.pubkey):.3f}")

asyncio.run(main())
```

### Pattern 3: Trust gates on framework tools

Rather than gating at service registration time, you can inspect trust
dynamically inside a handler and vary the response:

```python
from agent_os import TrustAgent, TrustContext
from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter

_adapter = PydanticAIAdapter(model="openai:gpt-4o-mini")
_mcp = _adapter.create_mcp_server()

tiered_agent = TrustAgent(name="tiered-ai")

@tiered_agent.service("query", min_trust=0.0)
async def query_handler(data: dict, ctx: TrustContext) -> dict:
    message = data.get("message", "")

    # Brand-new callers get a canned response
    if ctx.is_bootstrap:
        return {
            "response": "Welcome! Build a track record to access full AI capabilities.",
            "tier": "onboarding",
        }

    # Low-trust callers get a simple, unconfigured model call
    if ctx.caller_trust < 0.3:
        result = await _mcp.call_tool("pydantic_ai_run", {"message": message})
        return {
            "response": result.content[0].text if result.content else "",
            "tier": "basic",
        }

    # High-trust callers get an enhanced prompt with additional context
    enhanced_message = f"[Premium context enabled]\n\n{message}"
    result = await _mcp.call_tool("pydantic_ai_run", {"message": enhanced_message})
    return {
        "response": result.content[0].text if result.content else "",
        "tier": "premium",
    }
```

### Pattern 4: Persisting identity across restarts

By default, `TrustAgent` generates a fresh keypair every time it is
instantiated. To keep the same public key across restarts (important for
reputation continuity), pass an `identity_path`:

```python
from agent_os import TrustAgent

# The key is created on first run and loaded on subsequent runs.
# Corrupt key files are detected and auto-regenerated.
agent = TrustAgent(
    name="my-agent",
    identity_path="./data/my-agent.key",
    store_path="./data/my-agent-records.json",
)
print(f"Agent pubkey (stable across restarts): {agent.pubkey}")
```

### Pattern 5: Exporting a TrustAgent as an MCP server

Expose all services of a `TrustAgent` as a proper MCP server that other Claude
Code instances or MCP clients can connect to:

```python
from agent_os import TrustAgent, TrustContext

agent = TrustAgent(name="my-service-agent", identity_path="./data/agent.key")

@agent.service("compute", min_trust=0.0)
async def compute(data: dict, ctx: TrustContext) -> dict:
    return {"result": data.get("x", 0) ** 2}

@agent.service("premium_compute", min_trust=0.4)
async def premium_compute(data: dict, ctx: TrustContext) -> dict:
    return {"result": sum(range(data.get("n", 10)))}

# Export as MCP server
mcp = agent.as_mcp_server(name="My TrustAgent MCP")

# Run as stdio (for Claude Code / MCP clients):
# mcp.run()

# Or as HTTP:
# mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
```

The exported MCP server includes a `trustchain_agent_info` tool automatically,
which reports the agent's public key, trust score, interaction count, chain
integrity, and registered services.
