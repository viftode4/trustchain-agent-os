"""Tests for the TrustChain v2 HalfBlock data model."""

import pytest

from trustchain.halfblock import (
    GENESIS_HASH,
    BlockType,
    HalfBlock,
    compute_block_hash,
    create_half_block,
    sign_block,
    verify_block,
)
from trustchain.identity import Identity


class TestHalfBlockCreation:
    def test_create_half_block(self):
        identity = Identity()
        block = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"interaction_type": "service", "outcome": "completed"},
        )

        assert block.public_key == identity.pubkey_hex
        assert block.sequence_number == 1
        assert block.link_sequence_number == 0
        assert block.previous_hash == GENESIS_HASH
        assert block.block_type == BlockType.PROPOSAL
        assert block.signature != ""
        assert block.block_hash != ""

    def test_genesis_hash_constant(self):
        assert GENESIS_HASH == "0" * 64
        assert len(GENESIS_HASH) == 64

    def test_block_types(self):
        assert BlockType.PROPOSAL == "proposal"
        assert BlockType.AGREEMENT == "agreement"
        assert BlockType.CHECKPOINT == "checkpoint"


class TestHalfBlockHashing:
    def test_hash_deterministic(self):
        identity = Identity()
        block = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"a": 1},
            timestamp=1000.0,
        )

        # Recompute hash should match
        recomputed = compute_block_hash(block)
        assert recomputed == block.block_hash

    def test_hash_changes_with_content(self):
        identity = Identity()
        block1 = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"a": 1},
            timestamp=1000.0,
        )
        block2 = create_half_block(
            identity=identity,
            sequence_number=2,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=block1.block_hash,
            block_type=BlockType.PROPOSAL,
            transaction={"a": 2},
            timestamp=1001.0,
        )

        assert block1.block_hash != block2.block_hash

    def test_hash_computed_with_signature_zeroed(self):
        identity = Identity()
        block = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={},
            timestamp=1000.0,
        )

        # Signature should NOT affect the hash
        original_hash = block.block_hash
        # Manually verify the hash is computed without signature
        block.signature = "ff" * 64
        recomputed = compute_block_hash(block)
        assert recomputed == original_hash


class TestHalfBlockSigning:
    def test_sign_block(self):
        identity = Identity()
        block = HalfBlock(
            public_key=identity.pubkey_hex,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            signature="",
            block_type=BlockType.PROPOSAL,
            transaction={},
            block_hash="",
            timestamp=1000.0,
        )

        signed = sign_block(block, identity)
        assert signed.signature != ""
        assert signed.block_hash != ""

    def test_sign_block_wrong_identity_raises(self):
        identity_a = Identity()
        identity_b = Identity()
        block = HalfBlock(
            public_key=identity_a.pubkey_hex,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            signature="",
            block_type=BlockType.PROPOSAL,
            transaction={},
            block_hash="",
            timestamp=1000.0,
        )

        with pytest.raises(ValueError, match="does not match"):
            sign_block(block, identity_b)


class TestHalfBlockVerification:
    def test_verify_valid_block(self):
        identity = Identity()
        block = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"test": True},
        )

        assert verify_block(block) is True

    def test_verify_tampered_hash_fails(self):
        identity = Identity()
        block = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={},
        )

        block.block_hash = "f" * 64  # Tamper
        assert verify_block(block) is False

    def test_verify_tampered_signature_fails(self):
        identity = Identity()
        block = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={},
        )

        block.signature = "00" * 64  # Tamper
        assert verify_block(block) is False

    def test_verify_tampered_transaction_fails(self):
        identity = Identity()
        block = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"original": True},
        )

        block.transaction = {"tampered": True}  # Tamper
        assert verify_block(block) is False


class TestHalfBlockSerialization:
    def test_to_dict_roundtrip(self):
        identity = Identity()
        block = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"key": "value"},
        )

        d = block.to_dict()
        restored = HalfBlock.from_dict(d)

        assert restored.public_key == block.public_key
        assert restored.sequence_number == block.sequence_number
        assert restored.block_hash == block.block_hash
        assert restored.signature == block.signature
        assert restored.transaction == block.transaction
        assert verify_block(restored)


class TestProposalAgreementPair:
    def test_proposal_has_link_seq_zero(self):
        identity = Identity()
        proposal = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"interaction_type": "service"},
        )

        assert proposal.link_sequence_number == 0
        assert proposal.block_type == BlockType.PROPOSAL

    def test_agreement_links_back_to_proposal(self):
        identity_a = Identity()
        identity_b = Identity()

        proposal = create_half_block(
            identity=identity_a,
            sequence_number=1,
            link_public_key=identity_b.pubkey_hex,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"interaction_type": "service"},
        )

        agreement = create_half_block(
            identity=identity_b,
            sequence_number=1,
            link_public_key=identity_a.pubkey_hex,
            link_sequence_number=proposal.sequence_number,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.AGREEMENT,
            transaction=proposal.transaction,
        )

        assert agreement.link_public_key == identity_a.pubkey_hex
        assert agreement.link_sequence_number == 1
        assert agreement.block_type == BlockType.AGREEMENT
        assert verify_block(proposal)
        assert verify_block(agreement)

    def test_sequence_starts_at_one(self):
        identity = Identity()
        block = create_half_block(
            identity=identity,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={},
        )
        assert block.sequence_number == 1
