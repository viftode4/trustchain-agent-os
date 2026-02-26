"""Tests for the transport abstraction layer (Phase 1)."""

import json
import pytest

from trustchain.identity import Identity
from trustchain.blockstore import MemoryBlockStore
from trustchain.protocol import TrustChainProtocol
from trustchain.transport.base import (
    MessageType,
    Transport,
    TransportError,
    TransportMessage,
)
from trustchain.transport.http import (
    HTTPTransport,
    halfblock_to_bytes,
    bytes_to_halfblock,
)


class TestMessageType:
    def test_all_types_defined(self):
        assert MessageType.PROPOSE == 1
        assert MessageType.AGREE == 2
        assert MessageType.CRAWL_REQUEST == 3
        assert MessageType.CRAWL_RESPONSE == 4
        assert MessageType.CHECKPOINT == 5
        assert MessageType.PEER_EXCHANGE == 6
        assert MessageType.STATUS_REQUEST == 7
        assert MessageType.STATUS_RESPONSE == 8

    def test_types_are_ints(self):
        for mt in MessageType:
            assert isinstance(mt.value, int)


class TestTransportMessage:
    def test_create_message(self):
        msg = TransportMessage(
            msg_type=MessageType.PROPOSE,
            payload=b"test data",
            sender_pubkey="abc123",
        )
        assert msg.msg_type == MessageType.PROPOSE
        assert msg.payload == b"test data"
        assert msg.sender_pubkey == "abc123"
        assert msg.timestamp > 0

    def test_default_timestamp(self):
        msg = TransportMessage(
            msg_type=MessageType.STATUS_REQUEST,
            payload=b"",
            sender_pubkey="def456",
        )
        assert msg.timestamp > 0


class TestTransportError:
    def test_error_with_peer_id(self):
        err = TransportError("connection failed", peer_id="abc123")
        assert "connection failed" in str(err)
        assert err.peer_id == "abc123"

    def test_error_without_peer_id(self):
        err = TransportError("general failure")
        assert err.peer_id is None


class TestTransportABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            Transport()

    def test_register_handler(self):
        """Verify handler registration works on the base class dict."""

        class DummyTransport(Transport):
            async def send(self, peer_id, message):
                pass

            async def broadcast(self, message):
                pass

            async def start(self):
                pass

            async def stop(self):
                pass

            @property
            def connected_peers(self):
                return []

        t = DummyTransport()

        async def my_handler(msg):
            return None

        t.register_handler(MessageType.PROPOSE, my_handler)
        assert t.get_handler(MessageType.PROPOSE) is my_handler
        assert t.get_handler(MessageType.AGREE) is None


class TestHTTPTransport:
    def test_create(self):
        identity = Identity()
        transport = HTTPTransport(identity)
        assert transport.pubkey == identity.pubkey_hex
        assert transport.connected_peers == []

    def test_register_peer(self):
        identity = Identity()
        transport = HTTPTransport(identity)
        transport.register_peer("abc123", "http://localhost:8100")
        assert "abc123" in transport.connected_peers

    async def test_send_unknown_peer_raises(self):
        identity = Identity()
        transport = HTTPTransport(identity)
        msg = TransportMessage(
            msg_type=MessageType.PROPOSE,
            payload=b"{}",
            sender_pubkey=identity.pubkey_hex,
        )
        with pytest.raises(TransportError, match="Unknown peer"):
            await transport.send("unknown_peer", msg)

    async def test_stop_closes_client(self):
        identity = Identity()
        transport = HTTPTransport(identity)
        await transport.stop()
        assert transport._client is None


class TestHalfBlockSerialization:
    def test_roundtrip(self):
        identity = Identity()
        store = MemoryBlockStore()
        protocol = TrustChainProtocol(identity, store)

        counterparty = Identity()
        block = protocol.create_proposal(
            counterparty.pubkey_hex,
            {"interaction_type": "test", "outcome": "completed"},
        )

        data = halfblock_to_bytes(block)
        restored = bytes_to_halfblock(data)

        assert restored.public_key == block.public_key
        assert restored.sequence_number == block.sequence_number
        assert restored.link_public_key == block.link_public_key
        assert restored.link_sequence_number == block.link_sequence_number
        assert restored.previous_hash == block.previous_hash
        assert restored.signature == block.signature
        assert restored.block_type == block.block_type
        assert restored.transaction == block.transaction
        assert restored.block_hash == block.block_hash
        assert restored.timestamp == block.timestamp
