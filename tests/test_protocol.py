"""Tests for the TrustChain v2 protocol engine."""

import pytest

from trustchain.blockstore import MemoryBlockStore
from trustchain.exceptions import (
    AgreementError,
    PrevHashMismatchError,
    ProposalError,
    SequenceGapError,
    SignatureError,
)
from trustchain.halfblock import GENESIS_HASH, BlockType
from trustchain.identity import Identity
from trustchain.protocol import TrustChainProtocol


@pytest.fixture
def identity_a():
    return Identity()


@pytest.fixture
def identity_b():
    return Identity()


@pytest.fixture
def identity_c():
    return Identity()


@pytest.fixture
def store_a():
    return MemoryBlockStore()


@pytest.fixture
def store_b():
    return MemoryBlockStore()


@pytest.fixture
def protocol_a(identity_a, store_a):
    return TrustChainProtocol(identity_a, store_a)


@pytest.fixture
def protocol_b(identity_b, store_b):
    return TrustChainProtocol(identity_b, store_b)


class TestCreateProposal:
    def test_first_proposal(self, protocol_a, identity_b):
        proposal = protocol_a.create_proposal(
            identity_b.pubkey_hex,
            {"interaction_type": "service", "outcome": "completed"},
        )

        assert proposal.public_key == protocol_a.pubkey
        assert proposal.sequence_number == 1
        assert proposal.link_public_key == identity_b.pubkey_hex
        assert proposal.link_sequence_number == 0
        assert proposal.previous_hash == GENESIS_HASH
        assert proposal.block_type == BlockType.PROPOSAL

    def test_sequential_proposals(self, protocol_a, identity_b):
        p1 = protocol_a.create_proposal(identity_b.pubkey_hex, {"n": 1})
        p2 = protocol_a.create_proposal(identity_b.pubkey_hex, {"n": 2})

        assert p1.sequence_number == 1
        assert p2.sequence_number == 2
        assert p2.previous_hash == p1.block_hash

    def test_proposal_stored_locally(self, protocol_a, identity_b, store_a):
        proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {})
        stored = store_a.get_block(protocol_a.pubkey, 1)
        assert stored is not None
        assert stored.block_hash == proposal.block_hash


class TestReceiveProposal:
    def test_receive_valid_proposal(self, protocol_a, protocol_b, identity_a, identity_b):
        proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {"test": True})
        assert protocol_b.receive_proposal(proposal) is True

    def test_reject_proposal_not_for_us(self, protocol_a, protocol_b, identity_c):
        # Proposal addressed to C, not B
        proposal = protocol_a.create_proposal(identity_c.pubkey_hex, {})
        with pytest.raises(ProposalError, match="not addressed"):
            protocol_b.receive_proposal(proposal)

    def test_reject_tampered_proposal(self, protocol_a, identity_b):
        proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {})
        proposal.signature = "00" * 64  # Tamper

        protocol_b = TrustChainProtocol(Identity(identity_b._private_key), MemoryBlockStore())
        # Re-create protocol_b with same identity
        with pytest.raises(SignatureError):
            protocol_b.receive_proposal(proposal)

    def test_reject_non_proposal_type(self, protocol_b, identity_a, identity_b):
        from trustchain.halfblock import create_half_block
        agreement = create_half_block(
            identity=identity_a,
            sequence_number=1,
            link_public_key=identity_b.pubkey_hex,
            link_sequence_number=1,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.AGREEMENT,
            transaction={},
        )
        with pytest.raises(ProposalError, match="Expected proposal"):
            protocol_b.receive_proposal(agreement)


class TestCreateAgreement:
    def test_create_agreement(self, protocol_a, protocol_b, identity_a, identity_b):
        proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {"test": True})
        protocol_b.receive_proposal(proposal)
        agreement = protocol_b.create_agreement(proposal)

        assert agreement.public_key == identity_b.pubkey_hex
        assert agreement.sequence_number == 1
        assert agreement.link_public_key == identity_a.pubkey_hex
        assert agreement.link_sequence_number == proposal.sequence_number
        assert agreement.block_type == BlockType.AGREEMENT
        assert agreement.transaction == proposal.transaction

    def test_agreement_stored_locally(self, protocol_a, protocol_b, identity_b, store_b):
        proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {})
        protocol_b.receive_proposal(proposal)
        agreement = protocol_b.create_agreement(proposal)

        # Both the proposal and agreement should be in B's store
        stored_proposal = store_b.get_block(proposal.public_key, proposal.sequence_number)
        stored_agreement = store_b.get_block(identity_b.pubkey_hex, 1)
        assert stored_proposal is not None
        assert stored_agreement is not None

    def test_reject_agreement_on_non_proposal(self, protocol_b, identity_a, identity_b):
        from trustchain.halfblock import create_half_block
        non_proposal = create_half_block(
            identity=identity_a,
            sequence_number=1,
            link_public_key=identity_b.pubkey_hex,
            link_sequence_number=1,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.AGREEMENT,
            transaction={},
        )
        with pytest.raises(AgreementError, match="non-proposal"):
            protocol_b.create_agreement(non_proposal)


class TestReceiveAgreement:
    def test_receive_valid_agreement(self, protocol_a, protocol_b, identity_a, identity_b):
        proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {"test": True})
        protocol_b.receive_proposal(proposal)
        agreement = protocol_b.create_agreement(proposal)

        assert protocol_a.receive_agreement(agreement) is True

    def test_reject_agreement_wrong_link(self, protocol_a, protocol_b, identity_a, identity_b, identity_c):
        proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {})
        protocol_b.receive_proposal(proposal)
        agreement = protocol_b.create_agreement(proposal)

        # C's protocol shouldn't accept it (not linked to C)
        protocol_c = TrustChainProtocol(identity_c, MemoryBlockStore())
        with pytest.raises(AgreementError, match="does not link"):
            protocol_c.receive_agreement(agreement)

    def test_reject_tampered_agreement(self, protocol_a, protocol_b, identity_a, identity_b):
        proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {})
        protocol_b.receive_proposal(proposal)
        agreement = protocol_b.create_agreement(proposal)

        agreement.signature = "00" * 64  # Tamper
        with pytest.raises(SignatureError):
            protocol_a.receive_agreement(agreement)


class TestFullTransaction:
    def test_complete_transaction(self, protocol_a, protocol_b, identity_a, identity_b, store_a, store_b):
        """Full proposal/agreement round-trip."""
        tx = {"interaction_type": "service", "outcome": "completed"}

        # A proposes
        proposal = protocol_a.create_proposal(identity_b.pubkey_hex, tx)

        # B receives proposal, creates agreement
        protocol_b.receive_proposal(proposal)
        agreement = protocol_b.create_agreement(proposal)

        # A receives agreement
        protocol_a.receive_agreement(agreement)

        # Both stores have 2 blocks each
        # A's store: A's proposal + B's agreement
        assert store_a.get_latest_seq(identity_a.pubkey_hex) == 1
        assert store_a.get_block(identity_b.pubkey_hex, 1) is not None

        # B's store: A's proposal + B's agreement
        assert store_b.get_latest_seq(identity_b.pubkey_hex) == 1
        assert store_b.get_block(identity_a.pubkey_hex, 1) is not None

    def test_multiple_transactions(self, protocol_a, protocol_b, identity_a, identity_b):
        for i in range(5):
            tx = {"n": i, "interaction_type": "service", "outcome": "completed"}
            proposal = protocol_a.create_proposal(identity_b.pubkey_hex, tx)
            protocol_b.receive_proposal(proposal)
            agreement = protocol_b.create_agreement(proposal)
            protocol_a.receive_agreement(agreement)

        assert protocol_a.store.get_latest_seq(identity_a.pubkey_hex) == 5
        assert protocol_b.store.get_latest_seq(identity_b.pubkey_hex) == 5


class TestChainValidation:
    def test_validate_valid_chain(self, protocol_a, protocol_b, identity_a, identity_b):
        for i in range(3):
            proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {"n": i})
            protocol_b.receive_proposal(proposal)
            agreement = protocol_b.create_agreement(proposal)
            protocol_a.receive_agreement(agreement)

        assert protocol_a.validate_chain(identity_a.pubkey_hex) is True
        assert protocol_b.validate_chain(identity_b.pubkey_hex) is True

    def test_validate_empty_chain(self, protocol_a, identity_a):
        assert protocol_a.validate_chain(identity_a.pubkey_hex) is True

    def test_integrity_score_perfect(self, protocol_a, protocol_b, identity_a, identity_b):
        for i in range(3):
            proposal = protocol_a.create_proposal(identity_b.pubkey_hex, {"n": i})
            protocol_b.receive_proposal(proposal)
            agreement = protocol_b.create_agreement(proposal)
            protocol_a.receive_agreement(agreement)

        assert protocol_a.integrity_score(identity_a.pubkey_hex) == 1.0

    def test_integrity_score_empty(self, protocol_a, identity_a):
        assert protocol_a.integrity_score(identity_a.pubkey_hex) == 1.0


class TestSequenceGapRejection:
    """Tests for sequence gap detection in receive_proposal."""

    def test_reject_sequence_gap(self, identity_a, identity_b, store_a, store_b):
        """Receiving seq=3 when we know seq=1 should fail."""
        # A creates proposal seq=1 and B receives it
        proto_a = TrustChainProtocol(identity_a, store_a)
        proto_b = TrustChainProtocol(identity_b, store_b)
        p1 = proto_a.create_proposal(identity_b.pubkey_hex, {"outcome": "completed"})
        proto_b.receive_proposal(p1)

        # A creates seq=2 internally but doesn't send it
        p2 = proto_a.create_proposal(identity_b.pubkey_hex, {"outcome": "completed"})

        # A creates seq=3
        p3 = proto_a.create_proposal(identity_b.pubkey_hex, {"outcome": "completed"})

        # B tries to receive seq=3 (gap: missing seq=2)
        with pytest.raises(SequenceGapError):
            proto_b.receive_proposal(p3)

    def test_accept_consecutive_sequence(self, identity_a, identity_b, store_a, store_b):
        """Receiving seq=2 right after seq=1 should work."""
        proto_a = TrustChainProtocol(identity_a, store_a)
        proto_b = TrustChainProtocol(identity_b, store_b)
        p1 = proto_a.create_proposal(identity_b.pubkey_hex, {"outcome": "completed"})
        proto_b.receive_proposal(p1)
        p2 = proto_a.create_proposal(identity_b.pubkey_hex, {"outcome": "completed"})
        assert proto_b.receive_proposal(p2) is True


class TestPrevHashValidation:
    """Tests for previous_hash chain linkage in receive_proposal."""

    def test_reject_forged_prev_hash(self, identity_a, identity_b, store_a, store_b):
        """A proposal with wrong previous_hash should be rejected."""
        proto_a = TrustChainProtocol(identity_a, store_a)
        proto_b = TrustChainProtocol(identity_b, store_b)

        # B receives A's first block normally
        p1 = proto_a.create_proposal(identity_b.pubkey_hex, {"outcome": "completed"})
        proto_b.receive_proposal(p1)

        # Now create a forged seq=2 with wrong previous_hash
        from trustchain.halfblock import create_half_block, BlockType, GENESIS_HASH
        forged = create_half_block(
            identity=identity_a,
            sequence_number=2,
            link_public_key=identity_b.pubkey_hex,
            link_sequence_number=0,
            previous_hash="ff" * 32,  # Wrong prev_hash
            block_type=BlockType.PROPOSAL,
            transaction={"outcome": "completed"},
        )

        with pytest.raises(PrevHashMismatchError):
            proto_b.receive_proposal(forged)


class TestTransactionConsistency:
    """Tests for transaction consistency in receive_agreement."""

    def test_reject_agreement_with_modified_transaction(self, identity_a, identity_b, store_a, store_b):
        """An agreement with different transaction content should be rejected."""
        proto_a = TrustChainProtocol(identity_a, store_a)
        proto_b = TrustChainProtocol(identity_b, store_b)

        # A proposes
        p1 = proto_a.create_proposal(identity_b.pubkey_hex, {"outcome": "completed", "amount": 100})
        proto_b.receive_proposal(p1)

        # B creates a valid agreement first to get the sequence right
        # Then we'll create a tampered one
        from trustchain.halfblock import create_half_block, BlockType
        tampered_agreement = create_half_block(
            identity=identity_b,
            sequence_number=store_b.get_latest_seq(identity_b.pubkey_hex) + 1,
            link_public_key=identity_a.pubkey_hex,
            link_sequence_number=p1.sequence_number,
            previous_hash=store_b.get_head_hash(identity_b.pubkey_hex),
            block_type=BlockType.AGREEMENT,
            transaction={"outcome": "completed", "amount": 999},  # Tampered!
        )

        with pytest.raises(AgreementError, match="transaction does not match"):
            proto_a.receive_agreement(tampered_agreement)


class TestSelfProposal:
    """Test handling of self-referencing proposals."""

    def test_self_proposal_creates_block(self, identity_a, store_a):
        """Self-proposal should work (needed for checkpoint blocks)."""
        proto = TrustChainProtocol(identity_a, store_a)
        p = proto.create_proposal(identity_a.pubkey_hex, {"type": "self-test"})
        assert p.public_key == p.link_public_key
