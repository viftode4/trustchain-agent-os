"""Real Anthropic Claude adapter — wraps a Claude agent as a trust-gated MCP server.

Requires: pip install anthropic

Usage:
    from tc_frameworks.adapters.claude_agent_adapter import ClaudeAgentAdapter

    adapter = ClaudeAgentAdapter(
        model="claude-sonnet-4-20250514",
        instructions="You are a helpful assistant.",
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class ClaudeAgentAdapter(FrameworkAdapter):
    """Wraps a real Anthropic Claude model as a FastMCP server."""

    framework_name = "Claude (Anthropic)"
    framework_version = "0.49.0"

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        instructions: str = "You are a helpful assistant.",
        max_tokens: int = 1024,
        api_key: Optional[str] = None,
    ):
        self.model = model
        self.instructions = instructions
        self.max_tokens = max_tokens
        self.api_key = api_key
        self._client = None  # Cached client instance

    def _build_client(self):
        """Build an Anthropic client."""
        import anthropic

        kwargs: Dict[str, Any] = {}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return anthropic.Anthropic(**kwargs)

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("Claude Agent (Real)")
        adapter = self

        @mcp.tool(name="claude_query")
        async def claude_query(message: str) -> str:
            """Run a message through the Anthropic Claude model."""
            import asyncio

            if adapter._client is None:
                adapter._client = adapter._build_client()

            def _call():
                response = adapter._client.messages.create(
                    model=adapter.model,
                    max_tokens=adapter.max_tokens,
                    system=adapter.instructions,
                    messages=[{"role": "user", "content": message}],
                )
                return response.content[0].text

            return await asyncio.to_thread(_call)

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["claude_query"]
