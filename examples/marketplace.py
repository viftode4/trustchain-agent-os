"""
Trust-based marketplace: multiple sellers, one buyer.

Sellers have different reliability rates. The buyer sends tasks to all of
them round-robin for the first few rounds, then uses trust scores to route
exclusively to the most reliable ones.

Run: python examples/marketplace.py
"""
import asyncio
import random
from agent_os import TrustAgent, TrustContext

random.seed(42)

# ── Sellers with different reliability profiles ──────────────────────────────
SELLERS = [
    ("alpha",   1.00),   # always succeeds
    ("beta",    0.80),   # 80% success
    ("gamma",   0.50),   # coin flip
    ("delta",   0.20),   # mostly fails
    ("epsilon", 0.00),   # always fails (bad actor)
]

buyer = TrustAgent(name="buyer")
sellers: dict[str, TrustAgent] = {}

for seller_name, reliability in SELLERS:
    agent = TrustAgent(name=seller_name)
    sellers[seller_name] = agent

    # Closure trick: capture reliability per seller
    def make_handler(rel: float):
        async def compute(data: dict, ctx: TrustContext) -> dict:
            if random.random() > rel:
                raise RuntimeError("service unavailable")
            return {"result": data["x"] ** 2}
        return compute

    agent.service("compute", min_trust=0.0)(make_handler(reliability))


# ── Helpers ──────────────────────────────────────────────────────────────────

def scores() -> dict[str, float]:
    return {name: buyer.check_trust(agent.pubkey) for name, agent in sellers.items()}


def best_seller() -> str:
    """Return the seller name with the highest trust score from buyer's view."""
    return max(sellers.items(), key=lambda kv: buyer.check_trust(kv[1].pubkey))[0]


# ── Main ─────────────────────────────────────────────────────────────────────

EXPLORATION_ROUNDS = 15   # round-robin all sellers
EXPLOITATION_ROUNDS = 20  # route only to top-2 by trust


async def main():
    print("Trust-Based Marketplace Demo")
    print("=" * 65)
    print(f"Sellers: {', '.join(f'{n}({r*100:.0f}%)' for n, r in SELLERS)}")
    print()

    # ── Phase 1: Exploration — round-robin all sellers ────────────────────────
    print(f"Phase 1: Exploration ({EXPLORATION_ROUNDS} rounds, round-robin)")
    print(f"{'Rnd':>3}  {'Seller':>8}  {'OK':>4}  " +
          "  ".join(f"{n:>7}" for n, _ in SELLERS))
    print("-" * 65)

    seller_list = list(sellers.items())
    for i in range(EXPLORATION_ROUNDS):
        name, agent = seller_list[i % len(seller_list)]
        ok, reason, result = await buyer.call_service(agent, "compute", {"x": i + 1})
        ok_str = "OK" if "failed" not in reason else "FAIL"
        sc = scores()
        print(f"{i+1:>3}  {name:>8}  {ok_str:>4}  " +
              "  ".join(f"{sc[n]:>7.3f}" for n, _ in SELLERS))

    print()
    print("Scores after exploration:")
    for name, score in sorted(scores().items(), key=lambda x: -x[1]):
        reliability = dict(SELLERS)[name]
        print(f"  {name:>8}: {score:.3f}  (actual reliability {reliability*100:.0f}%)")

    # ── Phase 2: Exploitation — route only to top-2 ───────────────────────────
    top2 = sorted(scores().items(), key=lambda x: -x[1])[:2]
    top2_names = [n for n, _ in top2]
    print()
    print(f"Phase 2: Exploitation -- routing only to top-2: {top2_names}")
    print(f"{'Rnd':>3}  {'Seller':>8}  {'OK':>4}  " +
          "  ".join(f"{n:>7}" for n, _ in SELLERS))
    print("-" * 65)

    for i in range(EXPLOITATION_ROUNDS):
        # Pick best-trusted from top-2
        name = max(top2_names, key=lambda n: buyer.check_trust(sellers[n].pubkey))
        agent = sellers[name]
        ok, reason, result = await buyer.call_service(agent, "compute", {"x": i + 1})
        ok_str = "OK" if "failed" not in reason else "FAIL"
        sc = scores()
        print(f"{i+1:>3}  {name:>8}  {ok_str:>4}  " +
              "  ".join(f"{sc[n]:>7.3f}" for n, _ in SELLERS))

    print()
    print("Final scores (buyer's view of each seller):")
    for name, score in sorted(scores().items(), key=lambda x: -x[1]):
        reliability = dict(SELLERS)[name]
        bar = "#" * int(score * 20)
        print(f"  {name:>8}: {score:.3f}  {bar:<20}  ({reliability*100:.0f}% reliable)")

    print()
    winner = best_seller()
    print(f"Routing winner: {winner}  trust score {buyer.check_trust(sellers[winner].pubkey):.3f}")


asyncio.run(main())
