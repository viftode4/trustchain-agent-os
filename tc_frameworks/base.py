"""Base adapter interface for framework integration with TrustChain."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP


class FrameworkAdapter(ABC):
    """Common interface for integrating any agent framework with TrustChain.

    Each adapter wraps a framework-specific agent as a FastMCP server,
    which can then be mounted in the TrustChain gateway for trust-gated access.

    The key insight: TrustChain doesn't care WHICH framework built the agent.
    It only cares about the bilateral interaction history.
    """

    framework_name: str = "unknown"
    framework_version: str = "unknown"

    @abstractmethod
    def create_mcp_server(self) -> FastMCP:
        """Create a FastMCP server exposing this framework's agent capabilities.

        The returned server can be mounted in the TrustChain gateway:
            gateway.mount(adapter.create_mcp_server(), namespace="framework_name")
        """
        ...

    @abstractmethod
    def get_tool_names(self) -> List[str]:
        """Return the list of tool names this adapter exposes."""
        ...

    @property
    def info(self) -> Dict[str, Any]:
        """Return metadata about this framework adapter."""
        return {
            "framework": self.framework_name,
            "version": self.framework_version,
            "tools": self.get_tool_names(),
            "mcp_support": self.has_native_mcp,
            "python_native": self.is_python_native,
        }

    @property
    def has_native_mcp(self) -> bool:
        """Whether the framework has built-in MCP support."""
        return True

    @property
    def is_python_native(self) -> bool:
        """Whether the framework has a native Python SDK."""
        return True
