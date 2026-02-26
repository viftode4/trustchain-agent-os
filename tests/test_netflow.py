"""Tests for the TrustChain v2 NetFlow Sybil resistance."""

import pytest

from trustchain.blockstore import MemoryBlockStore
from trustchain.exceptions import NetFlowError
from trustchain.halfblock import GENESIS_HASH, BlockType, create_half_block
from trustchain.identity import Identity
from trustchain.netflow import NetFlowTrust


@pytest.fixture
def identity_a():
    return Identity()


@pytest.fixture
def identity_b():
    return Identity()


@pytest.fixture
def identity_c():
    return Identity()


def _create_transaction_pair(store, id_a, id_b, seq_a, seq_b, prev_a, prev_b):
    """Create a proposal+agreement pair and store both."""
    proposal = create_half_block(
        identity=id_a,
        sequence_number=seq_a,
        link_public_key=id_b.pubkey_hex,
        link_sequence_number=0,
        previous_hash=prev_a,
        block_type=BlockType.PROPOSAL,
        transaction={"interaction_type": "service", "outcome": "completed"},
    )
    store.add_block(proposal)

    agreement = create_half_block(
        identity=id_b,
        sequence_number=seq_b,
        link_public_key=id_a.pubkey_hex,
        link_sequence_number=seq_a,
        previous_hash=prev_b,
        block_type=BlockType.AGREEMENT,
        transaction={"interaction_type": "service", "outcome": "completed"},
    )
    store.add_block(agreement)

    return proposal, agreement


class TestNetFlowTrust:
    def test_requires_seed_nodes(self):
        store = MemoryBlockStore()
        with pytest.raises(NetFlowError, match="seed node"):
            NetFlowTrust(store, [])

    def test_seed_node_self_trust(self, identity_a):
        store = MemoryBlockStore()
        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        assert netflow.compute_trust(identity_a.pubkey_hex) == 1.0

    def test_unknown_agent_zero_trust(self, identity_a, identity_b):
        store = MemoryBlockStore()
        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        assert netflow.compute_trust(identity_b.pubkey_hex) == 0.0

    def test_connected_agent_gets_trust(self, identity_a, identity_b):
        store = MemoryBlockStore()
        _create_transaction_pair(
            store, identity_a, identity_b, 1, 1, GENESIS_HASH, GENESIS_HASH
        )

        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        trust = netflow.compute_trust(identity_b.pubkey_hex)
        assert trust > 0.0

    def test_transitive_trust(self, identity_a, identity_b, identity_c):
        """A -> B -> C: C should get some trust from seed A."""
        store = MemoryBlockStore()

        p_ab, a_ab = _create_transaction_pair(
            store, identity_a, identity_b, 1, 1, GENESIS_HASH, GENESIS_HASH
        )
        _create_transaction_pair(
            store, identity_b, identity_c,
            2, 1, a_ab.block_hash if a_ab.public_key == identity_b.pubkey_hex else p_ab.block_hash,
            GENESIS_HASH,
        )

        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        trust_b = netflow.compute_trust(identity_b.pubkey_hex)
        trust_c = netflow.compute_trust(identity_c.pubkey_hex)

        assert trust_b > 0.0
        assert trust_c > 0.0

    def test_sybil_cluster_low_trust(self, identity_a, identity_b, identity_c):
        """Sybil cluster (B<->C with no connection to seed A) gets near-zero trust."""
        store = MemoryBlockStore()

        # B and C interact only with each other
        _create_transaction_pair(
            store, identity_b, identity_c, 1, 1, GENESIS_HASH, GENESIS_HASH
        )

        # A is seed but has no interactions
        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        trust_b = netflow.compute_trust(identity_b.pubkey_hex)
        trust_c = netflow.compute_trust(identity_c.pubkey_hex)

        assert trust_b == 0.0
        assert trust_c == 0.0

    def test_compute_all_scores(self, identity_a, identity_b):
        store = MemoryBlockStore()
        _create_transaction_pair(
            store, identity_a, identity_b, 1, 1, GENESIS_HASH, GENESIS_HASH
        )

        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        scores = netflow.compute_all_scores()

        assert identity_a.pubkey_hex in scores
        assert identity_b.pubkey_hex in scores
        assert scores[identity_a.pubkey_hex] == 1.0
        assert scores[identity_b.pubkey_hex] > 0.0

    def test_build_contribution_graph(self, identity_a, identity_b):
        store = MemoryBlockStore()
        _create_transaction_pair(
            store, identity_a, identity_b, 1, 1, GENESIS_HASH, GENESIS_HASH
        )

        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        graph = netflow.build_contribution_graph()

        assert identity_a.pubkey_hex in graph
        assert identity_b.pubkey_hex in graph[identity_a.pubkey_hex]


class TestMultipleSeeds:
    def test_multiple_seed_nodes(self, identity_a, identity_b, identity_c):
        """Multiple seeds should all have trust=1.0."""
        store = MemoryBlockStore()
        _create_transaction_pair(
            store, identity_a, identity_b, 1, 1, GENESIS_HASH, GENESIS_HASH
        )

        netflow = NetFlowTrust(store, [identity_a.pubkey_hex, identity_b.pubkey_hex])
        assert netflow.compute_trust(identity_a.pubkey_hex) == 1.0
        assert netflow.compute_trust(identity_b.pubkey_hex) == 1.0

    def test_compute_all_scores_uses_batch(self, identity_a, identity_b):
        """compute_all_scores should return scores for all known agents."""
        store = MemoryBlockStore()
        _create_transaction_pair(
            store, identity_a, identity_b, 1, 1, GENESIS_HASH, GENESIS_HASH
        )

        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        scores = netflow.compute_all_scores()

        assert len(scores) >= 2
        assert all(0.0 <= s <= 1.0 for s in scores.values())
        assert scores[identity_a.pubkey_hex] == 1.0
        assert scores[identity_b.pubkey_hex] > 0.0

class TestNormalizationBounds:
    def test_all_scores_within_bounds(self, identity_a, identity_b, identity_c):
        """All trust scores should be in [0.0, 1.0]."""
        store = MemoryBlockStore()
        p1, a1 = _create_transaction_pair(
            store, identity_a, identity_b, 1, 1, GENESIS_HASH, GENESIS_HASH
        )
        _create_transaction_pair(
            store, identity_b, identity_c, 2, 1, a1.block_hash, GENESIS_HASH
        )

        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        for pk in store.get_all_pubkeys():
            score = netflow.compute_trust(pk)
            assert 0.0 <= score <= 1.0, f"Score {score} out of bounds for {pk[:16]}"

    def test_transitive_trust_ordering(self, identity_a, identity_b, identity_c):
        """Closer to seed should have >= trust than further."""
        store = MemoryBlockStore()
        p1, a1 = _create_transaction_pair(
            store, identity_a, identity_b, 1, 1, GENESIS_HASH, GENESIS_HASH
        )
        _create_transaction_pair(
            store, identity_b, identity_c, 2, 1, a1.block_hash, GENESIS_HASH
        )

        netflow = NetFlowTrust(store, [identity_a.pubkey_hex])
        trust_b = netflow.compute_trust(identity_b.pubkey_hex)
        trust_c = netflow.compute_trust(identity_c.pubkey_hex)
        assert trust_b >= trust_c
