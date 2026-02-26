"""TrustService — base class for trust-gated agent services."""

from __future__ import annotations

from typing import Any, Dict

from agent_os.context import TrustContext


class TrustService:
    """Base class for services that agents can expose.

    Subclass this and implement handle() to create a trust-gated service.
    The TrustAgent will wire up trust checks and recording automatically.
    """

    name: str = "service"
    min_trust: float = 0.0
    interaction_type: str = "service"

    async def handle(self, data: Dict[str, Any], ctx: TrustContext) -> Dict[str, Any]:
        """Handle a service request. Override in subclasses."""
        raise NotImplementedError

    async def __call__(self, data: Dict[str, Any], ctx: TrustContext) -> Dict[str, Any]:
        return await self.handle(data, ctx)
