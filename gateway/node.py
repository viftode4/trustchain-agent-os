"""GatewayNode — MCP gateway that is also a TrustChain node.

All participants are TrustChain-aware. The gateway itself runs a TrustChain
node that creates proper proposal/agreement pairs for every tool call.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from trustchain.api import TrustChainNode
from trustchain.blockstore import BlockStore, MemoryBlockStore
from trustchain.identity import Identity
from trustchain.trust import TrustEngine

logger = logging.getLogger("trustchain.gateway.node")


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

    def get_trust_score(self, peer_pubkey: str) -> float:
        """Get the trust score for a peer using the TrustEngine."""
        return self.trust_engine.compute_trust(peer_pubkey)

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
        # Count our interactions with this peer (blocks on our chain linking to them)
        our_chain = self.store.get_chain(self.pubkey)
        interaction_count = sum(1 for b in our_chain if b.link_public_key == peer_pubkey)
        is_bootstrap = interaction_count < bootstrap_interactions

        if not is_bootstrap and trust_score < min_trust:
            return {
                "accepted": False,
                "trust_score": trust_score,
                "error": f"Trust {trust_score:.3f} < threshold {min_trust:.3f}",
            }

        proposal, agreement = await self.transact(peer_pubkey, transaction)
        updated_trust = self.get_trust_score(peer_pubkey)

        return {
            "accepted": agreement is not None,
            "proposal": proposal,
            "agreement": agreement,
            "trust_score": updated_trust,
            "error": None if agreement else "Peer rejected or unreachable",
        }
