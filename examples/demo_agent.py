"""Demo: Two TrustAgents interacting with trust building.

This demo shows:
1. Two agents start with zero trust
2. Trust builds through successful interactions
3. High-trust services become accessible as trust grows
4. Low-trust agents get rejected from premium services

Run:
    cd G:/Projects/blockchains/trustchain-agent-os
    python examples/demo_agent.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trustchain.store import RecordStore
from agent_os.agent import TrustAgent
from agent_os.context import TrustContext


async def main():
    print("=" * 60)
    print("  TrustChain Agent OS — Two-Agent Demo")
    print("=" * 60)
    print()

    # Shared store so both agents see the same records
    store = RecordStore()

    # Create two agents
    alice = TrustAgent(name="alice", store=store)
    bob = TrustAgent(name="bob", store=store)

    # Register services on Bob
    @bob.service("basic", min_trust=0.0)
    async def basic_service(data: dict, ctx: TrustContext) -> dict:
        """Basic computation — no trust required."""
        return {"result": sum(data.get("numbers", []))}

    @bob.service("compute", min_trust=0.3)
    async def compute_service(data: dict, ctx: TrustContext) -> dict:
        """Advanced compute — requires moderate trust."""
        return {"result": data.get("x", 0) ** 2}

    @bob.service("code_review", min_trust=0.6)
    async def review_service(data: dict, ctx: TrustContext) -> dict:
        """Code review — requires high trust."""
        return {"review": "LGTM", "approved": True}

    print(f"Alice: {alice.short_id}... (trust: {alice.trust_score:.3f})")
    print(f"Bob:   {bob.short_id}... (trust: {bob.trust_score:.3f})")
    print()

    # Phase 1: Bootstrap interactions
    print("--- Phase 1: Bootstrap (basic service) ---")
    print()
    for i in range(5):
        accepted, reason, result = await alice.call_service(
            bob, "basic", {"numbers": [1, 2, 3, i]}
        )
        print(
            f"  [{i+1}] basic: accepted={accepted} "
            f"result={result} "
            f"alice_trust={alice.trust_score:.3f} "
            f"bob_trust={bob.trust_score:.3f}"
        )

    print()
    print(f"After 5 interactions:")
    print(f"  Alice trust: {alice.trust_score:.3f}")
    print(f"  Bob trust:   {bob.trust_score:.3f}")
    print()

    # Phase 2: Try premium service
    print("--- Phase 2: Attempt premium services ---")
    print()

    # Try compute (min_trust=0.3)
    accepted, reason, result = await alice.call_service(
        bob, "compute", {"x": 7}
    )
    print(f"  compute (min=0.3): accepted={accepted} reason='{reason}'")
    if result:
        print(f"    result={result}")
    print()

    # Try code_review (min_trust=0.6) — likely too high still
    accepted, reason, result = await alice.call_service(
        bob, "code_review", {"code": "print('hello')"}
    )
    print(f"  code_review (min=0.6): accepted={accepted} reason='{reason}'")
    print()

    # Phase 3: Build more trust
    print("--- Phase 3: Building more trust ---")
    print()

    # Add a third agent to increase diversity
    carol = TrustAgent(name="carol", store=store)

    @carol.service("data", min_trust=0.0)
    async def data_service(data: dict, ctx: TrustContext) -> dict:
        return {"data": "fetched"}

    for i in range(5):
        await alice.call_service(carol, "data")
        await bob.call_service(carol, "data")

    print(f"  After interacting with Carol:")
    print(f"  Alice trust: {alice.trust_score:.3f} ({alice.interaction_count} interactions)")
    print(f"  Bob trust:   {bob.trust_score:.3f} ({bob.interaction_count} interactions)")
    print(f"  Carol trust: {carol.trust_score:.3f} ({carol.interaction_count} interactions)")
    print()

    # Try code_review again
    accepted, reason, result = await alice.call_service(
        bob, "code_review", {"code": "print('hello')"}
    )
    print(f"  code_review retry: accepted={accepted}")
    if result:
        print(f"    result={result}")

    print()

    # Phase 4: New untrusted agent
    print("--- Phase 4: Untrusted newcomer ---")
    print()

    eve = TrustAgent(name="eve", store=store, bootstrap_interactions=0)

    @eve.service("malicious", min_trust=0.0)
    async def malicious(data: dict, ctx: TrustContext) -> dict:
        return {"pwned": True}

    # Bob won't accept Eve for premium services
    bob_strict = TrustAgent(name="bob-strict", store=store, bootstrap_interactions=0)

    @bob_strict.service("premium", min_trust=0.5)
    async def premium(data: dict, ctx: TrustContext) -> dict:
        return {"premium": True}

    accepted, reason, result = await eve.call_service(bob_strict, "premium")
    print(f"  Eve -> Bob premium: accepted={accepted}")
    print(f"    reason: {reason}")
    print(f"    Eve trust: {eve.trust_score:.3f}")

    print()
    print("=" * 60)
    print("  Demo complete! Trust builds through bilateral interactions.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
