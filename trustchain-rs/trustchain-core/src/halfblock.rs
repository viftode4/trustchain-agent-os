//! HalfBlock — the fundamental data structure of TrustChain.
//!
//! Each interaction produces two half-blocks: a proposal and an agreement.
//! Together they form a bilateral, cryptographically signed record.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::error::{Result, TrustChainError};
use crate::identity::Identity;
use crate::types::BlockType;

/// A half-block in the TrustChain protocol.
///
/// Each agent maintains a personal chain of half-blocks. A complete interaction
/// consists of a proposal half-block (from initiator) and an agreement half-block
/// (from responder), linked by cross-chain pointers.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HalfBlock {
    /// Public key of the block creator (64 hex chars).
    pub public_key: String,
    /// Sequence number in the creator's personal chain (1-based).
    pub sequence_number: u64,
    /// Public key of the counterparty (64 hex chars).
    pub link_public_key: String,
    /// Counterparty's sequence number: 0 for proposals, >0 for agreements.
    pub link_sequence_number: u64,
    /// Hash of the previous block in this agent's chain (or GENESIS_HASH).
    pub previous_hash: String,
    /// Ed25519 signature over the block hash (128 hex chars, empty before signing).
    pub signature: String,
    /// Block type: proposal, agreement, or checkpoint.
    pub block_type: String,
    /// Application-level transaction payload.
    pub transaction: serde_json::Value,
    /// SHA-256 hash of the block (64 hex chars).
    pub block_hash: String,
    /// Unix timestamp (seconds since epoch, with fractional part).
    pub timestamp: f64,
}

impl HalfBlock {
    /// Compute the SHA-256 hash of this block.
    ///
    /// The hash is computed over a JSON-canonical representation with `signature` set
    /// to an empty string and keys sorted alphabetically. This matches the Python
    /// implementation for wire compatibility.
    pub fn compute_hash(&self) -> String {
        // Build the hashable map with sorted keys (BTreeMap is sorted).
        let mut map = BTreeMap::new();
        map.insert("block_type", serde_json::Value::String(self.block_type.clone()));
        map.insert(
            "link_public_key",
            serde_json::Value::String(self.link_public_key.clone()),
        );
        map.insert(
            "link_sequence_number",
            serde_json::json!(self.link_sequence_number),
        );
        map.insert(
            "previous_hash",
            serde_json::Value::String(self.previous_hash.clone()),
        );
        map.insert(
            "public_key",
            serde_json::Value::String(self.public_key.clone()),
        );
        map.insert(
            "sequence_number",
            serde_json::json!(self.sequence_number),
        );
        // Signature is always empty string for hashing.
        map.insert("signature", serde_json::Value::String(String::new()));
        map.insert(
            "timestamp",
            serde_json::json!(self.timestamp),
        );
        map.insert("transaction", self.transaction.clone());

        // Compact JSON with sorted keys (BTreeMap guarantees order).
        let payload = serde_json::to_string(&map).expect("BTreeMap serialization cannot fail");
        let hash = Sha256::digest(payload.as_bytes());
        hex::encode(hash)
    }

    /// Verify that the stored `block_hash` matches a fresh computation.
    pub fn verify_hash(&self) -> bool {
        self.block_hash == self.compute_hash()
    }

    /// Verify the Ed25519 signature over the block hash.
    pub fn verify_signature(&self) -> Result<bool> {
        if self.signature.is_empty() {
            return Ok(false);
        }
        Identity::verify_hex(
            self.block_hash.as_bytes(),
            &self.signature,
            &self.public_key,
        )
    }

    /// Full verification: hash integrity + signature validity.
    pub fn verify(&self) -> Result<bool> {
        if !self.verify_hash() {
            return Ok(false);
        }
        self.verify_signature()
    }

    /// Get the block type as the typed enum.
    pub fn block_type_enum(&self) -> Option<BlockType> {
        BlockType::from_str_loose(&self.block_type)
    }

    /// Check if this is a proposal block.
    pub fn is_proposal(&self) -> bool {
        self.block_type == "proposal"
    }

    /// Check if this is an agreement block.
    pub fn is_agreement(&self) -> bool {
        self.block_type == "agreement"
    }

    /// Check if this is a checkpoint block.
    pub fn is_checkpoint(&self) -> bool {
        self.block_type == "checkpoint"
    }
}

impl std::fmt::Display for HalfBlock {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "HalfBlock({} seq={} type={} link={}:{})",
            &self.public_key[..8],
            self.sequence_number,
            self.block_type,
            &self.link_public_key[..std::cmp::min(8, self.link_public_key.len())],
            self.link_sequence_number,
        )
    }
}

/// Get the current Unix timestamp as f64.
pub fn now_timestamp() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock before UNIX epoch")
        .as_secs_f64()
}

/// Create, hash, and sign a half-block in one call.
pub fn create_half_block(
    identity: &Identity,
    sequence_number: u64,
    link_public_key: &str,
    link_sequence_number: u64,
    previous_hash: &str,
    block_type: BlockType,
    transaction: serde_json::Value,
    timestamp: Option<f64>,
) -> HalfBlock {
    let mut block = HalfBlock {
        public_key: identity.pubkey_hex(),
        sequence_number,
        link_public_key: link_public_key.to_string(),
        link_sequence_number,
        previous_hash: previous_hash.to_string(),
        signature: String::new(),
        block_type: block_type.to_string(),
        transaction,
        block_hash: String::new(),
        timestamp: timestamp.unwrap_or_else(now_timestamp),
    };

    // Compute hash (with signature="").
    block.block_hash = block.compute_hash();

    // Sign the hash.
    block.signature = identity.sign_hex(block.block_hash.as_bytes());

    block
}

/// Verify an existing block: recompute hash and check signature.
pub fn verify_block(block: &HalfBlock) -> Result<bool> {
    let recomputed = block.compute_hash();
    if recomputed != block.block_hash {
        return Err(TrustChainError::signature(
            &block.public_key,
            block.sequence_number,
            format!(
                "hash mismatch: computed {}, stored {}",
                recomputed, block.block_hash
            ),
        ));
    }
    block.verify_signature()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::GENESIS_HASH;

    fn test_identity() -> Identity {
        Identity::from_bytes(&[1u8; 32])
    }

    #[test]
    fn test_create_and_verify_block() {
        let id = test_identity();
        let block = create_half_block(
            &id,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({"service": "compute"}),
            Some(1000.0),
        );

        assert_eq!(block.public_key, id.pubkey_hex());
        assert_eq!(block.sequence_number, 1);
        assert_eq!(block.block_type, "proposal");
        assert!(!block.signature.is_empty());
        assert!(!block.block_hash.is_empty());
        assert_eq!(block.block_hash.len(), 64);
        assert_eq!(block.signature.len(), 128);
    }

    #[test]
    fn test_verify_block_ok() {
        let id = test_identity();
        let block = create_half_block(
            &id,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({}),
            Some(1000.0),
        );

        assert!(verify_block(&block).unwrap());
    }

    #[test]
    fn test_tampered_block_fails_verification() {
        let id = test_identity();
        let mut block = create_half_block(
            &id,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({}),
            Some(1000.0),
        );

        // Tamper with the transaction.
        block.transaction = serde_json::json!({"tampered": true});

        assert!(verify_block(&block).is_err());
    }

    #[test]
    fn test_hash_determinism() {
        let id = test_identity();
        let block = create_half_block(
            &id,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({"key": "value"}),
            Some(1000.0),
        );

        // Hash should be the same when recomputed.
        assert_eq!(block.compute_hash(), block.block_hash);
        assert_eq!(block.compute_hash(), block.compute_hash());
    }

    #[test]
    fn test_different_transactions_different_hashes() {
        let id = test_identity();
        let block1 = create_half_block(
            &id,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({"a": 1}),
            Some(1000.0),
        );
        let block2 = create_half_block(
            &id,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({"b": 2}),
            Some(1000.0),
        );

        assert_ne!(block1.block_hash, block2.block_hash);
    }

    #[test]
    fn test_wrong_signer_fails() {
        let id1 = test_identity();
        let id2 = Identity::from_bytes(&[2u8; 32]);

        let mut block = create_half_block(
            &id1,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({}),
            Some(1000.0),
        );

        // Replace public key but keep id1's signature.
        block.public_key = id2.pubkey_hex();
        block.block_hash = block.compute_hash();
        // Signature was made by id1, but block claims id2.
        let valid = block.verify_signature().unwrap();
        assert!(!valid);
    }

    #[test]
    fn test_block_display() {
        let id = test_identity();
        let block = create_half_block(
            &id,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({}),
            Some(1000.0),
        );
        let s = format!("{block}");
        assert!(s.contains("seq=1"));
        assert!(s.contains("proposal"));
    }

    #[test]
    fn test_block_type_checks() {
        let id = test_identity();

        let proposal = create_half_block(
            &id, 1, &"b".repeat(64), 0, GENESIS_HASH,
            BlockType::Proposal, serde_json::json!({}), Some(1000.0),
        );
        assert!(proposal.is_proposal());
        assert!(!proposal.is_agreement());
        assert!(!proposal.is_checkpoint());

        let agreement = create_half_block(
            &id, 2, &"b".repeat(64), 1, &proposal.block_hash,
            BlockType::Agreement, serde_json::json!({}), Some(1001.0),
        );
        assert!(agreement.is_agreement());
        assert!(!agreement.is_proposal());
    }

    #[test]
    fn test_genesis_hash_as_previous() {
        let id = test_identity();
        let block = create_half_block(
            &id,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({}),
            Some(1000.0),
        );
        assert_eq!(block.previous_hash, GENESIS_HASH);
        assert!(verify_block(&block).unwrap());
    }

    #[test]
    fn test_serde_roundtrip() {
        let id = test_identity();
        let block = create_half_block(
            &id,
            1,
            &"b".repeat(64),
            0,
            GENESIS_HASH,
            BlockType::Proposal,
            serde_json::json!({"nested": {"key": "val"}}),
            Some(1000.0),
        );

        let json = serde_json::to_string(&block).unwrap();
        let parsed: HalfBlock = serde_json::from_str(&json).unwrap();
        assert_eq!(block, parsed);
        assert!(verify_block(&parsed).unwrap());
    }
}
