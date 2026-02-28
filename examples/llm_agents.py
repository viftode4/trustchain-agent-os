"""
LLM-powered agents with automatic trust recording.

Two Claude-backed agents: a "researcher" and a "coder".
- researcher asks questions (calls coder's service)
- coder answers using Claude
- every interaction is recorded on the bilateral ledger
- trust grows (or drops) based on answer quality

The researcher also has a trust-gated "critique" service that the coder
can only call after building enough reputation.

Run: ANTHROPIC_API_KEY=sk-... python examples/llm_agents.py
"""
import asyncio
import os
import sys

import anthropic

from agent_os import TrustAgent, TrustContext

# ── Setup ────────────────────────────────────────────────────────────────────

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("Error: ANTHROPIC_API_KEY not set.")
    print("  export ANTHROPIC_API_KEY=sk-ant-...")
    sys.exit(1)

claude = anthropic.Anthropic(api_key=api_key)

researcher = TrustAgent(name="researcher")
coder      = TrustAgent(name="coder")

TASKS = [
    "Write a one-line Python function that returns the nth Fibonacci number.",
    "What is the time complexity of merge sort? One sentence.",
    "Give me a Python one-liner that flattens a nested list.",
    "Explain async/await in Python in two sentences.",
    "Write a Python function to check if a string is a palindrome.",
    "What is a hash collision and why does it matter?",
    "Give me a decorator that measures function execution time.",
    "What is the difference between a mutex and a semaphore?",
]


# ── Coder service (calls Claude) ─────────────────────────────────────────────

@coder.service("answer", min_trust=0.0)
async def answer_handler(data: dict, ctx: TrustContext) -> dict:
    question = data.get("question", "")
    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": f"Answer concisely (2-3 sentences max):\n\n{question}",
            }
        ],
    )
    return {"answer": response.content[0].text.strip()}


# ── Researcher critique service (trust-gated at 0.4) ─────────────────────────

@researcher.service("critique", min_trust=0.4)
async def critique_handler(data: dict, ctx: TrustContext) -> dict:
    answer = data.get("answer", "")
    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=128,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Rate this technical answer 1-10 and say why in one sentence:\n\n{answer}"
                ),
            }
        ],
    )
    return {"critique": response.content[0].text.strip()}


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print("LLM Agent Trust Demo")
    print("=" * 65)
    print("researcher asks → coder answers (via Claude)")
    print("trust recorded on every exchange")
    print()

    critique_unlocked = False

    for i, task in enumerate(TASKS, 1):
        print(f"Round {i}: {task[:55]}...")

        # researcher calls coder's answer service
        ok, reason, result = await researcher.call_service(
            coder, "answer", {"question": task}
        )

        score_researcher = researcher.trust_score
        score_coder = coder.trust_score

        if result:
            answer = result.get("answer", "")
            print(f"  Answer: {answer[:120]}{'...' if len(answer) > 120 else ''}")
        else:
            print(f"  FAILED: {reason}")

        print(f"  researcher={score_researcher:.3f}  coder={score_coder:.3f}")

        # Try the trust-gated critique service
        coder_trust_at_researcher = coder.check_trust(researcher.pubkey)
        if coder_trust_at_researcher >= 0.4 and not critique_unlocked:
            critique_ok, critique_reason, critique_result = await coder.call_service(
                researcher, "critique", {"answer": result.get("answer", "") if result else ""}
            )
            if critique_result:
                critique_unlocked = True
                print(f"  [CRITIQUE UNLOCKED] {critique_result.get('critique', '')[:100]}")

        print()

    print("=" * 65)
    print("Final trust scores")
    print(f"  researcher: {researcher.trust_score:.3f}  "
          f"({researcher.interaction_count} interactions)")
    print(f"  coder:      {coder.trust_score:.3f}  "
          f"({coder.interaction_count} interactions)")
    print(f"  critique gate unlocked: {critique_unlocked}")
    print()
    print("Every Claude API call was recorded on the bilateral ledger.")
    print("Trust is built automatically — no manual scoring.")


asyncio.run(main())
