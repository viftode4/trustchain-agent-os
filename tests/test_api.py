"""Tests for the TrustChain v2 HTTPS transport layer."""

import pytest
from fastapi.testclient import TestClient

from trustchain.api import HalfBlockModel, TrustChainNode
from trustchain.blockstore import MemoryBlockStore
from trustchain.halfblock import GENESIS_HASH, BlockType, create_half_block
from trustchain.identity import Identity


@pytest.fixture
def identity_a():
    return Identity()


@pytest.fixture
def identity_b():
    return Identity()


@pytest.fixture
def node_a(identity_a):
    store = MemoryBlockStore()
    return TrustChainNode(identity_a, store, "127.0.0.1", 8100)


@pytest.fixture
def node_b(identity_b):
    store = MemoryBlockStore()
    return TrustChainNode(identity_b, store, "127.0.0.1", 8101)


@pytest.fixture
def client_a(node_a):
    return TestClient(node_a.app)


@pytest.fixture
def client_b(node_b):
    return TestClient(node_b.app)


class TestStatusEndpoint:
    def test_status(self, client_a, identity_a):
        resp = client_a.get("/trustchain/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["public_key"] == identity_a.pubkey_hex
        assert data["chain_length"] == 0
        assert data["total_blocks"] == 0
        assert data["peers"] == []


class TestProposeEndpoint:
    def test_propose_and_get_agreement(self, client_b, identity_a, identity_b):
        """Send a proposal to node B, get agreement back."""
        proposal = create_half_block(
            identity=identity_a,
            sequence_number=1,
            link_public_key=identity_b.pubkey_hex,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"interaction_type": "service", "outcome": "completed"},
        )
        model = HalfBlockModel.from_halfblock(proposal)

        resp = client_b.post(
            "/trustchain/propose",
            json={"block": model.model_dump()},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] is True
        assert data["agreement"] is not None
        assert data["agreement"]["block_type"] == "agreement"
        assert data["agreement"]["link_public_key"] == identity_a.pubkey_hex
        assert data["agreement"]["link_sequence_number"] == 1


class TestBlocksEndpoint:
    def test_crawl_blocks(self, client_b, node_b, identity_a, identity_b):
        """Store some blocks and crawl them."""
        # Manually create blocks on node_b
        proposal = create_half_block(
            identity=identity_a,
            sequence_number=1,
            link_public_key=identity_b.pubkey_hex,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={},
        )
        node_b.store.add_block(proposal)

        resp = client_b.get(f"/trustchain/blocks/{identity_a.pubkey_hex}?start_seq=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["blocks"]) == 1
        assert data["blocks"][0]["sequence_number"] == 1

    def test_get_specific_block(self, client_b, node_b, identity_a, identity_b):
        proposal = create_half_block(
            identity=identity_a,
            sequence_number=1,
            link_public_key=identity_b.pubkey_hex,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"test": True},
        )
        node_b.store.add_block(proposal)

        resp = client_b.get(f"/trustchain/blocks/{identity_a.pubkey_hex}/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sequence_number"] == 1

    def test_get_nonexistent_block(self, client_b, identity_a):
        resp = client_b.get(f"/trustchain/blocks/{identity_a.pubkey_hex}/999")
        assert resp.status_code == 404


class TestTrustChainNode:
    def test_register_peer(self, node_a, identity_b):
        node_a.register_peer(identity_b.pubkey_hex, "http://127.0.0.1:8101")
        assert identity_b.pubkey_hex in node_a.peers
        assert node_a.peers[identity_b.pubkey_hex] == "http://127.0.0.1:8101"

    def test_node_properties(self, node_a, identity_a):
        assert node_a.pubkey == identity_a.pubkey_hex
        assert node_a.url == "http://127.0.0.1:8100"


class TestHalfBlockModel:
    def test_roundtrip(self, identity_a):
        block = create_half_block(
            identity=identity_a,
            sequence_number=1,
            link_public_key="0" * 64,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"key": "value"},
        )

        model = HalfBlockModel.from_halfblock(block)
        restored = model.to_halfblock()

        assert restored.public_key == block.public_key
        assert restored.block_hash == block.block_hash
        assert restored.signature == block.signature
        assert restored.transaction == block.transaction


class TestProposeRejection:
    def test_reject_invalid_proposal(self, client_b, identity_a, identity_b):
        """A proposal with invalid signature should be rejected."""
        proposal = create_half_block(
            identity=identity_a,
            sequence_number=1,
            link_public_key=identity_b.pubkey_hex,
            link_sequence_number=0,
            previous_hash=GENESIS_HASH,
            block_type=BlockType.PROPOSAL,
            transaction={"test": True},
        )
        proposal.signature = "00" * 64  # Tamper
        proposal.block_hash = "ff" * 32  # Also tamper hash
        model = HalfBlockModel.from_halfblock(proposal)

        resp = client_b.post(
            "/trustchain/propose",
            json={"block": model.model_dump()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] is False

class TestCrawlPagination:
    def test_crawl_with_limit(self, client_b, node_b, identity_a, identity_b):
        """Crawl should respect limit parameter."""
        # Add 5 blocks
        prev = GENESIS_HASH
        for i in range(1, 6):
            block = create_half_block(
                identity=identity_a,
                sequence_number=i,
                link_public_key=identity_b.pubkey_hex,
                link_sequence_number=0,
                previous_hash=prev,
                block_type=BlockType.PROPOSAL,
                transaction={},
            )
            node_b.store.add_block(block)
            prev = block.block_hash

        resp = client_b.get(f"/trustchain/blocks/{identity_a.pubkey_hex}?start_seq=1&limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["blocks"]) == 3

    def test_crawl_start_seq_validation(self, client_b, identity_a):
        """start_seq must be >= 1."""
        resp = client_b.get(f"/trustchain/blocks/{identity_a.pubkey_hex}?start_seq=0")
        assert resp.status_code == 422  # Validation error
