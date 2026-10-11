## Local Notes
- Read `CLAUDE.md` here before making non-trivial changes; it captures adapter-layer constraints.
- Verification: `pip install -e \".[dev]\"` (or needed extras) then `python -m pytest tests/ -x -q`.
- Do not add delegation/scope/TTL logic here; it belongs in `../trustchain-py`.
- Keep framework imports lazy so optional dependencies do not break the package.
- Trust/MCP failures must degrade safely, and MCP calls require `caller_pubkey` rather than anonymous fallback.
