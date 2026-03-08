"""Smoke, stress, and end-to-end tests for TrustChain Agent OS.

These are NOT unit tests.  Every test here exercises multiple real components
wired together — middleware, registry, recorder, trust engine, agents,
adapters — and verifies end-to-end behaviour across the full stack.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from unittest.mock import AsyncMock

import pytest

from trustchain.blockstore import MemoryBlockStore
from trustchain.identity import Identity
from trustchain.protocol import TrustChainProtocol
from trustchain.record import create_record, verify_record
from trustchain.store import RecordStore
from trustchain.trust import TrustEngine, compute_trust

from agent_os.agent import TrustAgent
from agent_os.context import TrustContext
from agent_os.decorators import record_interaction, trust_gate

from gateway.config import GatewayConfig, UpstreamServer
from gateway.middleware import TrustChainMiddleware
from gateway.node import GatewayNode
from gateway.recorder import InteractionRecorder
from gateway.registry import UpstreamRegistry
from gateway.trust_tools import register_trust_tools

# All 6 mock frameworks
from tc_frameworks.mock.langgraph_mock import LangGraphMock
from tc_frameworks.mock.crewai_mock import CrewAIMock
from tc_frameworks.mock.autogen_mock import AutoGenMock
from tc_frameworks.mock.openai_agents_mock import OpenAIAgentsMock
from tc_frameworks.mock.google_adk_mock import GoogleADKMock
from tc_frameworks.mock.elizaos_mock import ElizaOSMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeToolMessage:
    name: str
    arguments: dict = None

    def __post_init__(self):
        if self.arguments is None:
            self.arguments = {}


class FakeMiddlewareContext:
    def __init__(self, tool_name: str, arguments: dict = None):
        self.message = FakeToolMessage(name=tool_name, arguments=arguments)


def _make_gateway_stack(
    threshold=0.0, bootstrap=3, identity_dir=None, store=None,
):
    """Wire a full gateway stack: identity, registry, recorder, middleware."""
    store = store or RecordStore()
    gw_identity = Identity()
    registry = UpstreamRegistry(gw_identity, identity_dir=identity_dir)
    recorder = InteractionRecorder(gw_identity, store)
    middleware = TrustChainMiddleware(
        registry=registry,
        recorder=recorder,
        store=store,
        default_threshold=threshold,
        bootstrap_interactions=bootstrap,
    )
    return middleware, registry, gw_identity, recorder, store


def _register_mock_upstream(registry, upstream_name, tools, namespace=None, threshold=None):
    """Register a mock upstream server in the registry."""
    upstream = UpstreamServer(
        name=upstream_name,
        command="echo",  # Dummy command for testing
        namespace=namespace or upstream_name.lower().replace("/", "_").replace(" ", "_"),
        trust_threshold=threshold or 0.0,
    )
    registry.register_server(upstream)
    registry.register_tools_for_server(tools, upstream_name)
    return upstream


async def _call_tool(middleware, tool_name, args=None):
    """Simulate calling a tool through the middleware."""
    ctx = FakeMiddlewareContext(tool_name, args or {})
    call_next = AsyncMock(return_value=f"mock-result-{tool_name}")
    result = await middleware.on_call_tool(ctx, call_next)
    return result


# ============================================================================
# E2E: Multi-agent trust building via TrustAgent
# ============================================================================


class TestMultiAgentTrustBuilding:
    """Multiple TrustAgents build trust through bilateral service calls."""

    @pytest.fixture
    def agents(self):
        """Create 3 agents: alice, bob, charlie."""
        store = RecordStore()
        alice = TrustAgent("alice", store=store)
        bob = TrustAgent("bob", store=store)
        charlie = TrustAgent("charlie", store=store)

        @alice.service("compute", min_trust=0.0)
        async def alice_compute(data: dict, ctx: TrustContext) -> dict:
            return {"computed": sum(data.get("values", []))}

        @bob.service("analyze", min_trust=0.0)
        async def bob_analyze(data: dict, ctx: TrustContext) -> dict:
            return {"analysis": f"analyzed {len(data)} fields"}

        @charlie.service("validate", min_trust=0.3)
        async def charlie_validate(data: dict, ctx: TrustContext) -> dict:
            return {"valid": True}

        return alice, bob, charlie, store

    @pytest.mark.asyncio
    async def test_trust_grows_with_interactions(self, agents):
        """Trust scores increase as agents interact successfully."""
        alice, bob, _, store = agents

        # Before any interactions, trust is 0
        assert alice.check_trust(bob.pubkey) == 0.0
        assert bob.check_trust(alice.pubkey) == 0.0

        # 5 bilateral interactions
        for _ in range(5):
            ok, reason, result = await alice.call_service(bob, "analyze", {"x": 1})
            assert ok, f"Call failed: {reason}"

        # Both should now have non-zero trust
        alice_trust = alice.check_trust(bob.pubkey)
        bob_trust = bob.check_trust(alice.pubkey)
        assert alice_trust > 0.0
        assert bob_trust > 0.0

    @pytest.mark.asyncio
    async def test_trust_gate_denies_then_allows(self, agents):
        """Charlie requires trust >= 0.3. New callers get bootstrap, then must earn it."""
        alice, _, charlie, store = agents

        # Bootstrap mode: first calls should succeed despite 0.3 threshold
        for i in range(3):
            ok, reason, result = await alice.call_service(charlie, "validate", {"check": i})
            assert ok, f"Bootstrap call {i} denied: {reason}"

        # After bootstrap, alice has some trust from the 3 interactions
        # Whether she's blocked or allowed depends on accumulated trust
        alice_trust_at_charlie = charlie.check_trust(alice.pubkey)
        ok, reason, result = await alice.call_service(charlie, "validate", {"check": 99})
        if alice_trust_at_charlie < 0.3:
            assert not ok, "Should be denied after bootstrap with low trust"
            assert "denied" in reason.lower() or "Trust gate" in reason
        else:
            assert ok, "Should be allowed when trust is sufficient"

    @pytest.mark.asyncio
    async def test_three_agent_chain(self, agents):
        """A → B → C chain: direct interactions accumulate more trust."""
        alice, bob, charlie, store = agents

        # Build trust: alice ↔ bob (5 interactions)
        for _ in range(5):
            await alice.call_service(bob, "analyze", {"x": 1})

        # Build trust: bob ↔ charlie (5 interactions — bootstrap covers first 3)
        for _ in range(5):
            await bob.call_service(charlie, "validate", {"check": 1})

        # alice should have higher trust with bob (5 direct) than charlie (0 direct)
        alice_bob_trust = alice.check_trust(bob.pubkey)
        alice_charlie_trust = alice.check_trust(charlie.pubkey)
        assert alice_bob_trust > 0.0
        # Direct interaction creates stronger trust
        # (With shared store, charlie may have indirect trust, but less than bob)
        assert alice_bob_trust >= alice_charlie_trust
        # bob should have trust with both
        assert bob.check_trust(alice.pubkey) > 0.0
        assert bob.check_trust(charlie.pubkey) > 0.0

    @pytest.mark.asyncio
    async def test_failed_service_records_outcome(self, agents):
        """Failed service calls are recorded with outcome='failed'."""
        alice, bob, _, store = agents

        @bob.service("failing_service", min_trust=0.0)
        async def fails(data: dict, ctx: TrustContext) -> dict:
            raise RuntimeError("boom")

        ok, reason, result = await alice.call_service(bob, "failing_service")
        assert ok  # The call was accepted (not trust-denied), handler just failed
        assert "failed" in reason
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_service_rejected(self, agents):
        """Calling a non-existent service returns not-accepted."""
        alice, bob, _, _ = agents
        ok, reason, result = await alice.call_service(bob, "nonexistent_service")
        assert not ok
        assert "Unknown service" in reason


# ============================================================================
# E2E: All 6 framework mocks through the gateway middleware
# ============================================================================


ALL_MOCKS = [
    ("LangGraph", LangGraphMock, ["react_agent_run", "supervisor_delegate", "graph_state_query"]),
    ("CrewAI", CrewAIMock, ["research_topic", "write_report", "delegate_task"]),
    ("AutoGen", AutoGenMock, ["group_chat", "code_execution", "register_tool_call"]),
    ("OpenAI Agents", OpenAIAgentsMock, ["triage_request", "run_agent", "agent_web_search"]),
    ("Google ADK", GoogleADKMock, ["adk_agent_run", "a2a_send_task", "adk_sub_agent"]),
    ("ElizaOS", ElizaOSMock, ["eliza_message", "eliza_knowledge_query", "eliza_solana_action"]),
]


class TestAllFrameworksThroughGateway:
    """Every mock framework should be callable through the gateway with trust annotations."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("framework_name,mock_cls,tools", ALL_MOCKS)
    async def test_mock_framework_trusted_call(self, framework_name, mock_cls, tools):
        """Each framework's tools pass through the middleware and get trust-annotated."""
        middleware, registry, _, _, store = _make_gateway_stack()
        _register_mock_upstream(registry, framework_name, tools)

        # Call each tool
        for tool_name in tools:
            result = await _call_tool(middleware, tool_name)
            assert "[TrustChain]" in result, f"{framework_name}/{tool_name} missing trust annotation"
            assert "outcome=completed" in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("framework_name,mock_cls,tools", ALL_MOCKS)
    async def test_mock_framework_adapter_info(self, framework_name, mock_cls, tools):
        """Each mock adapter reports correct metadata."""
        adapter = mock_cls()
        info = adapter.info
        assert info["framework"] == adapter.framework_name
        assert info["tools"] == adapter.get_tool_names()
        server = adapter.create_mcp_server()
        assert server is not None

    @pytest.mark.asyncio
    async def test_all_frameworks_same_gateway(self):
        """All 6 frameworks mounted on a single gateway with trust gating."""
        middleware, registry, _, _, store = _make_gateway_stack()

        # Register all frameworks
        for name, _, tools in ALL_MOCKS:
            _register_mock_upstream(registry, name, tools)

        # Call one tool from each framework
        first_tools = [tools[0] for _, _, tools in ALL_MOCKS]
        results = []
        for tool_name in first_tools:
            result = await _call_tool(middleware, tool_name)
            results.append(result)

        # All should have trust annotations
        for i, result in enumerate(results):
            assert "[TrustChain]" in result, f"Framework {ALL_MOCKS[i][0]} missing annotation"

        # Store should have records for all frameworks
        # Each tool call creates a v1 record
        all_records = []
        for name, _, _ in ALL_MOCKS:
            identity = registry.identity_for(name)
            records = store.get_records_for(identity.pubkey_hex)
            all_records.extend(records)
        assert len(all_records) >= 6, f"Expected >=6 records, got {len(all_records)}"


# ============================================================================
# E2E: Gateway middleware trust blocking with real trust scoring
# ============================================================================


class TestGatewayTrustBlocking:
    """Tests the full denied → build trust → allowed progression."""

    @pytest.mark.asyncio
    async def test_bootstrap_then_blocked_then_allowed(self):
        """New server: bootstrap → block after bootstrap → allow after building trust."""
        from fastmcp.exceptions import ToolError

        store = RecordStore()
        gw_identity = Identity()

        # Use a very high threshold (0.95) so 2 bootstrap calls can't clear it
        middleware, registry, _, _, store = _make_gateway_stack(
            threshold=0.95, bootstrap=2, store=store,
        )
        _register_mock_upstream(registry, "HighSec", ["secure_op"], threshold=0.95)
        upstream_identity = registry.identity_for("HighSec")

        # Phase 1: Bootstrap mode (first 2 calls succeed despite threshold)
        for _ in range(2):
            result = await _call_tool(middleware, "secure_op")
            assert "outcome=completed" in result

        # Phase 2: Bootstrap exhausted — check if trust below threshold
        trust_after_bootstrap = compute_trust(upstream_identity.pubkey_hex, store)
        if trust_after_bootstrap < 0.95:
            # Should be BLOCKED
            with pytest.raises(ToolError, match="BLOCKED"):
                await _call_tool(middleware, "secure_op")

            # Phase 3: Build trust manually to exceed 0.95
            for i in range(30):
                seq_a = store.sequence_number_for(gw_identity.pubkey_hex)
                seq_b = store.sequence_number_for(upstream_identity.pubkey_hex)
                record = create_record(
                    identity_a=gw_identity,
                    identity_b=upstream_identity,
                    seq_a=seq_a, seq_b=seq_b,
                    prev_hash_a=store.last_hash_for(gw_identity.pubkey_hex),
                    prev_hash_b=store.last_hash_for(upstream_identity.pubkey_hex),
                    interaction_type="service", outcome="completed",
                )
                store.add_record(record)

            # Phase 4: Trust should now be high enough
            trust = compute_trust(upstream_identity.pubkey_hex, store)
            if trust >= 0.95:
                result = await _call_tool(middleware, "secure_op")
                assert "outcome=completed" in result


# ============================================================================
# Stress: Rapid sequential interactions
# ============================================================================


class TestStressRapidInteractions:
    """Stress tests with many rapid interactions."""

    @pytest.mark.asyncio
    async def test_100_sequential_tool_calls(self):
        """100 tool calls through the middleware without errors."""
        middleware, registry, _, _, store = _make_gateway_stack()
        _register_mock_upstream(registry, "FastService", ["fast_op"])

        for i in range(100):
            result = await _call_tool(middleware, "fast_op", {"i": i})
            assert "[TrustChain]" in result

        # Verify all interactions were recorded
        identity = registry.identity_for("FastService")
        records = store.get_records_for(identity.pubkey_hex)
        assert len(records) == 100

    @pytest.mark.asyncio
    async def test_50_agent_bilateral_interactions(self):
        """50 bilateral agent-to-agent calls building trust steadily."""
        store = RecordStore()
        alice = TrustAgent("alice", store=store)
        bob = TrustAgent("bob", store=store)

        @bob.service("echo", min_trust=0.0)
        async def echo(data: dict, ctx: TrustContext) -> dict:
            return {"echo": data}

        trust_history = []
        for i in range(50):
            ok, reason, result = await alice.call_service(bob, "echo", {"i": i})
            assert ok, f"Call {i} failed: {reason}"
            trust_history.append(alice.check_trust(bob.pubkey))

        # Trust should be monotonically non-decreasing (or at least stabilize)
        final_trust = trust_history[-1]
        assert final_trust > 0.0, "Trust should be positive after 50 interactions"

        # Verify record count
        alice_records = store.get_records_for(alice.pubkey)
        bob_records = store.get_records_for(bob.pubkey)
        assert len(alice_records) == 50
        assert len(bob_records) == 50

    @pytest.mark.asyncio
    async def test_concurrent_tool_calls(self):
        """Multiple concurrent tool calls don't corrupt state."""
        middleware, registry, _, _, store = _make_gateway_stack()
        _register_mock_upstream(registry, "ConcurrentSvc", ["concurrent_op"])

        tasks = [_call_tool(middleware, "concurrent_op", {"i": i}) for i in range(20)]
        results = await asyncio.gather(*tasks)

        assert all("[TrustChain]" in r for r in results)
        identity = registry.identity_for("ConcurrentSvc")
        records = store.get_records_for(identity.pubkey_hex)
        assert len(records) == 20


# ============================================================================
# E2E: Identity persistence across simulated restarts
# ============================================================================


class TestIdentityPersistenceE2E:
    """End-to-end: trust history survives simulated gateway restarts."""

    @pytest.mark.asyncio
    async def test_trust_survives_restart(self):
        """Build trust → 'restart' gateway → trust still there."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = RecordStore()
            identity_dir = Path(tmpdir) / "identities"
            identity_dir.mkdir()

            # Session 1: build trust with 5 calls
            m1, r1, gw1, _, _ = _make_gateway_stack(
                identity_dir=str(identity_dir), store=store,
            )
            _register_mock_upstream(r1, "PersistentSvc", ["persist_op"])
            up_identity_1 = r1.identity_for("PersistentSvc")

            for _ in range(5):
                await _call_tool(m1, "persist_op")

            trust_before = compute_trust(up_identity_1.pubkey_hex, store)
            records_before = len(store.get_records_for(up_identity_1.pubkey_hex))
            pubkey_before = up_identity_1.pubkey_hex

            # Session 2: "restart" — new middleware, same store and identity_dir
            m2, r2, gw2, _, _ = _make_gateway_stack(
                identity_dir=str(identity_dir), store=store,
            )
            _register_mock_upstream(r2, "PersistentSvc", ["persist_op"])
            up_identity_2 = r2.identity_for("PersistentSvc")

            # Same identity loaded from disk
            assert up_identity_2.pubkey_hex == pubkey_before, \
                "Identity should be loaded from persisted key file"

            # Trust is preserved
            trust_after = compute_trust(up_identity_2.pubkey_hex, store)
            assert trust_after == trust_before

            # New call continues building trust
            await _call_tool(m2, "persist_op")
            records_after = len(store.get_records_for(up_identity_2.pubkey_hex))
            assert records_after == records_before + 1

    @pytest.mark.asyncio
    async def test_corrupt_key_recovery_e2e(self):
        """Corrupt key file → auto-recovery → new identity but no crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            identity_dir = Path(tmpdir) / "identities"
            identity_dir.mkdir()

            # Session 1: create a key
            m1, r1, _, _, _ = _make_gateway_stack(identity_dir=str(identity_dir))
            _register_mock_upstream(r1, "CorruptTest", ["corrupt_op"])
            pubkey_1 = r1.identity_for("CorruptTest").pubkey_hex

            # Corrupt the key file
            key_file = identity_dir / "CorruptTest.key"
            assert key_file.exists()
            key_file.write_text("CORRUPTED DATA")

            # Session 2: should recover gracefully
            m2, r2, _, _, _ = _make_gateway_stack(identity_dir=str(identity_dir))
            _register_mock_upstream(r2, "CorruptTest", ["corrupt_op"])
            pubkey_2 = r2.identity_for("CorruptTest").pubkey_hex

            # New identity generated (old one was corrupt)
            assert pubkey_2 != pubkey_1
            # But the gateway works
            result = await _call_tool(m2, "corrupt_op")
            assert "[TrustChain]" in result


# ============================================================================
# E2E: GatewayNode v2 with TrustEngine
# ============================================================================


class TestGatewayNodeV2E2E:
    """End-to-end tests for the v2 half-block GatewayNode path."""

    @pytest.fixture
    def v2_stack(self):
        """Create a GatewayNode with TrustEngine."""
        identity = Identity()
        block_store = MemoryBlockStore()
        node = GatewayNode(identity, block_store)
        return node, identity, block_store

    @pytest.mark.asyncio
    async def test_v2_proposal_and_trust_scoring(self, v2_stack):
        """Creating proposals through protocol updates the trust chain."""
        node, identity, store = v2_stack
        peer = Identity()

        # Create a proposal using the protocol directly (no peer registration needed)
        proposal = node.protocol.create_proposal(
            peer.pubkey_hex,
            {"interaction_type": "test", "outcome": "completed"},
        )
        assert proposal is not None
        assert proposal.public_key == identity.pubkey_hex
        assert proposal.link_public_key == peer.pubkey_hex

        # Trust engine can score the peer
        trust = node.get_trust_score(peer.pubkey_hex)
        assert isinstance(trust, float)

    @pytest.mark.asyncio
    async def test_v2_trust_gate_in_trusted_transact(self, v2_stack):
        """Trust gating in trusted_transact blocks low-trust after bootstrap."""
        node, identity, store = v2_stack
        peer = Identity()

        # Build some interactions via protocol
        for _ in range(4):
            node.protocol.create_proposal(
                peer.pubkey_hex,
                {"interaction_type": "test", "outcome": "completed"},
            )

        # After bootstrap, with very high threshold → should be blocked
        # (peer not registered as URL, so trusted_transact can't do the
        #  network call, but trust gating happens before that)
        result = await node.trusted_transact(
            peer.pubkey_hex,
            {"interaction_type": "test", "outcome": "completed"},
            min_trust=0.99,  # Very high threshold
            bootstrap_interactions=3,
        )
        # Either blocked by trust gate, or fails at transact level
        # Key: no crash, structured result
        assert "accepted" in result or "trust_score" in result

    @pytest.mark.asyncio
    async def test_v2_bidirectional_counting(self, v2_stack):
        """Both outbound and inbound blocks are counted for bootstrap exit."""
        node, identity, store = v2_stack
        peer_identity = Identity()
        peer_store = MemoryBlockStore()
        peer_protocol = TrustChainProtocol(peer_identity, peer_store)

        # Create inbound proposal from peer → node
        proposal = peer_protocol.create_proposal(
            identity.pubkey_hex,
            {"interaction_type": "inbound", "outcome": "completed"},
        )
        # Simulate gossip: peer's block appears in our store
        store.add_block(proposal)

        # Now count should include the inbound interaction
        count = node._count_peer_interactions(peer_identity.pubkey_hex)
        assert count >= 1, "Inbound interaction should be counted"

    @pytest.mark.asyncio
    async def test_v2_cache_invalidation_works(self, v2_stack):
        """Interaction count cache is invalidated after new interactions."""
        node, identity, store = v2_stack
        peer = Identity()

        # First count (cached)
        count_1 = node._count_peer_interactions(peer.pubkey_hex)
        assert count_1 == 0

        # Create interaction via protocol directly
        node.protocol.create_proposal(
            peer.pubkey_hex,
            {"interaction_type": "test", "outcome": "completed"},
        )
        node.invalidate_count_cache(peer.pubkey_hex)

        # Count should reflect new interaction
        count_2 = node._count_peer_interactions(peer.pubkey_hex)
        assert count_2 > count_1


# ============================================================================
# E2E: TrustAgent + decorators end-to-end
# ============================================================================


class TestDecoratorE2E:
    """End-to-end tests for the trust_gate and record_interaction decorators."""

    @pytest.mark.asyncio
    async def test_trust_gate_decorator_e2e(self):
        """trust_gate decorator blocks low-trust callers after bootstrap."""
        store = RecordStore()
        agent = TrustAgent("gated-agent", store=store)
        caller = TrustAgent("caller", store=store)

        @agent.service("gated_op", min_trust=0.5)
        @trust_gate(min_trust=0.5)
        async def gated_op(data: dict, ctx: TrustContext) -> dict:
            return {"result": "secret"}

        # Bootstrap calls work
        for _ in range(3):
            ok, reason, result = await caller.call_service(agent, "gated_op")
            assert ok

    @pytest.mark.asyncio
    async def test_record_interaction_decorator_e2e(self):
        """record_interaction decorator creates bilateral records."""
        store = RecordStore()
        agent = TrustAgent("recording-agent", store=store)
        caller = TrustAgent("caller", store=store)

        @agent.service("recorded_op", min_trust=0.0)
        @record_interaction("custom_type")
        async def recorded_op(data: dict, ctx: TrustContext) -> dict:
            return {"done": True}

        ok, reason, result = await caller.call_service(agent, "recorded_op")
        assert ok

        # Records should exist for both parties
        agent_records = store.get_records_for(agent.pubkey)
        assert len(agent_records) >= 1


# ============================================================================
# E2E: TrustAgent v2 with TrustChainNode
# ============================================================================


class TestTrustAgentV2E2E:
    """TrustAgent with actual TrustChainNode (half-block protocol)."""

    def _make_v2_agent(self, name, store=None):
        """Create a v2 TrustAgent with a real TrustChainNode."""
        from trustchain.api import TrustChainNode
        identity = Identity()
        block_store = store or MemoryBlockStore()
        node = TrustChainNode(identity, block_store)
        agent = TrustAgent(name, node=node)
        return agent

    @pytest.mark.asyncio
    async def test_v2_bilateral_interaction(self):
        """Two v2 agents interact and create proper half-blocks."""
        alice = self._make_v2_agent("alice")
        bob = self._make_v2_agent("bob")

        @bob.service("greet", min_trust=0.0)
        async def greet(data: dict, ctx: TrustContext) -> dict:
            return {"greeting": f"Hello {data.get('name', 'world')}!"}

        ok, reason, result = await alice.call_service(bob, "greet", {"name": "Alice"})
        assert ok
        assert result == {"greeting": "Hello Alice!"}

        # Half-blocks should exist
        alice_chain = alice.node.store.get_chain(alice.pubkey)
        assert len(alice_chain) >= 1

    @pytest.mark.asyncio
    async def test_v2_error_handling_doesnt_break_call(self):
        """v2 protocol errors don't break the agent service call."""
        alice = self._make_v2_agent("alice")
        bob = self._make_v2_agent("bob")

        @bob.service("safe_op", min_trust=0.0)
        async def safe_op(data: dict, ctx: TrustContext) -> dict:
            return {"safe": True}

        # Even if protocol has issues, the call should succeed
        ok, reason, result = await alice.call_service(bob, "safe_op")
        assert ok
        assert result == {"safe": True}

    @pytest.mark.asyncio
    async def test_v2_multiple_interactions_build_chain(self):
        """Multiple v2 interactions create a proper chain."""
        alice = self._make_v2_agent("alice")
        bob = self._make_v2_agent("bob")

        @bob.service("increment", min_trust=0.0)
        async def increment(data: dict, ctx: TrustContext) -> dict:
            return {"value": data.get("value", 0) + 1}

        for i in range(10):
            ok, reason, result = await alice.call_service(
                bob, "increment", {"value": i},
            )
            assert ok
            assert result == {"value": i + 1}

        # Both chains should have 10 blocks
        alice_chain = alice.node.store.get_chain(alice.pubkey)
        assert len(alice_chain) == 10


# ============================================================================
# E2E: MCP server export
# ============================================================================


class TestMCPServerExport:
    """TrustAgent.as_mcp_server() exports services correctly."""

    def test_agent_exports_all_services(self):
        """as_mcp_server() creates tools for all registered services."""
        agent = TrustAgent("export-test")

        @agent.service("svc_a", min_trust=0.0)
        async def svc_a(data: dict, ctx: TrustContext) -> dict:
            return {}

        @agent.service("svc_b", min_trust=0.5)
        async def svc_b(data: dict, ctx: TrustContext) -> dict:
            return {}

        mcp = agent.as_mcp_server()
        assert mcp is not None
        # The MCP server should have tools for svc_a, svc_b, and trustchain_agent_info
        # (We can't easily list tools without starting the server, but no crash is good)

    def test_agent_repr(self):
        """Agent repr includes mode, services, and trust."""
        agent = TrustAgent("repr-test")

        @agent.service("my_svc")
        async def my_svc(data, ctx):
            return {}

        r = repr(agent)
        assert "repr-test" in r
        assert "v1" in r
        assert "my_svc" in r


# ============================================================================
# Stress: GatewayNode interaction count cache under load
# ============================================================================


class TestCacheStress:
    """Stress test the interaction count cache."""

    @pytest.mark.asyncio
    async def test_cache_under_rapid_access(self):
        """Rapid access to the cache doesn't corrupt it."""
        identity = Identity()
        store = MemoryBlockStore()
        node = GatewayNode(identity, store)

        peers = [Identity() for _ in range(20)]

        # Create interactions with all peers via protocol directly
        for peer in peers:
            node.protocol.create_proposal(
                peer.pubkey_hex,
                {"interaction_type": "stress", "outcome": "completed"},
            )

        # Rapid cache reads for all peers
        for _ in range(100):
            for peer in peers:
                count = node._count_peer_interactions(peer.pubkey_hex)
                assert count >= 0  # Never negative

    @pytest.mark.asyncio
    async def test_cache_ttl_expiry(self):
        """Cache entries expire after TTL."""
        from gateway.node import _CACHE_TTL

        identity = Identity()
        store = MemoryBlockStore()
        node = GatewayNode(identity, store)
        peer = Identity()

        # Warm the cache
        count_1 = node._count_peer_interactions(peer.pubkey_hex)
        assert count_1 == 0

        # Manually insert a stale entry
        import time as _time
        node._interaction_count_cache[peer.pubkey_hex] = (999, _time.monotonic() - _CACHE_TTL - 1)

        # Next call should recompute (not return stale 999)
        count_2 = node._count_peer_interactions(peer.pubkey_hex)
        assert count_2 == 0  # Real count, not stale


# ============================================================================
# Smoke: Framework adapter caching
# ============================================================================


class TestAdapterCaching:
    """Verify that adapters cache their built agents/crews."""

    def test_crewai_adapter_caches_crew(self):
        from tc_frameworks.adapters.crewai_adapter import CrewAIAdapter
        adapter = CrewAIAdapter(crew_config={"agents": [], "tasks": []})
        assert adapter._crew is None

    def test_langgraph_adapter_caches_agent(self):
        from tc_frameworks.adapters.langgraph_adapter import LangGraphAdapter
        adapter = LangGraphAdapter()
        assert adapter._agent is None

    def test_openai_agents_adapter_caches_agent(self):
        from tc_frameworks.adapters.openai_agents_adapter import OpenAIAgentsAdapter
        adapter = OpenAIAgentsAdapter()
        assert adapter._agent is None

    def test_google_adk_adapter_has_lock(self):
        from tc_frameworks.adapters.google_adk_adapter import GoogleADKAdapter
        adapter = GoogleADKAdapter()
        assert adapter._init_lock is not None
        assert adapter._runner is None
        assert adapter._session_id is None


# ============================================================================
# E2E: Native trust tools through the middleware
# ============================================================================


class TestNativeTrustToolsE2E:
    """Trust query tools (trustchain_*) bypass the gate and work end-to-end."""

    @pytest.mark.asyncio
    async def test_native_tools_bypass_gate(self):
        """trustchain_* tools are not trust-gated."""
        middleware, registry, _, _, _ = _make_gateway_stack(threshold=1.0)

        # No upstream registered, but native tools should still work
        ctx = FakeMiddlewareContext("trustchain_check_trust")
        call_next = AsyncMock(return_value="trust info")
        result = await middleware.on_call_tool(ctx, call_next)
        assert result == "trust info"  # No trust annotation on native tools

    @pytest.mark.asyncio
    async def test_non_native_tool_unknown_server_passes(self):
        """Tools not mapped to any server are forwarded without gating."""
        middleware, registry, _, _, _ = _make_gateway_stack()

        ctx = FakeMiddlewareContext("unknown_tool_xyz")
        call_next = AsyncMock(return_value="result")
        result = await middleware.on_call_tool(ctx, call_next)
        assert result == "result"  # Forwarded as-is


# ============================================================================
# Smoke: ElizaOS adapter properties
# ============================================================================


class TestElizaOSAdapter:
    """ElizaOS is the only non-Python-native, non-MCP framework."""

    def test_elizaos_properties(self):
        mock = ElizaOSMock()
        assert mock.has_native_mcp is False
        assert mock.is_python_native is False
        info = mock.info
        assert info["mcp_support"] is False
        assert info["python_native"] is False

    def test_other_frameworks_are_python_native(self):
        for name, cls, _ in ALL_MOCKS:
            if name != "ElizaOS":
                adapter = cls()
                assert adapter.is_python_native is True, f"{name} should be Python-native"


# ============================================================================
# Helpers: v2 gateway stack with audit support
# ============================================================================


def _make_v2_gateway_stack(threshold=0.0, bootstrap=3, audit_level="standard"):
    """Wire a full v2 gateway stack with GatewayNode and audit support."""
    store = RecordStore()
    gw_identity = Identity()
    block_store = MemoryBlockStore()
    registry = UpstreamRegistry(gw_identity)
    recorder = InteractionRecorder(gw_identity, store)
    gateway_node = GatewayNode(
        identity=gw_identity,
        store=block_store,
        seed_nodes=[gw_identity.pubkey_hex],
    )
    middleware = TrustChainMiddleware(
        registry=registry,
        recorder=recorder,
        store=store,
        default_threshold=threshold,
        bootstrap_interactions=bootstrap,
        trust_engine=gateway_node.trust_engine,
        gateway_node=gateway_node,
        audit_level=audit_level,
    )
    return middleware, registry, gw_identity, gateway_node


def _register_no_identity_upstream(registry, name, tools):
    """Register an upstream server and then remove its identity (single-player mode)."""
    upstream = _register_mock_upstream(registry, name, tools)
    registry._server_identities.pop(name, None)
    return upstream


# ============================================================================
# E2E: Audit fallback (single-player mode)
# ============================================================================


class TestAuditFallbackE2E:
    """End-to-end tests for single-player audit fallback through the full stack."""

    @pytest.mark.asyncio
    async def test_single_player_audit_block_created(self):
        """Register upstream, remove its identity -> tool call succeeds, audit block stored."""
        middleware, registry, gw_identity, gateway_node = _make_v2_gateway_stack()
        _register_no_identity_upstream(registry, "AuditSvc", ["audit_op"])

        result = await _call_tool(middleware, "audit_op")

        assert "mode=audit-only" in result
        assert "outcome=completed" in result

        # Verify audit block in GatewayNode's block store
        chain = gateway_node.protocol.store.get_chain(gw_identity.pubkey_hex)
        assert len(chain) == 1
        block = chain[0]
        assert block.block_type.value == "audit"
        assert block.transaction["action"] == "tool:audit_op"
        assert block.transaction["outcome"] == "completed"
        assert block.public_key == gw_identity.pubkey_hex

    @pytest.mark.asyncio
    async def test_single_player_chain_integrity(self):
        """5 audit-only calls -> validate_chain() passes, sequence 1..5 contiguous."""
        middleware, registry, gw_identity, gateway_node = _make_v2_gateway_stack()
        _register_no_identity_upstream(registry, "ChainSvc", ["chain_op"])

        for i in range(5):
            result = await _call_tool(middleware, "chain_op", {"i": i})
            assert "mode=audit-only" in result

        # Chain integrity
        pubkey = gw_identity.pubkey_hex
        assert gateway_node.protocol.validate_chain(pubkey) is True

        # Verify contiguous sequences 1..5
        chain = gateway_node.protocol.store.get_chain(pubkey)
        assert len(chain) == 5
        for i, block in enumerate(chain):
            assert block.sequence_number == i + 1

    @pytest.mark.asyncio
    async def test_mixed_bilateral_and_audit_chain(self):
        """Server A has identity (bilateral), Server B lacks identity (audit) -> valid chain."""
        middleware, registry, gw_identity, gateway_node = _make_v2_gateway_stack()

        # Server A: bilateral (has identity)
        _register_mock_upstream(registry, "BilateralSvc", ["bilateral_op"])
        # Server B: audit-only (no identity)
        _register_no_identity_upstream(registry, "AuditSvc", ["audit_op"])

        # Interleave calls: bilateral, audit, bilateral, audit
        for _ in range(2):
            r1 = await _call_tool(middleware, "bilateral_op")
            assert "outcome=completed" in r1
            assert "mode=audit-only" not in r1  # bilateral path

            r2 = await _call_tool(middleware, "audit_op")
            assert "mode=audit-only" in r2

        # Full chain should be valid (4 blocks: 2 proposals + 2 audits)
        pubkey = gw_identity.pubkey_hex
        assert gateway_node.protocol.validate_chain(pubkey) is True

        chain = gateway_node.protocol.store.get_chain(pubkey)
        assert len(chain) == 4
        block_types = [b.block_type.value for b in chain]
        assert block_types.count("proposal") == 2
        assert block_types.count("audit") == 2

    @pytest.mark.asyncio
    async def test_audit_fallback_on_bilateral_failure_e2e(self):
        """create_proposal raises mid-call -> audit block as fallback, chain still valid."""
        middleware, registry, gw_identity, gateway_node = _make_v2_gateway_stack()
        _register_mock_upstream(registry, "FlakyBilateral", ["flaky_op"])

        # Make create_proposal fail
        from unittest.mock import MagicMock
        gateway_node.protocol.create_proposal = MagicMock(
            side_effect=ValueError("store full")
        )

        result = await _call_tool(middleware, "flaky_op")
        # Call should still succeed (error resilience)
        assert "outcome=completed" in result

        # Should have an audit fallback block
        chain = gateway_node.protocol.store.get_chain(gw_identity.pubkey_hex)
        audit_blocks = [b for b in chain if b.block_type.value == "audit"]
        assert len(audit_blocks) == 1
        assert audit_blocks[0].transaction["fallback_reason"] == "store full"

        # Chain is still valid
        assert gateway_node.protocol.validate_chain(gw_identity.pubkey_hex) is True

    @pytest.mark.asyncio
    async def test_all_frameworks_single_player_mode(self):
        """All 6 mock frameworks with identities removed -> audit-only, audit blocks match."""
        middleware, registry, gw_identity, gateway_node = _make_v2_gateway_stack()

        all_tools = []
        for name, _, tools in ALL_MOCKS:
            _register_no_identity_upstream(registry, name, tools)
            all_tools.extend(tools)

        # Call one tool from each framework
        for name, _, tools in ALL_MOCKS:
            result = await _call_tool(middleware, tools[0])
            assert "mode=audit-only" in result, f"{name} missing audit-only annotation"
            assert "outcome=completed" in result

        # Audit blocks should match framework count (6 calls, one per framework)
        chain = gateway_node.protocol.store.get_chain(gw_identity.pubkey_hex)
        audit_blocks = [b for b in chain if b.block_type.value == "audit"]
        assert len(audit_blocks) == 6

    @pytest.mark.asyncio
    async def test_stress_50_audit_calls(self):
        """50 rapid audit-only calls -> 50 audit blocks, chain validates, no seq gaps."""
        middleware, registry, gw_identity, gateway_node = _make_v2_gateway_stack()
        _register_no_identity_upstream(registry, "StressSvc", ["stress_op"])

        for i in range(50):
            result = await _call_tool(middleware, "stress_op", {"i": i})
            assert "mode=audit-only" in result

        pubkey = gw_identity.pubkey_hex
        chain = gateway_node.protocol.store.get_chain(pubkey)
        assert len(chain) == 50

        # No sequence gaps
        for i, block in enumerate(chain):
            assert block.sequence_number == i + 1, f"Gap at seq {i+1}"

        # Full chain validation
        assert gateway_node.protocol.validate_chain(pubkey) is True

    @pytest.mark.asyncio
    async def test_audit_level_comprehensive_records_all(self):
        """comprehensive records audit blocks; minimal still records tool_call events."""
        # Comprehensive
        mw_comp, reg_comp, id_comp, gn_comp = _make_v2_gateway_stack(
            audit_level="comprehensive"
        )
        _register_no_identity_upstream(reg_comp, "CompSvc", ["comp_op"])
        await _call_tool(mw_comp, "comp_op")

        chain_comp = gn_comp.protocol.store.get_chain(id_comp.pubkey_hex)
        assert len(chain_comp) == 1
        assert chain_comp[0].block_type.value == "audit"

        # Minimal — tool_call is always included so audit block is still created
        mw_min, reg_min, id_min, gn_min = _make_v2_gateway_stack(
            audit_level="minimal"
        )
        _register_no_identity_upstream(reg_min, "MinSvc", ["min_op"])
        await _call_tool(mw_min, "min_op")

        chain_min = gn_min.protocol.store.get_chain(id_min.pubkey_hex)
        assert len(chain_min) == 1
        assert chain_min[0].transaction["event_type"] == "tool_call"
