"""Tests for the TrustChain v2 CHECO checkpoint consensus."""

import pytest

from trustchain.blockstore import MemoryBlockStore
from trustchain.consensus import CHECOConsensus
from trustchain.exceptions import CheckpointError
from trustchain.halfblock import GENESIS_HASH, BlockType, create_half_block
from trustchain.identity import Identity


@pytest.fixture
def identity_a():
    return Identity()


@pytest.fixture
def identity_b():
    return Identity()


@pytest.fixture
def store_a():
    return MemoryBlockStore()


@pytest.fixture
def store_b():
    return MemoryBlockStore()


def _seed_chain(store, identity, counterparty_pubkey, count=3):
    """Create a chain of proposals for testing."""
    prev = GENESIS_HASH
    for i in range(1, count + 1):
        block = create_half_block(
            identity=identity,
            sequence_number=i,
            link_public_key=counterparty_pubkey,
            link_sequence_number=0,
            previous_hash=prev,
            block_type=BlockType.PROPOSAL,
            transaction={"interaction_type": "service", "outcome": "completed"},
        )
        store.add_block(block)
        prev = block.block_hash
    return prev


class TestFacilitatorSelection:
    def test_deterministic_selection(self, identity_a, identity_b, store_a):
        checo = CHECOConsensus(
            identity_a, store_a,
            known_peers=[identity_b.pubkey_hex],
        )
        f1 = checo.select_facilitator()
        f2 = checo.select_facilitator()
        assert f1 == f2  # Deterministic

    def test_selection_from_peer_set(self, identity_a, identity_b, store_a):
        checo = CHECOConsensus(
            identity_a, store_a,
            known_peers=[identity_b.pubkey_hex],
        )
        facilitator = checo.select_facilitator()
        assert facilitator in [identity_a.pubkey_hex, identity_b.pubkey_hex]

    def test_solo_node_is_facilitator(self, identity_a, store_a):
        checo = CHECOConsensus(identity_a, store_a)
        assert checo.is_facilitator()


class TestProposeCheckpoint:
    def test_propose_checkpoint(self, identity_a, identity_b, store_a):
        _seed_chain(store_a, identity_a, identity_b.pubkey_hex, 3)

        checo = CHECOConsensus(identity_a, store_a)
        # Solo node is always facilitator
        block = checo.propose_checkpoint()

        assert block.block_type == BlockType.CHECKPOINT
        assert block.transaction["interaction_type"] == "checkpoint"
        assert "chain_heads" in block.transaction
        assert identity_a.pubkey_hex in block.transaction["chain_heads"]

    def test_non_facilitator_cannot_propose(self, identity_a, identity_b, store_a):
        # Create a consensus where A might not be facilitator
        # We force this by having many peers
        peers = [Identity().pubkey_hex for _ in range(100)]
        checo = CHECOConsensus(identity_a, store_a, known_peers=peers)

        if not checo.is_facilitator():
            with pytest.raises(CheckpointError, match="Not the current facilitator"):
                checo.propose_checkpoint()

    def test_checkpoint_stored_in_chain(self, identity_a, identity_b, store_a):
        _seed_chain(store_a, identity_a, identity_b.pubkey_hex, 3)

        checo = CHECOConsensus(identity_a, store_a)
        block = checo.propose_checkpoint()

        # Checkpoint should be seq=4 (after 3 proposals)
        assert block.sequence_number == 4
        stored = store_a.get_block(identity_a.pubkey_hex, 4)
        assert stored is not None
        assert stored.block_type == BlockType.CHECKPOINT


class TestValidateCheckpoint:
    def test_validate_valid_checkpoint(self, identity_a, identity_b, store_a, store_b):
        _seed_chain(store_a, identity_a, identity_b.pubkey_hex, 3)

        checo_a = CHECOConsensus(identity_a, store_a)
        checkpoint_block = checo_a.propose_checkpoint()

        checo_b = CHECOConsensus(identity_b, store_b, known_peers=[identity_a.pubkey_hex])
        assert checo_b.validate_checkpoint(checkpoint_block) is True

    def test_reject_non_checkpoint_type(self, identity_a, identity_b, store_b):
        proposal = create_half_block(
            identity=identity_a,
            sequence_number=1,
            link_public_key=identity_b.pubkey_hex,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={},
        )

        checo_b = CHECOConsensus(identity_b, store_b)
        with pytest.raises(CheckpointError, match="Expected checkpoint"):
            checo_b.validate_checkpoint(proposal)

    def test_reject_tampered_checkpoint(self, identity_a, identity_b, store_a, store_b):
        _seed_chain(store_a, identity_a, identity_b.pubkey_hex, 3)
        checo_a = CHECOConsensus(identity_a, store_a)
        checkpoint_block = checo_a.propose_checkpoint()

        checkpoint_block.signature = "00" * 64  # Tamper

        checo_b = CHECOConsensus(identity_b, store_b)
        with pytest.raises(CheckpointError, match="Invalid checkpoint signature"):
            checo_b.validate_checkpoint(checkpoint_block)


class TestSignAndFinalize:
    def test_sign_checkpoint(self, identity_a, identity_b, store_a, store_b):
        _seed_chain(store_a, identity_a, identity_b.pubkey_hex, 3)
        checo_a = CHECOConsensus(identity_a, store_a)
        checkpoint_block = checo_a.propose_checkpoint()

        checo_b = CHECOConsensus(identity_b, store_b)
        sig = checo_b.sign_checkpoint(checkpoint_block)
        assert len(sig) > 0

    def test_finalize_with_signatures(self, identity_a, identity_b, store_a, store_b):
        _seed_chain(store_a, identity_a, identity_b.pubkey_hex, 3)
        checo_a = CHECOConsensus(identity_a, store_a, min_signers=2)
        checkpoint_block = checo_a.propose_checkpoint()

        checo_b = CHECOConsensus(identity_b, store_b)
        sig_b = checo_b.sign_checkpoint(checkpoint_block)

        signatures = {
            identity_a.pubkey_hex: checkpoint_block.signature,
            identity_b.pubkey_hex: sig_b,
        }

        cp = checo_a.finalize_checkpoint(checkpoint_block, signatures)
        assert cp.finalized is True
        assert cp.signer_count == 2

    def test_finalize_insufficient_signatures(self, identity_a, store_a):
        _seed_chain(store_a, identity_a, "0" * 64, 3)
        checo_a = CHECOConsensus(identity_a, store_a, min_signers=3)
        checkpoint_block = checo_a.propose_checkpoint()

        signatures = {identity_a.pubkey_hex: checkpoint_block.signature}

        with pytest.raises(CheckpointError, match="Not enough signatures"):
            checo_a.finalize_checkpoint(checkpoint_block, signatures)


class TestFinality:
    def test_is_finalized(self, identity_a, identity_b, store_a):
        _seed_chain(store_a, identity_a, identity_b.pubkey_hex, 3)
        checo = CHECOConsensus(identity_a, store_a, min_signers=1)
        checkpoint_block = checo.propose_checkpoint()

        sigs = {identity_a.pubkey_hex: checkpoint_block.signature}
        checo.finalize_checkpoint(checkpoint_block, sigs)

        # Blocks 1-3 should be finalized
        assert checo.is_finalized(identity_a.pubkey_hex, 1) is True
        assert checo.is_finalized(identity_a.pubkey_hex, 3) is True

        # Block 10 (doesn't exist) should not be finalized
        assert checo.is_finalized(identity_a.pubkey_hex, 10) is False

    def test_not_finalized_without_signatures(self, identity_a, store_a):
        _seed_chain(store_a, identity_a, "0" * 64, 3)
        checo = CHECOConsensus(identity_a, store_a)
        checo.propose_checkpoint()  # Proposed but not finalized

        # Proposed checkpoint is not finalized
        assert checo.is_finalized(identity_a.pubkey_hex, 1) is False


class TestStaleCheckpoint:
    def test_reject_stale_checkpoint(self, identity_a, identity_b, store_a, store_b):
        """A checkpoint referencing old chain heads should be rejected when validator knows more."""
        # Seed A's chain with 3 blocks
        _seed_chain(store_a, identity_a, identity_b.pubkey_hex, 3)

        checo_a = CHECOConsensus(identity_a, store_a)
        checkpoint_block = checo_a.propose_checkpoint()

        # Now seed B's store with blocks from A (simulating B has more data)
        _seed_chain(store_b, identity_a, identity_b.pubkey_hex, 5)

        checo_b = CHECOConsensus(identity_b, store_b, known_peers=[identity_a.pubkey_hex])
        # B knows A has 5 blocks, but checkpoint says 3 → stale
        with pytest.raises(CheckpointError, match="Stale checkpoint"):
            checo_b.validate_checkpoint(checkpoint_block)
