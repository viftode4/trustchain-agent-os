"""TrustAgent — trust-native agent that runs on TrustChain.

v2: Wraps a TrustChainNode for proper half-block protocol, while keeping
v1 RecordStore-based call_service as a compat path.
"""

from __future__ import annotations

import inspect
import logging
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from fastmcp import FastMCP

from trustchain.identity import Identity
from trustchain.record import create_record, verify_record
from trustchain.store import FileRecordStore, RecordStore
from trustchain.trust import compute_trust

from agent_os.context import TrustContext
from agent_os.decorators import TrustGateError

logger = logging.getLogger("trustchain.agent")


SERVICE_TIERS = {
    "basic": 0.0,
    "compute": 0.3,
    "data": 0.3,
    "code_review": 0.6,
}


@dataclass
class _ServiceRegistration:
    """Internal tracking for a registered service."""
    name: str
    handler: Callable
    min_trust: float
    interaction_type: str


class TrustAgent:
    """A trust-native agent that builds reputation through bilateral interactions.

    v2: When node is provided, uses TrustChainNode for proper half-block
    protocol with HTTPS transport. When node is None, falls back to v1
    RecordStore-based bilateral recording.

    Usage:
        agent = TrustAgent(name="my-agent")

        @agent.service("compute", min_trust=0.3)
        async def run_compute(data: dict, ctx: TrustContext) -> dict:
            return {"result": process(data)}

        # Run as MCP server
        agent.as_mcp_server().run()

        # Or call another agent's service
        result = await agent.call_service(other_agent, "compute", {"x": 1})
    """

    def __init__(
        self,
        name: str,
        store: Optional[RecordStore] = None,
        identity_path: Optional[str] = None,
        store_path: Optional[str] = None,
        min_trust_threshold: float = 0.15,
        bootstrap_interactions: int = 3,
        node=None,  # Optional[TrustChainNode]
    ):
        self.name = name
        self.min_trust_threshold = min_trust_threshold
        self.bootstrap_interactions = bootstrap_interactions
        self._services: Dict[str, _ServiceRegistration] = {}
        self._node = node  # v2 TrustChainNode (optional)

        # Identity: load or create
        if node:
            self.identity = node.identity
        elif identity_path:
            path = Path(identity_path)
            if path.exists():
                self.identity = Identity.load(str(path))
            else:
                self.identity = Identity()
                path.parent.mkdir(parents=True, exist_ok=True)
                self.identity.save(str(path))
        else:
            self.identity = Identity()

        # Store: file-backed or in-memory (v1 compat)
        if store is not None:
            self.store = store
        elif store_path:
            self.store = FileRecordStore(store_path)
        else:
            self.store = RecordStore()

        # v2: cached TrustEngine instance
        self._trust_engine = None
        if self._node:
            from trustchain.trust import TrustEngine
            self._trust_engine = TrustEngine(
                self._node.store,
                seed_nodes=[self.pubkey],
            )

    @property
    def node(self):
        """The v2 TrustChainNode, if configured."""
        return self._node

    @property
    def pubkey(self) -> str:
        return self.identity.pubkey_hex

    @property
    def short_id(self) -> str:
        return self.identity.short_id

    @property
    def trust_score(self) -> float:
        if self._trust_engine:
            return self._trust_engine.compute_trust(self.pubkey)
        return compute_trust(self.pubkey, self.store)

    @property
    def interaction_count(self) -> int:
        if self._node:
            return self._node.store.get_latest_seq(self.pubkey)
        return len(self.store.get_records_for(self.pubkey))

    def check_trust(self, pubkey: str) -> float:
        """Get the trust score of another agent."""
        if self._trust_engine:
            return self._trust_engine.compute_trust(pubkey)
        return compute_trust(pubkey, self.store)

    def chain_integrity(self) -> float:
        """Compute this agent's personal chain integrity score."""
        if self._node:
            return self._node.protocol.integrity_score(self.pubkey)

        from trustchain.chain import compute_chain_integrity
        records = self.store.get_records_for(self.pubkey)
        if not records:
            return 1.0
        return compute_chain_integrity(self.pubkey, records)

    def would_accept(self, other_pubkey: str, min_trust: Optional[float] = None) -> bool:
        """Would this agent accept an interaction from other?"""
        threshold = min_trust if min_trust is not None else self.min_trust_threshold
        other_trust = self.check_trust(other_pubkey)
        # Check if the caller is in bootstrap mode (few interactions)
        if self._node:
            caller_history = self._node.store.get_latest_seq(other_pubkey)
        else:
            caller_history = len(self.store.get_records_for(other_pubkey))
        if caller_history < self.bootstrap_interactions:
            return True
        return other_trust >= threshold

    # ---- Service Registration ----

    def service(
        self,
        name: str,
        min_trust: float = 0.0,
        interaction_type: Optional[str] = None,
    ) -> Callable:
        """Decorator to register a service on this agent."""
        if interaction_type is None:
            interaction_type = name

        def decorator(func: Callable) -> Callable:
            self._services[name] = _ServiceRegistration(
                name=name,
                handler=func,
                min_trust=min_trust,
                interaction_type=interaction_type,
            )
            return func

        return decorator

    async def handle_service_call(
        self,
        service_name: str,
        data: Dict[str, Any],
        caller_pubkey: str,
    ) -> Tuple[bool, str, Any]:
        """Handle an incoming service call with trust gating.

        Returns (accepted, reason, result).
        """
        reg = self._services.get(service_name)
        if reg is None:
            return False, f"Unknown service: {service_name}", None

        # Trust gate
        caller_trust = self.check_trust(caller_pubkey)
        if self._node:
            caller_history = self._node.store.get_latest_seq(caller_pubkey)
        else:
            caller_history = len(self.store.get_records_for(caller_pubkey))
        is_bootstrap = caller_history < self.bootstrap_interactions

        if not is_bootstrap and caller_trust < reg.min_trust:
            return (
                False,
                f"Trust gate denied for '{service_name}': "
                f"trust {caller_trust:.3f} < {reg.min_trust:.3f}",
                None,
            )

        # Create context
        ctx = TrustContext(
            caller_pubkey=caller_pubkey,
            caller_trust=caller_trust,
            caller_history=caller_history,
            agent_identity=self.identity,
            store=self.store,
        )

        # Execute handler
        try:
            if inspect.iscoroutinefunction(reg.handler):
                result = await reg.handler(data, ctx)
            else:
                result = reg.handler(data, ctx)
            outcome = "completed"
        except TrustGateError as e:
            return False, str(e), None
        except Exception as e:
            outcome = "failed"
            logger.error("Service '%s' failed: %s", service_name, e)
            result = None

        return True, f"{service_name} {outcome}", result

    # ---- Calling Other Agents ----

    async def call_service(
        self,
        provider: TrustAgent,
        service_name: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Any]:
        """Call a service on another TrustAgent with bilateral recording.

        v2: If both agents have nodes, uses proper half-block protocol.
        v1: Falls back to RecordStore-based bilateral recording.

        Returns (accepted, reason, result).
        """
        if data is None:
            data = {}

        accepted, reason, result = await provider.handle_service_call(
            service_name=service_name,
            data=data,
            caller_pubkey=self.pubkey,
        )

        # outcome=failed if: trust gate denied, OR handler raised an exception
        outcome = "completed" if (accepted and "failed" not in reason) else "failed"

        # v2 path: proper half-block protocol
        # Only create proposal/agreement when the call was accepted.
        # Creating a proposal for denied calls would leave orphan blocks.
        #
        # Trust recording is infrastructure — if the protocol layer fails,
        # the agent interaction result is still returned. We log the error
        # but never let trust machinery break the agent-to-agent call.
        if self._node and provider._node:
            if accepted:
                try:
                    transaction = {
                        "interaction_type": service_name,
                        "outcome": outcome,
                        "timestamp": int(_time.time() * 1000),
                    }
                    proposal = self._node.protocol.create_proposal(
                        provider.pubkey, transaction
                    )
                    provider._node.protocol.receive_proposal(proposal)
                    agreement = provider._node.protocol.create_agreement(proposal)
                    self._node.protocol.receive_agreement(agreement)
                except Exception as e:
                    logger.error(
                        "v2 trust recording failed for %s -> %s (%s): %s",
                        self.name, provider.name, service_name, e,
                    )
            else:
                # Denied calls: record as a lightweight v1 record so the
                # trust engine can see repeated failed attempts (useful for
                # detecting malicious callers). No half-block is created —
                # that would leave orphan proposals on the chain.
                try:
                    seq_a = self.store.sequence_number_for(self.pubkey)
                    seq_b = self.store.sequence_number_for(provider.pubkey)
                    prev_hash_a = self.store.last_hash_for(self.pubkey)
                    prev_hash_b = self.store.last_hash_for(provider.pubkey)
                    record = create_record(
                        identity_a=self.identity,
                        identity_b=provider.identity,
                        seq_a=seq_a, seq_b=seq_b,
                        prev_hash_a=prev_hash_a, prev_hash_b=prev_hash_b,
                        interaction_type=service_name,
                        outcome="denied",
                    )
                    if verify_record(record):
                        self.store.add_record(record)
                except Exception as e:
                    logger.debug("Failed to record denied v2 attempt: %s", e)
            return accepted, reason, result

        # v1 compat path: bilateral record
        seq_a = self.store.sequence_number_for(self.pubkey)
        seq_b = self.store.sequence_number_for(provider.pubkey)
        prev_hash_a = self.store.last_hash_for(self.pubkey)
        prev_hash_b = self.store.last_hash_for(provider.pubkey)

        record = create_record(
            identity_a=self.identity,
            identity_b=provider.identity,
            seq_a=seq_a,
            seq_b=seq_b,
            prev_hash_a=prev_hash_a,
            prev_hash_b=prev_hash_b,
            interaction_type=service_name,
            outcome=outcome,
        )

        if verify_record(record):
            self.store.add_record(record)
            if provider.store is not self.store:
                provider.store.add_record(record)
        else:
            logger.error("Signature verification failed for record")

        return accepted, reason, result

    # ---- MCP Server Export ----

    def as_mcp_server(self, name: Optional[str] = None) -> FastMCP:
        """Export this agent's services as a FastMCP server."""
        server_name = name or f"TrustAgent:{self.name}"
        mcp = FastMCP(server_name)
        agent = self

        for svc_name, reg in self._services.items():
            _reg = reg

            @mcp.tool(name=svc_name, description=f"[min_trust={_reg.min_trust}] {_reg.handler.__doc__ or svc_name}")
            async def _tool_handler(
                data: Optional[dict] = None,
                caller_pubkey: str = "",
                _svc=svc_name,
            ) -> str:
                data = data or {}
                if not caller_pubkey:
                    caller_pubkey = "anonymous"
                accepted, reason, result = await agent.handle_service_call(
                    _svc, data, caller_pubkey
                )
                if not accepted:
                    return f"DENIED: {reason}"
                return f"OK: {reason}\nResult: {result}"

        @mcp.tool(name="trustchain_agent_info")
        async def agent_info() -> str:
            """Get this agent's TrustChain identity and trust info."""
            return (
                f"Agent: {agent.name}\n"
                f"Public Key: {agent.pubkey[:16]}...\n"
                f"Trust Score: {agent.trust_score:.3f}\n"
                f"Interactions: {agent.interaction_count}\n"
                f"Chain Integrity: {agent.chain_integrity():.3f}\n"
                f"Services: {', '.join(agent._services.keys())}"
            )

        return mcp

    def __repr__(self) -> str:
        mode = "v2" if self._node else "v1"
        return (
            f"TrustAgent(name={self.name!r}, pubkey={self.short_id}..., "
            f"trust={self.trust_score:.3f}, mode={mode}, "
            f"services={list(self._services.keys())})"
        )
