"""Integration tests for the TrustChain MCP Gateway."""

import pytest

from trustchain.identity import Identity
from trustchain.store import RecordStore
from trustchain.trust import compute_trust

from gateway.config import GatewayConfig, UpstreamServer
from gateway.recorder import InteractionRecorder
from gateway.registry import UpstreamRegistry
from gateway.trust_tools import register_trust_tools


class TestGatewayIntegration:
    """Test the gateway components wired together."""

    def setup_method(self):
        self.store = RecordStore()
        self.gw_identity = Identity()
        self.registry = UpstreamRegistry(self.gw_identity)
        self.recorder = InteractionRecorder(self.gw_identity, self.store)

        # Register two upstream servers
        self.fs_config = UpstreamServer(
            name="filesystem", command="echo", namespace="fs", trust_threshold=0.0
        )
        self.api_config = UpstreamServer(
            name="api", command="echo", namespace="api", trust_threshold=0.3
        )
        self.registry.register_server(self.fs_config)
        self.registry.register_server(self.api_config)

    def test_recorder_builds_trust_for_server(self):
        """Trust increases as interactions are recorded."""
        fs_identity = self.registry.identity_for("filesystem")
        assert compute_trust(fs_identity.pubkey_hex, self.store) == 0.0

        for _ in range(5):
            self.recorder.record(fs_identity, "tool:fs_read", "completed")

        trust = compute_trust(fs_identity.pubkey_hex, self.store)
        assert trust > 0.0

    def test_pair_history_tracks_interactions(self):
        fs_identity = self.registry.identity_for("filesystem")
        for _ in range(3):
            self.recorder.record(fs_identity, "tool:fs_read", "completed")

        history = self.store.get_pair_history(
            self.gw_identity.pubkey_hex,
            fs_identity.pubkey_hex,
        )
        assert len(history) == 3

    def test_different_servers_get_independent_trust(self):
        fs_identity = self.registry.identity_for("filesystem")
        api_identity = self.registry.identity_for("api")

        # Only interact with filesystem
        for _ in range(5):
            self.recorder.record(fs_identity, "tool:fs_read", "completed")

        assert compute_trust(fs_identity.pubkey_hex, self.store) > 0.0
        assert compute_trust(api_identity.pubkey_hex, self.store) == 0.0

    def test_failed_interactions_affect_trust(self):
        fs_identity = self.registry.identity_for("filesystem")

        # Mix of completed and failed
        for _ in range(3):
            self.recorder.record(fs_identity, "tool:fs_read", "completed")
        for _ in range(3):
            self.recorder.record(fs_identity, "tool:fs_write", "failed")

        trust = compute_trust(fs_identity.pubkey_hex, self.store)
        # Trust should be lower than all-completed
        assert trust > 0.0
        assert trust < 1.0


class TestTrustTools:
    """Test the native trust query tools via FastMCP Client."""

    def setup_method(self):
        self.store = RecordStore()
        self.gw_identity = Identity()
        self.registry = UpstreamRegistry(self.gw_identity)
        self.recorder = InteractionRecorder(self.gw_identity, self.store)

        self.config = UpstreamServer(
            name="test_server", command="echo", trust_threshold=0.2
        )
        self.registry.register_server(self.config)

    def _make_server(self):
        from fastmcp import FastMCP
        mcp = FastMCP("test")
        register_trust_tools(mcp, self.registry, self.store)
        return mcp

    @pytest.mark.asyncio
    async def test_check_trust_unknown_server(self):
        from fastmcp import Client
        mcp = self._make_server()
        async with Client(mcp) as client:
            result = await client.call_tool("trustchain_check_trust", {"server_name": "nonexistent"})
            text = result.content[0].text
            assert "Unknown server" in text

    @pytest.mark.asyncio
    async def test_check_trust_existing_server(self):
        from fastmcp import Client
        mcp = self._make_server()
        async with Client(mcp) as client:
            result = await client.call_tool("trustchain_check_trust", {"server_name": "test_server"})
            text = result.content[0].text
            assert "test_server" in text
            assert "Trust Score: 0.000" in text
            assert "bootstrap" in text

    @pytest.mark.asyncio
    async def test_list_servers(self):
        from fastmcp import Client
        mcp = self._make_server()
        async with Client(mcp) as client:
            result = await client.call_tool("trustchain_list_servers", {})
            text = result.content[0].text
            assert "test_server" in text

    @pytest.mark.asyncio
    async def test_get_history_empty(self):
        from fastmcp import Client
        mcp = self._make_server()
        async with Client(mcp) as client:
            result = await client.call_tool("trustchain_get_history", {"server_name": "test_server", "limit": 10})
            text = result.content[0].text
            assert "No interaction history" in text

    @pytest.mark.asyncio
    async def test_get_history_with_records(self):
        from fastmcp import Client
        mcp = self._make_server()

        # Add some interactions
        identity = self.registry.identity_for("test_server")
        for _ in range(3):
            self.recorder.record(identity, "tool:test", "completed")

        async with Client(mcp) as client:
            result = await client.call_tool("trustchain_get_history", {"server_name": "test_server", "limit": 10})
            text = result.content[0].text
            assert "showing 3/3" in text
            assert "tool:test" in text


class TestGatewayConfig:
    def test_upstream_server_defaults(self):
        server = UpstreamServer(name="test", command="echo")
        assert server.namespace == "test"
        assert server.trust_threshold == 0.0
        assert server.args == []
        assert server.env == {}

    def test_upstream_server_custom_namespace(self):
        server = UpstreamServer(name="filesystem", command="echo", namespace="fs")
        assert server.namespace == "fs"

    def test_gateway_config_defaults(self):
        config = GatewayConfig()
        assert config.upstreams == []
        assert config.default_trust_threshold == 0.0
        assert config.bootstrap_interactions == 3
        assert config.server_name == "TrustChain Gateway"
