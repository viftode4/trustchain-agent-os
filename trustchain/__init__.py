# === Identity ===
from trustchain.identity import Identity

# === v1 compat (deprecated) ===
from trustchain.record import InteractionRecord, create_record, verify_record
from trustchain.store import FileRecordStore, RecordStore
from trustchain.block import Block
from trustchain.block import HalfBlock as V1HalfBlock  # v1 read-only projection
from trustchain.block import GENESIS_HASH

# === v2 core ===
from trustchain.halfblock import (
    HalfBlock,
    BlockType,
    compute_block_hash,
    create_half_block,
    sign_block,
    verify_block,
)
from trustchain.halfblock import GENESIS_HASH  # re-export (same value)
from trustchain.blockstore import BlockStore, MemoryBlockStore, SQLiteBlockStore
from trustchain.protocol import TrustChainProtocol

# === Chain & validation ===
from trustchain.chain import PersonalChain, compute_chain_integrity, validate_chain_for

# === Trust scoring ===
from trustchain.trust import (
    TrustEngine,
    # v1 compat
    compute_chain_trust,
    compute_trust,
    compute_transitive_trust,
    compute_trust_with_decay,
    is_sybil_cluster,
)

# === Crawling & DAG ===
from trustchain.crawler import (
    BlockStoreCrawler,
    ChainCrawler,
    CrossChainLink,
    DAGView,
    HTTPCrawler,
    TamperingReport,
)

# === NetFlow (Sybil resistance) ===
from trustchain.netflow import NetFlowTrust

# === Transport Layer ===
from trustchain.transport.base import MessageType, Transport, TransportError, TransportMessage
from trustchain.transport.http import HTTPTransport

# === HTTPS Transport ===
from trustchain.api import TrustChainClient, TrustChainNode

# === Consensus ===
from trustchain.consensus import CHECOConsensus

# === Exceptions ===
from trustchain.exceptions import (
    AgreementError,
    ChainError,
    CheckpointError,
    DuplicateSequenceError,
    EntanglementError,
    InvalidBlockError,
    NetFlowError,
    OrphanBlockError,
    PrevHashMismatchError,
    ProposalError,
    SequenceGapError,
    SignatureError,
)
