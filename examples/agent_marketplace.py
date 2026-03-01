"""
Agent Marketplace — Competing framework agents, trust-based routing.

A client sends coding tasks to competing agents, each using a DIFFERENT
real framework runtime with Gemini as the LLM. Trust scores determine routing:

  - gemini_coder:  Google ADK agent (reliable expert persona)
  - langgraph_coder: LangGraph agent (systematic but sometimes verbose)
  - pydantic_coder: PydanticAI agent (precise, structured)
  - sloppy_coder:  Agno agent (careless persona, makes mistakes)
  - sybil_coder:   Smolagents agent (attacker — intentionally wrong code)

Phases:
  1. Exploration — round-robin, build trust history
  2. Exploitation — route to top-2 by trust
  3. Sybil attack — sybil floods with bad results
  4. Detection — trust reveals the attacker

Run: GEMINI_API_KEY=... python examples/agent_marketplace.py
"""
import asyncio
import os
import sys

from agent_os import TrustAgent, TrustContext

# ── Setup ────────────────────────────────────────────────────────────────────

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_KEY:
    print("Error: GEMINI_API_KEY not set.")
    print("  export GEMINI_API_KEY=your-key-here")
    sys.exit(1)

os.environ["GOOGLE_API_KEY"] = GEMINI_KEY
MODEL = "gemini-2.5-flash"

# ── Build framework agents with distinct personas ────────────────────────────

client = TrustAgent(name="client")

# Verifier agent — uses PydanticAI to check code quality
_verifier_adapter = None
try:
    from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter
    _verifier_adapter = PydanticAIAdapter(
        model=f"google-gla:{MODEL}",
        system_prompt=(
            "You are a code reviewer. You receive a task and a code solution. "
            "Determine if the code correctly solves the task. "
            "Answer with exactly 'CORRECT' or 'INCORRECT' on the first line, "
            "then one sentence explaining why."
        ),
    )
    _verifier_mcp = _verifier_adapter.create_mcp_server()
except Exception:
    pass


async def verify_code(task: str, code: str) -> tuple[bool, str]:
    """Use a real LLM to verify code quality."""
    if _verifier_adapter is None:
        return True, "verifier not loaded"
    prompt = f"Task: {task}\n\nCode:\n{code}\n\nIs this correct?"
    tool_name = _verifier_adapter.get_tool_names()[0]
    result = await _verifier_mcp.call_tool(tool_name, {"message": prompt})
    text = result.content[0].text if result.content else ""
    correct = text.upper().startswith("CORRECT")
    return correct, text[:100]


# ── Coder agents ─────────────────────────────────────────────────────────────

CODERS: list[tuple[str, str, str, object | None, object | None, bool]] = []
coders: dict[str, TrustAgent] = {}

print("Agent Marketplace Demo")
print("=" * 70)
print(f"LLM backend: Gemini ({MODEL}) for all agents")
print()
print("Loading framework agents...")


def register_coder(name, framework, persona, adapter, mcp, is_sybil=False):
    """Register a coder agent with its framework adapter."""
    agent = TrustAgent(name=name)
    coders[name] = agent
    CODERS.append((name, framework, persona, adapter, mcp, is_sybil))

    async def call_adapter_tool(message):
        if adapter is None:
            return f"# {name}: framework not loaded"
        tool_name = adapter.get_tool_names()[0]
        result = await mcp.call_tool(tool_name, {"message": message})
        return result.content[0].text if result.content else f"# {name}: no response"

    async def code_handler(data: dict, ctx: TrustContext) -> dict:
        task = data.get("task", "")
        prompt = f"{persona}\n\nTask: Write a Python function for: {task}"
        code = await call_adapter_tool(prompt)
        return {"code": code, "agent": name}

    agent.service("code", min_trust=0.0)(code_handler)
    return agent


# 1. Google ADK — reliable expert
try:
    from tc_frameworks.adapters.google_adk_adapter import GoogleADKAdapter
    _a = GoogleADKAdapter(
        agent_name="gemini_coder", model=MODEL,
        instruction="You are an expert Python coder. Write correct, clean, concise code. "
                    "Just the code, no explanation. Max 8 lines.",
    )
    _m = _a.create_mcp_server()
    register_coder("gemini_coder", "Google ADK", "Expert coder", _a, _m)
    print(f"  gemini_coder:    Google ADK ........... LOADED")
except Exception as e:
    register_coder("gemini_coder", "Google ADK", "Expert coder", None, None)
    print(f"  gemini_coder:    Google ADK ........... SKIP ({e})")

# 2. LangGraph — systematic
try:
    from tc_frameworks.adapters.langgraph_adapter import LangGraphAdapter
    _a = LangGraphAdapter(model_name=MODEL, model_provider="google", api_key=GEMINI_KEY)
    _m = _a.create_mcp_server()
    register_coder("langgraph_coder", "LangGraph", "Systematic coder", _a, _m)
    print(f"  langgraph_coder: LangGraph ............ LOADED")
except Exception as e:
    register_coder("langgraph_coder", "LangGraph", "Systematic coder", None, None)
    print(f"  langgraph_coder: LangGraph ............ SKIP ({e})")

# 3. PydanticAI — precise
try:
    _a = PydanticAIAdapter(
        model=f"google-gla:{MODEL}",
        system_prompt="You are a precise Python coder. Write correct, well-typed code. "
                      "Just the code, no explanation. Max 8 lines.",
    )
    _m = _a.create_mcp_server()
    register_coder("pydantic_coder", "PydanticAI", "Precise coder", _a, _m)
    print(f"  pydantic_coder:  PydanticAI ........... LOADED")
except Exception as e:
    register_coder("pydantic_coder", "PydanticAI", "Precise coder", None, None)
    print(f"  pydantic_coder:  PydanticAI ........... SKIP ({e})")

# 4. Agno — sloppy persona
try:
    from tc_frameworks.adapters.agno_adapter import AgnoAdapter
    _a = AgnoAdapter(
        agent_name="sloppy", model_provider="google",
        model_id=MODEL, api_key=GEMINI_KEY,
        instructions="You are a rushed coder who often makes mistakes. Write code that looks "
                     "plausible but may have subtle bugs like off-by-one errors, wrong edge cases, "
                     "or missing base cases. Just code, no explanation.",
    )
    _m = _a.create_mcp_server()
    register_coder("sloppy_coder", "Agno", "Sloppy coder", _a, _m)
    print(f"  sloppy_coder:    Agno ................. LOADED")
except Exception as e:
    register_coder("sloppy_coder", "Agno", "Sloppy coder", None, None)
    print(f"  sloppy_coder:    Agno ................. SKIP ({e})")

# 5. Smolagents — sybil attacker
try:
    from tc_frameworks.adapters.smolagents_adapter import SmolagentsAdapter
    _a = SmolagentsAdapter(
        model_id=f"gemini/{MODEL}", model_type="litellm", api_key=GEMINI_KEY,
        agent_type="tool_calling",
    )
    _m = _a.create_mcp_server()
    register_coder("sybil_coder", "Smolagents", "Sybil attacker", _a, _m, is_sybil=True)
    print(f"  sybil_coder:     Smolagents ........... LOADED [SYBIL]")
except Exception as e:
    register_coder("sybil_coder", "Smolagents", "Sybil attacker", None, None, is_sybil=True)
    print(f"  sybil_coder:     Smolagents ........... SKIP ({e})")

# Override sybil's handler to always produce wrong code
_sybil_agent = coders["sybil_coder"]

@_sybil_agent.service("code", min_trust=0.0)
async def sybil_handler(data: dict, ctx: TrustContext) -> dict:
    task = data.get("task", "")
    # Sybil claims success but delivers intentionally wrong code
    return {
        "code": f"def solve():\n    # {task}\n    return None  # TODO: implement",
        "agent": "sybil_coder",
    }


# ── Tasks ────────────────────────────────────────────────────────────────────

TASKS = [
    "fibonacci sequence (iterative)",
    "binary search in sorted list",
    "reverse a singly linked list",
    "merge sort",
    "validate email with regex",
    "simple rate limiter",
    "LRU cache with O(1) ops",
    "trie insert and search",
    "topological sort (DFS)",
    "check if binary tree is balanced",
    "longest palindromic substring",
    "implement min-heap",
    "detect cycle in linked list",
    "find kth largest element",
    "matrix spiral traversal",
]


def trust_scores() -> dict[str, float]:
    return {name: client.check_trust(agent.pubkey) for name, agent in coders.items()}


def print_scores(label: str = ""):
    sc = trust_scores()
    if label:
        print(f"\n  {label}")
    for name, fw, _, _, _, is_sybil in CODERS:
        score = sc[name]
        bar = "#" * int(score * 25)
        tag = " [SYBIL]" if is_sybil else ""
        print(f"    {name:>17} ({fw:>12}): {score:.3f} {bar:<25}{tag}")


# ── Main ─────────────────────────────────────────────────────────────────────

EXPLORE_ROUNDS = 10
EXPLOIT_ROUNDS = 15
SYBIL_ROUNDS = 10


async def main():
    print()

    # ── Phase 1: Exploration ─────────────────────────────────────────────────
    print(f"Phase 1: Exploration ({EXPLORE_ROUNDS} rounds, round-robin)")
    print("-" * 70)

    coder_list = list(coders.items())
    for i in range(EXPLORE_ROUNDS):
        name, agent = coder_list[i % len(coder_list)]
        task = TASKS[i % len(TASKS)]
        ok, reason, result = await client.call_service(agent, "code", {"task": task})
        if result:
            code = result.get("code", "")
            correct, verdict = await verify_code(task, code)
            status = "GOOD" if correct else "BAD"
            print(f"  Round {i+1:>2}: {name:>17} → {status:>4}  {task}")
        else:
            print(f"  Round {i+1:>2}: {name:>17} → FAIL  {task}")

    print_scores("Trust after exploration:")

    # ── Phase 2: Exploitation ────────────────────────────────────────────────
    sc = trust_scores()
    top2 = sorted(sc.items(), key=lambda x: -x[1])[:2]
    top2_names = [n for n, _ in top2]
    print(f"\nPhase 2: Exploitation ({EXPLOIT_ROUNDS} rounds, top-2: {top2_names})")
    print("-" * 70)

    for i in range(EXPLOIT_ROUNDS):
        name = max(top2_names, key=lambda n: client.check_trust(coders[n].pubkey))
        agent = coders[name]
        task = TASKS[(EXPLORE_ROUNDS + i) % len(TASKS)]
        ok, reason, result = await client.call_service(agent, "code", {"task": task})
        if result:
            code = result.get("code", "")
            correct, verdict = await verify_code(task, code)
            status = "GOOD" if correct else "BAD"
        else:
            status = "FAIL"
        if i % 5 == 0:
            print(f"  Round {i+1:>2}: {name:>17} → {status:>4}  {task}")

    print_scores("Trust after exploitation:")

    # ── Phase 3: Sybil attack ────────────────────────────────────────────────
    print(f"\nPhase 3: Sybil Attack ({SYBIL_ROUNDS} rounds)")
    print("-" * 70)
    print("  sybil_coder floods with plausible-looking but wrong code...")

    sybil = coders["sybil_coder"]
    for i in range(SYBIL_ROUNDS):
        task = TASKS[i % len(TASKS)]
        ok, reason, result = await client.call_service(sybil, "code", {"task": task})
        if result and i < 3:
            code = result.get("code", "")
            correct, verdict = await verify_code(task, code)
            status = "GOOD" if correct else "BAD"
            print(f"  Sybil round {i+1}: {status}  {verdict[:60]}")

    print_scores("Trust after Sybil attack:")

    # ── Phase 4: Detection ───────────────────────────────────────────────────
    print(f"\nPhase 4: Trust-Based Sybil Detection")
    print("-" * 70)
    sc = trust_scores()

    ranked = sorted(sc.items(), key=lambda x: -x[1])
    print("  Final trust ranking:")
    for rank, (name, score) in enumerate(ranked, 1):
        is_sybil = any(n == name and s for n, _, _, _, _, s in CODERS)
        fw = next(f for n, f, _, _, _, _ in CODERS if n == name)
        bar = "#" * int(score * 25)
        verdict = " ← SYBIL" if is_sybil else ""
        print(f"    #{rank} {name:>17} ({fw:>12}): {score:.3f} {bar:<25}{verdict}")

    print()
    print("  Each agent used a DIFFERENT framework runtime.")
    print("  Trust is earned through bilateral interaction history.")
    print("  Sybil agents can't inflate scores — every interaction is co-signed.")


asyncio.run(main())
