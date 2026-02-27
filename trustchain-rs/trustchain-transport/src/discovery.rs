//! Peer discovery — bootstrap, random walk, and gossip-based peer finding.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use tokio::sync::RwLock;

/// Information about a known peer.
#[derive(Debug, Clone)]
pub struct PeerRecord {
    pub pubkey: String,
    pub address: String,
    pub latest_seq: u64,
    pub last_seen: Instant,
    pub is_bootstrap: bool,
}

/// Peer discovery service.
#[derive(Debug, Clone)]
pub struct PeerDiscovery {
    /// Known peers by public key.
    peers: Arc<RwLock<HashMap<String, PeerRecord>>>,
    /// Bootstrap nodes to connect to initially.
    bootstrap_nodes: Vec<String>,
    /// Our own public key.
    our_pubkey: String,
}

impl PeerDiscovery {
    pub fn new(our_pubkey: String, bootstrap_nodes: Vec<String>) -> Self {
        Self {
            peers: Arc::new(RwLock::new(HashMap::new())),
            bootstrap_nodes,
            our_pubkey,
        }
    }

    /// Register a peer we've discovered.
    pub async fn add_peer(&self, pubkey: String, address: String, latest_seq: u64) {
        if pubkey == self.our_pubkey {
            return; // Don't add ourselves.
        }
        let mut peers = self.peers.write().await;
        let entry = peers.entry(pubkey.clone()).or_insert_with(|| PeerRecord {
            pubkey: pubkey.clone(),
            address: address.clone(),
            latest_seq,
            last_seen: Instant::now(),
            is_bootstrap: self.bootstrap_nodes.contains(&address),
        });
        entry.address = address;
        entry.latest_seq = latest_seq;
        entry.last_seen = Instant::now();
    }

    /// Get all known peers.
    pub async fn get_peers(&self) -> Vec<PeerRecord> {
        self.peers.read().await.values().cloned().collect()
    }

    /// Get a specific peer by public key.
    pub async fn get_peer(&self, pubkey: &str) -> Option<PeerRecord> {
        self.peers.read().await.get(pubkey).cloned()
    }

    /// Look up a peer by their HTTP address (e.g. "127.0.0.1:8202" or "http://127.0.0.1:8202").
    /// Used by the proxy to check whether an outbound call targets a known TC peer.
    pub async fn get_peer_by_address(&self, address: &str) -> Option<PeerRecord> {
        let needle = address.strip_prefix("http://").unwrap_or(address);
        self.peers.read().await.values().find(|p| {
            let peer_addr = p.address.strip_prefix("http://").unwrap_or(&p.address);
            peer_addr == needle
        }).cloned()
    }

    /// Get the number of known peers.
    pub async fn peer_count(&self) -> usize {
        self.peers.read().await.len()
    }

    /// Remove a peer.
    pub async fn remove_peer(&self, pubkey: &str) {
        self.peers.write().await.remove(pubkey);
    }

    /// Get bootstrap node addresses.
    pub fn bootstrap_addresses(&self) -> &[String] {
        &self.bootstrap_nodes
    }

    /// Get peer addresses for gossip exchange.
    pub async fn get_gossip_peers(&self, max_count: usize) -> Vec<PeerRecord> {
        let peers = self.peers.read().await;
        let mut list: Vec<PeerRecord> = peers.values().cloned().collect();
        // Sort by most recently seen first.
        list.sort_by(|a, b| a.last_seen.elapsed().cmp(&b.last_seen.elapsed()));
        list.truncate(max_count);
        list
    }

    /// Merge peers received from another node.
    pub async fn merge_peers(&self, incoming: Vec<(String, String, u64)>) {
        for (pubkey, address, seq) in incoming {
            self.add_peer(pubkey, address, seq).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_add_and_get_peer() {
        let disc = PeerDiscovery::new("us".to_string(), vec![]);
        disc.add_peer("peer1".to_string(), "127.0.0.1:8200".to_string(), 5).await;

        let peer = disc.get_peer("peer1").await.unwrap();
        assert_eq!(peer.address, "127.0.0.1:8200");
        assert_eq!(peer.latest_seq, 5);
    }

    #[tokio::test]
    async fn test_dont_add_self() {
        let disc = PeerDiscovery::new("us".to_string(), vec![]);
        disc.add_peer("us".to_string(), "127.0.0.1:8200".to_string(), 0).await;
        assert_eq!(disc.peer_count().await, 0);
    }

    #[tokio::test]
    async fn test_peer_count() {
        let disc = PeerDiscovery::new("us".to_string(), vec![]);
        disc.add_peer("a".to_string(), "addr1".to_string(), 0).await;
        disc.add_peer("b".to_string(), "addr2".to_string(), 0).await;
        assert_eq!(disc.peer_count().await, 2);
    }

    #[tokio::test]
    async fn test_remove_peer() {
        let disc = PeerDiscovery::new("us".to_string(), vec![]);
        disc.add_peer("a".to_string(), "addr1".to_string(), 0).await;
        disc.remove_peer("a").await;
        assert_eq!(disc.peer_count().await, 0);
    }

    #[tokio::test]
    async fn test_merge_peers() {
        let disc = PeerDiscovery::new("us".to_string(), vec![]);
        disc.merge_peers(vec![
            ("a".to_string(), "addr1".to_string(), 1),
            ("b".to_string(), "addr2".to_string(), 2),
        ]).await;
        assert_eq!(disc.peer_count().await, 2);
    }

    #[tokio::test]
    async fn test_gossip_peers_limit() {
        let disc = PeerDiscovery::new("us".to_string(), vec![]);
        for i in 0..10 {
            disc.add_peer(format!("p{i}"), format!("addr{i}"), 0).await;
        }
        let gossip = disc.get_gossip_peers(3).await;
        assert_eq!(gossip.len(), 3);
    }
}
