"""gRPC server for TrustChain nodes.

Starts a gRPC server with the TrustChainServicer handling all RPCs.
Integrates with TrustChainNode lifecycle (start/stop).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import grpc
from grpc import aio as grpc_aio

from trustchain.blockstore import BlockStore
from trustchain.grpc.service import TrustChainServicer, _build_generic_handlers
from trustchain.identity import Identity
from trustchain.protocol import TrustChainProtocol

logger = logging.getLogger("trustchain.grpc.server")

DEFAULT_GRPC_PORT = 50051


async def start_grpc_server(
    protocol: TrustChainProtocol,
    store: BlockStore,
    identity: Identity,
    port: int = DEFAULT_GRPC_PORT,
    trust_engine: Optional[Any] = None,
) -> grpc_aio.Server:
    """Create and start a gRPC server for TrustChain.

    Args:
        protocol: The TrustChainProtocol instance for handling proposals/agreements.
        store: The BlockStore for chain data.
        identity: This node's identity.
        port: The port to listen on (default 50051).
        trust_engine: Optional TrustEngine for trust score queries.

    Returns:
        The running gRPC server instance.
    """
    servicer = TrustChainServicer(
        protocol=protocol,
        store=store,
        identity=identity,
        trust_engine=trust_engine,
    )

    server = grpc_aio.server()
    handlers = _build_generic_handlers(servicer)
    for handler in handlers:
        server.add_generic_rpc_handlers([handler])

    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)

    await server.start()
    logger.info("gRPC server started on port %d", port)

    return server


async def stop_grpc_server(
    server: grpc_aio.Server,
    grace: float = 5.0,
) -> None:
    """Gracefully stop a gRPC server.

    Args:
        server: The server to stop.
        grace: Grace period in seconds for in-flight RPCs.
    """
    await server.stop(grace)
    logger.info("gRPC server stopped")
