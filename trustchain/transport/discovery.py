"""Peer discovery for TrustChain P2P network.

Combines three discovery mechanisms:
1. Bootstrap nodes — hardcoded seed addresses for initial connection
2. Random walk — periodically ask a random known peer for its peer list
3. Gossip — periodically share our peer list with random peers

Peer selection is weighted by TrustEngine scores: trustworthy peers are
preferred connections. Inspired by IPv8's random walk but trust-aware.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from trustchain.transport.base import (
    MessageType,
    Transport,
    TransportError,
    TransportMessage,
)

logger = logging.getLogger("trustchain.transport.discovery")


@dataclass
class PeerInfo:
    """Information about a discovered peer."""

    pubkey: str
    host: str
    port: int
    last_seen: float = field(default_factory=time.time)
    trust_score: float = 0.0
    connected: bool = False
    services: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pubkey": self.pubkey,
            "host": self.host,
            "port": self.port,
            "last_seen": self.last_seen,
            "trust_score": self.trust_score,
            "services": self.services,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PeerInfo:
        return cls(
            pubkey=data["pubkey"],
            host=data["host"],
            port=data["port"],
            last_seen=data.get("last_seen", time.time()),
            trust_score=data.get("trust_score", 0.0),
            services=data.get("services", []),
        )


class PeerDiscovery:
    """Trust-aware peer discovery for TrustChain networks.

    Uses bootstrap nodes for initial seeding, then random walk + gossip
    for continuous discovery. Peers are scored by trust and preferred
    for connections accordingly.
    """

    def __init__(
        self,
        my_pubkey: str,
        my_host: str = "0.0.0.0",
        my_port: int = 8200,
        bootstrap_nodes: Optional[List[Tuple[str, int]]] = None,
        trust_fn: Optional[Callable[[str], float]] = None,
        walk_interval: float = 30.0,
        gossip_interval: float = 60.0,
        cleanup_interval: float = 300.0,
        stale_timeout: float = 600.0,
        max_peers: int = 100,
    ) -> None:
        self.my_pubkey = my_pubkey
        self.my_host = my_host
        self.my_port = my_port
        self.bootstrap_nodes = bootstrap_nodes or []
        self._trust_fn = trust_fn
        self.walk_interval = walk_interval
        self.gossip_interval = gossip_interval
        self.cleanup_interval = cleanup_interval
        self.stale_timeout = stale_timeout
        self.max_peers = max_peers

        self._peers: Dict[str, PeerInfo] = {}
        self._transport: Optional[Transport] = None
        self._tasks: List[asyncio.Task] = []
        self._running = False

    @property
    def known_peers(self) -> Dict[str, PeerInfo]:
        """All known peers."""
        return dict(self._peers)

    @property
    def peer_count(self) -> int:
        return len(self._peers)

    def add_peer(
        self,
        pubkey: str,
        host: str,
        port: int,
        trust_score: float = 0.0,
    ) -> PeerInfo:
        """Add or update a known peer."""
        if pubkey == self.my_pubkey:
            return PeerInfo(pubkey=pubkey, host=host, port=port)

        if pubkey in self._peers:
            peer = self._peers[pubkey]
            peer.host = host
            peer.port = port
            peer.last_seen = time.time()
            if trust_score > 0:
                peer.trust_score = trust_score
            return peer

        if len(self._peers) >= self.max_peers:
            self._evict_least_trusted()

        peer = PeerInfo(
            pubkey=pubkey,
            host=host,
            port=port,
            trust_score=trust_score,
        )
        self._peers[pubkey] = peer
        logger.debug(
            "Discovered peer %s at %s:%d",
            pubkey[:16],
            host,
            port,
        )
        return peer

    def remove_peer(self, pubkey: str) -> None:
        """Remove a peer from the known list."""
        self._peers.pop(pubkey, None)

    def peer_score(self, pubkey: str) -> float:
        """Get trust-weighted score for a peer.

        If a trust function is configured, uses live TrustEngine scores.
        Otherwise falls back to cached trust_score in PeerInfo.
        """
        if self._trust_fn is not None:
            try:
                return self._trust_fn(pubkey)
            except Exception:
                pass

        peer = self._peers.get(pubkey)
        if peer:
            return peer.trust_score
        return 0.0

    def select_peers(self, n: int) -> List[str]:
        """Select n peers weighted by trust score.

        Higher-trust peers are more likely to be selected. This provides
        Sybil resistance in peer selection — fake identities with no
        real interactions have low trust and are rarely selected.
        """
        if not self._peers:
            return []

        peers = list(self._peers.keys())
        if len(peers) <= n:
            return peers

        weights = []
        for pubkey in peers:
            score = self.peer_score(pubkey)
            # Minimum weight of 0.1 so even zero-trust peers have small chance
            weights.append(max(score, 0.1))

        selected = set()
        attempts = 0
        while len(selected) < n and attempts < n * 10:
            # Weighted random selection
            total = sum(weights)
            r = random.random() * total
            cumulative = 0.0
            for i, w in enumerate(weights):
                cumulative += w
                if r <= cumulative:
                    selected.add(peers[i])
                    break
            attempts += 1

        return list(selected)

    def _evict_least_trusted(self) -> None:
        """Remove the least-trusted peer to make room for a new one."""
        if not self._peers:
            return
        worst = min(self._peers.values(), key=lambda p: p.trust_score)
        self._peers.pop(worst.pubkey, None)
        logger.debug("Evicted least-trusted peer %s", worst.pubkey[:16])

    # ---- Discovery Protocols ----

    async def bootstrap(self, transport: Transport) -> None:
        """Connect to bootstrap nodes and request their peer lists.

        This is the entry point for joining the network.
        """
        self._transport = transport

        for host, port in self.bootstrap_nodes:
            try:
                # Send a peer exchange request to the bootstrap node
                my_info = PeerInfo(
                    pubkey=self.my_pubkey,
                    host=self.my_host,
                    port=self.my_port,
                )
                payload = json.dumps({
                    "type": "bootstrap",
                    "sender": my_info.to_dict(),
                }).encode()

                msg = TransportMessage(
                    msg_type=MessageType.PEER_EXCHANGE,
                    payload=payload,
                    sender_pubkey=self.my_pubkey,
                )

                # We need the bootstrap node's pubkey to send via transport
                # For bootstrap, we use the host:port as a temporary identifier
                bootstrap_id = f"bootstrap:{host}:{port}"
                response = await transport.send(bootstrap_id, msg)

                if response is not None:
                    self._process_peer_exchange(response)

            except (TransportError, Exception) as e:
                logger.debug(
                    "Bootstrap to %s:%d failed: %s", host, port, e
                )

    async def random_walk(self) -> None:
        """Periodically pick a random known peer and request its peer list.

        Implements the random walk discovery protocol from IPv8.
        """
        while self._running:
            try:
                await asyncio.sleep(self.walk_interval)
                if not self._peers or self._transport is None:
                    continue

                # Select a random peer (trust-weighted)
                targets = self.select_peers(1)
                if not targets:
                    continue

                target = targets[0]
                peer = self._peers[target]

                payload = json.dumps({
                    "type": "walk",
                    "sender": {
                        "pubkey": self.my_pubkey,
                        "host": self.my_host,
                        "port": self.my_port,
                    },
                }).encode()

                msg = TransportMessage(
                    msg_type=MessageType.PEER_EXCHANGE,
                    payload=payload,
                    sender_pubkey=self.my_pubkey,
                )

                try:
                    response = await self._transport.send(target, msg)
                    if response is not None:
                        self._process_peer_exchange(response)
                        peer.last_seen = time.time()
                except TransportError:
                    logger.debug("Walk to %s failed", target[:16])

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in random walk")

    async def gossip(self) -> None:
        """Periodically share our peer list with random peers.

        Complements random walk — ensures peer information propagates
        bidirectionally through the network.
        """
        while self._running:
            try:
                await asyncio.sleep(self.gossip_interval)
                if not self._peers or self._transport is None:
                    continue

                # Select a few peers to gossip with
                targets = self.select_peers(min(3, len(self._peers)))

                # Build our peer list to share
                peer_list = [
                    p.to_dict()
                    for p in self._peers.values()
                    if time.time() - p.last_seen < self.stale_timeout
                ]

                payload = json.dumps({
                    "type": "gossip",
                    "peers": peer_list[:20],  # Limit gossip size
                }).encode()

                msg = TransportMessage(
                    msg_type=MessageType.PEER_EXCHANGE,
                    payload=payload,
                    sender_pubkey=self.my_pubkey,
                )

                for target in targets:
                    try:
                        await self._transport.send(target, msg)
                    except TransportError:
                        pass

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in gossip")

    async def cleanup_stale(self) -> None:
        """Periodically remove peers that haven't been seen recently."""
        while self._running:
            try:
                await asyncio.sleep(self.cleanup_interval)
                now = time.time()
                stale = [
                    pubkey
                    for pubkey, peer in self._peers.items()
                    if (now - peer.last_seen) > self.stale_timeout
                ]
                for pubkey in stale:
                    self.remove_peer(pubkey)
                    logger.debug("Removed stale peer %s", pubkey[:16])

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in stale cleanup")

    def _process_peer_exchange(self, message: TransportMessage) -> None:
        """Process a peer exchange response, adding new peers."""
        try:
            data = json.loads(message.payload)
            peers = data.get("peers", [])
            for peer_data in peers:
                if isinstance(peer_data, dict) and "pubkey" in peer_data:
                    self.add_peer(
                        pubkey=peer_data["pubkey"],
                        host=peer_data.get("host", ""),
                        port=peer_data.get("port", 0),
                        trust_score=peer_data.get("trust_score", 0.0),
                    )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("Failed to parse peer exchange: %s", e)

    def handle_peer_exchange(
        self, message: TransportMessage
    ) -> Optional[TransportMessage]:
        """Handle incoming peer exchange messages.

        Returns a response with our peer list (for walk/bootstrap requests).
        """
        try:
            data = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            return None

        msg_type = data.get("type", "")

        # Add the sender to our peer list
        sender_info = data.get("sender", {})
        if sender_info and "pubkey" in sender_info:
            self.add_peer(
                pubkey=sender_info["pubkey"],
                host=sender_info.get("host", ""),
                port=sender_info.get("port", 0),
            )

        # Process incoming peers (from gossip)
        if "peers" in data:
            for peer_data in data["peers"]:
                if isinstance(peer_data, dict) and "pubkey" in peer_data:
                    self.add_peer(
                        pubkey=peer_data["pubkey"],
                        host=peer_data.get("host", ""),
                        port=peer_data.get("port", 0),
                    )

        # For walk/bootstrap, respond with our peer list
        if msg_type in ("walk", "bootstrap"):
            peer_list = [
                p.to_dict()
                for p in self._peers.values()
                if time.time() - p.last_seen < self.stale_timeout
            ]
            response_payload = json.dumps({
                "type": "peer_response",
                "peers": peer_list[:20],
            }).encode()
            return TransportMessage(
                msg_type=MessageType.PEER_EXCHANGE,
                payload=response_payload,
                sender_pubkey=self.my_pubkey,
            )

        return None

    # ---- Lifecycle ----

    async def start(self, transport: Transport) -> None:
        """Start discovery background tasks."""
        self._transport = transport
        self._running = True

        # Register handler for peer exchange messages
        transport.register_handler(
            MessageType.PEER_EXCHANGE,
            self._async_handle_peer_exchange,
        )

        # Start background tasks
        self._tasks = [
            asyncio.create_task(self.random_walk()),
            asyncio.create_task(self.gossip()),
            asyncio.create_task(self.cleanup_stale()),
        ]

        logger.info(
            "Peer discovery started (bootstrap=%d, known=%d)",
            len(self.bootstrap_nodes),
            len(self._peers),
        )

    async def _async_handle_peer_exchange(
        self, message: TransportMessage
    ) -> Optional[TransportMessage]:
        """Async wrapper for handle_peer_exchange."""
        return self.handle_peer_exchange(message)

    async def stop(self) -> None:
        """Stop all discovery tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("Peer discovery stopped")
