"""GatewayNode — MCP gateway that is also a TrustChain node.

All participants are TrustChain-aware. The gateway itself runs a TrustChain
node that creates proper proposal/agreement pairs for every tool call.
"""

from __future__ import annotations

import logging
import time as _time
from typing import Any, Dict, Optional, Tuple

from trustchain.api import TrustChainNode
from trustchain.blockstore import BlockStore, MemoryBlockStore
from trustchain.identity import Identity
from trustchain.trust import TrustEngine

logger = logging.getLogger("trustchain.gateway.node")

# Lightweight interaction count cache.  Avoids O(N) chain scans on every
# tool call through the middleware hot path.  Entries expire after TTL seconds.
_CACHE_TTL = 5.0  # seconds


class GatewayNode(TrustChainNode):
    """MCP gateway that is also a TrustChain node.

    - Each tool call triggers a TrustChain transaction with the target server
    - Trust gating uses TrustEngine scores from the live chain
    - Peers must be registered TrustChain nodes
    """

    def __init__(
        self,
        identity: Identity,
        store: BlockStore,
        host: str = "0.0.0.0",
        port: int = 8100,
        seed_nodes: Optional[list] = None,
    ) -> None:
        super().__init__(identity, store, host, port)
        self.trust_engine = TrustEngine(
            store,
            seed_nodes=seed_nodes or [identity.pubkey_hex],
        )
        # (peer_pubkey) -> (count, timestamp)
        self._interaction_count_cache: Dict[str, Tuple[int, float]] = {}

    def get_trust_score(self, peer_pubkey: str) -> float:
        """Get the trust score for a peer using the TrustEngine."""
        return self.trust_engine.compute_trust(peer_pubkey)

    def _count_peer_interactions(self, peer_pubkey: str) -> int:
        """Count total interactions with a peer in both directions.

        Counts our proposals to them (link_public_key == peer on our chain)
        plus their proposals to us (link_public_key == our pubkey on their chain).

        Results are cached for _CACHE_TTL seconds to avoid O(N) chain scans
        on every tool call.
        """
        now = _time.monotonic()
        cached = self._interaction_count_cache.get(peer_pubkey)
        if cached is not None:
            count, ts = cached
            if now - ts < _CACHE_TTL:
                return count

        our_chain = self.store.get_chain(self.pubkey)
        outbound = sum(1 for b in our_chain if b.link_public_key == peer_pubkey)
        peer_chain = self.store.get_chain(peer_pubkey)
        inbound = sum(1 for b in peer_chain if b.link_public_key == self.pubkey)
        total = outbound + inbound

        self._interaction_count_cache[peer_pubkey] = (total, now)
        return total

    def invalidate_count_cache(self, peer_pubkey: str) -> None:
        """Invalidate the interaction count cache for a peer.

        Call after recording a new interaction so the next count reflects it.
        """
        self._interaction_count_cache.pop(peer_pubkey, None)

    def get_chain_integrity(self, peer_pubkey: str) -> float:
        """Get chain integrity for a peer."""
        return self.trust_engine.compute_chain_integrity(peer_pubkey)

    async def trusted_transact(
        self,
        peer_pubkey: str,
        transaction: Dict[str, Any],
        min_trust: float = 0.0,
        bootstrap_interactions: int = 3,
    ) -> Dict[str, Any]:
        """Execute a transaction with trust gating.

        Returns dict with: accepted, proposal, agreement, trust_score, error
        """
        trust_score = self.get_trust_score(peer_pubkey)
        # Count interactions with this peer (both directions: our proposals
        # to them + their proposals to us that we agreed to)
        interaction_count = self._count_peer_interactions(peer_pubkey)
        is_bootstrap = interaction_count < bootstrap_interactions

        if not is_bootstrap and trust_score < min_trust:
            return {
                "accepted": False,
                "trust_score": trust_score,
                "error": f"Trust {trust_score:.3f} < threshold {min_trust:.3f}",
            }

        try:
            proposal, agreement = await self.transact(peer_pubkey, transaction)
        except (ValueError, ConnectionError, OSError) as exc:
            return {
                "accepted": False,
                "trust_score": trust_score,
                "error": str(exc),
            }
        updated_trust = self.get_trust_score(peer_pubkey)

        return {
            "accepted": agreement is not None,
            "proposal": proposal,
            "agreement": agreement,
            "trust_score": updated_trust,
            "error": None if agreement else "Peer rejected or unreachable",
        }
