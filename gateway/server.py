"""FastMCP gateway entrypoint — wires everything together."""

from __future__ import annotations

import logging
from typing import Optional

from fastmcp import FastMCP
from fastmcp.server import create_proxy

from trustchain.identity import Identity
from trustchain.store import FileRecordStore, RecordStore

from gateway.config import GatewayConfig, UpstreamServer
from gateway.middleware import TrustChainMiddleware
from gateway.recorder import InteractionRecorder
from gateway.registry import UpstreamRegistry
from gateway.trust_tools import register_trust_tools

logger = logging.getLogger("trustchain.gateway")


def create_gateway(
    config: GatewayConfig,
    store: Optional[RecordStore] = None,
) -> FastMCP:
    """Create a TrustChain MCP Gateway from configuration.

    The gateway:
    1. Creates/loads its own TrustChain identity
    2. Generates identities for each upstream server
    3. Mounts upstream servers as proxied backends
    4. Attaches TrustChainMiddleware for trust-gating
    5. Registers native trust query tools
    """
    # Gateway identity
    if config.identity_path:
        try:
            gateway_identity = Identity.load(config.identity_path)
            logger.info("Loaded gateway identity from %s", config.identity_path)
        except FileNotFoundError:
            gateway_identity = Identity()
            gateway_identity.save(config.identity_path)
            logger.info("Created new gateway identity at %s", config.identity_path)
    else:
        gateway_identity = Identity()

    # Record store
    if store is None:
        if config.store_path:
            store = FileRecordStore(config.store_path)
        else:
            store = RecordStore()

    # Registry (with optional identity persistence)
    registry = UpstreamRegistry(
        gateway_identity,
        identity_dir=config.upstream_identity_dir,
    )

    # Create the gateway FastMCP server
    mcp = FastMCP(config.server_name)

    # Mount each upstream server
    for upstream in config.upstreams:
        _mount_upstream(mcp, upstream, registry)

    # Recorder
    recorder = InteractionRecorder(gateway_identity, store)

    # Middleware
    middleware = TrustChainMiddleware(
        registry=registry,
        recorder=recorder,
        store=store,
        default_threshold=config.default_trust_threshold,
        bootstrap_interactions=config.bootstrap_interactions,
    )
    mcp.add_middleware(middleware)

    # Native trust tools
    register_trust_tools(
        mcp, registry, store,
        bootstrap_interactions=config.bootstrap_interactions,
    )

    logger.info(
        "Gateway '%s' ready with %d upstream(s)",
        config.server_name, len(config.upstreams),
    )

    return mcp


def _mount_upstream(
    mcp: FastMCP,
    upstream: UpstreamServer,
    registry: UpstreamRegistry,
):
    """Mount a single upstream MCP server and register its identity."""
    registry.register_server(upstream)

    if upstream.url:
        # HTTP/SSE upstream
        proxy = create_proxy(upstream.url, name=upstream.name)
    else:
        # stdio upstream (command + args)
        proxy_config = {
            "mcpServers": {
                "default": {
                    "command": upstream.command,
                    "args": upstream.args,
                    **({"env": upstream.env} if upstream.env else {}),
                }
            }
        }
        proxy = create_proxy(proxy_config, name=upstream.name)

    mcp.mount(proxy, namespace=upstream.namespace)
    logger.info("Mounted upstream '%s' with namespace '%s'", upstream.name, upstream.namespace)


def create_gateway_from_dict(config_dict: dict) -> FastMCP:
    """Create a gateway from a plain dictionary configuration.

    Example config:
    {
        "server_name": "My Gateway",
        "store_path": "./trustchain_records.json",
        "default_trust_threshold": 0.0,
        "upstreams": [
            {
                "name": "filesystem",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "namespace": "fs",
                "trust_threshold": 0.3
            }
        ]
    }
    """
    upstreams = [
        UpstreamServer(**u) for u in config_dict.get("upstreams", [])
    ]
    config = GatewayConfig(
        upstreams=upstreams,
        identity_path=config_dict.get("identity_path"),
        store_path=config_dict.get("store_path"),
        upstream_identity_dir=config_dict.get("upstream_identity_dir"),
        default_trust_threshold=config_dict.get("default_trust_threshold", 0.0),
        bootstrap_interactions=config_dict.get("bootstrap_interactions", 3),
        server_name=config_dict.get("server_name", "TrustChain Gateway"),
    )
    return create_gateway(config)
