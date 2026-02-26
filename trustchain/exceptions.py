"""Chain validation error hierarchy for TrustChain."""

from __future__ import annotations


class ChainError(Exception):
    """Base error for all chain validation failures."""

    def __init__(self, message: str, pubkey: str = "", seq: int = -1):
        self.pubkey = pubkey
        self.seq = seq
        super().__init__(message)


class SequenceGapError(ChainError):
    """A block's sequence number doesn't follow contiguously from the chain head."""

    def __init__(self, pubkey: str, expected: int, got: int):
        self.expected = expected
        self.got = got
        super().__init__(
            f"Sequence gap for {pubkey[:16]}...: expected seq={expected}, got seq={got}",
            pubkey=pubkey,
            seq=got,
        )


class PrevHashMismatchError(ChainError):
    """A block's prev_hash doesn't match the hash of the preceding block."""

    def __init__(self, pubkey: str, seq: int, expected: str, got: str):
        self.expected = expected
        self.got = got
        super().__init__(
            f"Prev-hash mismatch for {pubkey[:16]}... at seq={seq}: "
            f"expected {expected[:16]}..., got {got[:16]}...",
            pubkey=pubkey,
            seq=seq,
        )


class SignatureError(ChainError):
    """One or both signatures on a block failed verification."""

    def __init__(self, pubkey: str, seq: int, detail: str = ""):
        self.detail = detail
        super().__init__(
            f"Signature verification failed for {pubkey[:16]}... at seq={seq}"
            + (f": {detail}" if detail else ""),
            pubkey=pubkey,
            seq=seq,
        )


class DuplicateSequenceError(ChainError):
    """Two blocks claim the same sequence number in one agent's chain."""

    def __init__(self, pubkey: str, seq: int):
        super().__init__(
            f"Duplicate sequence number for {pubkey[:16]}...: seq={seq}",
            pubkey=pubkey,
            seq=seq,
        )


class EntanglementError(ChainError):
    """Cross-chain entanglement verification failed.

    Agent A's block references agent B at a given sequence, but B's chain
    at that sequence doesn't reference A back.
    """

    def __init__(self, pubkey_a: str, seq_a: int, pubkey_b: str, seq_b: int):
        self.pubkey_a = pubkey_a
        self.seq_a = seq_a
        self.pubkey_b = pubkey_b
        self.seq_b = seq_b
        super().__init__(
            f"Entanglement mismatch: {pubkey_a[:16]}... seq={seq_a} references "
            f"{pubkey_b[:16]}... seq={seq_b}, but counterparty doesn't confirm",
            pubkey=pubkey_a,
            seq=seq_a,
        )


class InvalidBlockError(ChainError):
    """A block is structurally invalid (missing fields, wrong agent, etc.)."""

    def __init__(self, pubkey: str, seq: int, detail: str = ""):
        self.detail = detail
        super().__init__(
            f"Invalid block for {pubkey[:16]}... at seq={seq}"
            + (f": {detail}" if detail else ""),
            pubkey=pubkey,
            seq=seq,
        )


# --- v2 errors (half-block protocol) ---


class ProposalError(ChainError):
    """Error creating or validating a proposal half-block."""

    def __init__(self, pubkey: str, seq: int = -1, detail: str = ""):
        self.detail = detail
        super().__init__(
            f"Proposal error for {pubkey[:16]}... at seq={seq}"
            + (f": {detail}" if detail else ""),
            pubkey=pubkey,
            seq=seq,
        )


class AgreementError(ChainError):
    """Error creating or validating an agreement half-block."""

    def __init__(self, pubkey: str, seq: int = -1, detail: str = ""):
        self.detail = detail
        super().__init__(
            f"Agreement error for {pubkey[:16]}... at seq={seq}"
            + (f": {detail}" if detail else ""),
            pubkey=pubkey,
            seq=seq,
        )


class OrphanBlockError(ChainError):
    """A proposal half-block has no matching agreement."""

    def __init__(self, pubkey: str, seq: int):
        super().__init__(
            f"Orphan proposal for {pubkey[:16]}... at seq={seq}: no matching agreement",
            pubkey=pubkey,
            seq=seq,
        )


class CheckpointError(ChainError):
    """Error in checkpoint consensus (CHECO)."""

    def __init__(self, detail: str = "", pubkey: str = "", seq: int = -1):
        super().__init__(
            f"Checkpoint error" + (f": {detail}" if detail else ""),
            pubkey=pubkey,
            seq=seq,
        )


class NetFlowError(ChainError):
    """Error in NetFlow trust computation."""

    def __init__(self, detail: str = "", pubkey: str = ""):
        super().__init__(
            f"NetFlow error" + (f": {detail}" if detail else ""),
            pubkey=pubkey,
            seq=-1,
        )
