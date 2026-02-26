"""Simulated P2P network for TrustChain — replaces py-ipv8 for prototyping."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from trustchain.block import Block
from trustchain.chain import PersonalChain
from trustchain.crawler import ChainCrawler, DAGView, TamperingReport
from trustchain.identity import Identity
from trustchain.record import InteractionRecord, create_record
from trustchain.store import RecordStore

logger = logging.getLogger("trustchain.network")


@dataclass
class Peer:
    """A peer in the simulated network — wraps identity, store, and live chain."""

    identity: Identity
    store: RecordStore
    chain: PersonalChain = field(init=False)

    def __post_init__(self):
        self.chain = PersonalChain(self.identity.pubkey_hex)

    @property
    def pubkey(self) -> str:
        return self.identity.pubkey_hex

    @property
    def short_id(self) -> str:
        return self.identity.short_id

    def __repr__(self) -> str:
        return f"Peer({self.short_id}..., chain_len={self.chain.length})"


class SimulatedNetwork:
    """Simulated P2P network for TrustChain block creation and exchange.

    This is the reference implementation of the correct TrustChain protocol flow:
    reads seq/prev_hash from chain state (not store queries), creates records,
    validates+appends to both chains, then persists.
    """

    def __init__(self):
        self._peers: Dict[str, Peer] = {}
        self._listeners: Dict[str, List[Callable]] = {}

    def register_peer(
        self,
        identity: Identity,
        store: Optional[RecordStore] = None,
    ) -> Peer:
        """Register a peer on the network."""
        if store is None:
            store = RecordStore()
        peer = Peer(identity=identity, store=store)
        self._peers[peer.pubkey] = peer
        self._emit("peer_registered", peer)
        return peer

    def get_peer(self, pubkey: str) -> Optional[Peer]:
        """Look up a peer by public key."""
        return self._peers.get(pubkey)

    @property
    def peers(self) -> List[Peer]:
        """All registered peers."""
        return list(self._peers.values())

    def create_block(
        self,
        initiator: Peer,
        responder: Peer,
        interaction_type: str = "service",
        outcome: str = "completed",
    ) -> InteractionRecord:
        """Create a bilateral block between two peers using the correct protocol flow.

        1. Read seq and prev_hash from each peer's live chain (not store)
        2. Create and sign the bilateral record
        3. Validate and append to both chains
        4. Persist to both stores
        """
        # Read chain state
        seq_a = initiator.chain.next_seq
        seq_b = responder.chain.next_seq
        prev_hash_a = initiator.chain.head_hash
        prev_hash_b = responder.chain.head_hash

        # Create bilateral record
        record = create_record(
            identity_a=initiator.identity,
            identity_b=responder.identity,
            seq_a=seq_a,
            seq_b=seq_b,
            prev_hash_a=prev_hash_a,
            prev_hash_b=prev_hash_b,
            interaction_type=interaction_type,
            outcome=outcome,
        )

        # Wrap as block
        block = Block(record)

        # Validate and append to both chains
        initiator.chain.append(block)
        responder.chain.append(block)

        # Persist to stores
        initiator.store.add_record(record)
        if responder.store is not initiator.store:
            responder.store.add_record(record)

        self._emit("block_created", record, initiator, responder)
        logger.debug(
            "Block created: %s (seq=%d) <-> %s (seq=%d) type=%s",
            initiator.short_id, seq_a,
            responder.short_id, seq_b,
            interaction_type,
        )

        return record

    def exchange_chain(self, requester: Peer, target: Peer) -> PersonalChain:
        """Simulate a chain exchange: requester gets target's chain.

        Returns a PersonalChain built from the target's store records.
        """
        records = target.store.get_records_for(target.pubkey)
        chain = PersonalChain.from_records(target.pubkey, records)
        self._emit("chain_exchanged", requester, target, chain)
        return chain

    def verify_peer_chain(self, pubkey: str) -> bool:
        """Verify a peer's chain integrity.

        Returns True if the chain is valid. Raises ChainError on failure.
        """
        peer = self._peers.get(pubkey)
        if peer is None:
            raise ValueError(f"Unknown peer: {pubkey[:16]}...")
        return peer.chain.validate()

    def build_dag(self) -> DAGView:
        """Build a network-wide DAG view from all peers' stores."""
        all_records: List[InteractionRecord] = []
        seen_hashes: set = set()
        for peer in self._peers.values():
            for r in peer.store.records:
                h = r.record_hash
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    all_records.append(r)
        crawler = ChainCrawler(all_records)
        return crawler.build_dag()

    def detect_tampering(self) -> TamperingReport:
        """Run tampering detection across all peers' records."""
        all_records: List[InteractionRecord] = []
        seen_hashes: set = set()
        for peer in self._peers.values():
            for r in peer.store.records:
                h = r.record_hash
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    all_records.append(r)
        crawler = ChainCrawler(all_records)
        return crawler.detect_tampering()

    # ---- Event system ----

    def on(self, event: str, handler: Callable) -> None:
        """Register an event handler.

        Events: 'peer_registered', 'block_created', 'chain_exchanged'.
        """
        self._listeners.setdefault(event, []).append(handler)

    def _emit(self, event: str, *args: Any) -> None:
        """Emit an event to all registered handlers."""
        for handler in self._listeners.get(event, []):
            try:
                handler(*args)
            except Exception:
                logger.exception("Event handler error for '%s'", event)
