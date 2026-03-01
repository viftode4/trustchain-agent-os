"""Real OpenAI Agents SDK adapter — wraps an OpenAI agent as a trust-gated MCP server.

Requires: pip install openai-agents

Usage:
    from tc_frameworks.adapters.openai_agents_adapter import OpenAIAgentsAdapter

    adapter = OpenAIAgentsAdapter(
        agent_name="assistant",
        instructions="You are a helpful assistant.",
        tools=[my_function_tool],
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class OpenAIAgentsAdapter(FrameworkAdapter):
    """Wraps a real OpenAI Agents SDK agent as a FastMCP server.

    The agent becomes callable through MCP, and TrustChain
    trust-verifies every invocation.
    """

    framework_name = "OpenAI Agents SDK"
    framework_version = "0.10.1"

    def __init__(
        self,
        agent_name: str = "assistant",
        instructions: str = "You are a helpful assistant.",
        tools: Optional[List[Callable]] = None,
        model: Any = "gpt-4o-mini",
    ):
        self.agent_name = agent_name
        self.instructions = instructions
        self.tools = tools or []
        self.model = model
        self._agent = None  # Cached agent instance

    def _build_agent(self):
        """Build an OpenAI Agent from config."""
        from agents import Agent, function_tool

        agent_tools = []
        for tool_fn in self.tools:
            if not hasattr(tool_fn, "__wrapped__"):
                tool_fn = function_tool(tool_fn)
            agent_tools.append(tool_fn)

        return Agent(
            name=self.agent_name,
            instructions=self.instructions,
            tools=agent_tools,
            model=self.model,
        )

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("OpenAI Agent (Real)")
        adapter = self

        @mcp.tool(name="agent_run")
        async def agent_run(message: str) -> str:
            """Run a message through the OpenAI agent."""
            from agents import Runner
            if adapter._agent is None:
                adapter._agent = adapter._build_agent()
            result = await Runner.run(adapter._agent, message)
            return str(result.final_output)

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["agent_run"]
