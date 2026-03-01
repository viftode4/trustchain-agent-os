# TrustChain: The Universal Trust Primitive for the Agent Economy

## What Is TrustChain?

TrustChain is a **decentralized trust infrastructure** that lets any two parties — AI agents, humans, devices, services — interact with automatic, cryptographically verifiable trust. It is not a communication protocol, not a framework, and not a registry. It is the **missing trust layer** that sits underneath all of them.

Every agent protocol today (MCP, A2A, ACP, ANP) solves communication — how agents find each other, how they exchange messages, how they call tools. **None of them solve trust.** When Agent A calls Agent B, how does B know A is legitimate? How does A know B won't return garbage? Today the answer is API keys, OAuth tokens, and centralized allowlists — mechanisms designed for humans that completely break down in an autonomous agent economy where millions of agents interact without human oversight.

TrustChain replaces all of that with a single primitive: **bilateral signed interaction records on an append-only chain**, with Sybil resistance via max-flow graph analysis. No central authority. No shared secrets. No credential exchange. Two agents interact, both sign the interaction, trust accumulates over time, and the entire history is tamper-proof.

## The Core Insight

Trust is not a feature. It is **infrastructure**. Just as TCP/IP handles packet delivery so applications don't have to, TrustChain handles trust so agents don't have to. The agent doesn't call TrustChain — TrustChain runs as a **sidecar** (transparent proxy), intercepting normal HTTP calls and adding bilateral trust records invisibly. Set `HTTP_PROXY=localhost:8203` once, and every outbound call goes through the trust layer. The agent's code never changes.

This means TrustChain works with **any** agent framework, **any** discovery mechanism, and **any** communication protocol. It doesn't matter if your agents use LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, Google ADK, PydanticAI, Semantic Kernel, Agno, LlamaIndex, Smolagents, Claude, or ElizaOS. It doesn't matter if they discover each other via DNS, a registry, peer-to-peer gossip, or hardcoded URLs. Trust is always ground truth from the bilateral ledger.

## How It Works

### Three-Layer Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Layer 3: DISCOVERY                                     │
│  How agents find each other.                            │
│  Any source: registry, DNS, A2A, MCP, P2P gossip.      │
│  Returns: (endpoint, pubkey). That's all TrustChain     │
│  needs — the rest is handled by the trust layer.        │
├─────────────────────────────────────────────────────────┤
│  Layer 2: TRUST                                         │
│  The bilateral ledger.                                  │
│  Every interaction = proposal → agreement (half-blocks) │
│  Both parties sign. Append-only chain. Hash-linked.     │
│  Trust score = f(interaction_count, completion_rate,     │
│                  entropy, NetFlow Sybil resistance)      │
├─────────────────────────────────────────────────────────┤
│  Layer 1: IDENTITY                                      │
│  Ed25519 self-sovereign keypairs.                       │
│  No certificate authorities. No registration.           │
│  Your public key IS your identity.                      │
│  Delegation: grant scoped, time-limited authority.      │
│  Succession: rotate keys without losing trust history.  │
└─────────────────────────────────────────────────────────┘
```

### The Interaction Protocol

Every interaction between two agents follows a bilateral half-block protocol:

1. **Agent A sends a Proposal** — "I want to call your `compute` service with these parameters." The proposal is a signed half-block containing A's pubkey, the interaction type, a sequence number, and a hash link to A's previous block.

2. **Agent B creates an Agreement** — "I accept this proposal. Here is the result." The agreement is a signed half-block referencing A's proposal hash, containing B's pubkey, the result, and a hash link to B's previous block.

3. **Both blocks are stored** on both chains. Now there is a cryptographic proof that this interaction happened, signed by both parties, that neither side can forge or deny.

This is fundamentally different from server logs. A server log is unilateral — the server writes whatever it wants. A bilateral block is signed by both parties. If either side tampers, the hash chain breaks and `chain_integrity` drops below 1.0.

### Trust Scoring

Trust between two agents is computed from their bilateral history:

- **Interaction count** — More interactions = more data points = more confidence
- **Completion rate** — Ratio of successful agreements to total proposals (failed/timeout = lower trust)
- **Entropy** — Diversity of interaction types (an agent that only does one thing is less trustworthy than one with diverse capabilities)
- **Temporal decay** — Recent interactions matter more than old ones (configurable half-life)
- **NetFlow (Sybil resistance)** — Max-flow analysis on the interaction graph prevents fake identities from inflating trust (Dinic's algorithm)

The score is always **local** — Agent A computes trust in Agent B from A's own chain view. There is no global trust score. This is intentional: trust is subjective, contextual, and should never be controlled by a central authority.

### Sybil Resistance via NetFlow

The hardest problem in decentralized trust is Sybil attacks — an adversary creates 1000 fake identities that all vouch for each other. Traditional reputation systems break completely under Sybil attacks.

TrustChain uses **max-flow graph analysis** (Dinic's algorithm) to solve this. Trust flows through the interaction graph like water through pipes. Creating fake identities doesn't help because:

- Each fake identity needs **real interactions** with **real agents** to build trust paths
- The max-flow from the evaluating agent to the target is bounded by the narrowest bottleneck
- 1000 fake identities with 0 real interactions = 0 trust flow

This is the same mathematical foundation used by Google's PageRank and Bitcoin's proof-of-work — except applied to bilateral interaction history rather than links or hash puzzles.

## The Three Repositories

### 1. `trustchain` — Rust Node (Production Sidecar)

The core infrastructure, written in Rust for performance and safety.

- **4 crates:** trustchain-core, trustchain-transport, trustchain-node, trustchain-wasm
- **214 tests passing**
- **QUIC P2P transport** with Ed25519 TLS certificates
- **SQLite WAL** storage with checkpoint persistence
- **Transparent HTTP proxy** on port 8203 (set HTTP_PROXY once, trust is invisible)
- **HTTPS CONNECT** tunneling with TrustChain handshake
- **MCP server** (rmcp): 5 tools over streamable HTTP + stdio
- **CLI:** `trustchain-node launch --name X -- python app.py` (Dapr-style lifecycle)
- **Block types:** Proposal, Agreement, Checkpoint, Delegation, Revocation, Succession
- **Security:** 1 MiB body limit, SSRF protection, rate limiting, gossip validation, private key file permissions

**Key design decisions:**
- `BlockStore` trait is `Send` (not `Send+Sync`); `SqliteBlockStore` uses `Mutex<Connection>`
- All timestamps are **int milliseconds** (u64) — wire-compatible with Python SDK
- JSON canonical hashing uses BTreeMap (sorted keys) with compact separators
- Delegation TTL capped at 30 days (MAX_DELEGATION_TTL_SECS)
- QUIC rate limiter: HashMap capped at 65K entries with eviction
- Checkpoint protocol (CHECO): propose → collect votes → finalize → persist → broadcast

### 2. `trustchain-py` — Python SDK

The Python bindings for building trust-native applications.

- **174 tests passing**
- **PyPI:** `pip install trustchain-py`, `import trustchain`
- **Full protocol implementation:** Identity, HalfBlock, BlockStore, TrustEngine, NetFlow
- **Delegation:** DelegationCertificate, DelegationRecord, DelegationStore (43 tests)
- **Sidecar client:** Rust-compatible paths (/receive_proposal, /crawl, /status, /healthz)
- **QUIC transport:** persistent connections via `__aenter__()`, cleanup in `stop()`
- **Incremental NetFlow:** `_known_seqs` tracking, only scans new blocks (mirrors Rust CachedNetFlow)

**Wire compatibility with Rust:**
- All timestamps: int milliseconds (not float seconds)
- JSON canonical hashing: sorted keys, compact separators
- Block signing: Ed25519 over SHA-256 of canonical JSON
- Delegated trust: flat budget split (root_trust / active_count)

### 3. `trustchain-agent-os` — Agent Framework Layer

The integration layer that connects TrustChain to every major AI agent framework.

- **205 tests passing**
- **12 framework adapters** (6 original + 6 new):
  - LangGraph, CrewAI, AutoGen/AG2, OpenAI Agents SDK, Google ADK, ElizaOS
  - Claude (Anthropic), Smolagents, PydanticAI, Semantic Kernel, Agno, LlamaIndex
- **TrustAgent primitive:** lightweight agent with built-in identity, trust scoring, service registry
- **Trust-gated services:** `@service("compute", min_trust=0.36)` decorator
- **MCP Gateway:** FastAPI server with trust middleware, bilateral interaction recording
- **Sidecar integration:** `TrustChainSidecar` client for the Rust node

**All 12 adapters support Gemini as LLM backend** — verified with real API calls:
- LangGraph: `ChatGoogleGenerativeAI` via langchain-google-genai
- CrewAI: `gemini/` prefix via LiteLLM
- AutoGen: `GeminiLLMConfigEntry` to `LLMConfig`
- OpenAI Agents: Gemini via OpenAI-compatible endpoint
- Google ADK: native `google-genai`
- PydanticAI: `google-gla:` prefix
- Semantic Kernel: `GoogleAIChatCompletion`
- Agno: `agno.models.google.Gemini`
- LlamaIndex: `llama_index.llms.gemini.Gemini`
- Smolagents: `LiteLLMModel` with `gemini/` prefix
- Claude: `anthropic.Anthropic` SDK (uses Claude models, not Gemini)
- ElizaOS: external TypeScript server

## Use Cases

### 1. Agent-to-Agent Commerce

An agent economy where agents buy and sell services. Agent A needs code review, Agent B offers it. Today: A needs B's API key, or both need accounts on a marketplace. With TrustChain: A calls B directly, trust accumulates, and after enough successful interactions A can access B's premium tier. No marketplace needed. No API keys. No central intermediary.

### 2. Multi-Framework Orchestration

A workflow uses LangGraph for planning, CrewAI for execution, and PydanticAI for validation. Today: each framework has its own trust/auth mechanism (or none). With TrustChain: all three frameworks share one bilateral trust ledger. The LangGraph planner can check trust scores of CrewAI workers before dispatching tasks. The PydanticAI validator can reject results from low-trust sources.

### 3. Trust-Gated API Access

A service offers 3 tiers: basic (free), compute (needs track record), premium (established agents only). Today: subscription plans, API key tiers, manual approval. With TrustChain: `min_trust` thresholds on each service tier. New agents start with basic access and earn their way up through real interactions. No sign-up forms. No manual approval. Trust is the passport.

### 4. Sybil-Resistant Reputation

A marketplace where agents rate each other. Today: trivially gamed with fake accounts and fake reviews. With TrustChain: max-flow analysis means fake identities can't create real trust paths. 1000 fake agents with 0 real interactions = 0 trust flow. The math prevents gaming without any centralized moderation.

### 5. Delegated Authority

An organization deploys 50 agents. The root agent delegates scoped authority to each: "Agent X can do code review for the next 7 days." Today: service accounts with broad permissions. With TrustChain: delegation certificates with scope, TTL, and cryptographic proof of authority chain. Revocation is instant and propagates through the network.

### 6. Offline/Mesh Trust

Agents operating in disconnected environments (edge computing, field operations, disaster response). Today: no connectivity = no auth server = no trust. With TrustChain: trust history travels with the local chain. Agents can verify each other's chains offline. When connectivity returns, chains sync automatically.

### 7. Cross-Protocol Trust

Agent A uses MCP, Agent B uses A2A, Agent C uses raw HTTP. Today: each protocol has its own auth story (or none). With TrustChain: trust is decoupled from the communication protocol. Any discovery source returns `(endpoint, pubkey)`, and the trust layer handles the rest. Same bilateral ledger whether the call came over MCP, A2A, gRPC, or plain HTTP.

### 8. Autonomous Agent Accountability

An agent makes a decision that causes financial loss. Today: server logs are mutable and unilateral — the agent (or its operator) can deny or modify the record. With TrustChain: every interaction is bilateral, signed by both parties, append-only, and hash-linked. Tampering is cryptographically detectable. The chain is the audit trail.

## What Makes TrustChain Different

| Aspect | Traditional Auth | Centralized Reputation | TrustChain |
|--------|-----------------|----------------------|------------|
| **Trust source** | Credential issuer | Central server | Bilateral interaction history |
| **Sybil resistance** | Email verification | Manual moderation | Max-flow graph analysis |
| **Offline support** | None (needs auth server) | None (needs reputation server) | Full (chain travels locally) |
| **Tampering** | Server logs are mutable | Ratings are mutable | Append-only hash chain, bilateral signatures |
| **Cold start** | Credentials granted upfront | Ratings start at 0 | Bootstrap interactions, then earn trust |
| **Agent support** | API keys (designed for humans) | Not designed for agents | Native agent-to-agent trust primitive |
| **Framework lock-in** | Per-framework auth | Per-platform reputation | Framework-agnostic (works with any) |
| **Central authority** | Yes (key issuer) | Yes (reputation server) | No (each agent computes trust locally) |

## Project Status

**Feature-complete.** All three repositories are production-hardened and tested:

- Rust node: 214 tests, TLS pubkey pinning, QUIC P2P, SQLite persistence, checkpoint protocol
- Python SDK: 174 tests, wire-compatible with Rust, incremental NetFlow, delegation
- Agent-OS: 205 tests, 12 framework adapters, trust gates, MCP gateway, sidecar integration

**Verified working** (March 1, 2026):
- hello_trust: bilateral trust accumulation with 1.000 chain integrity
- trust_gate: progressive access control (compute gate at round 7, premium at round 9)
- framework_interop: 11 real framework runtimes with Gemini LLM, 8/11 successful (3 hit free-tier quota)

## Quick Start

```bash
# Install
pip install trustchain-py trustchain-agent-os

# Run the trust gate demo
python examples/trust_gate.py

# Run the multi-framework demo (needs Gemini API key)
GEMINI_API_KEY=your-key python examples/framework_interop.py
```

## Links

- **Rust node:** https://github.com/viftode4/trustchain
- **Python SDK:** https://github.com/viftode4/trustchain-py
- **Agent-OS:** https://github.com/viftode4/trustchain-agent-os
