"""Half-block data model for TrustChain v2.

Each agent creates and signs their own half-block independently.
A transaction consists of two linked half-blocks: a PROPOSAL and an AGREEMENT.

Per the TU Delft TrustChain protocol (Otte, de Vos, Pouwelse 2020;
IETF draft-pouwelse-trustchain-01).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from trustchain.identity import Identity

GENESIS_HASH = "0" * 64


class BlockType(str, Enum):
    PROPOSAL = "proposal"
    AGREEMENT = "agreement"
    CHECKPOINT = "checkpoint"


@dataclass
class HalfBlock:
    """One half of a TrustChain transaction — signed by a single agent.

    Key differences from v1 Block:
    - Each agent signs ONLY their own half-block (not both signing one record)
    - link_sequence_number=0 means proposal (counterparty hasn't agreed yet)
    - block_hash computed with signature field zeroed (per IETF draft)
    - Sequence numbers start at 1 (0 is reserved for unknown/unlinked)
    """

    public_key: str  # Ed25519 pubkey hex of block owner
    sequence_number: int  # 1-based, monotonically increasing
    link_public_key: str  # counterparty pubkey
    link_sequence_number: int  # counterparty seq (0 = proposal, >0 = agreement)
    previous_hash: str  # hash of owner's previous block (GENESIS for seq=1)
    signature: str  # Ed25519 signature hex by owner only
    block_type: str  # "proposal", "agreement", or "checkpoint"
    transaction: Dict[str, Any]  # interaction_type, outcome, timestamp, payload
    block_hash: str  # SHA-256 of all fields with signature zeroed
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "public_key": self.public_key,
            "sequence_number": self.sequence_number,
            "link_public_key": self.link_public_key,
            "link_sequence_number": self.link_sequence_number,
            "previous_hash": self.previous_hash,
            "signature": self.signature,
            "block_type": self.block_type,
            "transaction": self.transaction,
            "block_hash": self.block_hash,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HalfBlock:
        """Deserialize from a dictionary."""
        return cls(
            public_key=data["public_key"],
            sequence_number=data["sequence_number"],
            link_public_key=data["link_public_key"],
            link_sequence_number=data["link_sequence_number"],
            previous_hash=data["previous_hash"],
            signature=data["signature"],
            block_type=data["block_type"],
            transaction=data["transaction"],
            block_hash=data["block_hash"],
            timestamp=data["timestamp"],
        )


def compute_block_hash(block: HalfBlock) -> str:
    """Compute SHA-256 hash of a half-block with signature zeroed.

    Per the IETF draft, the hash is computed over all fields except
    the signature itself (which would create a circular dependency).
    """
    hashable = {
        "public_key": block.public_key,
        "sequence_number": block.sequence_number,
        "link_public_key": block.link_public_key,
        "link_sequence_number": block.link_sequence_number,
        "previous_hash": block.previous_hash,
        "signature": "",  # zeroed for hashing
        "block_type": block.block_type,
        "transaction": block.transaction,
        "timestamp": block.timestamp,
    }
    payload = json.dumps(hashable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def sign_block(block: HalfBlock, identity: Identity) -> HalfBlock:
    """Sign a half-block with the given identity.

    Computes the block hash (with signature zeroed), signs the hash,
    then sets both signature and block_hash on the block.

    Returns the block (mutated in place for convenience).
    """
    if identity.pubkey_hex != block.public_key:
        raise ValueError(
            f"Identity pubkey {identity.pubkey_hex[:16]}... does not match "
            f"block public_key {block.public_key[:16]}..."
        )
    # Compute hash with signature zeroed
    block.signature = ""
    block.block_hash = compute_block_hash(block)
    # Sign the hash
    sig = identity.sign(block.block_hash.encode())
    block.signature = sig.hex()
    return block


def verify_block(block: HalfBlock) -> bool:
    """Verify a half-block's hash and signature.

    1. Recompute hash with signature zeroed — must match block_hash
    2. Verify Ed25519 signature over the block_hash
    """
    # Check hash integrity
    expected_hash = compute_block_hash(block)
    if expected_hash != block.block_hash:
        return False

    # Check signature
    try:
        pubkey_bytes = bytes.fromhex(block.public_key)
        sig_bytes = bytes.fromhex(block.signature)
    except (ValueError, TypeError):
        return False

    return Identity.verify(
        block.block_hash.encode(),
        sig_bytes,
        pubkey_bytes,
    )


def create_half_block(
    identity: Identity,
    sequence_number: int,
    link_public_key: str,
    link_sequence_number: int,
    previous_hash: str,
    block_type: str,
    transaction: Dict[str, Any],
    timestamp: Optional[float] = None,
) -> HalfBlock:
    """Create, hash, and sign a new half-block.

    Convenience function that constructs the block, computes its hash,
    and signs it in one step.
    """
    block = HalfBlock(
        public_key=identity.pubkey_hex,
        sequence_number=sequence_number,
        link_public_key=link_public_key,
        link_sequence_number=link_sequence_number,
        previous_hash=previous_hash,
        signature="",
        block_type=block_type,
        transaction=transaction,
        block_hash="",
        timestamp=timestamp if timestamp is not None else time.time(),
    )
    return sign_block(block, identity)
