"""Demo: TrustChain MCP Gateway with mock upstream servers.

This demo creates a gateway with two mock upstream MCP servers,
shows how trust builds over tool calls, and demonstrates trust-gating.

Run:
    cd G:/Projects/blockchains/trustchain-agent-os
    python examples/demo_gateway.py
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import FastMCP, Client

from trustchain.identity import Identity
from trustchain.store import RecordStore
from trustchain.trust import compute_trust

from gateway.config import GatewayConfig, UpstreamServer
from gateway.recorder import InteractionRecorder
from gateway.registry import UpstreamRegistry
from gateway.trust_tools import register_trust_tools


def create_mock_upstream(name: str) -> FastMCP:
    """Create a mock upstream MCP server for demo purposes."""
    server = FastMCP(name)

    @server.tool(name="read_file")
    async def read_file(path: str) -> str:
        """Read a file from the filesystem."""
        return f"[mock] Contents of {path}: Hello, TrustChain!"

    @server.tool(name="write_file")
    async def write_file(path: str, content: str) -> str:
        """Write content to a file."""
        return f"[mock] Wrote {len(content)} bytes to {path}"

    @server.tool(name="list_files")
    async def list_files(directory: str = ".") -> str:
        """List files in a directory."""
        return f"[mock] Files in {directory}: README.md, main.py, config.json"

    return server


async def main():
    print("=" * 60)
    print("  TrustChain MCP Gateway — Demo")
    print("=" * 60)
    print()

    # Setup
    store = RecordStore()
    gw_identity = Identity()
    registry = UpstreamRegistry(gw_identity)
    recorder = InteractionRecorder(gw_identity, store)

    # Register mock upstream
    fs_config = UpstreamServer(
        name="filesystem",
        command="echo",  # Won't actually run
        namespace="fs",
        trust_threshold=0.3,
    )
    fs_identity_obj = registry.register_server(fs_config)
    fs_pubkey = fs_identity_obj.pubkey_hex

    # Create gateway FastMCP with trust tools
    gateway = FastMCP("TrustChain Gateway Demo")
    register_trust_tools(gateway, registry, store)

    # Mount mock upstream
    mock_fs = create_mock_upstream("filesystem")
    gateway.mount(mock_fs, namespace="fs")

    # Register tool mappings
    registry.register_tools_for_server(
        ["fs_read_file", "fs_write_file", "fs_list_files"], "filesystem"
    )

    print(f"Gateway ID:     {gw_identity.short_id}...")
    print(f"Filesystem ID:  {fs_identity_obj.short_id}...")
    print()

    # Simulate interactions
    print("--- Phase 1: Bootstrap (trust building) ---")
    print()

    for i in range(5):
        record = recorder.record(
            upstream_identity=fs_identity_obj,
            interaction_type=f"tool:fs_read_file",
            outcome="completed",
        )
        trust = compute_trust(fs_pubkey, store)
        count = len(store.get_records_for(fs_pubkey))
        print(
            f"  Interaction {i+1}: trust={trust:.3f} "
            f"interactions={count} "
            f"hash={record.record_hash[:12]}..."
        )

    print()
    trust = compute_trust(fs_pubkey, store)
    print(f"Final trust score: {trust:.3f}")
    print()

    # Show trust tool output
    print("--- Phase 2: Trust Query Tools ---")
    print()

    async with Client(gateway) as client:
        # List servers
        result = await client.call_tool("trustchain_list_servers", {})
        print("trustchain_list_servers:")
        for block in result.content:
            print(f"  {block.text}")
        print()

        # Check trust
        result = await client.call_tool(
            "trustchain_check_trust", {"server_name": "filesystem"}
        )
        print("trustchain_check_trust('filesystem'):")
        for block in result.content:
            print(f"  {block.text}")
        print()

        # Get history
        result = await client.call_tool(
            "trustchain_get_history", {"server_name": "filesystem", "limit": 3}
        )
        print("trustchain_get_history('filesystem', limit=3):")
        for block in result.content:
            print(f"  {block.text}")
        print()

        # Call a mounted tool
        print("--- Phase 3: Calling Mounted Tool ---")
        print()
        result = await client.call_tool(
            "fs_read_file", {"path": "/home/user/data.txt"}
        )
        print("fs_read_file('/home/user/data.txt'):")
        for block in result.content:
            print(f"  {block.text}")

    print()
    print("=" * 60)
    print("  Demo complete! Gateway works with trust-gated tool calls.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
