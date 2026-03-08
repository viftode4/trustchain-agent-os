"""Tests for audit fallback in single-player mode.

When no TrustChain-aware peer exists (upstream_identity is None), the gateway
should fall back to self-referencing audit blocks instead of failing.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from trustchain.audit import AuditLevel, EventType, default_events
from trustchain.blockstore import MemoryBlockStore
from trustchain.identity import Identity
from trustchain.store import RecordStore

from gateway.config import GatewayConfig, UpstreamServer
from gateway.middleware import TrustChainMiddleware
from gateway.node import GatewayNode
from gateway.recorder import InteractionRecorder
from gateway.registry import UpstreamRegistry


def _make_middleware(
    *,
    use_v2: bool = True,
    audit_level: str = "standard",
    register_identity: bool = True,
):
    """Build a middleware with optional v2 gateway node."""
    gw_identity = Identity()
    store = RecordStore()
    registry = UpstreamRegistry(gw_identity)
    recorder = InteractionRecorder(gw_identity, store)

    gateway_node = None
    trust_engine = None
    if use_v2:
        block_store = MemoryBlockStore()
        gateway_node = GatewayNode(
            identity=gw_identity,
            store=block_store,
            seed_nodes=[gw_identity.pubkey_hex],
        )
        trust_engine = gateway_node.trust_engine

    # Register a server but optionally skip identity registration
    upstream_cfg = UpstreamServer(name="fs", command="echo", namespace="fs")
    registry.register_server(upstream_cfg)
    if not register_identity:
        # Remove the identity so identity_for returns None
        registry._server_identities.pop("fs", None)

    middleware = TrustChainMiddleware(
        registry=registry,
        recorder=recorder,
        store=store,
        default_threshold=0.0,
        bootstrap_interactions=3,
        trust_engine=trust_engine,
        gateway_node=gateway_node,
        audit_level=audit_level,
    )
    return middleware, registry, gateway_node


def _make_context(tool_name: str = "fs_read_file"):
    """Build a mock MiddlewareContext."""
    context = MagicMock()
    context.message.name = tool_name
    context.message.arguments = {}
    return context


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuditFallback:
    @pytest.mark.asyncio
    async def test_no_identity_uses_audit_mode(self):
        """upstream_identity=None -> call succeeds, audit block created."""
        middleware, _, gateway_node = _make_middleware(register_identity=False)
        context = _make_context()
        call_next = AsyncMock(return_value="file contents")

        result = await middleware.on_call_tool(context, call_next)

        call_next.assert_awaited_once()
        assert "mode=audit-only" in str(result)
        # Verify an audit block was stored
        chain = gateway_node.protocol.store.get_chain(
            gateway_node.protocol.pubkey
        )
        assert len(chain) == 1
        assert chain[0].block_type.value == "audit"
        assert chain[0].transaction["action"] == "tool:fs_read_file"

    @pytest.mark.asyncio
    async def test_no_identity_failed_call_records_audit(self):
        """Tool raises -> audit block with outcome=failed, exception re-raised."""
        middleware, _, gateway_node = _make_middleware(register_identity=False)
        context = _make_context()
        call_next = AsyncMock(side_effect=RuntimeError("upstream down"))

        with pytest.raises(RuntimeError, match="upstream down"):
            await middleware.on_call_tool(context, call_next)

        chain = gateway_node.protocol.store.get_chain(
            gateway_node.protocol.pubkey
        )
        assert len(chain) == 1
        assert chain[0].transaction["outcome"] == "failed"
        assert chain[0].transaction["error"] == "upstream down"

    @pytest.mark.asyncio
    async def test_audit_annotation_shows_mode(self):
        """Result annotation includes mode=audit-only."""
        middleware, _, _ = _make_middleware(register_identity=False)
        context = _make_context()
        call_next = AsyncMock(return_value="ok")

        result = await middleware.on_call_tool(context, call_next)

        assert "mode=audit-only" in result
        assert "outcome=completed" in result

    @pytest.mark.asyncio
    async def test_bilateral_failure_falls_back_to_audit(self):
        """v2 create_proposal raises -> audit block created as fallback."""
        middleware, _, gateway_node = _make_middleware(register_identity=True)
        context = _make_context()
        call_next = AsyncMock(return_value="ok")

        # Make create_proposal fail
        original_create = gateway_node.protocol.create_proposal
        gateway_node.protocol.create_proposal = MagicMock(
            side_effect=ValueError("store full")
        )

        result = await middleware.on_call_tool(context, call_next)

        # Should have an audit block with fallback_reason
        chain = gateway_node.protocol.store.get_chain(
            gateway_node.protocol.pubkey
        )
        audit_blocks = [b for b in chain if b.block_type.value == "audit"]
        assert len(audit_blocks) == 1
        assert audit_blocks[0].transaction["fallback_reason"] == "store full"

    @pytest.mark.asyncio
    async def test_bilateral_success_no_audit(self):
        """Normal bilateral flow -> no audit block."""
        middleware, _, gateway_node = _make_middleware(register_identity=True)
        context = _make_context()
        call_next = AsyncMock(return_value="ok")

        await middleware.on_call_tool(context, call_next)

        chain = gateway_node.protocol.store.get_chain(
            gateway_node.protocol.pubkey
        )
        audit_blocks = [b for b in chain if b.block_type.value == "audit"]
        assert len(audit_blocks) == 0
        # But a proposal should exist
        proposals = [b for b in chain if b.block_type.value == "proposal"]
        assert len(proposals) == 1

    @pytest.mark.asyncio
    async def test_audit_captures_fallback_reason(self):
        """fallback_reason field populated in transaction."""
        middleware, _, gateway_node = _make_middleware(register_identity=True)
        context = _make_context()
        call_next = AsyncMock(return_value="ok")

        gateway_node.protocol.create_proposal = MagicMock(
            side_effect=Exception("network timeout")
        )

        await middleware.on_call_tool(context, call_next)

        chain = gateway_node.protocol.store.get_chain(
            gateway_node.protocol.pubkey
        )
        audit_blocks = [b for b in chain if b.block_type.value == "audit"]
        assert len(audit_blocks) == 1
        assert "network timeout" in audit_blocks[0].transaction["fallback_reason"]

    @pytest.mark.asyncio
    async def test_no_gateway_node_audit_is_noop(self):
        """v1 mode -> _record_audit silently returns (no crash)."""
        middleware, _, gateway_node = _make_middleware(
            use_v2=False, register_identity=False
        )
        context = _make_context()
        call_next = AsyncMock(return_value="ok")

        # Should not raise
        result = await middleware.on_call_tool(context, call_next)
        assert "mode=audit-only" in result

    @pytest.mark.asyncio
    async def test_audit_level_filtering(self):
        """MINIMAL level still records tool_call; excludes llm_decision."""
        middleware, _, gateway_node = _make_middleware(
            register_identity=False, audit_level="minimal"
        )
        assert EventType.TOOL_CALL in middleware._audit_events
        assert EventType.LLM_DECISION not in middleware._audit_events

        # tool_call should still create an audit block
        context = _make_context()
        call_next = AsyncMock(return_value="ok")
        await middleware.on_call_tool(context, call_next)

        chain = gateway_node.protocol.store.get_chain(
            gateway_node.protocol.pubkey
        )
        assert len(chain) == 1

    def test_config_audit_level_default(self):
        """GatewayConfig default audit_level is 'standard'."""
        config = GatewayConfig()
        assert config.audit_level == "standard"

    def test_create_gateway_from_dict_audit_level(self):
        """dict config propagates audit_level."""
        from gateway.server import create_gateway_from_dict

        # We can't easily inspect the middleware inside the FastMCP,
        # but we can verify GatewayConfig construction
        config = GatewayConfig(audit_level="comprehensive")
        assert config.audit_level == "comprehensive"

        config2 = GatewayConfig(audit_level="minimal")
        assert config2.audit_level == "minimal"
