"""Record store with graph queries and optional file persistence."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, List

import networkx as nx

from trustchain.record import InteractionRecord, verify_record

if TYPE_CHECKING:
    from trustchain.chain import PersonalChain

logger = logging.getLogger("trustchain.store")


class RecordStore:
    """Stores bilateral interaction records and provides graph queries."""

    def __init__(self):
        self.records: List[InteractionRecord] = []
        self._validate_on_add: bool = False

    def enable_validation(self) -> None:
        """Enable chain validation on add_record().

        When enabled, add_record() verifies signatures and checks that the
        new record's sequence numbers and prev-hashes are consistent with
        the existing chain state.
        """
        self._validate_on_add = True

    def add_record(self, record: InteractionRecord) -> None:
        if self._validate_on_add:
            # Signature check
            if not verify_record(record):
                from trustchain.exceptions import SignatureError

                raise SignatureError(
                    record.agent_a_pubkey, record.seq_a,
                    detail="failed signature verification on add",
                )

            # Chain integrity check for both sides
            for pubkey, seq, prev_hash in [
                (record.agent_a_pubkey, record.seq_a, record.prev_hash_a),
                (record.agent_b_pubkey, record.seq_b, record.prev_hash_b),
            ]:
                expected_seq = self.sequence_number_for(pubkey)
                expected_hash = self.last_hash_for(pubkey)
                if seq != expected_seq:
                    from trustchain.exceptions import SequenceGapError

                    raise SequenceGapError(pubkey, expected=expected_seq, got=seq)
                if prev_hash != expected_hash:
                    from trustchain.exceptions import PrevHashMismatchError

                    raise PrevHashMismatchError(
                        pubkey, seq, expected=expected_hash, got=prev_hash,
                    )

        self.records.append(record)

    def get_records_for(self, pubkey: str) -> List[InteractionRecord]:
        return [
            r
            for r in self.records
            if r.agent_a_pubkey == pubkey or r.agent_b_pubkey == pubkey
        ]

    def get_pair_history(
        self, pubkey_a: str, pubkey_b: str
    ) -> List[InteractionRecord]:
        return [
            r
            for r in self.records
            if {r.agent_a_pubkey, r.agent_b_pubkey} == {pubkey_a, pubkey_b}
        ]

    def get_interaction_graph(self) -> nx.Graph:
        """Build an undirected interaction graph. Edge weight = number of records."""
        G = nx.Graph()
        for r in self.records:
            a, b = r.agent_a_pubkey, r.agent_b_pubkey
            if G.has_edge(a, b):
                G[a][b]["weight"] += 1
            else:
                G.add_edge(a, b, weight=1)
        return G

    def sequence_number_for(self, pubkey: str) -> int:
        """Next sequence number for an agent's chain.

        Returns max(seq) + 1 across all records involving this agent,
        which handles out-of-order arrival correctly.
        """
        records = self.get_records_for(pubkey)
        if not records:
            return 0
        max_seq = -1
        for r in records:
            if r.agent_a_pubkey == pubkey:
                max_seq = max(max_seq, r.seq_a)
            if r.agent_b_pubkey == pubkey:
                max_seq = max(max_seq, r.seq_b)
        return max_seq + 1

    def last_hash_for(self, pubkey: str) -> str:
        """Hash of the highest-sequence record for this agent, or GENESIS_HASH."""
        records = self.get_records_for(pubkey)
        if not records:
            return "0" * 64
        # Find the record with the highest sequence number for this agent
        best_record = None
        best_seq = -1
        for r in records:
            if r.agent_a_pubkey == pubkey and r.seq_a > best_seq:
                best_seq = r.seq_a
                best_record = r
            if r.agent_b_pubkey == pubkey and r.seq_b > best_seq:
                best_seq = r.seq_b
                best_record = r
        return best_record.record_hash if best_record is not None else "0" * 64

    def get_chain(self, pubkey: str) -> PersonalChain:
        """Build and return a PersonalChain for the given agent."""
        from trustchain.chain import PersonalChain

        return PersonalChain.from_records(pubkey, self.records)


class FileRecordStore(RecordStore):
    """RecordStore that persists records to a JSON file on disk."""

    def __init__(self, path: str | Path):
        super().__init__()
        self.path = Path(path)
        self._load()

    def _load(self):
        """Load records from disk if the file exists."""
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for entry in data:
            record = InteractionRecord(
                agent_a_pubkey=entry["agent_a_pubkey"],
                agent_b_pubkey=entry["agent_b_pubkey"],
                seq_a=entry["seq_a"],
                seq_b=entry["seq_b"],
                prev_hash_a=entry["prev_hash_a"],
                prev_hash_b=entry["prev_hash_b"],
                interaction_type=entry["interaction_type"],
                outcome=entry["outcome"],
                timestamp=entry["timestamp"],
                sig_a=bytes.fromhex(entry["sig_a"]),
                sig_b=bytes.fromhex(entry["sig_b"]),
            )
            self.records.append(record)

    def _save(self):
        """Persist all records to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in self.records]
        self.path.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    def add_record(self, record: InteractionRecord):
        super().add_record(record)
        self._save()
