# TrustChain

**The missing trust layer for AI agents.**

Every agent protocol (MCP, A2A, ACP, ANP) handles communication. None handle trust. TrustChain is a bilateral signed ledger where every agent interaction produces cryptographic proof — two half-blocks, independently signed, forming an append-only chain per agent. Trust scores emerge from real interaction history, not ratings. Sybil attacks fail because fake identities have no legitimate transaction graph.

Built on the [TrustChain protocol](https://doi.org/10.1016/j.future.2020.01.031) (Otte, de Vos, Pouwelse — TU Delft), extended for AI agent economies.

## Quick Start

### Zero-config (Python + Rust sidecar)

```python
import trustchain
trustchain.init()  # downloads sidecar binary, spawns it, sets HTTP_PROXY
# All outbound HTTP calls are now trust-protected. Done.
```

### One-liner (Rust sidecar directly)

```bash
trustchain-node sidecar --name my-agent --endpoint http://localhost:8080
# Transparent proxy on :8203 — set HTTP_PROXY and forget
```

### Full control (Python SDK)

```python
import asyncio
from agent_os import TrustAgent, TrustContext

buyer = TrustAgent(name="buyer")
seller = TrustAgent(name="seller")

@seller.service("compute", min_trust=0.0)
async def compute(data: dict, ctx: TrustContext) -> dict:
    return {"result": data["x"] ** 2}

async def main():
    for i in range(1, 11):
        ok, reason, result = await buyer.call_service(seller, "compute", {"x": i})
        print(f"Round {i}: {i}² = {result['result']}  "
              f"| buyer={buyer.trust_score:.3f}  seller={seller.trust_score:.3f}")

asyncio.run(main())
```

## How It Works

TrustChain runs as a **sidecar** next to each agent — a transparent HTTP proxy that intercepts all agent-to-agent calls. Agents don't call TrustChain directly; they set `HTTP_PROXY` once and interact normally. Every call produces a bilateral cryptographic record. Trust accumulates automatically.

```
  Agent A                    Agent B
    │                          │
    │  HTTP call               │
    ▼                          ▼
┌────────┐   QUIC P2P    ┌────────┐
│Sidecar │◄──────────────►│Sidecar │
│ :8203  │  proposal/     │ :8203  │
│        │  agreement     │        │
└────────┘                └────────┘
    │                          │
    ▼                          ▼
  SQLite                    SQLite
  (chain)                   (chain)
```

Trust is **decoupled from discovery** — any discovery source (registry, A2A, MCP, DNS, P2P gossip) returns `(endpoint, pubkey)`, and the trust layer handles the rest. Registries can't fake trust.

## Why TrustChain

| Problem | Current State | TrustChain |
|---------|--------------|------------|
| Agent A calls Agent B | Blind trust or API keys | Bilateral signed proof of every interaction |
| Sybil attacks | Star ratings, trivially faked | Max-flow graph analysis (NetFlow) — fake nodes can't create real transaction paths |
| "Who do I trust?" | Centralized registries | Decentralized — each agent computes trust from its own chain view |
| Accountability | Logs (mutable, unilateral) | Append-only chains with hash links — tampering is detectable |
| Cold start | No data, no trust | Bootstrap interactions, then earn trust through real history |

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Your Agent (any framework: LangGraph, CrewAI, AutoGen, A2A...) │
│  Just set HTTP_PROXY=http://127.0.0.1:8203                      │
├──────────────────────────────────────────────────────────────────┤
│  Transparent Proxy (:8203)                                       │
│  Intercepts HTTP → resolves peer → bilateral handshake → forward │
├──────────────────────────────────────────────────────────────────┤
│  HTTP REST API (:8202)          │  gRPC API (:50051)             │
│  /propose /peers /trust /chain  │  Protobuf-native agent API     │
├──────────────────────────────────────────────────────────────────┤
│  QUIC P2P Transport (:8200)                                      │
│  TLS 1.3 mutual auth · rate limiting · connection reuse          │
│  Proposal/agreement · fraud proofs · CHECO checkpoints · gossip  │
├──────────────────────────────────────────────────────────────────┤
│  TrustEngine: NetFlow (Sybil resistance) + chain integrity +     │
│  statistical scoring · fraud penalty (hard zero for double-spend) │
├──────────────────────────────────────────────────────────────────┤
│  Protocol: proposal/agreement half-blocks · Ed25519 signatures   │
│  Tiered validation · double-sign/double-countersign detection    │
├──────────────────────────────────────────────────────────────────┤
│  SQLite (prod, WAL mode) / Memory (test) · peer persistence      │
└──────────────────────────────────────────────────────────────────┘
```

## Trust Scoring

Three-component weighted trust via `TrustEngine`:

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| **Chain Integrity** | 30% | Hash links valid, no gaps, all signatures verify |
| **NetFlow Score** | 40% | Max-flow from seed nodes through transaction graph — Sybil resistance |
| **Statistical Score** | 30% | Interaction volume, completion rate, counterparty diversity, account age, entropy |

Agents with proven double-spend fraud receive **hard zero** trust — no recovery.

## Protocol

Based on [IETF draft-pouwelse-trustchain](https://datatracker.ietf.org/doc/draft-pouwelse-trustchain/):

1. **Half-block model** — Each agent creates and signs their own block. No shared state.
2. **Proposal/Agreement flow** — A proposes `(seq, link_to_B, transaction, signature)`. B validates, creates agreement linking back. Both store both blocks.
3. **Hash-linked chains** — Every block references the previous block's hash. Gaps and forks are detectable.
4. **Ed25519 signatures** — Every block is signed by its creator only. Non-repudiable.

```
Alice's chain:        Bob's chain:
┌──────────┐          ┌──────────┐
│ PROPOSAL │─────────→│ AGREEMENT│
│ seq=1    │←─────────│ seq=1    │
│ sig=Alice│          │ sig=Bob  │
└──────────┘          └──────────┘
     ↑                      ↑
  prev_hash              prev_hash
     ↑                      ↑
┌──────────┐          ┌──────┐
│ PROPOSAL │─────────→│ AGREE│
│ seq=2    │←─────────│ seq=2│
└──────────┘          └──────┘
```

## Project Structure

```
trustchain-rs/                  # Rust production node (4 crates, 166 tests)
  trustchain-core/              #   Identity, HalfBlock, BlockStore, Protocol,
                                #   TrustEngine, NetFlow, Consensus, Crawler
  trustchain-transport/         #   QUIC, gRPC, HTTP, proxy, discovery, STUN
  trustchain-node/              #   CLI binary: run / sidecar / keygen / status
  trustchain-wasm/              #   WASM bindings (browser/edge)
  Dockerfile                    #   Multi-stage container build
  deploy/                       #   systemd service, Docker config
  .github/workflows/            #   CI/CD (build, test, release)

trustchain/                     # Python protocol bindings
  sidecar.py                    #   Zero-config sidecar SDK (trustchain.init())
agent_os/                       # Agent SDK (TrustAgent, decorators)
gateway/                        # MCP Gateway with trust middleware
frameworks/                     # Framework adapters (LangGraph, CrewAI, AutoGen...)
examples/                       # Usage examples
tests/                          # Python test suite
```

## Install

### Rust node (recommended for production)

```bash
# From source
cd trustchain-rs && cargo build --release
# Binary at target/release/trustchain-node

# Or via Docker
docker build -t trustchain trustchain-rs/
docker run -v trustchain-data:/data trustchain
```

### Python SDK

```bash
pip install trustchain-agent-os

# Or from source
git clone https://github.com/viftode4/trustchain-agent-os.git
cd trustchain-agent-os
pip install -e ".[dev]"
```

### Run tests

```bash
# Rust (166 tests)
cd trustchain-rs && cargo test --workspace

# Python
python -m pytest tests/ -v
```

## Deployment

### Sidecar mode (one agent)

```bash
trustchain-node sidecar \
  --name my-agent \
  --endpoint http://localhost:8080 \
  --advertise http://203.0.113.5:8202 \
  --bootstrap http://seed1.example.com:8202
```

### Full node

```bash
trustchain-node run --config node.toml
```

### Docker

```bash
docker run -d \
  -p 8200:8200 -p 8202:8202 -p 50051:50051 \
  -v trustchain-data:/data \
  trustchain
```

### systemd

```bash
sudo cp deploy/trustchain.service /etc/systemd/system/
sudo systemctl enable --now trustchain
```

## Features

- **Transparent proxy** — agents set `HTTP_PROXY` once, trust is invisible
- **P2P capability discovery** — find agents by proven interaction history, not self-reported claims
- **QUIC transport** — TLS 1.3 mutual auth, rate limiting (per-IP), connection reuse
- **CHECO consensus** — periodic checkpoint blocks for finality, facilitator rotation
- **Fraud detection** — tiered validation, double-sign/double-countersign detection, fraud propagation with TTL relay
- **STUN NAT traversal** — automatic public address discovery
- **Peer persistence** — SQLite WAL mode, peers survive restarts
- **Graceful shutdown** — clean ctrl-c handling

## Research Foundation

Based on the TrustChain protocol from the [TU Delft Blockchain Lab](https://www.tudelft.nl/ewi/over-de-faculteit/afdelingen/software-technology/distributed-systems/people/johan-pouwelse) (Distributed Systems Group).

**Core paper**: Otte, de Vos, Pouwelse — [TrustChain: A Sybil-resistant scalable blockchain](https://doi.org/10.1016/j.future.2020.01.031) (Future Generation Computer Systems, 2020)

Key contributions realized in this implementation:
- **Half-block architecture** (Section 3.1) — each party signs only their own block
- **NetFlow-based Sybil resistance** (Section 4) — trust via max-flow from seed nodes
- **Scalability through bilateral accountability** — linear scaling, no miners, no gas fees

**Extension for AI agents**: transparent sidecar model, trust-gated services, MCP gateway integration, framework adapters, QUIC P2P + gRPC + HTTP transport stack.

## References

- Otte, de Vos, Pouwelse — [TrustChain: A Sybil-resistant scalable blockchain](https://doi.org/10.1016/j.future.2020.01.031) (Future Generation Computer Systems, 2020)
- [IETF draft-pouwelse-trustchain-01](https://datatracker.ietf.org/doc/draft-pouwelse-trustchain/) — Protocol specification
- [py-ipv8](https://github.com/Tribler/py-ipv8) — TU Delft reference implementation (Python)
- [kotlin-ipv8](https://github.com/Tribler/kotlin-ipv8) — Mobile implementation (Kotlin/Android)

## License

MIT
