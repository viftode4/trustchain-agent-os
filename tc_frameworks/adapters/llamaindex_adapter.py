"""Real LlamaIndex adapter — wraps a LlamaIndex ReAct agent as a trust-gated MCP server.

Requires: pip install llama-index llama-index-llms-gemini  (or llama-index-llms-openai)

Usage:
    from tc_frameworks.adapters.llamaindex_adapter import LlamaIndexAdapter

    adapter = LlamaIndexAdapter(
        model="models/gemini-2.5-flash-lite",
        provider="google",
        api_key="your-key",
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class LlamaIndexAdapter(FrameworkAdapter):
    """Wraps a real LlamaIndex ReAct agent as a FastMCP server."""

    framework_name = "LlamaIndex"
    framework_version = "0.14.3"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        provider: str = "openai",
        tools: Optional[List] = None,
        system_prompt: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.model = model
        self.provider = provider
        self.tools = tools or []
        self.system_prompt = system_prompt
        self.api_key = api_key
        self._agent = None  # Cached agent instance

    def _build_agent(self):
        """Build a LlamaIndex ReAct agent."""
        from llama_index.core.agent import ReActAgent
        from llama_index.core.tools import FunctionTool

        if self.provider == "google":
            from llama_index.llms.gemini import Gemini
            llm_kwargs: Dict[str, Any] = {"model": self.model}
            if self.api_key:
                llm_kwargs["api_key"] = self.api_key
            llm = Gemini(**llm_kwargs)
        else:
            from llama_index.llms.openai import OpenAI
            llm_kwargs = {"model": self.model}
            if self.api_key:
                llm_kwargs["api_key"] = self.api_key
            llm = OpenAI(**llm_kwargs)

        li_tools = []
        for tool_fn in self.tools:
            li_tools.append(FunctionTool.from_defaults(fn=tool_fn))

        kwargs: Dict[str, Any] = {
            "llm": llm, "tools": li_tools, "verbose": False,
        }
        if self.system_prompt:
            kwargs["system_prompt"] = self.system_prompt
        return ReActAgent(**kwargs)

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("LlamaIndex Agent (Real)")
        adapter = self

        @mcp.tool(name="llamaindex_chat")
        async def llamaindex_chat(message: str) -> str:
            """Run a message through the LlamaIndex ReAct agent."""
            if adapter._agent is None:
                adapter._agent = adapter._build_agent()
            handler = adapter._agent.run(user_msg=message)
            response = await handler
            return str(response)

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["llamaindex_chat"]
