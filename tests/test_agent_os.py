"""Unit tests for the TrustChain Agent OS SDK."""

import pytest

from trustchain.store import RecordStore
from trustchain.trust import compute_trust

from agent_os.agent import TrustAgent
from agent_os.context import TrustContext
from agent_os.decorators import TrustGateError, record_interaction, trust_gate


class TestTrustContext:
    def test_create_context(self, identity_a, identity_b, store):
        ctx = TrustContext.create(
            caller_pubkey=identity_a.pubkey_hex,
            agent_identity=identity_b,
            store=store,
        )
        assert ctx.caller_pubkey == identity_a.pubkey_hex
        assert ctx.caller_trust == 0.0
        assert ctx.caller_history == 0
        assert ctx.is_bootstrap
        assert not ctx.is_trusted

    def test_context_with_history(self, identity_a, identity_b, populated_store):
        ctx = TrustContext.create(
            caller_pubkey=identity_a.pubkey_hex,
            agent_identity=identity_b,
            store=populated_store,
        )
        assert ctx.caller_trust > 0.0
        assert ctx.caller_history == 5
        assert not ctx.is_bootstrap
        assert ctx.is_trusted


class TestTrustGateDecorator:
    @pytest.mark.asyncio
    async def test_gate_allows_trusted_caller(self, identity_a, identity_b, populated_store):
        @trust_gate(min_trust=0.1)
        async def handler(ctx: TrustContext) -> str:
            return "ok"

        ctx = TrustContext.create(identity_a.pubkey_hex, identity_b, populated_store)
        result = await handler(ctx)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_gate_blocks_untrusted_caller(self, identity_a, identity_b, store):
        @trust_gate(min_trust=0.5, allow_bootstrap=False)
        async def handler(ctx: TrustContext) -> str:
            return "ok"

        ctx = TrustContext.create(identity_a.pubkey_hex, identity_b, store)
        with pytest.raises(TrustGateError, match="Trust gate denied"):
            await handler(ctx)

    @pytest.mark.asyncio
    async def test_gate_allows_bootstrap(self, identity_a, identity_b, store):
        @trust_gate(min_trust=0.5, allow_bootstrap=True)
        async def handler(ctx: TrustContext) -> str:
            return "ok"

        ctx = TrustContext.create(identity_a.pubkey_hex, identity_b, store)
        # Bootstrap mode: caller has 0 interactions, allow_bootstrap=True
        result = await handler(ctx)
        assert result == "ok"


class TestRecordInteractionDecorator:
    @pytest.mark.asyncio
    async def test_records_successful_interaction(self, identity_a, identity_b, store):
        @record_interaction(interaction_type="compute")
        async def handler(ctx: TrustContext) -> str:
            return "done"

        ctx = TrustContext.create(identity_a.pubkey_hex, identity_b, store)
        result = await handler(ctx)
        assert result == "done"
        assert len(store.records) == 1

    @pytest.mark.asyncio
    async def test_records_failed_interaction(self, identity_a, identity_b, store):
        @record_interaction(interaction_type="compute")
        async def handler(ctx: TrustContext) -> str:
            raise ValueError("bad input")

        ctx = TrustContext.create(identity_a.pubkey_hex, identity_b, store)
        with pytest.raises(ValueError):
            await handler(ctx)
        assert len(store.records) == 1
        assert store.records[0].outcome == "failed"


class TestTrustAgent:
    def test_agent_creation(self):
        agent = TrustAgent(name="alice")
        assert agent.name == "alice"
        assert len(agent.pubkey) == 64  # hex of 32 bytes
        assert agent.trust_score == 0.0
        assert agent.interaction_count == 0

    def test_agent_shared_store(self):
        store = RecordStore()
        alice = TrustAgent(name="alice", store=store)
        bob = TrustAgent(name="bob", store=store)
        assert alice.store is bob.store

    def test_service_registration(self):
        agent = TrustAgent(name="alice")

        @agent.service("compute", min_trust=0.3)
        async def run_compute(data: dict, ctx: TrustContext) -> dict:
            return {"result": 42}

        assert "compute" in agent._services
        assert agent._services["compute"].min_trust == 0.3

    @pytest.mark.asyncio
    async def test_call_service_accepted(self):
        store = RecordStore()
        alice = TrustAgent(name="alice", store=store)
        bob = TrustAgent(name="bob", store=store)

        @bob.service("echo", min_trust=0.0)
        async def echo(data: dict, ctx: TrustContext) -> dict:
            return {"echo": data}

        accepted, reason, result = await alice.call_service(bob, "echo", {"msg": "hi"})
        assert accepted
        assert result == {"echo": {"msg": "hi"}}
        assert alice.interaction_count == 1
        assert bob.interaction_count == 1

    @pytest.mark.asyncio
    async def test_call_service_denied_low_trust(self):
        store = RecordStore()
        alice = TrustAgent(name="alice", store=store, bootstrap_interactions=0)
        bob = TrustAgent(name="bob", store=store, bootstrap_interactions=0)

        @bob.service("premium", min_trust=0.9)
        async def premium(data: dict, ctx: TrustContext) -> dict:
            return {"premium": True}

        accepted, reason, result = await alice.call_service(bob, "premium")
        assert not accepted
        assert "Trust gate denied" in reason or "denied" in reason.lower()

    @pytest.mark.asyncio
    async def test_trust_builds_over_interactions(self):
        store = RecordStore()
        alice = TrustAgent(name="alice", store=store)
        bob = TrustAgent(name="bob", store=store)

        @bob.service("basic", min_trust=0.0)
        async def basic(data: dict, ctx: TrustContext) -> dict:
            return {"ok": True}

        for _ in range(5):
            accepted, _, _ = await alice.call_service(bob, "basic")
            assert accepted

        assert alice.trust_score > 0.0
        assert bob.trust_score > 0.0

    @pytest.mark.asyncio
    async def test_unknown_service_denied(self):
        store = RecordStore()
        alice = TrustAgent(name="alice", store=store)
        bob = TrustAgent(name="bob", store=store)

        accepted, reason, result = await alice.call_service(bob, "nonexistent")
        assert not accepted
        assert "Unknown service" in reason

    def test_would_accept_bootstrap(self):
        store = RecordStore()
        agent = TrustAgent(name="test", store=store, bootstrap_interactions=3)
        other = TrustAgent(name="other", store=store)
        assert agent.would_accept(other.pubkey)

    def test_as_mcp_server(self):
        agent = TrustAgent(name="alice")

        @agent.service("compute", min_trust=0.3)
        async def compute(data: dict, ctx: TrustContext) -> dict:
            return {"result": 42}

        mcp = agent.as_mcp_server()
        assert mcp is not None

    def test_agent_repr(self):
        agent = TrustAgent(name="alice")
        r = repr(agent)
        assert "alice" in r
        assert "trust=" in r
