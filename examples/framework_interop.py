"""
Framework Interop Demo — All 12 framework adapters, one trust layer.

Every agent uses its REAL framework runtime with Gemini as the LLM backend.
TrustChain works identically regardless of which framework the agent uses.

This is the "USB-C of trust" demo: plug any framework in, trust works.

Run: GEMINI_API_KEY=... python examples/framework_interop.py
"""
import asyncio
import os
import sys

from agent_os import TrustAgent, TrustContext

# ── API setup ────────────────────────────────────────────────────────────────

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_KEY:
    print("Error: GEMINI_API_KEY not set.")
    print("  export GEMINI_API_KEY=your-key-here")
    sys.exit(1)

os.environ["GOOGLE_API_KEY"] = GEMINI_KEY

# Spread across models — free tier = 20 RPD per model per project
MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash-lite"]


def model_for(idx: int) -> str:
    return MODELS[idx % len(MODELS)]


# ── Build real framework agents ──────────────────────────────────────────────

def build_framework_agents() -> list[tuple[str, str, object]]:
    """Try to build each real framework agent."""
    results = []
    idx = 0

    def try_load(name, display, build_fn):
        nonlocal idx
        try:
            adapter = build_fn(idx)
            results.append((name, display, adapter))
            print(f"  {name:>17}: {display:<24s} LOADED  (model: {model_for(idx)})")
            idx += 1
        except Exception as e:
            print(f"  {name:>17}: {display:<24s} SKIP ({e})")

    # 1. LangGraph
    def build_langgraph(i):
        from tc_frameworks.adapters.langgraph_adapter import LangGraphAdapter
        return LangGraphAdapter(model_name=model_for(i), model_provider="google", api_key=GEMINI_KEY)
    try_load("langgraph", "LangGraph", build_langgraph)

    # 2. CrewAI
    def build_crewai(i):
        from tc_frameworks.adapters.crewai_adapter import CrewAIAdapter
        return CrewAIAdapter(
            crew_config={
                "agents": [{"role": "Assistant", "goal": "Help with tasks", "backstory": "Expert assistant"}],
                "tasks": [{"description": "{message}", "expected_output": "A helpful response", "agent_role": "Assistant"}],
            },
            llm_model=f"gemini/{model_for(i)}", llm_api_key=GEMINI_KEY,
        )
    try_load("crewai", "CrewAI", build_crewai)

    # 3. OpenAI Agents SDK (via Gemini OpenAI-compatible endpoint)
    def build_openai(i):
        from tc_frameworks.adapters.openai_agents_adapter import OpenAIAgentsAdapter
        import openai
        from agents import OpenAIChatCompletionsModel
        client = openai.AsyncOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=GEMINI_KEY,
        )
        oai_model = OpenAIChatCompletionsModel(model=model_for(i), openai_client=client)
        return OpenAIAgentsAdapter(
            agent_name="openai_gemini", model=oai_model,
            instructions="You are a helpful assistant. Respond concisely in 2-3 sentences.",
        )
    try_load("openai_agents", "OpenAI Agents SDK", build_openai)

    # 4. Google ADK
    def build_adk(i):
        from tc_frameworks.adapters.google_adk_adapter import GoogleADKAdapter
        return GoogleADKAdapter(
            agent_name="adk_agent", model=model_for(i),
            instruction="You are a helpful assistant. Respond concisely in 2-3 sentences.",
        )
    try_load("google_adk", "Google ADK", build_adk)

    # 5. AutoGen/AG2
    def build_autogen(i):
        from tc_frameworks.adapters.autogen_adapter import AutoGenAdapter
        return AutoGenAdapter(
            agents_config=[{"name": "assistant", "system_message": "You are a helpful assistant. Respond concisely."}],
            llm_config={"model": model_for(i), "api_type": "google", "api_key": GEMINI_KEY},
        )
    try_load("autogen", "AutoGen/AG2", build_autogen)

    # 6. Claude (Anthropic SDK — uses real Anthropic API, not Gemini)
    def build_claude(i):
        from tc_frameworks.adapters.claude_agent_adapter import ClaudeAgentAdapter
        return ClaudeAgentAdapter(
            model="claude-haiku-4-5-20251001",
            instructions="You are a helpful assistant. Respond concisely in 2-3 sentences.",
        )
    try_load("claude", "Claude (Anthropic)", build_claude)

    # 7. Smolagents
    def build_smolagents(i):
        from tc_frameworks.adapters.smolagents_adapter import SmolagentsAdapter
        return SmolagentsAdapter(
            model_id=f"gemini/{model_for(i)}", model_type="litellm", api_key=GEMINI_KEY,
        )
    try_load("smolagents", "Smolagents (HF)", build_smolagents)

    # 8. PydanticAI
    def build_pydantic(i):
        from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter
        return PydanticAIAdapter(
            model=f"google-gla:{model_for(i)}",
            system_prompt="You are a helpful assistant. Respond concisely in 2-3 sentences.",
        )
    try_load("pydantic_ai", "PydanticAI", build_pydantic)

    # 9. Semantic Kernel
    def build_sk(i):
        from tc_frameworks.adapters.semantic_kernel_adapter import SemanticKernelAdapter
        return SemanticKernelAdapter(
            service_id="chat", model=model_for(i), provider="google", api_key=GEMINI_KEY,
        )
    try_load("semantic_kernel", "Semantic Kernel", build_sk)

    # 10. Agno
    def build_agno(i):
        from tc_frameworks.adapters.agno_adapter import AgnoAdapter
        return AgnoAdapter(
            agent_name="agno_agent", model_provider="google",
            model_id=model_for(i), api_key=GEMINI_KEY,
            instructions="You are a helpful assistant. Respond concisely in 2-3 sentences.",
        )
    try_load("agno", "Agno", build_agno)

    # 11. LlamaIndex
    def build_llamaindex(i):
        from tc_frameworks.adapters.llamaindex_adapter import LlamaIndexAdapter
        return LlamaIndexAdapter(
            model=f"models/{model_for(i)}", provider="google", api_key=GEMINI_KEY,
            system_prompt="You are a helpful assistant. Respond concisely in 2-3 sentences.",
        )
    try_load("llamaindex", "LlamaIndex", build_llamaindex)

    # 12. ElizaOS (REST bridge — needs running ElizaOS server)
    print(f"  {'elizaos':>17}: {'ElizaOS':<24s} SKIP (needs running server)")

    return results


# ── Build TrustAgent wrappers ────────────────────────────────────────────────

def make_trust_agent(name: str, adapter) -> TrustAgent:
    """Create a TrustAgent whose service calls the real framework adapter."""
    agent = TrustAgent(name=name)
    mcp_server = adapter.create_mcp_server()
    tool_name = adapter.get_tool_names()[0]

    async def handler(data: dict, ctx: TrustContext) -> dict:
        message = data.get("message", "")
        if tool_name == "crew_kickoff":
            args = {"inputs": {"message": message}}
        else:
            args = {"message": message}
        result = await mcp_server.call_tool(tool_name, args)
        text = result.content[0].text if result.content else "No response"
        return {"response": text, "framework": name}

    agent.service("process", min_trust=0.0)(handler)
    return agent


# ── Main ─────────────────────────────────────────────────────────────────────

TASKS = [
    "How should AI agents establish trust with each other? Answer in 2 sentences.",
]


async def main():
    print("Framework Interop Demo — The USB-C of Trust")
    print("=" * 70)
    print(f"LLM: Gemini (spread across {len(MODELS)} models to manage quota)")
    print("Loading real framework adapters...")
    print()

    frameworks = build_framework_agents()
    print(f"\n{len(frameworks)} frameworks loaded.\n")

    if not frameworks:
        print("No frameworks available. Install at least one:")
        print("  pip install google-adk pydantic-ai agno langgraph")
        sys.exit(1)

    client = TrustAgent(name="client")
    agents: dict[str, tuple[TrustAgent, str]] = {}
    for fw_name, display_name, adapter in frameworks:
        agent = make_trust_agent(fw_name, adapter)
        agents[fw_name] = (agent, display_name)

    for task_idx, task in enumerate(TASKS, 1):
        print(f"Task {task_idx}: {task}")
        print("-" * 70)

        for fw_name, (agent, display_name) in agents.items():
            try:
                ok, reason, result = await client.call_service(
                    agent, "process", {"message": task}
                )
                if result:
                    resp = result.get("response", "")
                    trust = client.check_trust(agent.pubkey)
                    preview = resp.replace("\n", " ")[:80]
                    print(f"  {fw_name:>17} trust={trust:.3f}  {preview}...")
                else:
                    print(f"  {fw_name:>17} FAILED: {reason}")
            except Exception as e:
                print(f"  {fw_name:>17} ERROR: {e}")

        print()

    print("=" * 70)
    print("Final Trust Scoreboard — All Frameworks on One Ledger")
    print("=" * 70)

    scored = []
    for fw_name, (agent, display_name) in agents.items():
        trust = client.check_trust(agent.pubkey)
        interactions = agent.interaction_count
        scored.append((fw_name, display_name, trust, interactions))

    scored.sort(key=lambda x: -x[2])
    for rank, (fw_name, display_name, trust, interactions) in enumerate(scored, 1):
        bar = "#" * int(trust * 25)
        print(f"  #{rank:>2} {fw_name:>17} ({display_name:>20}): "
              f"{trust:.3f} {bar:<25} ({interactions} interactions)")

    print()
    print("Every agent used its REAL framework runtime with Gemini as the LLM.")
    print("Trust scores are framework-agnostic — same bilateral ledger for all.")
    print()
    print("TrustChain: the trust layer UNDERNEATH all agent frameworks.")


asyncio.run(main())
