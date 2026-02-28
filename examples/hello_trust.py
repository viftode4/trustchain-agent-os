"""
Simplest possible TrustChain demo.

Two agents. One calls the other. Watch trust grow.
Run: python examples/hello_trust.py
"""
import asyncio
from agent_os import TrustAgent, TrustContext


buyer = TrustAgent(name="buyer")
seller = TrustAgent(name="seller")


@seller.service("compute", min_trust=0.0)
async def compute(data: dict, ctx: TrustContext) -> dict:
    x = data["x"]
    return {"result": x ** 2, "status": "completed"}


async def main():
    print(f"buyer  pubkey: {buyer.pubkey[:16]}...")
    print(f"seller pubkey: {seller.pubkey[:16]}...")
    print()

    for i in range(1, 11):
        ok, reason, result = await buyer.call_service(
            seller, "compute", {"x": i}
        )
        print(
            f"Round {i:2d}: {i}² = {result['result']:3d}  "
            f"| buyer={buyer.trust_score:.3f}  seller={seller.trust_score:.3f}"
        )

    print()
    print(f"buyer  interactions: {buyer.interaction_count}")
    print(f"seller interactions: {seller.interaction_count}")
    print(f"buyer  integrity:    {buyer.chain_integrity():.3f}")
    print(f"seller integrity:    {seller.chain_integrity():.3f}")


asyncio.run(main())
