"""Upstream server identity mapping and tool routing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from trustchain.identity import Identity

from gateway.config import UpstreamServer

logger = logging.getLogger("trustchain.gateway.registry")


class UpstreamRegistry:
    """Maps upstream server names to TrustChain identities and routes tools.

    v2: Also tracks TrustChain node URLs for direct protocol communication.

    When identity_dir is provided, upstream identities are persisted to disk
    so that they survive gateway restarts. Without this, every restart
    generates new identities, invalidating all historical trust records.
    """

    def __init__(
        self,
        gateway_identity: Identity,
        identity_dir: Optional[str] = None,
    ):
        self.gateway_identity = gateway_identity
        self._identity_dir = Path(identity_dir) if identity_dir else None
        self._server_identities: Dict[str, Identity] = {}
        self._server_configs: Dict[str, UpstreamServer] = {}
        self._tool_to_server: Dict[str, str] = {}
        self._trustchain_urls: Dict[str, str] = {}  # v2: server_name -> trustchain URL

        if self._identity_dir:
            self._identity_dir.mkdir(parents=True, exist_ok=True)

    def _load_or_create_identity(self, server_name: str) -> Identity:
        """Load a persisted identity for a server, or create and save a new one.

        If the key file exists but is corrupt/unreadable, it is regenerated
        with a warning. Trust history for the old identity is lost, but the
        gateway stays up. This is the right trade-off — a crashed gateway
        serves nobody.
        """
        if self._identity_dir:
            key_path = self._identity_dir / f"{server_name}.key"
            if key_path.exists():
                try:
                    identity = Identity.load(str(key_path))
                    logger.debug("Loaded identity for '%s' from %s", server_name, key_path)
                    return identity
                except Exception as e:
                    logger.warning(
                        "Corrupt key file for '%s' at %s (%s) — regenerating",
                        server_name, key_path, e,
                    )
            identity = Identity()
            identity.save(str(key_path))
            logger.debug("Created and saved identity for '%s' at %s", server_name, key_path)
            return identity
        return Identity()

    def register_server(self, config: UpstreamServer) -> Identity:
        """Register an upstream server with a persistent TrustChain identity."""
        identity = self._load_or_create_identity(config.name)
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
