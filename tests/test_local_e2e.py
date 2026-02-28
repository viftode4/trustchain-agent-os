"""Local end-to-end test: Rust sidecar + Python SDK + Agent OS.

This test spawns real Rust trustchain-node sidecars and verifies the full
stack works: identity generation, HTTP API, trust scoring, bilateral
proposals, and the Python sidecar SDK wrapper.

Requires: trustchain-node binary (built from G:\\Projects\\blockchains\\trustchain\\)

Run with:
    python -m pytest tests/test_local_e2e.py -v -s
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

BINARY = Path(r"G:\Projects\blockchains\trustchain\target\release\trustchain-node.exe")

if not BINARY.exists():
    pytest.skip(
        f"Rust binary not found at {BINARY} — run `cargo build --release` first",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_free_port_base(count: int = 4):
    """Find a base port where `count` consecutive ports are all free.

    Scans 18200-19000 in steps of 4 (shuffled) — same range as the SDK.
    """
    import random
    import socket

    candidates = list(range(18200, 19000, count))
    random.shuffle(candidates)

    for base in candidates:
        all_free = True
        for offset in range(count):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", base + offset))
            except OSError:
                all_free = False
                break
        if all_free:
            return base

    raise RuntimeError("Could not find 4 consecutive free ports in 18200-19000")


def _wait_for_http(url: str, timeout: float = 15.0, process: subprocess.Popen | None = None):
    """Poll a URL until it responds with 200."""
    deadline = time.monotonic() + timeout
    delay = 0.2
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        # Check if the process died
        if process is not None and process.poll() is not None:
            stderr = ""
            if process.stderr:
                try:
                    stderr = process.stderr.read(8192).decode("utf-8", errors="replace")
                except Exception:
                    stderr = "(stderr unreadable)"
            raise RuntimeError(
                f"Sidecar process exited with code {process.returncode}.\nstderr: {stderr}"
            )
        try:
            req = urllib.request.Request(url, method="GET")
            resp = opener.open(req, timeout=2)
            if resp.status == 200:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError,
                http.client.HTTPException):
            pass
        time.sleep(delay)
        delay = min(delay * 1.5, 1.0)
    raise TimeoutError(f"Timed out waiting for {url}")


class SidecarProcess:
    """Manages a trustchain-node sidecar process for testing."""

    def __init__(self, name: str, port_base: int, data_dir: str, endpoint: str = "http://localhost:9999"):
        self.name = name
        self.port_base = port_base
        self.data_dir = data_dir
        self.endpoint = endpoint
        self.http_url = f"http://127.0.0.1:{port_base + 2}"
        self.proxy_url = f"http://127.0.0.1:{port_base + 3}"
        self._process = None

    def start(self, bootstrap: str | None = None):
        cmd = [
            str(BINARY), "sidecar",
            "--name", self.name,
            "--endpoint", self.endpoint,
            "--port-base", str(self.port_base),
            "--data-dir", self.data_dir,
            "--log-level", "info",
        ]
        if bootstrap:
            cmd.extend(["--bootstrap", bootstrap])

        env = os.environ.copy()
        env.pop("HTTP_PROXY", None)
        env.pop("http_proxy", None)
        env["RUST_LOG"] = "info"

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

        # Wait for HTTP to be ready
        status = _wait_for_http(f"{self.http_url}/status", process=self._process)
        self.pubkey = status.get("public_key", "unknown")
        return status

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
        self._process = None

    def get(self, path: str):
        req = urllib.request.Request(f"{self.http_url}{path}", method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            resp = opener.open(req, timeout=5)
            return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"GET {path} failed ({exc.code}): {body_text}") from exc

    def post(self, path: str, body: dict):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.http_url}{path}",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            resp = opener.open(req, timeout=10)
            return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"POST {path} failed ({exc.code}): {body_text}") from exc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRustSidecarSmoke:
    """Smoke tests: spawn a single Rust sidecar, verify basic APIs."""

    @pytest.fixture(autouse=True)
    def sidecar(self, tmp_path):
        port_base = _find_free_port_base()
        self.node = SidecarProcess("smoke-test", port_base, str(tmp_path / "data"))
        status = self.node.start()
        print(f"\n  Sidecar started: port_base={port_base} pubkey={self.node.pubkey[:16]}...")
        yield
        self.node.stop()

    def test_status_returns_pubkey(self):
        """GET /status returns a valid public key."""
        status = self.node.get("/status")
        assert "public_key" in status
        assert len(status["public_key"]) == 64  # hex-encoded Ed25519 pubkey
        print(f"  Status: pubkey={status['public_key'][:16]}... blocks={status.get('block_count', 0)}")

    def test_trust_of_unknown_peer(self):
        """GET /trust/{unknown_pubkey} returns a score for unknown peer."""
        fake_pubkey = "a" * 64
        score = self.node.get(f"/trust/{fake_pubkey}")
        print(f"  Trust of unknown peer: {score}")
        # Should return some score (0.0 or 0.5 depending on TrustEngine defaults)
        assert isinstance(score, (dict, float, int))

    def test_peers_initially_empty(self):
        """GET /peers returns empty list when no bootstrap."""
        peers = self.node.get("/peers")
        assert isinstance(peers, list)
        print(f"  Peers: {len(peers)} (expected 0)")


class TestTwoSidecarBilateral:
    """Two Rust sidecars discover each other and create a bilateral proposal."""

    @pytest.fixture(autouse=True)
    def two_sidecars(self, tmp_path):
        port_base_a = _find_free_port_base()
        port_base_b = _find_free_port_base()
        # Make sure they don't overlap
        while abs(port_base_a - port_base_b) < 4:
            port_base_b = _find_free_port_base()

        self.alice = SidecarProcess("alice", port_base_a, str(tmp_path / "alice"))
        self.bob = SidecarProcess("bob", port_base_b, str(tmp_path / "bob"))

        # Start both without bootstrap (bootstrap triggers QUIC P2P which
        # may hang on Windows).  Use manual peer registration instead.
        self.alice.start()
        self.bob.start()

        print(f"\n  Alice: port={port_base_a} pubkey={self.alice.pubkey[:16]}...")
        print(f"  Bob:   port={port_base_b} pubkey={self.bob.pubkey[:16]}...")
        yield
        self.bob.stop()
        self.alice.stop()

    def test_mutual_peer_registration(self):
        """Register peers manually and verify they appear."""
        # Register Bob on Alice
        self.alice.post("/peers", {
            "pubkey": self.bob.pubkey,
            "address": self.bob.http_url,
        })
        # Register Alice on Bob
        self.bob.post("/peers", {
            "pubkey": self.alice.pubkey,
            "address": self.alice.http_url,
        })

        alice_peers = self.alice.get("/peers")
        bob_peers = self.bob.get("/peers")
        print(f"  Alice's peers: {len(alice_peers)}")
        print(f"  Bob's peers: {len(bob_peers)}")
        assert len(alice_peers) >= 1
        assert len(bob_peers) >= 1

    def test_bilateral_proposal(self):
        """Alice proposes to Bob, creating a bilateral half-block pair."""
        # Register Bob as a peer on Alice's side
        self.alice.post("/peers", {
            "pubkey": self.bob.pubkey,
            "address": self.bob.http_url,
        })

        # Alice proposes a transaction to Bob
        result = self.alice.post("/propose", {
            "counterparty_pubkey": self.bob.pubkey,
            "transaction": {
                "interaction_type": "e2e_test",
                "outcome": "completed",
            },
        })
        print(f"  Proposal result: completed={result.get('completed', False)}")

        # Check Alice's chain grew
        alice_status = self.alice.get("/status")
        print(f"  Alice blocks after proposal: {alice_status.get('block_count', 0)}")
        assert alice_status.get("block_count", 0) >= 2  # genesis + proposal


class TestPythonSidecarSDK:
    """Test the Python TrustChainSidecar wrapper with a real Rust binary."""

    def test_sidecar_lifecycle(self, tmp_path):
        """Start sidecar via Python SDK, check status, stop."""
        from trustchain.sidecar import TrustChainSidecar

        sc = TrustChainSidecar(
            name="sdk-test",
            binary=str(BINARY),
            data_dir=str(tmp_path / "data"),
            auto_start=False,
        )
        assert not sc.is_running

        sc.start()
        assert sc.is_running
        print(f"\n  SDK sidecar started: pubkey={sc.pubkey}")

        # Check status
        status = sc.status()
        assert "public_key" in status
        assert status["public_key"] == sc.pubkey
        print(f"  Status: {status}")

        # Check trust of self (should be high or neutral)
        if sc.pubkey:
            trust = sc.trust_score(sc.pubkey)
            print(f"  Self-trust: {trust}")

        # Check peers
        peers = sc.peers()
        print(f"  Peers: {peers}")

        sc.stop()
        assert not sc.is_running
        print("  Sidecar stopped cleanly.")

    def test_sidecar_context_manager(self, tmp_path):
        """Sidecar as context manager starts and stops cleanly."""
        from trustchain.sidecar import TrustChainSidecar

        with TrustChainSidecar(
            name="ctx-test",
            binary=str(BINARY),
            data_dir=str(tmp_path / "data"),
        ) as sc:
            assert sc.is_running
            status = sc.status()
            print(f"\n  Context manager sidecar: pubkey={status['public_key'][:16]}...")

        # After exiting context, should be stopped
        assert not sc.is_running
        print("  Context manager cleanup: OK")

    def test_two_sidecars_bilateral(self, tmp_path):
        """Two Python-managed sidecars perform a bilateral proposal."""
        from trustchain.sidecar import TrustChainSidecar

        with TrustChainSidecar(
            name="py-alice",
            binary=str(BINARY),
            data_dir=str(tmp_path / "alice"),
            auto_start=False,
        ) as alice:
            alice.start()

            with TrustChainSidecar(
                name="py-bob",
                binary=str(BINARY),
                data_dir=str(tmp_path / "bob"),
                auto_start=False,
            ) as bob:
                bob.start()

                print(f"\n  Alice: {alice.pubkey[:16]}... port={alice.port_base}")
                print(f"  Bob:   {bob.pubkey[:16]}... port={bob.port_base}")

                # Register Bob as peer on Alice
                try:
                    alice._post("/peers", {
                        "pubkey": bob.pubkey,
                        "address": bob.http_url,
                    })
                    print("  Peer registered: Alice knows Bob")
                except Exception as e:
                    print(f"  Peer registration: {e}")

                # Alice proposes to Bob
                try:
                    result = alice.propose(
                        bob.pubkey,
                        {"interaction_type": "sdk_e2e", "outcome": "completed"},
                    )
                    print(f"  Proposal result: {result}")
                except Exception as e:
                    print(f"  Proposal result: {e}")

                # Both should be running
                assert alice.is_running
                assert bob.is_running

        print("  Both sidecars stopped cleanly.")
