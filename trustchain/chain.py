"""PersonalChain — an agent's ordered sequence of half-blocks with full validation.

v2: Works with both the new HalfBlock model (from blockstore) and the legacy
    Block/InteractionRecord model (v1 compat shims).
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Union

from trustchain.block import GENESIS_HASH, Block
from trustchain.exceptions import (
    DuplicateSequenceError,
    InvalidBlockError,
    PrevHashMismatchError,
    SequenceGapError,
    SignatureError,
)
from trustchain.halfblock import HalfBlock as V2HalfBlock
from trustchain.halfblock import verify_block as verify_v2_block
from trustchain.record import InteractionRecord, verify_record


class PersonalChain:
    """An agent's personal chain — an ordered sequence of blocks keyed by sequence number.

    Supports both v1 Block objects and v2 HalfBlock objects.
    v1: seq starts at 0, uses Block(InteractionRecord) with bilateral signatures.
    v2: seq starts at 1, uses HalfBlock with single-party signatures.
    """

    def __init__(self, pubkey: str, *, v2_mode: bool = False):
        self.pubkey = pubkey
        self._blocks: Dict[int, Block] = {}  # v1 blocks
        self._v2_blocks: Dict[int, V2HalfBlock] = {}  # v2 half-blocks
        self._v2_mode = v2_mode

    @property
    def head(self) -> Optional[Union[Block, V2HalfBlock]]:
        """The block at the highest sequence number, or None if empty."""
        if self._v2_mode:
            if not self._v2_blocks:
                return None
            return self._v2_blocks[max(self._v2_blocks)]
        if not self._blocks:
            return None
        return self._blocks[max(self._blocks)]

    @property
    def head_hash(self) -> str:
        """Hash of the head block, or GENESIS_HASH if chain is empty."""
        if self._v2_mode:
            h = self.head
            if h is None:
                return GENESIS_HASH
            return h.block_hash  # type: ignore[union-attr]
        h = self.head
        if h is None:
            return GENESIS_HASH
        return h.record.record_hash  # type: ignore[union-attr]

    @property
    def next_seq(self) -> int:
        """The next expected sequence number."""
        if self._v2_mode:
            if not self._v2_blocks:
                return 1  # v2 sequences start at 1
            return max(self._v2_blocks) + 1
        if not self._blocks:
            return 0
        return max(self._blocks) + 1

    @property
    def length(self) -> int:
        """Number of blocks in this chain."""
        if self._v2_mode:
            return len(self._v2_blocks)
        return len(self._blocks)

    def get_block(self, seq: int) -> Optional[Union[Block, V2HalfBlock]]:
        """Get the block at a given sequence number."""
        if self._v2_mode:
            return self._v2_blocks.get(seq)
        return self._blocks.get(seq)

    # ---- v2 methods ----

    def append_v2(self, block: V2HalfBlock) -> None:
        """Validate and append a v2 half-block to this chain.

        Checks:
        1. Block's public_key matches this chain's pubkey
        2. Sequence number == next_seq
        3. previous_hash matches head_hash
        4. Signature valid
        """
        if block.public_key != self.pubkey:
            raise InvalidBlockError(
                self.pubkey, block.sequence_number,
                detail="block does not belong to this chain",
            )

        seq = block.sequence_number

        if seq in self._v2_blocks:
            raise DuplicateSequenceError(self.pubkey, seq)
        if seq != self.next_seq:
            raise SequenceGapError(self.pubkey, expected=self.next_seq, got=seq)

        if block.previous_hash != self.head_hash:
            raise PrevHashMismatchError(
                self.pubkey, seq,
                expected=self.head_hash,
                got=block.previous_hash,
            )

        if not verify_v2_block(block):
            raise SignatureError(self.pubkey, seq)

        self._v2_blocks[seq] = block

    def validate_v2(self) -> bool:
        """Full chain validation for v2 half-blocks.

        Returns True if valid, raises on first error.
        """
        if not self._v2_blocks:
            return True

        expected_seq = 1  # v2 seqs start at 1
        expected_hash = GENESIS_HASH

        for seq in sorted(self._v2_blocks):
            block = self._v2_blocks[seq]

            if seq != expected_seq:
                raise SequenceGapError(self.pubkey, expected=expected_seq, got=seq)

            if block.previous_hash != expected_hash:
                raise PrevHashMismatchError(
                    self.pubkey, seq,
                    expected=expected_hash,
                    got=block.previous_hash,
                )

            if not verify_v2_block(block):
                raise SignatureError(self.pubkey, seq)

            expected_hash = block.block_hash
            expected_seq = seq + 1

        return True

    def integrity_score_v2(self) -> float:
        """Compute chain integrity as a float in [0.0, 1.0] for v2 blocks."""
        if not self._v2_blocks:
            return 1.0

        total = len(self._v2_blocks)
        valid = 0
        expected_hash = GENESIS_HASH

        for seq in sorted(self._v2_blocks):
            block = self._v2_blocks[seq]

            if seq != valid + 1:
                break

            if block.previous_hash != expected_hash:
                break

            if not verify_v2_block(block):
                break

            expected_hash = block.block_hash
            valid += 1

        return valid / total

    @classmethod
    def from_store(cls, pubkey: str, store) -> PersonalChain:
        """Build a v2 PersonalChain from a BlockStore.

        Loads all blocks for pubkey from the store and populates the chain.
        Does NOT re-validate during load (use validate_v2() after).
        """
        chain = cls(pubkey, v2_mode=True)
        blocks = store.get_chain(pubkey)
        for block in blocks:
            chain._v2_blocks[block.sequence_number] = block
        return chain

    def v2_blocks_in_order(self) -> List[V2HalfBlock]:
        """Return v2 blocks sorted by sequence number."""
        return [self._v2_blocks[s] for s in sorted(self._v2_blocks)]

    # ---- v1 legacy methods ----

    def append(self, block: Block) -> None:
        """Validate and append a v1 block to this chain.

        Checks:
        1. Block involves this agent
        2. Sequence number == next_seq
        3. prev_hash matches head_hash
        4. Both signatures valid
        """
        # 1. Block must involve this agent
        if not block.involves(self.pubkey):
            raise InvalidBlockError(
                self.pubkey, -1,
                detail="block does not involve this agent",
            )

        half = block.half_for(self.pubkey)
        seq = half.sequence_number

        # 2. Sequence number check
        if seq in self._blocks:
            raise DuplicateSequenceError(self.pubkey, seq)
        if seq != self.next_seq:
            raise SequenceGapError(self.pubkey, expected=self.next_seq, got=seq)

        # 3. Previous hash check
        if half.previous_hash != self.head_hash:
            raise PrevHashMismatchError(
                self.pubkey, seq,
                expected=self.head_hash,
                got=half.previous_hash,
            )

        # 4. Signature verification
        if not verify_record(block.record):
            raise SignatureError(self.pubkey, seq)

        self._blocks[seq] = block

    def validate(self) -> bool:
        """Full chain validation from genesis to head.

        Delegates to v2 validation in v2_mode.
        Re-checks every block in sequence order: hash links and signatures.
        Returns True if valid, raises on first error.
        """
        if self._v2_mode:
            return self.validate_v2()

        expected_seq = 0
        expected_hash = GENESIS_HASH

        for seq in sorted(self._blocks):
            block = self._blocks[seq]
            half = block.half_for(self.pubkey)

            # Contiguous sequence
            if seq != expected_seq:
                raise SequenceGapError(self.pubkey, expected=expected_seq, got=seq)

            # Hash link
            if half.previous_hash != expected_hash:
                raise PrevHashMismatchError(
                    self.pubkey, seq,
                    expected=expected_hash,
                    got=half.previous_hash,
                )

            # Signature
            if not verify_record(block.record):
                raise SignatureError(self.pubkey, seq)

            expected_hash = block.record.record_hash
            expected_seq = seq + 1

        return True

    def integrity_score(self) -> float:
        """Compute chain integrity as a float in [0.0, 1.0].

        Delegates to v2 scoring in v2_mode.
        """
        if self._v2_mode:
            return self.integrity_score_v2()

        if not self._blocks:
            return 1.0

        total = len(self._blocks)
        valid = 0
        expected_hash = GENESIS_HASH

        for seq in sorted(self._blocks):
            block = self._blocks[seq]
            half = block.half_for(self.pubkey)

            # Check contiguity
            if seq != valid:
                break

            # Check hash link
            if half.previous_hash != expected_hash:
                break

            # Check signature
            if not verify_record(block.record):
                break

            expected_hash = block.record.record_hash
            valid += 1

        return valid / total

    @classmethod
    def from_records(cls, pubkey: str, records: List[InteractionRecord]) -> PersonalChain:
        """Build a PersonalChain from a list of v1 InteractionRecords.

        .. deprecated:: 2.0
            Use ``PersonalChain.from_store()`` with a BlockStore for v2 half-blocks.
        """
        chain = cls(pubkey)

        # Filter records involving this agent
        relevant: List[InteractionRecord] = []
        for r in records:
            if r.agent_a_pubkey == pubkey or r.agent_b_pubkey == pubkey:
                relevant.append(r)

        # Sort by this agent's sequence number
        def _seq_for(record: InteractionRecord) -> int:
            if record.agent_a_pubkey == pubkey:
                return record.seq_a
            return record.seq_b

        relevant.sort(key=_seq_for)

        for r in relevant:
            chain.append(Block(r))

        return chain

    def blocks_in_order(self) -> List[Block]:
        """Return v1 blocks sorted by sequence number."""
        return [self._blocks[s] for s in sorted(self._blocks)]

    def __repr__(self) -> str:
        mode = "v2" if self._v2_mode else "v1"
        return (
            f"PersonalChain(pubkey={self.pubkey[:8]}..., "
            f"length={self.length}, next_seq={self.next_seq}, mode={mode})"
        )


# ---- Convenience functions (v1 compat) ----


def validate_chain_for(pubkey: str, records: List[InteractionRecord]) -> bool:
    """Build and validate a personal chain for the given agent.

    .. deprecated:: 2.0
        Use ``TrustChainProtocol.validate_chain()`` for v2.
    """
    chain = PersonalChain.from_records(pubkey, records)
    return chain.validate()


def compute_chain_integrity(pubkey: str, records: List[InteractionRecord]) -> float:
    """Compute chain integrity score for the given agent.

    .. deprecated:: 2.0
        Use ``TrustChainProtocol.integrity_score()`` for v2.
    """
    chain = PersonalChain(pubkey)

    relevant: List[InteractionRecord] = []
    for r in records:
        if r.agent_a_pubkey == pubkey or r.agent_b_pubkey == pubkey:
            relevant.append(r)

    def _seq_for(record: InteractionRecord) -> int:
        if record.agent_a_pubkey == pubkey:
            return record.seq_a
        return record.seq_b

    relevant.sort(key=_seq_for)

    for r in relevant:
        block = Block(r)
        seq = block.half_for(pubkey).sequence_number
        if seq not in chain._blocks:
            chain._blocks[seq] = block

    return chain.integrity_score()
