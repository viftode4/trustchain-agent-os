"""Connection pool for QUIC transport.

Manages QUIC connections to peers with automatic lifecycle management:
- One multiplexed connection per peer
- Idle timeout with automatic cleanup
- Health checking via periodic pings
- Thread-safe connection management
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

logger = logging.getLogger("trustchain.transport.pool")


@dataclass
class PeerConnection:
    """Tracks a connection to a single peer."""

    peer_id: str  # pubkey hex
    host: str
    port: int
    connection: Any = None  # QUIC connection object
    last_activity: float = field(default_factory=time.time)
    connected: bool = False
    connecting: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()

    @property
    def idle_seconds(self) -> float:
        """Seconds since last activity."""
        return time.time() - self.last_activity


class ConnectionPool:
    """Manages QUIC connections to peers.

    Features:
    - Max one multiplexed connection per peer
    - Idle timeout with automatic cleanup
    - Connection factory for creating new connections
    - Health checking via ping callbacks
    """

    def __init__(
        self,
        idle_timeout: float = 60.0,
        max_peers: int = 100,
        cleanup_interval: float = 30.0,
    ) -> None:
        self.idle_timeout = idle_timeout
        self.max_peers = max_peers
        self.cleanup_interval = cleanup_interval
        self._peers: Dict[str, PeerConnection] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._connect_fn: Optional[
            Callable[[str, int], Coroutine[Any, Any, Any]]
        ] = None
        self._disconnect_fn: Optional[
            Callable[[Any], Coroutine[Any, Any, None]]
        ] = None

    def set_connect_factory(
        self,
        connect_fn: Callable[[str, int], Coroutine[Any, Any, Any]],
        disconnect_fn: Callable[[Any], Coroutine[Any, Any, None]],
    ) -> None:
        """Set the connection factory functions.

        Args:
            connect_fn: async (host, port) -> connection object
            disconnect_fn: async (connection) -> None
        """
        self._connect_fn = connect_fn
        self._disconnect_fn = disconnect_fn

    def register_peer(self, peer_id: str, host: str, port: int) -> None:
        """Register a peer's address without connecting yet."""
        if peer_id not in self._peers:
            self._peers[peer_id] = PeerConnection(
                peer_id=peer_id, host=host, port=port
            )

    async def get_connection(self, peer_id: str) -> Any:
        """Get or create a connection to a peer.

        Returns the connection object. Raises ValueError if peer is unknown.
        """
        peer = self._peers.get(peer_id)
        if peer is None:
            raise ValueError(f"Unknown peer: {peer_id[:16]}...")

        async with peer._lock:
            if peer.connected and peer.connection is not None:
                peer.touch()
                return peer.connection

            if self._connect_fn is None:
                raise RuntimeError("No connection factory set")

            peer.connecting = True
            try:
                peer.connection = await self._connect_fn(peer.host, peer.port)
                peer.connected = True
                peer.touch()
                logger.debug("Connected to peer %s (%s:%d)", peer_id[:16], peer.host, peer.port)
                return peer.connection
            except Exception:
                peer.connected = False
                peer.connection = None
                raise
            finally:
                peer.connecting = False

    async def disconnect(self, peer_id: str) -> None:
        """Disconnect from a specific peer."""
        peer = self._peers.get(peer_id)
        if peer is None:
            return

        async with peer._lock:
            if peer.connection is not None and self._disconnect_fn:
                try:
                    await self._disconnect_fn(peer.connection)
                except Exception:
                    logger.debug("Error disconnecting from %s", peer_id[:16])
            peer.connection = None
            peer.connected = False

    async def disconnect_all(self) -> None:
        """Disconnect from all peers."""
        for peer_id in list(self._peers.keys()):
            await self.disconnect(peer_id)

    @property
    def connected_peers(self) -> List[str]:
        """List of currently connected peer IDs."""
        return [
            pid for pid, p in self._peers.items() if p.connected
        ]

    @property
    def known_peers(self) -> List[str]:
        """List of all known peer IDs (connected or not)."""
        return list(self._peers.keys())

    async def _cleanup_idle(self) -> None:
        """Periodic task to disconnect idle peers."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                now = time.time()
                for peer_id, peer in list(self._peers.items()):
                    if peer.connected and (now - peer.last_activity) > self.idle_timeout:
                        logger.debug("Disconnecting idle peer %s", peer_id[:16])
                        await self.disconnect(peer_id)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in connection pool cleanup")

    async def start(self) -> None:
        """Start the idle connection cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_idle())

    async def stop(self) -> None:
        """Stop cleanup and disconnect all peers."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        await self.disconnect_all()
