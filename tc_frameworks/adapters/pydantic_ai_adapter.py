"""Real PydanticAI adapter — wraps a PydanticAI agent as a trust-gated MCP server.

Requires: pip install pydantic-ai

Usage:
    from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter

    adapter = PydanticAIAdapter(
        model="google-gla:gemini-2.5-flash-lite",
        system_prompt="You are a helpful assistant.",
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class PydanticAIAdapter(FrameworkAdapter):
    """Wraps a real PydanticAI agent as a FastMCP server."""

    framework_name = "PydanticAI"
    framework_version = "1.31.0"

    def __init__(
        self,
        model: str = "openai:gpt-4o-mini",
        system_prompt: str = "You are a helpful assistant.",
        tools: Optional[List[Callable]] = None,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self._agent = None  # Cached agent instance

    def _build_agent(self):
        """Build a PydanticAI agent."""
        from pydantic_ai import Agent

        agent = Agent(
            model=self.model,
            system_prompt=self.system_prompt,
        )
        for tool_fn in self.tools:
            agent.tool_plain(tool_fn)
        return agent

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("PydanticAI Agent (Real)")
        adapter = self

        @mcp.tool(name="pydantic_ai_run")
        async def pydantic_ai_run(message: str) -> str:
            """Run a message through the PydanticAI agent."""
            if adapter._agent is None:
                adapter._agent = adapter._build_agent()
            result = await adapter._agent.run(message)
            return str(result.output)

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["pydantic_ai_run"]
