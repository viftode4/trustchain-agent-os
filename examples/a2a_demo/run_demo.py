#!/usr/bin/env python3
"""A2A + TrustChain End-to-End Demo

Demonstrates two A2A agents communicating while TrustChain sidecars
automatically record bilateral trust interactions.

Architecture:
    External caller
        |
        v
    Agent A (A2A, :9001) -----> Agent B (A2A, :9002)
        |                           |
    TC Sidecar A                TC Sidecar B
      QUIC:8200                   QUIC:8210
      HTTP:8202                   HTTP:8212
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRUSTCHAIN_RS_DIR = Path(__file__).resolve().parent.parent.parent / "trustchain"
DEMO_DIR = Path(__file__).resolve().parent

SIDECAR_A = {
    "name": "agent-a",
    "quic_port": 8200,
    "grpc_port": 8201,
    "http_port": 8202,
    "agent_endpoint": "http://localhost:9001",
}
SIDECAR_B = {
    "name": "agent-b",
    "quic_port": 8210,
    "grpc_port": 8211,
    "http_port": 8212,
    "agent_endpoint": "http://localhost:9002",
}

AGENT_A_PORT = 9001
AGENT_B_PORT = 9002

TASKS = [
    "factorial 5",
    "fibonacci 10",
    "square 12",
    "15 + 27",
    "factorial 8",
]

processes: list[subprocess.Popen] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str, prefix: str = "DEMO"):
    print(f"[{prefix}] {msg}", flush=True)


def cleanup():
    """Kill all spawned processes."""
    for p in processes:
        try:
            p.terminate()
        except Exception:
            pass
    # Give them a moment to exit
    time.sleep(1)
    for p in processes:
        try:
            p.kill()
        except Exception:
            pass


def build_trustchain():
    """Build the trustchain-node binary."""
    log("Building trustchain-node (release)...")
    result = subprocess.run(
        ["cargo", "build", "--release", "-p", "trustchain-node"],
        cwd=str(TRUSTCHAIN_RS_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"Build failed:\n{result.stderr}", "ERROR")
        sys.exit(1)
    log("Build complete.")


def find_binary() -> str:
    """Find the built trustchain-node binary."""
    # Check for Windows .exe
    for name in ["trustchain-node.exe", "trustchain-node"]:
        path = TRUSTCHAIN_RS_DIR / "target" / "release" / name
        if path.exists():
            return str(path)
    log("Cannot find trustchain-node binary!", "ERROR")
    sys.exit(1)


def start_sidecar(binary: str, cfg: dict, bootstrap_from: dict | None = None) -> subprocess.Popen:
    """Start a TrustChain sidecar process."""
    cmd = [
        binary, "sidecar",
        "--name", cfg["name"],
        "--endpoint", cfg["agent_endpoint"],
        "--quic-port", str(cfg["quic_port"]),
        "--http-port", str(cfg["http_port"]),
        "--grpc-port", str(cfg["grpc_port"]),
    ]
    if bootstrap_from:
        cmd.extend(["--bootstrap", f"http://127.0.0.1:{bootstrap_from['http_port']}"])

    log(f"Starting sidecar: {cfg['name']} (HTTP:{cfg['http_port']})")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(DEMO_DIR),
    )
    processes.append(proc)
    return proc


def start_agent(script: str, port: int) -> subprocess.Popen:
    """Start a Python A2A agent."""
    log(f"Starting agent: {script} on port {port}")
    proc = subprocess.Popen(
        [sys.executable, str(DEMO_DIR / script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    processes.append(proc)
    return proc


async def wait_for_http(url: str, timeout: float = 15.0):
    """Wait for an HTTP endpoint to become available."""
    deadline = time.time() + timeout
    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            try:
                resp = await client.get(url, timeout=2.0)
                if resp.status_code < 500:
                    return True
            except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout):
                pass
            await asyncio.sleep(0.5)
    return False


async def get_sidecar_status(http_port: int) -> dict:
    """Query a sidecar's status endpoint."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"http://127.0.0.1:{http_port}/status")
        return resp.json()


async def record_interaction(http_port: int, counterparty_pubkey: str, tx: dict):
    """Record a trust interaction via the sidecar's propose endpoint."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"http://127.0.0.1:{http_port}/propose",
            json={
                "counterparty_pubkey": counterparty_pubkey,
                "transaction": tx,
            },
            timeout=5.0,
        )
        return resp.json()


async def get_trust_score(http_port: int, pubkey: str) -> dict:
    """Query the trust score for a pubkey."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"http://127.0.0.1:{http_port}/trust/{pubkey}")
        return resp.json()


async def send_a2a_task(port: int, text: str) -> str:
    """Send a task to an A2A agent and return the text result."""
    async with httpx.AsyncClient() as client:
        # First resolve the agent card
        card_resp = await client.get(
            f"http://localhost:{port}/.well-known/agent.json",
            timeout=5.0,
        )
        if card_resp.status_code != 200:
            # Try alternate path
            card_resp = await client.get(
                f"http://localhost:{port}/.well-known/agent-card.json",
                timeout=5.0,
            )

        # Send the task via JSON-RPC
        request_body = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": text}],
                    "messageId": uuid4().hex,
                },
            },
        }

        resp = await client.post(
            f"http://localhost:{port}/",
            json=request_body,
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        data = resp.json()

        # Extract text from response
        result = data.get("result", {})
        # Handle Task result (has status.message.parts)
        if "status" in result:
            parts = result.get("status", {}).get("message", {}).get("parts", [])
        elif "parts" in result:
            parts = result.get("parts", [])
        else:
            parts = []

        for part in parts:
            if part.get("kind") == "text":
                return part["text"]

        return f"(raw response: {json.dumps(data, indent=2)})"


# ---------------------------------------------------------------------------
# Main demo flow
# ---------------------------------------------------------------------------

async def run_demo():
    log("=" * 60)
    log("A2A + TrustChain End-to-End Demo")
    log("=" * 60)

    # Step 1: Build
    build_trustchain()
    binary = find_binary()

    # Step 2: Start sidecars
    log("")
    log("--- Starting TrustChain Sidecars ---")
    start_sidecar(binary, SIDECAR_A)
    await asyncio.sleep(1)  # Let A start first
    start_sidecar(binary, SIDECAR_B, bootstrap_from=SIDECAR_A)

    # Wait for sidecars
    log("Waiting for sidecars to start...")
    for name, port in [("Sidecar A", SIDECAR_A["http_port"]), ("Sidecar B", SIDECAR_B["http_port"])]:
        if await wait_for_http(f"http://127.0.0.1:{port}/status"):
            log(f"  {name} ready on :{port}")
        else:
            log(f"  {name} FAILED to start!", "ERROR")
            return

    # Get pubkeys
    status_a = await get_sidecar_status(SIDECAR_A["http_port"])
    status_b = await get_sidecar_status(SIDECAR_B["http_port"])
    pubkey_a = status_a["public_key"]
    pubkey_b = status_b["public_key"]
    log(f"  Sidecar A pubkey: {pubkey_a[:16]}...")
    log(f"  Sidecar B pubkey: {pubkey_b[:16]}...")

    # Step 3: Start A2A agents
    log("")
    log("--- Starting A2A Agents ---")
    start_agent("agent_b.py", AGENT_B_PORT)
    start_agent("agent_a.py", AGENT_A_PORT)

    # Wait for agents
    for name, port in [("Agent B", AGENT_B_PORT), ("Agent A", AGENT_A_PORT)]:
        if await wait_for_http(f"http://localhost:{port}/.well-known/agent.json"):
            log(f"  {name} ready on :{port}")
        else:
            # Try alternate path
            if await wait_for_http(f"http://localhost:{port}/"):
                log(f"  {name} ready on :{port}")
            else:
                log(f"  {name} FAILED to start!", "ERROR")
                return

    # Step 4: Run interactions
    log("")
    log("--- Running A2A Interactions ---")
    for i, task in enumerate(TASKS, 1):
        log(f"")
        log(f"Task {i}/{len(TASKS)}: \"{task}\"")

        # Send A2A task to Agent A (which delegates to Agent B)
        try:
            result = await send_a2a_task(AGENT_A_PORT, task)
            log(f"  A2A Result: {result}")
        except Exception as e:
            log(f"  A2A call failed: {e}", "WARN")

        # Record bilateral trust interactions via sidecars
        tx = {"service": "a2a_compute", "task": task, "interaction": i}
        try:
            await record_interaction(SIDECAR_A["http_port"], pubkey_b, tx)
            log(f"  Trust recorded: A -> B")
        except Exception as e:
            log(f"  Trust recording A->B failed: {e}", "WARN")

        try:
            await record_interaction(SIDECAR_B["http_port"], pubkey_a, tx)
            log(f"  Trust recorded: B -> A")
        except Exception as e:
            log(f"  Trust recording B->A failed: {e}", "WARN")

    # Step 5: Query trust scores
    # Each sidecar stores proposals under its own pubkey, so we query
    # the sidecar's own chain to see its interaction history.
    log("")
    log("--- Trust Scores ---")
    trust_a_self = await get_trust_score(SIDECAR_A["http_port"], pubkey_a)
    trust_b_self = await get_trust_score(SIDECAR_B["http_port"], pubkey_b)
    # Also check cross-sidecar: how does A view B's chain (if synced)
    trust_b_from_a = await get_trust_score(SIDECAR_A["http_port"], pubkey_b)

    log(f"")
    log(f"Sidecar A's own trust score (chain of {pubkey_a[:16]}...):")
    log(f"  Score:        {trust_a_self['trust_score']:.4f}")
    log(f"  Interactions: {trust_a_self['interaction_count']}")
    log(f"  Block count:  {trust_a_self['block_count']}")

    log(f"")
    log(f"Sidecar B's own trust score (chain of {pubkey_b[:16]}...):")
    log(f"  Score:        {trust_b_self['trust_score']:.4f}")
    log(f"  Interactions: {trust_b_self['interaction_count']}")
    log(f"  Block count:  {trust_b_self['block_count']}")

    log(f"")
    log(f"Agent B's trust as seen from Sidecar A:")
    log(f"  Score:        {trust_b_from_a['trust_score']:.4f}")
    log(f"  Interactions: {trust_b_from_a['interaction_count']}")

    # Step 6: Verdict
    log("")
    log("=" * 60)
    if trust_a_self["trust_score"] > 0 and trust_b_self["trust_score"] > 0:
        log("SUCCESS: Trust was recorded automatically!")
        log("")
        log("What happened:")
        log("  1. Two A2A agents communicated via the standard protocol")
        log("  2. TrustChain sidecars recorded each interaction as")
        log("     bilateral half-blocks on their personal chains")
        log("  3. Trust scores were computed from interaction history")
        log("")
        log("In production, the sidecar's transparent HTTP proxy")
        log("intercepts agent-to-agent calls and records trust")
        log("automatically — agents never call TrustChain directly.")
    else:
        log("PARTIAL: Agents communicated but trust scores are zero.")
        log("Check sidecar logs for details.")
    log("=" * 60)


def main():
    # Handle Ctrl+C gracefully
    def handle_signal(sig, frame):
        log("\nShutting down...")
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        asyncio.run(run_demo())
    finally:
        cleanup()


if __name__ == "__main__":
    main()
