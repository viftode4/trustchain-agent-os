# TrustChain Agent OS — Python API Reference

This document is the complete API reference for the `trustchain-agent-os` package. It covers every public class, method, parameter, and return type.

**Package layout:**

```
agent_os/
    agent.py          # TrustAgent — the core agent primitive
    context.py        # TrustContext — injected handler context
    decorators.py     # trust_gate, record_interaction, TrustGateError
gateway/
    config.py         # UpstreamServer, GatewayConfig
    server.py         # create_gateway, create_gateway_from_dict
    middleware.py     # TrustChainMiddleware
    registry.py       # UpstreamRegistry
    node.py           # GatewayNode
    recorder.py       # InteractionRecorder
    trust_tools.py    # register_trust_tools, verify_caller
tc_frameworks/
    base.py           # FrameworkAdapter (ABC)
    adapters/         # 12 concrete framework adapters
```

---

## agent_os.TrustAgent

**Module:** `agent_os.agent`

The core agent primitive. Provides trust-gated services, bilateral interaction recording, and MCP server export. Supports two operation modes:

- **v1 mode** (default): Uses a `RecordStore` for bilateral interaction records and `compute_trust()` for scoring.
- **v2 mode**: When a `TrustChainNode` is provided via the `node` parameter, uses the half-block protocol (proposal + agreement) and `TrustEngine` for scoring.

### Constructor

```python
TrustAgent(
    name: str,
    store: Optional[RecordStore] = None,
    identity_path: Optional[str] = None,
    store_path: Optional[str] = None,
    min_trust_threshold: float = 0.15,
    bootstrap_interactions: int = 3,
    node=None,
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Human-readable agent name, used in display and MCP server naming. |
| `store` | `Optional[RecordStore]` | `None` | Custom v1 `RecordStore` instance. If `None` and `store_path` is also `None`, an in-memory `RecordStore` is created. |
| `identity_path` | `Optional[str]` | `None` | Path to a `.key` file for persisting the Ed25519 identity. If the file exists, the key is loaded. If it does not exist, a new key is generated and saved at that path. If `None`, an ephemeral in-memory identity is created (lost on restart). |
| `store_path` | `Optional[str]` | `None` | Path for a `FileRecordStore`. Used only when `store` is `None`. Ignored in v2 mode (the node's `BlockStore` is used instead). |
| `min_trust_threshold` | `float` | `0.15` | Default minimum trust score for `would_accept()`. Can be overridden per-call. |
| `bootstrap_interactions` | `int` | `3` | Number of free interactions before trust gating activates. Callers with fewer than this many recorded interactions are always allowed through. |
| `node` | `Optional[TrustChainNode]` | `None` | A `TrustChainNode` instance (from `trustchain.api`). When provided, enables v2 mode: the node's identity is used, and `TrustEngine` replaces `compute_trust()`. |

**Identity resolution order:**
1. If `node` is provided: `identity = node.identity`
2. Else if `identity_path` is set and the file exists: loads from file
3. Else if `identity_path` is set but the file does not exist: generates a new key and saves it
4. Else: generates an ephemeral in-memory key

**Store resolution order (v1 mode only):**
1. If `store` is provided: uses it directly
2. Else if `store_path` is set: creates a `FileRecordStore(store_path)`
3. Else: creates an in-memory `RecordStore()`

**Example — minimal ephemeral agent:**

```python
from agent_os import TrustAgent

agent = TrustAgent(name="my-agent")
print(agent.pubkey)   # full 64-char hex
print(agent.short_id) # first 8 hex chars
```

**Example — persistent identity and store:**

```python
agent = TrustAgent(
    name="persistent-agent",
    identity_path="./agent.key",   # survives restarts
    store_path="./records.json",   # trust history survives restarts
    min_trust_threshold=0.3,
    bootstrap_interactions=5,
)
```

**Example — v2 mode with TrustChainNode:**

```python
from trustchain.api import TrustChainNode
from trustchain.blockstore import MemoryBlockStore
from trustchain.identity import Identity

identity = Identity()
node = TrustChainNode(identity, MemoryBlockStore())

agent = TrustAgent(name="v2-agent", node=node)
# agent.identity is node.identity
# agent._trust_engine is a TrustEngine seeded on agent.pubkey
```

---

### Properties

#### `pubkey: str`

Full 64-character hex-encoded Ed25519 public key. This is the agent's globally unique identity.

```python
agent.pubkey
# "a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2"
```

#### `short_id: str`

Short hex prefix of the public key, for human-readable display. Length depends on the `Identity` implementation (typically 8–16 characters).

```python
agent.short_id  # "a3f1b2c4"
```

#### `trust_score: float`

This agent's own trust score in the range `[0.0, 1.0]`. Computed as:
- **v2**: `TrustEngine.compute_trust(self.pubkey)` — combines chain integrity (0.3), NetFlow (0.4), and statistical score (0.3).
- **v1**: `compute_trust(self.pubkey, self.store)` — based on interaction count, unique counterparties, completion rate, account age, and entropy.

A newly created agent with no interactions has a trust score of `0.0`.

```python
print(f"Trust: {agent.trust_score:.3f}")
```

#### `interaction_count: int`

Total number of interactions recorded for this agent.
- **v2**: `node.store.get_latest_seq(self.pubkey)` — sequence number from the block store.
- **v1**: `len(store.get_records_for(self.pubkey))` — count of records in the record store.

```python
print(f"Interactions: {agent.interaction_count}")
```

#### `node`

The v2 `TrustChainNode` instance, or `None` if the agent is in v1 mode.

```python
if agent.node:
    print("v2 mode")
else:
    print("v1 mode")
```

---

### Methods

#### `check_trust(pubkey: str) -> float`

Get the trust score of any agent by their public key.

**Parameters:**
- `pubkey` (`str`): Hex-encoded Ed25519 public key of the agent to look up.

**Returns:** `float` in `[0.0, 1.0]`. Returns `0.0` for unknown agents with no interaction history.

**Computation:**
- **v2**: `TrustEngine.compute_trust(pubkey)`
- **v1**: `compute_trust(pubkey, self.store)`

```python
score = agent.check_trust(other_agent.pubkey)
print(f"Other agent trust: {score:.3f}")
```

---

#### `chain_integrity() -> float`

Compute this agent's chain integrity score. Detects tampering such as hash breaks, sequence gaps, or missing signatures.

**Returns:** `float` in `[0.0, 1.0]`. Returns `1.0` for a fully intact chain or an agent with no interactions yet.

**Computation:**
- **v2**: `node.protocol.integrity_score(self.pubkey)`
- **v1**: `compute_chain_integrity(self.pubkey, records)` from the record store.

```python
integrity = agent.chain_integrity()
if integrity < 1.0:
    print(f"Warning: chain integrity degraded to {integrity:.3f}")
```

---

#### `would_accept(other_pubkey: str, min_trust: Optional[float] = None) -> bool`

Check whether this agent would accept an interaction from another agent, given current trust scores and bootstrap state.

**Parameters:**
- `other_pubkey` (`str`): Public key of the potential caller.
- `min_trust` (`Optional[float]`): Override threshold. If `None`, uses `self.min_trust_threshold`.

**Returns:** `bool`. `True` if the interaction would be accepted.

**Logic:**
1. Compute `other_trust = check_trust(other_pubkey)`.
2. Determine `caller_history` (block count or record count for `other_pubkey`).
3. If `caller_history < self.bootstrap_interactions`: return `True` (bootstrap pass).
4. Otherwise: return `other_trust >= threshold`.

```python
# Before committing to a service call, pre-check
if not agent.would_accept(caller.pubkey, min_trust=0.5):
    print("Would be rejected — trust too low")
```

---

#### `service(name: str, min_trust: float = 0.0, interaction_type: Optional[str] = None) -> Callable`

Decorator that registers a trust-gated service on this agent.

**Parameters:**
- `name` (`str`): Service name. Also used as the MCP tool name when exported via `as_mcp_server()`.
- `min_trust` (`float`): Minimum trust score required to call this service. `0.0` means no trust minimum (bootstrap still applies). Defaults to `0.0`.
- `interaction_type` (`Optional[str]`): Override interaction type recorded for this service. Defaults to `name`.

**The decorated function must have the signature:**
```python
async def handler(data: dict, ctx: TrustContext) -> dict:
    ...
```
Synchronous functions are also supported; `agent.py` detects the coroutine flag at call time.

**Returns:** The original function, unmodified (for testability).

```python
@agent.service("analyze", min_trust=0.3, interaction_type="analysis")
async def run_analysis(data: dict, ctx: TrustContext) -> dict:
    """Analyze input data. Requires trust >= 0.3."""
    if ctx.is_bootstrap:
        print("Bootstrap caller — trust gate bypassed")
    return {"result": data.get("input", "").upper()}

# Registered under "analyze", recorded as "analysis" interaction type
```

---

#### `async handle_service_call(service_name: str, data: Dict[str, Any], caller_pubkey: str) -> Tuple[bool, str, Any]`

Handle an incoming service call with full trust gating. This is the server-side entry point called by `call_service()` and the MCP server wrapper.

**Parameters:**
- `service_name` (`str`): Name of the registered service to invoke.
- `data` (`Dict[str, Any]`): Payload dict passed to the handler.
- `caller_pubkey` (`str`): Hex-encoded public key of the caller.

**Returns:** `Tuple[bool, str, Any]` — `(accepted, reason, result)`

| `accepted` | `reason` | `result` | Condition |
|-----------|----------|----------|-----------|
| `False` | `"Unknown service: <name>"` | `None` | Service not registered |
| `False` | `"Trust gate denied for '<name>': trust <x> < <y>"` | `None` | Trust check failed |
| `True` | `"<service_name> completed"` | `dict` from handler | Handler succeeded |
| `True` | `"<service_name> failed"` | `None` | Handler raised an exception (logged) |
| `False` | TrustGateError message | `None` | Handler raised `TrustGateError` explicitly |

**Trust gate logic:**
1. Look up the service registration. If not found, return `(False, "Unknown service: ...", None)`.
2. Compute `caller_trust = check_trust(caller_pubkey)`.
3. Determine `caller_history`.
4. If `caller_history >= bootstrap_interactions` and `caller_trust < reg.min_trust`: deny.
5. Otherwise: build a `TrustContext` and invoke the handler.

```python
accepted, reason, result = await provider.handle_service_call(
    "analyze",
    {"input": "hello"},
    caller_pubkey=caller.pubkey,
)
if accepted:
    print(result)
else:
    print(f"Denied: {reason}")
```

---

#### `async call_service(provider: TrustAgent, service_name: str, data: Optional[Dict] = None) -> Tuple[bool, str, Any]`

Call a service on another `TrustAgent` with bilateral trust recording.

**Parameters:**
- `provider` (`TrustAgent`): The agent providing the service.
- `service_name` (`str`): Name of the service to call on the provider.
- `data` (`Optional[Dict[str, Any]]`): Payload dict. Defaults to `{}`.

**Returns:** Same `Tuple[bool, str, Any]` as `handle_service_call`.

**v2 recording behavior (both agents have nodes):**
- If the call is accepted: creates a proposal half-block (`self._node.protocol.create_proposal()`), receives it on the provider's node (`provider._node.protocol.receive_proposal()`), creates an agreement (`provider._node.protocol.create_agreement()`), and receives it back. All within the same process.
- If the call is denied: falls back to creating a lightweight v1 record with `outcome="denied"` in the caller's record store. This ensures denial patterns are visible to the trust engine without creating orphan half-blocks.
- If v2 trust recording fails for any reason: the error is logged but the caller still receives the correct `(accepted, reason, result)` return value. Trust machinery never breaks agent calls.

**v1 recording behavior (no nodes):**
- Creates a bilateral `InteractionRecord` signed by both identities via `create_record()`.
- Verifies the record via `verify_record()`.
- Adds the record to `self.store` and `provider.store` (if they are different instances).

```python
accepted, reason, result = await caller.call_service(
    provider=provider_agent,
    service_name="compute",
    data={"x": 42},
)
print(f"Accepted: {accepted}, Reason: {reason}, Result: {result}")
```

---

#### `async call_remote_service(peer_url: str, service_name: str, data: Optional[Dict] = None, *, peer_pubkey: Optional[str] = None, sidecar_url: Optional[str] = None) -> Tuple[bool, str, Any]`

Call a remote agent's service over HTTP. Supports transparent sidecar proxy mode and optional trust recording via the sidecar's `/propose` endpoint.

**Parameters:**
- `peer_url` (`str`): Base HTTP URL of the remote agent (e.g. `"http://host:8080"`). The method POSTs to `{peer_url}/{service_name}`.
- `service_name` (`str`): Service/tool name to invoke.
- `data` (`Optional[Dict[str, Any]]`): Payload dict. Defaults to `{}`.
- `peer_pubkey` (`Optional[str]`): Hex-encoded public key of the remote agent. Required for sidecar trust recording; ignored if `sidecar_url` is also `None` and `TRUSTCHAIN_HTTP` env var is unset.
- `sidecar_url` (`Optional[str]`): URL of the local TrustChain sidecar (e.g. `"http://localhost:8202"`). If `None`, falls back to the `TRUSTCHAIN_HTTP` environment variable.

**Returns:** `Tuple[bool, str, Any]`
- Success: `(True, "<service_name> completed", result_dict)` — `result_dict` is the parsed JSON response body.
- Failure: `(False, "Remote call failed: <exception>", None)` — any network or HTTP error.

**HTTP request format:**

The method POSTs the following JSON body to `{peer_url}/{service_name}`:
```json
{
  "data": { ... },
  "caller_pubkey": "<this agent's pubkey>"
}
```

**Proxy behavior:**

If the `HTTP_PROXY` environment variable is set, `urllib.request` uses it automatically. This enables transparent proxy mode: all HTTP traffic is intercepted by the TrustChain sidecar, which records the bilateral trust interaction without any application-level changes.

**Sidecar trust recording:**

When `sidecar_url` (or `TRUSTCHAIN_HTTP` env var) is set and `peer_pubkey` is provided, the method also POSTs a propose request to `{sidecar_url}/propose`:
```json
{
  "counterparty_pubkey": "<peer_pubkey>",
  "transaction": {
    "interaction_type": "<service_name>",
    "outcome": "completed",
    "timestamp": 1748000000000
  }
}
```
If this secondary request fails, a warning is logged but the result is still returned normally.

```python
# Transparent proxy mode (set HTTP_PROXY before calling)
import os
os.environ["HTTP_PROXY"] = "http://localhost:8203"
accepted, reason, result = await agent.call_remote_service(
    "http://peer-host:8080",
    "analyze",
    {"input": "data"},
)

# Explicit sidecar with trust recording
accepted, reason, result = await agent.call_remote_service(
    "http://peer-host:8080",
    "analyze",
    {"input": "data"},
    peer_pubkey="a3f1b2c4...",
    sidecar_url="http://localhost:8202",
)
```

---

#### `as_mcp_server(name: Optional[str] = None) -> FastMCP`

Export all registered services as a `FastMCP` server. Each service becomes an MCP tool. The server can be run standalone or mounted in the TrustChain gateway.

**Parameters:**
- `name` (`Optional[str]`): MCP server name. Defaults to `"TrustAgent:<self.name>"`.

**Returns:** `FastMCP` instance with all registered services as tools, plus a built-in `trustchain_agent_info` tool.

**Generated tools:**

For each registered service `"svc"`:
- Tool name: `"svc"`
- Tool description: `"[min_trust=<x>] <handler docstring or svc name>"`
- Parameters: `data: Optional[dict] = None`, `caller_pubkey: str = ""`
- Behavior: calls `handle_service_call(svc, data, caller_pubkey)`. Returns `"DENIED: <reason>"` if `caller_pubkey` is missing or trust gate fails. Returns `"OK: <reason>\nResult: <result>"` on success.

**Note:** `caller_pubkey` is required for trust gating. If it is missing or empty, the call is immediately denied with `"DENIED: caller_pubkey is required for trust-gated services"`.

**Built-in tool — `trustchain_agent_info`:**
- No parameters.
- Returns a text string with agent name, public key (truncated), trust score, interaction count, chain integrity, and list of registered services.

```python
mcp = agent.as_mcp_server(name="My Trust Agent")
mcp.run()  # starts the MCP server
```

```python
# Mount in a gateway
from fastmcp import FastMCP

gateway = FastMCP("gateway")
gateway.mount(agent.as_mcp_server(), namespace="myagent")
```

---

### Internal: `_ServiceRegistration`

A private dataclass (`agent_os.agent._ServiceRegistration`) that stores registered service metadata. Not part of the public API.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Service name |
| `handler` | `Callable` | The decorated function |
| `min_trust` | `float` | Minimum trust threshold |
| `interaction_type` | `str` | Interaction type for records |

---

### `__repr__`

```python
str(agent)
# "TrustAgent(name='my-agent', pubkey=a3f1b2c4..., trust=0.000, mode=v1, services=['compute', 'analyze'])"
```

---

## agent_os.TrustContext

**Module:** `agent_os.context`

A dataclass injected into every service handler. Provides the caller's identity information, trust score, access to the record store, and convenience properties.

### Definition

```python
@dataclass
class TrustContext:
    caller_pubkey: str
    caller_trust: float
    caller_history: int
    agent_identity: Identity
    store: RecordStore
    bootstrap_interactions: int = 3
    node: object = None
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `caller_pubkey` | `str` | Full hex Ed25519 public key of the calling agent. |
| `caller_trust` | `float` | Trust score of the caller at the time of the call, in `[0.0, 1.0]`. |
| `caller_history` | `int` | Number of recorded interactions the caller has. Used to determine bootstrap status. |
| `agent_identity` | `Identity` | The `Identity` object of the agent handling the request (not the caller). Gives access to the agent's own signing key and public key. |
| `store` | `RecordStore` | The v1 `RecordStore` associated with this agent. In v2 mode, the block store is accessed via `ctx.node.store`. |
| `bootstrap_interactions` | `int` | Bootstrap threshold copied from the agent. Default `3`. |
| `node` | `object` | The v2 `TrustChainNode` if the agent is running in v2 mode, else `None`. |

### Properties

#### `is_trusted: bool`

Whether the caller has any established trust. Returns `True` if `caller_trust > 0.0`.

```python
async def handler(data: dict, ctx: TrustContext) -> dict:
    if not ctx.is_trusted:
        return {"warning": "Caller has no established trust"}
    return {"data": process(data)}
```

#### `is_bootstrap: bool`

Whether the caller is still in bootstrap mode, meaning they have fewer than `bootstrap_interactions` recorded interactions. Bootstrap callers pass trust gates regardless of their trust score.

```python
async def handler(data: dict, ctx: TrustContext) -> dict:
    if ctx.is_bootstrap:
        # New agent — be helpful but cautious
        return {"result": "limited_access"}
    # Established agent
    return {"result": "full_access"}
```

### Methods

#### `check_trust(pubkey: str) -> float`

Look up the trust score for any agent by their public key. Uses the v1 `compute_trust()` against the context's record store. Does not use the v2 `TrustEngine` even in v2 mode — for v2 trust queries within handlers, access `ctx.node` directly.

**Parameters:**
- `pubkey` (`str`): Hex-encoded public key of any agent.

**Returns:** `float` in `[0.0, 1.0]`.

```python
async def handler(data: dict, ctx: TrustContext) -> dict:
    third_party = data.get("recommend_pubkey")
    if third_party:
        score = ctx.check_trust(third_party)
        return {"recommendation_trust": score}
```

#### `classmethod create(caller_pubkey: str, agent_identity: Identity, store: RecordStore) -> TrustContext`

Factory classmethod for constructing a `TrustContext` from primitives. Computes `caller_trust` and `caller_history` from the store automatically.

**Parameters:**
- `caller_pubkey` (`str`): Caller's public key.
- `agent_identity` (`Identity`): The handling agent's identity.
- `store` (`RecordStore`): The agent's record store.

**Returns:** A fully populated `TrustContext` with default `bootstrap_interactions=3` and `node=None`.

```python
ctx = TrustContext.create(
    caller_pubkey=caller.pubkey,
    agent_identity=agent.identity,
    store=agent.store,
)
```

---

## agent_os.decorators

**Module:** `agent_os.decorators`

Standalone decorators for trust gating and interaction recording. These can be applied to service handlers independently of `TrustAgent`, or stacked together.

### `TrustGateError`

```python
class TrustGateError(Exception):
    pass
```

Raised by the `@trust_gate` decorator (and by handler code directly) when a caller fails a trust gate check. When `TrustAgent.handle_service_call()` catches a `TrustGateError`, it returns `(False, str(e), None)` to the caller.

```python
from agent_os.decorators import TrustGateError

# Raise manually in a handler for fine-grained control
async def handler(data: dict, ctx: TrustContext) -> dict:
    if data.get("privileged") and ctx.caller_trust < 0.8:
        raise TrustGateError(
            f"Privileged operations require trust >= 0.8, got {ctx.caller_trust:.3f}"
        )
    return {"result": "ok"}
```

---

### `trust_gate(min_trust: float = 0.0, allow_bootstrap: bool = True)`

Decorator that blocks callers below a trust threshold. Finds the `TrustContext` in the decorated function's arguments (positional or keyword) and checks `ctx.caller_trust`.

**Parameters:**
- `min_trust` (`float`): Minimum trust score required. Default `0.0` (no minimum).
- `allow_bootstrap` (`bool`): If `True`, callers with `caller_history < 3` pass through regardless of trust score. Default `True`.

**Raises:** `TrustGateError` if the caller's trust is below `min_trust` and bootstrap does not apply.

**Behavior:** Works on both `async` and synchronous functions. If no `TrustContext` is found in the arguments, the gate is skipped silently.

**Important:** When used inside `TrustAgent.service()`, the agent already performs trust gating via its `min_trust` parameter. Using `@trust_gate` inside the handler adds an additional, finer-grained check.

```python
from agent_os.decorators import trust_gate

@agent.service("admin", min_trust=0.3)
@trust_gate(min_trust=0.7, allow_bootstrap=False)
async def admin_op(data: dict, ctx: TrustContext) -> dict:
    # This handler requires trust >= 0.7, even if the service allows 0.3
    # Bootstrap agents are also denied (allow_bootstrap=False)
    return {"admin": "done"}
```

**Stacking with `@record_interaction`:**

```python
@agent.service("op")
@trust_gate(min_trust=0.5)
@record_interaction(interaction_type="custom_op")
async def my_op(data: dict, ctx: TrustContext) -> dict:
    return {}
```

---

### `record_interaction(interaction_type: str = "service")`

Decorator that auto-creates a bilateral record after the handler executes.

**Parameters:**
- `interaction_type` (`str`): Interaction type label for the record. Default `"service"`.

**Behavior:**
- **v2 mode** (context has `node`): This decorator is a no-op. Recording is handled at the `TrustAgent.call_service()` level with proper half-block semantics. Double-recording is prevented by the `if ctx.node is not None: return` guard.
- **v1 mode**: Creates an agent-attested bilateral record after the handler returns. If the handler raises `TrustGateError`, no record is created. If the handler raises any other exception, a record with `outcome="failed"` is created, then the exception is re-raised.

**Note on v1 signing:** In v1 mode, the agent signs on behalf of both parties (itself and the caller). This is a known limitation — bilateral Ed25519 verification requires both private keys, which is only possible in v2 mode where both agents run nodes.

```python
@agent.service("compute")
@record_interaction(interaction_type="computation")
async def compute(data: dict, ctx: TrustContext) -> dict:
    return {"result": data.get("x", 0) * 2}
```

---

## gateway.config

**Module:** `gateway.config`

Configuration dataclasses for the TrustChain MCP Gateway.

### `UpstreamServer`

```python
@dataclass
class UpstreamServer:
    name: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    namespace: str = ""
    trust_threshold: float = 0.0
    url: Optional[str] = None
    trustchain_url: Optional[str] = None
```

Configuration for a single upstream MCP server that the gateway proxies.

**Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Unique server name. Used as identity file name when `upstream_identity_dir` is set. |
| `command` | `str` | `""` | Shell command to launch a stdio MCP server (e.g. `"npx"`). Empty for URL-based upstreams. |
| `args` | `List[str]` | `[]` | Arguments for the launch command (e.g. `["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]`). |
| `env` | `Dict[str, str]` | `{}` | Extra environment variables for the stdio process. |
| `namespace` | `str` | `""` | MCP tool namespace prefix. Defaults to `name` if empty (applied in `__post_init__`). Tool `"read_file"` under namespace `"fs"` becomes `"fs_read_file"`. |
| `trust_threshold` | `float` | `0.0` | Per-upstream minimum trust score. Overrides the gateway's `default_trust_threshold` for this server only. |
| `url` | `Optional[str]` | `None` | HTTP/SSE URL for a remote MCP server. If set, overrides `command`/`args`. |
| `trustchain_url` | `Optional[str]` | `None` | v2: The TrustChain node HTTP endpoint of this upstream (e.g. `"http://peer:8200"`). Used by `UpstreamRegistry` for direct protocol communication. |

**`__post_init__` behavior:** If `namespace` is empty, it is set to `name`.

**Example — stdio upstream:**

```python
from gateway.config import UpstreamServer

fs_server = UpstreamServer(
    name="filesystem",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    namespace="fs",
    trust_threshold=0.3,
)
```

**Example — HTTP upstream:**

```python
web_server = UpstreamServer(
    name="web-agent",
    url="http://agent-host:8080/mcp",
    namespace="web",
    trust_threshold=0.5,
    trustchain_url="http://agent-host:8200",  # v2 only
)
```

---

### `GatewayConfig`

```python
@dataclass
class GatewayConfig:
    upstreams: List[UpstreamServer] = field(default_factory=list)
    identity_path: Optional[str] = None
    store_path: Optional[str] = None
    upstream_identity_dir: Optional[str] = None
    default_trust_threshold: float = 0.0
    bootstrap_interactions: int = 3
    server_name: str = "TrustChain Gateway"
    use_v2: bool = False
```

Top-level configuration for `create_gateway()`.

**Fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `upstreams` | `List[UpstreamServer]` | `[]` | List of upstream server configurations to proxy. |
| `identity_path` | `Optional[str]` | `None` | Path to persist the gateway's own Ed25519 key. If `None`, generates an ephemeral key each restart. |
| `store_path` | `Optional[str]` | `None` | Path for the gateway's `FileRecordStore`. If `None`, uses an in-memory store. |
| `upstream_identity_dir` | `Optional[str]` | `None` | Directory where upstream server identity `.key` files are persisted. Without this, upstream identities are ephemeral and trust history is lost on restart. |
| `default_trust_threshold` | `float` | `0.0` | Global minimum trust threshold applied to all upstreams unless overridden by `UpstreamServer.trust_threshold`. |
| `bootstrap_interactions` | `int` | `3` | Number of free interactions before trust gating activates for any upstream. |
| `server_name` | `str` | `"TrustChain Gateway"` | Name of the FastMCP server. |
| `use_v2` | `bool` | `False` | Enable v2 mode: creates a `GatewayNode` and `TrustEngine` for half-block protocol. When `True`, trust scoring uses `TrustEngine` instead of `compute_trust()`. |

**Example:**

```python
from gateway.config import GatewayConfig, UpstreamServer

config = GatewayConfig(
    server_name="Production Gateway",
    identity_path="./gateway.key",
    store_path="./gateway_records.json",
    upstream_identity_dir="./upstream_keys/",
    default_trust_threshold=0.1,
    bootstrap_interactions=5,
    use_v2=True,
    upstreams=[
        UpstreamServer(
            name="data-agent",
            url="http://data-agent:8080/mcp",
            namespace="data",
            trust_threshold=0.4,
        ),
        UpstreamServer(
            name="compute-agent",
            command="python",
            args=["compute_server.py"],
            namespace="compute",
        ),
    ],
)
```

---

## gateway.server

**Module:** `gateway.server`

### `create_gateway(config: GatewayConfig, store: Optional[RecordStore] = None) -> FastMCP`

Create a fully wired TrustChain MCP Gateway from a `GatewayConfig`.

**Parameters:**
- `config` (`GatewayConfig`): Full gateway configuration.
- `store` (`Optional[RecordStore]`): Pre-existing record store. If `None`, a store is created based on `config.store_path` (or in-memory if unset).

**Returns:** `FastMCP` instance with all upstreams mounted, `TrustChainMiddleware` attached, and trust query tools registered.

**Creation steps (in order):**

1. **Identity**: Load or create the gateway's Ed25519 identity from `config.identity_path`.
2. **Record store**: Use `store` if provided, else create `FileRecordStore(config.store_path)` or `RecordStore()`.
3. **Registry**: Create `UpstreamRegistry(gateway_identity, identity_dir=config.upstream_identity_dir)`.
4. **FastMCP server**: Create `FastMCP(config.server_name)`.
5. **Mount upstreams**: For each `UpstreamServer` in `config.upstreams`, register with the registry and mount as a proxy (HTTP via `create_proxy(url)` or stdio via `create_proxy(config_dict)`).
6. **Recorder**: Create `InteractionRecorder(gateway_identity, store)`.
7. **v2 node** (if `config.use_v2`): Create `MemoryBlockStore`, `GatewayNode`, and `TrustEngine`.
8. **Middleware**: Attach `TrustChainMiddleware` to the server.
9. **Trust tools**: Call `register_trust_tools()` to add 6 native MCP tools.

**Example:**

```python
from gateway.config import GatewayConfig, UpstreamServer
from gateway.server import create_gateway

config = GatewayConfig(
    upstreams=[
        UpstreamServer(
            name="search",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-brave-search"],
            env={"BRAVE_API_KEY": "your-key"},
            namespace="search",
            trust_threshold=0.2,
        )
    ],
    identity_path="./gateway.key",
    store_path="./records.json",
    upstream_identity_dir="./keys/",
)

mcp = create_gateway(config)
mcp.run()
```

---

### `create_gateway_from_dict(config_dict: dict) -> FastMCP`

Create a gateway from a plain Python dictionary. Convenience wrapper around `create_gateway`.

**Parameters:**
- `config_dict` (`dict`): Configuration dictionary. Keys map to `GatewayConfig` fields. The `"upstreams"` key is a list of dicts, each mapping to `UpstreamServer` fields.

**Returns:** `FastMCP` instance (same as `create_gateway`).

**Recognized keys:**

| Key | Type | Description |
|-----|------|-------------|
| `server_name` | `str` | FastMCP server name |
| `identity_path` | `str` | Gateway identity file |
| `store_path` | `str` | Record store file |
| `upstream_identity_dir` | `str` | Upstream key directory |
| `default_trust_threshold` | `float` | Global trust threshold |
| `bootstrap_interactions` | `int` | Bootstrap window |
| `use_v2` | `bool` | Enable v2 protocol |
| `upstreams` | `list[dict]` | List of upstream server configs |

**Example:**

```python
from gateway.server import create_gateway_from_dict

mcp = create_gateway_from_dict({
    "server_name": "My Gateway",
    "store_path": "./trustchain_records.json",
    "default_trust_threshold": 0.0,
    "upstreams": [
        {
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "namespace": "fs",
            "trust_threshold": 0.3,
        }
    ],
})
mcp.run()
```

---

## gateway.middleware.TrustChainMiddleware

**Module:** `gateway.middleware`

FastMCP middleware that intercepts every tool call, performs trust gating, records the interaction, and appends a trust annotation to the result.

### Constructor

```python
TrustChainMiddleware(
    registry: UpstreamRegistry,
    recorder: InteractionRecorder,
    store: RecordStore,
    default_threshold: float = 0.0,
    bootstrap_interactions: int = 3,
    trust_engine: Optional[TrustEngine] = None,
    gateway_node: Optional[GatewayNode] = None,
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `registry` | `UpstreamRegistry` | Maps tool names to upstream servers and their identities. |
| `recorder` | `InteractionRecorder` | v1 interaction recorder. |
| `store` | `RecordStore` | v1 record store for trust computation. |
| `default_threshold` | `float` | Global minimum trust score. Default `0.0`. |
| `bootstrap_interactions` | `int` | Bootstrap interaction window. Default `3`. |
| `trust_engine` | `Optional[TrustEngine]` | v2 trust engine. If provided, used instead of `compute_trust()`. |
| `gateway_node` | `Optional[GatewayNode]` | v2 gateway node. If provided, creates half-block proposals instead of v1 records. |

### `async on_call_tool(context: MiddlewareContext, call_next)`

The middleware hook called for every tool invocation.

**Per-call logic:**

1. If the tool name starts with `"trustchain_"` (native tool): forward immediately without gating.
2. Identify the upstream server via `registry.server_for_tool(tool_name)`. If no server found: log a warning and forward.
3. Look up the upstream's `Identity`. If missing: raise `ToolError`.
4. Compute `trust_score` via `TrustEngine` (v2) or `compute_trust()` (v1).
5. Get the per-server `threshold` from `registry.threshold_for()`.
6. Compute `interaction_count` — v2: bidirectional count from `GatewayNode._count_peer_interactions()`; v1: record count.
7. If `interaction_count >= bootstrap_interactions` and `trust_score < threshold`: raise `ToolError` with a detailed message.
8. Forward the call via `call_next(context)`. If it raises, record a `"failed"` interaction and re-raise.
9. Record the interaction: v2 creates a Proposal half-block; v1 uses `InteractionRecorder.record()`.
10. Re-compute trust after recording and append an annotation to the result:
    ```
    [TrustChain] server=<name> trust=<score> outcome=<completed|failed>
    ```

**v2 half-block semantics:**

The gateway creates only a *Proposal* half-block for each tool call. It does not create the matching *Agreement* — that must come from the upstream agent's own sidecar via the bilateral P2P handshake. Dangling proposals (no matching agreement) are expected and are resolved when the upstream sidecar later calls `/receive_proposal`, or when a crawl/sync delivers the agreement.

**Example trust block error:**

```
ToolError: [TrustChain] BLOCKED: server=filesystem trust=0.120 < threshold=0.300 (interactions=7)
```

---

## gateway.registry.UpstreamRegistry

**Module:** `gateway.registry`

Maps upstream server names to TrustChain identities, routes tool names to their owning servers, and manages optional identity persistence.

### Constructor

```python
UpstreamRegistry(
    gateway_identity: Identity,
    identity_dir: Optional[str] = None,
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `gateway_identity` | `Identity` | The gateway's own identity. |
| `identity_dir` | `Optional[str]` | Directory for persisting upstream `.key` files. If `None`, upstream identities are ephemeral. |

### Methods

#### `register_server(config: UpstreamServer) -> Identity`

Register an upstream server and load or create its identity.

- If `identity_dir` is set: looks for `{identity_dir}/{config.name}.key`. Loads it if found; creates and saves a new one if not.
- If the key file is corrupt: logs a warning and generates a new key (trust history for the old identity is lost).
- Registers `config.trustchain_url` (if set) for v2 protocol communication.

**Returns:** The `Identity` for this upstream server.

#### `register_upstream(name: str, url: str, trustchain_url: str) -> Identity`

Convenience method for registering a v2-aware upstream with explicit URLs. Creates an `UpstreamServer` internally and delegates to `register_server()`.

#### `register_tool(tool_name: str, server_name: str)`

Explicitly map a tool name to an upstream server. Overrides namespace prefix matching.

#### `register_tools_for_server(tool_names: List[str], server_name: str)`

Map multiple tool names to the same upstream server. Note parameter order: `tool_names` first.

```python
registry.register_tools_for_server(["read_file", "write_file"], "filesystem")
```

#### `server_for_tool(tool_name: str) -> Optional[str]`

Look up which upstream server owns a tool.

1. Checks the explicit `_tool_to_server` mapping first.
2. Falls back to namespace prefix matching: a tool `"fs_read_file"` matches a server with `namespace="fs"` (prefix `"fs_"`).

**Returns:** Server name string, or `None` if not found.

#### `identity_for(server_name: str) -> Optional[Identity]`

Get the `Identity` object for an upstream server. Returns `None` if the server is not registered.

#### `config_for(server_name: str) -> Optional[UpstreamServer]`

Get the `UpstreamServer` configuration for a server. Returns `None` if not found.

#### `threshold_for(server_name: str, default: float = 0.0) -> float`

Get the trust threshold for a server. Returns `config.trust_threshold` if found, else `default`.

#### `trustchain_url_for(server_name: str) -> Optional[str]`

Get the TrustChain node URL for an upstream server (v2 only). Returns `None` if not set.

### Properties

#### `server_names: List[str]`

List of all registered upstream server names.

---

## gateway.node.GatewayNode

**Module:** `gateway.node`

A `TrustChainNode` subclass that adds trust scoring, bidirectional interaction counting with TTL cache, and a `trusted_transact()` method. Used by the gateway in v2 mode.

### Constructor

```python
GatewayNode(
    identity: Identity,
    store: BlockStore,
    host: str = "0.0.0.0",
    port: int = 8100,
    seed_nodes: Optional[list] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `identity` | `Identity` | required | Gateway's Ed25519 identity. |
| `store` | `BlockStore` | required | Block store for the half-block chain. |
| `host` | `str` | `"0.0.0.0"` | Listen host. |
| `port` | `int` | `8100` | Listen port. |
| `seed_nodes` | `Optional[list]` | `None` | Seed pubkeys for NetFlow. Defaults to `[identity.pubkey_hex]`. |

Creates an internal `TrustEngine` seeded on the provided `seed_nodes`.

### Methods

#### `get_trust_score(peer_pubkey: str) -> float`

Compute the trust score for a peer using the `TrustEngine`.

#### `_count_peer_interactions(peer_pubkey: str) -> int`

Count total interactions with a peer in both directions:
- Outbound: blocks on our chain where `link_public_key == peer_pubkey`.
- Inbound: blocks on their chain where `link_public_key == self.pubkey`.

Results are cached for `_CACHE_TTL = 5.0` seconds. The cache is bounded to prevent unbounded memory growth.

#### `invalidate_count_cache(peer_pubkey: str) -> None`

Remove the cached interaction count for a peer. Call after recording a new interaction so the next count query reflects it immediately.

#### `get_chain_integrity(peer_pubkey: str) -> float`

Get chain integrity for a peer via `TrustEngine.compute_chain_integrity()`.

#### `async trusted_transact(peer_pubkey: str, transaction: Dict[str, Any], min_trust: float = 0.0, bootstrap_interactions: int = 3) -> Dict[str, Any]`

Execute a trust-gated transaction with a peer.

**Parameters:**
- `peer_pubkey`: Target peer's public key.
- `transaction`: Transaction payload dict (e.g. `{"interaction_type": "query", "outcome": "completed"}`).
- `min_trust`: Minimum trust required. Default `0.0`.
- `bootstrap_interactions`: Bootstrap window. Default `3`.

**Returns:** `dict` with keys:

| Key | Type | Description |
|-----|------|-------------|
| `accepted` | `bool` | Whether the transaction was accepted. |
| `proposal` | `Block` or `None` | The created proposal half-block. |
| `agreement` | `Block` or `None` | The agreement half-block, or `None` if peer rejected. |
| `trust_score` | `float` | Updated trust score after the transaction. |
| `error` | `str` or `None` | Error message, or `None` on success. |

If trust < threshold (and not bootstrap): returns `{"accepted": False, "trust_score": <score>, "error": "Trust ... < threshold ..."}`.

---

## gateway.recorder.InteractionRecorder

**Module:** `gateway.recorder`

Creates and stores bilateral signed v1 records for gateway-to-upstream interactions. Used in v1 mode (when no `GatewayNode` is configured).

### Constructor

```python
InteractionRecorder(gateway_identity: Identity, store: RecordStore)
```

### Methods

#### `record(upstream_identity: Identity, interaction_type: str = "tool_call", outcome: str = "completed") -> InteractionRecord`

Create a bilateral signed record between the gateway and an upstream server.

**Parameters:**
- `upstream_identity`: The upstream server's `Identity`.
- `interaction_type`: Interaction label (e.g. `"tool:read_file"`). Default `"tool_call"`.
- `outcome`: Outcome label (`"completed"` or `"failed"`). Default `"completed"`.

**Returns:** The created and stored `InteractionRecord`.

**Raises:** `RuntimeError` if signature verification fails on the created record (should never happen in normal operation).

**Side effects:** After recording, checks chain integrity for the upstream's pubkey and logs a warning if `integrity < 1.0`.

---

## gateway.trust_tools

**Module:** `gateway.trust_tools`

Registers native trust query MCP tools on the gateway server, and provides the `verify_caller()` signature verification helper.

### `verify_caller(pubkey: str, signature: str, nonce: str, tool_name: str) -> bool`

Verify that the caller owns the Ed25519 key they claim.

**Parameters:**
- `pubkey` (`str`): Hex-encoded 32-byte Ed25519 public key claimed by the caller.
- `signature` (`str`): Hex-encoded 64-byte Ed25519 signature.
- `nonce` (`str`): Caller-supplied nonce string (recommended: Unix timestamp as string).
- `tool_name` (`str`): The MCP tool being called (bound into the challenge message).

**Returns:** `True` if the signature is valid, `False` otherwise. Also returns `False` for malformed hex or incorrect key/signature lengths.

**Challenge message format:**
```
"{pubkey}:{tool_name}:{nonce}".encode("utf-8")
```

**Example — signing a tool call (caller side):**

```python
import time
from trustchain.identity import Identity

identity = Identity.load("./my.key")
pubkey = identity.pubkey_hex
tool_name = "trustchain_check_trust"
nonce = str(int(time.time()))
message = f"{pubkey}:{tool_name}:{nonce}".encode("utf-8")
signature = identity.sign(message).hex()

# Now pass pubkey, signature, nonce to the tool call
```

---

### `register_trust_tools(mcp, registry, store, trust_engine=None, bootstrap_interactions=3)`

Register six native trust query MCP tools on a `FastMCP` server instance.

**Parameters:**
- `mcp`: The `FastMCP` server to register tools on.
- `registry` (`UpstreamRegistry`): For server identity and threshold lookups.
- `store` (`RecordStore`): v1 record store for trust data.
- `trust_engine` (`Optional[TrustEngine]`): If provided, uses v2 `TrustEngine` for scoring and `BlockStore` for data.
- `bootstrap_interactions` (`int`): Bootstrap window. Default `3`.

All six tools accept optional caller authentication parameters:
- `caller_pubkey: str = ""` — Caller's hex public key.
- `caller_signature: str = ""` — Ed25519 signature over the challenge.
- `caller_nonce: str = ""` — Nonce string for replay protection.

If `caller_pubkey` is provided but `caller_signature` is absent: a deprecation warning is logged and the call is allowed through (backward compatibility). If `caller_signature` is present but does not verify: the tool returns an error string.

### Registered Tools

#### `trustchain_check_trust`

Check the current trust score for a named upstream MCP server.

**Parameters:** `server_name: str`, plus optional auth params.

**Returns:** Multi-line string with server name, trust score, threshold, interaction count, status (bootstrap/established), public key prefix, and a warning if trust is below threshold.

```
Server: filesystem
Trust Score: 0.423
Threshold: 0.300
Interactions: 12
Status: established
Public Key: a3f1b2c4d5e6f7a8...
```

---

#### `trustchain_get_history`

Get recent interaction history with an upstream MCP server.

**Parameters:** `server_name: str`, `limit: int = 10`, plus optional auth params.

**Returns:** Formatted interaction log.
- **v2**: Shows block sequence number, hash, interaction type, and outcome from the `BlockStore`.
- **v1**: Shows record hash, interaction type, outcome, sequence numbers, and verification status.

---

#### `trustchain_list_servers`

List all upstream MCP servers and their current trust scores.

**Parameters:** Optional auth params only.

**Returns:** Formatted table of all servers with trust score, threshold, interaction count, status, and TrustChain URL (if configured).

---

#### `trustchain_verify_chain`

Verify blockchain integrity for an upstream MCP server.

**Parameters:** `server_name: str`, plus optional auth params.

**Returns:** Report including chain length, integrity score, combined trust score, and status (`VALID` or `INVALID` with error details).

---

#### `trustchain_crawl`

Crawl a server's TrustChain data and report any tampering.

**Parameters:** `server_name: str`, plus optional auth params.

**Returns:** Either `"chain is clean (N blocks)"` or a detailed issue report covering chain gaps, hash breaks, signature failures, entanglement issues, and orphan proposals.

---

#### `trustchain_trust_score`

Get a detailed trust score breakdown for a server.

**Parameters:** `server_name: str`, plus optional auth params.

**Returns:**
- **v2**: Shows combined trust, chain integrity component (weight 0.3), NetFlow component (weight 0.4), and statistical component (weight 0.3).
- **v1**: Shows base trust and chain trust.

```
Server: filesystem
Combined Trust: 0.387
  Chain Integrity: 0.500 (weight: 0.3)
  NetFlow Score: 0.350 (weight: 0.4)
  Statistical Score: 0.420 (weight: 0.3)
```

---

## tc_frameworks.base.FrameworkAdapter

**Module:** `tc_frameworks.base`

Abstract base class for all framework adapters. Each adapter wraps a framework-specific agent as a FastMCP server for trust-gated access through the TrustChain gateway.

### Class Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `framework_name` | `str` | `"unknown"` | Human-readable framework name. Override in subclasses. |
| `framework_version` | `str` | `"unknown"` | Framework version string. Override in subclasses. |

### Abstract Methods

#### `create_mcp_server() -> FastMCP`

Create a `FastMCP` server exposing this framework's agent capabilities. The returned server can be mounted directly or registered with the gateway.

Must be implemented by all concrete adapter subclasses.

```python
mcp = adapter.create_mcp_server()
gateway.mount(mcp, namespace="my_framework")
```

#### `get_tool_names() -> List[str]`

Return the list of MCP tool names this adapter exposes. Used for registry mapping and documentation.

Must be implemented by all concrete adapter subclasses.

### Properties

#### `info: Dict[str, Any]`

Metadata dictionary with the following keys:

```python
{
    "framework": str,       # framework_name
    "version": str,         # framework_version
    "tools": List[str],     # get_tool_names()
    "mcp_support": bool,    # has_native_mcp
    "python_native": bool,  # is_python_native
}
```

#### `has_native_mcp: bool`

Whether the framework has built-in MCP support. Default `True`. Overridden to `False` by `ElizaOSAdapter` (TypeScript, bridged via REST).

#### `is_python_native: bool`

Whether the framework has a native Python SDK. Default `True`. Overridden to `False` by `ElizaOSAdapter`.

---

## Framework Adapters

All adapters live in `tc_frameworks/adapters/`. Each adapter:
1. Lazily builds the underlying framework agent on first call (cached as `self._agent` or similar).
2. Wraps it in a `FastMCP` server with the documented tool(s).
3. Can be mounted in any gateway or used standalone.

### Installation

```bash
pip install crewai                           # CrewAI
pip install openai-agents                    # OpenAI Agents SDK
pip install ag2                              # AutoGen/AG2
pip install langgraph langchain-core         # LangGraph
pip install google-adk                       # Google ADK
pip install anthropic                        # Claude Agent
pip install smolagents                       # HuggingFace Smolagents
pip install pydantic-ai                      # PydanticAI
pip install semantic-kernel                  # Microsoft Semantic Kernel
pip install agno                             # Agno (ex-Phidata)
pip install llama-index                      # LlamaIndex
# ElizaOS: npm install -g @elizaos/cli       # TypeScript — use REST bridge
```

---

### `LangGraphAdapter`

**Module:** `tc_frameworks.adapters.langgraph_adapter`
**Requires:** `pip install langgraph langchain-openai` (or `langchain-anthropic`, `langchain-google-genai`)

Wraps a LangGraph ReAct agent.

```python
LangGraphAdapter(
    tools: Optional[List] = None,
    model_name: str = "gpt-4o-mini",
    model_provider: str = "openai",
    api_key: Optional[str] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tools` | `Optional[List]` | `[]` | LangChain `@tool`-decorated functions to give the agent. |
| `model_name` | `str` | `"gpt-4o-mini"` | LLM model name. |
| `model_provider` | `str` | `"openai"` | Provider: `"openai"`, `"anthropic"`, or `"google"`. |
| `api_key` | `Optional[str]` | `None` | API key. Falls back to environment variable if `None`. |

**Tool:** `react_agent_invoke(message: str) -> str`

Sends `message` to the LangGraph ReAct agent and returns the final message content. The agent is built lazily and cached.

```python
from langchain_core.tools import tool
from tc_frameworks.adapters.langgraph_adapter import LangGraphAdapter

@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression."""
    return str(eval(expression))

adapter = LangGraphAdapter(
    tools=[calculator],
    model_provider="openai",       # or "google", "anthropic"
    model_name="gpt-4o-mini",
)
mcp = adapter.create_mcp_server()
```

---

### `CrewAIAdapter`

**Module:** `tc_frameworks.adapters.crewai_adapter`
**Requires:** `pip install crewai`

Wraps a CrewAI crew. The crew is built from a declarative config dict.

```python
CrewAIAdapter(
    crew_config: Dict[str, Any],
    llm_model: str = "openai/gpt-4o-mini",
    llm_base_url: Optional[str] = None,
    llm_api_key: Optional[str] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `crew_config` | `Dict[str, Any]` | required | Dict with `"agents"` and `"tasks"` keys (see below). |
| `llm_model` | `str` | `"openai/gpt-4o-mini"` | LiteLLM model string (e.g. `"ollama/llama3"`, `"anthropic/claude-3-haiku"`). |
| `llm_base_url` | `Optional[str]` | `None` | Override LLM base URL (e.g. `"http://localhost:11434"` for Ollama). |
| `llm_api_key` | `Optional[str]` | `None` | API key override. |

**`crew_config` format:**

```python
{
    "agents": [
        {
            "role": str,              # Required — agent role name
            "goal": str,              # Required
            "backstory": str,         # Optional
            "allow_delegation": bool, # Optional, default False
        }
    ],
    "tasks": [
        {
            "description": str,       # Required — supports {variable} placeholders
            "expected_output": str,   # Required
            "agent_role": str,        # Optional — matches agent by role
        }
    ]
}
```

**Tool:** `crew_kickoff(inputs: dict = {}) -> str`

Runs the crew asynchronously with the given inputs and returns the result as a string. The crew is built lazily and cached.

```python
from tc_frameworks.adapters.crewai_adapter import CrewAIAdapter

adapter = CrewAIAdapter(
    crew_config={
        "agents": [
            {"role": "Researcher", "goal": "Research {topic}", "backstory": "Expert"},
        ],
        "tasks": [
            {"description": "Research {topic}", "expected_output": "Summary", "agent_role": "Researcher"},
        ],
    },
    llm_model="ollama/llama3",
    llm_base_url="http://localhost:11434",
)
```

---

### `AutoGenAdapter`

**Module:** `tc_frameworks.adapters.autogen_adapter`
**Requires:** `pip install ag2[openai]`

Wraps an AG2 (AutoGen) multi-agent group chat.

```python
AutoGenAdapter(
    agents_config: Optional[List[Dict[str, Any]]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agents_config` | `Optional[List[Dict]]` | Single assistant agent | List of agent configs, each with `"name"` and `"system_message"` keys. |
| `llm_config` | `Optional[Dict]` | `{"model": "gpt-4o-mini"}` | LLM configuration dict. For other providers: include `"api_type"` key (e.g. `"google"`). |

**Tool:** `group_chat_run(message: str, max_turns: int = 3) -> str`

If only one agent is configured, runs it directly. If two or more agents are configured, creates a `GroupChat` with a `GroupChatManager` and initiates a conversation. Returns the last message content.

**Other providers:** Pass `"api_type": "google"` in `llm_config` for Gemini (`GeminiLLMConfigEntry`), or configure per AG2 docs.

```python
from tc_frameworks.adapters.autogen_adapter import AutoGenAdapter

adapter = AutoGenAdapter(
    agents_config=[
        {"name": "planner", "system_message": "Create step-by-step plans."},
        {"name": "coder", "system_message": "Implement the plan in Python."},
    ],
    llm_config={"model": "gpt-4o", "api_key": "sk-..."},
)
```

---

### `OpenAIAgentsAdapter`

**Module:** `tc_frameworks.adapters.openai_agents_adapter`
**Requires:** `pip install openai-agents`

Wraps an OpenAI Agents SDK agent.

```python
OpenAIAgentsAdapter(
    agent_name: str = "assistant",
    instructions: str = "You are a helpful assistant.",
    tools: Optional[List[Callable]] = None,
    model: Any = "gpt-4o-mini",
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_name` | `str` | `"assistant"` | Agent name. |
| `instructions` | `str` | `"You are a helpful assistant."` | System instructions. |
| `tools` | `Optional[List[Callable]]` | `[]` | Tool functions. If not already wrapped with `function_tool()`, they are wrapped automatically. |
| `model` | `Any` | `"gpt-4o-mini"` | Model string or model object. For Gemini via OpenAI-compatible endpoint: pass a configured model object. |

**Tool:** `agent_run(message: str) -> str`

Runs the agent via `Runner.run()` and returns `result.final_output`.

**Other providers:** Use the framework's model configuration. For Gemini, use the OpenAI-compatible endpoint (`generativelanguage.googleapis.com/v1beta/openai/`).

```python
from tc_frameworks.adapters.openai_agents_adapter import OpenAIAgentsAdapter

adapter = OpenAIAgentsAdapter(
    agent_name="researcher",
    instructions="You research topics and summarize findings.",
    model="gpt-4o-mini",
)
```

---

### `GoogleADKAdapter`

**Module:** `tc_frameworks.adapters.google_adk_adapter`
**Requires:** `pip install google-adk`

Wraps a Google ADK `LlmAgent` with persistent session management.

```python
GoogleADKAdapter(
    agent_name: str = "assistant",
    model: str = "gemini-2.0-flash",
    instruction: str = "You are a helpful assistant.",
    tools: Optional[List[Callable]] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_name` | `str` | `"assistant"` | ADK agent name. |
| `model` | `str` | `"gemini-2.0-flash"` | Gemini model name (Google ADK is Gemini-native). |
| `instruction` | `str` | `"You are a helpful assistant."` | Agent instruction. |
| `tools` | `Optional[List[Callable]]` | `[]` | ADK-compatible tool functions. |

**Tool:** `adk_invoke(message: str) -> str`

Sends a message through the ADK `Runner` and collects all response parts. Session infrastructure is initialized lazily with an `asyncio.Lock` to prevent duplicate initialization under concurrent calls.

```python
from tc_frameworks.adapters.google_adk_adapter import GoogleADKAdapter

adapter = GoogleADKAdapter(
    model="gemini-2.0-flash",
    instruction="You are a data analysis assistant.",
)
```

---

### `ClaudeAgentAdapter`

**Module:** `tc_frameworks.adapters.claude_agent_adapter`
**Requires:** `pip install anthropic`

Wraps the Anthropic Claude API as a stateless MCP tool. Each call is a new `messages.create()` request with the configured system prompt.

```python
ClaudeAgentAdapter(
    model: str = "claude-sonnet-4-20250514",
    instructions: str = "You are a helpful assistant.",
    max_tokens: int = 1024,
    api_key: Optional[str] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str` | `"claude-sonnet-4-20250514"` | Anthropic model ID. |
| `instructions` | `str` | `"You are a helpful assistant."` | System prompt. |
| `max_tokens` | `int` | `1024` | Maximum tokens in response. |
| `api_key` | `Optional[str]` | `None` | Anthropic API key. Falls back to `ANTHROPIC_API_KEY` env var. |

**Tool:** `claude_query(message: str) -> str`

Calls `client.messages.create()` in a thread (via `asyncio.to_thread`) and returns `response.content[0].text`.

```python
from tc_frameworks.adapters.claude_agent_adapter import ClaudeAgentAdapter

adapter = ClaudeAgentAdapter(
    model="claude-opus-4-6",
    instructions="You are an expert code reviewer.",
    max_tokens=2048,
)
```

---

### `SmolagentsAdapter`

**Module:** `tc_frameworks.adapters.smolagents_adapter`
**Requires:** `pip install smolagents`

Wraps a HuggingFace Smolagents `CodeAgent` or `ToolCallingAgent`.

```python
SmolagentsAdapter(
    model_id: str = "Qwen/Qwen2.5-Coder-32B-Instruct",
    tools: Optional[List] = None,
    agent_type: str = "code",
    model_type: str = "hf",
    api_key: Optional[str] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model_id` | `str` | `"Qwen/Qwen2.5-Coder-32B-Instruct"` | HuggingFace model ID or LiteLLM model string. |
| `tools` | `Optional[List]` | `[]` | Smolagents tool instances. |
| `agent_type` | `str` | `"code"` | `"code"` for `CodeAgent`, anything else for `ToolCallingAgent`. |
| `model_type` | `str` | `"hf"` | `"hf"` for `HfApiModel`, `"litellm"` for `LiteLLMModel`. |
| `api_key` | `Optional[str]` | `None` | API key (token for HF, key for LiteLLM provider). |

**Tool:** `smolagent_run(message: str) -> str`

Runs the agent synchronously in a thread via `asyncio.to_thread(agent.run, message)`.

```python
from tc_frameworks.adapters.smolagents_adapter import SmolagentsAdapter

adapter = SmolagentsAdapter(
    model_id="Qwen/Qwen2.5-Coder-32B-Instruct",  # default: HF Inference API
    model_type="hf",                                # or "litellm" for OpenAI/Gemini/etc.
)
```

---

### `PydanticAIAdapter`

**Module:** `tc_frameworks.adapters.pydantic_ai_adapter`
**Requires:** `pip install pydantic-ai`

Wraps a PydanticAI `Agent`.

```python
PydanticAIAdapter(
    model: str = "openai:gpt-4o-mini",
    system_prompt: str = "You are a helpful assistant.",
    tools: Optional[List[Callable]] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str` | `"openai:gpt-4o-mini"` | PydanticAI model string (e.g. `"openai:gpt-4o"`, `"google-gla:gemini-2.5-flash"`). |
| `system_prompt` | `str` | `"You are a helpful assistant."` | System prompt for the agent. |
| `tools` | `Optional[List[Callable]]` | `[]` | Tool functions registered via `agent.tool_plain()`. |

**Tool:** `pydantic_ai_run(message: str) -> str`

Runs `await agent.run(message)` and returns `str(result.output)`.

```python
from tc_frameworks.adapters.pydantic_ai_adapter import PydanticAIAdapter

adapter = PydanticAIAdapter(
    model="openai:gpt-4o-mini",            # or "google-gla:gemini-2.5-flash", "anthropic:claude-sonnet-4-20250514"
    system_prompt="You analyze financial data.",
)
```

---

### `SemanticKernelAdapter`

**Module:** `tc_frameworks.adapters.semantic_kernel_adapter`
**Requires:** `pip install semantic-kernel`

Wraps a Microsoft Semantic Kernel with a chat completion service.

```python
SemanticKernelAdapter(
    service_id: str = "chat",
    model: str = "gpt-4o-mini",
    provider: str = "openai",
    plugins: Optional[List] = None,
    api_key: Optional[str] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_id` | `str` | `"chat"` | Semantic Kernel service ID for the chat completion service. |
| `model` | `str` | `"gpt-4o-mini"` | Model name. |
| `provider` | `str` | `"openai"` | `"openai"` for `OpenAIChatCompletion`, `"google"` for `GoogleAIChatCompletion`. |
| `plugins` | `Optional[List]` | `[]` | Kernel plugins to add. |
| `api_key` | `Optional[str]` | `None` | API key override. |

**Tool:** `kernel_invoke(message: str) -> str`

Gets the chat service via `kernel.get_service(service_id)`, creates a `ChatHistory` with the user message, and calls `get_chat_message_contents()` with a `PromptExecutionSettings`. Returns the first result's string representation.

**Note:** `PromptExecutionSettings(service_id=...)` must be passed explicitly when using `GoogleAIChatCompletion`.

```python
from tc_frameworks.adapters.semantic_kernel_adapter import SemanticKernelAdapter

adapter = SemanticKernelAdapter(
    model="gpt-4o-mini",                   # or Gemini model ID with provider="google"
    provider="openai",                      # or "google"
)
```

---

### `AgnoAdapter`

**Module:** `tc_frameworks.adapters.agno_adapter`
**Requires:** `pip install agno`

Wraps an Agno (ex-Phidata) `Agent`.

```python
AgnoAdapter(
    agent_name: str = "assistant",
    model_provider: str = "openai",
    model_id: str = "gpt-4o-mini",
    instructions: str = "You are a helpful assistant.",
    tools: Optional[List] = None,
    api_key: Optional[str] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_name` | `str` | `"assistant"` | Agno agent name. |
| `model_provider` | `str` | `"openai"` | `"openai"` for `OpenAIChat`, `"google"` for `Gemini`. |
| `model_id` | `str` | `"gpt-4o-mini"` | Model ID. |
| `instructions` | `str` | `"You are a helpful assistant."` | Agent instructions (passed as list internally). |
| `tools` | `Optional[List]` | `[]` | Agno tool instances. |
| `api_key` | `Optional[str]` | `None` | API key for the model provider. |

**Tool:** `agno_run(message: str) -> str`

Runs `agent.run(message, stream=False)` synchronously in a thread and returns `response.content`.

```python
from tc_frameworks.adapters.agno_adapter import AgnoAdapter

adapter = AgnoAdapter(
    model_provider="openai",               # or "google"
    model_id="gpt-4o-mini",
    instructions="You write marketing copy.",
)
```

---

### `LlamaIndexAdapter`

**Module:** `tc_frameworks.adapters.llamaindex_adapter`
**Requires:** `pip install llama-index llama-index-llms-openai` (or `llama-index-llms-gemini` for Google)

Wraps a LlamaIndex `ReActAgent`.

```python
LlamaIndexAdapter(
    model: str = "gpt-4o-mini",
    provider: str = "openai",
    tools: Optional[List] = None,
    system_prompt: Optional[str] = None,
    api_key: Optional[str] = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str` | `"gpt-4o-mini"` | Model name (for Google: full model path, e.g. `"models/gemini-2.5-flash"`). |
| `provider` | `str` | `"openai"` | `"openai"` for OpenAI, `"google"` for Gemini. |
| `tools` | `Optional[List]` | `[]` | Functions to wrap as `FunctionTool.from_defaults(fn=...)`. |
| `system_prompt` | `Optional[str]` | `None` | System prompt for the ReAct agent. |
| `api_key` | `Optional[str]` | `None` | API key for the LLM provider. |

**Tool:** `llamaindex_chat(message: str) -> str`

Runs `agent.run(user_msg=message)` and awaits the handler. Note: uses the `ReActAgent` constructor directly — `from_tools()` was removed in v0.14.

```python
from tc_frameworks.adapters.llamaindex_adapter import LlamaIndexAdapter

adapter = LlamaIndexAdapter(
    model="gpt-4o-mini",                   # or "models/gemini-2.5-flash" with provider="google"
    provider="openai",                      # or "google"
)
```

---

### `ElizaOSAdapter`

**Module:** `tc_frameworks.adapters.elizaos_adapter`
**Requires:** ElizaOS running as a service (`npm install -g @elizaos/cli && elizaos start`)

Bridges a running ElizaOS TypeScript instance via its REST API. The adapter is not Python-native (`is_python_native = False`) and does not have built-in MCP support (`has_native_mcp = False`).

```python
ElizaOSAdapter(
    base_url: str = "http://localhost:3000",
    agent_id: Optional[str] = None,
    server_id: str = "trustchain",
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_url` | `str` | `"http://localhost:3000"` | Base URL of the ElizaOS REST API. |
| `agent_id` | `Optional[str]` | `None` | Specific agent ID to target. Currently stored but not sent in requests (all agents respond). |
| `server_id` | `str` | `"trustchain"` | Server ID included in message submissions. |

**Tools:**

**`eliza_send_message(content: str, room_id: str = "default", user_id: str = "trustchain-gateway") -> str`**

POST to `{base_url}/api/messaging/submit` with the message payload. Returns the raw JSON response as a string.

Request body:
```json
{
  "content": "<content>",
  "channel_id": "<room_id>",
  "server_id": "<server_id>",
  "author_id": "<user_id>",
  "source_type": "api",
  "raw_message": {"text": "<content>"}
}
```

**`eliza_list_agents() -> str`**

GET `{base_url}/api/agents`. Returns the raw JSON response as a string.

```python
from tc_frameworks.adapters.elizaos_adapter import ElizaOSAdapter

adapter = ElizaOSAdapter(base_url="http://localhost:3000")
mcp = adapter.create_mcp_server()
```

---

## Adapter Tool Reference

Quick reference for all adapter tools.

| Adapter Class | Tool Name | Input Parameters | Returns |
|---------------|-----------|-----------------|---------|
| `LangGraphAdapter` | `react_agent_invoke` | `message: str` | Final message content |
| `CrewAIAdapter` | `crew_kickoff` | `inputs: dict = {}` | Crew result string |
| `AutoGenAdapter` | `group_chat_run` | `message: str`, `max_turns: int = 3` | Last message content |
| `OpenAIAgentsAdapter` | `agent_run` | `message: str` | `result.final_output` |
| `GoogleADKAdapter` | `adk_invoke` | `message: str` | Joined response parts |
| `ClaudeAgentAdapter` | `claude_query` | `message: str` | `response.content[0].text` |
| `SmolagentsAdapter` | `smolagent_run` | `message: str` | Agent run result |
| `PydanticAIAdapter` | `pydantic_ai_run` | `message: str` | `str(result.output)` |
| `SemanticKernelAdapter` | `kernel_invoke` | `message: str` | First chat result |
| `AgnoAdapter` | `agno_run` | `message: str` | `response.content` |
| `LlamaIndexAdapter` | `llamaindex_chat` | `message: str` | Agent response string |
| `ElizaOSAdapter` | `eliza_send_message` | `content: str`, `room_id: str`, `user_id: str` | Raw JSON string |
| `ElizaOSAdapter` | `eliza_list_agents` | (none) | Raw JSON string |

---

## Complete Examples

### Example 1 — Minimal two-agent trust interaction (v1)

```python
import asyncio
from agent_os import TrustAgent
from agent_os.context import TrustContext

provider = TrustAgent(name="provider", min_trust_threshold=0.2)
caller = TrustAgent(name="caller")

@provider.service("compute", min_trust=0.2)
async def compute(data: dict, ctx: TrustContext) -> dict:
    """Compute service — requires trust 0.2+."""
    return {"result": data.get("x", 0) * 2}

async def main():
    # First call — bootstrap (caller has 0 interactions), allowed
    accepted, reason, result = await caller.call_service(provider, "compute", {"x": 10})
    print(f"Call 1: accepted={accepted}, result={result}")
    # accepted=True, result={"result": 20}

    print(f"Provider trust score: {provider.trust_score:.3f}")
    print(f"Caller interaction count: {caller.interaction_count}")

asyncio.run(main())
```

---

### Example 2 — Trust-gated service with context inspection

```python
from agent_os import TrustAgent
from agent_os.context import TrustContext
from agent_os.decorators import TrustGateError

agent = TrustAgent(name="secure-agent", min_trust_threshold=0.15)

@agent.service("sensitive", min_trust=0.5)
async def sensitive_op(data: dict, ctx: TrustContext) -> dict:
    # Additional fine-grained check inside the handler
    if data.get("privileged") and ctx.caller_trust < 0.8:
        raise TrustGateError(
            f"Privileged operations require trust >= 0.8, "
            f"caller has {ctx.caller_trust:.3f}"
        )
    return {
        "caller": ctx.caller_pubkey[:16] + "...",
        "trust": ctx.caller_trust,
        "bootstrap": ctx.is_bootstrap,
        "result": data,
    }
```

---

### Example 3 — MCP server export

```python
from agent_os import TrustAgent
from agent_os.context import TrustContext

agent = TrustAgent(
    name="mcp-agent",
    identity_path="./agent.key",
    store_path="./records.json",
)

@agent.service("echo", min_trust=0.0)
async def echo(data: dict, ctx: TrustContext) -> dict:
    """Echo the input back."""
    return {"echo": data}

@agent.service("analyze", min_trust=0.3)
async def analyze(data: dict, ctx: TrustContext) -> dict:
    """Analyze input. Requires trust >= 0.3."""
    return {"length": len(str(data.get("text", ""))), "caller_trust": ctx.caller_trust}

mcp = agent.as_mcp_server(name="My Trust Agent")
# mcp.run()  # starts MCP server (stdio or HTTP)
```

---

### Example 4 — Gateway with two upstreams

```python
from gateway.config import GatewayConfig, UpstreamServer
from gateway.server import create_gateway

config = GatewayConfig(
    server_name="Production Gateway",
    identity_path="./gateway.key",
    store_path="./gateway_records.json",
    upstream_identity_dir="./upstream_keys/",
    default_trust_threshold=0.0,
    bootstrap_interactions=3,
    use_v2=False,
    upstreams=[
        UpstreamServer(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            namespace="fs",
            trust_threshold=0.3,
        ),
        UpstreamServer(
            name="web-search",
            url="http://search-agent:8080/mcp",
            namespace="search",
            trust_threshold=0.2,
        ),
    ],
)

mcp = create_gateway(config)
# mcp.run()
```

---

### Example 5 — Framework adapter in a gateway

```python
from tc_frameworks.adapters.langgraph_adapter import LangGraphAdapter
from tc_frameworks.adapters.crewai_adapter import CrewAIAdapter
from gateway.config import GatewayConfig, UpstreamServer
from gateway.server import create_gateway

# Create adapters
lg_adapter = LangGraphAdapter(model_provider="openai", model_name="gpt-4o-mini")
crew_adapter = CrewAIAdapter(
    crew_config={
        "agents": [{"role": "Writer", "goal": "Write content", "backstory": "Creative writer"}],
        "tasks": [{"description": "Write about {topic}", "expected_output": "Article", "agent_role": "Writer"}],
    },
    llm_model="openai/gpt-4o-mini",
)

# Start as standalone MCP servers (for gateway to proxy)
lg_mcp = lg_adapter.create_mcp_server()
crew_mcp = crew_adapter.create_mcp_server()

# Register in gateway via URL upstreams (both must be running as HTTP servers)
config = GatewayConfig(
    upstreams=[
        UpstreamServer(name="langgraph", url="http://localhost:8001/mcp", namespace="lg"),
        UpstreamServer(name="crewai", url="http://localhost:8002/mcp", namespace="crew"),
    ],
)
gateway = create_gateway(config)
```

---

### Example 6 — Remote HTTP service call with sidecar

```python
import asyncio
import os
from agent_os import TrustAgent

agent = TrustAgent(name="client", identity_path="./client.key")

async def main():
    # Option A: transparent proxy (TrustChain sidecar intercepts all traffic)
    os.environ["HTTP_PROXY"] = "http://localhost:8203"
    accepted, reason, result = await agent.call_remote_service(
        "http://remote-agent:8080",
        "compute",
        {"x": 42},
    )

    # Option B: explicit sidecar with trust recording
    accepted, reason, result = await agent.call_remote_service(
        "http://remote-agent:8080",
        "compute",
        {"x": 42},
        peer_pubkey="a3f1b2c4d5e6f7a8...",
        sidecar_url="http://localhost:8202",
    )

    print(f"Accepted: {accepted}")
    print(f"Result: {result}")

asyncio.run(main())
```

---

## trustchain SDK Types

These types from the `trustchain-py` package are used throughout this API.

### `trustchain.identity.Identity`

Ed25519 keypair used for signing and verification.

```python
Identity()                          # Generate new random keypair
Identity.load(path: str)            # Load from .key file
identity.save(path: str)            # Save to .key file (0o600 permissions on Unix)
identity.pubkey_hex: str            # 64-char hex-encoded public key
identity.short_id: str              # Short display prefix
identity.sign(message: bytes) -> bytes  # Sign bytes, returns 64-byte signature
Identity.verify(message, sig, pubkey_bytes) -> bool  # Static verification
```

### `trustchain.store.RecordStore`

In-memory v1 bilateral record store.

```python
RecordStore()
store.add_record(record)                      # Add a signed bilateral record
store.get_records_for(pubkey: str) -> List    # All records involving a pubkey
store.sequence_number_for(pubkey: str) -> int # Next sequence number for pubkey
store.last_hash_for(pubkey: str) -> str       # Hash of latest record for pubkey
```

### `trustchain.store.FileRecordStore`

File-backed record store. Persists to JSON. Same API as `RecordStore`.

```python
FileRecordStore(path: str)   # Creates or loads from JSON file
```

### `trustchain.trust.compute_trust`

Legacy v1 trust computation function.

```python
from trustchain.trust import compute_trust
score: float = compute_trust(pubkey: str, store: RecordStore) -> float
```

Features weighted by: interaction count, unique counterparties, completion rate, account age, entropy. Returns `[0.0, 1.0]`.

### `trustchain.trust.TrustEngine`

v2 trust engine with chain integrity, NetFlow graph scoring, and statistical analysis.

```python
TrustEngine(
    store,                           # BlockStore
    seed_nodes=None,                 # List of trusted seed pubkeys
    weights=None,                    # TrustWeights (integrity, netflow, statistical)
    delegation_store=None,           # DelegationStore for delegated trust
    decay_half_life_ms=None,         # Temporal decay half-life in milliseconds
    checkpoint=None,                 # Checkpoint for verified block ranges
)

engine.compute_trust(pubkey: str, interaction_type: Optional[str] = None) -> float
engine.compute_chain_integrity(pubkey: str) -> float
engine.compute_netflow_score(pubkey: str) -> float
engine.compute_statistical_score(pubkey: str) -> float
```

**Default weights:** `integrity=0.3`, `netflow=0.4`, `statistical=0.3`. If no seed nodes are configured, the netflow weight is redistributed proportionally to the other two components.

**Temporal decay:** When `decay_half_life_ms` is set, older interactions contribute less to trust via the formula `2^(-age_ms / half_life_ms)`.
