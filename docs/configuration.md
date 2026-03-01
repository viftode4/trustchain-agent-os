# TrustChain Configuration Reference

This document covers every configuration option across all three TrustChain repositories:

- **trustchain/** — Rust node (binary + library)
- **trustchain-py/** — Python SDK
- **trustchain-agent-os/** — Agent-OS framework

---

## Table of Contents

1. [Rust Node (trustchain-node)](#rust-node-trustchain-node)
   - [TOML Configuration File](#toml-configuration-file-nodetoml)
   - [CLI Flags](#cli-flags-override-toml)
   - [Internal Constants](#internal-constants)
   - [HTTP API Endpoints](#http-api-endpoints)
2. [Python SDK (trustchain-py)](#python-sdk-trustchain-py)
   - [TrustEngine](#trustengine-configuration)
   - [TrustWeights](#trustweights-configuration)
   - [NetFlowTrust](#netflowtrust-configuration)
   - [DelegationStore](#delegationstore-configuration)
   - [TrustChainSidecar Client](#trustchainsidecar-client-configuration)
   - [Identity](#identity-configuration)
3. [Agent-OS (trustchain-agent-os)](#agent-os-trustchain-agent-os)
   - [TrustAgent](#trustagent-configuration)
   - [Service Registration](#service-registration)
   - [TrustContext](#trustcontext)
   - [Gateway Configuration](#gateway-configuration)
   - [UpstreamServer](#upstreamserver-options)
   - [Framework Adapters](#framework-adapter-configuration)
4. [Environment Variables](#environment-variables-summary)
5. [Dockerfile Configuration](#dockerfile-configuration)
6. [Gemini Model Selection](#gemini-model-selection)

---

## Rust Node (trustchain-node)

### TOML Configuration File (`node.toml`)

The node reads configuration from a TOML file. Generate a default file with:

```sh
trustchain-node init-config > node.toml
```

All fields have defaults and are optional unless noted.

```toml
# ── Network addresses ─────────────────────────────────────────────────────────

# QUIC P2P transport — Ed25519 TLS mutual auth, Dinic's max-flow gossip
quic_addr = "0.0.0.0:8200"

# gRPC block exchange (reserved; currently unused by default)
grpc_addr = "0.0.0.0:8201"

# HTTP REST API — management, delegation, identity, metrics endpoints
http_addr = "0.0.0.0:8202"

# Transparent HTTP proxy — intercepts agent-to-agent calls and records interactions
# Use 0.0.0.0:8203 (not 127.0.0.1) in Docker for inter-container access
proxy_addr = "127.0.0.1:8203"


# ── Identity ──────────────────────────────────────────────────────────────────

# Path to Ed25519 private key file
# Auto-generated if the file is missing; saved with 0o600 permissions on Unix
key_path = "identity.key"


# ── Storage ───────────────────────────────────────────────────────────────────

# Path to the primary SQLite database (WAL mode enabled automatically)
# A sibling delegations.db file is created alongside this path for the
# DelegationStore (e.g. db_path = "trustchain.db" → "delegations.db")
db_path = "trustchain.db"


# ── Networking ────────────────────────────────────────────────────────────────

# List of peer HTTP addresses to connect to on startup
# Leave empty for a standalone / first-node-in-network deployment
# Default is intentionally empty — decentralized bilateral model, peers connect directly
# Example: ["http://192.168.1.10:8202", "http://192.168.1.11:8202"]
bootstrap_nodes = []

# STUN server used to discover the node's public IP address
# Supports both IPv4 and IPv6 (XOR-MAPPED-ADDRESS with magic cookie + tx_id)
stun_server = "stun.l.google.com:19302"

# Configurable QUIC port offset from HTTP port
# QUIC listens at http_port - quic_port_offset
# Centralized constant — replaces all previous hardcoded "port - 2" values
# Default matches quic_addr=8200 / http_addr=8202 (offset = 2)
quic_port_offset = 2


# ── Consensus (CHECO checkpoint protocol) ─────────────────────────────────────

# Minimum number of co-signers required to finalize a checkpoint
# Set to 1 for a single-node deployment; increase for multi-node quorum
min_signers = 1

# Seconds between checkpoint proposals by the checkpoint loop in node.rs
# The loop: proposes → collects votes → finalizes → persists → broadcasts
# Persisted checkpoints are loaded on restart from the SQLite checkpoints table
checkpoint_interval_secs = 60


# ── Security ──────────────────────────────────────────────────────────────────

# QUIC rate limiter: maximum new connections accepted per IP address per second
# The internal HashMap is capped at 65,536 entries; oldest entries are evicted
# when the cap is reached to prevent memory exhaustion
max_connections_per_ip_per_sec = 20


# ── Logging ───────────────────────────────────────────────────────────────────

# Rust tracing log level
# Valid values: trace | debug | info | warn | error
log_level = "info"
```

#### Port Layout Summary

| Port (default) | Protocol | Purpose |
|----------------|----------|---------|
| 8200 | QUIC / UDP | P2P gossip, block exchange, delegation/succession delivery |
| 8201 | gRPC / TCP | Block exchange (reserved) |
| 8202 | HTTP / TCP | REST API (management, identity, delegation, metrics) |
| 8203 | HTTP CONNECT / TCP | Transparent proxy for agent-to-agent calls |

When using `sidecar` or `launch` CLI commands the `--port-base` flag shifts all four ports together (base, base+1, base+2, base+3).

---

### CLI Flags (override TOML)

All CLI flags take precedence over their TOML counterparts.

#### `trustchain-node run`

Starts the node using a TOML config file.

| Flag | Default | Description |
|------|---------|-------------|
| `--config, -c` | `node.toml` | Path to TOML configuration file |

#### `trustchain-node sidecar`

Starts a node with a simplified CLI (no TOML needed). Designed to be invoked by the Python `TrustChainSidecar.launch()` helper.

| Flag | Default | Description |
|------|---------|-------------|
| `--name` | (required) | Agent name, used as the data directory prefix: `~/.trustchain/<name>/` |
| `--endpoint` | (required) | The agent's own HTTP endpoint that this sidecar proxies for (e.g. `http://localhost:8080`) |
| `--port-base` | `8200` | Base port; QUIC = base, gRPC = base+1, HTTP = base+2, proxy = base+3 |
| `--bootstrap` | (none) | Comma-separated list of bootstrap peer HTTP addresses |
| `--advertise` | (auto via STUN) | Public HTTP address to advertise to other peers |
| `--data-dir` | `~/.trustchain/<name>/` | Directory for the key file and SQLite database |
| `--log-level` | `info` | Log level (`trace`, `debug`, `info`, `warn`, `error`) |

#### `trustchain-node launch`

Same as `sidecar` but additionally spawns a child process (Dapr-style lifecycle). The child inherits `HTTP_PROXY=http://127.0.0.1:<proxy-port>` automatically.

All `sidecar` flags apply, plus:

| Flag | Default | Description |
|------|---------|-------------|
| `--endpoint` | `http://localhost:8080` | Agent endpoint (has a default, unlike `sidecar`) |
| `<COMMAND>...` | (required) | Command to launch after `--` separator |

Example:
```sh
trustchain-node launch --name alice -- python my_agent.py
```

#### `trustchain-node keygen`

Generates a new Ed25519 key pair.

| Flag | Default | Description |
|------|---------|-------------|
| `--output, -o` | `identity.key` | Output file path for the private key |

#### `trustchain-node init-config`

Prints a default `node.toml` to stdout. Redirect to a file:

```sh
trustchain-node init-config > node.toml
```

---

### Internal Constants

These values are compiled in and are not user-configurable at runtime. They are documented here for operational awareness.

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_DELEGATION_TTL_SECS` | 2,592,000 (30 days) | Hard upper bound on delegation time-to-live; longer TTLs are clamped |
| `QUIC_PORT_OFFSET` | 2 | Difference between QUIC port and HTTP port; centralized to avoid drift |
| HTTP body size limit | 1 MiB | Enforced by `tower-http` `RequestBodyLimitLayer` on all HTTP endpoints |
| QUIC rate limiter cap | 65,536 | Maximum entries in the per-IP rate limiter HashMap before eviction |
| POST /peers replay window | 5 minutes | Ed25519 signature timestamps older than this are rejected |
| TLS | Ed25519 pubkey in X.509 extension OID `1.3.6.1.4.1.99999.1` | Used for pubkey pinning via `PubkeyVerifier` in `tls.rs` |

---

### HTTP API Endpoints

These are the management endpoints exposed on `http_addr` (default port 8202).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Health check — returns 200 OK when the node is ready |
| GET | `/metrics` | Prometheus-format metrics |
| GET | `/status` | Node status (pubkey, peer count, block count) |
| GET | `/identity/{pk}` | Fetch identity info for a public key |
| POST | `/peers` | Register a peer (Ed25519 signed payload, 5-min replay protection) |
| GET | `/chain` | Full local block chain |
| GET | `/block/{seq}` | Fetch a single block by sequence number |
| GET | `/crawl` | Crawl all known peers and return their chains |
| POST | `/receive_proposal` | Receive an interaction proposal block |
| POST | `/receive_agreement` | Receive an interaction agreement block |
| POST | `/delegate` | Create a delegation proposal |
| POST | `/accept_delegation` | Accept a delegation proposal (bilateral handshake) |
| GET | `/delegations/{pk}` | List delegations for a public key |
| GET | `/delegation/{id}` | Fetch a single delegation record by ID |
| POST | `/revoke` | Revoke a delegation |
| POST | `/accept_succession` | Accept a key succession proposal (explicit; not auto-accepted) |

---

## Python SDK (trustchain-py)

Import as `import trustchain` (PyPI: `trustchain-py`).

### TrustEngine Configuration

`TrustEngine` is the central object for computing trust scores from a `BlockStore`.

```python
from trustchain.trust import TrustEngine

engine = TrustEngine(
    store=block_store,          # BlockStore instance — required
                                # Typically SqliteBlockStore or InMemoryBlockStore

    seed_nodes=[                # Pubkeys used as NetFlow source anchors
        "my_pubkey",            # Include your own pubkey and any unconditionally
        "trusted_peer_pubkey",  #   trusted peers
    ],                          # If empty, NetFlow weight is redistributed
                                #   proportionally to the other components

    weights={                   # Trust component weights — must sum to 1.0
        "integrity":   0.3,     # Chain integrity score (broken chain = major penalty)
        "netflow":     0.4,     # NetFlow Sybil-resistance (max-flow from seeds)
        "statistical": 0.3,     # Statistical: interaction count, completion rate, entropy
    },

    delegation_store=None,      # Optional DelegationStore for delegated trust
                                # When provided, delegated trust is added on top of direct score

    decay_half_life_ms=None,    # Optional temporal decay half-life in milliseconds
                                # Formula: effective_weight = 2^(-age_ms / half_life_ms)
                                # Applies to: interaction_count, completion_rate, entropy
                                # None = no decay (all interactions weighted equally regardless of age)
                                # Example: 86_400_000 = 24-hour half-life

    checkpoint=None,            # Optional finalized Checkpoint object
                                # When provided, Ed25519 signature verification is skipped
                                #   for all blocks covered by the checkpoint
                                # Structural checks (hash links, sequence numbers) always run
)
```

#### Using with a Checkpoint (skip re-verification)

```python
checkpoint = store.latest_finalized_checkpoint()
engine = TrustEngine(store=store, seed_nodes=[my_pk], checkpoint=checkpoint)
score = engine.compute_trust(target_pubkey)
```

#### `with_checkpoint()` helper

```python
engine2 = engine.with_checkpoint(checkpoint)
# Returns a new TrustEngine with the checkpoint set; other fields unchanged
```

---

### TrustWeights Configuration

`TrustWeights` is a dataclass controlling component weights and temporal decay. It is passed directly to `TrustEngine` as keyword arguments or as a separate object.

```python
from trustchain.trust import TrustWeights

weights = TrustWeights(
    integrity=0.3,              # Weight for chain integrity component [0.0, 1.0]
    netflow=0.4,                # Weight for NetFlow component [0.0, 1.0]
    statistical=0.3,            # Weight for statistical component [0.0, 1.0]
                                # integrity + netflow + statistical must equal 1.0

    decay_half_life_ms=None,    # Temporal decay — see TrustEngine above
)
```

---

### NetFlowTrust Configuration

`NetFlowTrust` computes Sybil-resistance scores using incremental max-flow (Dinic's algorithm is skipped for performance — see source comments for the design rationale).

```python
from trustchain.netflow import NetFlowTrust

netflow = NetFlowTrust(
    store=block_store,          # BlockStore instance — required

    seeds=["pubkey1"],          # Seed pubkeys serving as trusted source nodes
                                # Typically just your own pubkey
)
```

#### Incremental Updates (`_known_seqs`)

`NetFlowTrust` internally tracks `_known_seqs` (per-pubkey highest seen sequence number) and only scans new blocks on each call to `get_or_build_graph()`. This matches the Rust `CachedNetFlow` behaviour and avoids re-scanning the full chain on every trust query.

---

### DelegationStore Configuration

Used by `TrustEngine` to resolve delegated trust relationships.

```python
from trustchain.delegation import InMemoryDelegationStore, SqliteDelegationStore

# In-memory (lost on restart)
delegation_store = InMemoryDelegationStore()

# SQLite-backed (persisted)
# Creates / opens a file at the given path; schema is auto-migrated
# Default path alongside db_path is "delegations.db"
delegation_store = SqliteDelegationStore("delegations.db")
```

`SqliteDelegationStore` notes:
- Timestamps stored as `INTEGER` (milliseconds, not seconds or float).
- Delegation expiry checks use millisecond comparisons.
- TTL is capped at `MAX_DELEGATION_TTL_SECS` (30 days) in the Rust node; the SDK does not re-enforce this cap but respects it.
- `accept_delegation()` rejects already-revoked delegations.

---

### TrustChainSidecar Client Configuration

`TrustChainSidecar` is the Python client for the Rust HTTP API. It replaces the deprecated `TrustChainNode`.

```python
from trustchain.sidecar import TrustChainSidecar

sidecar = TrustChainSidecar(
    base_url="http://localhost:8202",   # HTTP API URL of the running Rust node
                                        # Matches http_addr in node.toml
)
```

Available methods (all `async`):

| Method | Description |
|--------|-------------|
| `status()` | Node status |
| `healthz()` | Health check |
| `chain()` | Full local chain |
| `block(seq)` | Single block by sequence |
| `crawl(pubkey=None)` | Crawl peers (optional pubkey filter) |
| `metrics()` | Prometheus metrics text |
| `receive_proposal(block)` | Send proposal to Rust node |
| `receive_agreement(block)` | Send agreement to Rust node |
| `accept_delegation(block)` | Accept a delegation proposal |
| `accept_succession(block)` | Accept a succession proposal |
| `init_delegate(cert)` | Initiate bilateral delegation handshake (calls accept_delegation internally) |

---

### Identity Configuration

```python
from trustchain.identity import Identity

# Generate a new Ed25519 key pair
identity = Identity()

# Load an existing key from a file
identity = Identity.load("path/to/identity.key")

# Persist the key to a file
# On Unix: file is written with 0o600 permissions
identity.save("path/to/identity.key")

# Access the public key (hex-encoded)
pubkey_hex = identity.public_key_hex()
```

Key files use a raw binary format (32-byte Ed25519 seed). The same format is used by the Rust node's `key_path`.

---

## Agent-OS (trustchain-agent-os)

### TrustAgent Configuration

`TrustAgent` is the main user-facing class. It wraps an in-memory or persisted interaction ledger and exposes trust-gated service handlers.

```python
from agent_os import TrustAgent

agent = TrustAgent(
    name="my-agent",                    # Human-readable name (used in logging and registration)

    store=None,                         # Custom RecordStore implementation
                                        # None = in-memory store (lost on restart)
                                        # Pass a FileRecordStore for persistence

    identity_path=None,                 # Path to persist the Ed25519 identity key
                                        # None = ephemeral (new key on every restart)
                                        # Corrupt key files are auto-regenerated with a warning

    store_path=None,                    # Shorthand: path for a FileRecordStore
                                        # If provided alongside store=None, a FileRecordStore
                                        #   is created automatically at this path

    min_trust_threshold=0.15,           # Default trust threshold applied to services that
                                        #   do not specify their own min_trust
                                        # Used by would_accept() checks

    bootstrap_interactions=3,           # Number of free interactions allowed before trust
                                        #   gating is enforced
                                        # 1 = strict (gate from the very first call)
                                        # 3 = lenient bootstrap window (default)
                                        # Higher values delay gating for new peers

    node=None,                          # TrustChainNode (v2) for half-block protocol
                                        # When set: bidirectional interaction records written
                                        #   to the bilateral ledger (TrustContext.node populated)
                                        # None = v1 in-memory ledger only
)
```

---

### Service Registration

Services are registered using the `@agent.service()` decorator. Each registered service becomes an MCP tool callable by remote agents.

```python
@agent.service(
    name="compute",                     # Service / MCP tool name (required)
                                        # Also used as the interaction_type default

    min_trust=0.36,                     # Trust threshold for this specific service [0.0, 1.0]
                                        # Overrides agent.min_trust_threshold
                                        #
                                        # Reference values (sigmoid curve, roughly):
                                        #   0.00 = open to all callers
                                        #   0.15 = minimal bar (very lenient)
                                        #   0.36 = moderate (~7 interactions to reach)
                                        #   0.50 = high bar (~30 interactions to reach)
                                        #   0.70 = very exclusive
                                        #
                                        # Exact interaction counts depend on TrustEngine weights

    interaction_type=None,              # Record type stored in the ledger
                                        # None = defaults to the value of `name`
)
async def handler(data: dict, ctx: TrustContext) -> dict:
    ...
```

---

### TrustContext

`TrustContext` is passed as the second argument to every service handler. It carries caller metadata and the trust score for the current call.

| Field | Type | Description |
|-------|------|-------------|
| `caller_pubkey` | `str` | Ed25519 public key of the caller (required; anonymous calls rejected) |
| `trust_score` | `float` | Computed trust score for the caller [0.0, 1.0] |
| `interaction_count` | `int` | Number of past interactions with this caller |
| `node` | `TrustChainNode \| None` | v2 node reference (populated when `agent.node` is set) |

---

### Gateway Configuration

The gateway wraps multiple upstream MCP servers and applies per-call trust gating. It is the recommended deployment mode for production.

#### `GatewayConfig`

```python
from gateway.config import GatewayConfig, UpstreamServer

config = GatewayConfig(
    upstreams=[...],                    # List of UpstreamServer objects (see below)

    identity_path="./gateway.key",      # Path to persist the gateway's Ed25519 key
                                        # Auto-generated if missing

    store_path="./records.json",        # Path to the FileRecordStore for interaction records

    upstream_identity_dir="./upstream_keys/",
                                        # Directory where upstream identity pubkeys are stored
                                        # File naming: <upstream_name>.pub

    default_trust_threshold=0.0,        # Global trust threshold applied to all upstreams
                                        # that do not specify their own trust_threshold
                                        # 0.0 = no gating (pass all calls through)

    bootstrap_interactions=3,           # Bootstrap window — calls before trust gating applies
                                        # Mirrors TrustAgent.bootstrap_interactions

    server_name="TrustChain Gateway",   # FastMCP server name (shown in MCP discovery)

    use_v2=False,                       # Enable v2 mode: GatewayNode + TrustEngine
                                        # When True: half-block protocol active,
                                        #   TrustContext.node is populated
)
```

#### Dict-Based Configuration

For YAML/JSON-driven deployments:

```python
from gateway.server import create_gateway_from_dict

gateway = create_gateway_from_dict({
    "server_name": "My Gateway",
    "store_path": "./trustchain_records.json",
    "identity_path": "./gateway.key",
    "upstream_identity_dir": "./upstream_keys/",
    "default_trust_threshold": 0.0,
    "bootstrap_interactions": 3,
    "use_v2": False,
    "upstreams": [
        {
            # stdio-based upstream (subprocess)
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {},                  # Extra environment variables for the subprocess
            "namespace": "fs",          # MCP tool namespace prefix
            "trust_threshold": 0.3
        },
        {
            # URL-based upstream (HTTP/SSE)
            "name": "remote-tools",
            "url": "http://localhost:3000/mcp",
            "trust_threshold": 0.5,
            "trustchain_url": "http://localhost:8202"  # v2: TrustChain node endpoint
        }
    ]
})
```

---

### UpstreamServer Options

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | (required) | Upstream identifier; used for logging and identity file names |
| `command` | `str` | `""` | stdio command to spawn the upstream MCP server |
| `args` | `list[str]` | `[]` | Arguments for `command` |
| `env` | `dict[str, str]` | `{}` | Extra environment variables passed to the subprocess |
| `namespace` | `str` | `name` | MCP tool namespace prefix (e.g. `"fs"` → tools are `fs/<tool>`) |
| `trust_threshold` | `float` | `default_trust_threshold` | Per-upstream trust threshold; overrides global default |
| `url` | `str \| None` | `None` | HTTP/SSE URL for URL-based upstreams (alternative to `command`) |
| `trustchain_url` | `str \| None` | `None` | v2: URL of the TrustChain Rust node associated with this upstream |

`command` defaults to `""` (empty string) — it is not a required field for URL-based upstreams.

---

### Framework Adapter Configuration

The `tc_frameworks/` package ships adapters for 12 agent frameworks. Each adapter follows a common pattern: it accepts an `agent_name`, optional LLM config, and optional TrustChain wiring.

#### Common LLM Provider Environment Variables

| Variable | Frameworks | Description |
|----------|-----------|-------------|
| `GOOGLE_API_KEY` | Google ADK, LangGraph (google), CrewAI (gemini/), Agno, LlamaIndex, Semantic Kernel, PydanticAI | Google / Gemini API key |
| `GEMINI_API_KEY` | Some frameworks | Alternative name for the Gemini key |
| `OPENAI_API_KEY` | LangGraph (openai), AutoGen, OpenAI Agents, Semantic Kernel | OpenAI API key |
| `ANTHROPIC_API_KEY` | Claude adapter, LangGraph (anthropic) | Anthropic API key |
| `HUGGING_FACE_HUB_TOKEN` | Smolagents (hf provider) | HuggingFace Hub token |

#### Adapter-Specific Notes

**AutoGen**
```python
# Use GeminiLLMConfigEntry (not a flat dict to LLMConfig)
from autogen_ext.models.google import GeminiChatCompletionClient
```

**OpenAI Agents**
```python
# Gemini via OpenAI-compatible endpoint
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
api_key = os.environ["GOOGLE_API_KEY"]
```

**Google ADK**
```python
# asyncio.Lock protects _ensure_session() for concurrent calls
# Requires GOOGLE_API_KEY
```

**CrewAI**
```python
# Use async kickoff; crew is cached after first creation
# Gemini model string prefix: "gemini/"
```

**LangGraph / AutoGen / OpenAI Agents**
```python
# Crew, agents, and graphs are cached after first creation to avoid re-init overhead
```

**Semantic Kernel**
```python
# GoogleAIChatCompletion requires an explicit PromptExecutionSettings argument
from semantic_kernel.connectors.ai.google.google_ai import GoogleAIChatPromptExecutionSettings
```

**LlamaIndex**
```python
# Use ReActAgent constructor directly — from_tools() was removed in v0.14
from llama_index.core.agent.react import ReActAgent
agent = ReActAgent(tools=[...], llm=llm)
```

**FastMCP tool access**
```python
# Call tools via mcp.call_tool(); result is ToolResult
result = await mcp.call_tool(name, args)
text = result.content[0].text
```

---

## Environment Variables Summary

| Variable | Used By | Description |
|----------|---------|-------------|
| `GOOGLE_API_KEY` | ADK, LangGraph, CrewAI, PydanticAI, SK, Agno, LlamaIndex | Google / Gemini API key |
| `GEMINI_API_KEY` | Some frameworks | Alternative name; takes precedence in frameworks that check it first |
| `OPENAI_API_KEY` | LangGraph, AutoGen, OpenAI Agents, SK | OpenAI API key |
| `ANTHROPIC_API_KEY` | Claude adapter | Anthropic API key |
| `HUGGING_FACE_HUB_TOKEN` | Smolagents | HuggingFace Hub token |
| `HTTP_PROXY` | `TrustAgent.call_remote_service()` | Sidecar proxy URL; set automatically by `trustchain-node launch` |
| `TRUSTCHAIN_HTTP` | `TrustAgent.call_remote_service()` | Alternative sidecar HTTP base URL (fallback to `HTTP_PROXY`) |

---

## Dockerfile Configuration

The official `Dockerfile` for the Rust node uses a multi-stage build.

```dockerfile
# Build stage
FROM rust:1.85-bookworm AS builder
WORKDIR /build
COPY . .
RUN cargo build --release -p trustchain-node

# Runtime stage
FROM debian:bookworm-slim

# Run as a non-root user
RUN useradd -m trustchain
USER trustchain

WORKDIR /home/trustchain

COPY --from=builder /build/target/release/trustchain-node /usr/local/bin/

# Health check using the /healthz endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8202/healthz || exit 1

EXPOSE 8200/udp 8201/tcp 8202/tcp 8203/tcp

CMD ["trustchain-node", "run", "--config", "node.toml"]
```

### Docker-Specific Configuration Notes

- Set `proxy_addr = "0.0.0.0:8203"` (not `127.0.0.1`) so other containers can reach the proxy.
- Use Docker network aliases and `bootstrap_nodes` to wire multiple nodes together.
- The `HEALTHCHECK` targets port 8202 (`http_addr`).
- Mount a volume at the data directory to persist `trustchain.db`, `delegations.db`, and `identity.key` across container restarts.

Example `docker-compose.yml` snippet:

```yaml
services:
  alice:
    image: trustchain-node:latest
    volumes:
      - alice_data:/home/trustchain
    environment:
      - RUST_LOG=info
    ports:
      - "8202:8202"
      - "8203:8203"
      - "8200:8200/udp"

  bob:
    image: trustchain-node:latest
    volumes:
      - bob_data:/home/trustchain
    environment:
      - RUST_LOG=info
    command: >
      trustchain-node run --config /dev/stdin <<EOF
      http_addr = "0.0.0.0:8202"
      proxy_addr = "0.0.0.0:8203"
      bootstrap_nodes = ["http://alice:8202"]
      EOF

volumes:
  alice_data:
  bob_data:
```

---

## Gemini Model Selection

Gemini's free tier allows 20 requests per day (RPD) per model per project. Spread load across multiple models to maximize the free tier.

### Available Models (as of March 2026)

| Model | Notes |
|-------|-------|
| `gemini-2.5-flash` | Most capable flash-tier model |
| `gemini-2.5-flash-lite` | Lighter, faster variant |
| `gemini-2.0-flash-lite` | Previous-generation lite model |

> **Note:** `gemini-2.0-flash` was retired on March 3, 2026 and must not be used.

### Round-Robin Helper

```python
MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
]

def model_for(idx: int) -> str:
    """Return a model name, cycling through MODELS to spread RPD quota."""
    return MODELS[idx % len(MODELS)]
```

Usage in multi-agent demos:
```python
agents = [
    MyFrameworkAdapter(model=model_for(0)),
    MyFrameworkAdapter(model=model_for(1)),
    MyFrameworkAdapter(model=model_for(2)),
]
```

---

## Version Reference

| Repository | Version | Notes |
|-----------|---------|-------|
| `trustchain` (Rust) | 0.1.0 | Pre-stable versioning convention |
| `trustchain-py` (Python) | 2.1.0 | PyPI: `trustchain-py` |
| `trustchain-agent-os` (Python) | 2.0.0 | Depends on `trustchain-py>=2.0` and `fastmcp>=3.0` |

Version numbers are intentionally different across repos — they track their own release cadences.

---

## Quick-Start Configuration Examples

### Minimal Single-Node Setup (Rust)

```toml
# node.toml — single node, all defaults
key_path   = "identity.key"
db_path    = "trustchain.db"
log_level  = "info"
```

### Minimal Multi-Node Setup (Rust)

```toml
# node.toml — second node joining alice
key_path        = "identity.key"
db_path         = "trustchain.db"
bootstrap_nodes = ["http://alice.example.com:8202"]
```

### Minimal TrustAgent (Python, ephemeral)

```python
from agent_os import TrustAgent

agent = TrustAgent(name="alice")

@agent.service(name="ping", min_trust=0.0)
async def ping(data, ctx):
    return {"pong": True}
```

### Minimal TrustAgent (Python, persistent)

```python
from agent_os import TrustAgent

agent = TrustAgent(
    name="alice",
    identity_path="./alice.key",
    store_path="./alice_records.json",
)
```

### Minimal Gateway (Python)

```python
from gateway.config import GatewayConfig, UpstreamServer
from gateway.server import create_gateway

config = GatewayConfig(
    upstreams=[
        UpstreamServer(
            name="tools",
            url="http://localhost:3000/mcp",
            trust_threshold=0.3,
        )
    ],
    identity_path="./gateway.key",
    store_path="./records.json",
)
gateway = create_gateway(config)
```
