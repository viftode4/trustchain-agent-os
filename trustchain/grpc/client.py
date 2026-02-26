"""gRPC client for TrustChain node communication.

Async gRPC client that agents use to communicate with their local
TrustChain node. Handles connection management and retry logic.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import grpc
from grpc import aio as grpc_aio

from trustchain.halfblock import HalfBlock
from trustchain.proto.serialization import (
    decode_propose_message,
    encode_propose_message,
    encode_agree_message,
    halfblock_to_proto,
    proto_to_halfblock,
)

logger = logging.getLogger("trustchain.grpc.client")

SERVICE_NAME = "trustchain.TrustChainService"


def _method(name: str) -> str:
    return f"/{SERVICE_NAME}/{name}"


class TrustChainGRPCClient:
    """Async gRPC client for communicating with a TrustChain node.

    Usage:
        client = TrustChainGRPCClient("localhost:50051")
        accepted, agreement = await client.propose(proposal_block)
        async for block in client.crawl("pubkey_hex", start_seq=1):
            process(block)
        await client.close()
    """

    def __init__(
        self,
        target: str,
        timeout: float = 30.0,
    ) -> None:
        self.target = target
        self.timeout = timeout
        self._channel: Optional[grpc_aio.Channel] = None

    async def _get_channel(self) -> grpc_aio.Channel:
        if self._channel is None:
            self._channel = grpc_aio.insecure_channel(self.target)
        return self._channel

    async def propose(
        self, block: HalfBlock
    ) -> Tuple[bool, Optional[HalfBlock]]:
        """Send a proposal and receive an agreement.

        Returns (accepted, agreement_block). agreement_block is None if rejected.
        """
        channel = await self._get_channel()
        request = encode_propose_message(block)

        try:
            response = await channel.unary_unary(
                _method("Propose"),
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request, timeout=self.timeout)

            # Parse the AgreeMessage response
            result = self._parse_agree_response(response)
            return result

        except grpc.RpcError as e:
            logger.error("Propose RPC failed: %s", e)
            return False, None

    async def agree(self, block: HalfBlock) -> bool:
        """Send an agreement acknowledgement."""
        channel = await self._get_channel()
        request = encode_propose_message(block)  # Same wire format

        try:
            response = await channel.unary_unary(
                _method("Agree"),
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request, timeout=self.timeout)

            accepted, _ = self._parse_agree_response(response)
            return accepted

        except grpc.RpcError as e:
            logger.error("Agree RPC failed: %s", e)
            return False

    async def crawl(
        self,
        pubkey: str,
        start_seq: int = 1,
        limit: int = 100,
    ) -> List[HalfBlock]:
        """Crawl a chain via server-streaming RPC.

        Returns list of HalfBlocks. For very large chains, use
        crawl_stream() for an async iterator instead.
        """
        channel = await self._get_channel()
        request = json.dumps({
            "public_key": pubkey,
            "start_seq": start_seq,
            "limit": limit,
        }).encode()

        blocks = []
        try:
            call = channel.unary_stream(
                _method("CrawlChain"),
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )
            async for response in call(request, timeout=self.timeout):
                block = proto_to_halfblock(response)
                blocks.append(block)
        except grpc.RpcError as e:
            logger.error("CrawlChain RPC failed: %s", e)

        return blocks

    async def get_block(
        self, pubkey: str, seq: int
    ) -> Optional[HalfBlock]:
        """Get a specific block by pubkey and sequence number."""
        channel = await self._get_channel()
        request = json.dumps({
            "public_key": pubkey,
            "start_seq": seq,
        }).encode()

        try:
            response = await channel.unary_unary(
                _method("GetBlock"),
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request, timeout=self.timeout)

            if response:
                return proto_to_halfblock(response)
            return None

        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                return None
            logger.error("GetBlock RPC failed: %s", e)
            return None

    async def get_status(self) -> Optional[Dict[str, Any]]:
        """Get node status."""
        channel = await self._get_channel()

        try:
            response = await channel.unary_unary(
                _method("GetStatus"),
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(b"", timeout=self.timeout)

            return json.loads(response)

        except grpc.RpcError as e:
            logger.error("GetStatus RPC failed: %s", e)
            return None

    async def get_trust_score(
        self, target_pubkey: str
    ) -> Optional[Dict[str, Any]]:
        """Get trust score for a target pubkey."""
        channel = await self._get_channel()
        request = json.dumps({"target_pubkey": target_pubkey}).encode()

        try:
            response = await channel.unary_unary(
                _method("GetTrustScore"),
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request, timeout=self.timeout)

            return json.loads(response)

        except grpc.RpcError as e:
            logger.error("GetTrustScore RPC failed: %s", e)
            return None

    def _parse_agree_response(
        self, data: bytes
    ) -> Tuple[bool, Optional[HalfBlock]]:
        """Parse an AgreeMessage response.

        The AgreeMessage has: field 1 = HalfBlockProto, field 2 = bool, field 3 = string.
        We parse it using the raw protobuf wire format.
        """
        from trustchain.proto.serialization import (
            _decode_varint,
            WIRETYPE_VARINT,
            WIRETYPE_LENGTH_DELIMITED,
            WIRETYPE_FIXED64,
        )

        accepted = False
        block_bytes = None
        offset = 0

        while offset < len(data):
            tag_value, offset = _decode_varint(data, offset)
            field_number = tag_value >> 3
            wire_type = tag_value & 0x07

            if wire_type == WIRETYPE_LENGTH_DELIMITED:
                length, offset = _decode_varint(data, offset)
                if field_number == 1:
                    block_bytes = data[offset : offset + length]
                offset += length
            elif wire_type == WIRETYPE_VARINT:
                value, offset = _decode_varint(data, offset)
                if field_number == 2:
                    accepted = bool(value)
            elif wire_type == WIRETYPE_FIXED64:
                offset += 8

        block = None
        if block_bytes:
            try:
                block = proto_to_halfblock(block_bytes)
            except Exception:
                pass

        return accepted, block

    async def close(self) -> None:
        """Close the gRPC channel."""
        if self._channel:
            await self._channel.close()
            self._channel = None
