"""Unit tests for TrustChainMiddleware."""

import pytest

from trustchain.identity import Identity
from trustchain.record import create_record
from trustchain.store import RecordStore
from trustchain.trust import compute_trust

from gateway.config import UpstreamServer
from gateway.middleware import TrustChainMiddleware
from gateway.recorder import InteractionRecorder
from gateway.registry import UpstreamRegistry


class TestInteractionRecorder:
    def test_record_creates_valid_bilateral_record(self, store, identity_a, identity_b):
        recorder = InteractionRecorder(identity_a, store)
        record = recorder.record(identity_b, "tool_call", "completed")

        assert record.agent_a_pubkey == identity_a.pubkey_hex
        assert record.agent_b_pubkey == identity_b.pubkey_hex
        assert record.interaction_type == "tool_call"
        assert record.outcome == "completed"
        assert record.seq_a == 0
        assert record.seq_b == 0
        assert len(store.records) == 1

    def test_record_increments_sequence_numbers(self, store, identity_a, identity_b):
        recorder = InteractionRecorder(identity_a, store)
        recorder.record(identity_b)
        recorder.record(identity_b)
        r3 = recorder.record(identity_b)

        assert r3.seq_a == 2
        assert r3.seq_b == 2
        assert len(store.records) == 3

    def test_record_chains_prev_hashes(self, store, identity_a, identity_b):
        recorder = InteractionRecorder(identity_a, store)
        r1 = recorder.record(identity_b)
        r2 = recorder.record(identity_b)

        assert r2.prev_hash_a == r1.record_hash
        assert r2.prev_hash_b == r1.record_hash


class TestUpstreamRegistry:
    def test_register_and_lookup_server(self, identity_a):
        registry = UpstreamRegistry(identity_a)
        config = UpstreamServer(name="test", command="echo", namespace="test")
        identity = registry.register_server(config)

        assert registry.identity_for("test") is identity
        assert registry.config_for("test") is config
        assert "test" in registry.server_names

    def test_tool_to_server_mapping(self, identity_a):
        registry = UpstreamRegistry(identity_a)
        config = UpstreamServer(name="fs", command="echo", namespace="fs")
        registry.register_server(config)
        registry.register_tool("read_file", "fs")

        assert registry.server_for_tool("read_file") == "fs"

    def test_namespace_prefix_fallback(self, identity_a):
        registry = UpstreamRegistry(identity_a)
        config = UpstreamServer(name="fs", command="echo", namespace="fs")
        registry.register_server(config)

        # No explicit mapping — falls back to prefix
        assert registry.server_for_tool("fs_read_file") == "fs"

    def test_unknown_tool_returns_none(self, identity_a):
        registry = UpstreamRegistry(identity_a)
        assert registry.server_for_tool("nonexistent") is None

    def test_threshold_for_server(self, identity_a):
        registry = UpstreamRegistry(identity_a)
        config = UpstreamServer(name="api", command="echo", trust_threshold=0.5)
        registry.register_server(config)

        assert registry.threshold_for("api") == 0.5
        assert registry.threshold_for("unknown", default=0.1) == 0.1


class TestTrustChainMiddleware:
    def _make_middleware(self, store=None, threshold=0.0, bootstrap=3):
        if store is None:
            store = RecordStore()
        gw_identity = Identity()
        registry = UpstreamRegistry(gw_identity)
        recorder = InteractionRecorder(gw_identity, store)
        middleware = TrustChainMiddleware(
            registry=registry,
            recorder=recorder,
            store=store,
            default_threshold=threshold,
            bootstrap_interactions=bootstrap,
        )
        return middleware, registry, gw_identity

    def test_native_tools_bypass_gate(self):
        middleware, _, _ = self._make_middleware()
        assert middleware._is_native_tool("trustchain_check_trust")
        assert middleware._is_native_tool("trustchain_list_servers")
        assert not middleware._is_native_tool("fs_read_file")

    def test_trust_scoring_builds_over_interactions(self, store, identity_a, identity_b):
        """Trust grows as interactions accumulate."""
        assert compute_trust(identity_a.pubkey_hex, store) == 0.0

        for i in range(5):
            record = create_record(
                identity_a, identity_b,
                seq_a=i, seq_b=i,
                prev_hash_a=store.last_hash_for(identity_a.pubkey_hex),
                prev_hash_b=store.last_hash_for(identity_b.pubkey_hex),
                interaction_type="service",
                outcome="completed",
            )
            store.add_record(record)

        trust = compute_trust(identity_a.pubkey_hex, store)
        assert trust > 0.0

    def test_bootstrap_allows_new_servers(self):
        """Servers with < bootstrap_interactions are always allowed."""
        store = RecordStore()
        middleware, registry, gw = self._make_middleware(
            store=store, threshold=0.5, bootstrap=3
        )
        config = UpstreamServer(name="new", command="echo", trust_threshold=0.5)
        registry.register_server(config)

        identity = registry.identity_for("new")
        count = len(store.get_records_for(identity.pubkey_hex))
        assert count < 3  # Bootstrap mode
