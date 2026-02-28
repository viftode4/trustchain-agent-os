"""End-to-end TrustChain demo: trust growth, gating, and delegation.

Default: in-process mode (no Rust sidecar needed).
With --sidecar: connects to a local Rust sidecar on port 8202.

Run:
    python examples/e2e_sidecar_demo.py
    python examples/e2e_sidecar_demo.py --sidecar
"""
import argparse, asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_os import TrustAgent, TrustContext

H = lambda t: print(f"\n{'='*60}\n  {t}\n{'='*60}\n")

# -- Agents --
agent_a = TrustAgent(name="agent-a")
agent_b = TrustAgent(name="agent-b", bootstrap_interactions=1)
agent_c = TrustAgent(name="agent-c")

# -- Services on Agent B --
@agent_b.service("echo", min_trust=0.0)
async def echo(data: dict, ctx: TrustContext) -> dict:
    return {"echo": data.get("msg", "")}

@agent_b.service("premium", min_trust=0.35)
async def premium(data: dict, ctx: TrustContext) -> dict:
    return {"secret": 42, "caller_trust": round(ctx.caller_trust, 3)}

# -- Step 1: Trust building --
async def step1():
    H("Step 1: Trust Building Through Repeated Interactions")
    print(f"Agent A: {agent_a.pubkey[:16]}...\nAgent B: {agent_b.pubkey[:16]}...")
    print(f"\n{'Rnd':>3}  {'A trust':>8}  {'B trust':>8}  result\n" + "-" * 50)
    for i in range(1, 11):
        _, _, r = await agent_a.call_service(agent_b, "echo", {"msg": f"hello-{i}"})
        print(f"{i:>3}  {agent_a.trust_score:>8.3f}  {agent_b.trust_score:>8.3f}  echo={r['echo']}")
    print(f"\nAfter 10 calls: A={agent_a.interaction_count} interactions, B={agent_b.interaction_count}")

# -- Step 2: Trust gating --
async def step2():
    H("Step 2: Trust Gating -- Stranger vs Established Caller")
    # C burns bootstrap on first call, then gets blocked on premium
    await agent_c.call_service(agent_b, "echo", {"msg": "warmup"})
    ok_c, reason_c, _ = await agent_c.call_service(agent_b, "premium", {})
    print(f"Agent C (new)         -> premium: {'OK' if ok_c else 'BLOCKED'}")
    if not ok_c:
        print(f"  Reason: {reason_c}")
    # A has trust from step 1
    ok_a, _, res_a = await agent_a.call_service(agent_b, "premium", {})
    print(f"Agent A (established) -> premium: {'OK' if ok_a else 'BLOCKED'}")
    if ok_a:
        print(f"  Result: {res_a}")
    print(f"\nA trust={agent_a.trust_score:.3f}, C trust={agent_c.trust_score:.3f}")

# -- Step 3: Delegation --
async def step3():
    H("Step 3: Delegation -- Agent A Delegates to Agent C")
    from trustchain.blockstore import MemoryBlockStore
    from trustchain.protocol import TrustChainProtocol
    from trustchain.delegation import MemoryDelegationStore

    dstore = MemoryDelegationStore()
    proto_a = TrustChainProtocol(agent_a.identity, MemoryBlockStore(), delegation_store=dstore)
    proto_c = TrustChainProtocol(agent_c.identity, MemoryBlockStore(), delegation_store=dstore)

    proposal = proto_a.create_delegation(
        delegate_pubkey=agent_c.pubkey, scope=["echo", "premium"],
        max_depth=0, ttl_seconds=3600,
    )
    agreement, cert = proto_c.accept_delegation(proposal)
    record = dstore.get_delegation_by_delegate(agent_c.pubkey)

    print(f"A delegated to C:  scope=['echo','premium']  ttl=3600s  depth=0")
    print(f"  Proposal seq:  {proposal.sequence_number}  |  Agreement seq: {agreement.sequence_number}")
    print(f"  Cert hash:     {cert.certificate_hash[:24]}...")
    print(f"  Active:        {record.is_active}")
    print(f"\nDelegation lets C act on A's behalf, inheriting A's trust reputation.")

# -- Step 4: Integrity --
async def step4():
    H("Step 4: Chain Integrity Verification")
    for name, a in [("A", agent_a), ("B", agent_b), ("C", agent_c)]:
        print(f"  Agent {name}: integrity={a.chain_integrity():.3f}  "
              f"interactions={a.interaction_count}  trust={a.trust_score:.3f}")
    print("\nIntegrity=1.0 means a perfect, unbroken chain of signed blocks.")

# -- Sidecar mode --
async def run_sidecar():
    H("Sidecar Mode (connecting to Rust node)")
    try:
        import trustchain
        sc = trustchain.init(name="demo-agent", endpoint="http://127.0.0.1:8202")
        print(f"Connected to sidecar at {sc.http_url}")
        print(f"Identity: {sc.pubkey[:16]}...  Trust: {sc.trust_score():.3f}")
        print("\nSidecar connectivity verified. Run without --sidecar for full demo.")
    except Exception as e:
        print(f"Could not connect: {e}")
        print("Start a sidecar with: trustchain-node --http-port 8202")
        sys.exit(1)

# -- Main --
async def main():
    parser = argparse.ArgumentParser(description="TrustChain E2E Demo")
    parser.add_argument("--sidecar", action="store_true",
                        help="Connect to real Rust sidecars instead of in-process agents")
    args = parser.parse_args()
    if args.sidecar:
        await run_sidecar(); return

    H("TrustChain End-to-End Demo (in-process mode)")
    await step1()
    await step2()
    await step3()
    await step4()
    H("Demo Complete")
    print("  Bilaterally signed, hash-chained, Sybil-resistant.")
    print("  No central authority. Trust from the interaction graph.\n")

if __name__ == "__main__":
    asyncio.run(main())
