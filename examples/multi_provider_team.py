"""
Multi-Provider Agent Team — Real framework agents collaborating through TrustChain.

Each agent uses a DIFFERENT framework runtime, all powered by Gemini:

  coordinator: Google ADK          → creates research plans
  researcher:  LangGraph           → gathers findings
  analyst:     PydanticAI          → analyzes patterns
  writer:      Anthropic Claude    → drafts summaries
  reviewer:    Semantic Kernel     → reviews quality

Trust gates enforce progressive unlocking:
  - Research: open (min_trust=0.0)
  - Analysis/Writing: needs trust (min_trust=0.2)
  - Review: needs established trust (min_trust=0.4)

Run: GEMINI_API_KEY=... ANTHROPIC_API_KEY=... python examples/multi_provider_team.py
     (ANTHROPIC_API_KEY is optional — writer falls back to Gemini via PydanticAI)
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
MODEL = "gemini-2.5-flash"

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ── Build real framework agents ──────────────────────────────────────────────

print("Multi-Provider Agent Team Demo")
print("=" * 70)
print(f"LLM backend: Gemini ({MODEL}) + Claude (if key available)")
print()
print("Loading framework agents...")

# 1. Coordinator — Google ADK
coordinator = TrustAgent(name="coordinator")
coordinator_fw = "Google ADK"
try:
    from tc_frameworks.adapters.google_adk_adapter import GoogleADKAdapter
    _coord_adapter = GoogleADKAdapter(
        agent_name="coordinator", model=MODEL,
        instruction="You are a project coordinator. Create brief, actionable research plans. "
                    "Keep responses to 3-4 sentences maximum.",
    )
    _coord_mcp = _coord_adapter.create_mcp_server()
    print(f"  coordinator:  Google ADK ........... LOADED")
except Exception as e:
    _coord_adapter = None
    print(f"  coordinator:  Google ADK ........... SKIP ({e})")

# 2. Researcher — LangGraph
researcher = TrustAgent(name="researcher")
researcher_fw = "LangGraph"
try:
    from tc_frameworks.adapters.langgraph_adapter import LangGraphAdapter
    _research_adapter = LangGraphAdapter(
        model_name=MODEL, model_provider="google", api_key=GEMINI_KEY,
    )
    _research_mcp = _research_adapter.create_mcp_server()
    print(f"  researcher:   LangGraph ............ LOADED")
except Exception as e:
    _research_adapter = None
    print(f"  researcher:   LangGraph ............ SKIP ({e})")

# 3. Analyst — PydanticAI
analyst = TrustAgent(name="analyst")
analyst_fw = "PydanticAI"
try:
    from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter
    _analyst_adapter = PydanticAIAdapter(
        model=f"google-gla:{MODEL}",
        system_prompt="You are a data analyst. Identify patterns, draw conclusions, "
                      "and make recommendations. Be concise: 3-4 sentences.",
    )
    _analyst_mcp = _analyst_adapter.create_mcp_server()
    print(f"  analyst:      PydanticAI ........... LOADED")
except Exception as e:
    _analyst_adapter = None
    print(f"  analyst:      PydanticAI ........... SKIP ({e})")

# 4. Writer — Anthropic Claude (falls back to PydanticAI+Gemini)
writer = TrustAgent(name="writer")
if ANTHROPIC_KEY:
    writer_fw = "Claude (Anthropic)"
    try:
        from tc_frameworks.adapters.claude_agent_adapter import ClaudeAgentAdapter
        _writer_adapter = ClaudeAgentAdapter(
            model="claude-haiku-4-5-20251001",
            instructions="You are a technical writer. Write clear, structured summaries. "
                         "Keep responses to 4-5 sentences maximum.",
            api_key=ANTHROPIC_KEY,
        )
        _writer_mcp = _writer_adapter.create_mcp_server()
        print(f"  writer:       Claude (Anthropic) ... LOADED")
    except Exception as e:
        _writer_adapter = None
        print(f"  writer:       Claude (Anthropic) ... SKIP ({e})")
else:
    writer_fw = "PydanticAI (Gemini)"
    try:
        _writer_adapter = PydanticAIAdapter(
            model=f"google-gla:{MODEL}",
            system_prompt="You are a technical writer. Write clear, structured summaries. "
                          "Keep responses to 4-5 sentences maximum.",
        )
        _writer_mcp = _writer_adapter.create_mcp_server()
        print(f"  writer:       PydanticAI (Gemini) .. LOADED (no ANTHROPIC_API_KEY)")
    except Exception as e:
        _writer_adapter = None
        print(f"  writer:       PydanticAI fallback .. SKIP ({e})")

# 5. Reviewer — Semantic Kernel
reviewer = TrustAgent(name="reviewer")
reviewer_fw = "Semantic Kernel"
try:
    from tc_frameworks.adapters.semantic_kernel_adapter import SemanticKernelAdapter
    _reviewer_adapter = SemanticKernelAdapter(
        service_id="chat", model=MODEL, provider="google", api_key=GEMINI_KEY,
    )
    _reviewer_mcp = _reviewer_adapter.create_mcp_server()
    print(f"  reviewer:     Semantic Kernel ...... LOADED")
except Exception as e:
    _reviewer_adapter = None
    print(f"  reviewer:     Semantic Kernel ...... SKIP ({e})")

AGENTS = [
    (coordinator, coordinator_fw),
    (researcher, researcher_fw),
    (analyst, analyst_fw),
    (writer, writer_fw),
    (reviewer, reviewer_fw),
]


# ── Helper: call adapter tool directly ───────────────────────────────────────

async def call_adapter(adapter, mcp_server, message: str) -> str:
    """Call the first tool on an adapter's MCP server."""
    tool_name = adapter.get_tool_names()[0]
    result = await mcp_server.call_tool(tool_name, {"message": message})
    return result.content[0].text if result.content else "No response"


# ── Service handlers (each calls a real framework) ───────────────────────────

@coordinator.service("plan", min_trust=0.0)
async def plan_handler(data: dict, ctx: TrustContext) -> dict:
    topic = data.get("topic", "")
    prompt = f"Create a brief 3-step research plan for: {topic}"
    if _coord_adapter:
        result = await call_adapter(_coord_adapter, _coord_mcp, prompt)
    else:
        result = f"Plan: 1. Research {topic} 2. Analyze findings 3. Summarize"
    return {"plan": result}


@researcher.service("research", min_trust=0.0)
async def research_handler(data: dict, ctx: TrustContext) -> dict:
    topic = data.get("topic", "")
    plan = data.get("plan", "")
    prompt = f"Research this topic: {topic}\nFollowing plan: {plan[:200]}\nProvide 3 key findings."
    if _research_adapter:
        result = await call_adapter(_research_adapter, _research_mcp, prompt)
    else:
        result = f"Findings on {topic}: requires framework to be installed"
    return {"findings": result}


@analyst.service("analyze", min_trust=0.2)
async def analyze_handler(data: dict, ctx: TrustContext) -> dict:
    findings = data.get("findings", "")
    prompt = f"Analyze these research findings and identify key patterns:\n{findings}"
    if _analyst_adapter:
        result = await call_adapter(_analyst_adapter, _analyst_mcp, prompt)
    else:
        result = f"Analysis: requires framework to be installed"
    return {"analysis": result}


@writer.service("draft", min_trust=0.2)
async def draft_handler(data: dict, ctx: TrustContext) -> dict:
    analysis = data.get("analysis", "")
    prompt = f"Write a structured executive summary based on:\n{analysis}"
    if _writer_adapter:
        result = await call_adapter(_writer_adapter, _writer_mcp, prompt)
    else:
        result = f"Draft: requires framework to be installed"
    return {"draft": result}


@reviewer.service("review", min_trust=0.4)
async def review_handler(data: dict, ctx: TrustContext) -> dict:
    draft = data.get("draft", "")
    prompt = (
        f"Evaluate this draft for completeness, accuracy, clarity. "
        f"Rate each 1-10, give feedback, end with APPROVE or REVISE:\n{draft}"
    )
    if _reviewer_adapter:
        result = await call_adapter(_reviewer_adapter, _reviewer_mcp, prompt)
    else:
        result = f"Review: requires framework to be installed"
    return {"review": result}


# ── Pipeline ─────────────────────────────────────────────────────────────────

TOPICS = [
    "How AI agents can collaborate across trust boundaries",
    "The role of bilateral ledgers in decentralized identity",
    "Comparing agent communication protocols: MCP, A2A, ACP",
    "Sybil resistance mechanisms for open agent networks",
    "The future of agent-to-agent economic transactions",
]


async def run_pipeline(topic: str, round_num: int):
    print(f"\n{'─' * 70}")
    print(f"Round {round_num}: {topic}")
    print(f"{'─' * 70}")

    # Step 1: Coordinator plans
    ok, reason, result = await coordinator.call_service(
        coordinator, "plan", {"topic": topic}
    )
    plan = result.get("plan", "") if result else ""
    print(f"  [coordinator → plan] {plan[:120]}...")

    # Step 2: Researcher investigates
    ok, reason, result = await coordinator.call_service(
        researcher, "research", {"topic": topic, "plan": plan}
    )
    findings = result.get("findings", "") if result else ""
    print(f"  [researcher → findings] {findings[:120]}...")

    # Step 3: Analyst (trust-gated at 0.2)
    ok, reason, result = await researcher.call_service(
        analyst, "analyze", {"findings": findings}
    )
    if result:
        analysis = result.get("analysis", "")
        print(f"  [analyst → analysis] {analysis[:120]}...")
    else:
        print(f"  [analyst → BLOCKED] {reason}")
        analysis = findings

    # Step 4: Writer (trust-gated at 0.2)
    ok, reason, result = await analyst.call_service(
        writer, "draft", {"analysis": analysis}
    )
    if result:
        draft = result.get("draft", "")
        print(f"  [writer → draft] {draft[:120]}...")
    else:
        print(f"  [writer → BLOCKED] {reason}")
        draft = analysis

    # Step 5: Reviewer (trust-gated at 0.4)
    ok, reason, result = await writer.call_service(
        reviewer, "review", {"draft": draft}
    )
    if result:
        review = result.get("review", "")
        print(f"  [reviewer → review] {review[:120]}...")
    else:
        print(f"  [reviewer → BLOCKED] {reason} (need more trust history)")

    # Trust snapshot
    print(f"\n  Trust snapshot:")
    for agent, fw in AGENTS:
        score = agent.trust_score
        interactions = agent.interaction_count
        print(f"    {agent._name:>12} ({fw:>20}): "
              f"score={score:.3f}  interactions={interactions}")


async def main():
    print()
    print("Trust gates: research=0.0, analysis/writing=0.2, review=0.4")
    print("Watch trust build and gates unlock across framework boundaries!")

    for i, topic in enumerate(TOPICS, 1):
        await run_pipeline(topic, i)

    print(f"\n{'=' * 70}")
    print("Final trust scores — every framework on the same ledger")
    print(f"{'=' * 70}")
    for agent, fw in AGENTS:
        score = agent.trust_score
        interactions = agent.interaction_count
        bar = "#" * int(score * 30)
        print(f"  {agent._name:>12} ({fw:>20}): "
              f"{score:.3f} {bar:<30} ({interactions} interactions)")

    print()
    print("5 different frameworks. 1 bilateral trust ledger.")
    print("TrustChain doesn't care which framework built the agent.")


asyncio.run(main())
