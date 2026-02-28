"""Real Google ADK adapter — wraps a Google ADK agent as a trust-gated MCP server.

Requires: pip install google-adk

Usage:
    from tc_frameworks.adapters.google_adk_adapter import GoogleADKAdapter

    adapter = GoogleADKAdapter(
        agent_name="assistant",
        model="gemini-2.0-flash",
        instruction="You are a helpful assistant.",
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


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
        # Cached session infrastructure for conversation continuity
        self._runner = None
        self._session_service = None
        self._session_id: Optional[str] = None
        self._init_lock = asyncio.Lock()

    def _build_agent(self):
        """Build a Google ADK LlmAgent."""
        from google.adk.agents import LlmAgent
        return LlmAgent(
            name=self.agent_name,
            model=self.model,
            instruction=self.instruction,
            tools=self.tools,
        )

    async def _ensure_session(self):
        """Lazily create runner and session, reusing across tool calls.

        Uses asyncio.Lock to prevent duplicate initialization when
        multiple tool calls arrive concurrently.
        """
        async with self._init_lock:
            if self._runner is None:
                from google.adk.runners import Runner
                from google.adk.sessions import InMemorySessionService

                agent = self._build_agent()
                self._session_service = InMemorySessionService()
                self._runner = Runner(
                    agent=agent,
                    app_name="trustchain",
                    session_service=self._session_service,
                )
            if self._session_id is None:
                session = await self._session_service.create_session(
                    app_name="trustchain", user_id="trustchain-gateway"
                )
                self._session_id = session.id
        return self._runner, self._session_id

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("Google ADK Agent (Real)")
        adapter = self

        @mcp.tool(name="adk_invoke")
        async def adk_invoke(message: str) -> str:
            """Run a message through the Google ADK agent."""
            from google.genai import types

            runner, session_id = await adapter._ensure_session()
            content = types.Content(
                role="user", parts=[types.Part(text=message)]
            )
            responses = []
            async for event in runner.run_async(
                user_id="trustchain-gateway",
                session_id=session_id,
                new_message=content,
            ):
                if event.content and event.content.parts:
                    responses.append(event.content.parts[0].text)
            return "\n".join(responses) if responses else "No response"

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["adk_invoke"]
