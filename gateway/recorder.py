"""Bilateral interaction recording for gateway ↔ upstream interactions."""

from __future__ import annotations

import logging

from trustchain.identity import Identity
from trustchain.record import InteractionRecord, create_record, verify_record
from trustchain.store import RecordStore

logger = logging.getLogger("trustchain.recorder")


class InteractionRecorder:
    """Creates and stores bilateral signed records for gateway interactions.

    The gateway controls both identities (its own + upstream's) since upstream
    MCP servers aren't TrustChain-aware. This is "gateway-attested" signing.
    """

    def __init__(self, gateway_identity: Identity, store: RecordStore):
        self.gateway_identity = gateway_identity
        self.store = store

    def record(
        self,
        upstream_identity: Identity,
        interaction_type: str = "tool_call",
        outcome: str = "completed",
    ) -> InteractionRecord:
        """Create a bilateral record between gateway and upstream server."""
        seq_a = self.store.sequence_number_for(self.gateway_identity.pubkey_hex)
        seq_b = self.store.sequence_number_for(upstream_identity.pubkey_hex)
        prev_hash_a = self.store.last_hash_for(self.gateway_identity.pubkey_hex)
        prev_hash_b = self.store.last_hash_for(upstream_identity.pubkey_hex)

        record = create_record(
            identity_a=self.gateway_identity,
            identity_b=upstream_identity,
            seq_a=seq_a,
            seq_b=seq_b,
            prev_hash_a=prev_hash_a,
            prev_hash_b=prev_hash_b,
            interaction_type=interaction_type,
            outcome=outcome,
        )

        if not verify_record(record):
            raise RuntimeError("Signature verification failed on recorded interaction")

        self.store.add_record(record)

        # Chain integrity warning
        try:
            from trustchain.chain import compute_chain_integrity

            integrity = compute_chain_integrity(
                upstream_identity.pubkey_hex,
                self.store.get_records_for(upstream_identity.pubkey_hex),
            )
            if integrity < 1.0:
                logger.warning(
                    "Chain integrity degraded for %s: %.3f",
                    upstream_identity.short_id,
                    integrity,
                )
        except Exception:
            pass  # Don't let integrity check failures break recording

        return record
