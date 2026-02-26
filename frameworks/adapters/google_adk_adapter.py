"""Real Google ADK adapter — wraps a Google ADK agent as a trust-gated MCP server.

Requires: pip install google-adk

Usage:
    from frameworks.adapters.google_adk_adapter import GoogleADKAdapter

    adapter = GoogleADKAdapter(
        agent_name="assistant",
        model="gemini-2.0-flash",
        instruction="You are a helpful assistant.",
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from frameworks.base import FrameworkAdapter


class GoogleADKAdapter(FrameworkAdapter):
    """Wraps a real Google ADK agent as a FastMCP server."""

    framework_name = "Google ADK"
    framework_version = "1.25.1"

    def __init__(
        self,
        agent_name: str = "assistant",
        model: str = "gemini-2.0-flash",
        instruction: str = "You are a helpful assistant.",
        tools: Optional[List[Callable]] = None,
    ):
        self.agent_name = agent_name
        self.model = model
        self.instruction = instruction
        self.tools = tools or []

    def _build_agent(self):
        """Build a Google ADK LlmAgent."""
        from google.adk.agents import LlmAgent
        return LlmAgent(
            name=self.agent_name,
            model=self.model,
            instruction=self.instruction,
            tools=self.tools,
        )

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("Google ADK Agent (Real)")
        adapter = self

        @mcp.tool(name="adk_invoke")
        async def adk_invoke(message: str) -> str:
            """Run a message through the Google ADK agent."""
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
            from google.genai import types

            agent = adapter._build_agent()
            session_service = InMemorySessionService()
            runner = Runner(
                agent=agent,
                app_name="trustchain",
                session_service=session_service,
            )
            session = await session_service.create_session(
                app_name="trustchain", user_id="trustchain-gateway"
            )
            content = types.Content(
                role="user", parts=[types.Part(text=message)]
            )
            responses = []
            async for event in runner.run_async(
                user_id="trustchain-gateway",
                session_id=session.id,
                new_message=content,
            ):
                if event.content and event.content.parts:
                    responses.append(event.content.parts[0].text)
            return "\n".join(responses) if responses else "No response"

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["adk_invoke"]
