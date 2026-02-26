//! Node — wires together protocol, storage, and all transports.

use std::net::SocketAddr;
use std::sync::Arc;

use tokio::sync::Mutex;

use trustchain_core::{Identity, MemoryBlockStore, TrustChainProtocol};
use trustchain_transport::{
    AppState, PeerDiscovery, QuicTransport,
    start_grpc_server, start_http_server,
};

use crate::config::NodeConfig;

/// A running TrustChain node.
pub struct Node {
    pub identity: Identity,
    pub config: NodeConfig,
    pub protocol: Arc<Mutex<TrustChainProtocol<MemoryBlockStore>>>,
    pub discovery: Arc<PeerDiscovery>,
}

impl Node {
    /// Create a new node from configuration.
    pub fn new(identity: Identity, config: NodeConfig) -> Self {
        let store = MemoryBlockStore::new();
        let protocol = TrustChainProtocol::new(identity.clone(), store);
        let discovery = PeerDiscovery::new(
            identity.pubkey_hex(),
            config.bootstrap_nodes.clone(),
        );

        Self {
            identity,
            config,
            protocol: Arc::new(Mutex::new(protocol)),
            discovery: Arc::new(discovery),
        }
    }

    /// Start all node services (QUIC, gRPC, HTTP).
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

        // Start HTTP REST API.
        let http_addr: SocketAddr = self.config.http_addr.parse()?;
        let http_state = AppState {
            protocol: self.protocol.clone(),
            discovery: self.discovery.clone(),
        };
        let http_handle = tokio::spawn(async move {
            if let Err(e) = start_http_server(http_addr, http_state).await {
                tracing::error!("HTTP server error: {e}");
            }
        });
        tracing::info!(%http_addr, "HTTP API ready");

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
        }

        quic.shutdown();
        Ok(())
    }
}
