# TrustChain Agent OS

**The missing trust layer for AI agents.**

Every agent protocol (MCP, A2A, ACP) handles communication. None handle trust. TrustChain is a bilateral signed ledger where every agent interaction produces cryptographic proof — two half-blocks, independently signed, forming an append-only chain per agent. Trust scores emerge from real interaction history, not ratings. Sybil attacks fail because fake identities have no legitimate transaction graph.

Built on the [TrustChain protocol](https://doi.org/10.1016/j.future.2020.01.031) (Otte, de Vos, Pouwelse — TU Delft), extended for AI agent economies.

```
pip install trustchain-agent-os
```

## 30-Second Demo

```python
import asyncio
from trustchain import Identity, MemoryBlockStore, TrustChainProtocol

async def main():
    # Two agents, each with their own identity and chain
    alice_id, bob_id = Identity(), Identity()
    store = MemoryBlockStore()
    alice = TrustChainProtocol(alice_id, store)
    bob = TrustChainProtocol(bob_id, store)

    # Alice proposes a transaction to Bob
    proposal = alice.create_proposal(
        bob_id.pubkey_hex,
        {"service": "compute", "outcome": "completed"}
    )

    # Bob validates and agrees — creates his own signed half-block
    bob.receive_proposal(proposal)
    agreement = bob.create_agreement(proposal)

    # Alice receives the agreement — both chains grow
    alice.receive_agreement(agreement)

    print(f"Alice chain length: {store.get_latest_seq(alice_id.pubkey_hex)}")
    print(f"Bob chain length: {store.get_latest_seq(bob_id.pubkey_hex)}")
    # Both: 1. Trust builds with every bilateral interaction.

asyncio.run(main())
```

## Why TrustChain

| Problem | Current State | TrustChain |
|---------|--------------|------------|
| Agent A calls Agent B's API | Blind trust or API keys | Bilateral signed proof of every interaction |
| Sybil attacks on reputation | Star ratings, trivially faked | Max-flow graph analysis (NetFlow) — fake nodes have no real transaction paths |
| "Who do I trust?" | Centralized registries | Decentralized — each agent computes trust from its own chain view |
| Accountability | Logs (mutable, unilateral) | Append-only chains with hash links — tampering is detectable |
| Cold start | No data, no trust | Bootstrap mode: new agents get 3 free interactions, then earn trust |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent / MCP Gateway                       │
│  TrustAgent SDK  ←→  MCP Gateway (trust-gated tool calls)   │
├─────────────────────────────────────────────────────────────┤
│  gRPC Agent API (50051)  │  HTTP/3 REST API (8100)          │
├─────────────────────────────────────────────────────────────┤
│              QUIC P2P Transport (8200)                        │
│  TLS 1.3 mutual auth · stream multiplexing · NAT traversal  │
├─────────────────────────────────────────────────────────────┤
│  TrustEngine: NetFlow (Sybil) + chain integrity + stats      │
├─────────────────────────────────────────────────────────────┤
│  Protocol: proposal/agreement half-blocks · Ed25519 sigs     │
├─────────────────────────────────────────────────────────────┤
│  BlockStore: SQLite (prod) / Memory (test)                   │
└─────────────────────────────────────────────────────────────┘
```

Three transport layers, one protocol:
- **QUIC** — P2P between nodes (chain sync, proposals, gossip). What IPv8's UDP would be if redesigned today.
- **gRPC** — Agent-to-node RPC. Protobuf-native, streaming, high throughput.
- **HTTP/3** — REST API for external clients. Same FastAPI app, served via Hypercorn.

## Agent SDK

Build trust-native agents in 10 lines:

```python
from agent_os import TrustAgent, TrustContext

agent = TrustAgent(name="compute-agent")

@agent.service("compute", min_trust=0.3)
async def run_compute(data: dict, ctx: TrustContext) -> dict:
    """Only agents with trust >= 0.3 can call this."""
    return {"result": data["x"] ** 2}

# Agent-to-agent with automatic trust recording
alice = TrustAgent(name="alice")
accepted, reason, result = await alice.call_service(agent, "compute", {"x": 7})
# accepted=True, result={"result": 49}
# Both agents' chains grow. Trust accumulates.
```

## MCP Gateway

Drop-in proxy that adds trust verification to any MCP server:

```python
from gateway.config import GatewayConfig, UpstreamServer
from gateway.server import create_gateway

config = GatewayConfig(
    upstreams=[
        UpstreamServer(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            trust_threshold=0.3,
        ),
    ],
)

gateway = create_gateway(config)
gateway.run()  # Claude Code connects via stdio
```

LLMs get native trust tools: `trustchain_check_trust`, `trustchain_verify_chain`, `trustchain_crawl`.

## Protocol

Based on [IETF draft-pouwelse-trustchain](https://datatracker.ietf.org/doc/draft-pouwelse-trustchain/):

1. **Half-block model** — Each agent creates and signs their own block. No shared state.
2. **Proposal/Agreement flow** — A proposes (seq_number, link_to_B, transaction, signature). B validates, creates agreement linking back. Both store both blocks.
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
┌──────────┐          ┌──────────┐
│ PROPOSAL │─────────→│ AGREEMENT│
│ seq=2    │←─────────│ seq=2    │
└──────────┘          └──────────┘
```

## Trust Scoring

Three-component weighted trust (via `TrustEngine`):

| Component | Weight | What it measures |
|-----------|--------|-----------------|
| **Chain Integrity** | 30% | Hash links valid, no gaps, all signatures check out |
| **NetFlow Score** | 40% | Max-flow from seed nodes through transaction graph — Sybil resistance |
| **Statistical Score** | 30% | Interaction volume, completion rate, counterparty diversity |

## CHECO Consensus

Periodic checkpoint blocks for finality (optional):
- Deterministic facilitator selection from chain state
- Facilitator proposes checkpoint referencing all known chain heads
- Nodes validate and co-sign
- Checkpoint finalizes all blocks before it

## Project Structure

```
trustchain/              # Core protocol
  halfblock.py           # HalfBlock data model, Ed25519 signing
  protocol.py            # Proposal/agreement two-phase flow
  blockstore.py          # MemoryBlockStore / SQLiteBlockStore
  trust.py               # TrustEngine (NetFlow + integrity + stats)
  netflow.py             # Max-flow Sybil resistance
  consensus.py           # CHECO checkpoint consensus
  api.py                 # HTTP REST API + TrustChainNode
  transport/             # Transport abstraction layer
    base.py              # Transport ABC, MessageType
    http.py              # HTTP transport
    quic.py              # QUIC P2P transport (aioquic)
    pool.py              # Connection pool
    tls.py               # Self-signed TLS from Ed25519 identity
    discovery.py         # Peer discovery (bootstrap + walk + gossip)
  proto/                 # Protobuf wire protocol
    trustchain.proto     # Schema definition
    serialization.py     # Binary serialization (no protoc needed)
  grpc/                  # gRPC Agent API
    service.py           # TrustChainServicer
    client.py            # Async gRPC client
    server.py            # gRPC server runner
gateway/                 # MCP Gateway with trust middleware
agent_os/                # Agent SDK (TrustAgent, decorators)
frameworks/              # Framework adapters (LangGraph, CrewAI, AutoGen, etc.)
tests/                   # 335 tests
```

## Install

```bash
pip install trustchain-agent-os

# Or from source
git clone https://github.com/viftode4/trustchain-agent-os.git
cd trustchain-agent-os
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v  # 335 tests, ~40s
```

## Research Foundation

This implementation is based on the TrustChain protocol developed at the [TU Delft Blockchain Lab](https://www.tudelft.nl/ewi/over-de-faculteit/afdelingen/software-technology/distributed-systems/people/johan-pouwelse) (Distributed Systems Group, Software Technology Department).

**Core paper**: Otte, de Vos, Pouwelse — [TrustChain: A Sybil-resistant scalable blockchain](https://doi.org/10.1016/j.future.2020.01.031) (Future Generation Computer Systems, 2020)

Key contributions from the paper that this implementation realizes:
- **Half-block architecture** (Section 3.1) — Each party creates and signs only their own block. No global consensus needed for bilateral interactions.
- **NetFlow-based Sybil resistance** (Section 4) — Trust is computed as max-flow from seed nodes through the transaction graph. Sybil identities with no legitimate transaction paths receive zero trust, regardless of how many fake interactions they create among themselves.
- **Scalability through bilateral accountability** — Unlike global blockchains (Bitcoin, Ethereum), TrustChain scales linearly: each transaction only involves two parties. No miners, no gas fees, no block limits.

**Extension for AI agents**: This implementation adds trust-gated service calls, MCP gateway integration, framework adapters (LangGraph, CrewAI, AutoGen, ElizaOS, Google ADK, OpenAI Agents), and a dual-stack transport architecture (QUIC P2P + gRPC + HTTP/3) — making TrustChain the trust substrate for the emerging AI agent economy.

## References

- Otte, de Vos, Pouwelse — [TrustChain: A Sybil-resistant scalable blockchain](https://doi.org/10.1016/j.future.2020.01.031) (Future Generation Computer Systems, 2020)
- [IETF draft-pouwelse-trustchain-01](https://datatracker.ietf.org/doc/draft-pouwelse-trustchain/) — Protocol specification
- [py-ipv8](https://github.com/Tribler/py-ipv8) — TU Delft reference implementation (Python)
- [kotlin-ipv8](https://github.com/Tribler/kotlin-ipv8) — Mobile implementation (Kotlin/Android)
- Kempen, Pouwelse — [Offline Digital Euro: A CBDC Using Groth-Sahai Proofs](https://repository.tudelft.nl/) — Settlement layer research

## License

MIT
