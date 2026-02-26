"""TrustChain middleware — trust-gates every tool call through the gateway.

v2: Supports both legacy RecordStore-based trust and new TrustEngine-based trust.
When a GatewayNode is available, creates proper half-block transactions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.exceptions import ToolError

from trustchain.trust import compute_trust

if TYPE_CHECKING:
    from gateway.node import GatewayNode
    from gateway.recorder import InteractionRecorder
    from gateway.registry import UpstreamRegistry
    from trustchain.store import RecordStore
    from trustchain.trust import TrustEngine

logger = logging.getLogger("trustchain.gateway")


class TrustChainMiddleware(Middleware):
    """MCP middleware that trust-verifies every tool call.

    For each tool invocation:
    1. Identify the upstream server from the tool name
    2. Compute trust score for that upstream
    3. If trust < threshold AND enough history -> BLOCK
    4. Otherwise -> forward the call
    5. Record bilateral interaction (v2 half-block or v1 record)
    6. Append trust annotation to result

    v2: When trust_engine is provided, uses TrustEngine for scoring.
    When gateway_node is provided, creates proper half-block transactions.
    """

    # Tool names registered by the gateway itself (trust query tools)
    NATIVE_TOOL_PREFIXES = ("trustchain_",)

    def __init__(
        self,
        registry: UpstreamRegistry,
        recorder: InteractionRecorder,
        store: RecordStore,
        default_threshold: float = 0.0,
        bootstrap_interactions: int = 3,
        trust_engine: Optional[TrustEngine] = None,
        gateway_node: Optional[GatewayNode] = None,
    ):
        self.registry = registry
        self.recorder = recorder
        self.store = store
        self.default_threshold = default_threshold
        self.bootstrap_interactions = bootstrap_interactions
        self.trust_engine = trust_engine
        self.gateway_node = gateway_node

    def _is_native_tool(self, tool_name: str) -> bool:
        """Check if a tool is a gateway-native trust tool (skip gating)."""
        return any(tool_name.startswith(p) for p in self.NATIVE_TOOL_PREFIXES)

    def _compute_trust(self, pubkey: str) -> float:
        """Compute trust using v2 TrustEngine if available, else v1."""
        if self.trust_engine:
            return self.trust_engine.compute_trust(pubkey)
        return compute_trust(pubkey, self.store)

    def _get_interaction_count(self, pubkey: str) -> int:
        """Get interaction count for a peer.

        v2: Uses our own chain's interactions with this peer (blocks where
        link_public_key == pubkey).
        v1: Counts records involving this pubkey.
        """
        if self.gateway_node:
            # Count blocks on our chain that link to this peer
            our_chain = self.gateway_node.store.get_chain(self.gateway_node.pubkey)
            return sum(1 for b in our_chain if b.link_public_key == pubkey)
        return len(self.store.get_records_for(pubkey))

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """Intercept tool calls for trust gating and recording."""
        tool_name = context.message.name
        args = context.message.arguments or {}

        # Native trust tools bypass the gate
        if self._is_native_tool(tool_name):
            return await call_next(context)

        # Identify which upstream server owns this tool
        server_name = self.registry.server_for_tool(tool_name)
        if server_name is None:
            logger.warning("Tool '%s' not mapped to any upstream server", tool_name)
            return await call_next(context)

        upstream_identity = self.registry.identity_for(server_name)
        if upstream_identity is None:
            raise ToolError(f"No identity registered for upstream server '{server_name}'")

        upstream_pubkey = upstream_identity.pubkey_hex

        # Compute trust score
        trust_score = self._compute_trust(upstream_pubkey)
        threshold = self.registry.threshold_for(server_name, self.default_threshold)
        interaction_count = self._get_interaction_count(upstream_pubkey)

        # Bootstrap logic
        is_bootstrap = interaction_count < self.bootstrap_interactions

        if not is_bootstrap and trust_score < threshold:
            raise ToolError(
                f"[TrustChain] BLOCKED: server={server_name} "
                f"trust={trust_score:.3f} < threshold={threshold:.3f} "
                f"(interactions={interaction_count})"
            )

        # Forward the tool call
        outcome = "completed"
        try:
            result = await call_next(context)
        except Exception as exc:
            outcome = "failed"
            self._record_interaction(
                upstream_identity=upstream_identity,
                upstream_pubkey=upstream_pubkey,
                tool_name=tool_name,
                outcome=outcome,
            )
            raise

        # Record the successful interaction
        self._record_interaction(
            upstream_identity=upstream_identity,
            upstream_pubkey=upstream_pubkey,
            tool_name=tool_name,
            outcome=outcome,
        )

        # Re-compute trust after this interaction
        updated_trust = self._compute_trust(upstream_pubkey)

        # Append trust annotation to the result
        annotation = (
            f"\n\n[TrustChain] server={server_name} "
            f"trust={updated_trust:.3f} outcome={outcome}"
        )
        result = _append_to_result(result, annotation)

        logger.info(
            "Tool '%s' on server '%s': trust=%.3f outcome=%s",
            tool_name, server_name, updated_trust, outcome,
        )

        return result

    def _record_interaction(
        self,
        upstream_identity,
        upstream_pubkey: str,
        tool_name: str,
        outcome: str,
    ) -> None:
        """Record the interaction using v2 half-blocks or v1 records."""
        if self.gateway_node:
            # v2: Create proper half-block proposal/agreement
            transaction = {
                "interaction_type": f"tool:{tool_name}",
                "outcome": outcome,
            }
            try:
                proposal = self.gateway_node.protocol.create_proposal(
                    upstream_pubkey, transaction
                )
                # If the peer is registered, we could send via HTTP, but for
                # local recording we just create the proposal on our chain.
                logger.debug(
                    "Recorded v2 half-block for tool:%s -> %s",
                    tool_name,
                    upstream_pubkey[:16],
                )
            except Exception as e:
                logger.warning("Failed to create v2 half-block: %s", e)
        else:
            # v1: Use the InteractionRecorder
            self.recorder.record(
                upstream_identity=upstream_identity,
                interaction_type=f"tool:{tool_name}",
                outcome=outcome,
            )


def _append_to_result(result, annotation: str):
    """Append a trust annotation to a tool call result."""
    if isinstance(result, list):
        from mcp.types import TextContent
        result.append(TextContent(type="text", text=annotation))
        return result
    elif isinstance(result, str):
        return result + annotation
    else:
        return result
