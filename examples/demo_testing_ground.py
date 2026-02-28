"""TrustChain Testing Ground — 6 Agent Frameworks, 1 Trust Layer.

Demonstrates that TrustChain works as a universal trust substrate
beneath ANY agent framework. Each framework's agent connects through
the TrustChain gateway, gets trust-verified, and builds bilateral
interaction history.

The thesis argument: "Code is cheap, trust is priceless."
Every framework handles communication — NONE handle trust.
TrustChain fills that gap.

Run:
    cd G:/Projects/blockchains/trustchain-agent-os
    python examples/demo_testing_ground.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import FastMCP, Client

from trustchain.identity import Identity
from trustchain.store import RecordStore
from trustchain.trust import compute_trust

from gateway.middleware import TrustChainMiddleware
from gateway.recorder import InteractionRecorder
from gateway.registry import UpstreamRegistry
from gateway.trust_tools import register_trust_tools

from tc_frameworks.mock import (
    CrewAIMock,
    OpenAIAgentsMock,
    AutoGenMock,
    LangGraphMock,
    GoogleADKMock,
    ElizaOSMock,
)


def print_header(text: str, width: int = 70):
    print()
    print("=" * width)
    print(f"  {text}")
    print("=" * width)
    print()


def print_section(text: str):
    print(f"\n--- {text} ---\n")


async def main():
    print_header("TrustChain Testing Ground")
    print("  6 Agent Frameworks, 1 Trust Layer")
    print("  'Code Is Cheap, Trust Is Priceless'")
    print()

    # ---- Setup: TrustChain infrastructure ----
    store = RecordStore()
    gw_identity = Identity()
    registry = UpstreamRegistry(gw_identity)
    recorder = InteractionRecorder(gw_identity, store)

    # ---- Create mock agents for each framework ----
    frameworks = [
        ("crewai",        CrewAIMock()),
        ("openai_agents", OpenAIAgentsMock()),
        ("autogen",       AutoGenMock()),
        ("langgraph",     LangGraphMock()),
        ("google_adk",    GoogleADKMock()),
        ("elizaos",       ElizaOSMock()),
    ]

    # ---- Build the gateway with all 6 frameworks mounted ----
    gateway = FastMCP("TrustChain Testing Ground")

    for namespace, adapter in frameworks:
        # Register each framework's identity in the registry
        from gateway.config import UpstreamServer
        config = UpstreamServer(
            name=adapter.framework_name,
            command="mock",
            namespace=namespace,
            trust_threshold=0.3,  # All frameworks need 0.3 trust
        )
        identity = registry.register_server(config)

        # Mount their MCP server
        mcp_server = adapter.create_mcp_server()
        gateway.mount(mcp_server, namespace=namespace)

        # Register tool mappings
        tool_names = [f"{namespace}_{t}" for t in adapter.get_tool_names()]
        registry.register_tools_for_server(tool_names, adapter.framework_name)

    # Attach middleware and trust tools
    middleware = TrustChainMiddleware(
        registry=registry,
        recorder=recorder,
        store=store,
        default_threshold=0.3,
        bootstrap_interactions=3,
    )
    gateway.add_middleware(middleware)
    register_trust_tools(gateway, registry, store)

    print("Frameworks registered:")
    for namespace, adapter in frameworks:
        identity = registry.identity_for(adapter.framework_name)
        print(
            f"  {adapter.framework_name:20s} "
            f"namespace={namespace:15s} "
            f"id={identity.short_id}... "
            f"python={'yes' if adapter.is_python_native else 'no (REST bridge)':15s} "
            f"tools={adapter.get_tool_names()}"
        )

    # ---- Phase 1: Initial state — all at zero trust ----
    print_section("Phase 1: Initial Trust Scores (all zero)")

    async with Client(gateway) as client:
        result = await client.call_tool("trustchain_list_servers", {})
        print(result.content[0].text)

    # ---- Phase 2: Bootstrap interactions ----
    print_section("Phase 2: Bootstrap Interactions (building trust)")

    for namespace, adapter in frameworks:
        identity = registry.identity_for(adapter.framework_name)
        # Simulate 4 successful interactions per framework
        for i in range(4):
            tool_name = f"{namespace}_{adapter.get_tool_names()[0]}"
            recorder.record(
                upstream_identity=identity,
                interaction_type=f"tool:{tool_name}",
                outcome="completed",
            )
        trust = compute_trust(identity.pubkey_hex, store)
        count = len(store.get_records_for(identity.pubkey_hex))
        print(f"  {adapter.framework_name:20s} trust={trust:.3f} interactions={count}")

    # ---- Phase 3: Trust comparison across frameworks ----
    print_section("Phase 3: Trust Scores After Bootstrap")

    async with Client(gateway) as client:
        result = await client.call_tool("trustchain_list_servers", {})
        print(result.content[0].text)

    # ---- Phase 4: Call tools through the gateway ----
    print_section("Phase 4: Calling Each Framework Through TrustChain")

    test_calls = [
        ("crewai_research_topic", {"topic": "AI Agent Trust"}),
        ("openai_agents_triage_request", {"user_message": "I need a refund"}),
        ("autogen_group_chat", {"task": "Design a trust protocol"}),
        ("langgraph_react_agent_run", {"query": "What is TrustChain?"}),
        ("google_adk_a2a_send_task", {"agent_url": "http://agent.example.com", "task": "Verify identity"}),
        ("elizaos_eliza_message", {"content": "Hello from TrustChain!"}),
    ]

    async with Client(gateway) as client:
        for tool_name, args in test_calls:
            try:
                result = await client.call_tool(tool_name, args)
                text = result.content[0].text
                # Show first 2 lines + trust annotation
                lines = text.strip().split("\n")
                preview = "\n    ".join(lines[:2])
                # Find trust annotation
                trust_line = [l for l in lines if "[TrustChain]" in l]
                print(f"  {tool_name}:")
                print(f"    {preview}")
                if trust_line:
                    print(f"    {trust_line[0].strip()}")
                print()
            except Exception as e:
                print(f"  {tool_name}: ERROR - {e}\n")

    # ---- Phase 5: Simulate a bad actor ----
    print_section("Phase 5: Bad Actor Simulation")

    # Add some failed interactions for one framework
    eliza_identity = registry.identity_for("ElizaOS")
    for _ in range(5):
        recorder.record(
            upstream_identity=eliza_identity,
            interaction_type="tool:elizaos_eliza_message",
            outcome="failed",
        )
    eliza_trust = compute_trust(eliza_identity.pubkey_hex, store)
    print(f"  ElizaOS after 5 failed interactions: trust={eliza_trust:.3f}")

    # Meanwhile, a reliable framework keeps succeeding
    crewai_identity = registry.identity_for("CrewAI")
    for _ in range(10):
        recorder.record(
            upstream_identity=crewai_identity,
            interaction_type="tool:crewai_research_topic",
            outcome="completed",
        )
    crewai_trust = compute_trust(crewai_identity.pubkey_hex, store)
    print(f"  CrewAI after 10 more successes:      trust={crewai_trust:.3f}")

    print()
    print("  Trust divergence demonstrates that TrustChain tracks")
    print("  reliability per-framework based on actual outcomes.")

    # ---- Phase 6: Final trust dashboard ----
    print_section("Phase 6: Final Trust Dashboard")

    async with Client(gateway) as client:
        result = await client.call_tool("trustchain_list_servers", {})
        print(result.content[0].text)

    # ---- Summary statistics ----
    print_section("Summary")

    total_records = len(store.records)
    unique_servers = len(set(
        r.agent_b_pubkey for r in store.records
    ))
    completed = sum(1 for r in store.records if r.outcome == "completed")
    failed = sum(1 for r in store.records if r.outcome == "failed")

    print(f"  Total bilateral records:  {total_records}")
    print(f"  Unique upstream servers:  {unique_servers}")
    print(f"  Completed interactions:   {completed}")
    print(f"  Failed interactions:      {failed}")
    print(f"  All records bilaterally signed with Ed25519")
    print()
    print("  Frameworks covered:")
    print("    Python-native: CrewAI, OpenAI Agents, AutoGen/AG2, LangGraph, Google ADK")
    print("    REST bridge:   ElizaOS (TypeScript)")
    print()
    print("  Key insight: NONE of these frameworks have built-in trust.")
    print("  TrustChain adds it uniformly beneath ALL of them.")

    print_header("Testing Ground Complete")


if __name__ == "__main__":
    asyncio.run(main())
