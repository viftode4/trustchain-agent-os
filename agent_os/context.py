"""TrustContext — injectable context for trust-gated service handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trustchain.identity import Identity
from trustchain.store import RecordStore
from trustchain.trust import compute_trust


@dataclass
class TrustContext:
    """Context passed to trust-gated service handlers.

    Provides the caller's identity info, trust score, and access
    to trust queries during request handling.
    """

    caller_pubkey: str
    caller_trust: float
    caller_history: int
    agent_identity: Identity
    store: RecordStore
    bootstrap_interactions: int = 3
    node: object = None  # v2 TrustChainNode, set when both sides have nodes

    @property
    def is_trusted(self) -> bool:
        """Whether the caller has any established trust (> 0)."""
        return self.caller_trust > 0.0

    @property
    def is_bootstrap(self) -> bool:
        """Whether the caller is still in bootstrap mode."""
        return self.caller_history < self.bootstrap_interactions

    def check_trust(self, pubkey: str) -> float:
        """Look up the trust score for any agent."""
        return compute_trust(pubkey, self.store)

    @classmethod
    def create(
        cls,
        caller_pubkey: str,
        agent_identity: Identity,
        store: RecordStore,
    ) -> TrustContext:
        """Create a TrustContext for a caller."""
        return cls(
            caller_pubkey=caller_pubkey,
            caller_trust=compute_trust(caller_pubkey, store),
            caller_history=len(store.get_records_for(caller_pubkey)),
            agent_identity=agent_identity,
            store=store,
        )
