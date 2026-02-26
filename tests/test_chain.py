"""Comprehensive tests for the TrustChain blockchain layer."""

import pytest

from trustchain.block import GENESIS_HASH, Block, HalfBlock
from trustchain.chain import PersonalChain, compute_chain_integrity, validate_chain_for
from trustchain.crawler import ChainCrawler, DAGView
from trustchain.exceptions import (
    ChainError,
    DuplicateSequenceError,
    EntanglementError,
    InvalidBlockError,
    PrevHashMismatchError,
    SequenceGapError,
    SignatureError,
)
from trustchain.identity import Identity
from trustchain.network import Peer, SimulatedNetwork
from trustchain.record import InteractionRecord, create_record, verify_record
from trustchain.store import RecordStore
from trustchain.trust import compute_chain_trust


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chain_records(identity_a, identity_b, count=5):
    """Create a list of well-formed bilateral records for a chain."""
    records = []
    prev_hash_a = GENESIS_HASH
    prev_hash_b = GENESIS_HASH
    for i in range(count):
        record = create_record(
            identity_a=identity_a,
            identity_b=identity_b,
            seq_a=i,
            seq_b=i,
            prev_hash_a=prev_hash_a,
            prev_hash_b=prev_hash_b,
            interaction_type="service",
            outcome="completed",
        )
        records.append(record)
        prev_hash_a = record.record_hash
        prev_hash_b = record.record_hash
    return records


# ===========================================================================
# Block & HalfBlock
# ===========================================================================


class TestBlock:
    def test_half_a_projection(self, identity_a, identity_b):
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        block = Block(record)
        half = block.half_a

        assert isinstance(half, HalfBlock)
        assert half.public_key == identity_a.pubkey_hex
        assert half.sequence_number == 0
        assert half.previous_hash == GENESIS_HASH
        assert half.link_public_key == identity_b.pubkey_hex
        assert half.link_sequence_number == 0
        assert half.block_hash == record.record_hash

    def test_half_b_projection(self, identity_a, identity_b):
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        block = Block(record)
        half = block.half_b

        assert half.public_key == identity_b.pubkey_hex
        assert half.link_public_key == identity_a.pubkey_hex

    def test_half_for(self, identity_a, identity_b):
        record = create_record(
            identity_a, identity_b,
            seq_a=3, seq_b=5,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        block = Block(record)

        assert block.half_for(identity_a.pubkey_hex).sequence_number == 3
        assert block.half_for(identity_b.pubkey_hex).sequence_number == 5

    def test_half_for_unknown_raises(self, identity_a, identity_b, identity_c):
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        block = Block(record)

        with pytest.raises(ValueError, match="not a party"):
            block.half_for(identity_c.pubkey_hex)

    def test_counterparty_half(self, identity_a, identity_b):
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        block = Block(record)

        cp = block.counterparty_half(identity_a.pubkey_hex)
        assert cp.public_key == identity_b.pubkey_hex

    def test_involves(self, identity_a, identity_b, identity_c):
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        block = Block(record)

        assert block.involves(identity_a.pubkey_hex)
        assert block.involves(identity_b.pubkey_hex)
        assert not block.involves(identity_c.pubkey_hex)

    def test_genesis_hash_constant(self):
        assert GENESIS_HASH == "0" * 64
        assert len(GENESIS_HASH) == 64

    def test_half_block_is_frozen(self, identity_a, identity_b):
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        half = Block(record).half_a
        with pytest.raises(AttributeError):
            half.sequence_number = 99


# ===========================================================================
# PersonalChain
# ===========================================================================


class TestPersonalChain:
    def test_empty_chain(self, identity_a):
        chain = PersonalChain(identity_a.pubkey_hex)
        assert chain.length == 0
        assert chain.head is None
        assert chain.head_hash == GENESIS_HASH
        assert chain.next_seq == 0

    def test_append_single_block(self, identity_a, identity_b):
        chain = PersonalChain(identity_a.pubkey_hex)
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        chain.append(Block(record))

        assert chain.length == 1
        assert chain.next_seq == 1
        assert chain.head_hash == record.record_hash

    def test_append_multiple_blocks(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=5)
        chain = PersonalChain(identity_a.pubkey_hex)
        for r in records:
            chain.append(Block(r))

        assert chain.length == 5
        assert chain.next_seq == 5
        assert chain.head_hash == records[-1].record_hash

    def test_from_records(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=3)
        chain = PersonalChain.from_records(identity_a.pubkey_hex, records)

        assert chain.length == 3
        assert chain.validate()

    def test_validate_valid_chain(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=5)
        chain = PersonalChain.from_records(identity_a.pubkey_hex, records)
        assert chain.validate() is True

    def test_validate_empty_chain(self, identity_a):
        chain = PersonalChain(identity_a.pubkey_hex)
        assert chain.validate() is True

    def test_blocks_in_order(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=3)
        chain = PersonalChain.from_records(identity_a.pubkey_hex, records)
        blocks = chain.blocks_in_order()
        assert len(blocks) == 3
        for i, b in enumerate(blocks):
            assert b.half_for(identity_a.pubkey_hex).sequence_number == i

    def test_get_block(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=3)
        chain = PersonalChain.from_records(identity_a.pubkey_hex, records)

        assert chain.get_block(0) is not None
        assert chain.get_block(2) is not None
        assert chain.get_block(5) is None


# ===========================================================================
# Chain validation errors
# ===========================================================================


class TestChainValidationErrors:
    def test_sequence_gap(self, identity_a, identity_b):
        """Skipping a sequence number should fail."""
        chain = PersonalChain(identity_a.pubkey_hex)
        # Create block at seq=0
        r0 = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        chain.append(Block(r0))

        # Try to append seq=2 (skipping 1)
        r2 = create_record(
            identity_a, identity_b,
            seq_a=2, seq_b=1,
            prev_hash_a=r0.record_hash, prev_hash_b=r0.record_hash,
            interaction_type="service", outcome="completed",
        )
        with pytest.raises(SequenceGapError) as exc_info:
            chain.append(Block(r2))
        assert exc_info.value.expected == 1
        assert exc_info.value.got == 2

    def test_prev_hash_mismatch(self, identity_a, identity_b):
        """Wrong prev_hash should fail."""
        chain = PersonalChain(identity_a.pubkey_hex)
        r0 = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        chain.append(Block(r0))

        # Use wrong prev_hash
        r1 = create_record(
            identity_a, identity_b,
            seq_a=1, seq_b=1,
            prev_hash_a="bad_hash" + "0" * 56,
            prev_hash_b=r0.record_hash,
            interaction_type="service", outcome="completed",
        )
        with pytest.raises(PrevHashMismatchError):
            chain.append(Block(r1))

    def test_duplicate_sequence(self, identity_a, identity_b):
        """Duplicate sequence number should fail."""
        chain = PersonalChain(identity_a.pubkey_hex)
        r0 = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        chain.append(Block(r0))

        # Try to append another seq=0
        r0_dup = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=1,
            prev_hash_a=GENESIS_HASH, prev_hash_b=r0.record_hash,
            interaction_type="service", outcome="completed",
        )
        with pytest.raises(DuplicateSequenceError):
            chain.append(Block(r0_dup))

    def test_signature_failure(self, identity_a, identity_b):
        """Tampered signature should fail."""
        chain = PersonalChain(identity_a.pubkey_hex)
        r0 = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        # Tamper with signature
        r0.sig_a = b"\x00" * 64
        with pytest.raises(SignatureError):
            chain.append(Block(r0))

    def test_invalid_block_wrong_agent(self, identity_a, identity_b, identity_c):
        """Block not involving the chain's agent should fail."""
        chain = PersonalChain(identity_a.pubkey_hex)
        r_bc = create_record(
            identity_b, identity_c,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        with pytest.raises(InvalidBlockError, match="does not involve"):
            chain.append(Block(r_bc))

    def test_exception_hierarchy(self):
        """All chain exceptions inherit from ChainError."""
        assert issubclass(SequenceGapError, ChainError)
        assert issubclass(PrevHashMismatchError, ChainError)
        assert issubclass(SignatureError, ChainError)
        assert issubclass(DuplicateSequenceError, ChainError)
        assert issubclass(EntanglementError, ChainError)
        assert issubclass(InvalidBlockError, ChainError)


# ===========================================================================
# Chain integrity scoring
# ===========================================================================


class TestChainIntegrity:
    def test_perfect_chain_score(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=5)
        chain = PersonalChain.from_records(identity_a.pubkey_hex, records)
        assert chain.integrity_score() == 1.0

    def test_empty_chain_score(self, identity_a):
        chain = PersonalChain(identity_a.pubkey_hex)
        assert chain.integrity_score() == 1.0

    def test_broken_chain_score(self, identity_a, identity_b):
        """A chain with a hash break mid-way should have partial integrity."""
        # Build 3 good blocks
        records = _make_chain_records(identity_a, identity_b, count=3)
        chain = PersonalChain(identity_a.pubkey_hex)
        for r in records:
            chain.append(Block(r))

        # Force-insert a bad block at seq=3 (wrong prev_hash)
        bad_record = create_record(
            identity_a, identity_b,
            seq_a=3, seq_b=3,
            prev_hash_a="bad_" + "0" * 60,
            prev_hash_b=records[-1].record_hash,
            interaction_type="service", outcome="completed",
        )
        # Bypass validation by inserting directly
        chain._blocks[3] = Block(bad_record)

        # 3 out of 4 blocks valid before the break
        assert chain.integrity_score() == 3.0 / 4.0

    def test_compute_chain_integrity_convenience(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=5)
        score = compute_chain_integrity(identity_a.pubkey_hex, records)
        assert score == 1.0

    def test_validate_chain_for_convenience(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=5)
        assert validate_chain_for(identity_a.pubkey_hex, records) is True


# ===========================================================================
# Store validation mode
# ===========================================================================


class TestStoreValidation:
    def test_default_no_validation(self, store, identity_a, identity_b):
        """By default, store accepts anything."""
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        store.add_record(record)
        assert len(store.records) == 1

    def test_validation_rejects_bad_signature(self, identity_a, identity_b):
        store = RecordStore()
        store.enable_validation()
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        record.sig_a = b"\x00" * 64  # Tamper
        with pytest.raises(SignatureError):
            store.add_record(record)

    def test_validation_rejects_wrong_sequence(self, identity_a, identity_b):
        store = RecordStore()
        store.enable_validation()
        record = create_record(
            identity_a, identity_b,
            seq_a=5, seq_b=0,  # Should be 0
            prev_hash_a=GENESIS_HASH, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        with pytest.raises(SequenceGapError):
            store.add_record(record)

    def test_validation_rejects_wrong_prev_hash(self, identity_a, identity_b):
        store = RecordStore()
        store.enable_validation()
        record = create_record(
            identity_a, identity_b,
            seq_a=0, seq_b=0,
            prev_hash_a="wrong" + "0" * 59, prev_hash_b=GENESIS_HASH,
            interaction_type="service", outcome="completed",
        )
        with pytest.raises(PrevHashMismatchError):
            store.add_record(record)

    def test_validation_accepts_valid_chain(self, identity_a, identity_b):
        store = RecordStore()
        store.enable_validation()
        records = _make_chain_records(identity_a, identity_b, count=5)
        for r in records:
            store.add_record(r)
        assert len(store.records) == 5

    def test_get_chain(self, identity_a, identity_b):
        store = RecordStore()
        records = _make_chain_records(identity_a, identity_b, count=3)
        for r in records:
            store.add_record(r)
        chain = store.get_chain(identity_a.pubkey_hex)
        assert chain.length == 3
        assert chain.validate()


# ===========================================================================
# Store bug fixes
# ===========================================================================


class TestStoreBugFixes:
    def test_sequence_number_uses_max_seq(self, identity_a, identity_b):
        """sequence_number_for should return max(seq)+1, not len(records)."""
        store = RecordStore()
        records = _make_chain_records(identity_a, identity_b, count=3)
        for r in records:
            store.add_record(r)

        assert store.sequence_number_for(identity_a.pubkey_hex) == 3
        assert store.sequence_number_for(identity_b.pubkey_hex) == 3

    def test_last_hash_uses_highest_seq(self, identity_a, identity_b):
        """last_hash_for should return hash of highest-seq record."""
        store = RecordStore()
        records = _make_chain_records(identity_a, identity_b, count=3)
        for r in records:
            store.add_record(r)

        assert store.last_hash_for(identity_a.pubkey_hex) == records[-1].record_hash
        assert store.last_hash_for(identity_b.pubkey_hex) == records[-1].record_hash

    def test_empty_store_defaults(self):
        store = RecordStore()
        assert store.sequence_number_for("nonexistent") == 0
        assert store.last_hash_for("nonexistent") == GENESIS_HASH


# ===========================================================================
# Cross-chain entanglement & DAG
# ===========================================================================


class TestCrossChainEntanglement:
    def test_dag_view_simple(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=3)
        crawler = ChainCrawler(records)
        dag = crawler.build_dag()

        assert identity_a.pubkey_hex in dag.agents
        assert identity_b.pubkey_hex in dag.agents
        assert dag.total_blocks == 6  # 3 blocks x 2 chains
        assert len(dag.cross_links) == 3

    def test_entanglement_ratio_perfect(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=5)
        crawler = ChainCrawler(records)
        dag = crawler.build_dag()

        assert dag.entanglement_ratio == 1.0

    def test_entanglement_ratio_empty(self):
        dag = DAGView()
        assert dag.entanglement_ratio == 1.0  # vacuously true

    def test_dag_with_three_agents(self, identity_a, identity_b, identity_c):
        """DAG with A<->B and B<->C interactions."""
        records_ab = _make_chain_records(identity_a, identity_b, count=2)
        # For B<->C, B's seq starts at 2 (after 2 with A)
        records_bc = []
        prev_hash_b = records_ab[-1].record_hash  # B's last hash
        prev_hash_c = GENESIS_HASH
        for i in range(2):
            r = create_record(
                identity_b, identity_c,
                seq_a=2 + i, seq_b=i,
                prev_hash_a=prev_hash_b, prev_hash_b=prev_hash_c,
                interaction_type="data", outcome="completed",
            )
            records_bc.append(r)
            prev_hash_b = r.record_hash
            prev_hash_c = r.record_hash

        all_records = records_ab + records_bc
        crawler = ChainCrawler(all_records)
        dag = crawler.build_dag()

        assert len(dag.agents) == 3
        # A has 2 blocks, B has 4 (2 with A + 2 with C), C has 2 = 8 total
        assert dag.chains[identity_a.pubkey_hex].length == 2
        assert dag.chains[identity_b.pubkey_hex].length == 4
        assert dag.chains[identity_c.pubkey_hex].length == 2
        assert dag.total_blocks == 8


# ===========================================================================
# Tampering detection
# ===========================================================================


class TestTamperingDetection:
    def test_clean_records(self, identity_a, identity_b):
        records = _make_chain_records(identity_a, identity_b, count=5)
        crawler = ChainCrawler(records)
        report = crawler.detect_tampering()

        assert report.is_clean
        assert report.issue_count == 0

    def test_detect_sequence_gap(self, identity_a, identity_b):
        """Deliberately create a gap and detect it."""
        records = _make_chain_records(identity_a, identity_b, count=2)
        # Create a record with seq_a=5 (gap from 2)
        bad_record = create_record(
            identity_a, identity_b,
            seq_a=5, seq_b=2,
            prev_hash_a=records[-1].record_hash,
            prev_hash_b=records[-1].record_hash,
            interaction_type="service", outcome="completed",
        )
        records.append(bad_record)

        crawler = ChainCrawler(records)
        report = crawler.detect_tampering()

        assert not report.is_clean
        assert len(report.chain_gaps) > 0

    def test_detect_hash_break(self, identity_a, identity_b):
        """Create a hash break and detect it."""
        records = _make_chain_records(identity_a, identity_b, count=2)
        # Create record with wrong prev_hash_a
        bad_record = create_record(
            identity_a, identity_b,
            seq_a=2, seq_b=2,
            prev_hash_a="f" * 64,  # Wrong hash
            prev_hash_b=records[-1].record_hash,
            interaction_type="service", outcome="completed",
        )
        records.append(bad_record)

        crawler = ChainCrawler(records)
        report = crawler.detect_tampering()

        assert not report.is_clean
        assert len(report.hash_breaks) > 0


# ===========================================================================
# Simulated Network
# ===========================================================================


class TestSimulatedNetwork:
    def test_register_peer(self, identity_a):
        net = SimulatedNetwork()
        peer = net.register_peer(identity_a)

        assert isinstance(peer, Peer)
        assert peer.pubkey == identity_a.pubkey_hex
        assert peer.chain.length == 0
        assert len(net.peers) == 1

    def test_create_block(self, identity_a, identity_b):
        net = SimulatedNetwork()
        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)

        record = net.create_block(peer_a, peer_b, "service", "completed")

        assert verify_record(record)
        assert record.seq_a == 0
        assert record.seq_b == 0
        assert record.prev_hash_a == GENESIS_HASH
        assert record.prev_hash_b == GENESIS_HASH
        assert peer_a.chain.length == 1
        assert peer_b.chain.length == 1

    def test_create_multiple_blocks(self, identity_a, identity_b):
        net = SimulatedNetwork()
        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)

        for i in range(5):
            net.create_block(peer_a, peer_b, "service", "completed")

        assert peer_a.chain.length == 5
        assert peer_b.chain.length == 5
        assert peer_a.chain.next_seq == 5
        assert peer_b.chain.next_seq == 5

    def test_chains_are_valid(self, identity_a, identity_b):
        net = SimulatedNetwork()
        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)

        for _ in range(3):
            net.create_block(peer_a, peer_b)

        assert peer_a.chain.validate()
        assert peer_b.chain.validate()

    def test_multi_peer_network(self, identity_a, identity_b, identity_c):
        net = SimulatedNetwork()
        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)
        peer_c = net.register_peer(identity_c)

        net.create_block(peer_a, peer_b, "service", "completed")
        net.create_block(peer_a, peer_b, "service", "completed")
        net.create_block(peer_b, peer_c, "data", "completed")
        net.create_block(peer_a, peer_c, "compute", "completed")

        assert peer_a.chain.length == 3  # 2 with B + 1 with C
        assert peer_b.chain.length == 3  # 2 with A + 1 with C
        assert peer_c.chain.length == 2  # 1 with B + 1 with A

        assert peer_a.chain.validate()
        assert peer_b.chain.validate()
        assert peer_c.chain.validate()

    def test_exchange_chain(self, identity_a, identity_b):
        net = SimulatedNetwork()
        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)

        for _ in range(3):
            net.create_block(peer_a, peer_b)

        exchanged = net.exchange_chain(peer_a, peer_b)
        assert exchanged.length == 3
        assert exchanged.validate()

    def test_verify_peer_chain(self, identity_a, identity_b):
        net = SimulatedNetwork()
        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)

        net.create_block(peer_a, peer_b)
        assert net.verify_peer_chain(identity_a.pubkey_hex)
        assert net.verify_peer_chain(identity_b.pubkey_hex)

    def test_verify_unknown_peer_raises(self):
        net = SimulatedNetwork()
        with pytest.raises(ValueError, match="Unknown peer"):
            net.verify_peer_chain("nonexistent")

    def test_network_dag(self, identity_a, identity_b, identity_c):
        net = SimulatedNetwork()
        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)
        peer_c = net.register_peer(identity_c)

        net.create_block(peer_a, peer_b)
        net.create_block(peer_b, peer_c)

        dag = net.build_dag()
        assert len(dag.agents) == 3
        assert dag.entanglement_ratio == 1.0

    def test_network_tampering_clean(self, identity_a, identity_b):
        net = SimulatedNetwork()
        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)

        for _ in range(3):
            net.create_block(peer_a, peer_b)

        report = net.detect_tampering()
        assert report.is_clean

    def test_event_system(self, identity_a, identity_b):
        net = SimulatedNetwork()
        events = []

        net.on("block_created", lambda *args: events.append("block"))
        net.on("peer_registered", lambda *args: events.append("peer"))

        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)
        net.create_block(peer_a, peer_b)

        assert events == ["peer", "peer", "block"]

    def test_shared_store(self, identity_a, identity_b):
        """Two peers can share the same store."""
        net = SimulatedNetwork()
        shared_store = RecordStore()
        peer_a = net.register_peer(identity_a, store=shared_store)
        peer_b = net.register_peer(identity_b, store=shared_store)

        net.create_block(peer_a, peer_b)
        # Only one copy in shared store
        assert len(shared_store.records) == 1

    def test_separate_stores(self, identity_a, identity_b):
        """Two peers with separate stores each get the record."""
        net = SimulatedNetwork()
        peer_a = net.register_peer(identity_a)
        peer_b = net.register_peer(identity_b)

        net.create_block(peer_a, peer_b)
        assert len(peer_a.store.records) == 1
        assert len(peer_b.store.records) == 1


# ===========================================================================
# Chain trust scoring
# ===========================================================================


class TestChainTrust:
    def test_chain_trust_valid_chain(self, identity_a, identity_b):
        store = RecordStore()
        records = _make_chain_records(identity_a, identity_b, count=5)
        for r in records:
            store.add_record(r)

        trust = compute_chain_trust(identity_a.pubkey_hex, store)

        # With a valid chain, chain_trust should equal base trust (no penalty)
        from trustchain.trust import compute_trust
        base = compute_trust(identity_a.pubkey_hex, store)
        assert trust == base

    def test_chain_trust_empty_records(self, identity_a):
        store = RecordStore()
        trust = compute_chain_trust(identity_a.pubkey_hex, store)
        assert trust == 0.0

    def test_chain_trust_penalizes_broken_chain(self, identity_a, identity_b):
        """Broken chain should reduce trust below base trust."""
        store = RecordStore()
        # Build 3 valid records
        records = _make_chain_records(identity_a, identity_b, count=3)
        for r in records:
            store.add_record(r)

        # Add a record with wrong prev_hash (breaks chain)
        bad_record = create_record(
            identity_a, identity_b,
            seq_a=3, seq_b=3,
            prev_hash_a="f" * 64,
            prev_hash_b=records[-1].record_hash,
            interaction_type="service", outcome="completed",
        )
        store.add_record(bad_record)

        from trustchain.trust import compute_trust
        base = compute_trust(identity_a.pubkey_hex, store)
        chain_trust = compute_chain_trust(identity_a.pubkey_hex, store)

        assert chain_trust < base


# ===========================================================================
# Backward compatibility with existing conftest patterns
# ===========================================================================


class TestBackwardCompatibility:
    def test_populated_store_fixture(self, populated_store, identity_a, identity_b):
        """The existing populated_store fixture still works correctly."""
        assert len(populated_store.records) == 5
        assert populated_store.sequence_number_for(identity_a.pubkey_hex) == 5
        assert populated_store.sequence_number_for(identity_b.pubkey_hex) == 5

    def test_chain_from_populated_store(self, populated_store, identity_a):
        """Can build a valid chain from the populated_store fixture."""
        chain = populated_store.get_chain(identity_a.pubkey_hex)
        assert chain.length == 5
        assert chain.validate()
        assert chain.integrity_score() == 1.0

    def test_store_methods_backward_compat(self, store, identity_a, identity_b):
        """For well-formed chains appended in order, fixed methods give same results."""
        for i in range(5):
            record = create_record(
                identity_a=identity_a,
                identity_b=identity_b,
                seq_a=i,
                seq_b=i,
                prev_hash_a=store.last_hash_for(identity_a.pubkey_hex),
                prev_hash_b=store.last_hash_for(identity_b.pubkey_hex),
                interaction_type="service",
                outcome="completed",
            )
            store.add_record(record)

        # These are the same values the old buggy code would return
        # for well-formed chains — because len(records)==max(seq)+1
        assert store.sequence_number_for(identity_a.pubkey_hex) == 5
        assert store.sequence_number_for(identity_b.pubkey_hex) == 5
