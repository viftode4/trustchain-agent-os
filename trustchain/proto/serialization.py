"""Protobuf-compatible binary serialization for TrustChain messages.

Uses struct-based encoding for maximum simplicity — no protoc dependency.
The .proto file serves as documentation; this module implements the same
wire format using Python's struct module.

Wire format for each field follows protobuf encoding rules:
- Varint for integers (field tag + value)
- Length-delimited for strings and bytes
- Fixed64 for doubles
"""

from __future__ import annotations

import json
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

from trustchain.halfblock import HalfBlock
from trustchain.transport.base import MessageType, TransportMessage


# ---- Protobuf wire type constants ----
WIRETYPE_VARINT = 0
WIRETYPE_FIXED64 = 1
WIRETYPE_LENGTH_DELIMITED = 2


def _encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf varint."""
    parts = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts)


def _decode_varint(data: bytes, offset: int) -> Tuple[int, int]:
    """Decode a varint from bytes at offset. Returns (value, new_offset)."""
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("Truncated varint")
        byte = data[offset]
        result |= (byte & 0x7F) << shift
        offset += 1
        if not (byte & 0x80):
            break
        shift += 7
    return result, offset


def _encode_tag(field_number: int, wire_type: int) -> bytes:
    """Encode a protobuf field tag."""
    return _encode_varint((field_number << 3) | wire_type)


def _encode_string_field(field_number: int, value: str) -> bytes:
    """Encode a string field (length-delimited)."""
    encoded = value.encode("utf-8")
    return (
        _encode_tag(field_number, WIRETYPE_LENGTH_DELIMITED)
        + _encode_varint(len(encoded))
        + encoded
    )


def _encode_bytes_field(field_number: int, value: bytes) -> bytes:
    """Encode a bytes field (length-delimited)."""
    return (
        _encode_tag(field_number, WIRETYPE_LENGTH_DELIMITED)
        + _encode_varint(len(value))
        + value
    )


def _encode_uint64_field(field_number: int, value: int) -> bytes:
    """Encode a uint64 field (varint)."""
    if value == 0:
        return b""  # protobuf default, omit
    return _encode_tag(field_number, WIRETYPE_VARINT) + _encode_varint(value)


def _encode_uint32_field(field_number: int, value: int) -> bytes:
    """Encode a uint32 field (varint)."""
    if value == 0:
        return b""
    return _encode_tag(field_number, WIRETYPE_VARINT) + _encode_varint(value)


def _encode_double_field(field_number: int, value: float) -> bytes:
    """Encode a double field (fixed64)."""
    if value == 0.0:
        return b""
    return (
        _encode_tag(field_number, WIRETYPE_FIXED64)
        + struct.pack("<d", value)
    )


def _encode_bool_field(field_number: int, value: bool) -> bytes:
    """Encode a bool field (varint 0 or 1)."""
    if not value:
        return b""
    return _encode_tag(field_number, WIRETYPE_VARINT) + _encode_varint(1)


# ---- HalfBlock serialization ----


def halfblock_to_proto(block: HalfBlock) -> bytes:
    """Serialize a HalfBlock to protobuf-compatible binary format.

    Field mapping matches HalfBlockProto in trustchain.proto:
      1: public_key (string)
      2: sequence_number (uint64)
      3: link_public_key (string)
      4: link_sequence_number (uint64)
      5: previous_hash (string)
      6: signature (string)
      7: block_type (string)
      8: transaction (bytes, JSON-encoded)
      9: block_hash (string)
     10: timestamp (double)
    """
    parts = []
    if block.public_key:
        parts.append(_encode_string_field(1, block.public_key))
    parts.append(_encode_uint64_field(2, block.sequence_number))
    if block.link_public_key:
        parts.append(_encode_string_field(3, block.link_public_key))
    parts.append(_encode_uint64_field(4, block.link_sequence_number))
    if block.previous_hash:
        parts.append(_encode_string_field(5, block.previous_hash))
    if block.signature:
        parts.append(_encode_string_field(6, block.signature))
    if block.block_type:
        parts.append(_encode_string_field(7, block.block_type))

    tx_bytes = json.dumps(
        block.transaction, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    parts.append(_encode_bytes_field(8, tx_bytes))

    if block.block_hash:
        parts.append(_encode_string_field(9, block.block_hash))
    parts.append(_encode_double_field(10, block.timestamp))

    return b"".join(parts)


def proto_to_halfblock(data: bytes) -> HalfBlock:
    """Deserialize protobuf-compatible binary data to a HalfBlock.

    Parses the field tags and values according to HalfBlockProto schema.
    """
    fields: Dict[int, Any] = {}
    offset = 0

    while offset < len(data):
        tag_value, offset = _decode_varint(data, offset)
        field_number = tag_value >> 3
        wire_type = tag_value & 0x07

        if wire_type == WIRETYPE_VARINT:
            value, offset = _decode_varint(data, offset)
            fields[field_number] = value
        elif wire_type == WIRETYPE_FIXED64:
            value = struct.unpack("<d", data[offset : offset + 8])[0]
            offset += 8
            fields[field_number] = value
        elif wire_type == WIRETYPE_LENGTH_DELIMITED:
            length, offset = _decode_varint(data, offset)
            value = data[offset : offset + length]
            offset += length
            fields[field_number] = value
        else:
            raise ValueError(f"Unsupported wire type: {wire_type}")

    # Decode string fields from bytes
    def get_str(fn: int, default: str = "") -> str:
        val = fields.get(fn)
        if val is None:
            return default
        if isinstance(val, bytes):
            return val.decode("utf-8")
        return str(val)

    def get_int(fn: int, default: int = 0) -> int:
        return int(fields.get(fn, default))

    def get_float(fn: int, default: float = 0.0) -> float:
        return float(fields.get(fn, default))

    # Decode transaction from JSON bytes
    tx_bytes = fields.get(8, b"{}")
    if isinstance(tx_bytes, bytes):
        transaction = json.loads(tx_bytes)
    else:
        transaction = {}

    return HalfBlock(
        public_key=get_str(1),
        sequence_number=get_int(2),
        link_public_key=get_str(3),
        link_sequence_number=get_int(4),
        previous_hash=get_str(5),
        signature=get_str(6),
        block_type=get_str(7),
        transaction=transaction,
        block_hash=get_str(9),
        timestamp=get_float(10),
    )


# ---- Envelope serialization ----


def encode_envelope(
    msg_type: MessageType,
    payload: bytes,
    sender_pubkey: str,
    timestamp: Optional[float] = None,
) -> bytes:
    """Encode a transport envelope to protobuf-compatible binary.

    Envelope schema (from trustchain.proto):
      1: msg_type (uint32)
      2: payload (bytes)
      3: sender_pubkey (string)
      4: timestamp (double)
    """
    ts = timestamp if timestamp is not None else time.time()
    parts = [
        _encode_uint32_field(1, int(msg_type)),
        _encode_bytes_field(2, payload),
        _encode_string_field(3, sender_pubkey),
        _encode_double_field(4, ts),
    ]
    return b"".join(parts)


def decode_envelope(data: bytes) -> TransportMessage:
    """Decode protobuf-compatible binary data to a TransportMessage."""
    fields: Dict[int, Any] = {}
    offset = 0

    while offset < len(data):
        tag_value, offset = _decode_varint(data, offset)
        field_number = tag_value >> 3
        wire_type = tag_value & 0x07

        if wire_type == WIRETYPE_VARINT:
            value, offset = _decode_varint(data, offset)
            fields[field_number] = value
        elif wire_type == WIRETYPE_FIXED64:
            value = struct.unpack("<d", data[offset : offset + 8])[0]
            offset += 8
            fields[field_number] = value
        elif wire_type == WIRETYPE_LENGTH_DELIMITED:
            length, offset = _decode_varint(data, offset)
            value = data[offset : offset + length]
            offset += length
            fields[field_number] = value
        else:
            raise ValueError(f"Unsupported wire type: {wire_type}")

    msg_type_val = int(fields.get(1, 0))
    payload = fields.get(2, b"")
    if isinstance(payload, int):
        payload = b""

    sender = fields.get(3, b"")
    if isinstance(sender, bytes):
        sender = sender.decode("utf-8")

    ts = float(fields.get(4, 0.0))

    return TransportMessage(
        msg_type=MessageType(msg_type_val),
        payload=payload,
        sender_pubkey=sender,
        timestamp=ts,
    )


# ---- Convenience functions for specific message types ----


def encode_propose_message(block: HalfBlock) -> bytes:
    """Encode a ProposeMessage (field 1 = HalfBlockProto)."""
    block_bytes = halfblock_to_proto(block)
    return _encode_bytes_field(1, block_bytes)


def decode_propose_message(data: bytes) -> HalfBlock:
    """Decode a ProposeMessage to get the HalfBlock."""
    offset = 0
    while offset < len(data):
        tag_value, offset = _decode_varint(data, offset)
        field_number = tag_value >> 3
        wire_type = tag_value & 0x07
        if wire_type == WIRETYPE_LENGTH_DELIMITED:
            length, offset = _decode_varint(data, offset)
            if field_number == 1:
                return proto_to_halfblock(data[offset : offset + length])
            offset += length
        elif wire_type == WIRETYPE_VARINT:
            _, offset = _decode_varint(data, offset)
        elif wire_type == WIRETYPE_FIXED64:
            offset += 8
    raise ValueError("No block field found in ProposeMessage")


def encode_agree_message(
    block: Optional[HalfBlock] = None,
    accepted: bool = True,
    error: str = "",
) -> bytes:
    """Encode an AgreeMessage."""
    parts = []
    if block is not None:
        block_bytes = halfblock_to_proto(block)
        parts.append(_encode_bytes_field(1, block_bytes))
    parts.append(_encode_bool_field(2, accepted))
    if error:
        parts.append(_encode_string_field(3, error))
    return b"".join(parts)


def encode_crawl_request(
    public_key: str, start_seq: int = 1, limit: int = 100
) -> bytes:
    """Encode a CrawlRequest."""
    parts = [
        _encode_string_field(1, public_key),
        _encode_uint64_field(2, start_seq),
        _encode_uint32_field(3, limit),
    ]
    return b"".join(parts)


def encode_crawl_response(blocks: List[HalfBlock]) -> bytes:
    """Encode a CrawlResponse (repeated HalfBlockProto in field 1)."""
    parts = []
    for block in blocks:
        block_bytes = halfblock_to_proto(block)
        parts.append(_encode_bytes_field(1, block_bytes))
    return b"".join(parts)


def decode_crawl_response(data: bytes) -> List[HalfBlock]:
    """Decode a CrawlResponse to get the list of HalfBlocks."""
    blocks = []
    offset = 0
    while offset < len(data):
        tag_value, offset = _decode_varint(data, offset)
        field_number = tag_value >> 3
        wire_type = tag_value & 0x07
        if wire_type == WIRETYPE_LENGTH_DELIMITED:
            length, offset = _decode_varint(data, offset)
            if field_number == 1:
                blocks.append(proto_to_halfblock(data[offset : offset + length]))
            offset += length
        elif wire_type == WIRETYPE_VARINT:
            _, offset = _decode_varint(data, offset)
        elif wire_type == WIRETYPE_FIXED64:
            offset += 8
    return blocks
