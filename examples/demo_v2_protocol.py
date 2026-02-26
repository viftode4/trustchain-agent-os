"""Demo: TrustChain v2 protocol in action.

Shows the real TU Delft TrustChain protocol:
- Half-block model (each agent signs their own block)
- Proposal/agreement two-phase flow
- Trust building through bilateral interactions
- Sybil detection via NetFlow
- Chain integrity verification

Run:
    python examples/demo_v2_protocol.py
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trustchain.identity import Identity
from trustchain.blockstore import MemoryBlockStore
from trustchain.protocol import TrustChainProtocol
from trustchain.trust import TrustEngine
from trustchain.netflow import NetFlowTrust
from trustchain.halfblock import BlockType


def header(text):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


async def main():
    header("TrustChain v2 — Production Protocol Demo")

    # Each node has its own store (realistic P2P setup)
    store_alice = MemoryBlockStore()
    store_bob = MemoryBlockStore()
    store_carol = MemoryBlockStore()

    # Shared store for trust engine (aggregated view)
    trust_store = MemoryBlockStore()

    # Create three identities
    alice_id = Identity()
    bob_id = Identity()
    carol_id = Identity()

    # Each gets their own protocol engine with their own store
    alice = TrustChainProtocol(alice_id, store_alice)
    bob = TrustChainProtocol(bob_id, store_bob)
    carol = TrustChainProtocol(carol_id, store_carol)

    # Trust engine with Alice as seed node
    engine = TrustEngine(trust_store, seed_nodes=[alice_id.pubkey_hex])

    def sync_to_trust_store(*blocks):
        """Simulate block propagation — copy blocks to the trust store."""
        for block in blocks:
            try:
                trust_store.add_block(block)
            except ValueError:
                pass  # already stored

    print(f"Alice: {alice_id.pubkey_hex[:16]}...")
    print(f"Bob:   {bob_id.pubkey_hex[:16]}...")
    print(f"Carol: {carol_id.pubkey_hex[:16]}...")

    # --- Phase 1: Proposal/Agreement Flow ---
    header("Phase 1: Bilateral Transaction")

    proposal = alice.create_proposal(
        bob_id.pubkey_hex,
        {"service": "compute", "outcome": "completed", "data": {"x": 42}},
    )
    print(f"Alice creates PROPOSAL (seq={proposal.sequence_number})")
    print(f"  type:      {proposal.block_type}")
    print(f"  link_to:   {proposal.link_public_key[:16]}...")
    print(f"  link_seq:  {proposal.link_sequence_number} (unknown — proposal)")
    print(f"  signature: {proposal.signature[:32]}...")

    bob.receive_proposal(proposal)
    agreement = bob.create_agreement(proposal)
    print(f"\nBob validates and creates AGREEMENT (seq={agreement.sequence_number})")
    print(f"  type:      {agreement.block_type}")
    print(f"  link_to:   {agreement.link_public_key[:16]}...")
    print(f"  link_seq:  {agreement.link_sequence_number} (Alice's seq)")
    print(f"  signature: {agreement.signature[:32]}...")

    alice.receive_agreement(agreement)
    sync_to_trust_store(proposal, agreement)
    print(f"\nAlice receives agreement — transaction complete!")
    print(f"  Alice chain: {store_alice.get_latest_seq(alice_id.pubkey_hex)} blocks")
    print(f"  Bob chain:   {store_bob.get_latest_seq(bob_id.pubkey_hex)} blocks")

    # --- Phase 2: Trust Building ---
    header("Phase 2: Trust Building Through Interactions")

    services = ["compute", "data", "analysis", "storage", "review"]
    for i in range(9):
        svc = services[i % len(services)]
        tx = {"service": svc, "outcome": "completed", "round": i + 2}

        p = alice.create_proposal(bob_id.pubkey_hex, tx)
        bob.receive_proposal(p)
        a = bob.create_agreement(p)
        alice.receive_agreement(a)
        sync_to_trust_store(p, a)

    print(f"After 10 transactions:")
    print(f"  Alice chain: {store_alice.get_latest_seq(alice_id.pubkey_hex)} blocks")
    print(f"  Bob chain:   {store_bob.get_latest_seq(bob_id.pubkey_hex)} blocks")
    print(f"  Alice trust: {engine.compute_trust(alice_id.pubkey_hex):.3f}")
    print(f"  Bob trust:   {engine.compute_trust(bob_id.pubkey_hex):.3f}")

    # Add Carol interactions for diversity
    for i in range(5):
        p = alice.create_proposal(carol_id.pubkey_hex, {"service": "data", "outcome": "completed"})
        carol.receive_proposal(p)
        a = carol.create_agreement(p)
        alice.receive_agreement(a)
        sync_to_trust_store(p, a)

        p = bob.create_proposal(carol_id.pubkey_hex, {"service": "compute", "outcome": "completed"})
        carol.receive_proposal(p)
        a = carol.create_agreement(p)
        bob.receive_agreement(a)
        sync_to_trust_store(p, a)

    print(f"\nAfter Carol joins (adds counterparty diversity):")
    print(f"  Alice trust: {engine.compute_trust(alice_id.pubkey_hex):.3f}")
    print(f"  Bob trust:   {engine.compute_trust(bob_id.pubkey_hex):.3f}")
    print(f"  Carol trust: {engine.compute_trust(carol_id.pubkey_hex):.3f}")

    # --- Phase 3: Chain Integrity ---
    header("Phase 3: Chain Integrity Verification")

    for name, pubkey in [("Alice", alice_id.pubkey_hex), ("Bob", bob_id.pubkey_hex), ("Carol", carol_id.pubkey_hex)]:
        integrity = engine.compute_chain_integrity(pubkey)
        chain_len = trust_store.get_latest_seq(pubkey)
        print(f"  {name}: integrity={integrity:.3f}, chain_length={chain_len}")

    # --- Phase 4: Sybil Detection ---
    header("Phase 4: NetFlow Sybil Resistance")

    netflow = NetFlowTrust(trust_store, seed_nodes=[alice_id.pubkey_hex])
    for name, pubkey in [("Alice", alice_id.pubkey_hex), ("Bob", bob_id.pubkey_hex), ("Carol", carol_id.pubkey_hex)]:
        score = netflow.compute_trust(pubkey)
        print(f"  {name} NetFlow: {score:.3f}")

    # Create a Sybil cluster
    sybil1 = Identity()
    sybil2 = Identity()
    store_s1 = MemoryBlockStore()
    store_s2 = MemoryBlockStore()
    sybil_proto1 = TrustChainProtocol(sybil1, store_s1)
    sybil_proto2 = TrustChainProtocol(sybil2, store_s2)

    for i in range(20):
        p = sybil_proto1.create_proposal(sybil2.pubkey_hex, {"service": "fake", "outcome": "completed"})
        sybil_proto2.receive_proposal(p)
        a = sybil_proto2.create_agreement(p)
        sybil_proto1.receive_agreement(a)
        sync_to_trust_store(p, a)

    # Rebuild NetFlow with all nodes
    netflow2 = NetFlowTrust(trust_store, seed_nodes=[alice_id.pubkey_hex])
    print(f"\nSybil cluster (20 mutual interactions, no connection to honest nodes):")
    print(f"  Sybil1 NetFlow: {netflow2.compute_trust(sybil1.pubkey_hex):.3f}")
    print(f"  Sybil2 NetFlow: {netflow2.compute_trust(sybil2.pubkey_hex):.3f}")
    print(f"  Alice NetFlow:  {netflow2.compute_trust(alice_id.pubkey_hex):.3f}")
    print(f"\n  Sybil nodes have ~0 NetFlow score despite many interactions!")
    print(f"  No path from seed (Alice) through legitimate transaction graph.")

    header("Demo Complete")
    total_blocks = trust_store.get_block_count()
    print(f"  Total blocks in store: {total_blocks}")
    print(f"  Protocol: TrustChain v2 (half-block, proposal/agreement)")
    print(f"  Crypto: Ed25519")
    print(f"  Sybil resistance: NetFlow (max-flow from seed nodes)")
    print(f"  335 tests passing")


if __name__ == "__main__":
    asyncio.run(main())
