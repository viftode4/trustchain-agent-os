//! Core type definitions and constants for the TrustChain protocol.

use serde::{Deserialize, Serialize};
use std::fmt;

/// Hash of the imaginary block 0 — used as `previous_hash` for the first block in a chain.
pub const GENESIS_HASH: &str =
    "0000000000000000000000000000000000000000000000000000000000000000";

/// First valid sequence number.
pub const GENESIS_SEQ: u64 = 1;

/// Unknown/unlinked sequence number (used in proposals before the responder replies).
pub const UNKNOWN_SEQ: u64 = 0;

// ---------------------------------------------------------------------------
// ValidationResult — tiered validation matching py-ipv8
// ---------------------------------------------------------------------------

/// Tiered validation result matching py-ipv8's ValidationResult.
///
/// Validation levels, from most to least confidence:
/// - `Valid` — block and full chain context verified
/// - `PartialNext` — valid but we don't know (or have gaps after) the next block
/// - `PartialPrevious` — valid but we don't know the previous block
/// - `Partial` — valid but we have gaps on both sides
/// - `NoInfo` — we have no chain context for this public key at all
/// - `Invalid` — the block violates at least one invariant
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ValidationResult {
    Valid,
    PartialNext,
    PartialPrevious,
    Partial,
    NoInfo,
    Invalid(Vec<String>),
}

impl ValidationResult {
    pub fn is_valid(&self) -> bool {
        !matches!(self, ValidationResult::Invalid(_))
    }

    pub fn is_fully_valid(&self) -> bool {
        matches!(self, ValidationResult::Valid)
    }

    pub fn errors(&self) -> &[String] {
        match self {
            ValidationResult::Invalid(errs) => errs,
            _ => &[],
        }
    }
}

/// Block types in the TrustChain protocol.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BlockType {
    /// Initiator's half-block (link_sequence_number = 0).
    Proposal,
    /// Responder's half-block (links back to proposal).
    Agreement,
    /// Consensus checkpoint block (self-referencing).
    Checkpoint,
}

impl fmt::Display for BlockType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BlockType::Proposal => write!(f, "proposal"),
            BlockType::Agreement => write!(f, "agreement"),
            BlockType::Checkpoint => write!(f, "checkpoint"),
        }
    }
}

impl BlockType {
    /// Parse from string (case-insensitive).
    pub fn from_str_loose(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "proposal" => Some(BlockType::Proposal),
            "agreement" => Some(BlockType::Agreement),
            "checkpoint" => Some(BlockType::Checkpoint),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_genesis_hash_length() {
        assert_eq!(GENESIS_HASH.len(), 64);
        assert!(GENESIS_HASH.chars().all(|c| c == '0'));
    }

    #[test]
    fn test_block_type_display() {
        assert_eq!(BlockType::Proposal.to_string(), "proposal");
        assert_eq!(BlockType::Agreement.to_string(), "agreement");
        assert_eq!(BlockType::Checkpoint.to_string(), "checkpoint");
    }

    #[test]
    fn test_block_type_serde() {
        let json = serde_json::to_string(&BlockType::Proposal).unwrap();
        assert_eq!(json, "\"proposal\"");

        let parsed: BlockType = serde_json::from_str("\"agreement\"").unwrap();
        assert_eq!(parsed, BlockType::Agreement);
    }

    #[test]
    fn test_block_type_from_str_loose() {
        assert_eq!(BlockType::from_str_loose("Proposal"), Some(BlockType::Proposal));
        assert_eq!(BlockType::from_str_loose("AGREEMENT"), Some(BlockType::Agreement));
        assert_eq!(BlockType::from_str_loose("checkpoint"), Some(BlockType::Checkpoint));
        assert_eq!(BlockType::from_str_loose("invalid"), None);
    }

    #[test]
    fn test_validation_result_valid() {
        let r = ValidationResult::Valid;
        assert!(r.is_valid());
        assert!(r.is_fully_valid());
        assert!(r.errors().is_empty());
    }

    #[test]
    fn test_validation_result_partial() {
        let r = ValidationResult::Partial;
        assert!(r.is_valid());
        assert!(!r.is_fully_valid());
    }

    #[test]
    fn test_validation_result_invalid() {
        let r = ValidationResult::Invalid(vec!["bad sig".to_string()]);
        assert!(!r.is_valid());
        assert!(!r.is_fully_valid());
        assert_eq!(r.errors().len(), 1);
    }

    #[test]
    fn test_constants() {
        assert_eq!(GENESIS_SEQ, 1);
        assert_eq!(UNKNOWN_SEQ, 0);
        assert_eq!(GENESIS_HASH.len(), 64);
    }
}
