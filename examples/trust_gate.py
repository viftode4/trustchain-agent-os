"""
Trust gate enforcement: a tiered service that blocks low-trust callers.

Three service tiers:
  - basic    min_trust=0.0  -- anyone can call
  - compute  min_trust=0.36 -- needs track record
  - premium  min_trust=0.40 -- only established agents

A new caller gets 1 bootstrap interaction (free pass). After that,
every call is score-gated. The caller builds rep on "basic" until
trust is high enough to unlock higher tiers.

Run: python examples/trust_gate.py
"""
import asyncio
from agent_os import TrustAgent, TrustContext

# Provider: 1-round bootstrap window (just 1 free call per service)
provider = TrustAgent(name="provider", bootstrap_interactions=1)


@provider.service("basic", min_trust=0.0)
async def basic_handler(data: dict, ctx: TrustContext) -> dict:
    return {"echo": data.get("msg", "hello")}


@provider.service("compute", min_trust=0.36)
async def compute_handler(data: dict, ctx: TrustContext) -> dict:
    return {"result": data["x"] ** 2}


@provider.service("premium", min_trust=0.40)
async def premium_handler(data: dict, ctx: TrustContext) -> dict:
    return {"secret": "the answer is 42"}


caller = TrustAgent(name="new-agent")


async def probe(svc: str, data: dict) -> str:
    ok, reason, result = await caller.call_service(provider, svc, data)
    return "OK" if result is not None else "BLOCKED"


async def main():
    print("Trust Gate Enforcement Demo")
    print("=" * 62)
    print("Tiers:  basic(0.0)  compute(0.36)  premium(0.40)")
    print("Bootstrap: 1 free call per service, then score-gated")
    print()
    print(f"{'Rnd':>3}  {'Score':>7}  {'basic':>8}  {'compute':>9}  {'premium':>9}  note")
    print("-" * 62)

    unlocked_compute = False
    unlocked_premium = False

    for i in range(1, 31):
        # Build reputation on basic (always works)
        await caller.call_service(provider, "basic", {"msg": f"ping-{i}"})
        score = caller.check_trust(provider.pubkey)

        # Probe higher tiers
        c = await probe("compute", {"x": i})
        p = await probe("premium", {"x": i})

        note = ""
        if not unlocked_compute and c == "OK":
            unlocked_compute = True
            note = "<< compute gate open"
        if not unlocked_premium and p == "OK":
            unlocked_premium = True
            note = "<< PREMIUM gate open"

        print(f"{i:>3}  {score:>7.3f}  {'OK':>8}  {c:>9}  {p:>9}  {note}")

    print()
    final = caller.check_trust(provider.pubkey)
    print(f"Final score: {final:.3f}  |  interactions: {caller.interaction_count}")
    print(f"compute unlocked: {unlocked_compute}  |  premium unlocked: {unlocked_premium}")
    print()
    print("Trust is the passport. No API keys, no allowlists.")
    print("The bilateral ledger decides who gets in.")


asyncio.run(main())
