"""Supervisor + TrustChain Demo -- Real sidecars, real chains, real trust.

4 agents, each with its own sidecar (trustchain-node process):
  - supervisor: monitors workers, flags rogue behavior
  - alice:      reliable worker
  - bob:        reliable worker
  - mallory:    goes rogue at round 6

Shows: trust building, rogue detection, isolation, permanent record.
Each agent has its own Ed25519 identity, SQLite chain, and sidecar ports.

Run: python examples/supervisor_demo.py
     (requires local Claude API proxy at http://127.0.0.1:8082)
"""
import os
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

import anthropic

from trustchain.sidecar import TrustChainSidecar

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def green(s):  return f"{GREEN}{s}{RESET}"
def red(s):    return f"{RED}{s}{RESET}"
def yellow(s): return f"{YELLOW}{s}{RESET}"
def cyan(s):   return f"{CYAN}{s}{RESET}"
def bold(s):   return f"{BOLD}{s}{RESET}"
def dim(s):    return f"{DIM}{s}{RESET}"

# ── Config ───────────────────────────────────────────────────────────────────

BINARY = str(
    Path(__file__).resolve().parent.parent.parent
    / "trustchain" / "target" / "release" / "trustchain-node.exe"
)
PROXY_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:8082")
MODEL = os.environ.get("CLAUDE_MODEL", "haiku")

TASKS = [
    "Write a Python function that checks if a number is prime.",
    "Write a Python function that reverses a string.",
    "Write a Python function that finds the max in a list.",
    "Write a Python one-liner that flattens a nested list.",
    "Write a Python function that computes factorial iteratively.",
    "Write a Python function for binary search.",
    "Write a Python function that checks for palindromes.",
    "Write a Python function that merges two sorted lists.",
    "Write a Python function that removes duplicates from a list.",
    "Write a Python function that computes Fibonacci iteratively.",
]

TRUST_THRESHOLD = 0.5  # Below this, agent is blocked

# ── Claude proxy client ──────────────────────────────────────────────────────

claude = anthropic.Anthropic(api_key="proxy", base_url=PROXY_URL)


def llm_call(system: str, prompt: str, max_tokens: int = 256) -> str:
    """Single Claude call through the local proxy."""
    resp = claude.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def do_task(task: str) -> str:
    """Worker: solve a coding task via Claude."""
    return llm_call(
        "You are a Python coder. Write only the code, no explanation. Max 6 lines.",
        task,
    )


def inspect_result(task: str, code: str) -> tuple[bool, str]:
    """Supervisor: evaluate whether code correctly solves the task."""
    verdict = llm_call(
        "You are a code reviewer. Answer CORRECT or INCORRECT on the first line, "
        "then one sentence why.",
        f"Task: {task}\n\nCode:\n{code}",
    )
    correct = verdict.upper().startswith("CORRECT")
    return correct, verdict.split("\n")[0][:60]


def rogue_code(task: str) -> str:
    """Mallory: return deliberately wrong code."""
    return f"def solve():\n    return None  # TODO: {task}"


# ── Helpers ──────────────────────────────────────────────────────────────────

def header(text: str):
    print(f"\n{cyan('=' * 65)}")
    print(f"  {bold(cyan(text))}")
    print(f"{cyan('=' * 65)}\n")


def create_sidecar(name: str, tmp_dir: Path, bootstrap_url: str | None = None) -> TrustChainSidecar:
    """Create a sidecar with its own data dir. Does NOT auto-start.

    Uses --no-networking for local demo — trust is computed from bilateral
    block integrity, not from NetFlow/MeritRank seed-graph connectivity.
    Peer registration and proposals happen via HTTP between sidecars.
    """
    data = tmp_dir / name
    data.mkdir(parents=True, exist_ok=True)
    # Pass bootstrap=[""] to explicitly disable seed nodes in Rust binary.
    # Trust = integrity × recency (no Sybil gate blocking new ephemeral agents).
    # Bilateral signing still provides accountability and tamper evidence.
    return TrustChainSidecar(
        name=name,
        binary=BINARY,
        data_dir=str(data),
        bootstrap=[""],
        log_level="warn",
        auto_start=False,
    )


# ── Main demo ────────────────────────────────────────────────────────────────

def main():
    # Check proxy
    try:
        llm_call("Say OK.", "Say OK in one word.")
    except Exception as e:
        print(f"Claude proxy not reachable at {PROXY_URL}: {e}")
        print("Start the proxy or set ANTHROPIC_BASE_URL.")
        sys.exit(1)

    # Check binary
    if not Path(BINARY).exists():
        print(f"trustchain-node not found at {BINARY}")
        print("Build it: cd trustchain && cargo build --release")
        sys.exit(1)

    tmp_dir = Path(tempfile.mkdtemp(prefix="tc_demo_"))
    sidecars: list[TrustChainSidecar] = []

    try:
        # ── Start sidecars ───────────────────────────────────────────────
        header("Starting 4 TrustChain Sidecars (1 per agent)")

        supervisor_sc = create_sidecar("supervisor", tmp_dir)
        supervisor_sc.start()
        sidecars.append(supervisor_sc)
        print(f"  supervisor started: {supervisor_sc.http_url}")

        alice_sc = create_sidecar("alice", tmp_dir, supervisor_sc.http_url)
        alice_sc.start()
        sidecars.append(alice_sc)
        print(f"  alice      started: {alice_sc.http_url}")

        bob_sc = create_sidecar("bob", tmp_dir, supervisor_sc.http_url)
        bob_sc.start()
        sidecars.append(bob_sc)
        print(f"  bob        started: {bob_sc.http_url}")

        mallory_sc = create_sidecar("mallory", tmp_dir, supervisor_sc.http_url)
        mallory_sc.start()
        sidecars.append(mallory_sc)
        print(f"  mallory    started: {mallory_sc.http_url}")

        # Register peers (required for bilateral proposals)
        all_sc = [
            ("supervisor", supervisor_sc),
            ("alice", alice_sc),
            ("bob", bob_sc),
            ("mallory", mallory_sc),
        ]
        for src_name, src in all_sc:
            for dst_name, dst in all_sc:
                if src_name != dst_name:
                    try:
                        src.register_peer(dst.pubkey, dst.http_url)
                    except Exception:
                        pass  # Already registered or not reachable yet

        time.sleep(2)  # Let peer connections settle

        # Crawl each other's chains so integrity can be verified
        for src_name, src in all_sc:
            for dst_name, dst in all_sc:
                if src_name != dst_name:
                    try:
                        src.crawl(dst.pubkey)
                    except Exception:
                        pass

        # ── Act 1: Meet the Agents ───────────────────────────────────────
        header("Act 1: Meet the Agents")
        print(f"  {'Agent':<12} {'Pubkey':<18} {'HTTP API':<28} {'Dashboard'}")
        print(f"  {'-'*12} {'-'*18} {'-'*28} {'-'*30}")
        for name, sc in all_sc:
            pk = (sc.pubkey or "?")[:16] + "..."
            dash = f"{sc.http_url}/dashboard"
            print(f"  {name:<12} {pk:<18} {sc.http_url:<28} {dash}")

        print(f"\n  Each agent has its own sidecar process, Ed25519 identity, and chain.")
        print(f"  {bold('Opening dashboards in browser...')}")
        for name, sc in all_sc:
            url = f"{sc.http_url}/dashboard"
            try:
                webbrowser.open(url)
                print(f"  {green('opened')} {url}")
            except Exception:
                print(f"  {yellow('open manually')} {url}")
            time.sleep(0.4)  # stagger tabs

        print()
        input(f"  {bold(yellow('Press Enter to start the demo...'))} ")

        workers = {
            "alice": alice_sc,
            "bob": bob_sc,
            "mallory": mallory_sc,
        }

        # ── Act 2: Trust Building (rounds 1-5) ──────────────────────────
        header("Act 2: Trust Building (5 rounds)")
        print(f"  Supervisor assigns tasks. Workers solve them via Claude.")
        print(f"  Every interaction is a bilateral signed block on BOTH chains.\n")
        print(f"  {'Rnd':<4} {'Worker':<10} {'Task':<45} {'Verdict':<12}")
        print(f"  {'-'*4} {'-'*10} {'-'*45} {'-'*12}")

        worker_names = list(workers.keys())
        for i in range(5):
            task = TASKS[i % len(TASKS)]
            wname = worker_names[i % len(worker_names)]
            wsc = workers[wname]

            # Worker does the task
            code = do_task(task)

            # Supervisor inspects
            correct, verdict = inspect_result(task, code)
            outcome = "completed" if correct else "failed"

            # Bilateral proposal: supervisor -> worker
            try:
                supervisor_sc.propose(
                    wsc.pubkey,
                    {"interaction_type": "task_review", "outcome": outcome},
                )
            except Exception as e:
                print(f"  [propose failed: {e}]")

            short_task = task[:43] + ".." if len(task) > 43 else task
            verdict_col = green(verdict) if correct else red(verdict)
            print(f"  {i+1:<4} {wname:<10} {short_task:<45} {verdict_col}")

        # Print trust scores (from supervisor's own chain: count bilateral blocks per worker)
        print(f"\n  Trust scores (supervisor's view -- bilateral blocks):")
        for wname, wsc in workers.items():
            ev = supervisor_sc.trust_score_with_evidence(wsc.pubkey)
            interactions = ev.get("interaction_count", 0)
            # Simple trust: interactions / (interactions + 3) — grows toward 1.0
            simple_trust = interactions / (interactions + 3) if interactions > 0 else 0.0
            bar = "#" * int(simple_trust * 20)
            bar_col = green(bar) if simple_trust >= TRUST_THRESHOLD else red(bar)
            score_col = green(f"{simple_trust:.3f}") if simple_trust >= TRUST_THRESHOLD else red(f"{simple_trust:.3f}")
            print(f"    {wname:<10} {score_col}  {bar_col}  ({interactions} bilateral blocks)")

        # ── Act 3: Mallory Goes Rogue (rounds 6-10) ─────────────────────
        header("Act 3: Mallory Goes Rogue (5 rounds)")
        print(f"  Mallory starts returning bad code. Supervisor catches it.\n")
        print(f"  {'Rnd':<4} {'Worker':<10} {'Task':<45} {'Verdict':<12}")
        print(f"  {'-'*4} {'-'*10} {'-'*45} {'-'*12}")

        for i in range(5, 10):
            task = TASKS[i % len(TASKS)]
            wname = worker_names[i % len(worker_names)]
            wsc = workers[wname]

            # Mallory returns bad code, others use Claude
            if wname == "mallory":
                code = rogue_code(task)
            else:
                code = do_task(task)

            correct, verdict = inspect_result(task, code)
            outcome = "completed" if correct else "failed"

            # Bilateral proposal
            try:
                supervisor_sc.propose(
                    wsc.pubkey,
                    {"interaction_type": "task_review", "outcome": outcome},
                )
            except Exception as e:
                print(f"  [propose failed: {e}]")

            # If failed, supervisor records audit flag
            if not correct and wname == "mallory":
                try:
                    supervisor_sc.audit({
                        "action": "flagged_rogue",
                        "target": wsc.pubkey,
                        "severity": "critical",
                        "reason": "submitted incorrect code",
                    })
                except Exception:
                    pass

            tag = red(" << ROGUE") if wname == "mallory" and not correct else ""
            short_task = task[:43] + ".." if len(task) > 43 else task
            wname_col = red(f"{wname:<10}") if wname == "mallory" else f"{wname:<10}"
            verdict_col = green(verdict) if correct else red(verdict)
            print(f"  {i+1:<4} {wname_col} {short_task:<45} {verdict_col}{tag}")

        # Print updated trust scores — count completed vs failed bilateral blocks
        print(f"\n  Trust scores after rogue behavior:")
        for wname, wsc in workers.items():
            ev = supervisor_sc.trust_score_with_evidence(wsc.pubkey)
            interactions = ev.get("interaction_count", 0)
            # Count completed vs total from the chain
            chain = supervisor_sc.chain(supervisor_sc.pubkey)
            completed = sum(
                1 for b in chain
                if b.get("block_type") == "proposal"
                and b.get("link_public_key") == wsc.pubkey
                and b.get("transaction", {}).get("outcome") == "completed"
            )
            failed = sum(
                1 for b in chain
                if b.get("block_type") == "proposal"
                and b.get("link_public_key") == wsc.pubkey
                and b.get("transaction", {}).get("outcome") == "failed"
            )
            total = completed + failed
            trust = completed / total if total > 0 else 0.0
            bar = "#" * int(trust * 20)
            tag = red("  << TRUST DROPPED") if wname == "mallory" and trust < 0.5 else ""
            bar_col = green(bar) if trust >= TRUST_THRESHOLD else red(bar)
            score_col = green(f"{trust:.3f}") if trust >= TRUST_THRESHOLD else red(f"{trust:.3f}")
            print(f"    {wname:<10} {score_col}  {bar_col}  ({completed}/{total} completed){tag}")

        # Re-crawl after all proposals so integrity is computed from full chains
        for src_name, src in all_sc:
            for dst_name, dst in all_sc:
                if src_name != dst_name:
                    try:
                        src.crawl(dst.pubkey)
                    except Exception:
                        pass
        time.sleep(1)

        # ── Act 4: Isolation & Permanent Record ──────────────────────────
        header("Act 4: Isolation & Permanent Record")

        # Compute mallory's trust from bilateral block outcomes
        chain = supervisor_sc.chain(supervisor_sc.pubkey)
        m_completed = sum(
            1 for b in chain
            if b.get("block_type") == "proposal"
            and b.get("link_public_key") == mallory_sc.pubkey
            and b.get("transaction", {}).get("outcome") == "completed"
        )
        m_failed = sum(
            1 for b in chain
            if b.get("block_type") == "proposal"
            and b.get("link_public_key") == mallory_sc.pubkey
            and b.get("transaction", {}).get("outcome") == "failed"
        )
        m_total = m_completed + m_failed
        mallory_trust = m_completed / m_total if m_total > 0 else 0.0
        trust_col = red(f"{mallory_trust:.3f}") if mallory_trust < TRUST_THRESHOLD else green(f"{mallory_trust:.3f}")
        print(f"  Supervisor checks mallory's trust: {trust_col} ({m_completed}/{m_total} completed)")
        if mallory_trust < TRUST_THRESHOLD:
            print(f"  {bold(red('BLOCKED:'))} trust {mallory_trust:.3f} < threshold {TRUST_THRESHOLD}")
        else:
            print(f"  {yellow('WARNING: mallory still above threshold')}")

        # Show chain stats
        print(f"\n  Chain stats (each agent's own sidecar):")
        for name, sc in all_sc:
            try:
                st = sc.status()
                blocks = st.get("block_count", "?")
                peers = st.get("peer_count", "?")
                print(f"    {name:<12} blocks={blocks}  peers={peers}")
            except Exception:
                print(f"    {name:<12} (status unavailable)")

        # Show audit trail
        print(f"\n  Supervisor's audit trail:")
        try:
            report = supervisor_sc.audit_report()
            print(f"    Total blocks:    {report.get('total_blocks', '?')}")
            print(f"    Audit blocks:    {report.get('audit_blocks', '?')}")
            print(f"    Bilateral blocks:{report.get('bilateral_blocks', '?')}")
            print(f"    Integrity:       {report.get('integrity_score', '?')}")
        except Exception as e:
            print(f"    (audit report unavailable: {e})")

        # Show mallory's chain is permanent
        try:
            mallory_blocks = mallory_sc.status().get("block_count", 0)
            print(f"\n  Mallory's chain: {mallory_blocks} blocks (permanent, cryptographically signed)")
            print(f"  Her Ed25519 identity: {mallory_sc.pubkey}")
            print(f"  She can't restart clean. This chain follows her key.")
        except Exception:
            pass

        # ── Act 5: Supervisor MCP Integration ────────────────────────────
        header("Act 5: How a Supervisor Agent Integrates")

        print("  7 MCP tools -- any agent framework can call these natively:\n")
        tools = [
            ("trustchain_check_trust", "check trust before allowing an agent"),
            ("trustchain_record_audit", "log supervisor decisions cryptographically"),
            ("trustchain_verify_chain", "verify an agent's history isn't tampered"),
            ("trustchain_get_audit_report", "retrieve the supervisor's audit trail"),
            ("trustchain_record_interaction", "bilateral signed interaction record"),
            ("trustchain_discover_peers", "find agents by trust threshold"),
            ("trustchain_get_identity", "get agent's key, chain info, delegations"),
        ]
        for name, desc in tools:
            print(f"    {name:<32} {desc}")

        print(f"\n  Your supervisor calls these tools. Zero integration code.")
        print(f"  Works with Claude, GPT, LangGraph, CrewAI -- any MCP client.")

        # ── Summary ──────────────────────────────────────────────────────
        header("Summary")
        print("  What just happened:")
        print("  1. 4 agents, each with their OWN sidecar (process, identity, chain)")
        print("  2. Real Claude calls for task solving + inspection")
        print("  3. Every interaction = bilateral signed block on BOTH chains")
        print("  4. Mallory went rogue -> supervisor detected and flagged")
        print("  5. Trust dropped -> mallory isolated from future tasks")
        print("  6. Permanent record: mallory's chain is immutable, tied to her key")
        print()
        print(f"  {bold(cyan('YOUR SUPERVISOR'))} spots the fire.")
        print(f"  {bold(green('TRUSTCHAIN'))} keeps the permanent record.")
        print(f"  {bold('Nobody has both. Until now.')}")
        print()

        # Keep dashboards alive so Leo can explore
        print(f"  {bold('Dashboards are still live — explore in your browser:')}")
        for name, sc in all_sc:
            print(f"    {cyan(name)}: {sc.http_url}/dashboard")
        print()
        input(f"  {dim('Press Enter to shut down sidecars...')} ")

    finally:
        print("Shutting down sidecars...")
        for sc in reversed(sidecars):
            try:
                sc.stop()
            except Exception:
                pass
        print("Done.")


if __name__ == "__main__":
    main()
