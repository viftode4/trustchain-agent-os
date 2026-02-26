"""DAG crawling and cross-chain entanglement verification for TrustChain.

v2: Supports both v1 InteractionRecord-based crawling and v2 BlockStore-based
crawling with half-block pairs. Adds HTTPCrawler and orphan proposal detection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from trustchain.block import Block
from trustchain.chain import PersonalChain
from trustchain.exceptions import (
    ChainError,
    OrphanBlockError,
    PrevHashMismatchError,
    SequenceGapError,
    SignatureError,
)
from trustchain.record import InteractionRecord

logger = logging.getLogger("trustchain.crawler")


@dataclass
class CrossChainLink:
    """A link between two agents' chains at specific sequence numbers."""

    pubkey_a: str
    seq_a: int
    pubkey_b: str
    seq_b: int
    block_hash: str
    verified: bool = False


@dataclass
class DAGView:
    """A view of the full TrustChain DAG: all agents' personal chains plus
    cross-chain entanglement links.
    """

    chains: Dict[str, PersonalChain] = field(default_factory=dict)
    cross_links: List[CrossChainLink] = field(default_factory=list)
    orphan_proposals: List[str] = field(default_factory=list)

    @property
    def agents(self) -> Set[str]:
        """All agent public keys in the DAG."""
        return set(self.chains.keys())

    @property
    def total_blocks(self) -> int:
        """Total number of blocks across all chains."""
        return sum(c.length for c in self.chains.values())

    @property
    def entanglement_ratio(self) -> float:
        """Fraction of cross-chain links that are verified.

        Returns 1.0 if there are no cross-links (vacuously true).
        """
        if not self.cross_links:
            return 1.0
        verified = sum(1 for link in self.cross_links if link.verified)
        return verified / len(self.cross_links)


@dataclass
class TamperingReport:
    """Results of tampering detection across the DAG."""

    chain_gaps: List[str] = field(default_factory=list)
    hash_breaks: List[str] = field(default_factory=list)
    signature_failures: List[str] = field(default_factory=list)
    entanglement_issues: List[str] = field(default_factory=list)
    orphan_proposals: List[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not (
            self.chain_gaps
            or self.hash_breaks
            or self.signature_failures
            or self.entanglement_issues
            or self.orphan_proposals
        )

    @property
    def issue_count(self) -> int:
        return (
            len(self.chain_gaps)
            + len(self.hash_breaks)
            + len(self.signature_failures)
            + len(self.entanglement_issues)
            + len(self.orphan_proposals)
        )


# ===========================================================================
# v2 BlockStore-based crawler
# ===========================================================================


class BlockStoreCrawler:
    """Crawls a BlockStore to build DAG views and detect tampering.

    Works with v2 HalfBlock model — proposal/agreement pairs with
    cross-chain entanglement.
    """

    def __init__(self, store) -> None:
        from trustchain.blockstore import BlockStore
        self.store: BlockStore = store

    def build_dag(self) -> DAGView:
        """Build a DAGView from the block store."""
        from trustchain.halfblock import verify_block

        dag = DAGView()
        pubkeys = self.store.get_all_pubkeys()

        # Build personal chains (v2 mode)
        for pubkey in pubkeys:
            chain = PersonalChain.from_store(pubkey, self.store)
            dag.chains[pubkey] = chain

        # Verify cross-chain entanglement via proposal/agreement pairs
        for pubkey in pubkeys:
            blocks = self.store.get_chain(pubkey)
            for block in blocks:
                if block.block_type == "proposal":
                    linked = self.store.get_linked_block(block)
                    link = CrossChainLink(
                        pubkey_a=block.public_key,
                        seq_a=block.sequence_number,
                        pubkey_b=block.link_public_key,
                        seq_b=linked.sequence_number if linked else 0,
                        block_hash=block.block_hash,
                    )
                    if linked and verify_block(linked):
                        link.verified = True
                    else:
                        dag.orphan_proposals.append(
                            f"{block.public_key[:16]}... seq={block.sequence_number}"
                        )
                    dag.cross_links.append(link)

        return dag

    def detect_tampering(self) -> TamperingReport:
        """Analyze the block store for signs of tampering."""
        from trustchain.halfblock import GENESIS_HASH, verify_block

        report = TamperingReport()
        pubkeys = self.store.get_all_pubkeys()

        for pubkey in pubkeys:
            short = pubkey[:16]
            chain = self.store.get_chain(pubkey)

            for i, block in enumerate(chain):
                expected_seq = i + 1
                if block.sequence_number != expected_seq:
                    report.chain_gaps.append(
                        f"{short}...: gap at seq={block.sequence_number} "
                        f"(expected {expected_seq})"
                    )
                    break

                expected_prev = GENESIS_HASH if i == 0 else chain[i - 1].block_hash
                if block.previous_hash != expected_prev:
                    report.hash_breaks.append(
                        f"{short}...: hash break at seq={block.sequence_number}"
                    )
                    break

                if not verify_block(block):
                    report.signature_failures.append(
                        f"{short}...: signature failure at seq={block.sequence_number}"
                    )
                    break

        # Check for orphan proposals
        dag = self.build_dag()
        for orphan in dag.orphan_proposals:
            report.orphan_proposals.append(f"Orphan proposal: {orphan}")

        for link in dag.cross_links:
            if not link.verified:
                report.entanglement_issues.append(
                    f"{link.pubkey_a[:16]}... seq={link.seq_a} <-> "
                    f"{link.pubkey_b[:16]}... seq={link.seq_b}: unverified"
                )

        return report


class HTTPCrawler:
    """Crawl remote TrustChain nodes via HTTPS and store their blocks locally."""

    def __init__(self, store, client=None) -> None:
        from trustchain.blockstore import BlockStore
        self.store: BlockStore = store
        self._client = client

    async def _get_client(self):
        if self._client is None:
            from trustchain.api import TrustChainClient
            from trustchain.identity import Identity
            self._client = TrustChainClient(Identity())
        return self._client

    async def crawl_peer(
        self, peer_url: str, pubkey: str, start_seq: int = 1
    ) -> int:
        """Fetch and store blocks from a remote peer.

        Returns the number of new blocks stored.
        """
        from trustchain.halfblock import verify_block

        client = await self._get_client()
        blocks = await client.crawl_chain(peer_url, pubkey, start_seq)

        new_count = 0
        for block in blocks:
            if not verify_block(block):
                logger.warning(
                    "Ignoring invalid block from %s seq=%d",
                    pubkey[:16],
                    block.sequence_number,
                )
                continue
            try:
                self.store.add_block(block)
                new_count += 1
            except ValueError:
                pass  # Already stored

        return new_count

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


# ===========================================================================
# v1 compat crawler (InteractionRecord-based)
# ===========================================================================


class ChainCrawler:
    """Crawls interaction records to build DAG views and detect tampering.

    .. deprecated:: 2.0
        Use ``BlockStoreCrawler`` for v2 half-block model.
    """

    def __init__(self, records: List[InteractionRecord]):
        self.records = records

    def _discover_agents(self) -> Set[str]:
        agents: Set[str] = set()
        for r in self.records:
            agents.add(r.agent_a_pubkey)
            agents.add(r.agent_b_pubkey)
        return agents

    def build_dag(self, strict: bool = False) -> DAGView:
        """Build a DAGView from all records."""
        agents = self._discover_agents()
        dag = DAGView()

        for pubkey in agents:
            if strict:
                chain = PersonalChain.from_records(pubkey, self.records)
                chain.validate()
            else:
                chain = self._build_chain_lenient(pubkey)
            dag.chains[pubkey] = chain

        for r in self.records:
            link = CrossChainLink(
                pubkey_a=r.agent_a_pubkey,
                seq_a=r.seq_a,
                pubkey_b=r.agent_b_pubkey,
                seq_b=r.seq_b,
                block_hash=r.record_hash,
            )

            chain_b = dag.chains.get(r.agent_b_pubkey)
            chain_a = dag.chains.get(r.agent_a_pubkey)

            if chain_a and chain_b:
                block_in_a = chain_a.get_block(r.seq_a)
                block_in_b = chain_b.get_block(r.seq_b)

                if (
                    block_in_a is not None
                    and block_in_b is not None
                    and block_in_a.record.record_hash == r.record_hash
                    and block_in_b.record.record_hash == r.record_hash
                ):
                    link.verified = True

            dag.cross_links.append(link)

        return dag

    def _build_chain_lenient(self, pubkey: str) -> PersonalChain:
        chain = PersonalChain(pubkey)

        relevant: List[InteractionRecord] = []
        for r in self.records:
            if r.agent_a_pubkey == pubkey or r.agent_b_pubkey == pubkey:
                relevant.append(r)

        def _seq_for(record: InteractionRecord) -> int:
            if record.agent_a_pubkey == pubkey:
                return record.seq_a
            return record.seq_b

        relevant.sort(key=_seq_for)

        for r in relevant:
            try:
                chain.append(Block(r))
            except ChainError:
                pass

        return chain

    def detect_tampering(self) -> TamperingReport:
        """Analyze the DAG for signs of tampering."""
        report = TamperingReport()
        agents = self._discover_agents()

        for pubkey in agents:
            short = pubkey[:16]
            try:
                chain = PersonalChain.from_records(pubkey, self.records)
                chain.validate()
            except SequenceGapError as e:
                report.chain_gaps.append(
                    f"{short}...: gap at seq={e.got} (expected {e.expected})"
                )
            except PrevHashMismatchError as e:
                report.hash_breaks.append(
                    f"{short}...: hash break at seq={e.seq}"
                )
            except SignatureError as e:
                report.signature_failures.append(
                    f"{short}...: signature failure at seq={e.seq}"
                )
            except ChainError as e:
                report.chain_gaps.append(f"{short}...: {e}")

        dag = self.build_dag(strict=False)
        for link in dag.cross_links:
            if not link.verified:
                report.entanglement_issues.append(
                    f"{link.pubkey_a[:16]}... seq={link.seq_a} <-> "
                    f"{link.pubkey_b[:16]}... seq={link.seq_b}: unverified"
                )

        return report
