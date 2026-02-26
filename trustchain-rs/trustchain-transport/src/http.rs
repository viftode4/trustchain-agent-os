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

use trustchain_core::{BlockStore, HalfBlock, TrustChainProtocol};

use crate::discovery::PeerDiscovery;

/// Shared application state for HTTP handlers, generic over BlockStore.
pub struct AppState<S: BlockStore + 'static> {
    pub protocol: Arc<Mutex<TrustChainProtocol<S>>>,
    pub discovery: Arc<PeerDiscovery>,
}

// Manual Clone impl — Arc handles the cloning, S doesn't need Clone.
impl<S: BlockStore + 'static> Clone for AppState<S> {
    fn clone(&self) -> Self {
        Self {
            protocol: self.protocol.clone(),
            discovery: self.discovery.clone(),
        }
    }
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

/// Request for receiving a proposal from a remote node.
#[derive(Deserialize)]
pub struct ReceiveProposalRequest {
    pub proposal: HalfBlock,
}

/// Response for receiving a proposal — returns the agreement if accepted.
#[derive(Serialize)]
pub struct ReceiveProposalResponse {
    pub accepted: bool,
    pub agreement: Option<HalfBlock>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Request for receiving an agreement from a remote node.
#[derive(Deserialize)]
pub struct ReceiveAgreementRequest {
    pub agreement: HalfBlock,
}

/// Response for receiving an agreement.
#[derive(Serialize)]
pub struct ReceiveAgreementResponse {
    pub accepted: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
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

#[derive(Serialize)]
pub struct PeerInfoResponse {
    pub pubkey: String,
    pub address: String,
    pub latest_seq: u64,
}

/// Build the Axum router with all REST endpoints.
pub fn build_router<S: BlockStore + Send + 'static>(state: AppState<S>) -> Router {
    Router::new()
        .route("/status", get(handle_status::<S>))
        .route("/propose", post(handle_propose::<S>))
        .route("/receive_proposal", post(handle_receive_proposal::<S>))
        .route("/receive_agreement", post(handle_receive_agreement::<S>))
        .route("/chain/{pubkey}", get(handle_get_chain::<S>))
        .route("/block/{pubkey}/{seq}", get(handle_get_block::<S>))
        .route("/crawl/{pubkey}", get(handle_crawl::<S>))
        .route("/peers", get(handle_get_peers::<S>))
        .with_state(state)
}

/// Start the HTTP server.
pub async fn start_http_server<S: BlockStore + 'static>(
    addr: SocketAddr,
    state: AppState<S>,
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

async fn handle_status<S: BlockStore + 'static>(
    State(state): State<AppState<S>>,
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

async fn handle_propose<S: BlockStore + 'static>(
    State(state): State<AppState<S>>,
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

/// Receive a proposal from a remote node — validates, stores, and returns agreement.
async fn handle_receive_proposal<S: BlockStore + 'static>(
    State(state): State<AppState<S>>,
    Json(req): Json<ReceiveProposalRequest>,
) -> Json<ReceiveProposalResponse> {
    let mut proto = state.protocol.lock().await;

    // Receive and validate the proposal.
    if let Err(e) = proto.receive_proposal(&req.proposal) {
        return Json(ReceiveProposalResponse {
            accepted: false,
            agreement: None,
            error: Some(e.to_string()),
        });
    }

    // Create agreement.
    match proto.create_agreement(&req.proposal, None) {
        Ok(agreement) => Json(ReceiveProposalResponse {
            accepted: true,
            agreement: Some(agreement),
            error: None,
        }),
        Err(e) => Json(ReceiveProposalResponse {
            accepted: false,
            agreement: None,
            error: Some(e.to_string()),
        }),
    }
}

/// Receive an agreement from a remote node — validates and stores.
async fn handle_receive_agreement<S: BlockStore + 'static>(
    State(state): State<AppState<S>>,
    Json(req): Json<ReceiveAgreementRequest>,
) -> Json<ReceiveAgreementResponse> {
    let mut proto = state.protocol.lock().await;

    match proto.receive_agreement(&req.agreement) {
        Ok(_) => Json(ReceiveAgreementResponse {
            accepted: true,
            error: None,
        }),
        Err(e) => Json(ReceiveAgreementResponse {
            accepted: false,
            error: Some(e.to_string()),
        }),
    }
}

async fn handle_get_chain<S: BlockStore + 'static>(
    State(state): State<AppState<S>>,
    Path(pubkey): Path<String>,
) -> Result<Json<BlocksResponse>, StatusCode> {
    let proto = state.protocol.lock().await;

    match proto.store().get_chain(&pubkey) {
        Ok(blocks) => Ok(Json(BlocksResponse { blocks })),
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn handle_get_block<S: BlockStore + 'static>(
    State(state): State<AppState<S>>,
    Path((pubkey, seq)): Path<(String, u64)>,
) -> Result<Json<BlockResponse>, StatusCode> {
    let proto = state.protocol.lock().await;

    match proto.store().get_block(&pubkey, seq) {
        Ok(block) => Ok(Json(BlockResponse { block })),
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn handle_crawl<S: BlockStore + 'static>(
    State(state): State<AppState<S>>,
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

async fn handle_get_peers<S: BlockStore + 'static>(
    State(state): State<AppState<S>>,
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

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::Request;
    use tower::ServiceExt;
    use trustchain_core::{Identity, MemoryBlockStore};

    fn make_test_state() -> AppState<MemoryBlockStore> {
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

    #[tokio::test]
    async fn test_receive_proposal_endpoint() {
        let state = make_test_state();

        // Create a proposal from Alice to the test node.
        let alice = Identity::from_bytes(&[2u8; 32]);
        let test_pubkey = Identity::from_bytes(&[1u8; 32]).pubkey_hex();
        let proposal = trustchain_core::create_half_block(
            &alice,
            1,
            &test_pubkey,
            0,
            trustchain_core::GENESIS_HASH,
            trustchain_core::BlockType::Proposal,
            serde_json::json!({"service": "test"}),
            Some(1000.0),
        );

        let app = build_router(state);
        let body = serde_json::json!({ "proposal": proposal });

        let request = Request::builder()
            .method("POST")
            .uri("/receive_proposal")
            .header("content-type", "application/json")
            .body(Body::from(serde_json::to_string(&body).unwrap()))
            .unwrap();

        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }
}
