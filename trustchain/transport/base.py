"""Transport abstraction base classes.

Defines the Transport ABC and message types that all transport implementations
(HTTP, QUIC, gRPC) must conform to.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine, Dict, List, Optional


class MessageType(IntEnum):
    """Wire message types for TrustChain P2P communication."""

    PROPOSE = 1
    AGREE = 2
    CRAWL_REQUEST = 3
    CRAWL_RESPONSE = 4
    CHECKPOINT = 5
    PEER_EXCHANGE = 6
    STATUS_REQUEST = 7
    STATUS_RESPONSE = 8


@dataclass
class TransportMessage:
    """A message exchanged between TrustChain nodes.

    Attributes:
        msg_type: The type of message (propose, agree, crawl, etc.)
        payload: Serialized message content (bytes).
        sender_pubkey: Hex pubkey of the sending node.
        timestamp: Unix timestamp of message creation.
    """

    msg_type: MessageType
    payload: bytes
    sender_pubkey: str
    timestamp: float = field(default_factory=time.time)


class TransportError(Exception):
    """Raised when a transport operation fails."""

    def __init__(self, message: str, peer_id: Optional[str] = None) -> None:
        self.peer_id = peer_id
        super().__init__(message)


# Type alias for message handlers
MessageHandler = Callable[
    [TransportMessage], Coroutine[Any, Any, Optional[TransportMessage]]
]


class Transport(ABC):
    """Abstract transport for TrustChain node-to-node communication.

    All transport implementations (HTTP, QUIC, gRPC) must implement this
    interface. The protocol engine (TrustChainProtocol) is transport-agnostic
    and communicates through this abstraction.
    """

    def __init__(self) -> None:
        self._handlers: Dict[MessageType, MessageHandler] = {}

    def register_handler(
        self, msg_type: MessageType, handler: MessageHandler
    ) -> None:
        """Register a handler for a specific message type.

        When an incoming message of this type is received, the handler
        is called with the TransportMessage and should return an optional
        response TransportMessage.
        """
        self._handlers[msg_type] = handler

    def get_handler(self, msg_type: MessageType) -> Optional[MessageHandler]:
        """Get the registered handler for a message type."""
        return self._handlers.get(msg_type)

    @abstractmethod
    async def send(
        self, peer_id: str, message: TransportMessage
    ) -> Optional[TransportMessage]:
        """Send a message to a specific peer and optionally receive a response.

        Args:
            peer_id: The pubkey or address of the target peer.
            message: The message to send.

        Returns:
            Response message from the peer, or None for fire-and-forget.

        Raises:
            TransportError: If the send fails.
        """

    @abstractmethod
    async def broadcast(self, message: TransportMessage) -> None:
        """Broadcast a message to all connected peers.

        Fire-and-forget — no responses expected.
        """

    @abstractmethod
    async def start(self) -> None:
        """Start the transport (begin listening for incoming messages)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport (close connections, stop listening)."""

    @property
    @abstractmethod
    def connected_peers(self) -> List[str]:
        """List of currently connected peer identifiers."""
