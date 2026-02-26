//! Transport message types for TrustChain networking.

use serde::{Deserialize, Serialize};
use trustchain_core::HalfBlock;

/// Types of messages that can be sent between nodes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum MessageType {
    Proposal,
    Agreement,
    CrawlRequest,
    CrawlResponse,
    StatusRequest,
    StatusResponse,
    DiscoveryRequest,
    DiscoveryResponse,
    Ping,
    Pong,
}

impl std::fmt::Display for MessageType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MessageType::Proposal => write!(f, "proposal"),
            MessageType::Agreement => write!(f, "agreement"),
            MessageType::CrawlRequest => write!(f, "crawl_request"),
            MessageType::CrawlResponse => write!(f, "crawl_response"),
            MessageType::StatusRequest => write!(f, "status_request"),
            MessageType::StatusResponse => write!(f, "status_response"),
            MessageType::DiscoveryRequest => write!(f, "discovery_request"),
            MessageType::DiscoveryResponse => write!(f, "discovery_response"),
            MessageType::Ping => write!(f, "ping"),
            MessageType::Pong => write!(f, "pong"),
        }
    }
}

/// A transport message envelope.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransportMessage {
    pub message_type: MessageType,
    pub sender_pubkey: String,
    pub payload: Vec<u8>,
    pub request_id: String,
}

impl TransportMessage {
    pub fn new(
        message_type: MessageType,
        sender_pubkey: String,
        payload: Vec<u8>,
        request_id: String,
    ) -> Self {
        Self {
            message_type,
            sender_pubkey,
            payload,
            request_id,
        }
    }
}

/// Convert HalfBlock to/from protobuf wire format (JSON for now).
pub fn block_to_bytes(block: &HalfBlock) -> Vec<u8> {
    serde_json::to_vec(block).expect("HalfBlock serialization cannot fail")
}

pub fn bytes_to_block(bytes: &[u8]) -> Result<HalfBlock, serde_json::Error> {
    serde_json::from_slice(bytes)
}

/// Serialize a list of blocks.
pub fn blocks_to_bytes(blocks: &[HalfBlock]) -> Vec<u8> {
    serde_json::to_vec(blocks).expect("Vec<HalfBlock> serialization cannot fail")
}

pub fn bytes_to_blocks(bytes: &[u8]) -> Result<Vec<HalfBlock>, serde_json::Error> {
    serde_json::from_slice(bytes)
}
