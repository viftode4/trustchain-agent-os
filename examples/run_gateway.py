"""Run the TrustChain Gateway as a stdio MCP server.

Configure Claude Code to use this as an MCP server by adding to .mcp.json:

{
    "mcpServers": {
        "trustchain-gateway": {
            "command": "python",
            "args": ["path/to/run_gateway.py"]
        }
    }
}

Environment variables:
    TRUSTCHAIN_STORE_PATH: Path to persist interaction records (default: ./data/records.json)
    TRUSTCHAIN_IDENTITY_PATH: Path to persist gateway identity (default: ./data/gateway.key)
    TRUSTCHAIN_IDENTITY_DIR: Path to persist upstream identities (default: ./data/identities)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway.config import GatewayConfig, UpstreamServer
from gateway.server import create_gateway

# Configure upstream servers here.
# Each upstream is an MCP server that the gateway will proxy and trust-gate.
config = GatewayConfig(
    server_name="TrustChain Gateway",
    store_path=os.environ.get("TRUSTCHAIN_STORE_PATH", "./data/records.json"),
    identity_path=os.environ.get("TRUSTCHAIN_IDENTITY_PATH", "./data/gateway.key"),
    upstream_identity_dir=os.environ.get("TRUSTCHAIN_IDENTITY_DIR", "./data/identities"),
    default_trust_threshold=0.0,
    bootstrap_interactions=3,
    upstreams=[
        # Example: stdio MCP server (npm required)
        # UpstreamServer(
        #     name="filesystem",
        #     command="npx",
        #     args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        #     namespace="fs",
        #     trust_threshold=0.0,
        # ),
        #
        # Example: HTTP-based MCP server
        # UpstreamServer(
        #     name="my-api",
        #     url="http://localhost:3001/mcp",
        #     namespace="api",
        #     trust_threshold=0.3,
        # ),
    ],
)

gateway = create_gateway(config)

if __name__ == "__main__":
    gateway.run()
