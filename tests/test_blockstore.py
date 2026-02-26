"""Tests for the TrustChain v2 BlockStore."""

import os
import tempfile

import pytest

from trustchain.blockstore import MemoryBlockStore, SQLiteBlockStore
from trustchain.halfblock import GENESIS_HASH, BlockType, create_half_block
from trustchain.identity import Identity


@pytest.fixture
def identity_a():
    return Identity()


@pytest.fixture
def identity_b():
    return Identity()


def _make_block(identity, seq, prev_hash, link_pubkey, link_seq=0, block_type=BlockType.PROPOSAL):
    return create_half_block(
        identity=identity,
        sequence_number=seq,
        link_public_key=link_pubkey,
        link_sequence_number=link_seq,
        previous_hash=prev_hash,
        block_type=block_type,
        transaction={"interaction_type": "service", "outcome": "completed"},
    )


class TestMemoryBlockStore:
    def test_add_and_get_block(self, identity_a, identity_b):
        store = MemoryBlockStore()
        block = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
        store.add_block(block)

        retrieved = store.get_block(identity_a.pubkey_hex, 1)
        assert retrieved is not None
        assert retrieved.block_hash == block.block_hash

    def test_duplicate_raises(self, identity_a, identity_b):
        store = MemoryBlockStore()
        block = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
        store.add_block(block)

        with pytest.raises(ValueError, match="Duplicate"):
            store.add_block(block)

    def test_get_chain_sorted(self, identity_a, identity_b):
        store = MemoryBlockStore()
        b1 = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
        store.add_block(b1)
        b2 = _make_block(identity_a, 2, b1.block_hash, identity_b.pubkey_hex)
        store.add_block(b2)

        chain = store.get_chain(identity_a.pubkey_hex)
        assert len(chain) == 2
        assert chain[0].sequence_number == 1
        assert chain[1].sequence_number == 2

    def test_get_latest_seq(self, identity_a, identity_b):
        store = MemoryBlockStore()
        assert store.get_latest_seq(identity_a.pubkey_hex) == 0

        b1 = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
        store.add_block(b1)
        assert store.get_latest_seq(identity_a.pubkey_hex) == 1

        b2 = _make_block(identity_a, 2, b1.block_hash, identity_b.pubkey_hex)
        store.add_block(b2)
        assert store.get_latest_seq(identity_a.pubkey_hex) == 2

    def test_get_head_hash(self, identity_a, identity_b):
        store = MemoryBlockStore()
        assert store.get_head_hash(identity_a.pubkey_hex) == GENESIS_HASH

        b1 = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
        store.add_block(b1)
        assert store.get_head_hash(identity_a.pubkey_hex) == b1.block_hash

    def test_get_linked_block_proposal_agreement(self, identity_a, identity_b):
        store = MemoryBlockStore()

        proposal = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex, 0, BlockType.PROPOSAL)
        store.add_block(proposal)

        agreement = _make_block(identity_b, 1, GENESIS_HASH, identity_a.pubkey_hex, 1, BlockType.AGREEMENT)
        store.add_block(agreement)

        # Find agreement from proposal
        linked = store.get_linked_block(proposal)
        assert linked is not None
        assert linked.public_key == identity_b.pubkey_hex
        assert linked.block_type == BlockType.AGREEMENT

        # Find proposal from agreement
        linked_back = store.get_linked_block(agreement)
        assert linked_back is not None
        assert linked_back.public_key == identity_a.pubkey_hex
        assert linked_back.block_type == BlockType.PROPOSAL

    def test_get_linked_block_no_agreement(self, identity_a, identity_b):
        store = MemoryBlockStore()
        proposal = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex, 0, BlockType.PROPOSAL)
        store.add_block(proposal)

        linked = store.get_linked_block(proposal)
        assert linked is None

    def test_crawl(self, identity_a, identity_b):
        store = MemoryBlockStore()
        b1 = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
        store.add_block(b1)
        b2 = _make_block(identity_a, 2, b1.block_hash, identity_b.pubkey_hex)
        store.add_block(b2)
        b3 = _make_block(identity_a, 3, b2.block_hash, identity_b.pubkey_hex)
        store.add_block(b3)

        # Crawl from seq 2
        blocks = store.crawl(identity_a.pubkey_hex, 2)
        assert len(blocks) == 2
        assert blocks[0].sequence_number == 2
        assert blocks[1].sequence_number == 3

    def test_get_all_pubkeys(self, identity_a, identity_b):
        store = MemoryBlockStore()
        b1 = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
        store.add_block(b1)
        b2 = _make_block(identity_b, 1, GENESIS_HASH, identity_a.pubkey_hex)
        store.add_block(b2)

        pubkeys = store.get_all_pubkeys()
        assert len(pubkeys) == 2
        assert identity_a.pubkey_hex in pubkeys
        assert identity_b.pubkey_hex in pubkeys

    def test_get_block_count(self, identity_a, identity_b):
        store = MemoryBlockStore()
        assert store.get_block_count() == 0

        b1 = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
        store.add_block(b1)
        assert store.get_block_count() == 1


class TestSQLiteBlockStore:
    def test_add_and_get_block(self, identity_a, identity_b):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store = SQLiteBlockStore(db_path)
            block = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
            store.add_block(block)

            retrieved = store.get_block(identity_a.pubkey_hex, 1)
            assert retrieved is not None
            assert retrieved.block_hash == block.block_hash
            store.close()
        finally:
            os.unlink(db_path)

    def test_persistence_across_connections(self, identity_a, identity_b):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Write
            store1 = SQLiteBlockStore(db_path)
            block = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
            store1.add_block(block)
            store1.close()

            # Read
            store2 = SQLiteBlockStore(db_path)
            retrieved = store2.get_block(identity_a.pubkey_hex, 1)
            assert retrieved is not None
            assert retrieved.block_hash == block.block_hash
            store2.close()
        finally:
            os.unlink(db_path)

    def test_duplicate_raises(self, identity_a, identity_b):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store = SQLiteBlockStore(db_path)
            block = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
            store.add_block(block)

            with pytest.raises(ValueError, match="Duplicate"):
                store.add_block(block)
            store.close()
        finally:
            os.unlink(db_path)

    def test_get_chain_and_crawl(self, identity_a, identity_b):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            store = SQLiteBlockStore(db_path)
            b1 = _make_block(identity_a, 1, GENESIS_HASH, identity_b.pubkey_hex)
            store.add_block(b1)
            b2 = _make_block(identity_a, 2, b1.block_hash, identity_b.pubkey_hex)
            store.add_block(b2)

            chain = store.get_chain(identity_a.pubkey_hex)
            assert len(chain) == 2

            crawled = store.crawl(identity_a.pubkey_hex, 2)
            assert len(crawled) == 1
            assert crawled[0].sequence_number == 2
            store.close()
        finally:
            os.unlink(db_path)
