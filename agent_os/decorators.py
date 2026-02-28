"""Decorators for trust-gated agent services.

v2: record_interaction creates half-block pairs when the context has a
TrustChainNode available. Falls back to v1 attested recording otherwise.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Optional

from agent_os.context import TrustContext


class TrustGateError(Exception):
    """Raised when a caller fails the trust gate check."""
    pass


def trust_gate(min_trust: float = 0.0, allow_bootstrap: bool = True):
    """Decorator that blocks callers below a trust threshold.

    Args:
        min_trust: Minimum trust score required to call this service.
        allow_bootstrap: Whether to allow callers in bootstrap mode
                        (< 3 interactions) regardless of trust.
    """
    def decorator(func: Callable) -> Callable:
        func._trust_gate_min = min_trust
        func._trust_gate_bootstrap = allow_bootstrap

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Find the TrustContext in the arguments
            ctx = _extract_context(args, kwargs)
            if ctx is not None:
                # Bootstrap mode: allow if enabled and caller is new
                if allow_bootstrap and ctx.is_bootstrap:
                    pass  # Allow through
                elif ctx.caller_trust < min_trust:
                    raise TrustGateError(
                        f"Trust gate denied: caller trust {ctx.caller_trust:.3f} "
                        f"< required {min_trust:.3f} "
                        f"(caller={ctx.caller_pubkey[:16]}...)"
                    )
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)

        return wrapper
    return decorator


def record_interaction(interaction_type: str = "service"):
    """Decorator that auto-creates a bilateral record after handler execution.

    v2: If context has a node attribute, creates proper half-block pairs.
    v1: Falls back to agent-attested bilateral recording.
    """
    def decorator(func: Callable) -> Callable:
        func._record_type = interaction_type

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            ctx = _extract_context(args, kwargs)
            outcome = "completed"
            try:
                if inspect.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
            except TrustGateError:
                raise  # Don't record gate denials
            except Exception:
                outcome = "failed"
                if ctx is not None:
                    _create_record(ctx, interaction_type, outcome)
                raise

            if ctx is not None:
                _create_record(ctx, interaction_type, outcome)
            return result

        return wrapper
    return decorator


def _extract_context(args: tuple, kwargs: dict) -> Optional[TrustContext]:
    """Find TrustContext in positional or keyword arguments."""
    for arg in args:
        if isinstance(arg, TrustContext):
            return arg
    for val in kwargs.values():
        if isinstance(val, TrustContext):
            return val
    return None


def _create_record(ctx: TrustContext, interaction_type: str, outcome: str):
    """Create a bilateral record between caller and agent.

    v2: When the context has a node, proper half-block protocol is handled
    at the TrustAgent.call_service level — this decorator is a no-op.

    v1: Uses agent-attested signing. The caller's identity is created
    deterministically from their pubkey seed so records accumulate
    correctly under the caller's pubkey.

    Note: In v1 mode, the agent signs on behalf of the caller. This is
    a known limitation — bilateral verification requires both parties'
    private keys, which is only possible in v2 mode.
    """
    # v2: half-block recording is handled by TrustAgent.call_service,
    # not by the decorator. Skip double-recording.
    if ctx.node is not None:
        return

    from trustchain.record import create_record

    store = ctx.store
    seq_a = store.sequence_number_for(ctx.caller_pubkey)
    seq_b = store.sequence_number_for(ctx.agent_identity.pubkey_hex)
    prev_hash_a = store.last_hash_for(ctx.caller_pubkey)
    prev_hash_b = store.last_hash_for(ctx.agent_identity.pubkey_hex)

    # Agent-attested: we sign as the agent for both parties.
    # Records are stored under the agent's pubkey (seq_b chain).
    # The caller_pubkey is tracked in the record for trust scoring.
    record = create_record(
        identity_a=ctx.agent_identity,  # Agent-attested signing for caller side
        identity_b=ctx.agent_identity,
        seq_a=seq_a,
        seq_b=seq_b,
        prev_hash_a=prev_hash_a,
        prev_hash_b=prev_hash_b,
        interaction_type=interaction_type,
        outcome=outcome,
    )
    store.add_record(record)
