"""
Multi-agent trust network: 6 agents, 200 interactions, watch the graph evolve.

Agents:
  alice, bob, carol  — honest (100% reliability)
  dave, eve          — mediocre (50% reliability)
  mallory            — Sybil / bad actor (0% reliability)

All agents call all others at random. Over time the trust graph separates:
honest agents float to the top, bad actors sink to zero.

Run: python examples/network.py
"""
import asyncio
import random
from agent_os import TrustAgent, TrustContext

random.seed(7)

# ── Agent definitions ────────────────────────────────────────────────────────

PROFILES = {
    "alice":   1.00,
    "bob":     1.00,
    "carol":   1.00,
    "dave":    0.50,
    "eve":     0.50,
    "mallory": 0.00,
}

agents: dict[str, TrustAgent] = {}

for agent_name, reliability in PROFILES.items():
    a = TrustAgent(name=agent_name)
    agents[agent_name] = a

    def make_handler(rel: float):
        async def svc(data: dict, ctx: TrustContext) -> dict:
            if random.random() > rel:
                raise RuntimeError("unavailable")
            return {"ok": True}
        return svc

    a.service("work", min_trust=0.0)(make_handler(reliability))


# ── Helpers ──────────────────────────────────────────────────────────────────

def all_scores() -> dict[str, float]:
    """Each agent's self-assessed trust score."""
    return {name: a.trust_score for name, a in agents.items()}


def print_scores(label: str):
    sc = all_scores()
    print(f"\n{label}")
    print(f"  {'Agent':>8}  {'Score':>7}  {'Bar':<24}  Profile")
    print(f"  {'-'*8}  {'-'*7}  {'-'*24}  {'-'*15}")
    for name, score in sorted(sc.items(), key=lambda x: -x[1]):
        bar = "#" * int(score * 24)
        rel = PROFILES[name]
        tier = "honest" if rel == 1.0 else ("mediocre" if rel == 0.5 else "Sybil")
        print(f"  {name:>8}  {score:>7.3f}  {bar:<24}  {tier} ({rel*100:.0f}%)")


async def random_interaction():
    """Pick two random distinct agents and have one call the other."""
    names = list(agents.keys())
    caller_name = random.choice(names)
    provider_name = random.choice([n for n in names if n != caller_name])
    caller = agents[caller_name]
    provider = agents[provider_name]
    await caller.call_service(provider, "work", {})


# ── Main ─────────────────────────────────────────────────────────────────────

TOTAL_ROUNDS = 200
SNAPSHOT_AT = [1, 10, 50, 100, 200]


async def main():
    print("Multi-Agent Trust Network")
    print("=" * 60)
    print(f"Agents: {', '.join(PROFILES.keys())}")
    print(f"Running {TOTAL_ROUNDS} random interactions...")

    print_scores("Initial state (no history)")

    for i in range(1, TOTAL_ROUNDS + 1):
        await random_interaction()
        if i in SNAPSHOT_AT:
            print_scores(f"After {i} interaction{'s' if i > 1 else ''}")

    # ── Final analysis ────────────────────────────────────────────────────────
    sc = all_scores()
    honest_avg  = sum(sc[n] for n in ["alice", "bob", "carol"]) / 3
    mediocre_avg = sum(sc[n] for n in ["dave", "eve"]) / 2
    sybil_score = sc["mallory"]

    print()
    print("Network separation")
    print("-" * 40)
    print(f"  Honest avg:   {honest_avg:.3f}")
    print(f"  Mediocre avg: {mediocre_avg:.3f}")
    print(f"  Sybil score:  {sybil_score:.3f}")

    separated = honest_avg > mediocre_avg > sybil_score
    print()
    if separated:
        print("  Network correctly ranked all tiers.")
    else:
        print("  Note: more interactions needed for full separation.")

    print()
    print("Total interactions per agent:")
    for name, a in agents.items():
        print(f"  {name:>8}: {a.interaction_count} blocks")


asyncio.run(main())
