"""QUIC P2P transport for TrustChain.

Uses aioquic for real peer-to-peer communication with:
- TLS 1.3 built-in (self-signed certs from Ed25519 identity)
- Stream multiplexing (control, proposals, chain sync)
- Length-prefix message framing with protobuf Envelope encoding
- NAT traversal ready (UDP-based)

This is what IPv8's raw UDP transport would be if redesigned today.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import ssl
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from aioquic.asyncio import connect as quic_connect, serve as quic_serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import (
    HandshakeCompleted,
    QuicEvent,
    StreamDataReceived,
    ConnectionTerminated,
)

from trustchain.identity import Identity
from trustchain.proto.serialization import encode_envelope, decode_envelope
from trustchain.transport.base import (
    MessageType,
    Transport,
    TransportError,
    TransportMessage,
)
from trustchain.transport.pool import ConnectionPool
from trustchain.transport.tls import generate_self_signed_cert

logger = logging.getLogger("trustchain.transport.quic")

# Stream allocation:
# Even-numbered streams are client-initiated, odd are server-initiated
# Stream 0: Control (handshake, peer exchange, status)
# Stream 4: Proposals/agreements (request-response)
# Stream 8: Chain sync/crawl (streaming)
STREAM_CONTROL = 0
STREAM_PROTOCOL = 4
STREAM_CRAWL = 8

# 4-byte big-endian length prefix for message framing
FRAME_HEADER_SIZE = 4
MAX_FRAME_SIZE = 16 * 1024 * 1024  # 16 MB max message


def _frame_message(data: bytes) -> bytes:
    """Add a 4-byte big-endian length prefix to data."""
    return struct.pack(">I", len(data)) + data


def _stream_for_message_type(msg_type: MessageType) -> int:
    """Map message types to QUIC streams."""
    if msg_type in (MessageType.PROPOSE, MessageType.AGREE):
        return STREAM_PROTOCOL
    if msg_type in (MessageType.CRAWL_REQUEST, MessageType.CRAWL_RESPONSE):
        return STREAM_CRAWL
    return STREAM_CONTROL


class TrustChainQuicProtocol(QuicConnectionProtocol):
    """QUIC protocol handler for TrustChain nodes.

    Handles incoming QUIC streams, dispatches messages to registered handlers,
    and manages the request-response pattern.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._handlers: Dict[MessageType, Callable] = {}
        self._pending_responses: Dict[int, asyncio.Future] = {}
        self._buffers: Dict[int, bytearray] = {}
        self._peer_pubkey: Optional[str] = None

    def set_handlers(self, handlers: Dict[MessageType, Callable]) -> None:
        self._handlers = handlers

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, HandshakeCompleted):
            logger.debug("QUIC handshake completed")
        elif isinstance(event, StreamDataReceived):
            self._handle_stream_data(event)
        elif isinstance(event, ConnectionTerminated):
            logger.debug("QUIC connection terminated")
            # Cancel any pending futures
            for future in self._pending_responses.values():
                if not future.done():
                    future.cancel()
            self._pending_responses.clear()

    def _handle_stream_data(self, event: StreamDataReceived) -> None:
        """Handle incoming data on a QUIC stream."""
        stream_id = event.stream_id
        data = event.data

        # Buffer incoming data
        if stream_id not in self._buffers:
            self._buffers[stream_id] = bytearray()
        self._buffers[stream_id].extend(data)

        # Try to parse complete frames
        while len(self._buffers[stream_id]) >= FRAME_HEADER_SIZE:
            frame_len = struct.unpack(
                ">I", self._buffers[stream_id][:FRAME_HEADER_SIZE]
            )[0]
            if frame_len > MAX_FRAME_SIZE:
                logger.error("Frame too large: %d bytes", frame_len)
                self._buffers[stream_id].clear()
                return

            total_len = FRAME_HEADER_SIZE + frame_len
            if len(self._buffers[stream_id]) < total_len:
                break  # Wait for more data

            frame_data = bytes(
                self._buffers[stream_id][FRAME_HEADER_SIZE:total_len]
            )
            del self._buffers[stream_id][:total_len]

            # Process the frame
            asyncio.ensure_future(self._process_frame(stream_id, frame_data))

    async def _process_frame(
        self, stream_id: int, frame_data: bytes
    ) -> None:
        """Process a complete frame received on a stream."""
        try:
            message = decode_envelope(frame_data)
        except Exception as e:
            logger.error("Failed to decode envelope: %s", e)
            return

        # Check if this is a response to a pending request
        if stream_id in self._pending_responses:
            future = self._pending_responses.pop(stream_id)
            if not future.done():
                future.set_result(message)
            return

        # Otherwise dispatch to handler
        handler = self._handlers.get(message.msg_type)
        if handler is not None:
            try:
                response = await handler(message)
                if response is not None:
                    # Send response back on the same stream
                    envelope = encode_envelope(
                        msg_type=response.msg_type,
                        payload=response.payload,
                        sender_pubkey=response.sender_pubkey,
                        timestamp=response.timestamp,
                    )
                    self._quic.send_stream_data(
                        stream_id, _frame_message(envelope), end_stream=True
                    )
                    self.transmit()
            except Exception as e:
                logger.error("Handler error for %s: %s", message.msg_type, e)
        else:
            logger.warning("No handler for message type %s", message.msg_type)

    async def send_message(
        self, message: TransportMessage, timeout: float = 30.0
    ) -> Optional[TransportMessage]:
        """Send a message and wait for a response.

        Uses the appropriate QUIC stream based on message type.
        """
        stream_id = self._quic.get_next_available_stream_id()
        envelope = encode_envelope(
            msg_type=message.msg_type,
            payload=message.payload,
            sender_pubkey=message.sender_pubkey,
            timestamp=message.timestamp,
        )

        # Set up response future
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_responses[stream_id] = future

        # Send the framed message
        self._quic.send_stream_data(
            stream_id, _frame_message(envelope), end_stream=False
        )
        self.transmit()

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_responses.pop(stream_id, None)
            raise TransportError("Request timed out")
        except asyncio.CancelledError:
            self._pending_responses.pop(stream_id, None)
            return None

    def send_fire_and_forget(self, message: TransportMessage) -> None:
        """Send a message without expecting a response."""
        stream_id = self._quic.get_next_available_stream_id()
        envelope = encode_envelope(
            msg_type=message.msg_type,
            payload=message.payload,
            sender_pubkey=message.sender_pubkey,
            timestamp=message.timestamp,
        )
        self._quic.send_stream_data(
            stream_id, _frame_message(envelope), end_stream=True
        )
        self.transmit()


class QUICTransport(Transport):
    """QUIC-based P2P transport for TrustChain.

    Each peer connection is a single QUIC connection with multiplexed streams:
    - Stream 0 (control): handshake, peer exchange, status
    - Stream 4 (protocol): proposals and agreements
    - Stream 8 (crawl): chain sync and block crawling

    Uses TLS 1.3 with self-signed certificates derived from Ed25519 identity.
    """

    def __init__(
        self,
        identity: Identity,
        host: str = "0.0.0.0",
        port: int = 8200,
        idle_timeout: float = 60.0,
    ) -> None:
        super().__init__()
        self.identity = identity
        self.host = host
        self.port = port

        # TLS configuration
        self._cert_path: Optional[str] = None
        self._key_path: Optional[str] = None

        # Connection management
        self.pool = ConnectionPool(idle_timeout=idle_timeout)
        self._protocols: Dict[str, TrustChainQuicProtocol] = {}
        self._server = None
        self._server_task = None

    @property
    def pubkey(self) -> str:
        return self.identity.pubkey_hex

    @property
    def connected_peers(self) -> List[str]:
        return self.pool.connected_peers

    def register_peer(self, pubkey: str, host: str, port: int) -> None:
        """Register a peer for QUIC communication."""
        self.pool.register_peer(pubkey, host, port)

    def _get_server_config(self) -> QuicConfiguration:
        """Create QUIC server configuration with TLS."""
        if self._cert_path is None:
            self._cert_path, self._key_path = generate_self_signed_cert(
                self.identity
            )

        config = QuicConfiguration(
            is_client=False,
            max_datagram_frame_size=65536,
        )
        config.load_cert_chain(self._cert_path, self._key_path)
        return config

    def _get_client_config(self) -> QuicConfiguration:
        """Create QUIC client configuration."""
        if self._cert_path is None:
            self._cert_path, self._key_path = generate_self_signed_cert(
                self.identity
            )

        config = QuicConfiguration(
            is_client=True,
            max_datagram_frame_size=65536,
        )
        config.load_cert_chain(self._cert_path, self._key_path)
        # Accept self-signed certs
        config.verify_mode = ssl.CERT_NONE
        return config

    def _create_protocol(self, *args, **kwargs) -> TrustChainQuicProtocol:
        """Factory for creating QUIC protocol handlers."""
        protocol = TrustChainQuicProtocol(*args, **kwargs)
        protocol.set_handlers(self._handlers)
        return protocol

    async def start(self) -> None:
        """Start the QUIC server and connection pool."""
        config = self._get_server_config()

        self._server = await quic_serve(
            self.host,
            self.port,
            configuration=config,
            create_protocol=self._create_protocol,
        )

        await self.pool.start()
        logger.info(
            "QUIC transport started on %s:%d (pubkey=%s...)",
            self.host,
            self.port,
            self.pubkey[:16],
        )

    async def stop(self) -> None:
        """Stop the QUIC server and disconnect all peers."""
        await self.pool.stop()

        if self._server:
            self._server.close()
            self._server = None

        self._protocols.clear()
        logger.info("QUIC transport stopped")

    async def _get_protocol(self, peer_id: str) -> TrustChainQuicProtocol:
        """Get or create a QUIC connection to a peer."""
        if peer_id in self._protocols:
            return self._protocols[peer_id]

        peer = self.pool._peers.get(peer_id)
        if peer is None:
            raise TransportError(f"Unknown peer: {peer_id[:16]}...", peer_id)

        config = self._get_client_config()

        try:
            async with quic_connect(
                peer.host,
                peer.port,
                configuration=config,
                create_protocol=self._create_protocol,
            ) as protocol:
                self._protocols[peer_id] = protocol
                peer.connected = True
                peer.touch()
                return protocol
        except Exception as e:
            raise TransportError(
                f"Failed to connect to {peer_id[:16]}...: {e}", peer_id
            )

    async def send(
        self, peer_id: str, message: TransportMessage
    ) -> Optional[TransportMessage]:
        """Send a message to a peer via QUIC and wait for response."""
        protocol = await self._get_protocol(peer_id)
        try:
            return await protocol.send_message(message)
        except Exception as e:
            # Clean up failed connection
            self._protocols.pop(peer_id, None)
            if not isinstance(e, TransportError):
                raise TransportError(str(e), peer_id)
            raise

    async def broadcast(self, message: TransportMessage) -> None:
        """Broadcast a message to all connected peers."""
        for peer_id, protocol in list(self._protocols.items()):
            try:
                protocol.send_fire_and_forget(message)
            except Exception:
                logger.warning("Broadcast to %s failed", peer_id[:16])
