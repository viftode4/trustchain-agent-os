"""Tests for peer discovery (Phase 4)."""

import asyncio
import json
import time

import pytest

from trustchain.identity import Identity
from trustchain.transport.base import (
    MessageType,
    Transport,
    TransportError,
    TransportMessage,
)
from trustchain.transport.discovery import PeerDiscovery, PeerInfo


# ---- Mock Transport for Testing ----


class MockTransport(Transport):
    """A mock transport that records sent messages and returns canned responses."""

    def __init__(self) -> None:
        super().__init__()
        self.sent_messages: list = []
        self.responses: dict = {}  # peer_id -> TransportMessage

    @property
    def connected_peers(self) -> list:
        return list(self.responses.keys())

    def set_response(self, peer_id: str, response: TransportMessage) -> None:
        self.responses[peer_id] = response

    async def send(self, peer_id, message):
        self.sent_messages.append((peer_id, message))
        return self.responses.get(peer_id)

    async def broadcast(self, message):
        for peer_id in self.responses:
            self.sent_messages.append((peer_id, message))

    async def start(self):
        pass

    async def stop(self):
        pass


# ---- PeerInfo Tests ----


class TestPeerInfo:
    def test_create(self):
        peer = PeerInfo(pubkey="abc", host="localhost", port=8200)
        assert peer.pubkey == "abc"
        assert peer.host == "localhost"
        assert peer.port == 8200
        assert peer.last_seen > 0
        assert peer.trust_score == 0.0

    def test_to_dict_roundtrip(self):
        peer = PeerInfo(
            pubkey="abc123",
            host="10.0.0.1",
            port=8200,
            trust_score=0.75,
            services=["compute", "data"],
        )
        data = peer.to_dict()
        restored = PeerInfo.from_dict(data)
        assert restored.pubkey == peer.pubkey
        assert restored.host == peer.host
        assert restored.port == peer.port
        assert restored.trust_score == peer.trust_score
        assert restored.services == peer.services


# ---- PeerDiscovery Core Tests ----


class TestPeerDiscoveryCore:
    def test_add_peer(self):
        disco = PeerDiscovery(my_pubkey="me")
        disco.add_peer("peer1", "localhost", 8200)
        assert disco.peer_count == 1
        assert "peer1" in disco.known_peers

    def test_add_self_ignored(self):
        disco = PeerDiscovery(my_pubkey="me")
        disco.add_peer("me", "localhost", 8200)
        assert disco.peer_count == 0

    def test_add_peer_updates_existing(self):
        disco = PeerDiscovery(my_pubkey="me")
        disco.add_peer("peer1", "host1", 8200)
        disco.add_peer("peer1", "host2", 8201)
        assert disco.peer_count == 1
        assert disco.known_peers["peer1"].host == "host2"
        assert disco.known_peers["peer1"].port == 8201

    def test_remove_peer(self):
        disco = PeerDiscovery(my_pubkey="me")
        disco.add_peer("peer1", "localhost", 8200)
        disco.remove_peer("peer1")
        assert disco.peer_count == 0

    def test_remove_nonexistent_peer(self):
        disco = PeerDiscovery(my_pubkey="me")
        disco.remove_peer("nonexistent")  # Should not raise
        assert disco.peer_count == 0

    def test_max_peers_eviction(self):
        disco = PeerDiscovery(my_pubkey="me", max_peers=3)
        disco.add_peer("p1", "h1", 1, trust_score=0.5)
        disco.add_peer("p2", "h2", 2, trust_score=0.1)
        disco.add_peer("p3", "h3", 3, trust_score=0.9)

        # Adding 4th should evict least trusted (p2 with 0.1)
        disco.add_peer("p4", "h4", 4, trust_score=0.7)
        assert disco.peer_count == 3
        assert "p2" not in disco.known_peers
        assert "p4" in disco.known_peers


# ---- Trust-Weighted Selection Tests ----


class TestTrustWeightedSelection:
    def test_select_all_when_fewer(self):
        disco = PeerDiscovery(my_pubkey="me")
        disco.add_peer("p1", "h1", 1)
        disco.add_peer("p2", "h2", 2)
        selected = disco.select_peers(5)
        assert set(selected) == {"p1", "p2"}

    def test_select_subset(self):
        disco = PeerDiscovery(my_pubkey="me")
        for i in range(10):
            disco.add_peer(f"p{i}", f"h{i}", 8200 + i, trust_score=0.5)
        selected = disco.select_peers(3)
        assert len(selected) == 3

    def test_empty_peers(self):
        disco = PeerDiscovery(my_pubkey="me")
        assert disco.select_peers(3) == []

    def test_trust_weighted_prefers_high_trust(self):
        """High-trust peers should be selected more often statistically."""
        disco = PeerDiscovery(my_pubkey="me")
        disco.add_peer("high_trust", "h1", 1, trust_score=0.99)
        disco.add_peer("low_trust", "h2", 2, trust_score=0.01)

        counts = {"high_trust": 0, "low_trust": 0}
        for _ in range(200):
            selected = disco.select_peers(1)
            if selected:
                counts[selected[0]] += 1

        # high_trust should be selected significantly more often
        assert counts["high_trust"] > counts["low_trust"]

    def test_peer_score_with_trust_fn(self):
        scores = {"p1": 0.9, "p2": 0.1}
        disco = PeerDiscovery(
            my_pubkey="me",
            trust_fn=lambda pk: scores.get(pk, 0.0),
        )
        disco.add_peer("p1", "h1", 1)
        disco.add_peer("p2", "h2", 2)
        assert disco.peer_score("p1") == 0.9
        assert disco.peer_score("p2") == 0.1

    def test_peer_score_fallback_to_cached(self):
        disco = PeerDiscovery(my_pubkey="me")
        disco.add_peer("p1", "h1", 1, trust_score=0.5)
        assert disco.peer_score("p1") == 0.5


# ---- Peer Exchange Handler Tests ----


class TestPeerExchangeHandler:
    def test_handle_walk_request(self):
        disco = PeerDiscovery(my_pubkey="me")
        disco.add_peer("existing", "h1", 8200)

        msg = TransportMessage(
            msg_type=MessageType.PEER_EXCHANGE,
            payload=json.dumps({
                "type": "walk",
                "sender": {"pubkey": "walker", "host": "h2", "port": 8201},
            }).encode(),
            sender_pubkey="walker",
        )

        response = disco.handle_peer_exchange(msg)
        assert response is not None
        assert response.msg_type == MessageType.PEER_EXCHANGE

        # Walker should be added
        assert "walker" in disco.known_peers

        # Response should contain peers
        data = json.loads(response.payload)
        assert "peers" in data
        assert len(data["peers"]) > 0

    def test_handle_gossip_adds_peers(self):
        disco = PeerDiscovery(my_pubkey="me")

        msg = TransportMessage(
            msg_type=MessageType.PEER_EXCHANGE,
            payload=json.dumps({
                "type": "gossip",
                "peers": [
                    {"pubkey": "p1", "host": "h1", "port": 1},
                    {"pubkey": "p2", "host": "h2", "port": 2},
                ],
            }).encode(),
            sender_pubkey="gossiper",
        )

        response = disco.handle_peer_exchange(msg)
        # Gossip doesn't need a response
        assert response is None
        # But peers should be added
        assert "p1" in disco.known_peers
        assert "p2" in disco.known_peers

    def test_handle_bootstrap_returns_peers(self):
        disco = PeerDiscovery(my_pubkey="me")
        disco.add_peer("p1", "h1", 1)
        disco.add_peer("p2", "h2", 2)

        msg = TransportMessage(
            msg_type=MessageType.PEER_EXCHANGE,
            payload=json.dumps({
                "type": "bootstrap",
                "sender": {"pubkey": "new_node", "host": "h3", "port": 3},
            }).encode(),
            sender_pubkey="new_node",
        )

        response = disco.handle_peer_exchange(msg)
        assert response is not None
        data = json.loads(response.payload)
        peer_keys = {p["pubkey"] for p in data["peers"]}
        assert "p1" in peer_keys or "p2" in peer_keys
        assert "new_node" in disco.known_peers

    def test_handle_malformed_message(self):
        disco = PeerDiscovery(my_pubkey="me")
        msg = TransportMessage(
            msg_type=MessageType.PEER_EXCHANGE,
            payload=b"not json",
            sender_pubkey="bad",
        )
        response = disco.handle_peer_exchange(msg)
        assert response is None


# ---- Background Task Tests ----


class TestDiscoveryLifecycle:
    async def test_start_stop(self):
        disco = PeerDiscovery(my_pubkey="me")
        transport = MockTransport()
        await disco.start(transport)
        assert disco._running
        assert len(disco._tasks) == 3  # walk, gossip, cleanup

        await disco.stop()
        assert not disco._running
        assert len(disco._tasks) == 0

    async def test_cleanup_removes_stale_peers(self):
        disco = PeerDiscovery(
            my_pubkey="me",
            stale_timeout=0.1,  # Very short for testing
            cleanup_interval=0.1,
        )
        disco.add_peer("old_peer", "h1", 1)
        # Backdate the last_seen
        disco._peers["old_peer"].last_seen = time.time() - 10

        transport = MockTransport()
        await disco.start(transport)

        # Wait for cleanup to run
        await asyncio.sleep(0.3)

        assert "old_peer" not in disco.known_peers

        await disco.stop()

    async def test_random_walk_sends_messages(self):
        disco = PeerDiscovery(
            my_pubkey="me",
            walk_interval=0.1,
        )
        disco.add_peer("target", "h1", 1, trust_score=1.0)

        transport = MockTransport()
        # Set up a canned response
        response = TransportMessage(
            msg_type=MessageType.PEER_EXCHANGE,
            payload=json.dumps({
                "type": "peer_response",
                "peers": [{"pubkey": "discovered", "host": "h2", "port": 2}],
            }).encode(),
            sender_pubkey="target",
        )
        transport.set_response("target", response)

        await disco.start(transport)
        await asyncio.sleep(0.3)  # Let walk run at least once
        await disco.stop()

        # Should have sent at least one walk message
        assert len(transport.sent_messages) > 0
        # Should have discovered the new peer from the response
        assert "discovered" in disco.known_peers

    async def test_gossip_propagates_to_peers(self):
        disco = PeerDiscovery(
            my_pubkey="me",
            gossip_interval=0.1,
        )
        disco.add_peer("p1", "h1", 1, trust_score=1.0)

        transport = MockTransport()
        transport.set_response("p1", None)

        await disco.start(transport)
        await asyncio.sleep(0.3)
        await disco.stop()

        # Should have sent gossip messages
        gossip_msgs = [
            (pid, m)
            for pid, m in transport.sent_messages
            if m.msg_type == MessageType.PEER_EXCHANGE
        ]
        assert len(gossip_msgs) > 0
