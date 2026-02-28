"""
What happens when the seller starts failing tasks?
Run: python examples/trust_drop.py
"""
import asyncio
from agent_os import TrustAgent, TrustContext

buyer = TrustAgent(name="buyer")
seller = TrustAgent(name="seller")

fail_from = 6  # seller starts failing at round 6


@seller.service("compute", min_trust=0.0)
async def compute(data: dict, ctx: TrustContext) -> dict:
    if data.get("round", 0) >= fail_from:
        raise RuntimeError("seller is down")  # triggers outcome=failed
    return {"result": data["x"] ** 2}


async def main():
    print(f"{'Round':>5}  {'Result':>8}  {'buyer':>8}  {'seller':>8}  note")
    print("-" * 55)

    for i in range(1, 16):
        ok, reason, result = await buyer.call_service(
            seller, "compute", {"x": i, "round": i}
        )
        note = "OK" if "failed" not in reason else "FAIL"
        result_val = result.get("result") if result else "ERR"
        print(
            f"{i:>5}  {str(result_val):>8}  "
            f"{buyer.trust_score:>8.3f}  {seller.trust_score:>8.3f}  {note}"
        )

asyncio.run(main())
