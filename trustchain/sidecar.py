"""Zero-config TrustChain sidecar SDK.

Usage::

    import trustchain
    trustchain.init()
    # All HTTP calls are now trust-protected.

Or with full control::

    with trustchain.TrustChainSidecar(name="test") as tc:
        print(tc.pubkey)
        print(tc.trust_score("deadbeef..."))
"""

from __future__ import annotations

import atexit
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Global singleton
_instance: TrustChainSidecar | None = None
_lock = threading.Lock()

# urllib opener that bypasses HTTP_PROXY (prevents infinite loop when
# our sidecar IS the proxy)
_direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _binary_name() -> str:
    return "trustchain-node.exe" if _is_windows() else "trustchain-node"


def _generate_name() -> str:
    """Generate a default sidecar name from the script name + PID."""
    main = sys.modules.get("__main__")
    if main and hasattr(main, "__file__") and main.__file__:
        stem = Path(main.__file__).stem
    else:
        stem = "python"
    return f"{stem}-{os.getpid()}"


def _find_binary(explicit: str | None = None) -> str:
    """Locate the trustchain-node binary.

    Search order:
    1. Explicit path (if provided)
    2. PATH lookup
    3. ~/.trustchain/bin/
    4. Repo dev build (target/release/)
    5. Auto-build via cargo (5 min timeout)
    """
    name = _binary_name()

    # 1. Explicit
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)
        raise RuntimeError(f"Binary not found at explicit path: {explicit}")

    # 2. PATH
    found = shutil.which("trustchain-node")
    if found:
        return found

    # 3. ~/.trustchain/bin/
    home_bin = Path.home() / ".trustchain" / "bin" / name
    if home_bin.is_file():
        return str(home_bin)

    # 4. Repo dev build — walk up from this file to find trustchain-rs/
    here = Path(__file__).resolve().parent  # trustchain/
    repo = here.parent  # trustchain-agent-os/
    release = repo / "trustchain-rs" / "target" / "release" / name
    if release.is_file():
        return str(release)

    # 5. Auto-build
    cargo_dir = repo / "trustchain-rs"
    if (cargo_dir / "Cargo.toml").is_file():
        print("[trustchain] Binary not found, building from source (this may take a few minutes)...")
        try:
            subprocess.run(
                ["cargo", "build", "--release", "-p", "trustchain-node"],
                cwd=str(cargo_dir),
                check=True,
                timeout=300,
            )
        except FileNotFoundError:
            pass  # cargo not installed — fall through to error
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Auto-build failed: {exc}") from exc
        else:
            if release.is_file():
                return str(release)

    raise RuntimeError(
        "Could not find trustchain-node binary. Install options:\n"
        "  1. Add trustchain-node to PATH\n"
        "  2. Place binary at ~/.trustchain/bin/trustchain-node\n"
        "  3. Build from source: cd trustchain-rs && cargo build --release -p trustchain-node"
    )


def _find_free_port_base(count: int = 4) -> int:
    """Find a base port where `count` consecutive ports are all free.

    Scans 18200-19000 in steps of 4 (shuffled) so multiple sidecars
    don't collide.
    """
    import random

    candidates = list(range(18200, 19000, count))
    random.shuffle(candidates)

    for base in candidates:
        if _ports_available(base, count):
            return base

    # Fallback: let the OS pick a port and round down to a multiple of `count`
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return port - (port % count)


def _ports_available(base: int, count: int) -> bool:
    for offset in range(count):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", base + offset))
        except OSError:
            return False
    return True


class TrustChainSidecar:
    """Manages a trustchain-node sidecar process.

    Spawns the Rust binary, waits for it to be ready, and provides
    convenience methods for the HTTP API.
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        endpoint: str = "http://127.0.0.1:0",
        port_base: int | None = None,
        bootstrap: str | list[str] | None = None,
        data_dir: str | None = None,
        log_level: str = "info",
        binary: str | None = None,
        auto_start: bool = True,
    ) -> None:
        self._name = name or _generate_name()
        self._endpoint = endpoint
        self._port_base = port_base or _find_free_port_base()
        self._log_level = log_level
        self._binary_path = binary
        self._data_dir = data_dir
        self._pubkey: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._stopped = False
        self._prev_http_proxy: str | None = None

        # Normalize bootstrap to list
        if bootstrap is None:
            self._bootstrap: list[str] = []
        elif isinstance(bootstrap, str):
            self._bootstrap = [b.strip() for b in bootstrap.split(",") if b.strip()]
        else:
            self._bootstrap = list(bootstrap)

        if auto_start:
            self.start()

    # -- Properties --

    @property
    def name(self) -> str:
        return self._name

    @property
    def pubkey(self) -> str | None:
        if self._pubkey is None and self.is_running:
            try:
                st = self.status()
                self._pubkey = st.get("public_key")
            except Exception:
                pass
        return self._pubkey

    @property
    def port_base(self) -> int:
        return self._port_base

    @property
    def http_port(self) -> int:
        return self._port_base + 2

    @property
    def proxy_port(self) -> int:
        return self._port_base + 3

    @property
    def http_url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.proxy_port}"

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # -- Lifecycle --

    def start(self) -> None:
        """Find the binary, spawn the sidecar, wait for ready, set HTTP_PROXY."""
        if self.is_running:
            return

        self._stopped = False
        binary = _find_binary(self._binary_path)

        cmd = [
            binary, "sidecar",
            "--name", self._name,
            "--endpoint", self._endpoint,
            "--port-base", str(self._port_base),
            "--log-level", self._log_level,
        ]
        if self._bootstrap:
            cmd.extend(["--bootstrap", ",".join(self._bootstrap)])
        if self._data_dir:
            cmd.extend(["--data-dir", self._data_dir])

        env = os.environ.copy()
        env.pop("HTTP_PROXY", None)
        env.pop("http_proxy", None)

        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": env,
        }
        if _is_windows():
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        self._process = subprocess.Popen(cmd, **kwargs)

        # Parse pubkey from stdout banner (non-blocking reader)
        self._start_stdout_reader()

        # Wait for /status to respond
        self._wait_ready()

        # Set HTTP_PROXY so all outbound HTTP goes through the sidecar
        self._prev_http_proxy = os.environ.get("HTTP_PROXY")
        os.environ["HTTP_PROXY"] = self.proxy_url
        os.environ["http_proxy"] = self.proxy_url

        # Register cleanup
        atexit.register(self.stop)
        try:
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
        except (OSError, ValueError):
            pass  # fails in non-main thread or on some platforms

    def _start_stdout_reader(self) -> None:
        """Read stdout in a background thread to capture the public key."""
        def _reader() -> None:
            assert self._process is not None
            assert self._process.stdout is not None
            for raw_line in self._process.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                # Look for "Public key: <hex>"
                m = re.search(r"Public key:\s*([0-9a-fA-F]{64})", line)
                if m:
                    self._pubkey = m.group(1)

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

    def _wait_ready(self, timeout: float = 10.0) -> None:
        """Poll GET /status with exponential backoff until the sidecar responds."""
        url = f"{self.http_url}/status"
        deadline = time.monotonic() + timeout
        delay = 0.1  # start at 100ms

        while time.monotonic() < deadline:
            # Check if the process died
            if self._process is not None and self._process.poll() is not None:
                stderr = ""
                if self._process.stderr:
                    stderr = self._process.stderr.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Sidecar process exited with code {self._process.returncode}.\n"
                    f"stderr: {stderr}"
                )

            try:
                req = urllib.request.Request(url, method="GET")
                resp = _direct_opener.open(req, timeout=2)
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    if self._pubkey is None:
                        self._pubkey = data.get("public_key")
                    return
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                pass

            time.sleep(delay)
            delay = min(delay * 1.5, 1.0)

        raise TimeoutError(
            f"Sidecar did not become ready within {timeout}s "
            f"(checked {url})"
        )

    def stop(self) -> None:
        """Stop the sidecar process and restore HTTP_PROXY. Idempotent."""
        if self._stopped:
            return
        self._stopped = True

        # Restore HTTP_PROXY
        if self._prev_http_proxy is not None:
            os.environ["HTTP_PROXY"] = self._prev_http_proxy
            os.environ["http_proxy"] = self._prev_http_proxy
        else:
            os.environ.pop("HTTP_PROXY", None)
            os.environ.pop("http_proxy", None)

        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self._process.kill()
                    self._process.wait(timeout=2)
                except OSError:
                    pass
            self._process = None

    # -- Context manager --

    def __enter__(self) -> TrustChainSidecar:
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # -- HTTP API helpers --

    def _get(self, path: str) -> Any:
        url = f"{self.http_url}{path}"
        req = urllib.request.Request(url, method="GET")
        try:
            resp = _direct_opener.open(req, timeout=5)
            return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"GET {path} failed ({exc.code}): {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GET {path} failed: {exc.reason}") from exc

    def _post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self.http_url}{path}"
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            resp = _direct_opener.open(req, timeout=10)
            return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"POST {path} failed ({exc.code}): {body_text}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"POST {path} failed: {exc.reason}") from exc

    def status(self) -> dict[str, Any]:
        """GET /status — node status including public_key, block_count, peer_count."""
        return self._get("/status")

    def trust_score(self, pubkey: str) -> float:
        """GET /trust/{pubkey} — compute trust score for a peer."""
        data = self._get(f"/trust/{pubkey}")
        # Response may be {"trust_score": 0.85} or just a number
        if isinstance(data, (int, float)):
            return float(data)
        return float(data.get("trust_score", data.get("score", 0.0)))

    def discover(
        self,
        capability: str,
        *,
        min_trust: float | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        """GET /discover — P2P capability discovery with fan-out to peers."""
        params = [f"capability={urllib.request.quote(capability)}"]
        if min_trust is not None:
            params.append(f"min_trust={min_trust}")
        if max_results is not None:
            params.append(f"max_results={max_results}")
        return self._get(f"/discover?{'&'.join(params)}")

    def peers(self) -> list[dict[str, Any]]:
        """GET /peers — list known peers."""
        return self._get("/peers")

    def propose(self, counterparty: str, transaction: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST /propose — initiate a bilateral proposal with a peer."""
        body: dict[str, Any] = {"counterparty": counterparty}
        if transaction:
            body["transaction"] = transaction
        return self._post("/propose", body)

    # -- Repr --

    def __repr__(self) -> str:
        state = "running" if self.is_running else "stopped"
        return (
            f"TrustChainSidecar(name={self._name!r}, "
            f"port_base={self._port_base}, {state})"
        )


# === Module-level convenience API ===


def init(
    *,
    name: str | None = None,
    endpoint: str = "http://127.0.0.1:0",
    port_base: int | None = None,
    bootstrap: str | list[str] | None = None,
    data_dir: str | None = None,
    log_level: str = "info",
    binary: str | None = None,
) -> TrustChainSidecar:
    """Start the TrustChain sidecar (idempotent singleton).

    Call once at the top of your script::

        import trustchain
        trustchain.init()
        # done — all HTTP calls are now trust-protected
    """
    global _instance
    with _lock:
        if _instance is not None and _instance.is_running:
            return _instance
        _instance = TrustChainSidecar(
            name=name,
            endpoint=endpoint,
            port_base=port_base,
            bootstrap=bootstrap,
            data_dir=data_dir,
            log_level=log_level,
            binary=binary,
        )
        return _instance


def protect(**kwargs: Any) -> TrustChainSidecar:
    """Alias for init() that communicates intent: protect all HTTP calls."""
    return init(**kwargs)
