"""gRPC service implementation for TrustChain.

Implements the TrustChainService using grpcio's generic service handler.
No protoc compilation needed — we handle serialization manually using
the proto/serialization module.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import grpc
from grpc import aio as grpc_aio

from trustchain.blockstore import BlockStore
from trustchain.halfblock import HalfBlock
from trustchain.identity import Identity
from trustchain.proto.serialization import (
    halfblock_to_proto,
    proto_to_halfblock,
    encode_propose_message,
    decode_propose_message,
    encode_agree_message,
    encode_crawl_response,
)
from trustchain.protocol import TrustChainProtocol

logger = logging.getLogger("trustchain.grpc.service")


# Service name and method paths matching the .proto service definition
SERVICE_NAME = "trustchain.TrustChainService"


def _method(name: str) -> str:
    return f"/{SERVICE_NAME}/{name}"


class TrustChainServicer:
    """gRPC servicer that implements the TrustChainService interface.

    Maps gRPC method calls to TrustChainProtocol + BlockStore operations.
    """

    def __init__(
        self,
        protocol: TrustChainProtocol,
        store: BlockStore,
        identity: Identity,
        trust_engine: Optional[Any] = None,
    ) -> None:
        self.protocol = protocol
        self.store = store
        self.identity = identity
        self._trust_engine = trust_engine
        self._peers: Dict[str, str] = {}  # pubkey -> url

    @property
    def pubkey(self) -> str:
        return self.identity.pubkey_hex

    async def Propose(
        self, request_data: bytes, context: grpc_aio.ServicerContext
    ) -> bytes:
        """Handle a proposal: validate, create agreement, return it."""
        try:
            proposal = decode_propose_message(request_data)

            # Validate and store the proposal
            self.protocol.receive_proposal(proposal)

            # Create agreement
            agreement = self.protocol.create_agreement(proposal)

            return encode_agree_message(
                block=agreement, accepted=True
            )

        except Exception as e:
            logger.warning("Proposal rejected via gRPC: %s", e)
            return encode_agree_message(
                accepted=False, error="Proposal validation failed"
            )

    async def Agree(
        self, request_data: bytes, context: grpc_aio.ServicerContext
    ) -> bytes:
        """Handle an incoming agreement."""
        try:
            # Parse the agreement block from the request
            # AgreeMessage has block at field 1
            agreement = decode_propose_message(request_data)  # Same wire format
            self.protocol.receive_agreement(agreement)
            return encode_agree_message(accepted=True)
        except Exception as e:
            logger.warning("Agreement rejected via gRPC: %s", e)
            return encode_agree_message(
                accepted=False, error="Agreement validation failed"
            )

    async def CrawlChain(
        self, request_data: bytes, context: grpc_aio.ServicerContext
    ):
        """Stream blocks for a given pubkey. Server-streaming RPC."""
        try:
            # Parse CrawlRequest manually
            request = json.loads(request_data) if request_data else {}
            pubkey = request.get("public_key", "")
            start_seq = request.get("start_seq", 1)
            limit = request.get("limit", 100)

            blocks = self.store.crawl(pubkey, start_seq)
            for block in blocks[:limit]:
                yield halfblock_to_proto(block)

        except Exception as e:
            logger.error("CrawlChain error: %s", e)

    async def GetBlock(
        self, request_data: bytes, context: grpc_aio.ServicerContext
    ) -> bytes:
        """Get a specific block by pubkey and sequence number."""
        try:
            request = json.loads(request_data) if request_data else {}
            pubkey = request.get("public_key", "")
            seq = request.get("start_seq", 1)

            block = self.store.get_block(pubkey, seq)
            if block is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Block not found")
                return b""

            return halfblock_to_proto(block)

        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return b""

    async def GetStatus(
        self, request_data: bytes, context: grpc_aio.ServicerContext
    ) -> bytes:
        """Return node status information."""
        status = {
            "public_key": self.pubkey,
            "chain_length": self.store.get_latest_seq(self.pubkey),
            "total_blocks": self.store.get_block_count(),
            "peer_keys": list(self._peers.keys()),
        }
        return json.dumps(status).encode()

    async def GetPeers(
        self, request_data: bytes, context: grpc_aio.ServicerContext
    ) -> bytes:
        """Return known peer list."""
        peers = [
            {"public_key": pk, "url": url}
            for pk, url in self._peers.items()
        ]
        return json.dumps({"peers": peers}).encode()

    async def GetTrustScore(
        self, request_data: bytes, context: grpc_aio.ServicerContext
    ) -> bytes:
        """Compute and return trust score for a target pubkey."""
        try:
            request = json.loads(request_data) if request_data else {}
            target = request.get("target_pubkey", "")

            trust_score = 0.0
            chain_integrity = 1.0
            netflow_score = 0.0
            interaction_count = 0

            if self._trust_engine:
                trust_score = self._trust_engine.compute_trust(target)
                chain_integrity = self._trust_engine.compute_chain_integrity(
                    target
                )
                try:
                    netflow_score = self._trust_engine.compute_netflow_score(
                        target
                    )
                except Exception:
                    pass
            interaction_count = self.store.get_latest_seq(target)

            result = {
                "target_pubkey": target,
                "trust_score": trust_score,
                "chain_integrity": chain_integrity,
                "netflow_score": netflow_score,
                "interaction_count": interaction_count,
            }
            return json.dumps(result).encode()

        except Exception as e:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return b""


class _TrustChainGenericHandler(grpc.GenericRpcHandler):
    """Generic RPC handler that routes method calls to the servicer.

    This allows us to register handlers without protoc-generated stubs.
    """

    def __init__(self, servicer: TrustChainServicer) -> None:
        self._method_handlers = {
            _method("Propose"): grpc.unary_unary_rpc_method_handler(
                servicer.Propose,
            ),
            _method("Agree"): grpc.unary_unary_rpc_method_handler(
                servicer.Agree,
            ),
            _method("GetBlock"): grpc.unary_unary_rpc_method_handler(
                servicer.GetBlock,
            ),
            _method("GetStatus"): grpc.unary_unary_rpc_method_handler(
                servicer.GetStatus,
            ),
            _method("GetPeers"): grpc.unary_unary_rpc_method_handler(
                servicer.GetPeers,
            ),
            _method("GetTrustScore"): grpc.unary_unary_rpc_method_handler(
                servicer.GetTrustScore,
            ),
            _method("CrawlChain"): grpc.unary_stream_rpc_method_handler(
                servicer.CrawlChain,
            ),
        }

    def service(self, handler_call_details):
        return self._method_handlers.get(handler_call_details.method)


def _build_generic_handlers(servicer: TrustChainServicer) -> list:
    """Build grpc generic handlers for the servicer."""
    return [_TrustChainGenericHandler(servicer)]
