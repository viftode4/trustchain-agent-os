"""Transport abstraction layer for TrustChain.

Decouples protocol logic from wire format. Supports HTTP, QUIC, and gRPC
transports behind a common interface.
"""

from trustchain.transport.base import (
    MessageType,
    Transport,
    TransportError,
    TransportMessage,
)

__all__ = [
    "MessageType",
    "Transport",
    "TransportError",
    "TransportMessage",
]
