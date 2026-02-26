"""Upstream server identity mapping and tool routing."""

from __future__ import annotations

from typing import Dict, Optional

from trustchain.identity import Identity

from gateway.config import UpstreamServer


class UpstreamRegistry:
    """Maps upstream server names to TrustChain identities and routes tools.

    v2: Also tracks TrustChain node URLs for direct protocol communication.
    """

    def __init__(self, gateway_identity: Identity):
        self.gateway_identity = gateway_identity
        self._server_identities: Dict[str, Identity] = {}
        self._server_configs: Dict[str, UpstreamServer] = {}
        self._tool_to_server: Dict[str, str] = {}
        self._trustchain_urls: Dict[str, str] = {}  # v2: server_name -> trustchain URL

    def register_server(self, config: UpstreamServer) -> Identity:
        """Register an upstream server and generate its TrustChain identity."""
        identity = Identity()
        self._server_identities[config.name] = identity
        self._server_configs[config.name] = config
        if config.trustchain_url:
            self._trustchain_urls[config.name] = config.trustchain_url
        return identity

    def register_upstream(
        self, name: str, url: str, trustchain_url: str
    ) -> Identity:
        """Register an upstream with explicit TrustChain node URL (v2).

        All participants must be TrustChain-aware — this registers both
        the MCP endpoint and the TrustChain protocol endpoint.
        """
        config = UpstreamServer(name=name, command="", url=url, trustchain_url=trustchain_url)
        identity = self.register_server(config)
        return identity

    def trustchain_url_for(self, server_name: str) -> Optional[str]:
        """Get the TrustChain node URL for an upstream server."""
        return self._trustchain_urls.get(server_name)

    def register_tool(self, tool_name: str, server_name: str):
        """Map a tool name to its upstream server."""
        self._tool_to_server[tool_name] = server_name

    def register_tools_for_server(self, tool_names: list[str], server_name: str):
        """Map multiple tools to their upstream server."""
        for name in tool_names:
            self._tool_to_server[name] = server_name

    def server_for_tool(self, tool_name: str) -> Optional[str]:
        """Look up which upstream server owns a tool.

        First checks the explicit mapping, then falls back to namespace prefix matching.
        """
        if tool_name in self._tool_to_server:
            return self._tool_to_server[tool_name]
        # Namespace prefix matching: "fs_read_file" -> server with namespace "fs"
        for name, config in self._server_configs.items():
            prefix = config.namespace + "_"
            if tool_name.startswith(prefix):
                return name
        return None

    def identity_for(self, server_name: str) -> Optional[Identity]:
        """Get the TrustChain identity for an upstream server."""
        return self._server_identities.get(server_name)

    def config_for(self, server_name: str) -> Optional[UpstreamServer]:
        """Get the configuration for an upstream server."""
        return self._server_configs.get(server_name)

    def threshold_for(self, server_name: str, default: float = 0.0) -> float:
        """Get the trust threshold for a server."""
        config = self._server_configs.get(server_name)
        if config is not None:
            return config.trust_threshold
        return default

    @property
    def server_names(self) -> list[str]:
        return list(self._server_configs.keys())
