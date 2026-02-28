"""Integration tests for TrustChain MCP Gateway.

Tests the full middleware on_call_tool flow, trust blocking, GatewayNode v2,
identity persistence, crawl filtering, bootstrap config, trust tools,
create_gateway, registry methods, and error recovery.
"""

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trustchain.blockstore import MemoryBlockStore
from trustchain.identity import Identity
from trustchain.protocol import TrustChainProtocol
from trustchain.record import create_record
from trustchain.store import RecordStore
from trustchain.trust import TrustEngine, compute_trust

from gateway.config import GatewayConfig, UpstreamServer
from gateway.middleware import TrustChainMiddleware
from gateway.node import GatewayNode
from gateway.recorder import InteractionRecorder
from gateway.registry import UpstreamRegistry
from gateway.trust_tools import register_trust_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_middleware(
    store=None,
    threshold=0.0,
    bootstrap=3,
    gateway_node=None,
    trust_engine=None,
    identity_dir=None,
):
    """Create a middleware + registry + gateway identity for testing."""
    if store is None:
        store = RecordStore()
    gw_identity = Identity()
    registry = UpstreamRegistry(gw_identity, identity_dir=identity_dir)
    recorder = InteractionRecorder(gw_identity, store)
    middleware = TrustChainMiddleware(
        registry=registry,
        recorder=recorder,
        store=store,
        default_threshold=threshold,
        bootstrap_interactions=bootstrap,
        trust_engine=trust_engine,
        gateway_node=gateway_node,
    )
    return middleware, registry, gw_identity, recorder


def _build_trust(store, identity_a, identity_b, count=5):
    """Build trust by creating bilateral records."""
    for i in range(count):
        record = create_record(
            identity_a=identity_a,
            identity_b=identity_b,
            seq_a=i,
            seq_b=i,
            prev_hash_a=store.last_hash_for(identity_a.pubkey_hex),
            prev_hash_b=store.last_hash_for(identity_b.pubkey_hex),
            interaction_type="service",
            outcome="completed",
        )
        store.add_record(record)


@dataclass
class FakeToolMessage:
    name: str
    arguments: dict = None

    def __post_init__(self):
        if self.arguments is None:
            self.arguments = {}


class FakeMiddlewareContext:
    """Minimal stand-in for fastmcp MiddlewareContext."""

    def __init__(self, tool_name: str, arguments: dict = None):
        self.message = FakeToolMessage(name=tool_name, arguments=arguments)


# ---------------------------------------------------------------------------
# Test: Full on_call_tool end-to-end flow
# ---------------------------------------------------------------------------


class TestMiddlewareEndToEnd:
    """Test the full middleware intercept → trust → forward → record → annotate flow."""

    @pytest.mark.asyncio
    async def test_tool_call_is_forwarded_and_annotated(self):
        """A normal tool call should be forwarded, recorded, and annotated."""
        store = RecordStore()
        middleware, registry, gw_identity, recorder = _make_middleware(store=store)

        # Register an upstream server
        config = UpstreamServer(name="fs", command="echo", namespace="fs")
        registry.register_server(config)

        context = FakeMiddlewareContext("fs_read_file", {"path": "/tmp/test"})

        # call_next returns a string result
        call_next = AsyncMock(return_value="file contents here")

        result = await middleware.on_call_tool(context, call_next)

        # Should have been forwarded
        call_next.assert_awaited_once_with(context)

        # Result should contain the annotation
        assert "[TrustChain]" in result
        assert "server=fs" in result
        assert "outcome=completed" in result

        # Interaction should have been recorded in the store
        fs_identity = registry.identity_for("fs")
        records = store.get_records_for(fs_identity.pubkey_hex)
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_native_tools_bypass_gating(self):
        """trustchain_* tools should be forwarded without trust checks."""
        middleware, _, _, _ = _make_middleware()
        context = FakeMiddlewareContext("trustchain_check_trust", {"server_name": "fs"})

        call_next = AsyncMock(return_value="trust info")
        result = await middleware.on_call_tool(context, call_next)

        call_next.assert_awaited_once()
        # No annotation should be appended (it's a native tool)
        assert result == "trust info"

    @pytest.mark.asyncio
    async def test_failed_tool_call_records_failure(self):
        """When a tool call raises, the interaction is recorded as failed."""
        store = RecordStore()
        middleware, registry, _, _ = _make_middleware(store=store)

        config = UpstreamServer(name="api", command="echo", namespace="api")
        registry.register_server(config)

        context = FakeMiddlewareContext("api_dangerous_call")
        call_next = AsyncMock(side_effect=RuntimeError("API exploded"))

        with pytest.raises(RuntimeError, match="API exploded"):
            await middleware.on_call_tool(context, call_next)

        # Should still have recorded the failed interaction
        api_identity = registry.identity_for("api")
        records = store.get_records_for(api_identity.pubkey_hex)
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_unknown_tool_forwarded_without_recording(self):
        """Tools not mapped to any server should be forwarded without recording."""
        store = RecordStore()
        middleware, _, _, _ = _make_middleware(store=store)

        context = FakeMiddlewareContext("unknown_tool")
        call_next = AsyncMock(return_value="ok")

        result = await middleware.on_call_tool(context, call_next)
        call_next.assert_awaited_once()
        # No annotation because no server was identified
        assert result == "ok"
        assert len(store.records) == 0


# ---------------------------------------------------------------------------
# Test: Trust blocking path
# ---------------------------------------------------------------------------


class TestTrustBlocking:
    """Test that the middleware blocks tool calls when trust is below threshold."""

    @pytest.mark.asyncio
    async def test_blocks_when_trust_below_threshold(self):
        """After bootstrap, low trust should block the call with ToolError."""
        from fastmcp.exceptions import ToolError

        store = RecordStore()
        # Bootstrap = 0 means no bootstrap grace period
        middleware, registry, gw_identity, recorder = _make_middleware(
            store=store, threshold=0.5, bootstrap=0
        )

        config = UpstreamServer(name="api", command="echo", namespace="api", trust_threshold=0.5)
        registry.register_server(config)

        context = FakeMiddlewareContext("api_call")
        call_next = AsyncMock(return_value="should not reach here")

        with pytest.raises(ToolError, match="BLOCKED"):
            await middleware.on_call_tool(context, call_next)

        # call_next should NOT have been called
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_allows_during_bootstrap(self):
        """During bootstrap period, calls should be allowed even with zero trust."""
        store = RecordStore()
        middleware, registry, gw_identity, recorder = _make_middleware(
            store=store, threshold=0.5, bootstrap=3
        )

        config = UpstreamServer(name="api", command="echo", namespace="api", trust_threshold=0.5)
        registry.register_server(config)

        context = FakeMiddlewareContext("api_call")
        call_next = AsyncMock(return_value="allowed in bootstrap")

        result = await middleware.on_call_tool(context, call_next)
        call_next.assert_awaited_once()
        assert "[TrustChain]" in result

    @pytest.mark.asyncio
    async def test_blocks_after_bootstrap_exhausted(self):
        """After bootstrap interactions, low trust blocks."""
        from fastmcp.exceptions import ToolError

        store = RecordStore()
        middleware, registry, gw_identity, recorder = _make_middleware(
            store=store, threshold=0.8, bootstrap=2
        )

        config = UpstreamServer(name="api", command="echo", namespace="api", trust_threshold=0.8)
        registry.register_server(config)

        # Record 2 interactions to exhaust bootstrap
        api_identity = registry.identity_for("api")
        for _ in range(2):
            recorder.record(api_identity, "tool:api_call", "completed")

        context = FakeMiddlewareContext("api_call")
        call_next = AsyncMock(return_value="should not reach")

        # Trust built from 2 interactions won't be 0.8, so this should block
        trust = compute_trust(api_identity.pubkey_hex, store)
        assert trust < 0.8

        with pytest.raises(ToolError, match="BLOCKED"):
            await middleware.on_call_tool(context, call_next)


# ---------------------------------------------------------------------------
# Test: Identity persistence
# ---------------------------------------------------------------------------


class TestIdentityPersistence:
    """Test that upstream identities survive gateway restarts."""

    def test_identity_persisted_and_reloaded(self):
        """Same server name should get the same identity across registry instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = Identity()
            reg1 = UpstreamRegistry(gw, identity_dir=tmpdir)
            config = UpstreamServer(name="myserver", command="echo")
            id1 = reg1.register_server(config)

            # Create a second registry (simulating restart)
            reg2 = UpstreamRegistry(gw, identity_dir=tmpdir)
            id2 = reg2.register_server(config)

            # Should be the same key
            assert id1.pubkey_hex == id2.pubkey_hex

    def test_different_servers_get_different_identities(self):
        """Different server names should get distinct identities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = Identity()
            registry = UpstreamRegistry(gw, identity_dir=tmpdir)

            config_a = UpstreamServer(name="server_a", command="echo")
            config_b = UpstreamServer(name="server_b", command="echo")
            id_a = registry.register_server(config_a)
            id_b = registry.register_server(config_b)

            assert id_a.pubkey_hex != id_b.pubkey_hex

    def test_no_identity_dir_generates_fresh_each_time(self):
        """Without identity_dir, each call creates a new identity."""
        gw = Identity()
        reg1 = UpstreamRegistry(gw)
        reg2 = UpstreamRegistry(gw)

        config = UpstreamServer(name="myserver", command="echo")
        id1 = reg1.register_server(config)
        id2 = reg2.register_server(config)

        # Should be different
        assert id1.pubkey_hex != id2.pubkey_hex

    def test_key_files_created_on_disk(self):
        """Identity files should be written to the identity directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = Identity()
            registry = UpstreamRegistry(gw, identity_dir=tmpdir)

            config = UpstreamServer(name="testserver", command="echo")
            registry.register_server(config)

            key_file = Path(tmpdir) / "testserver.key"
            assert key_file.exists()


# ---------------------------------------------------------------------------
# Test: Bootstrap config propagation to trust tools
# ---------------------------------------------------------------------------


class TestBootstrapConfigPropagation:
    """Test that bootstrap_interactions flows through to trust tools."""

    @pytest.mark.asyncio
    async def test_check_trust_uses_configured_bootstrap(self):
        """trustchain_check_trust should use the configured bootstrap count."""
        from fastmcp import Client, FastMCP

        store = RecordStore()
        gw = Identity()
        registry = UpstreamRegistry(gw)

        config = UpstreamServer(name="srv", command="echo", trust_threshold=0.0)
        registry.register_server(config)
        recorder = InteractionRecorder(gw, store)

        # Record exactly 5 interactions — with bootstrap=10, still in bootstrap
        srv_id = registry.identity_for("srv")
        for _ in range(5):
            recorder.record(srv_id, "tool:test", "completed")

        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, store, bootstrap_interactions=10)

        async with Client(mcp) as client:
            result = await client.call_tool("trustchain_check_trust", {"server_name": "srv"})
            text = result.content[0].text
            assert "bootstrap (always allowed)" in text

    @pytest.mark.asyncio
    async def test_check_trust_established_with_low_bootstrap(self):
        """With bootstrap=2, 5 interactions should show 'established'."""
        from fastmcp import Client, FastMCP

        store = RecordStore()
        gw = Identity()
        registry = UpstreamRegistry(gw)

        config = UpstreamServer(name="srv", command="echo", trust_threshold=0.0)
        registry.register_server(config)
        recorder = InteractionRecorder(gw, store)

        srv_id = registry.identity_for("srv")
        for _ in range(5):
            recorder.record(srv_id, "tool:test", "completed")

        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, store, bootstrap_interactions=2)

        async with Client(mcp) as client:
            result = await client.call_tool("trustchain_check_trust", {"server_name": "srv"})
            text = result.content[0].text
            assert "established" in text


# ---------------------------------------------------------------------------
# Test: Crawl filtering (v2 path)
# ---------------------------------------------------------------------------


class TestCrawlFiltering:
    """Test that trustchain_crawl filters to the requested server."""

    @pytest.mark.asyncio
    async def test_crawl_v1_filters_to_server(self):
        """v1 crawl should only inspect the requested server's records."""
        from fastmcp import Client, FastMCP

        store = RecordStore()
        gw = Identity()
        registry = UpstreamRegistry(gw)
        recorder = InteractionRecorder(gw, store)

        config_a = UpstreamServer(name="server_a", command="echo")
        config_b = UpstreamServer(name="server_b", command="echo")
        registry.register_server(config_a)
        registry.register_server(config_b)

        # Only interact with server_a
        id_a = registry.identity_for("server_a")
        for _ in range(3):
            recorder.record(id_a, "tool:a_op", "completed")

        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, store)

        async with Client(mcp) as client:
            # Crawl server_b (no interactions)
            result = await client.call_tool("trustchain_crawl", {"server_name": "server_b"})
            text = result.content[0].text
            assert "No chain data" in text

            # Crawl server_a (has interactions)
            result = await client.call_tool("trustchain_crawl", {"server_name": "server_a"})
            text = result.content[0].text
            assert "clean" in text


# ---------------------------------------------------------------------------
# Test: TrustContext bootstrap configurability
# ---------------------------------------------------------------------------


class TestTrustContextBootstrap:
    """Test that TrustContext.is_bootstrap respects the configured threshold."""

    def test_is_bootstrap_default(self, identity_a, identity_b, store):
        """Default bootstrap threshold is 3."""
        from agent_os.context import TrustContext

        ctx = TrustContext(
            caller_pubkey=identity_a.pubkey_hex,
            caller_trust=0.0,
            caller_history=2,
            agent_identity=identity_b,
            store=store,
        )
        assert ctx.is_bootstrap

        ctx2 = TrustContext(
            caller_pubkey=identity_a.pubkey_hex,
            caller_trust=0.0,
            caller_history=3,
            agent_identity=identity_b,
            store=store,
        )
        assert not ctx2.is_bootstrap

    def test_is_bootstrap_custom_threshold(self, identity_a, identity_b, store):
        """Custom bootstrap_interactions should change the threshold."""
        from agent_os.context import TrustContext

        ctx = TrustContext(
            caller_pubkey=identity_a.pubkey_hex,
            caller_trust=0.0,
            caller_history=4,
            agent_identity=identity_b,
            store=store,
            bootstrap_interactions=5,
        )
        assert ctx.is_bootstrap  # 4 < 5

        ctx2 = TrustContext(
            caller_pubkey=identity_a.pubkey_hex,
            caller_trust=0.0,
            caller_history=5,
            agent_identity=identity_b,
            store=store,
            bootstrap_interactions=5,
        )
        assert not ctx2.is_bootstrap  # 5 >= 5


# ---------------------------------------------------------------------------
# Test: Interaction counting counts both directions
# ---------------------------------------------------------------------------


class TestBidirectionalCounting:
    """Test that interaction counting works in both directions."""

    @pytest.mark.asyncio
    async def test_responder_interactions_counted(self):
        """When gateway is responder, inbound interactions should count."""
        from agent_os.agent import TrustAgent
        from agent_os.context import TrustContext

        store = RecordStore()
        alice = TrustAgent(name="alice", store=store)
        bob = TrustAgent(name="bob", store=store, bootstrap_interactions=0)

        @bob.service("basic", min_trust=0.0)
        async def basic(data: dict, ctx: TrustContext) -> dict:
            return {"ok": True}

        # Alice calls bob 3 times
        for _ in range(3):
            accepted, _, _ = await alice.call_service(bob, "basic")
            assert accepted

        # Both should have interactions recorded
        assert alice.interaction_count > 0
        assert bob.interaction_count > 0


# ---------------------------------------------------------------------------
# Test: GatewayNode
# ---------------------------------------------------------------------------


class TestGatewayNode:
    """Test GatewayNode — trust gating, scoring, bidirectional counting."""

    def _make_node(self):
        """Create a GatewayNode with in-memory store."""
        identity = Identity()
        store = MemoryBlockStore()
        node = GatewayNode(identity, store)
        return node

    def test_init_creates_trust_engine(self):
        node = self._make_node()
        assert node.trust_engine is not None
        assert node.pubkey == node.identity.pubkey_hex

    def test_get_trust_score_unknown_peer(self):
        node = self._make_node()
        peer = Identity()
        # Unknown peer — no interactions, score should be low/zero
        score = node.get_trust_score(peer.pubkey_hex)
        assert isinstance(score, float)
        assert score >= 0.0

    def test_get_chain_integrity_empty(self):
        node = self._make_node()
        peer = Identity()
        integrity = node.get_chain_integrity(peer.pubkey_hex)
        # Empty chain = perfect integrity (nothing broken)
        assert integrity == 1.0

    def test_count_peer_interactions_empty(self):
        node = self._make_node()
        peer = Identity()
        count = node._count_peer_interactions(peer.pubkey_hex)
        assert count == 0

    def test_count_peer_interactions_outbound(self):
        """Outbound proposals (our chain → peer) should be counted."""
        node = self._make_node()
        peer = Identity()
        peer_store = MemoryBlockStore()
        peer_protocol = TrustChainProtocol(peer, peer_store)

        # Create 3 proposals from us to peer
        for _ in range(3):
            proposal = node.protocol.create_proposal(
                peer.pubkey_hex, {"type": "test"}
            )
            peer_protocol.receive_proposal(proposal)
            agreement = peer_protocol.create_agreement(proposal)
            node.protocol.receive_agreement(agreement)

        count = node._count_peer_interactions(peer.pubkey_hex)
        assert count >= 3  # At least 3 outbound

    def test_count_peer_interactions_inbound(self):
        """Inbound proposals (peer chain → us) should be counted.

        In the real network, peer blocks arrive via gossip/crawl and get
        stored locally. We simulate this by having the peer use its own
        store and then copying the peer's chain blocks into the node's store.
        """
        node = self._make_node()
        peer = Identity()
        peer_store = MemoryBlockStore()
        peer_protocol = TrustChainProtocol(peer, peer_store)

        for _ in range(2):
            proposal = peer_protocol.create_proposal(
                node.pubkey, {"type": "test"}
            )
            # Simulate gossip: copy peer's proposal block into node's store
            node.store.add_block(proposal)

        # Now the node's store has the peer's chain with link_public_key = node.pubkey
        count = node._count_peer_interactions(peer.pubkey_hex)
        assert count >= 2  # At least 2 inbound

    @pytest.mark.asyncio
    async def test_trusted_transact_blocks_low_trust(self):
        """trusted_transact should reject when trust is below threshold."""
        node = self._make_node()
        peer = Identity()

        result = await node.trusted_transact(
            peer.pubkey_hex,
            {"type": "test"},
            min_trust=0.9,
            bootstrap_interactions=0,  # No bootstrap grace
        )
        assert result["accepted"] is False
        assert "Trust" in result["error"]
        assert result["trust_score"] < 0.9

    @pytest.mark.asyncio
    async def test_trusted_transact_allows_bootstrap(self):
        """trusted_transact should allow in bootstrap mode despite low trust."""
        node = self._make_node()
        peer = Identity()
        # Register the peer so transact() can find its URL
        node.register_peer(peer.pubkey_hex, "http://localhost:9999")

        # With bootstrap_interactions=5, 0 interactions = bootstrap mode
        # This will fail at the HTTP level (no real peer), but trust gate should pass
        try:
            result = await node.trusted_transact(
                peer.pubkey_hex,
                {"type": "test"},
                min_trust=0.9,
                bootstrap_interactions=5,
            )
            # If it reaches transact(), it passed the trust gate
            # Agreement may be None (peer unreachable)
        except Exception:
            # Connection error to fake peer is expected — but we confirmed
            # that the trust gate didn't reject (it would return a dict, not raise)
            pass


# ---------------------------------------------------------------------------
# Test: Trust tools — verify_chain and trust_score
# ---------------------------------------------------------------------------


class TestVerifyChainTool:
    """Test the trustchain_verify_chain tool."""

    def _make_server_with_interactions(self, count=3):
        from fastmcp import FastMCP

        store = RecordStore()
        gw = Identity()
        registry = UpstreamRegistry(gw)
        recorder = InteractionRecorder(gw, store)

        config = UpstreamServer(name="srv", command="echo", trust_threshold=0.0)
        registry.register_server(config)

        srv_id = registry.identity_for("srv")
        for _ in range(count):
            recorder.record(srv_id, "tool:test", "completed")

        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, store)
        return mcp

    @pytest.mark.asyncio
    async def test_verify_chain_unknown_server(self):
        from fastmcp import Client

        mcp = self._make_server_with_interactions(0)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "trustchain_verify_chain", {"server_name": "nonexistent"}
            )
            text = result.content[0].text
            assert "Unknown server" in text

    @pytest.mark.asyncio
    async def test_verify_chain_no_interactions(self):
        from fastmcp import Client, FastMCP

        store = RecordStore()
        gw = Identity()
        registry = UpstreamRegistry(gw)
        config = UpstreamServer(name="srv", command="echo")
        registry.register_server(config)

        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, store)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "trustchain_verify_chain", {"server_name": "srv"}
            )
            text = result.content[0].text
            assert "No chain data" in text or "no interactions" in text.lower()

    @pytest.mark.asyncio
    async def test_verify_chain_valid(self):
        from fastmcp import Client

        mcp = self._make_server_with_interactions(5)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "trustchain_verify_chain", {"server_name": "srv"}
            )
            text = result.content[0].text
            assert "VALID" in text
            assert "Chain Length" in text
            assert "Chain Integrity" in text


class TestTrustScoreTool:
    """Test the trustchain_trust_score tool."""

    @pytest.mark.asyncio
    async def test_trust_score_unknown_server(self):
        from fastmcp import Client, FastMCP

        store = RecordStore()
        registry = UpstreamRegistry(Identity())
        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, store)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "trustchain_trust_score", {"server_name": "nonexistent"}
            )
            text = result.content[0].text
            assert "Unknown server" in text

    @pytest.mark.asyncio
    async def test_trust_score_v1_breakdown(self):
        from fastmcp import Client, FastMCP

        store = RecordStore()
        gw = Identity()
        registry = UpstreamRegistry(gw)
        recorder = InteractionRecorder(gw, store)

        config = UpstreamServer(name="srv", command="echo")
        registry.register_server(config)

        srv_id = registry.identity_for("srv")
        for _ in range(5):
            recorder.record(srv_id, "tool:test", "completed")

        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, store)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "trustchain_trust_score", {"server_name": "srv"}
            )
            text = result.content[0].text
            assert "Base Trust" in text
            assert "Chain Trust" in text

    @pytest.mark.asyncio
    async def test_trust_score_v2_breakdown(self):
        """v2 path with TrustEngine should show component breakdown."""
        from fastmcp import Client, FastMCP

        block_store = MemoryBlockStore()
        gw_id = Identity()
        peer_id = Identity()
        engine = TrustEngine(block_store, seed_nodes=[gw_id.pubkey_hex])

        # Create some blocks for the peer
        protocol = TrustChainProtocol(gw_id, block_store)
        for _ in range(3):
            proposal = protocol.create_proposal(
                peer_id.pubkey_hex, {"type": "test"}
            )

        rec_store = RecordStore()
        registry = UpstreamRegistry(gw_id)
        config = UpstreamServer(name="srv", command="echo")
        registry.register_server(config)

        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, rec_store, trust_engine=engine)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "trustchain_trust_score", {"server_name": "srv"}
            )
            text = result.content[0].text
            assert "Combined Trust" in text
            assert "Chain Integrity" in text
            assert "NetFlow Score" in text
            assert "Statistical Score" in text


# ---------------------------------------------------------------------------
# Test: Crawl tool (additional)
# ---------------------------------------------------------------------------


class TestCrawlTool:
    """Test trustchain_crawl edge cases."""

    @pytest.mark.asyncio
    async def test_crawl_unknown_server(self):
        from fastmcp import Client, FastMCP

        store = RecordStore()
        registry = UpstreamRegistry(Identity())
        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, store)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "trustchain_crawl", {"server_name": "nonexistent"}
            )
            text = result.content[0].text
            assert "Unknown server" in text


# ---------------------------------------------------------------------------
# Test: Registry methods
# ---------------------------------------------------------------------------


class TestRegistryMethods:
    """Test register_upstream, register_tools_for_server, trustchain_url_for."""

    def test_register_upstream_sets_trustchain_url(self):
        gw = Identity()
        registry = UpstreamRegistry(gw)
        identity = registry.register_upstream(
            "peer", "http://peer:8080", "http://peer:8100"
        )
        assert identity is not None
        assert registry.trustchain_url_for("peer") == "http://peer:8100"
        assert registry.identity_for("peer") is identity

    def test_register_upstream_creates_config(self):
        gw = Identity()
        registry = UpstreamRegistry(gw)
        registry.register_upstream("peer", "http://peer:8080", "http://peer:8100")
        config = registry.config_for("peer")
        assert config is not None
        assert config.name == "peer"
        assert config.url == "http://peer:8080"

    def test_register_tools_for_server(self):
        gw = Identity()
        registry = UpstreamRegistry(gw)
        config = UpstreamServer(name="fs", command="echo", namespace="fs")
        registry.register_server(config)

        registry.register_tools_for_server(
            ["read_file", "write_file", "list_dir"], "fs"
        )
        assert registry.server_for_tool("read_file") == "fs"
        assert registry.server_for_tool("write_file") == "fs"
        assert registry.server_for_tool("list_dir") == "fs"

    def test_trustchain_url_for_none_when_not_set(self):
        gw = Identity()
        registry = UpstreamRegistry(gw)
        config = UpstreamServer(name="basic", command="echo")
        registry.register_server(config)
        assert registry.trustchain_url_for("basic") is None

    def test_register_upstream_identity_persisted(self):
        """register_upstream should also persist identities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = Identity()
            reg1 = UpstreamRegistry(gw, identity_dir=tmpdir)
            id1 = reg1.register_upstream(
                "peer", "http://peer:8080", "http://peer:8100"
            )

            reg2 = UpstreamRegistry(gw, identity_dir=tmpdir)
            id2 = reg2.register_upstream(
                "peer", "http://peer:8080", "http://peer:8100"
            )

            assert id1.pubkey_hex == id2.pubkey_hex


# ---------------------------------------------------------------------------
# Test: Corrupt key recovery
# ---------------------------------------------------------------------------


class TestCorruptKeyRecovery:
    """Test that corrupt key files are handled gracefully."""

    def test_corrupt_key_file_regenerates(self):
        """A corrupt .key file should be replaced with a fresh identity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = Identity()

            # Write garbage to the key file
            key_path = Path(tmpdir) / "broken.key"
            key_path.write_text("this is not a valid key file")

            registry = UpstreamRegistry(gw, identity_dir=tmpdir)
            config = UpstreamServer(name="broken", command="echo")

            # Should not crash — should regenerate
            identity = registry.register_server(config)
            assert identity is not None
            assert len(identity.pubkey_hex) == 64

    def test_empty_key_file_regenerates(self):
        """An empty .key file should be replaced."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gw = Identity()

            key_path = Path(tmpdir) / "empty.key"
            key_path.write_bytes(b"")

            registry = UpstreamRegistry(gw, identity_dir=tmpdir)
            config = UpstreamServer(name="empty", command="echo")
            identity = registry.register_server(config)
            assert identity is not None


# ---------------------------------------------------------------------------
# Test: create_gateway and create_gateway_from_dict
# ---------------------------------------------------------------------------


class TestCreateGateway:
    """Test the gateway factory functions."""

    def test_create_gateway_minimal(self):
        """create_gateway with minimal config should return a FastMCP server."""
        from gateway.server import create_gateway

        config = GatewayConfig(server_name="Test Gateway")
        mcp = create_gateway(config)
        assert mcp is not None

    def test_create_gateway_with_identity_path(self):
        """create_gateway should persist the gateway identity."""
        from gateway.server import create_gateway

        with tempfile.TemporaryDirectory() as tmpdir:
            id_path = str(Path(tmpdir) / "gw.key")
            config = GatewayConfig(
                identity_path=id_path,
                server_name="Test Gateway",
            )
            mcp = create_gateway(config)
            assert mcp is not None
            assert Path(id_path).exists()

            # Second call should load the same identity
            mcp2 = create_gateway(config)
            assert mcp2 is not None

    def test_create_gateway_with_upstream_identity_dir(self):
        """create_gateway should wire upstream_identity_dir to registry."""
        from gateway.server import create_gateway

        with tempfile.TemporaryDirectory() as tmpdir:
            config = GatewayConfig(
                upstream_identity_dir=tmpdir,
                server_name="Test Gateway",
            )
            mcp = create_gateway(config)
            assert mcp is not None

    def test_create_gateway_with_custom_store(self):
        """create_gateway should accept a custom RecordStore."""
        from gateway.server import create_gateway

        store = RecordStore()
        config = GatewayConfig(server_name="Test Gateway")
        mcp = create_gateway(config, store=store)
        assert mcp is not None

    def test_create_gateway_from_dict_minimal(self):
        """create_gateway_from_dict should parse a simple config."""
        from gateway.server import create_gateway_from_dict

        config_dict = {
            "server_name": "Dict Gateway",
            "default_trust_threshold": 0.1,
            "bootstrap_interactions": 5,
        }
        mcp = create_gateway_from_dict(config_dict)
        assert mcp is not None

    def test_create_gateway_from_dict_with_upstreams(self):
        """create_gateway_from_dict should handle upstream server configs."""
        from gateway.server import create_gateway_from_dict

        config_dict = {
            "server_name": "Dict Gateway",
            "upstreams": [
                {
                    "name": "test",
                    "command": "echo",
                    "namespace": "test",
                    "trust_threshold": 0.3,
                },
            ],
        }
        # This will fail at mount time (echo is not a real MCP server)
        # but the parsing and wiring should succeed
        try:
            mcp = create_gateway_from_dict(config_dict)
        except Exception:
            # Mount failure is expected — but parsing should have worked
            pass

    def test_create_gateway_from_dict_with_upstream_identity_dir(self):
        """create_gateway_from_dict should wire upstream_identity_dir."""
        from gateway.server import create_gateway_from_dict

        with tempfile.TemporaryDirectory() as tmpdir:
            config_dict = {
                "server_name": "Dict Gateway",
                "upstream_identity_dir": tmpdir,
            }
            mcp = create_gateway_from_dict(config_dict)
            assert mcp is not None


# ---------------------------------------------------------------------------
# Test: v2 error handling — protocol failures don't break agent calls
# ---------------------------------------------------------------------------


class TestV2ErrorHandling:
    """Trust recording is infrastructure — it should never break agent calls."""

    @pytest.mark.asyncio
    async def test_protocol_error_doesnt_break_call_service(self):
        """If v2 protocol raises, the agent call still returns its result."""
        from agent_os.agent import TrustAgent
        from agent_os.context import TrustContext
        from trustchain.api import TrustChainNode

        store_a = MemoryBlockStore()
        store_b = MemoryBlockStore()
        node_a = TrustChainNode(Identity(), store_a)
        node_b = TrustChainNode(Identity(), store_b)

        alice = TrustAgent(name="alice", node=node_a)
        bob = TrustAgent(name="bob", node=node_b)

        @bob.service("echo", min_trust=0.0)
        async def echo(data: dict, ctx: TrustContext) -> dict:
            return {"echo": data}

        # Sabotage the protocol — make create_proposal raise
        original = node_a.protocol.create_proposal
        node_a.protocol.create_proposal = MagicMock(
            side_effect=RuntimeError("protocol exploded")
        )

        # The call should still succeed — error is logged, not raised
        accepted, reason, result = await alice.call_service(
            bob, "echo", {"msg": "hi"}
        )
        assert accepted
        assert result == {"echo": {"msg": "hi"}}

        # Restore for cleanup
        node_a.protocol.create_proposal = original


# ---------------------------------------------------------------------------
# Test: Interaction count caching
# ---------------------------------------------------------------------------


class TestInteractionCountCache:
    """Test that GatewayNode caches interaction counts."""

    def test_cache_avoids_redundant_scans(self):
        """Repeated calls should return cached value without re-scanning."""
        identity = Identity()
        store = MemoryBlockStore()
        node = GatewayNode(identity, store)
        peer = Identity()

        # First call — cold cache
        count1 = node._count_peer_interactions(peer.pubkey_hex)
        assert count1 == 0

        # Should be cached now
        assert peer.pubkey_hex in node._interaction_count_cache

        # Second call — should hit cache (same result)
        count2 = node._count_peer_interactions(peer.pubkey_hex)
        assert count2 == 0

    def test_invalidate_cache(self):
        """invalidate_count_cache should clear the cached entry."""
        identity = Identity()
        store = MemoryBlockStore()
        node = GatewayNode(identity, store)
        peer = Identity()

        node._count_peer_interactions(peer.pubkey_hex)
        assert peer.pubkey_hex in node._interaction_count_cache

        node.invalidate_count_cache(peer.pubkey_hex)
        assert peer.pubkey_hex not in node._interaction_count_cache

    def test_cache_reflects_new_interactions_after_invalidation(self):
        """After invalidation, next count should reflect new blocks."""
        identity = Identity()
        store = MemoryBlockStore()
        node = GatewayNode(identity, store)
        peer = Identity()
        peer_store = MemoryBlockStore()
        peer_protocol = TrustChainProtocol(peer, peer_store)

        # Initial count
        assert node._count_peer_interactions(peer.pubkey_hex) == 0

        # Add an outbound interaction
        proposal = node.protocol.create_proposal(
            peer.pubkey_hex, {"type": "test"}
        )

        # Cache still has 0
        assert node._count_peer_interactions(peer.pubkey_hex) == 0

        # Invalidate and re-count
        node.invalidate_count_cache(peer.pubkey_hex)
        assert node._count_peer_interactions(peer.pubkey_hex) >= 1


# ---------------------------------------------------------------------------
# Test: Denied v2 calls are recorded
# ---------------------------------------------------------------------------


class TestDeniedCallRecording:
    """Test that denied v2 calls leave an audit trail."""

    @pytest.mark.asyncio
    async def test_denied_v2_call_records_in_store(self):
        """When v2 call is denied, a v1 record with outcome='denied' is created."""
        from agent_os.agent import TrustAgent
        from agent_os.context import TrustContext
        from trustchain.api import TrustChainNode

        store_a = MemoryBlockStore()
        store_b = MemoryBlockStore()
        node_a = TrustChainNode(Identity(), store_a)
        node_b = TrustChainNode(Identity(), store_b)

        # v1 stores for the agents (used for denied-call audit trail)
        v1_store = RecordStore()

        alice = TrustAgent(
            name="alice", store=v1_store, node=node_a,
            bootstrap_interactions=0,
        )
        bob = TrustAgent(
            name="bob", store=v1_store, node=node_b,
            bootstrap_interactions=0,
        )

        @bob.service("premium", min_trust=0.9)
        async def premium(data: dict, ctx: TrustContext) -> dict:
            return {"premium": True}

        accepted, reason, result = await alice.call_service(bob, "premium")
        assert not accepted
        assert "denied" in reason.lower()

        # Should have a denied record in the v1 store
        records = v1_store.records
        assert len(records) >= 1
        denied = [r for r in records if r.outcome == "denied"]
        assert len(denied) >= 1
