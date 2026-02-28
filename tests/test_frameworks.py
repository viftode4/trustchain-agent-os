"""Tests for the framework testing ground — mock adapters + gateway integration."""

import pytest

from fastmcp import Client

from trustchain.identity import Identity
from trustchain.store import RecordStore
from trustchain.trust import compute_trust

from gateway.config import UpstreamServer
from gateway.recorder import InteractionRecorder
from gateway.registry import UpstreamRegistry

from tc_frameworks.mock import (
    ALL_MOCKS,
    CrewAIMock,
    OpenAIAgentsMock,
    AutoGenMock,
    LangGraphMock,
    GoogleADKMock,
    ElizaOSMock,
)


class TestMockAdapters:
    """Test each mock adapter creates a valid MCP server."""

    @pytest.mark.parametrize("MockClass", ALL_MOCKS)
    def test_adapter_has_framework_name(self, MockClass):
        adapter = MockClass()
        assert adapter.framework_name
        assert adapter.framework_version

    @pytest.mark.parametrize("MockClass", ALL_MOCKS)
    def test_adapter_has_tools(self, MockClass):
        adapter = MockClass()
        tools = adapter.get_tool_names()
        assert len(tools) >= 1

    @pytest.mark.parametrize("MockClass", ALL_MOCKS)
    def test_adapter_creates_mcp_server(self, MockClass):
        adapter = MockClass()
        mcp = adapter.create_mcp_server()
        assert mcp is not None

    @pytest.mark.parametrize("MockClass", ALL_MOCKS)
    def test_adapter_info(self, MockClass):
        adapter = MockClass()
        info = adapter.info
        assert "framework" in info
        assert "tools" in info
        assert "mcp_support" in info

    def test_elizaos_not_python_native(self):
        adapter = ElizaOSMock()
        assert not adapter.is_python_native

    def test_python_frameworks_are_native(self):
        for MockClass in [CrewAIMock, OpenAIAgentsMock, AutoGenMock, LangGraphMock, GoogleADKMock]:
            adapter = MockClass()
            assert adapter.is_python_native


class TestMockToolCalls:
    """Test that mock tools return meaningful responses via FastMCP Client."""

    @pytest.mark.asyncio
    async def test_crewai_research(self):
        mcp = CrewAIMock().create_mcp_server()
        async with Client(mcp) as client:
            result = await client.call_tool("research_topic", {"topic": "AI Trust"})
            text = result.content[0].text
            assert "CrewAI" in text
            assert "AI Trust" in text

    @pytest.mark.asyncio
    async def test_openai_agents_triage(self):
        mcp = OpenAIAgentsMock().create_mcp_server()
        async with Client(mcp) as client:
            result = await client.call_tool("triage_request", {"user_message": "I want a refund"})
            text = result.content[0].text
            assert "refund_agent" in text

    @pytest.mark.asyncio
    async def test_autogen_group_chat(self):
        mcp = AutoGenMock().create_mcp_server()
        async with Client(mcp) as client:
            result = await client.call_tool("group_chat", {"task": "Build trust"})
            text = result.content[0].text
            assert "AG2" in text
            assert "GroupChat" in text

    @pytest.mark.asyncio
    async def test_langgraph_react(self):
        mcp = LangGraphMock().create_mcp_server()
        async with Client(mcp) as client:
            result = await client.call_tool("react_agent_run", {"query": "What is trust?"})
            text = result.content[0].text
            assert "LangGraph" in text
            assert "ReAct" in text

    @pytest.mark.asyncio
    async def test_google_adk_a2a(self):
        mcp = GoogleADKMock().create_mcp_server()
        async with Client(mcp) as client:
            result = await client.call_tool("a2a_send_task", {
                "agent_url": "http://example.com",
                "task": "Verify identity"
            })
            text = result.content[0].text
            assert "A2A" in text
            assert "COMPLETED" in text

    @pytest.mark.asyncio
    async def test_elizaos_message(self):
        mcp = ElizaOSMock().create_mcp_server()
        async with Client(mcp) as client:
            result = await client.call_tool("eliza_message", {"content": "Hello"})
            text = result.content[0].text
            assert "ElizaOS" in text


class TestFrameworkTrustIntegration:
    """Test that trust builds correctly across different frameworks."""

    def test_trust_builds_independently_per_framework(self):
        store = RecordStore()
        gw_identity = Identity()
        registry = UpstreamRegistry(gw_identity)
        recorder = InteractionRecorder(gw_identity, store)

        # Register two frameworks
        crewai_config = UpstreamServer(name="CrewAI", command="mock", trust_threshold=0.3)
        openai_config = UpstreamServer(name="OpenAI", command="mock", trust_threshold=0.3)
        crewai_id = registry.register_server(crewai_config)
        openai_id = registry.register_server(openai_config)

        # Only interact with CrewAI
        for _ in range(5):
            recorder.record(crewai_id, "tool:research", "completed")

        crewai_trust = compute_trust(crewai_id.pubkey_hex, store)
        openai_trust = compute_trust(openai_id.pubkey_hex, store)

        assert crewai_trust > 0.0
        assert openai_trust == 0.0

    def test_failed_interactions_reduce_trust(self):
        store = RecordStore()
        gw_identity = Identity()
        registry = UpstreamRegistry(gw_identity)
        recorder = InteractionRecorder(gw_identity, store)

        config = UpstreamServer(name="BadFramework", command="mock")
        identity = registry.register_server(config)

        # Mix of completed and failed
        for _ in range(3):
            recorder.record(identity, "tool:test", "completed")
        for _ in range(7):
            recorder.record(identity, "tool:test", "failed")

        trust = compute_trust(identity.pubkey_hex, store)
        # 30% completion rate should result in lower trust
        assert trust < 0.5

    def test_all_six_frameworks_get_independent_trust(self):
        store = RecordStore()
        gw_identity = Identity()
        registry = UpstreamRegistry(gw_identity)
        recorder = InteractionRecorder(gw_identity, store)

        identities = {}
        for name in ["CrewAI", "OpenAI", "AutoGen", "LangGraph", "GoogleADK", "ElizaOS"]:
            config = UpstreamServer(name=name, command="mock")
            identities[name] = registry.register_server(config)

        # Give each a different number of interactions
        interaction_counts = {
            "CrewAI": 10, "OpenAI": 8, "AutoGen": 5,
            "LangGraph": 3, "GoogleADK": 1, "ElizaOS": 0,
        }
        for name, count in interaction_counts.items():
            for _ in range(count):
                recorder.record(identities[name], "tool:test", "completed")

        # Trust should be monotonically ordered
        trusts = {
            name: compute_trust(identities[name].pubkey_hex, store)
            for name in identities
        }
        assert trusts["CrewAI"] >= trusts["OpenAI"]
        assert trusts["OpenAI"] >= trusts["AutoGen"]
        assert trusts["AutoGen"] >= trusts["LangGraph"]
        assert trusts["LangGraph"] >= trusts["GoogleADK"]
        assert trusts["GoogleADK"] >= trusts["ElizaOS"]
        assert trusts["ElizaOS"] == 0.0
