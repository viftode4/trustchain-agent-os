"""TrustChain v2 protocol engine — two-phase proposal/agreement flow.

Implements the real TU Delft TrustChain protocol:
  1. A creates a PROPOSAL half-block (link_sequence_number=0), signs it, sends to B
  2. B validates, creates an AGREEMENT half-block linking back to A's, signs it
  3. Both parties store both half-blocks

Per Otte, de Vos, Pouwelse 2020; IETF draft-pouwelse-trustchain-01.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from trustchain.blockstore import BlockStore
from trustchain.exceptions import (
    AgreementError,
    PrevHashMismatchError,
    ProposalError,
    SequenceGapError,
    SignatureError,
)
from trustchain.halfblock import (
    GENESIS_HASH,
    BlockType,
    HalfBlock,
    create_half_block,
    verify_block,
)
from trustchain.identity import Identity

logger = logging.getLogger("trustchain.protocol")


class TrustChainProtocol:
    """Two-phase proposal/agreement protocol engine.

    Each instance is bound to a single identity and block store.
    """

    def __init__(self, identity: Identity, store: BlockStore) -> None:
        self.identity = identity
        self.store = store

    @property
    def pubkey(self) -> str:
        return self.identity.pubkey_hex

    def create_proposal(
        self,
        counterparty_pubkey: str,
        transaction: Dict[str, Any],
        timestamp: Optional[float] = None,
    ) -> HalfBlock:
        """Create and sign a PROPOSAL half-block.

        - seq = store.get_latest_seq(my_pubkey) + 1
        - prev_hash = store.get_head_hash(my_pubkey)
        - link_sequence_number = 0 (unknown until agreement)
        - Sign with own key, store locally
        """
        seq = self.store.get_latest_seq(self.pubkey) + 1
        prev_hash = self.store.get_head_hash(self.pubkey)

        block = create_half_block(
            identity=self.identity,
            sequence_number=seq,
            link_public_key=counterparty_pubkey,
            link_sequence_number=0,  # proposal: counterparty seq unknown
            previous_hash=prev_hash,
            block_type=BlockType.PROPOSAL,
            transaction=transaction,
            timestamp=timestamp or time.time(),
        )

        self.store.add_block(block)
        logger.debug(
            "Created proposal: %s seq=%d -> %s",
            self.identity.short_id,
            seq,
            counterparty_pubkey[:16],
        )
        return block

    def receive_proposal(self, proposal: HalfBlock) -> bool:
        """Validate an incoming proposal from a counterparty.

        Checks:
        - Block type is PROPOSAL
        - link_public_key matches our pubkey (proposal is for us)
        - Signature is valid
        - Hash is valid
        - Sequence is valid for proposer's known chain

        Returns True if valid. Raises on failure.
        """
        if proposal.block_type != BlockType.PROPOSAL:
            raise ProposalError(
                proposal.public_key,
                proposal.sequence_number,
                f"Expected proposal, got {proposal.block_type}",
            )

        if proposal.link_public_key != self.pubkey:
            raise ProposalError(
                proposal.public_key,
                proposal.sequence_number,
                f"Proposal not addressed to us ({self.pubkey[:16]}...)",
            )

        if not verify_block(proposal):
            raise SignatureError(
                proposal.public_key,
                proposal.sequence_number,
            )

        # Validate sequence continuity for proposer's chain (if we know it)
        known_seq = self.store.get_latest_seq(proposal.public_key)
        if known_seq > 0:
            if proposal.sequence_number <= known_seq:
                raise ProposalError(
                    proposal.public_key,
                    proposal.sequence_number,
                    f"Sequence {proposal.sequence_number} <= known latest {known_seq}",
                )
            if proposal.sequence_number > known_seq + 1:
                raise SequenceGapError(
                    pubkey=proposal.public_key,
                    expected=known_seq + 1,
                    got=proposal.sequence_number,
                )

            # Verify previous_hash links to our stored predecessor
            expected_prev = self.store.get_head_hash(proposal.public_key)
            if proposal.previous_hash != expected_prev:
                raise PrevHashMismatchError(
                    pubkey=proposal.public_key,
                    seq=proposal.sequence_number,
                    expected=expected_prev,
                    got=proposal.previous_hash,
                )

        # Store the proposal (counterparty's block, in our store for verification)
        try:
            self.store.add_block(proposal)
        except ValueError:
            pass  # Already stored (idempotent)

        logger.debug(
            "Received valid proposal from %s seq=%d",
            proposal.public_key[:16],
            proposal.sequence_number,
        )
        return True

    def create_agreement(
        self,
        proposal: HalfBlock,
        timestamp: Optional[float] = None,
    ) -> HalfBlock:
        """Create an AGREEMENT half-block in response to a valid proposal.

        - seq = store.get_latest_seq(my_pubkey) + 1
        - prev_hash = store.get_head_hash(my_pubkey)
        - link_public_key = proposal.public_key
        - link_sequence_number = proposal.sequence_number
        - transaction = copy of proposal's transaction
        - Sign with own key, store locally
        """
        if proposal.block_type != BlockType.PROPOSAL:
            raise AgreementError(
                self.pubkey,
                detail=f"Cannot agree to non-proposal block type: {proposal.block_type}",
            )

        if proposal.link_public_key != self.pubkey:
            raise AgreementError(
                self.pubkey,
                detail="Proposal is not addressed to us",
            )

        # Defense-in-depth: verify proposal integrity even if receive_proposal was called
        if not verify_block(proposal):
            raise SignatureError(
                proposal.public_key,
                proposal.sequence_number,
            )

        seq = self.store.get_latest_seq(self.pubkey) + 1
        prev_hash = self.store.get_head_hash(self.pubkey)

        block = create_half_block(
            identity=self.identity,
            sequence_number=seq,
            link_public_key=proposal.public_key,
            link_sequence_number=proposal.sequence_number,
            previous_hash=prev_hash,
            block_type=BlockType.AGREEMENT,
            transaction=proposal.transaction,
            timestamp=timestamp or time.time(),
        )

        self.store.add_block(block)
        logger.debug(
            "Created agreement: %s seq=%d -> %s seq=%d",
            self.identity.short_id,
            seq,
            proposal.public_key[:16],
            proposal.sequence_number,
        )
        return block

    def receive_agreement(self, agreement: HalfBlock) -> bool:
        """Validate and store an incoming agreement.

        Checks:
        - Block type is AGREEMENT
        - It links back to one of our proposals
        - Signature and hash are valid
        - The linked proposal exists in our store

        Returns True if valid. Raises on failure.
        """
        if agreement.block_type != BlockType.AGREEMENT:
            raise AgreementError(
                agreement.public_key,
                agreement.sequence_number,
                f"Expected agreement, got {agreement.block_type}",
            )

        if agreement.link_public_key != self.pubkey:
            raise AgreementError(
                agreement.public_key,
                agreement.sequence_number,
                "Agreement does not link to our chain",
            )

        if not verify_block(agreement):
            raise SignatureError(
                agreement.public_key,
                agreement.sequence_number,
            )

        # Verify the linked proposal exists
        proposal = self.store.get_block(
            self.pubkey, agreement.link_sequence_number
        )
        if proposal is None:
            raise AgreementError(
                agreement.public_key,
                agreement.sequence_number,
                f"No proposal found at ({self.pubkey[:16]}..., seq={agreement.link_sequence_number})",
            )

        if proposal.block_type != BlockType.PROPOSAL:
            raise AgreementError(
                agreement.public_key,
                agreement.sequence_number,
                f"Linked block is not a proposal: {proposal.block_type}",
            )

        # Verify transaction content matches the proposal
        if agreement.transaction != proposal.transaction:
            raise AgreementError(
                agreement.public_key,
                agreement.sequence_number,
                "Agreement transaction does not match proposal transaction",
            )

        # Store the agreement
        try:
            self.store.add_block(agreement)
        except ValueError:
            pass  # Already stored (idempotent)

        logger.debug(
            "Received valid agreement from %s seq=%d for our proposal seq=%d",
            agreement.public_key[:16],
            agreement.sequence_number,
            agreement.link_sequence_number,
        )
        return True

    def validate_chain(self, pubkey: str) -> bool:
        """Full chain validation for a given agent.

        Checks:
        - Contiguous sequence numbers starting at 1
        - Hash links: each block's previous_hash matches prior block's block_hash
        - All signatures valid
        """
        chain = self.store.get_chain(pubkey)
        if not chain:
            return True  # Empty chain is valid

        for i, block in enumerate(chain):
            expected_seq = i + 1
            if block.sequence_number != expected_seq:
                raise SequenceGapError(
                    pubkey=pubkey,
                    expected=expected_seq,
                    got=block.sequence_number,
                )

            expected_prev = (
                GENESIS_HASH if i == 0 else chain[i - 1].block_hash
            )
            if block.previous_hash != expected_prev:
                raise PrevHashMismatchError(
                    pubkey=pubkey,
                    seq=block.sequence_number,
                    expected=expected_prev,
                    got=block.previous_hash,
                )

            if not verify_block(block):
                raise SignatureError(pubkey=pubkey, seq=block.sequence_number)

        return True

    def integrity_score(self, pubkey: str) -> float:
        """Chain integrity as float [0.0, 1.0].

        Returns fraction of blocks that are valid before the first break.
        """
        chain = self.store.get_chain(pubkey)
        if not chain:
            return 1.0

        valid_count = 0
        for i, block in enumerate(chain):
            expected_seq = i + 1
            if block.sequence_number != expected_seq:
                break

            expected_prev = (
                GENESIS_HASH if i == 0 else chain[i - 1].block_hash
            )
            if block.previous_hash != expected_prev:
                break

            if not verify_block(block):
                break

            valid_count += 1

        return valid_count / len(chain)
