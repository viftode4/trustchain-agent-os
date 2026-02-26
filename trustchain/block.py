"""Block projections for TrustChain — wraps InteractionRecord without replacing it."""

from __future__ import annotations

from dataclasses import dataclass

from trustchain.record import InteractionRecord

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class HalfBlock:
    """One side of a bilateral block — an agent's view of the interaction.

    This is a read-only projection; the source of truth is always the
    underlying InteractionRecord.
    """

    public_key: str
    sequence_number: int
    previous_hash: str
    link_public_key: str
    link_sequence_number: int
    block_hash: str
    interaction_type: str
    outcome: str
    timestamp: float


class Block:
    """Wraps an InteractionRecord to provide half-block projections.

    Does NOT replace InteractionRecord — this is a convenience layer that
    projects the bilateral record into per-agent half-blocks for chain
    validation.
    """

    __slots__ = ("record",)

    def __init__(self, record: InteractionRecord):
        self.record = record

    @property
    def half_a(self) -> HalfBlock:
        """Agent A's half-block projection."""
        return HalfBlock(
            public_key=self.record.agent_a_pubkey,
            sequence_number=self.record.seq_a,
            previous_hash=self.record.prev_hash_a,
            link_public_key=self.record.agent_b_pubkey,
            link_sequence_number=self.record.seq_b,
            block_hash=self.record.record_hash,
            interaction_type=self.record.interaction_type,
            outcome=self.record.outcome,
            timestamp=self.record.timestamp,
        )

    @property
    def half_b(self) -> HalfBlock:
        """Agent B's half-block projection."""
        return HalfBlock(
            public_key=self.record.agent_b_pubkey,
            sequence_number=self.record.seq_b,
            previous_hash=self.record.prev_hash_b,
            link_public_key=self.record.agent_a_pubkey,
            link_sequence_number=self.record.seq_a,
            block_hash=self.record.record_hash,
            interaction_type=self.record.interaction_type,
            outcome=self.record.outcome,
            timestamp=self.record.timestamp,
        )

    def half_for(self, pubkey: str) -> HalfBlock:
        """Return the half-block for the given agent, or raise ValueError."""
        if pubkey == self.record.agent_a_pubkey:
            return self.half_a
        if pubkey == self.record.agent_b_pubkey:
            return self.half_b
        raise ValueError(
            f"Agent {pubkey[:16]}... is not a party to this block"
        )

    def counterparty_half(self, pubkey: str) -> HalfBlock:
        """Return the counterparty's half-block, or raise ValueError."""
        if pubkey == self.record.agent_a_pubkey:
            return self.half_b
        if pubkey == self.record.agent_b_pubkey:
            return self.half_a
        raise ValueError(
            f"Agent {pubkey[:16]}... is not a party to this block"
        )

    def involves(self, pubkey: str) -> bool:
        """Check if this block involves the given agent."""
        return pubkey in (self.record.agent_a_pubkey, self.record.agent_b_pubkey)

    def __repr__(self) -> str:
        return (
            f"Block(a={self.record.agent_a_pubkey[:8]}... seq={self.record.seq_a}, "
            f"b={self.record.agent_b_pubkey[:8]}... seq={self.record.seq_b}, "
            f"type={self.record.interaction_type})"
        )
