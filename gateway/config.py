"""Gateway configuration dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class UpstreamServer:
    """Configuration for a single upstream MCP server."""

    name: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    namespace: str = ""
    trust_threshold: float = 0.0
    url: Optional[str] = None
    trustchain_url: Optional[str] = None  # v2: TrustChain node endpoint

    def __post_init__(self):
        if not self.namespace:
            self.namespace = self.name


@dataclass
class GatewayConfig:
    """Configuration for the TrustChain MCP Gateway."""

    upstreams: List[UpstreamServer] = field(default_factory=list)
    identity_path: Optional[str] = None
    store_path: Optional[str] = None
    upstream_identity_dir: Optional[str] = None
    default_trust_threshold: float = 0.0
    bootstrap_interactions: int = 3
    server_name: str = "TrustChain Gateway"
