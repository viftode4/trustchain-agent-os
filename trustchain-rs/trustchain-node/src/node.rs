//! Node — wires together protocol, storage, and all transports.

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::{mpsc, Mutex};

use trustchain_core::{
    BlockStore, HalfBlock, Identity, SqliteBlockStore, TrustChainProtocol,
};
use trustchain_transport::{
    AppState, ConnectionPool, PeerDiscovery, QuicTransport,
    start_grpc_server, start_http_server,
    message::{MessageType, TransportMessage, block_to_bytes, bytes_to_block},
};

use crate::config::NodeConfig;

/// A running TrustChain node.
pub struct Node {
    pub identity: Identity,
    pub config: NodeConfig,
    pub protocol: Arc<Mutex<TrustChainProtocol<SqliteBlockStore>>>,
    pub discovery: Arc<PeerDiscovery>,
    pub pool: Arc<ConnectionPool>,
}

impl Node {
    /// Create a new node from configuration.
    pub fn new(identity: Identity, config: NodeConfig) -> Self {
        let db_path = config.db_path.to_str().unwrap_or("trustchain.db");
        let store = SqliteBlockStore::open(db_path)
            .expect("failed to open SQLite database");
        let protocol = TrustChainProtocol::new(identity.clone(), store);
        let discovery = PeerDiscovery::new(
            identity.pubkey_hex(),
            config.bootstrap_nodes.clone(),
        );
        let pool = ConnectionPool::default();

        Self {
            identity,
            config,
            protocol: Arc::new(Mutex::new(protocol)),
            discovery: Arc::new(discovery),
            pool: Arc::new(pool),
        }
    }

    /// Start all node services (QUIC, gRPC, HTTP, discovery).
    pub async fn run(&self) -> anyhow::Result<()> {
        let pubkey = self.identity.pubkey_hex();
        tracing::info!(
            pubkey = &pubkey[..8],
            "starting TrustChain node"
        );

        // Start QUIC transport.
        let quic_addr: SocketAddr = self.config.quic_addr.parse()?;
        let quic = QuicTransport::bind(quic_addr, &pubkey).await
            .map_err(|e| anyhow::anyhow!("QUIC bind failed: {e}"))?;
        let quic_local = quic.local_addr()
            .map_err(|e| anyhow::anyhow!("QUIC local addr: {e}"))?;
        tracing::info!(%quic_local, "QUIC transport ready");

        // Start QUIC accept loop with message routing.
        let (quic_tx, quic_rx) = mpsc::channel::<(Vec<u8>, mpsc::Sender<Vec<u8>>)>(256);
        let quic_accept_handle = {
            let quic = Arc::new(quic);
            let q = quic.clone();
            let handle = tokio::spawn(async move {
                if let Err(e) = q.accept_loop(quic_tx).await {
                    tracing::error!("QUIC accept loop error: {e}");
                }
            });
            // Spawn the message router for QUIC.
            let protocol = self.protocol.clone();
            let discovery = self.discovery.clone();
            tokio::spawn(Self::quic_message_router(quic_rx, protocol, discovery));
            (handle, quic)
        };
        tracing::info!("QUIC message router started");

        // Start gRPC service.
        let grpc_addr: SocketAddr = self.config.grpc_addr.parse()?;
        let grpc_protocol = self.protocol.clone();
        let grpc_discovery = self.discovery.clone();
        let grpc_handle = tokio::spawn(async move {
            if let Err(e) = start_grpc_server(grpc_addr, grpc_protocol, grpc_discovery).await {
                tracing::error!("gRPC server error: {e}");
            }
        });
        tracing::info!(%grpc_addr, "gRPC service ready");

        // Start HTTP REST API — with QUIC transport for P2P proposal flow.
        let http_addr: SocketAddr = self.config.http_addr.parse()?;
        let http_state = AppState {
            protocol: self.protocol.clone(),
            discovery: self.discovery.clone(),
            quic: Some(quic_accept_handle.1.clone()),
        };
        let http_handle = tokio::spawn(async move {
            if let Err(e) = start_http_server(http_addr, http_state).await {
                tracing::error!("HTTP server error: {e}");
            }
        });
        tracing::info!(%http_addr, "HTTP API ready");

        // Start peer discovery bootstrap + gossip.
        let disc = self.discovery.clone();
        let disc_protocol = self.protocol.clone();
        let bootstrap_nodes = self.config.bootstrap_nodes.clone();
        let discovery_handle = tokio::spawn(async move {
            Self::discovery_loop(disc, disc_protocol, bootstrap_nodes).await;
        });
        tracing::info!("peer discovery started");

        // Start connection pool cleanup task.
        let pool = self.pool.clone();
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(60)).await;
                pool.cleanup().await;
            }
        });

        tracing::info!(
            pubkey = &pubkey[..8],
            quic = %quic_local,
            grpc = %grpc_addr,
            http = %http_addr,
            "node fully started"
        );

        // Wait for any service to exit.
        tokio::select! {
            _ = grpc_handle => tracing::warn!("gRPC server exited"),
            _ = http_handle => tracing::warn!("HTTP server exited"),
            _ = quic_accept_handle.0 => tracing::warn!("QUIC accept loop exited"),
            _ = discovery_handle => tracing::warn!("discovery loop exited"),
        }

        quic_accept_handle.1.shutdown();
        Ok(())
    }

    /// Route incoming QUIC messages to the protocol engine.
    async fn quic_message_router(
        mut rx: mpsc::Receiver<(Vec<u8>, mpsc::Sender<Vec<u8>>)>,
        protocol: Arc<Mutex<TrustChainProtocol<SqliteBlockStore>>>,
        discovery: Arc<PeerDiscovery>,
    ) {
        while let Some((data, resp_tx)) = rx.recv().await {
            let protocol = protocol.clone();
            let discovery = discovery.clone();
            tokio::spawn(async move {
                let response = Self::handle_quic_message(&data, &protocol, &discovery).await;
                let _ = resp_tx.send(response).await;
            });
        }
    }

    /// Handle a single incoming QUIC message.
    async fn handle_quic_message(
        data: &[u8],
        protocol: &Arc<Mutex<TrustChainProtocol<SqliteBlockStore>>>,
        discovery: &Arc<PeerDiscovery>,
    ) -> Vec<u8> {
        // Try to deserialize as TransportMessage.
        let msg: TransportMessage = match serde_json::from_slice(data) {
            Ok(m) => m,
            Err(e) => {
                tracing::warn!("invalid QUIC message: {e}");
                return serde_json::to_vec(&serde_json::json!({
                    "error": format!("invalid message: {e}")
                })).unwrap_or_default();
            }
        };

        match msg.message_type {
            MessageType::Proposal => {
                // Deserialize the proposal block from payload.
                let proposal: HalfBlock = match bytes_to_block(&msg.payload) {
                    Ok(b) => b,
                    Err(e) => {
                        return Self::error_response(&format!("invalid proposal: {e}"));
                    }
                };

                let mut proto = protocol.lock().await;

                // Receive and validate.
                if let Err(e) = proto.receive_proposal(&proposal) {
                    return Self::error_response(&format!("proposal rejected: {e}"));
                }

                // Create agreement.
                match proto.create_agreement(&proposal, None) {
                    Ok(agreement) => {
                        let resp = TransportMessage::new(
                            MessageType::Agreement,
                            proto.pubkey(),
                            block_to_bytes(&agreement),
                            msg.request_id,
                        );
                        serde_json::to_vec(&resp).unwrap_or_default()
                    }
                    Err(e) => Self::error_response(&format!("agreement failed: {e}")),
                }
            }

            MessageType::Agreement => {
                // Deserialize agreement block.
                let agreement: HalfBlock = match bytes_to_block(&msg.payload) {
                    Ok(b) => b,
                    Err(e) => {
                        return Self::error_response(&format!("invalid agreement: {e}"));
                    }
                };

                let mut proto = protocol.lock().await;
                match proto.receive_agreement(&agreement) {
                    Ok(_) => {
                        serde_json::to_vec(&serde_json::json!({"accepted": true}))
                            .unwrap_or_default()
                    }
                    Err(e) => Self::error_response(&format!("agreement rejected: {e}")),
                }
            }

            MessageType::CrawlRequest => {
                let proto = protocol.lock().await;
                // Payload is JSON: {"public_key": "...", "start_seq": N}
                let req: serde_json::Value = serde_json::from_slice(&msg.payload)
                    .unwrap_or_default();
                let pubkey = req.get("public_key")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let start_seq = req.get("start_seq")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(1);

                match proto.store().crawl(pubkey, start_seq) {
                    Ok(blocks) => {
                        let resp = TransportMessage::new(
                            MessageType::CrawlResponse,
                            proto.pubkey(),
                            serde_json::to_vec(&blocks).unwrap_or_default(),
                            msg.request_id,
                        );
                        serde_json::to_vec(&resp).unwrap_or_default()
                    }
                    Err(e) => Self::error_response(&format!("crawl error: {e}")),
                }
            }

            MessageType::StatusRequest => {
                let proto = protocol.lock().await;
                let pubkey = proto.pubkey();
                let latest_seq = proto.store().get_latest_seq(&pubkey).unwrap_or(0);
                let block_count = proto.store().get_block_count().unwrap_or(0);

                serde_json::to_vec(&serde_json::json!({
                    "public_key": pubkey,
                    "latest_seq": latest_seq,
                    "block_count": block_count,
                })).unwrap_or_default()
            }

            MessageType::DiscoveryRequest => {
                // Payload: list of (pubkey, address, seq).
                if let Ok(peers) = serde_json::from_slice::<Vec<(String, String, u64)>>(&msg.payload) {
                    discovery.merge_peers(peers).await;
                }

                let our_peers = discovery.get_gossip_peers(20).await;
                let peers: Vec<(String, String, u64)> = our_peers
                    .iter()
                    .map(|p| (p.pubkey.clone(), p.address.clone(), p.latest_seq))
                    .collect();

                let resp = TransportMessage::new(
                    MessageType::DiscoveryResponse,
                    msg.sender_pubkey,
                    serde_json::to_vec(&peers).unwrap_or_default(),
                    msg.request_id,
                );
                serde_json::to_vec(&resp).unwrap_or_default()
            }

            MessageType::Ping => {
                let resp = TransportMessage::new(
                    MessageType::Pong,
                    String::new(),
                    Vec::new(),
                    msg.request_id,
                );
                serde_json::to_vec(&resp).unwrap_or_default()
            }

            _ => {
                Self::error_response("unhandled message type")
            }
        }
    }

    fn error_response(msg: &str) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({"error": msg})).unwrap_or_default()
    }

    /// Peer discovery: bootstrap then periodic gossip.
    async fn discovery_loop(
        discovery: Arc<PeerDiscovery>,
        protocol: Arc<Mutex<TrustChainProtocol<SqliteBlockStore>>>,
        bootstrap_nodes: Vec<String>,
    ) {
        // Phase 1: Bootstrap — connect to known nodes and fetch their peers.
        for addr in &bootstrap_nodes {
            tracing::info!(addr = %addr, "bootstrapping from peer");
            match Self::fetch_status_http(addr).await {
                Ok((pubkey, latest_seq)) => {
                    discovery.add_peer(pubkey, addr.clone(), latest_seq).await;
                    tracing::info!(addr = %addr, "bootstrap peer added");
                }
                Err(e) => {
                    tracing::warn!(addr = %addr, err = %e, "bootstrap peer unreachable");
                }
            }

            // Also try to discover more peers from this bootstrap node.
            if let Ok(peers) = Self::fetch_peers_http(addr).await {
                for (pk, address, seq) in peers {
                    discovery.add_peer(pk, address, seq).await;
                }
            }
        }

        let peer_count = discovery.peer_count().await;
        tracing::info!(peer_count, "bootstrap complete");

        // Phase 2: Periodic gossip — exchange peer lists with random peers.
        let mut interval = tokio::time::interval(Duration::from_secs(30));
        loop {
            interval.tick().await;

            let gossip_peers = discovery.get_gossip_peers(3).await;
            for peer in gossip_peers {
                // Refresh peer status.
                match Self::fetch_status_http(&peer.address).await {
                    Ok((pubkey, latest_seq)) => {
                        discovery.add_peer(pubkey, peer.address.clone(), latest_seq).await;
                    }
                    Err(_) => {
                        // Stale peer — remove if not seen for 5 minutes.
                        if peer.last_seen.elapsed() > Duration::from_secs(300) {
                            discovery.remove_peer(&peer.pubkey).await;
                        }
                    }
                }

                // Exchange peer lists.
                if let Ok(peers) = Self::fetch_peers_http(&peer.address).await {
                    for (pk, address, seq) in peers {
                        discovery.add_peer(pk, address, seq).await;
                    }
                }
            }

            // Sync chains from peers we know about.
            let all_peers = discovery.get_peers().await;
            for peer in &all_peers {
                let our_seq = {
                    let proto = protocol.lock().await;
                    proto.store().get_latest_seq(&peer.pubkey).unwrap_or(0)
                };
                if peer.latest_seq > our_seq {
                    // Fetch missing blocks.
                    if let Ok(blocks) = Self::fetch_crawl_http(&peer.address, &peer.pubkey, our_seq + 1).await {
                        let mut proto = protocol.lock().await;
                        for block in &blocks {
                            let _ = proto.store_mut().add_block(block);
                        }
                        if !blocks.is_empty() {
                            tracing::info!(
                                peer = &peer.pubkey[..8],
                                synced = blocks.len(),
                                "synced blocks from peer"
                            );
                        }
                    }
                }
            }
        }
    }

    /// Fetch status from a peer via HTTP.
    async fn fetch_status_http(addr: &str) -> anyhow::Result<(String, u64)> {
        let url = if addr.starts_with("http") {
            format!("{addr}/status")
        } else {
            format!("http://{addr}/status")
        };

        let resp: serde_json::Value = reqwest::Client::new()
            .get(&url)
            .timeout(Duration::from_secs(5))
            .send()
            .await?
            .json()
            .await?;

        let pubkey = resp.get("public_key")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("missing public_key"))?
            .to_string();
        let latest_seq = resp.get("latest_seq")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);

        Ok((pubkey, latest_seq))
    }

    /// Fetch peer list from a peer via HTTP.
    async fn fetch_peers_http(addr: &str) -> anyhow::Result<Vec<(String, String, u64)>> {
        let url = if addr.starts_with("http") {
            format!("{addr}/peers")
        } else {
            format!("http://{addr}/peers")
        };

        let resp: Vec<serde_json::Value> = reqwest::Client::new()
            .get(&url)
            .timeout(Duration::from_secs(5))
            .send()
            .await?
            .json()
            .await?;

        let peers: Vec<(String, String, u64)> = resp
            .iter()
            .filter_map(|p| {
                let pk = p.get("pubkey")?.as_str()?.to_string();
                let addr = p.get("address")?.as_str()?.to_string();
                let seq = p.get("latest_seq").and_then(|v| v.as_u64()).unwrap_or(0);
                Some((pk, addr, seq))
            })
            .collect();

        Ok(peers)
    }

    /// Fetch blocks from a peer via HTTP crawl endpoint.
    async fn fetch_crawl_http(
        addr: &str,
        pubkey: &str,
        start_seq: u64,
    ) -> anyhow::Result<Vec<HalfBlock>> {
        let url = if addr.starts_with("http") {
            format!("{addr}/crawl/{pubkey}?start_seq={start_seq}")
        } else {
            format!("http://{addr}/crawl/{pubkey}?start_seq={start_seq}")
        };

        let resp: serde_json::Value = reqwest::Client::new()
            .get(&url)
            .timeout(Duration::from_secs(10))
            .send()
            .await?
            .json()
            .await?;

        let blocks: Vec<HalfBlock> = resp
            .get("blocks")
            .and_then(|v| serde_json::from_value(v.clone()).ok())
            .unwrap_or_default();

        Ok(blocks)
    }
}
