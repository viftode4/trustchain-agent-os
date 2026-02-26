"""Tests for HTTP/3 upgrade (Phase 6).

Tests Hypercorn integration with the existing FastAPI app.
Verifies lifecycle management and backward compatibility.
"""

import asyncio

import pytest

from trustchain.identity import Identity
from trustchain.blockstore import MemoryBlockStore
from trustchain.api import TrustChainNode


class TestHypercornLifecycle:
    async def test_http3_node_start_stop(self):
        """Verify TrustChainNode starts and stops cleanly with Hypercorn."""
        identity = Identity()
        store = MemoryBlockStore()
        node = TrustChainNode(
            identity, store, host="127.0.0.1", port=18300, use_http3=True
        )

        await node.start()
        # Give the server a moment to start
        await asyncio.sleep(0.3)

        # Server task should be running
        assert node._serve_task is not None
        assert not node._serve_task.done()

        await node.stop()
        # Task should complete
        await asyncio.sleep(0.1)

    async def test_http3_node_has_shutdown_event(self):
        """Hypercorn mode should use shutdown_event for clean shutdown."""
        identity = Identity()
        store = MemoryBlockStore()
        node = TrustChainNode(
            identity, store, host="127.0.0.1", port=18301, use_http3=True
        )

        await node.start()
        assert node._shutdown_event is not None

        await node.stop()

    async def test_uvicorn_node_no_shutdown_event(self):
        """Uvicorn mode should not create a shutdown event."""
        identity = Identity()
        store = MemoryBlockStore()
        node = TrustChainNode(
            identity, store, host="127.0.0.1", port=18302, use_http3=False
        )

        await node.start()
        assert node._shutdown_event is None

        await node.stop()


class TestHTTP3BackwardCompat:
    async def test_default_is_uvicorn(self):
        """Default TrustChainNode should use uvicorn (HTTP/1.1)."""
        identity = Identity()
        store = MemoryBlockStore()
        node = TrustChainNode(identity, store, host="127.0.0.1", port=18303)
        assert node.use_http3 is False

    async def test_http3_flag(self):
        """use_http3=True should be stored correctly."""
        identity = Identity()
        store = MemoryBlockStore()
        node = TrustChainNode(
            identity, store, host="127.0.0.1", port=18304, use_http3=True
        )
        assert node.use_http3 is True

    async def test_app_is_same_regardless_of_server(self):
        """The FastAPI app should be identical for both uvicorn and Hypercorn."""
        identity = Identity()
        store = MemoryBlockStore()

        node_uvicorn = TrustChainNode(
            identity, store, host="127.0.0.1", port=18305, use_http3=False
        )
        node_hypercorn = TrustChainNode(
            identity, store, host="127.0.0.1", port=18306, use_http3=True
        )

        # Both should have the same FastAPI app structure
        uvicorn_routes = {r.path for r in node_uvicorn.app.routes}
        hypercorn_routes = {r.path for r in node_hypercorn.app.routes}
        assert uvicorn_routes == hypercorn_routes


class TestHTTP3WithHTTPClient:
    """Test that Hypercorn-served endpoints can be reached via httpx.

    Note: httpx doesn't support HTTP/3, so these tests verify HTTP/2 fallback.
    The TLS cert is self-signed so we need verify=False.
    """

    async def test_https_status_endpoint(self):
        """Status endpoint should work over HTTPS (HTTP/2 fallback from HTTP/3)."""
        import httpx

        identity = Identity()
        store = MemoryBlockStore()
        node = TrustChainNode(
            identity, store, host="127.0.0.1", port=18307, use_http3=True
        )

        await node.start()
        await asyncio.sleep(0.5)

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(
                    "https://127.0.0.1:18307/trustchain/status",
                    timeout=5.0,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["public_key"] == identity.pubkey_hex
        finally:
            await node.stop()

    async def test_https_propose_endpoint(self):
        """Propose endpoint should work over HTTPS."""
        import httpx

        alice = Identity()
        bob = Identity()
        store_b = MemoryBlockStore()
        proto_a_store = MemoryBlockStore()

        from trustchain.protocol import TrustChainProtocol
        proto_a = TrustChainProtocol(alice, proto_a_store)

        node = TrustChainNode(
            bob, store_b, host="127.0.0.1", port=18308, use_http3=True
        )

        await node.start()
        await asyncio.sleep(0.5)

        try:
            proposal = proto_a.create_proposal(
                bob.pubkey_hex,
                {"interaction_type": "test", "outcome": "completed"},
            )

            from trustchain.api import HalfBlockModel
            model = HalfBlockModel.from_halfblock(proposal)

            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(
                    "https://127.0.0.1:18308/trustchain/propose",
                    json={"block": model.model_dump()},
                    timeout=5.0,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["accepted"] is True
                assert data["agreement"] is not None
        finally:
            await node.stop()
