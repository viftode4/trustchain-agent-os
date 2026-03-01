"""Real Microsoft Semantic Kernel adapter — wraps a Semantic Kernel agent as a trust-gated MCP server.

Requires: pip install semantic-kernel

Usage:
    from tc_frameworks.adapters.semantic_kernel_adapter import SemanticKernelAdapter

    adapter = SemanticKernelAdapter(
        service_id="chat",
        model="gemini-2.5-flash-lite",
        provider="google",
        api_key="your-key",
    )
    mcp_server = adapter.create_mcp_server()
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from tc_frameworks.base import FrameworkAdapter


class SemanticKernelAdapter(FrameworkAdapter):
    """Wraps a real Microsoft Semantic Kernel agent as a FastMCP server."""

    framework_name = "Semantic Kernel"
    framework_version = "1.35.0"

    def __init__(
        self,
        service_id: str = "chat",
        model: str = "gpt-4o-mini",
        provider: str = "openai",
        plugins: Optional[List] = None,
        api_key: Optional[str] = None,
    ):
        self.service_id = service_id
        self.model = model
        self.provider = provider
        self.plugins = plugins or []
        self.api_key = api_key
        self._kernel = None  # Cached kernel instance

    def _build_kernel(self):
        """Build a Semantic Kernel with chat completion service."""
        import semantic_kernel as sk

        kernel = sk.Kernel()

        if self.provider == "google":
            from semantic_kernel.connectors.ai.google.google_ai import (
                GoogleAIChatCompletion,
            )
            kwargs: Dict[str, Any] = {
                "gemini_model_id": self.model,
                "service_id": self.service_id,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            kernel.add_service(GoogleAIChatCompletion(**kwargs))
        else:
            from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
            kwargs = {
                "service_id": self.service_id,
                "ai_model_id": self.model,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            kernel.add_service(OpenAIChatCompletion(**kwargs))

        for plugin in self.plugins:
            kernel.add_plugin(plugin)
        return kernel

    def create_mcp_server(self) -> FastMCP:
        mcp = FastMCP("Semantic Kernel Agent (Real)")
        adapter = self

        @mcp.tool(name="kernel_invoke")
        async def kernel_invoke(message: str) -> str:
            """Run a message through the Semantic Kernel agent."""
            from semantic_kernel.connectors.ai.prompt_execution_settings import (
                PromptExecutionSettings,
            )
            from semantic_kernel.contents import ChatHistory

            if adapter._kernel is None:
                adapter._kernel = adapter._build_kernel()
            chat_service = adapter._kernel.get_service(adapter.service_id)
            history = ChatHistory()
            history.add_user_message(message)
            settings = PromptExecutionSettings(service_id=adapter.service_id)
            result = await chat_service.get_chat_message_contents(
                history, settings,
            )
            return str(result[0]) if result else "No response"

        return mcp

    def get_tool_names(self) -> List[str]:
        return ["kernel_invoke"]
