# TrustChain Agent-OS -- Claude Code Instructions

Agent framework integration layer for TrustChain. Connects 12 AI agent frameworks to
TrustChain's trust primitive via a sidecar gateway and MCP tooling.

## Setup

```bash
pip install -e ".[dev]"          # core + test deps
pip install -e ".[dev,gateway]"  # include FastAPI gateway
pip install -e ".[all-frameworks]"  # install all optional framework SDKs
```

## Tests

```bash
python -m pytest tests/ -x -q   # 205 tests
python -m pytest tests/ -x -q --co -q  # dry-run (list tests only)
```

## Package Structure

```
agent_os/         agent.py, context.py, decorators.py
gateway/          config.py, middleware.py, node.py, recorder.py, registry.py, server.py, trust_tools.py
tc_frameworks/
  base.py
  adapters/       langgraph, crewai, autogen, openai_agents, google_adk, elizaos,
                  claude_agent, smolagents, pydantic_ai, semantic_kernel, agno, llamaindex
  mock/           6 mocks (langgraph, crewai, autogen, openai_agents, google_adk, elizaos)
```

## Key Conventions

- **Dependencies**: `trustchain-py>=2.0` + `fastmcp>=3.0` are the only hard deps.
  Framework SDKs (langgraph, crewai, ag2, etc.) are optional -- declared under
  `[project.optional-dependencies]` in pyproject.toml.
- **No delegation logic here**: delegation, scope, TTL, revocation all live in
  `trustchain-py`. This repo only calls into the SDK.
- **Lazy imports**: never import framework SDKs at module top level. Import inside
  the adapter class or function so missing optional deps don't crash the whole package.
- **Error resilience**: trust machinery (sidecar calls, MCP tools) must never raise
  into the agent call path -- catch and log, return a safe default.
- **MCP caller identity**: all MCP tool calls require `caller_pubkey`. Anonymous
  callers are rejected -- no fallback to anonymous identity.
- **Bootstrap thresholds**: never hardcode values like `< 3` interactions. Read from
  config or pass as parameters.

## Do Not

- Do not add delegation/scope/TTL logic -- that belongs in `trustchain-py`.
- Do not import framework SDKs at the top of any module.
- Do not hardcode bootstrap thresholds or trust score cutoffs.
- Do not break agent calls on trust errors -- always degrade gracefully.
