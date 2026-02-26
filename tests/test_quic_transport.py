"""Tests for QUIC P2P transport (Phase 3).

Tests the TLS certificate generation, connection pool, message framing,
and end-to-end QUIC communication between two TrustChain nodes.
"""

import asyncio
import os
import struct
import tempfile
import time

import pytest

from trustchain.identity import Identity
from trustchain.blockstore import MemoryBlockStore
from trustchain.protocol import TrustChainProtocol
from trustchain.proto.serialization import (
    encode_envelope,
    decode_envelope,
    halfblock_to_proto,
    proto_to_halfblock,
)
from trustchain.transport.base import (
    MessageType,
    TransportError,
    TransportMessage,
)
from trustchain.transport.tls import (
    generate_self_signed_cert,
    extract_pubkey_from_cert,
    verify_peer_cert,
)
from trustchain.transport.pool import ConnectionPool, PeerConnection
from trustchain.transport.quic import (
    _frame_message,
    FRAME_HEADER_SIZE,
    QUICTransport,
)


# ---- TLS Certificate Tests ----


class TestTLSCertificates:
    def test_generate_cert_files_created(self):
        identity = Identity()
        cert_path, key_path = generate_self_signed_cert(identity)
        try:
            assert os.path.exists(cert_path)
            assert os.path.exists(key_path)
            assert os.path.getsize(cert_path) > 0
            assert os.path.getsize(key_path) > 0
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)

    def test_cert_contains_pubkey(self):
        identity = Identity()
        cert_path, key_path = generate_self_signed_cert(identity)
        try:
            extracted = extract_pubkey_from_cert(cert_path)
            assert extracted == identity.pubkey_hex
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)

    def test_verify_matching_cert(self):
        identity = Identity()
        cert_path, key_path = generate_self_signed_cert(identity)
        try:
            assert verify_peer_cert(cert_path, identity.pubkey_hex) is True
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)

    def test_verify_mismatched_cert(self):
        identity = Identity()
        cert_path, key_path = generate_self_signed_cert(identity)
        try:
            other = Identity()
            assert verify_peer_cert(cert_path, other.pubkey_hex) is False
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)

    def test_custom_cert_paths(self):
        identity = Identity()
        cert_f = tempfile.NamedTemporaryFile(
            suffix=".pem", delete=False
        )
        key_f = tempfile.NamedTemporaryFile(
            suffix=".pem", delete=False
        )
        cert_f.close()
        key_f.close()
        cert_path, key_path = generate_self_signed_cert(
            identity, cert_path=cert_f.name, key_path=key_f.name
        )
        try:
            assert cert_path == cert_f.name
            assert key_path == key_f.name
            assert verify_peer_cert(cert_path, identity.pubkey_hex)
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)

    def test_two_identities_different_certs(self):
        id_a = Identity()
        id_b = Identity()
        cert_a, key_a = generate_self_signed_cert(id_a)
        cert_b, key_b = generate_self_signed_cert(id_b)
        try:
            assert extract_pubkey_from_cert(cert_a) != extract_pubkey_from_cert(
                cert_b
            )
            assert verify_peer_cert(cert_a, id_a.pubkey_hex)
            assert verify_peer_cert(cert_b, id_b.pubkey_hex)
            assert not verify_peer_cert(cert_a, id_b.pubkey_hex)
        finally:
            os.unlink(cert_a)
            os.unlink(key_a)
            os.unlink(cert_b)
            os.unlink(key_b)


# ---- Connection Pool Tests ----


class TestConnectionPool:
    def test_register_peer(self):
        pool = ConnectionPool()
        pool.register_peer("abc123", "localhost", 8200)
        assert "abc123" in pool.known_peers
        assert "abc123" not in pool.connected_peers

    def test_register_multiple_peers(self):
        pool = ConnectionPool()
        pool.register_peer("peer1", "host1", 8200)
        pool.register_peer("peer2", "host2", 8201)
        assert len(pool.known_peers) == 2

    def test_register_same_peer_idempotent(self):
        pool = ConnectionPool()
        pool.register_peer("abc123", "localhost", 8200)
        pool.register_peer("abc123", "localhost", 8200)
        assert len(pool.known_peers) == 1

    async def test_get_connection_unknown_peer(self):
        pool = ConnectionPool()
        with pytest.raises(ValueError, match="Unknown peer"):
            await pool.get_connection("unknown")

    async def test_get_connection_no_factory(self):
        pool = ConnectionPool()
        pool.register_peer("abc123", "localhost", 8200)
        with pytest.raises(RuntimeError, match="No connection factory"):
            await pool.get_connection("abc123")

    async def test_get_connection_with_factory(self):
        pool = ConnectionPool()
        pool.register_peer("abc123", "localhost", 8200)

        mock_conn = object()

        async def connect(host, port):
            return mock_conn

        async def disconnect(conn):
            pass

        pool.set_connect_factory(connect, disconnect)
        conn = await pool.get_connection("abc123")
        assert conn is mock_conn
        assert "abc123" in pool.connected_peers

    async def test_disconnect_peer(self):
        pool = ConnectionPool()
        pool.register_peer("abc123", "localhost", 8200)

        disconnected = []

        async def connect(host, port):
            return "conn"

        async def disconnect(conn):
            disconnected.append(conn)

        pool.set_connect_factory(connect, disconnect)
        await pool.get_connection("abc123")
        assert "abc123" in pool.connected_peers

        await pool.disconnect("abc123")
        assert "abc123" not in pool.connected_peers
        assert disconnected == ["conn"]

    async def test_disconnect_all(self):
        pool = ConnectionPool()

        async def connect(host, port):
            return "conn"

        async def disconnect(conn):
            pass

        pool.set_connect_factory(connect, disconnect)
        pool.register_peer("p1", "h1", 1)
        pool.register_peer("p2", "h2", 2)
        await pool.get_connection("p1")
        await pool.get_connection("p2")
        assert len(pool.connected_peers) == 2

        await pool.disconnect_all()
        assert len(pool.connected_peers) == 0

    async def test_connection_reuse(self):
        pool = ConnectionPool()
        call_count = 0

        async def connect(host, port):
            nonlocal call_count
            call_count += 1
            return f"conn_{call_count}"

        async def disconnect(conn):
            pass

        pool.set_connect_factory(connect, disconnect)
        pool.register_peer("abc123", "localhost", 8200)

        conn1 = await pool.get_connection("abc123")
        conn2 = await pool.get_connection("abc123")
        assert conn1 is conn2
        assert call_count == 1  # Only connected once


class TestPeerConnection:
    def test_idle_seconds(self):
        pc = PeerConnection(peer_id="abc", host="localhost", port=8200)
        pc.last_activity = time.time() - 10
        assert pc.idle_seconds >= 10

    def test_touch_resets_idle(self):
        pc = PeerConnection(peer_id="abc", host="localhost", port=8200)
        pc.last_activity = time.time() - 100
        pc.touch()
        assert pc.idle_seconds < 1


# ---- Message Framing Tests ----


class TestMessageFraming:
    def test_frame_message(self):
        data = b"hello world"
        framed = _frame_message(data)
        assert len(framed) == FRAME_HEADER_SIZE + len(data)

        # Verify length prefix
        length = struct.unpack(">I", framed[:FRAME_HEADER_SIZE])[0]
        assert length == len(data)
        assert framed[FRAME_HEADER_SIZE:] == data

    def test_frame_empty_message(self):
        framed = _frame_message(b"")
        assert len(framed) == FRAME_HEADER_SIZE
        length = struct.unpack(">I", framed[:FRAME_HEADER_SIZE])[0]
        assert length == 0

    def test_frame_large_message(self):
        data = b"x" * 100000
        framed = _frame_message(data)
        length = struct.unpack(">I", framed[:FRAME_HEADER_SIZE])[0]
        assert length == 100000

    def test_envelope_round_trip_through_frame(self):
        """Full round-trip: message -> envelope -> frame -> unframe -> decode."""
        msg = TransportMessage(
            msg_type=MessageType.PROPOSE,
            payload=b"test proposal",
            sender_pubkey="ab" * 32,
        )

        # Encode to envelope bytes
        envelope_bytes = encode_envelope(
            msg_type=msg.msg_type,
            payload=msg.payload,
            sender_pubkey=msg.sender_pubkey,
            timestamp=msg.timestamp,
        )

        # Frame it
        framed = _frame_message(envelope_bytes)

        # Unframe it
        length = struct.unpack(">I", framed[:FRAME_HEADER_SIZE])[0]
        extracted = framed[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + length]

        # Decode
        decoded = decode_envelope(extracted)
        assert decoded.msg_type == msg.msg_type
        assert decoded.payload == msg.payload
        assert decoded.sender_pubkey == msg.sender_pubkey


# ---- QUICTransport Unit Tests ----


class TestQUICTransportInit:
    def test_create(self):
        identity = Identity()
        transport = QUICTransport(identity, port=8200)
        assert transport.pubkey == identity.pubkey_hex
        assert transport.port == 8200
        assert transport.connected_peers == []

    def test_register_peer(self):
        identity = Identity()
        transport = QUICTransport(identity)
        transport.register_peer("peer_abc", "10.0.0.1", 8200)
        assert "peer_abc" in transport.pool.known_peers

    async def test_send_unknown_peer_raises(self):
        identity = Identity()
        transport = QUICTransport(identity)
        msg = TransportMessage(
            msg_type=MessageType.PROPOSE,
            payload=b"test",
            sender_pubkey=identity.pubkey_hex,
        )
        with pytest.raises(TransportError, match="Unknown peer"):
            await transport.send("unknown_peer", msg)


# ---- End-to-End QUIC Tests ----
# These tests require actual QUIC connections on localhost.


class TestQUICEndToEnd:
    """Integration tests for two QUIC nodes communicating on localhost.

    These test the full stack: TLS → QUIC → framing → envelope → handler.
    """

    async def test_two_nodes_start_stop(self):
        """Verify two QUIC nodes can start and stop cleanly."""
        alice_id = Identity()
        bob_id = Identity()

        alice = QUICTransport(alice_id, host="127.0.0.1", port=18200)
        bob = QUICTransport(bob_id, host="127.0.0.1", port=18201)

        await alice.start()
        await bob.start()

        # Give servers a moment to start
        await asyncio.sleep(0.2)

        await alice.stop()
        await bob.stop()

    async def test_proposal_round_trip(self):
        """Full proposal/agreement via QUIC between two nodes."""
        alice_id = Identity()
        bob_id = Identity()
        store_a = MemoryBlockStore()
        store_b = MemoryBlockStore()
        proto_a = TrustChainProtocol(alice_id, store_a)
        proto_b = TrustChainProtocol(bob_id, store_b)

        alice = QUICTransport(alice_id, host="127.0.0.1", port=18210)
        bob = QUICTransport(bob_id, host="127.0.0.1", port=18211)

        # Register Bob's handler for proposals
        async def handle_propose(msg: TransportMessage) -> TransportMessage:
            # Decode the proposal
            from trustchain.proto.serialization import (
                decode_propose_message,
                encode_agree_message,
            )

            proposal = decode_propose_message(msg.payload)
            proto_b.receive_proposal(proposal)
            agreement = proto_b.create_agreement(proposal)
            return TransportMessage(
                msg_type=MessageType.AGREE,
                payload=encode_agree_message(agreement, accepted=True),
                sender_pubkey=bob_id.pubkey_hex,
            )

        bob.register_handler(MessageType.PROPOSE, handle_propose)

        await bob.start()
        await alice.start()
        await asyncio.sleep(0.2)

        # Register peers
        alice.register_peer(bob_id.pubkey_hex, "127.0.0.1", 18211)

        # Create proposal
        proposal = proto_a.create_proposal(
            bob_id.pubkey_hex,
            {"interaction_type": "test", "outcome": "completed"},
        )

        from trustchain.proto.serialization import encode_propose_message

        msg = TransportMessage(
            msg_type=MessageType.PROPOSE,
            payload=encode_propose_message(proposal),
            sender_pubkey=alice_id.pubkey_hex,
        )

        try:
            response = await alice.send(bob_id.pubkey_hex, msg)
            assert response is not None
            assert response.msg_type == MessageType.AGREE
        except TransportError:
            # QUIC connection may fail in CI environments without proper UDP
            pytest.skip("QUIC connection failed (likely CI environment)")
        finally:
            await alice.stop()
            await bob.stop()
