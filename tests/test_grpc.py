"""Tests for gRPC Agent API (Phase 5).

Tests the gRPC servicer, client, and server lifecycle.
"""

import asyncio
import json

import pytest

from trustchain.identity import Identity
from trustchain.blockstore import MemoryBlockStore
from trustchain.protocol import TrustChainProtocol
from trustchain.halfblock import HalfBlock, BlockType
from trustchain.trust import TrustEngine
from trustchain.grpc.service import TrustChainServicer, SERVICE_NAME, _method
from trustchain.grpc.client import TrustChainGRPCClient
from trustchain.grpc.server import start_grpc_server, stop_grpc_server
from trustchain.proto.serialization import (
    encode_propose_message,
    decode_propose_message,
    encode_agree_message,
    halfblock_to_proto,
    proto_to_halfblock,
)


# ---- Servicer Unit Tests ----


class MockContext:
    """Minimal mock for grpc.aio.ServicerContext."""

    def __init__(self):
        self._code = None
        self._details = None

    def set_code(self, code):
        self._code = code

    def set_details(self, details):
        self._details = details


class TestServicerPropose:
    async def test_valid_proposal(self):
        alice = Identity()
        bob = Identity()
        store_a = MemoryBlockStore()
        store_b = MemoryBlockStore()
        proto_a = TrustChainProtocol(alice, store_a)
        proto_b = TrustChainProtocol(bob, store_b)

        servicer = TrustChainServicer(
            protocol=proto_b, store=store_b, identity=bob
        )

        # Alice creates a proposal for Bob
        proposal = proto_a.create_proposal(
            bob.pubkey_hex,
            {"interaction_type": "test", "outcome": "completed"},
        )

        # Encode and send to servicer
        request = encode_propose_message(proposal)
        ctx = MockContext()
        response = await servicer.Propose(request, ctx)

        # Should return an agreement
        assert response is not None
        assert len(response) > 0

    async def test_invalid_proposal_rejected(self):
        bob = Identity()
        store_b = MemoryBlockStore()
        proto_b = TrustChainProtocol(bob, store_b)

        servicer = TrustChainServicer(
            protocol=proto_b, store=store_b, identity=bob
        )

        # Send garbage data
        ctx = MockContext()
        response = await servicer.Propose(b"invalid", ctx)
        assert response is not None  # Should return error response, not crash


class TestServicerGetStatus:
    async def test_status(self):
        bob = Identity()
        store = MemoryBlockStore()
        proto = TrustChainProtocol(bob, store)

        servicer = TrustChainServicer(
            protocol=proto, store=store, identity=bob
        )

        ctx = MockContext()
        response = await servicer.GetStatus(b"", ctx)
        data = json.loads(response)
        assert data["public_key"] == bob.pubkey_hex
        assert data["chain_length"] == 0


class TestServicerGetTrustScore:
    async def test_trust_score_with_engine(self):
        bob = Identity()
        store = MemoryBlockStore()
        proto = TrustChainProtocol(bob, store)
        engine = TrustEngine(store, seed_nodes=[bob.pubkey_hex])

        servicer = TrustChainServicer(
            protocol=proto, store=store, identity=bob, trust_engine=engine
        )

        ctx = MockContext()
        request = json.dumps({"target_pubkey": bob.pubkey_hex}).encode()
        response = await servicer.GetTrustScore(request, ctx)
        data = json.loads(response)
        assert "trust_score" in data
        assert "chain_integrity" in data
        assert "interaction_count" in data

    async def test_trust_score_without_engine(self):
        bob = Identity()
        store = MemoryBlockStore()
        proto = TrustChainProtocol(bob, store)

        servicer = TrustChainServicer(
            protocol=proto, store=store, identity=bob
        )

        ctx = MockContext()
        request = json.dumps({"target_pubkey": bob.pubkey_hex}).encode()
        response = await servicer.GetTrustScore(request, ctx)
        data = json.loads(response)
        assert data["trust_score"] == 0.0


class TestServicerGetBlock:
    async def test_get_existing_block(self):
        bob = Identity()
        store = MemoryBlockStore()
        proto = TrustChainProtocol(bob, store)
        peer = Identity()

        block = proto.create_proposal(
            peer.pubkey_hex, {"type": "test"}
        )

        servicer = TrustChainServicer(
            protocol=proto, store=store, identity=bob
        )

        ctx = MockContext()
        request = json.dumps({
            "public_key": bob.pubkey_hex,
            "start_seq": 1,
        }).encode()
        response = await servicer.GetBlock(request, ctx)
        assert len(response) > 0
        restored = proto_to_halfblock(response)
        assert restored.public_key == bob.pubkey_hex
        assert restored.sequence_number == 1

    async def test_get_nonexistent_block(self):
        bob = Identity()
        store = MemoryBlockStore()
        proto = TrustChainProtocol(bob, store)

        servicer = TrustChainServicer(
            protocol=proto, store=store, identity=bob
        )

        ctx = MockContext()
        request = json.dumps({
            "public_key": "nonexistent",
            "start_seq": 1,
        }).encode()
        response = await servicer.GetBlock(request, ctx)
        assert response == b""


# ---- gRPC Server Integration Tests ----


class TestGRPCServerLifecycle:
    async def test_start_stop(self):
        """Verify gRPC server starts and stops cleanly."""
        bob = Identity()
        store = MemoryBlockStore()
        proto = TrustChainProtocol(bob, store)

        server = await start_grpc_server(
            protocol=proto,
            store=store,
            identity=bob,
            port=50099,
        )

        # Server should be running
        assert server is not None

        await stop_grpc_server(server)


class TestGRPCClientServerIntegration:
    async def test_propose_via_grpc(self):
        """Full proposal/agreement round-trip over gRPC."""
        alice = Identity()
        bob = Identity()
        store_a = MemoryBlockStore()
        store_b = MemoryBlockStore()
        proto_a = TrustChainProtocol(alice, store_a)
        proto_b = TrustChainProtocol(bob, store_b)

        # Start Bob's gRPC server
        server = await start_grpc_server(
            protocol=proto_b,
            store=store_b,
            identity=bob,
            port=50098,
        )

        try:
            # Alice creates a client and proposes
            client = TrustChainGRPCClient("localhost:50098")

            proposal = proto_a.create_proposal(
                bob.pubkey_hex,
                {"interaction_type": "compute", "outcome": "completed"},
            )

            accepted, agreement = await client.propose(proposal)

            assert accepted is True
            assert agreement is not None
            assert agreement.block_type == BlockType.AGREEMENT
            assert agreement.public_key == bob.pubkey_hex
            assert agreement.link_public_key == alice.pubkey_hex

            await client.close()
        finally:
            await stop_grpc_server(server)

    async def test_get_status_via_grpc(self):
        """Get node status over gRPC."""
        bob = Identity()
        store = MemoryBlockStore()
        proto = TrustChainProtocol(bob, store)

        server = await start_grpc_server(
            protocol=proto,
            store=store,
            identity=bob,
            port=50097,
        )

        try:
            client = TrustChainGRPCClient("localhost:50097")
            status = await client.get_status()

            assert status is not None
            assert status["public_key"] == bob.pubkey_hex
            assert status["chain_length"] == 0

            await client.close()
        finally:
            await stop_grpc_server(server)

    async def test_get_trust_score_via_grpc(self):
        """Get trust score over gRPC."""
        bob = Identity()
        store = MemoryBlockStore()
        proto = TrustChainProtocol(bob, store)
        engine = TrustEngine(store, seed_nodes=[bob.pubkey_hex])

        server = await start_grpc_server(
            protocol=proto,
            store=store,
            identity=bob,
            port=50096,
            trust_engine=engine,
        )

        try:
            client = TrustChainGRPCClient("localhost:50096")
            result = await client.get_trust_score(bob.pubkey_hex)

            assert result is not None
            assert "trust_score" in result
            assert result["target_pubkey"] == bob.pubkey_hex

            await client.close()
        finally:
            await stop_grpc_server(server)

    async def test_get_block_via_grpc(self):
        """Get a specific block over gRPC."""
        bob = Identity()
        store = MemoryBlockStore()
        proto = TrustChainProtocol(bob, store)
        peer = Identity()

        # Create a block first
        proto.create_proposal(peer.pubkey_hex, {"type": "test"})

        server = await start_grpc_server(
            protocol=proto,
            store=store,
            identity=bob,
            port=50095,
        )

        try:
            client = TrustChainGRPCClient("localhost:50095")
            block = await client.get_block(bob.pubkey_hex, 1)

            assert block is not None
            assert block.public_key == bob.pubkey_hex
            assert block.sequence_number == 1

            await client.close()
        finally:
            await stop_grpc_server(server)

    async def test_multiple_proposals_via_grpc(self):
        """Multiple sequential proposals over gRPC."""
        alice = Identity()
        bob = Identity()
        store_a = MemoryBlockStore()
        store_b = MemoryBlockStore()
        proto_a = TrustChainProtocol(alice, store_a)
        proto_b = TrustChainProtocol(bob, store_b)

        server = await start_grpc_server(
            protocol=proto_b,
            store=store_b,
            identity=bob,
            port=50094,
        )

        try:
            client = TrustChainGRPCClient("localhost:50094")

            for i in range(3):
                proposal = proto_a.create_proposal(
                    bob.pubkey_hex,
                    {"type": "test", "round": i},
                )
                accepted, agreement = await client.propose(proposal)
                assert accepted is True
                assert agreement is not None

            # Bob should have 3 blocks on his chain
            assert store_b.get_latest_seq(bob.pubkey_hex) == 3

            await client.close()
        finally:
            await stop_grpc_server(server)
