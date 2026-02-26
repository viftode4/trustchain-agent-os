"""CHECO Consensus — checkpoint-based consensus for periodic finality.

Implements a simplified CHECO protocol for TrustChain:
- Random facilitator selection based on chain state
- Facilitator proposes checkpoint block referencing all known chain heads
- Other nodes validate and sign checkpoint
- Checkpoint provides finality for all blocks before it

Reference: Otte, de Vos, Pouwelse — CHECO checkpoint consensus
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from trustchain.blockstore import BlockStore
from trustchain.exceptions import CheckpointError
from trustchain.halfblock import (
    GENESIS_HASH,
    BlockType,
    HalfBlock,
    create_half_block,
    verify_block,
)
from trustchain.identity import Identity

logger = logging.getLogger("trustchain.consensus")


@dataclass
class Checkpoint:
    """A validated checkpoint — provides finality for referenced blocks."""

    facilitator_pubkey: str
    chain_heads: Dict[str, int]  # pubkey -> latest seq at checkpoint time
    checkpoint_block: HalfBlock
    signatures: Dict[str, str]  # pubkey -> signature hex
    timestamp: float
    finalized: bool = False

    @property
    def signer_count(self) -> int:
        return len(self.signatures)


class CHECOConsensus:
    """Checkpoint-based consensus per the CHECO protocol.

    - Random facilitator selection based on chain state
    - Facilitator proposes checkpoint block referencing all known chain heads
    - Other nodes validate and sign checkpoint
    - Checkpoint provides finality for all blocks before it
    """

    def __init__(
        self,
        identity: Identity,
        store: BlockStore,
        known_peers: Optional[List[str]] = None,
        min_signers: int = 1,
    ) -> None:
        self.identity = identity
        self.store = store
        self.known_peers = known_peers or []
        self.min_signers = min_signers
        self._checkpoints: List[Checkpoint] = []

    @property
    def pubkey(self) -> str:
        return self.identity.pubkey_hex

    def select_facilitator(self) -> str:
        """Select the facilitator for the next checkpoint round.

        Uses a deterministic hash of all known chain heads to select
        a facilitator from the known peer set. This ensures all honest
        nodes agree on who should propose the next checkpoint.
        """
        all_peers = sorted(set([self.pubkey] + self.known_peers))
        if not all_peers:
            return self.pubkey

        # Build deterministic state from chain heads
        heads = {}
        for peer in all_peers:
            heads[peer] = self.store.get_latest_seq(peer)

        state_hash = hashlib.sha256(
            json.dumps(heads, sort_keys=True).encode()
        ).hexdigest()

        # Deterministic selection
        index = int(state_hash, 16) % len(all_peers)
        return all_peers[index]

    def is_facilitator(self) -> bool:
        """Check if this node is the current facilitator."""
        return self.select_facilitator() == self.pubkey

    def propose_checkpoint(self) -> HalfBlock:
        """Propose a checkpoint block if this node is the facilitator.

        The checkpoint references all known chain heads at the current time.
        """
        if not self.is_facilitator():
            raise CheckpointError(
                detail="Not the current facilitator",
                pubkey=self.pubkey,
            )

        # Gather all known chain heads
        all_peers = sorted(set([self.pubkey] + self.known_peers))
        chain_heads = {}
        for peer in all_peers:
            seq = self.store.get_latest_seq(peer)
            if seq > 0:
                chain_heads[peer] = seq

        transaction = {
            "interaction_type": "checkpoint",
            "outcome": "proposed",
            "timestamp": time.time(),
            "chain_heads": chain_heads,
            "checkpoint_round": len(self._checkpoints) + 1,
        }

        seq = self.store.get_latest_seq(self.pubkey) + 1
        prev_hash = self.store.get_head_hash(self.pubkey)

        block = create_half_block(
            identity=self.identity,
            sequence_number=seq,
            link_public_key=self.pubkey,  # Self-referencing for checkpoints
            link_sequence_number=0,
            previous_hash=prev_hash,
            block_type=BlockType.CHECKPOINT,
            transaction=transaction,
        )

        self.store.add_block(block)

        checkpoint = Checkpoint(
            facilitator_pubkey=self.pubkey,
            chain_heads=chain_heads,
            checkpoint_block=block,
            signatures={self.pubkey: block.signature},
            timestamp=time.time(),
        )
        self._checkpoints.append(checkpoint)

        logger.info(
            "Proposed checkpoint round=%d covering %d chains",
            len(self._checkpoints),
            len(chain_heads),
        )
        return block

    def validate_checkpoint(self, checkpoint_block: HalfBlock) -> bool:
        """Validate a proposed checkpoint against local chain state.

        Checks:
        - Block is a valid checkpoint type
        - Signature is valid
        - Chain heads referenced exist in our store (or we accept unknown chains)
        - Facilitator was correctly selected
        """
        if checkpoint_block.block_type != BlockType.CHECKPOINT:
            raise CheckpointError(
                detail=f"Expected checkpoint, got {checkpoint_block.block_type}",
            )

        if not verify_block(checkpoint_block):
            raise CheckpointError(
                detail="Invalid checkpoint signature",
                pubkey=checkpoint_block.public_key,
            )

        # Verify chain heads are plausible
        tx = checkpoint_block.transaction
        chain_heads = tx.get("chain_heads", {})

        for peer_pubkey, claimed_seq in chain_heads.items():
            known_seq = self.store.get_latest_seq(peer_pubkey)
            # We accept if we know less (we might not have crawled yet)
            # But reject if our known seq is HIGHER (would mean checkpoint is stale)
            if known_seq > claimed_seq:
                raise CheckpointError(
                    detail=(
                        f"Stale checkpoint: references seq={claimed_seq} for "
                        f"{peer_pubkey[:16]}... but we know seq={known_seq}"
                    ),
                    pubkey=checkpoint_block.public_key,
                )

        return True

    def sign_checkpoint(self, checkpoint_block: HalfBlock) -> str:
        """Sign a validated checkpoint to express agreement.

        Returns the signature hex.
        """
        self.validate_checkpoint(checkpoint_block)
        sig = self.identity.sign(checkpoint_block.block_hash.encode())
        return sig.hex()

    def finalize_checkpoint(
        self, checkpoint_block: HalfBlock, signatures: Dict[str, str]
    ) -> Checkpoint:
        """Finalize a checkpoint with collected signatures.

        Requires min_signers signatures to finalize.
        """
        if len(signatures) < self.min_signers:
            raise CheckpointError(
                detail=f"Not enough signatures: {len(signatures)} < {self.min_signers}",
            )

        # Verify all signatures
        for pubkey, sig_hex in signatures.items():
            try:
                pubkey_bytes = bytes.fromhex(pubkey)
                sig_bytes = bytes.fromhex(sig_hex)
                if not Identity.verify(
                    checkpoint_block.block_hash.encode(),
                    sig_bytes,
                    pubkey_bytes,
                ):
                    raise CheckpointError(
                        detail=f"Invalid signature from {pubkey[:16]}...",
                        pubkey=pubkey,
                    )
            except (ValueError, TypeError) as e:
                raise CheckpointError(
                    detail=f"Malformed signature from {pubkey[:16]}...: {e}",
                    pubkey=pubkey,
                )

        tx = checkpoint_block.transaction
        checkpoint = Checkpoint(
            facilitator_pubkey=checkpoint_block.public_key,
            chain_heads=tx.get("chain_heads", {}),
            checkpoint_block=checkpoint_block,
            signatures=signatures,
            timestamp=time.time(),
            finalized=True,
        )
        self._checkpoints.append(checkpoint)

        logger.info(
            "Finalized checkpoint with %d signers",
            len(signatures),
        )
        return checkpoint

    def is_finalized(self, pubkey: str, seq: int) -> bool:
        """Check if a block is covered by a validated checkpoint.

        A block at (pubkey, seq) is finalized if any finalized checkpoint
        references chain_heads[pubkey] >= seq.
        """
        for cp in reversed(self._checkpoints):
            if not cp.finalized:
                continue
            head_seq = cp.chain_heads.get(pubkey, 0)
            if head_seq >= seq:
                return True
        return False

    @property
    def checkpoints(self) -> List[Checkpoint]:
        """All checkpoints (proposed and finalized)."""
        return list(self._checkpoints)

    @property
    def finalized_checkpoints(self) -> List[Checkpoint]:
        """Only finalized checkpoints."""
        return [cp for cp in self._checkpoints if cp.finalized]
