"""Real HuggingFace Smolagents adapter — wraps a Smolagents agent as a trust-gated MCP server.

Requires: pip install smolagents

Usage:
    from tc_frameworks.adapters.smolagents_adapter import SmolagentsAdapter

    adapter = SmolagentsAdapter(
        model_id="gemini/gemini-2.5-flash-lite",
        model_type="litellm",
        api_key="your-key",
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class SmolagentsAdapter(FrameworkAdapter):
    """Wraps a real HuggingFace Smolagents agent as a FastMCP server."""

    framework_name = "Smolagents"
    framework_version = "1.24.0"

    def __init__(
        self,
        model_id: str = "Qwen/Qwen2.5-Coder-32B-Instruct",
        tools: Optional[List] = None,
        agent_type: str = "code",
        model_type: str = "hf",
        api_key: Optional[str] = None,
    ):
        self.model_id = model_id
        self.tools = tools or []
        self.agent_type = agent_type
        self.model_type = model_type
        self.api_key = api_key
        self._agent = None  # Cached agent instance

    def _build_agent(self):
        """Build a Smolagents agent."""
        from smolagents import CodeAgent, ToolCallingAgent

        if self.model_type == "litellm":
            from smolagents import LiteLLMModel
            model = LiteLLMModel(model_id=self.model_id, api_key=self.api_key)
        else:
            from smolagents import HfApiModel
            model = HfApiModel(model_id=self.model_id, token=self.api_key)

        agent_cls = CodeAgent if self.agent_type == "code" else ToolCallingAgent
        return agent_cls(tools=self.tools, model=model)

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("Smolagents (Real)")
        adapter = self

        @mcp.tool(name="smolagent_run")
        async def smolagent_run(message: str) -> str:
            """Run a task through the HuggingFace Smolagents agent."""
            import asyncio

            if adapter._agent is None:
                adapter._agent = adapter._build_agent()
            # smolagents.run() is synchronous
            result = await asyncio.to_thread(adapter._agent.run, message)
            return str(result)

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["smolagent_run"]
