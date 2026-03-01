# Sidecar & Rust Node Guide

## Overview

The TrustChain node is a Rust binary that runs as a sidecar next to any agent. It provides:

- Ed25519 identity management
- QUIC P2P transport for peer discovery
- HTTP API for trust operations
- Transparent HTTP proxy on port 8203 that intercepts agent calls and adds bilateral trust records
- MCP server (5 tools) over streamable HTTP + stdio
- SQLite WAL storage with checkpoint persistence

## Installation

### Pre-built binary

Download from GitHub releases: https://github.com/viftode4/trustchain

### Build from source

```bash
git clone https://github.com/viftode4/trustchain
cd trustchain
cargo build --release
# Binary at target/release/trustchain-node (or .exe on Windows)
```

### Docker

```dockerfile
# Dockerfile uses rust:1.85-bookworm
# Non-root user, HEALTHCHECK included
docker build -t trustchain .
docker run -p 8200-8203:8200-8203 trustchain
```

Note: In Docker, proxy_addr should be `0.0.0.0:8203` for inter-container access.

## CLI Commands

### `trustchain-node keygen`

Generate a new Ed25519 identity keypair.

```
Options:
  -o, --output <OUTPUT>  Output path [default: identity.key]
```

Private key files are saved with `0o600` permissions on Unix.

### `trustchain-node run`

Start the full TrustChain node with all services.

```
Options:
  -c, --config <CONFIG>  Path to TOML config file [default: node.toml]
```

### `trustchain-node sidecar`

Run as a sidecar next to an agent. Generates identity, starts all services, prints `HTTP_PROXY`.

```
Options:
  --name <NAME>              Agent name (data dir: ~/.trustchain/<name>/)
  --endpoint <ENDPOINT>      Agent's HTTP endpoint (e.g. http://localhost:8080)
  --port-base <PORT_BASE>    Base port [default: 8200]
                             QUIC=base, gRPC=base+1, HTTP=base+2, proxy=base+3
  --bootstrap <BOOTSTRAP>    Bootstrap peer addresses (comma-separated HTTP addresses)
  --advertise <ADVERTISE>    Public HTTP address for other nodes
  --data-dir <DATA_DIR>      Data directory [default: ~/.trustchain/<name>/]
  --log-level <LOG_LEVEL>    Log level [default: info]
```

### `trustchain-node launch`

Dapr-style: start sidecar, wait for health, set `HTTP_PROXY`, launch your app.

```bash
trustchain-node launch --name my-agent -- python my_agent.py
```

```
Options:
  --name <NAME>              Agent name
  --endpoint <ENDPOINT>      Agent's HTTP endpoint [default: http://localhost:8080]
  --port-base <PORT_BASE>    Base port [default: 8200]
  --bootstrap <BOOTSTRAP>    Bootstrap peer addresses
  --advertise <ADVERTISE>    Public HTTP address
  --data-dir <DATA_DIR>      Data directory
  --log-level <LOG_LEVEL>    Log level [default: info]
```

On app exit, the sidecar shuts down automatically.

### `trustchain-node mcp-stdio`

Run MCP server over stdio for local LLM hosts (Claude Desktop, Cursor, etc.).

### `trustchain-node status`

Query a running node's status.

### `trustchain-node propose`

Send a proposal to a peer.

### `trustchain-node init-config`

Print default TOML configuration:

```toml
quic_addr = "0.0.0.0:8200"
grpc_addr = "0.0.0.0:8201"
http_addr = "0.0.0.0:8202"
proxy_addr = "127.0.0.1:8203"
key_path = "identity.key"
db_path = "trustchain.db"
bootstrap_nodes = []
min_signers = 1
max_connections_per_ip_per_sec = 20
checkpoint_interval_secs = 60
stun_server = "stun.l.google.com:19302"
log_level = "info"
```

## Port Layout

| Port | Service | Description |
|------|---------|-------------|
| 8200 | QUIC | P2P transport (Ed25519 TLS) |
| 8201 | gRPC | Block exchange service |
| 8202 | HTTP | REST API (proposals, status, metrics, delegation) |
| 8203 | Proxy | Transparent HTTP proxy for agent traffic |

All ports are offset from `port_base`. Running multiple sidecars: use different `port_base` values (8200, 8210, 8220, etc.).

## HTTP API Endpoints

### Trust Operations

- `POST /receive_proposal` — Submit a proposal block
- `POST /receive_agreement` — Submit an agreement block
- `POST /accept_delegation` — Accept a delegation proposal
- `POST /accept_succession` — Accept a succession proposal
- `GET /crawl?pubkey=<pk>` — Crawl a peer's chain

### Identity & Status

- `GET /status` — Node status (pubkey, peers, blocks)
- `GET /healthz` — Health check
- `GET /identity/<pk>` — Get identity info for a pubkey
- `GET /metrics` — Prometheus-format metrics

### Delegation

- `POST /delegate` — Create a delegation
- `POST /revoke` — Revoke a delegation
- `GET /delegations/<pk>` — List delegations for a pubkey
- `GET /delegation/<id>` — Get specific delegation

### Peers

- `POST /peers` — Register a peer (Ed25519 signed, 5-min replay window)
- `GET /peers` — List known peers

## Transparent Proxy Mode

The key feature: set `HTTP_PROXY=http://localhost:8203` and all agent HTTP calls are intercepted.

### How it works

1. Agent makes a normal HTTP call: `requests.get("http://other-agent:8080/api")`
2. The call goes through the TrustChain proxy on `:8203`
3. Proxy performs TrustChain handshake with the remote agent's sidecar
4. Request is forwarded to the actual endpoint
5. Bilateral trust record is created automatically
6. Response is returned to the agent unchanged

### HTTPS CONNECT

The proxy supports the `CONNECT` method for TLS tunneling. SSRF protection blocks loopback, RFC1918, and link-local addresses.

### TLS Pubkey Pinning

`PubkeyVerifier` in `tls.rs` validates the Ed25519 pubkey embedded in the X.509 custom extension (OID `1.3.6.1.4.1.99999.1`). `send_message_pinned()` is used for identity-verified QUIC connections. Passing `None` as the pubkey falls back to `AcceptAnyCert` mode with a startup warning.

## Python Sidecar Client

The Python SDK provides `TrustChainSidecar` for programmatic access:

```python
from trustchain.sidecar import TrustChainSidecar

sidecar = TrustChainSidecar(base_url="http://localhost:8202")

# Check status
status = await sidecar.status()

# Health check
healthy = await sidecar.healthz()

# Submit proposal
await sidecar.receive_proposal(proposal_block)

# Submit agreement
await sidecar.receive_agreement(agreement_block)

# Crawl peer chain
blocks = await sidecar.crawl(peer_pubkey)

# Delegation
await sidecar.init_delegate(delegate_pubkey, scope, ttl)
await sidecar.accept_delegation(delegation_block)

# Metrics
metrics = await sidecar.metrics()

# Chain info
chain = await sidecar.chain(pubkey)
block = await sidecar.block(block_hash)
```

## Usage Patterns

### Pattern 1: Launch mode (simplest)

```bash
trustchain-node launch --name my-agent -- python my_agent.py
```

The sidecar starts, sets `HTTP_PROXY`, and launches your app. Everything is automatic. On app exit the sidecar shuts down.

### Pattern 2: Sidecar mode (separate processes)

Terminal 1:

```bash
trustchain-node sidecar --name my-agent --endpoint http://localhost:8080
```

Terminal 2:

```bash
export HTTP_PROXY=http://localhost:8203
python my_agent.py
```

### Pattern 3: Multi-agent local network

```bash
# Agent A on ports 8200-8203
trustchain-node sidecar --name agent-a --endpoint http://localhost:8080 --port-base 8200

# Agent B on ports 8210-8213, bootstrapping from A
trustchain-node sidecar --name agent-b --endpoint http://localhost:8081 --port-base 8210 --bootstrap http://localhost:8202

# Agent C on ports 8220-8223, bootstrapping from A
trustchain-node sidecar --name agent-c --endpoint http://localhost:8082 --port-base 8220 --bootstrap http://localhost:8202
```

### Pattern 4: Docker Compose

```yaml
services:
  agent-a:
    build: ./my-agent
    environment:
      - HTTP_PROXY=http://sidecar-a:8203
  sidecar-a:
    image: trustchain
    command: sidecar --name agent-a --endpoint http://agent-a:8080
    ports: ["8200-8203:8200-8203"]
    # proxy_addr must be 0.0.0.0:8203 in Docker for inter-container access
```

## MCP Server

The node exposes 5 MCP tools over streamable HTTP and stdio:

| Tool | Description |
|------|-------------|
| `discover_peers` | Find peers, with optional capability filter (calls `find_capable_agents()` when capability param is non-empty) |
| `propose_interaction` | Send a trust proposal to a peer |
| `check_trust` | Compute trust score for a pubkey |
| `get_identity` | Retrieve identity info for a pubkey |
| `get_chain_status` | Get the local chain's block count and latest block |

Use with Claude Desktop by adding to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "trustchain": {
      "command": "trustchain-node",
      "args": ["mcp-stdio"]
    }
  }
}
```

### MCP caller verification

`verify_caller()` checks an Ed25519 signature over `pubkey:tool:nonce`. Backward compatible — callers without a signature receive a deprecation warning but are not rejected.

## Identity & Key Management

Each node has a single Ed25519 keypair that serves as its permanent identity:

- Key file path is set by `key_path` in config (default: `identity.key`)
- In `sidecar`/`launch` mode, keys are stored under `~/.trustchain/<name>/`
- If the key file does not exist, a new keypair is generated on startup
- Corrupt key files are detected and auto-regenerated
- On Unix, key files are saved with `0o600` permissions (owner-read-only)

The public key is the node's canonical identifier — used in block signatures, TLS handshakes, delegation records, and peer registration.

## Block Types

The bilateral ledger records six block types:

| BlockType | Description |
|-----------|-------------|
| `Proposal` | Initiating party proposes an interaction |
| `Agreement` | Counterparty accepts, forming a bilateral pair |
| `Checkpoint` | CHECO-style aggregate checkpoint over a batch of blocks |
| `Delegation` | Delegate authority to another pubkey with scope and TTL |
| `Revocation` | Revoke an active delegation |
| `Succession` | Transfer identity to a new keypair |

All blocks are Ed25519-signed by their author. Agreement blocks reference the proposal block hash. Checkpoint blocks cover a range of sequence numbers and require `min_signers` co-signers.

## Storage

SQLite with WAL mode is used for all persistence:

- `trustchain.db` — main block store (`blocks` table, `checkpoints` table, `peers` table)
- `delegations.db` — delegation store (separate file alongside `trustchain.db`)
- `AppState` in `http.rs` is generic over `<S: BlockStore, D: DelegationStore>`
- `BlockStore` trait is `Send` (not `Send+Sync`); `SqliteBlockStore` uses `Mutex<Connection>`
- Mutex poisoning is recovered via `into_inner()`
- Timestamps are stored as `INTEGER` columns (milliseconds since epoch, `u64`)

### Checkpoints

CHECO checkpoint lifecycle:

1. `checkpoint_loop` in `node.rs` proposes a checkpoint every `checkpoint_interval_secs`
2. Collects votes from co-signers (`min_signers` threshold)
3. Finalizes and persists the checkpoint to the `checkpoints` table
4. Broadcasts the finalized checkpoint to peers
5. On restart, persisted checkpoints are loaded and `AppState.latest_checkpoint` is updated

`TrustEngine.with_checkpoint(cp)` skips Ed25519 verification for blocks covered by the checkpoint (structural checks always run).

## Trust Computation

### NetFlow / Sybil Resistance

Trust scores are computed via max-flow (NetFlow) on the bilateral interaction graph. Each bilateral pair contributes flow proportional to completion rate and interaction count. Sybil clusters are naturally capped — a cluster's outbound trust is bounded by its inbound flow.

`CachedNetFlow` owns the block store and performs incremental updates: only newly-appended blocks are scanned on each call. Per-pubkey sequence number tracking (`_known_seqs`) means recomputation is O(new blocks), not O(total blocks).

### Temporal Decay

`TrustWeights.decay_half_life_ms` applies exponential decay to interaction count, completion rate, and entropy:

```
weight = 2^(-age_ms / half_life_ms)
```

Older interactions contribute less to the current trust score.

### Completion Rate

Completion rate is computed from linked block pairs (proposal + agreement). Fallback logic for blocks missing their counterpart matches the Python SDK implementation.

### Delegated Trust

When a delegation is active, the root trust budget is split flat across all active delegates:

```
delegate_trust = root_trust / active_delegate_count
```

This matches the Rust implementation in `trustchain-core`.

## Wire Format & Hashing

- JSON canonical hashing uses `BTreeMap` (sorted keys) with compact separators (no spaces)
- All timestamps are `u64` milliseconds on the wire — never float seconds
- Block hashes are SHA-256 over the canonical JSON representation
- Ed25519 signatures are over the block hash bytes

## QUIC Transport

- Runs on `quic_addr` (default `0.0.0.0:8200`)
- Ed25519 pubkey embedded in X.509 certificate via custom OID for peer verification
- QUIC handler routes incoming blocks by `BlockType`:
  - `Delegation` → `accept_delegation()`
  - `Succession` → `accept_succession()`
  - All others → `create_agreement()` / `add_block()`
- STUN (`stun.l.google.com:19302` by default) used for NAT traversal; supports both IPv4 and IPv6 (`XOR-MAPPED-ADDRESS` with full IPv6 XOR)
- Rate limiter: max `max_connections_per_ip_per_sec` (default 20) new QUIC connections per IP per second; rate-limiter HashMap capped at 65K entries with LRU eviction

## Gossip & Peer Discovery

- Peers register via `POST /peers` (Ed25519-signed payload, 5-minute replay window)
- Bootstrap peers are contacted on startup; they propagate the new node's address via gossip
- `BlockPairBroadcast` gossip validates that a proposal and agreement are a matched pair before storing
- Discovery loop periodically crawls known peers' chains for new blocks
- `default_seed_nodes()` returns an empty list — decentralized bilateral model; peers connect directly

## Security Notes

- **HTTP body limit**: 1 MiB enforced via `tower-http` `RequestBodyLimitLayer`
- **QUIC rate limiting**: 20 connections/IP/sec; HashMap capped at 65K entries with eviction
- **Gossip validation**: `BlockPairBroadcast` validates matched pairs before storing
- **Private key permissions**: `0o600` on Unix
- **POST /peers**: Ed25519-signed payload with 5-minute replay window; unauthenticated registrations emit a warning log
- **Delegation TTL**: capped at 30 days (`MAX_DELEGATION_TTL_SECS`)
- **Sequence number gaps**: warn on receive but do not reject — out-of-order delivery is resolved by crawl
- **CONNECT proxy SSRF**: blocks loopback (`127.0.0.0/8`), RFC1918 (`10.x`, `172.16-31.x`, `192.168.x`), and link-local (`169.254.x`)
- **Succession**: requires explicit `POST /accept_succession`; auto-accept was removed
- **Delegation auto-accept**: removed; crawl no longer auto-accepts pending delegations (requires explicit `POST /accept_delegation`)
- **TLS AcceptAnyCert**: emits a startup warning; full pubkey pinning is active for `send_message_pinned()` connections

## Observability

- `GET /healthz` — returns `200 OK` when the node is ready to serve traffic
- `GET /metrics` — Prometheus text format; scrape with Prometheus + Grafana
- Log levels: `trace`, `debug`, `info`, `warn`, `error` (set via `--log-level` or config `log_level`)
- Structured logging via the `tracing` crate
- Silent duplicate-block errors: duplicate `add_block()` calls are silently ignored; all other errors are logged

## Configuring via TOML

Full example `node.toml`:

```toml
# Network addresses
quic_addr = "0.0.0.0:8200"
grpc_addr = "0.0.0.0:8201"
http_addr = "0.0.0.0:8202"
proxy_addr = "127.0.0.1:8203"   # Use 0.0.0.0:8203 in Docker

# Identity and storage
key_path = "identity.key"
db_path = "trustchain.db"

# Peers
bootstrap_nodes = [
  "http://peer-a:8202",
  "http://peer-b:8202"
]

# Security
min_signers = 1
max_connections_per_ip_per_sec = 20

# Checkpoints
checkpoint_interval_secs = 60

# NAT traversal
stun_server = "stun.l.google.com:19302"

# Logging
log_level = "info"
```

The `quic_port_offset` field is also configurable for deployments where QUIC must run on a non-default offset relative to the HTTP port.

## Versioning

- Rust crate version: `0.1.0` (pre-stable convention)
- Python SDK version: `2.1.0`
- Agent-OS version: `2.0.0`

Versions are intentionally different across repos; they track independent release cadences.

## Repository

Source: https://github.com/viftode4/trustchain (branch: `master`)

Workspace crates:

| Crate | Description |
|-------|-------------|
| `trustchain-core` | Block types, crypto, NetFlow, trust engine |
| `trustchain-transport` | QUIC + gRPC transport |
| `trustchain-node` | HTTP server, proxy, CLI, MCP server |
| `trustchain-wasm` | WASM bindings (npm package, CI via `wasm.yml`) |

Run all tests:

```bash
cargo test --workspace
```

214 tests pass as of the latest release.
