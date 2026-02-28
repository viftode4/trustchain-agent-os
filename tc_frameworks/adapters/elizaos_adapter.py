"""Real ElizaOS adapter — bridges ElizaOS (TypeScript) via REST API.

Requires: ElizaOS running as a service (npm install -g @elizaos/cli && elizaos start)

Usage:
    from tc_frameworks.adapters.elizaos_adapter import ElizaOSAdapter

    adapter = ElizaOSAdapter(base_url="http://localhost:3000")
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class ElizaOSAdapter(FrameworkAdapter):
    """Bridges a running ElizaOS instance via REST API into TrustChain.

    ElizaOS is TypeScript-first, so we communicate over HTTP.
    This adapter calls the ElizaOS REST API and exposes it as MCP tools.
    """

    framework_name = "ElizaOS"
    framework_version = "1.7.2"

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        agent_id: Optional[str] = None,
        server_id: str = "trustchain",
    ):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.server_id = server_id

    @property
    def has_native_mcp(self) -> bool:
        return False

    @property
    def is_python_native(self) -> bool:
        return False

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("ElizaOS Agent (Real)")
        adapter = self

        @mcp.tool(name="eliza_send_message")
        async def eliza_send_message(
            content: str,
            room_id: str = "default",
            user_id: str = "trustchain-gateway",
        ) -> str:
            """Send a message to an ElizaOS agent via REST API."""
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{adapter.base_url}/api/messaging/submit",
                    json={
                        "content": content,
                        "channel_id": room_id,
                        "server_id": adapter.server_id,
                        "author_id": user_id,
                        "source_type": "api",
                        "raw_message": {"text": content},
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                return str(resp.json())

        @mcp.tool(name="eliza_list_agents")
        async def eliza_list_agents() -> str:
            """List all agents running in the ElizaOS instance."""
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{adapter.base_url}/api/agents",
                    timeout=10.0,
                )
                resp.raise_for_status()
                return str(resp.json())

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["eliza_send_message", "eliza_list_agents"]
