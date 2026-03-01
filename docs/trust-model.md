# Trust Model & Tuning Guide

## Overview

TrustChain computes trust from three components, combined with configurable weights:

| Component | Default Weight | What It Measures |
|-----------|---------------|------------------|
| Chain Integrity | 0.3 | Has the chain been tampered with? |
| NetFlow (Sybil Resistance) | 0.4 | Can trust flow from seed nodes to this agent? |
| Statistical Score | 0.3 | How many interactions, how successful, how diverse? |

**Final score = integrity × 0.3 + netflow × 0.4 + statistical × 0.3**

If NetFlow is not configured (no seed nodes), its weight is redistributed proportionally:
- integrity → 0.5, statistical → 0.5

## Component 1: Chain Integrity (weight: 0.3)

Every agent maintains an append-only chain of blocks. Each block contains:
- Sequence number (monotonically increasing)
- Hash of the previous block (hash link)
- Ed25519 signature

Chain integrity checks:
- Are sequence numbers sequential with no gaps?
- Does each block's prev_hash match the actual hash of the previous block?
- Are all signatures valid?

**Score:**
- 1.0 = chain is fully intact
- 0.0 = chain is broken (tampered, forged, or corrupted)

**Impact:** A single broken link drops the entire integrity score. Since integrity has weight 0.3, a tampered chain immediately loses 30% of its possible trust score, and the other components are also affected because they rely on chain data.

## Component 2: NetFlow / Sybil Resistance (weight: 0.4)

The most important and novel component. Uses **Dinic's max-flow algorithm** on the interaction graph.

### How it works

1. **Build a directed graph** from all known bilateral interactions
   - Each agent is a node
   - Each bilateral interaction creates edges between the two agents
   - Edge weight = number of successful interactions between the pair

2. **Add a super-source** connected to all seed nodes
   - Seed nodes are your own pubkey + any explicitly trusted peers
   - Super-source edges have capacity = sum of all outgoing edges from each seed

3. **Compute max-flow** from super-source to the target agent
   - Uses Dinic's algorithm (O(V²E) worst case, fast in practice)
   - The flow represents how much "trust" can reach the target through real interaction paths

4. **Normalize** by dividing by max_possible (sum of super-source edge capacities)
   - Result is [0.0, 1.0]

### Why Sybil attacks fail

An attacker creates 1000 fake identities that vouch for each other:
```
Attacker → Fake1 → Fake2 → ... → Fake1000
                ↕ (lots of fake interactions)
```

But the max-flow from your seed nodes to any fake identity is bounded by the **narrowest bottleneck** between you and the attacker. If the attacker has only 1 real interaction with a legitimate agent, the max-flow through the entire fake cluster is capped at 1 — regardless of how many fake interactions exist within the cluster.

This is mathematically equivalent to: "Fake identities can't create real trust paths."

### Incremental updates (CachedNetFlow)

For performance, the graph is updated incrementally:
- `_known_seqs` tracks the latest sequence number processed for each pubkey
- Only new blocks are scanned on each trust computation
- Full rebuild only when the graph structure changes significantly

## Component 3: Statistical Score (weight: 0.3)

Computed from the agent's interaction history:

### Features

| Feature | Formula | Range | What It Means |
|---------|---------|-------|---------------|
| interaction_count | min(count / 20, 1.0) | [0, 1] | More interactions = more confidence. Saturates at 20. |
| completion_rate | completed / total | [0, 1] | Fraction of interactions with outcome="completed" |
| counterparty_diversity | unique_partners / total | [0, 1] | Higher = interacts with more different agents |
| entropy | Shannon entropy / log2(unique) | [0, 1] | Distribution uniformity across partners |

### Combination
The statistical features are combined (typically averaged or weighted) to produce a single statistical score in [0.0, 1.0].

## Temporal Decay

Optional: interactions lose weight over time.

**Formula:** `weight = 2^(-age_ms / half_life_ms)`

| Age | Half-life = 7 days | Half-life = 30 days |
|-----|-------------------|---------------------|
| 0 (now) | 1.000 | 1.000 |
| 1 day | 0.906 | 0.977 |
| 7 days | 0.500 | 0.851 |
| 30 days | 0.046 | 0.500 |
| 90 days | 0.0001 | 0.125 |

**When to use:**
- Enable if you want agents to maintain ongoing activity to keep trust high
- Disable (default) if historical trust should persist indefinitely
- Configure via `decay_half_life_ms` on TrustEngine

## Delegation Trust

When an agent is a delegate (authorized by a root identity via delegation certificate):

**Delegated trust = root_trust / active_delegate_count**

- root_trust: the trust score of the delegating (root) identity
- active_delegate_count: number of currently active (non-revoked) delegates
- This is a "flat budget split" — the root's trust is divided equally among all delegates

If a delegation is revoked, the delegate immediately gets trust score 0.0.

Delegation scope can restrict which interaction types a delegate can perform. `create_proposal` enforces this — a delegate trying to act outside their scope is rejected.

Delegation TTL is capped at 30 days (MAX_DELEGATION_TTL_SECS).

## Trust Score Progression

Here's what scores look like in practice (no NetFlow, bootstrap_interactions=3):

| Interactions | Score | Notes |
|-------------|-------|-------|
| 0 | 0.000 | No history |
| 1 | 0.302 | First interaction (bootstrap allows it) |
| 2 | 0.215 | Dropped — failed attempts on gated services count |
| 3 | 0.235 | Bootstrap window closing |
| 5 | 0.299 | Building up |
| 7 | 0.370 | Moderate trust |
| 9 | 0.400 | Good trust |
| 10 | 0.415 | Solid |
| 20 | 0.480 | interaction_count saturates at 20 |
| 30 | 0.501 | Near maximum for single-peer interaction |
| 50+ | ~0.50 | Plateau without diversity |

**Key insight:** Score plateaus around 0.5 with a single counterparty. To get higher scores, interact with diverse agents (entropy component rewards diversity).

## Setting Trust Thresholds (min_trust)

### Recommended thresholds

| Threshold | Use Case | Interactions Needed |
|-----------|----------|-------------------|
| 0.0 | Open to all, including new agents | 0 |
| 0.15 | Minimal bar — filters out agents with no history | ~2 |
| 0.30 | Basic — agent has at least some track record | ~5 |
| 0.36 | Moderate — meaningful interaction history | ~7 |
| 0.40 | Good — established agent | ~9 |
| 0.50 | High — well-established with diverse interactions | ~30+ |
| 0.70 | Very high — extensive history with many partners | Requires diversity |

### Bootstrap interactions
The `bootstrap_interactions` parameter (default 3) gives new agents N free calls before trust gating kicks in. This solves the cold-start problem: an agent needs to interact to build trust, but can't interact if it has no trust.

| Setting | Behavior |
|---------|----------|
| 1 | Very strict — only 1 free call, then gated |
| 3 (default) | Balanced — enough to establish initial history |
| 5-10 | Lenient — good for public/demo services |
| 0 | No bootstrap — must have pre-existing trust (e.g., via delegation) |

### Practical examples

```python
# Public service — anyone can call
@agent.service("status", min_trust=0.0)

# Basic service — need some history
@agent.service("search", min_trust=0.15)

# Compute service — need real track record
@agent.service("compute", min_trust=0.36)

# Premium service — established agents only
@agent.service("premium", min_trust=0.40)

# Admin service — high trust required
@agent.service("admin", min_trust=0.70)
```

## Fraud Detection

### Tampered chains
If an agent modifies historical blocks, `chain_integrity()` drops below 1.0. The integrity component (weight 0.3) ensures tampered agents lose at least 30% of their trust.

### Double-spend detection
If an agent tries to create two different blocks with the same sequence number, the conflict is detected during chain verification.

### Fraud propagation
When fraud is detected for an agent, TrustChain checks ALL delegates (active + revoked), not just active ones. This prevents an attacker from delegating, committing fraud, then revoking the delegation to hide the evidence.

### Hard-zero scoring
Agents with detected fraud get a hard 0.0 trust score. This propagates through the graph — if a seed node is compromised, all trust flowing through it is cut off.

## Customizing Weights

```python
from trustchain.trust import TrustEngine

# Default weights
engine = TrustEngine(store, seed_nodes=[my_pubkey])
# integrity=0.3, netflow=0.4, statistical=0.3

# High-security: emphasize Sybil resistance
engine = TrustEngine(store, seed_nodes=[my_pubkey], weights={
    "integrity": 0.2,
    "netflow": 0.6,
    "statistical": 0.2,
})

# Simple deployment: no NetFlow (single-node, no P2P)
engine = TrustEngine(store, weights={
    "integrity": 0.5,
    "netflow": 0.0,
    "statistical": 0.5,
})
# Or just don't provide seed_nodes — same effect

# History-focused: reward interaction volume
engine = TrustEngine(store, seed_nodes=[my_pubkey], weights={
    "integrity": 0.2,
    "netflow": 0.3,
    "statistical": 0.5,
})
```

## Checkpoints (CHECO Protocol)

Finalized checkpoints allow skipping Ed25519 signature verification for historical blocks:
- `TrustEngine.with_checkpoint(cp)` skips Ed25519 for covered blocks
- Structural checks (hash links, sequence numbers) always run
- Checkpoints are persisted in SQLite: `save_checkpoint()`, `load_checkpoints()`, `latest_finalized_checkpoint()`
- The checkpoint loop in node.rs: propose → collect votes → finalize → persist → broadcast
- Persisted checkpoints are loaded on restart

## Quick Reference

**"What score do I need for X interactions?"**
- 1 interaction: ~0.302
- 5 interactions: ~0.300
- 10 interactions: ~0.415
- 20 interactions: ~0.480
- 30 interactions: ~0.501

**"How many interactions to reach score X?"**
- 0.30: ~5 interactions
- 0.36: ~7 interactions
- 0.40: ~9 interactions
- 0.45: ~13 interactions
- 0.50: ~30 interactions

**"Why is my score stuck at ~0.50?"**
- With a single counterparty, scores plateau around 0.50
- Interact with diverse agents to increase the entropy component
- Enable NetFlow with seed nodes for higher possible scores

**"Why did my score drop?"**
- Failed interactions (timeout, error, denied) reduce completion_rate
- Chain integrity issue (corrupted data)
- Temporal decay (if enabled)
- Delegation revoked (if agent is a delegate)
