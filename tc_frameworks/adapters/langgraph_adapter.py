"""Real LangGraph adapter — wraps a LangGraph agent as a trust-gated MCP server.

Requires: pip install langgraph langchain-openai  (or langchain-anthropic, langchain-google-genai)

Usage:
    from tc_frameworks.adapters.langgraph_adapter import LangGraphAdapter
    from langchain_core.tools import tool

    @tool
    def search(query: str) -> str:
        return f"Results for {query}"

    adapter = LangGraphAdapter(tools=[search], model_provider="google",
                               model_name="gemini-2.5-flash-lite")
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class LangGraphAdapter(FrameworkAdapter):
    """Wraps a real LangGraph ReAct agent as a FastMCP server."""

    framework_name = "LangGraph"
    framework_version = "1.0.10"

    def __init__(
        self,
        tools: Optional[List] = None,
        model_name: str = "gpt-4o-mini",
        model_provider: str = "openai",
        api_key: Optional[str] = None,
    ):
        self.tools = tools or []
        self.model_name = model_name
        self.model_provider = model_provider
        self.api_key = api_key
        self._agent = None  # Cached agent graph

    def _build_agent(self):
        """Build a LangGraph ReAct agent."""
        from langgraph.prebuilt import create_react_agent

        if self.model_provider == "openai":
            from langchain_openai import ChatOpenAI
            model = ChatOpenAI(model=self.model_name)
        elif self.model_provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            model = ChatAnthropic(model=self.model_name)
        elif self.model_provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            kwargs: Dict[str, Any] = {"model": self.model_name}
            if self.api_key:
                kwargs["google_api_key"] = self.api_key
            model = ChatGoogleGenerativeAI(**kwargs)
        else:
            raise ValueError(f"Unsupported model provider: {self.model_provider}")

        return create_react_agent(model=model, tools=self.tools)

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("LangGraph Agent (Real)")
        adapter = self

        @mcp.tool(name="react_agent_invoke")
        async def react_agent_invoke(message: str) -> str:
            """Run a message through the LangGraph ReAct agent."""
            if adapter._agent is None:
                adapter._agent = adapter._build_agent()
            result = await adapter._agent.ainvoke({
                "messages": [{"role": "user", "content": message}]
            })
            messages = result.get("messages", [])
            if messages:
                return str(messages[-1].content)
            return "No response"

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["react_agent_invoke"]
