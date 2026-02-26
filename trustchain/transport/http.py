"""HTTP transport implementation.

Wraps the existing TrustChainClient + FastAPI server behind the Transport ABC.
This is the default transport — all existing functionality is preserved.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from trustchain.blockstore import BlockStore
from trustchain.halfblock import HalfBlock
from trustchain.identity import Identity
from trustchain.transport.base import (
    MessageType,
    Transport,
    TransportError,
    TransportMessage,
)

logger = logging.getLogger("trustchain.transport.http")


def halfblock_to_bytes(block: HalfBlock) -> bytes:
    """Serialize a HalfBlock to JSON bytes for transport."""
    return json.dumps(block.to_dict(), sort_keys=True).encode()


def bytes_to_halfblock(data: bytes) -> HalfBlock:
    """Deserialize JSON bytes back to a HalfBlock."""
    return HalfBlock.from_dict(json.loads(data))


def _encode_payload(msg_type: MessageType, **kwargs: Any) -> bytes:
    """Encode a message payload as JSON bytes."""
    return json.dumps(kwargs, sort_keys=True).encode()


def _decode_payload(data: bytes) -> Dict[str, Any]:
    """Decode JSON bytes to a dict."""
    return json.loads(data)


class HTTPTransport(Transport):
    """HTTP-based transport using httpx client and FastAPI server.

    This wraps the existing HTTP infrastructure behind the Transport ABC.
    Peers are identified by their pubkey, with URL mappings stored in peers dict.
    """

    def __init__(
        self,
        identity: Identity,
        host: str = "0.0.0.0",
        port: int = 8100,
    ) -> None:
        super().__init__()
        self.identity = identity
        self.host = host
        self.port = port
        self.peers: Dict[str, str] = {}  # pubkey -> URL
        self._client = None
        self._server = None
        self._serve_task = None

    @property
    def pubkey(self) -> str:
        return self.identity.pubkey_hex

    @property
    def connected_peers(self) -> List[str]:
        return list(self.peers.keys())

    def register_peer(self, pubkey: str, url: str) -> None:
        """Register a peer's HTTP URL."""
        self.peers[pubkey] = url

    async def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def send(
        self, peer_id: str, message: TransportMessage
    ) -> Optional[TransportMessage]:
        """Send a message to a peer via HTTP POST.

        Maps MessageType to the appropriate HTTP endpoint.
        """
        peer_url = self.peers.get(peer_id)
        if not peer_url:
            raise TransportError(f"Unknown peer: {peer_id[:16]}...", peer_id)

        client = await self._get_client()
        payload = _decode_payload(message.payload)

        try:
            if message.msg_type == MessageType.PROPOSE:
                resp = await client.post(
                    f"{peer_url}/trustchain/propose",
                    json={"block": payload.get("block", payload)},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return TransportMessage(
                        msg_type=MessageType.AGREE,
                        payload=json.dumps(data).encode(),
                        sender_pubkey=peer_id,
                    )
                return TransportMessage(
                    msg_type=MessageType.AGREE,
                    payload=json.dumps(
                        {"accepted": False, "error": f"HTTP {resp.status_code}"}
                    ).encode(),
                    sender_pubkey=peer_id,
                )

            elif message.msg_type == MessageType.CRAWL_REQUEST:
                pubkey = payload.get("public_key", "")
                start_seq = payload.get("start_seq", 1)
                limit = payload.get("limit", 100)
                resp = await client.get(
                    f"{peer_url}/trustchain/blocks/{pubkey}",
                    params={"start_seq": start_seq, "limit": limit},
                )
                if resp.status_code == 200:
                    return TransportMessage(
                        msg_type=MessageType.CRAWL_RESPONSE,
                        payload=json.dumps(resp.json()).encode(),
                        sender_pubkey=peer_id,
                    )
                return None

            elif message.msg_type == MessageType.STATUS_REQUEST:
                resp = await client.get(f"{peer_url}/trustchain/status")
                if resp.status_code == 200:
                    return TransportMessage(
                        msg_type=MessageType.STATUS_RESPONSE,
                        payload=json.dumps(resp.json()).encode(),
                        sender_pubkey=peer_id,
                    )
                return None

            else:
                raise TransportError(
                    f"Unsupported message type for HTTP: {message.msg_type}",
                    peer_id,
                )

        except TransportError:
            raise
        except Exception as e:
            raise TransportError(str(e), peer_id)

    async def broadcast(self, message: TransportMessage) -> None:
        """Broadcast a message to all known peers."""
        for peer_id in list(self.peers.keys()):
            try:
                await self.send(peer_id, message)
            except TransportError:
                logger.warning("Broadcast to %s failed", peer_id[:16])

    async def start(self) -> None:
        """Start the HTTP server as a background task."""
        # Server startup is handled by TrustChainNode — the transport
        # itself just manages the client side. The FastAPI app is built
        # by the node and served separately.
        pass

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
