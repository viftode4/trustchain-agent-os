# TrustChain Demo Results — March 1, 2026

## Environment

- **OS:** Windows 11 Pro
- **Python:** 3.13
- **Rust node:** trustchain-node 0.1.0 (pre-built release binary)
- **SDK:** trustchain-py 2.1.0
- **Agent-OS:** trustchain-agent-os 2.0.0
- **Test counts:** Rust 214, SDK 174, Agent-OS 205

---

## Demo 1: hello_trust — Bilateral Trust Accumulation

Two agents (buyer and seller) perform 10 interactions. Trust grows with every
successful bilateral exchange. Both parties maintain identical, tamper-proof chains.

```
buyer  pubkey: fa98a124a5ce6f76...
seller pubkey: 262d90b0ee9ef85a...

Round  1: 1² =   1  | buyer=0.302  seller=0.302
Round  2: 2² =   4  | buyer=0.315  seller=0.315
Round  3: 3² =   9  | buyer=0.328  seller=0.328
Round  4: 4² =  16  | buyer=0.340  seller=0.340
Round  5: 5² =  25  | buyer=0.353  seller=0.353
Round  6: 6² =  36  | buyer=0.365  seller=0.365
Round  7: 7² =  49  | buyer=0.378  seller=0.378
Round  8: 8² =  64  | buyer=0.390  seller=0.390
Round  9: 9² =  81  | buyer=0.403  seller=0.403
Round 10: 10² = 100  | buyer=0.415  seller=0.415

buyer  interactions: 10
seller interactions: 10
buyer  integrity:    1.000
seller integrity:    1.000
```

**Key observations:**
- Trust starts at 0.302 (bootstrap score for first interaction)
- Grows steadily with each completed interaction
- Both parties get symmetric trust (bilateral — neither side can fabricate)
- Chain integrity = 1.000 (no tampering detected, hash chain intact)

---

## Demo 2: trust_gate — Progressive Access Control

A new agent tries to access 3 service tiers with different trust thresholds.
It starts blocked from higher tiers and must earn access through real interactions.

```
Trust Gate Enforcement Demo
==============================================================
Tiers:  basic(0.0)  compute(0.36)  premium(0.40)
Bootstrap: 1 free call per service, then score-gated

Rnd    Score     basic    compute    premium  note
--------------------------------------------------------------
  1    0.302        OK    BLOCKED    BLOCKED
  2    0.215        OK    BLOCKED    BLOCKED
  3    0.235        OK    BLOCKED    BLOCKED
  4    0.265        OK    BLOCKED    BLOCKED
  5    0.299        OK    BLOCKED    BLOCKED
  6    0.334        OK    BLOCKED    BLOCKED
  7    0.370        OK         OK    BLOCKED  << compute gate open
  8    0.392        OK         OK    BLOCKED
  9    0.400        OK         OK         OK  << PREMIUM gate open
 10    0.415        OK         OK         OK
 ...
 30    0.500        OK         OK         OK

Final score: 0.501  |  interactions: 90
compute unlocked: True  |  premium unlocked: True
```

**Key observations:**
- Round 1: bootstrap gives one free call, initial trust = 0.302
- Round 2: score drops to 0.215 (failed attempts on gated services count)
- Round 7: trust reaches 0.370 → compute gate (threshold 0.36) opens
- Round 9: trust reaches 0.400 → premium gate (threshold 0.40) opens
- No API keys, no allowlists — trust earned through real interaction history

---

## Demo 3: framework_interop — 11 Frameworks, One Trust Layer

Every major AI agent framework running its REAL runtime with Gemini as the LLM
backend, all sharing one trust ledger. This is the "USB-C of trust" demo.

### Frameworks loaded

| # | Name | Framework | Model | Status |
|---|------|-----------|-------|--------|
| 1 | langgraph | LangGraph | gemini-2.5-flash | LOADED |
| 2 | crewai | CrewAI | gemini-2.5-flash-lite | LOADED |
| 3 | openai_agents | OpenAI Agents SDK | gemini-2.0-flash-lite | LOADED |
| 4 | google_adk | Google ADK | gemini-2.5-flash | LOADED |
| 5 | autogen | AutoGen/AG2 | gemini-2.5-flash-lite | LOADED |
| 6 | claude | Claude (Anthropic) | gemini-2.0-flash-lite | LOADED |
| 7 | smolagents | Smolagents (HF) | gemini-2.5-flash | LOADED |
| 8 | pydantic_ai | PydanticAI | gemini-2.5-flash-lite | LOADED |
| 9 | semantic_kernel | Semantic Kernel | gemini-2.0-flash-lite | LOADED |
| 10 | agno | Agno | gemini-2.5-flash | LOADED |
| 11 | llamaindex | LlamaIndex | gemini-2.5-flash-lite | LOADED |
| 12 | elizaos | ElizaOS | — | SKIP (needs running server) |

### Task

> "How should AI agents establish trust with each other? Answer in 2 sentences."

### Final trust scoreboard

```
======================================================================
Final Trust Scoreboard — All Frameworks on One Ledger
======================================================================
  # 1         langgraph (           LangGraph): 0.302  (1 interactions)
  # 2            crewai (              CrewAI): 0.302  (1 interactions)
  # 3        google_adk (          Google ADK): 0.302  (1 interactions)
  # 4           autogen (         AutoGen/AG2): 0.302  (1 interactions)
  # 5        smolagents (     Smolagents (HF)): 0.302  (1 interactions)
  # 6       pydantic_ai (          PydanticAI): 0.302  (1 interactions)
  # 7              agno (                Agno): 0.302  (1 interactions)
  # 8        llamaindex (          LlamaIndex): 0.302  (1 interactions)
  # 9     openai_agents (   OpenAI Agents SDK): 0.053  (1 interactions) *
  #10            claude (  Claude (Anthropic)): 0.053  (1 interactions) *
  #11   semantic_kernel (     Semantic Kernel): 0.053  (1 interactions) *

* These 3 hit Gemini free-tier quota (gemini-2.0-flash-lite exhausted).
  The low score (0.053) is correct behavior: a failed interaction = low trust.
```

### Sample LLM responses (real, not simulated)

- **LangGraph:** "AI agents should establish trust by transparently communicating their intentions..."
- **Smolagents:** "AI agents can establish trust through transparent communication of their goals..."
- **PydanticAI:** "AI agents can establish trust by adhering to transparent and verifiable communication..."
- **LlamaIndex:** "AI agents can establish trust through transparent communication of their intentions..."

**Key observations:**
- 11 different framework runtimes, all on one bilateral trust ledger
- Trust scoring is framework-agnostic — same algorithm regardless of which framework
- Failed calls (quota errors) correctly produce low trust (0.053), not crashes
- Every agent used its REAL framework runtime (not simulated string responses)
- Gemini free tier: models spread across gemini-2.5-flash, 2.5-flash-lite, 2.0-flash-lite
