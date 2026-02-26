"""Bilateral interaction records — the core of TrustChain."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Optional

from trustchain.identity import Identity


@dataclass
class InteractionRecord:
    """A bilateral signed interaction record between two agents."""

    agent_a_pubkey: str  # hex
    agent_b_pubkey: str  # hex
    seq_a: int
    seq_b: int
    prev_hash_a: str
    prev_hash_b: str
    interaction_type: str  # service, payment, etc.
    outcome: str  # completed, failed, disputed
    timestamp: float = field(default_factory=time.time)
    sig_a: bytes = b""
    sig_b: bytes = b""

    @property
    def payload(self) -> dict:
        """The signable payload (everything except signatures)."""
        return {
            "agent_a_pubkey": self.agent_a_pubkey,
            "agent_b_pubkey": self.agent_b_pubkey,
            "seq_a": self.seq_a,
            "seq_b": self.seq_b,
            "prev_hash_a": self.prev_hash_a,
            "prev_hash_b": self.prev_hash_b,
            "interaction_type": self.interaction_type,
            "outcome": self.outcome,
            "timestamp": self.timestamp,
        }

    @property
    def payload_bytes(self) -> bytes:
        return json.dumps(self.payload, sort_keys=True).encode()

    @property
    def record_hash(self) -> str:
        return hashlib.sha256(self.payload_bytes).hexdigest()

    def to_dict(self) -> dict:
        d = self.payload
        d["sig_a"] = self.sig_a.hex()
        d["sig_b"] = self.sig_b.hex()
        d["record_hash"] = self.record_hash
        return d


def create_record(
    identity_a: Identity,
    identity_b: Identity,
    seq_a: int,
    seq_b: int,
    prev_hash_a: str,
    prev_hash_b: str,
    interaction_type: str,
    outcome: str,
) -> InteractionRecord:
    """Create a bilateral record — both parties sign the same payload."""
    record = InteractionRecord(
        agent_a_pubkey=identity_a.pubkey_hex,
        agent_b_pubkey=identity_b.pubkey_hex,
        seq_a=seq_a,
        seq_b=seq_b,
        prev_hash_a=prev_hash_a,
        prev_hash_b=prev_hash_b,
        interaction_type=interaction_type,
        outcome=outcome,
    )
    # Both parties sign the same canonical payload
    record.sig_a = identity_a.sign(record.payload_bytes)
    record.sig_b = identity_b.sign(record.payload_bytes)
    return record


def verify_record(record: InteractionRecord) -> bool:
    """Verify both signatures on a bilateral record."""
    payload = record.payload_bytes
    a_ok = Identity.verify(
        payload, record.sig_a, bytes.fromhex(record.agent_a_pubkey)
    )
    b_ok = Identity.verify(
        payload, record.sig_b, bytes.fromhex(record.agent_b_pubkey)
    )
    return a_ok and b_ok
