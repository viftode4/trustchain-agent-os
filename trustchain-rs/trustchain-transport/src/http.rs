//! HTTP REST API for TrustChain nodes, using Axum.

use std::net::SocketAddr;
use std::sync::Arc;

use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    routing::{get, post},
};
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;

use trustchain_core::{
    BlockStore, HalfBlock, MemoryBlockStore, TrustChainProtocol,
};

use crate::discovery::PeerDiscovery;

/// Shared application state for HTTP handlers.
#[derive(Clone)]
pub struct AppState {
    pub protocol: Arc<Mutex<TrustChainProtocol<MemoryBlockStore>>>,
    pub discovery: Arc<PeerDiscovery>,
}

/// Response for status endpoint.
#[derive(Serialize)]
pub struct StatusResponse {
    pub public_key: String,
    pub latest_seq: u64,
    pub block_count: usize,
    pub peer_count: usize,
}

/// Request for proposal endpoint.
#[derive(Deserialize)]
pub struct ProposeRequest {
    pub counterparty_pubkey: String,
    pub transaction: serde_json::Value,
}

/// Response for proposal endpoint.
#[derive(Serialize)]
pub struct ProposeResponse {
    pub proposal: HalfBlock,
}

/// Query parameters for crawl endpoint.
#[derive(Deserialize)]
pub struct CrawlQuery {
    pub start_seq: Option<u64>,
}

/// Response for block retrieval.
#[derive(Serialize)]
pub struct BlockResponse {
    pub block: Option<HalfBlock>,
}

/// Response wrapping a list of blocks.
#[derive(Serialize)]
pub struct BlocksResponse {
    pub blocks: Vec<HalfBlock>,
}

/// Generic error response.
#[derive(Serialize)]
pub struct ErrorResponse {
    pub error: String,
}

/// Build the Axum router with all REST endpoints.
pub fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/status", get(handle_status))
        .route("/propose", post(handle_propose))
        .route("/chain/{pubkey}", get(handle_get_chain))
        .route("/block/{pubkey}/{seq}", get(handle_get_block))
        .route("/crawl/{pubkey}", get(handle_crawl))
        .route("/peers", get(handle_get_peers))
        .with_state(state)
}

/// Start the HTTP server.
pub async fn start_http_server(
    addr: SocketAddr,
    state: AppState,
) -> Result<(), Box<dyn std::error::Error>> {
    let router = build_router(state);

    log::info!("HTTP server listening on {addr}");

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, router).await?;

    Ok(())
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

async fn handle_status(
    State(state): State<AppState>,
) -> Result<Json<StatusResponse>, StatusCode> {
    let proto = state.protocol.lock().await;
    let pubkey = proto.pubkey();

    let latest_seq = proto.store().get_latest_seq(&pubkey).unwrap_or(0);
    let block_count = proto.store().get_block_count().unwrap_or(0);
    let peer_count = state.discovery.peer_count().await;

    Ok(Json(StatusResponse {
        public_key: pubkey,
        latest_seq,
        block_count,
        peer_count,
    }))
}

async fn handle_propose(
    State(state): State<AppState>,
    Json(req): Json<ProposeRequest>,
) -> Result<Json<ProposeResponse>, (StatusCode, Json<ErrorResponse>)> {
    let mut proto = state.protocol.lock().await;

    match proto.create_proposal(&req.counterparty_pubkey, req.transaction, None) {
        Ok(proposal) => Ok(Json(ProposeResponse { proposal })),
        Err(e) => Err((
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                error: e.to_string(),
            }),
        )),
    }
}

async fn handle_get_chain(
    State(state): State<AppState>,
    Path(pubkey): Path<String>,
) -> Result<Json<BlocksResponse>, StatusCode> {
    let proto = state.protocol.lock().await;

    match proto.store().get_chain(&pubkey) {
        Ok(blocks) => Ok(Json(BlocksResponse { blocks })),
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn handle_get_block(
    State(state): State<AppState>,
    Path((pubkey, seq)): Path<(String, u64)>,
) -> Result<Json<BlockResponse>, StatusCode> {
    let proto = state.protocol.lock().await;

    match proto.store().get_block(&pubkey, seq) {
        Ok(block) => Ok(Json(BlockResponse { block })),
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn handle_crawl(
    State(state): State<AppState>,
    Path(pubkey): Path<String>,
    Query(params): Query<CrawlQuery>,
) -> Result<Json<BlocksResponse>, StatusCode> {
    let proto = state.protocol.lock().await;
    let start = params.start_seq.unwrap_or(1);

    match proto.store().crawl(&pubkey, start) {
        Ok(blocks) => Ok(Json(BlocksResponse { blocks })),
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn handle_get_peers(
    State(state): State<AppState>,
) -> Json<Vec<PeerInfoResponse>> {
    let peers = state.discovery.get_peers().await;
    let response: Vec<PeerInfoResponse> = peers
        .iter()
        .map(|p| PeerInfoResponse {
            pubkey: p.pubkey.clone(),
            address: p.address.clone(),
            latest_seq: p.latest_seq,
        })
        .collect();
    Json(response)
}

#[derive(Serialize)]
pub struct PeerInfoResponse {
    pub pubkey: String,
    pub address: String,
    pub latest_seq: u64,
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::Request;
    use tower::ServiceExt;
    use trustchain_core::Identity;

    fn make_test_state() -> AppState {
        let identity = Identity::from_bytes(&[1u8; 32]);
        let store = MemoryBlockStore::new();
        let protocol = TrustChainProtocol::new(identity.clone(), store);
        let discovery = PeerDiscovery::new(identity.pubkey_hex(), vec![]);

        AppState {
            protocol: Arc::new(Mutex::new(protocol)),
            discovery: Arc::new(discovery),
        }
    }

    #[tokio::test]
    async fn test_status_endpoint() {
        let state = make_test_state();
        let app = build_router(state);

        let request = Request::builder()
            .uri("/status")
            .body(Body::empty())
            .unwrap();

        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_propose_endpoint() {
        let state = make_test_state();
        let app = build_router(state);

        let bob_pubkey = Identity::from_bytes(&[2u8; 32]).pubkey_hex();
        let body = serde_json::json!({
            "counterparty_pubkey": bob_pubkey,
            "transaction": {"service": "compute"},
        });

        let request = Request::builder()
            .method("POST")
            .uri("/propose")
            .header("content-type", "application/json")
            .body(Body::from(serde_json::to_string(&body).unwrap()))
            .unwrap();

        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_chain_endpoint_empty() {
        let state = make_test_state();
        let app = build_router(state);

        let request = Request::builder()
            .uri("/chain/nonexistent")
            .body(Body::empty())
            .unwrap();

        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_peers_endpoint() {
        let state = make_test_state();
        let app = build_router(state);

        let request = Request::builder()
            .uri("/peers")
            .body(Body::empty())
            .unwrap();

        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }
}
