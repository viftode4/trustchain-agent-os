//! Core type definitions and constants for the TrustChain protocol.

use serde::{Deserialize, Serialize};
use std::fmt;

/// Hash of the imaginary block 0 — used as `previous_hash` for the first block in a chain.
pub const GENESIS_HASH: &str =
    "0000000000000000000000000000000000000000000000000000000000000000";

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
}
