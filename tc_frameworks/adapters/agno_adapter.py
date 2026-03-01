"""Real Agno (ex-Phidata) adapter — wraps an Agno agent as a trust-gated MCP server.

Requires: pip install agno

Usage:
    from tc_frameworks.adapters.agno_adapter import AgnoAdapter

    adapter = AgnoAdapter(
        agent_name="assistant",
        model_provider="google",
        model_id="gemini-2.5-flash-lite",
        api_key="your-key",
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class AgnoAdapter(FrameworkAdapter):
    """Wraps a real Agno (ex-Phidata) agent as a FastMCP server."""

    framework_name = "Agno"
    framework_version = "2.5.5"

    def __init__(
        self,
        agent_name: str = "assistant",
        model_provider: str = "openai",
        model_id: str = "gpt-4o-mini",
        instructions: str = "You are a helpful assistant.",
        tools: Optional[List] = None,
        api_key: Optional[str] = None,
    ):
        self.agent_name = agent_name
        self.model_provider = model_provider
        self.model_id = model_id
        self.instructions = instructions
        self.tools = tools or []
        self.api_key = api_key
        self._agent = None  # Cached agent instance

    def _build_agent(self):
        """Build an Agno agent."""
        from agno.agent import Agent

        if self.model_provider == "google":
            from agno.models.google import Gemini
            model = Gemini(id=self.model_id, api_key=self.api_key)
        else:
            from agno.models.openai import OpenAIChat
            model = OpenAIChat(id=self.model_id, api_key=self.api_key)

        return Agent(
            name=self.agent_name,
            model=model,
            instructions=[self.instructions],
            tools=self.tools,
        )

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("Agno Agent (Real)")
        adapter = self

        @mcp.tool(name="agno_run")
        async def agno_run(message: str) -> str:
            """Run a message through the Agno agent."""
            import asyncio

            if adapter._agent is None:
                adapter._agent = adapter._build_agent()
            response = await asyncio.to_thread(
                adapter._agent.run, message, stream=False,
            )
            return response.content

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["agno_run"]
