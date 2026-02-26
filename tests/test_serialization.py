"""Tests for protobuf-compatible binary serialization (Phase 2)."""

import json
import time
import pytest

from trustchain.identity import Identity
from trustchain.blockstore import MemoryBlockStore
from trustchain.halfblock import HalfBlock, GENESIS_HASH, BlockType
from trustchain.protocol import TrustChainProtocol
from trustchain.proto.serialization import (
    halfblock_to_proto,
    proto_to_halfblock,
    encode_envelope,
    decode_envelope,
    encode_propose_message,
    decode_propose_message,
    encode_agree_message,
    encode_crawl_request,
    encode_crawl_response,
    decode_crawl_response,
    _encode_varint,
    _decode_varint,
)
from trustchain.transport.base import MessageType, TransportMessage


class TestVarintEncoding:
    def test_single_byte(self):
        for val in [0, 1, 50, 127]:
            encoded = _encode_varint(val)
            decoded, offset = _decode_varint(encoded, 0)
            assert decoded == val
            assert offset == len(encoded)

    def test_multi_byte(self):
        for val in [128, 300, 16384, 2**21, 2**32 - 1]:
            encoded = _encode_varint(val)
            decoded, offset = _decode_varint(encoded, 0)
            assert decoded == val

    def test_zero(self):
        encoded = _encode_varint(0)
        assert encoded == b"\x00"
        decoded, _ = _decode_varint(encoded, 0)
        assert decoded == 0


class TestHalfBlockProtoRoundTrip:
    def _make_block(self) -> HalfBlock:
        identity = Identity()
        store = MemoryBlockStore()
        protocol = TrustChainProtocol(identity, store)
        counterparty = Identity()
        return protocol.create_proposal(
            counterparty.pubkey_hex,
            {"interaction_type": "compute", "outcome": "completed", "data": {"x": 42}},
        )

    def test_roundtrip_proposal(self):
        block = self._make_block()
        data = halfblock_to_proto(block)
        restored = proto_to_halfblock(data)

        assert restored.public_key == block.public_key
        assert restored.sequence_number == block.sequence_number
        assert restored.link_public_key == block.link_public_key
        assert restored.link_sequence_number == block.link_sequence_number
        assert restored.previous_hash == block.previous_hash
        assert restored.signature == block.signature
        assert restored.block_type == block.block_type
        assert restored.transaction == block.transaction
        assert restored.block_hash == block.block_hash
        assert abs(restored.timestamp - block.timestamp) < 0.001

    def test_roundtrip_agreement(self):
        alice = Identity()
        bob = Identity()
        store_a = MemoryBlockStore()
        store_b = MemoryBlockStore()
        proto_a = TrustChainProtocol(alice, store_a)
        proto_b = TrustChainProtocol(bob, store_b)

        proposal = proto_a.create_proposal(
            bob.pubkey_hex,
            {"interaction_type": "test", "outcome": "completed"},
        )
        proto_b.receive_proposal(proposal)
        agreement = proto_b.create_agreement(proposal)

        data = halfblock_to_proto(agreement)
        restored = proto_to_halfblock(data)

        assert restored.public_key == agreement.public_key
        assert restored.sequence_number == agreement.sequence_number
        assert restored.block_type == BlockType.AGREEMENT
        assert restored.link_public_key == alice.pubkey_hex
        assert restored.link_sequence_number == proposal.sequence_number

    def test_empty_transaction(self):
        identity = Identity()
        store = MemoryBlockStore()
        protocol = TrustChainProtocol(identity, store)
        counterparty = Identity()
        block = protocol.create_proposal(counterparty.pubkey_hex, {})

        data = halfblock_to_proto(block)
        restored = proto_to_halfblock(data)
        assert restored.transaction == {}

    def test_unicode_in_transaction(self):
        identity = Identity()
        store = MemoryBlockStore()
        protocol = TrustChainProtocol(identity, store)
        counterparty = Identity()
        tx = {"name": "test_unicode", "data": "Hello \u4e16\u754c \U0001f600"}
        block = protocol.create_proposal(counterparty.pubkey_hex, tx)

        data = halfblock_to_proto(block)
        restored = proto_to_halfblock(data)
        assert restored.transaction["data"] == "Hello \u4e16\u754c \U0001f600"

    def test_large_sequence_numbers(self):
        """Verify encoding handles large uint64 values."""
        identity = Identity()
        store = MemoryBlockStore()
        protocol = TrustChainProtocol(identity, store)
        counterparty = Identity()
        block = protocol.create_proposal(counterparty.pubkey_hex, {"type": "test"})
        # Manually set a large sequence number for encoding test
        block.sequence_number = 2**32 + 1

        data = halfblock_to_proto(block)
        restored = proto_to_halfblock(data)
        assert restored.sequence_number == 2**32 + 1

    def test_size_smaller_than_json(self):
        """Protobuf encoding should be more compact than JSON."""
        block = self._make_block()
        proto_bytes = halfblock_to_proto(block)
        json_bytes = json.dumps(block.to_dict(), sort_keys=True).encode()

        # Proto should be smaller (no field names, no quotes, varint encoding)
        assert len(proto_bytes) < len(json_bytes), (
            f"Proto ({len(proto_bytes)} bytes) should be smaller than "
            f"JSON ({len(json_bytes)} bytes)"
        )


class TestEnvelopeRoundTrip:
    def test_roundtrip(self):
        payload = b"some binary data here"
        sender = "ab" * 32

        encoded = encode_envelope(
            msg_type=MessageType.PROPOSE,
            payload=payload,
            sender_pubkey=sender,
            timestamp=1700000000.0,
        )
        decoded = decode_envelope(encoded)

        assert decoded.msg_type == MessageType.PROPOSE
        assert decoded.payload == payload
        assert decoded.sender_pubkey == sender
        assert abs(decoded.timestamp - 1700000000.0) < 0.001

    def test_all_message_types(self):
        for mt in MessageType:
            encoded = encode_envelope(
                msg_type=mt,
                payload=b"test",
                sender_pubkey="abc",
                timestamp=1.0,
            )
            decoded = decode_envelope(encoded)
            assert decoded.msg_type == mt

    def test_empty_payload(self):
        encoded = encode_envelope(
            msg_type=MessageType.STATUS_REQUEST,
            payload=b"",
            sender_pubkey="test",
        )
        decoded = decode_envelope(encoded)
        assert decoded.payload == b""


class TestProposeMessage:
    def test_roundtrip(self):
        identity = Identity()
        store = MemoryBlockStore()
        protocol = TrustChainProtocol(identity, store)
        counterparty = Identity()
        block = protocol.create_proposal(
            counterparty.pubkey_hex, {"type": "test"}
        )

        encoded = encode_propose_message(block)
        decoded = decode_propose_message(encoded)

        assert decoded.public_key == block.public_key
        assert decoded.sequence_number == block.sequence_number
        assert decoded.block_hash == block.block_hash


class TestCrawlResponse:
    def test_roundtrip_multiple_blocks(self):
        identity = Identity()
        store = MemoryBlockStore()
        protocol = TrustChainProtocol(identity, store)
        counterparty = Identity()

        blocks = []
        for i in range(5):
            block = protocol.create_proposal(
                counterparty.pubkey_hex,
                {"type": "test", "index": i},
            )
            blocks.append(block)

        encoded = encode_crawl_response(blocks)
        decoded = decode_crawl_response(encoded)

        assert len(decoded) == 5
        for i, (original, restored) in enumerate(zip(blocks, decoded)):
            assert restored.public_key == original.public_key
            assert restored.sequence_number == original.sequence_number
            assert restored.block_hash == original.block_hash
            assert restored.transaction["index"] == i

    def test_empty_response(self):
        encoded = encode_crawl_response([])
        decoded = decode_crawl_response(encoded)
        assert decoded == []
