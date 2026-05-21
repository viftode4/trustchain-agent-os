<!-- OMX:AGENTS-INIT:MANAGED -->
<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-07 | Updated: 2026-05-21 -->

# trustchain-agent-os

## Purpose
Agent framework integration layer for TrustChain. Connects 12 AI agent frameworks (LangGraph, CrewAI, AutoGen, OpenAI Agents, Google ADK, ElizaOS, Claude Agent, Smolagents, Pydantic AI, Semantic Kernel, Agno, LlamaIndex) to TrustChain's trust primitive via a sidecar gateway and MCP tooling. Trust logic stays in `trustchain-py`; this repo is the adapter surface. See `CLAUDE.md` for adapter constraints.

## Key Files
| File | Description |
|------|-------------|
| `CLAUDE.md` | Adapter-layer constraints (read first) |
| `pyproject.toml` | Package metadata, optional-deps per framework |
| `README.md` | Overview |
| `LICENSE` | License |
| `.env` | Local environment configuration |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `agent_os/` | Core agent abstractions: `agent.py`, `context.py`, `decorators.py` |
| `gateway/` | FastAPI gateway: `config`, `middleware`, `node`, `recorder`, `registry`, `server`, `trust_tools` |
| `tc_frameworks/` | `base.py` + `adapters/` (12 frameworks) + `mock/` (6 mocks for testing) |
| `tests/` | Pytest suite (192 tests) |
| `examples/` | Per-framework usage examples |
| `docs/` | Adapter documentation |

## For AI Agents
### Working In This Directory
- Read `CLAUDE.md` before non-trivial changes.
- Hard deps only: `trustchain-py>=2.0` + `fastmcp>=3.0`. Framework SDKs are optional extras under `[project.optional-dependencies]`.
- Lazy-import all framework SDKs (never at module top level). Missing optional deps must not crash the whole package.
- Trust machinery (sidecar calls, MCP tools) must never raise into the agent call path — catch, log, return safe default.
- MCP calls require `caller_pubkey`. Anonymous callers are rejected; do not fall back to anonymous identity.
- No delegation/scope/TTL logic here — it belongs in `trustchain-py`.
- Never hardcode bootstrap thresholds (e.g. `< 3` interactions) or trust score cutoffs; read from config or pass as parameters.

### Testing Requirements
- `pip install -e ".[dev]"` (add `[dev,gateway]` for gateway tests, `[all-frameworks]` for framework SDKs)
- `python -m pytest tests/ -x -q` (192 tests)
- `python -m pytest tests/ -x -q --co -q` for a dry-run listing

### Common Patterns
- One adapter per framework under `tc_frameworks/adapters/`.
- Mock counterparts under `tc_frameworks/mock/` for offline tests.
- Gateway HTTP exposed via `gateway/server.py`; MCP tools via `gateway/trust_tools.py`.

## Dependencies
### Internal
- `trustchain-py` (hard dep).
- Calls Rust sidecar transitively via `trustchain-py`.

### External
- Hard: `fastmcp>=3.0`.
- Optional per-framework: `langgraph`, `crewai`, `ag2`, `openai-agents`, `google-adk`, `elizaos`, `claude-agent-sdk`, `smolagents`, `pydantic-ai`, `semantic-kernel`, `agno`, `llama-index`.

<!-- OMX:AGENTS-INIT:MANUAL:START -->
## Local Notes
- Read `CLAUDE.md` here before making non-trivial changes; it captures adapter-layer constraints.
- Verification: `pip install -e \".[dev]\"` (or needed extras) then `python -m pytest tests/ -x -q`.
- Do not add delegation/scope/TTL logic here; it belongs in `../trustchain-py`.
- Keep framework imports lazy so optional dependencies do not break the package.
- Trust/MCP failures must degrade safely, and MCP calls require `caller_pubkey` rather than anonymous fallback.
<!-- OMX:AGENTS-INIT:MANUAL:END -->
