//! TrustChain WASM bindings for browser nodes.
//!
//! Exports core protocol types to JavaScript via wasm-bindgen,
//! enabling browsers to verify blocks, compute trust, and participate
//! in the TrustChain protocol.

use wasm_bindgen::prelude::*;

use trustchain_core::identity::Identity as CoreIdentity;
use trustchain_core::halfblock::{create_half_block, verify_block};
use trustchain_core::blockstore::MemoryBlockStore;
use trustchain_core::protocol::TrustChainProtocol;
use trustchain_core::netflow::NetFlowTrust;
use trustchain_core::types::{BlockType, GENESIS_HASH};

/// WASM-exported Ed25519 identity.
#[wasm_bindgen]
pub struct WasmIdentity {
    inner: CoreIdentity,
}

#[wasm_bindgen]
impl WasmIdentity {
    /// Generate a new random identity.
    #[wasm_bindgen(constructor)]
    pub fn new() -> Self {
        Self {
            inner: CoreIdentity::generate(),
        }
    }

    /// Create from raw 32-byte private key (as hex string).
    #[wasm_bindgen(js_name = fromHex)]
    pub fn from_hex(secret_hex: &str) -> Result<WasmIdentity, JsValue> {
        let bytes = hex::decode(secret_hex)
            .map_err(|e| JsValue::from_str(&format!("invalid hex: {e}")))?;
        if bytes.len() != 32 {
            return Err(JsValue::from_str("secret key must be 32 bytes"));
        }
        let mut arr = [0u8; 32];
        arr.copy_from_slice(&bytes);
        Ok(Self {
            inner: CoreIdentity::from_bytes(&arr),
        })
    }

    /// Get the hex-encoded public key.
    #[wasm_bindgen(getter, js_name = pubkeyHex)]
    pub fn pubkey_hex(&self) -> String {
        self.inner.pubkey_hex()
    }

    /// Get short identifier (first 8 hex chars).
    #[wasm_bindgen(getter, js_name = shortId)]
    pub fn short_id(&self) -> String {
        self.inner.short_id()
    }

    /// Sign data (UTF-8 string) and return hex-encoded signature.
    pub fn sign(&self, data: &str) -> String {
        self.inner.sign_hex(data.as_bytes())
    }

    /// Verify a signature (hex) against data and public key (hex).
    #[wasm_bindgen(js_name = verify)]
    pub fn verify_static(data: &str, signature_hex: &str, pubkey_hex: &str) -> Result<bool, JsValue> {
        CoreIdentity::verify_hex(data.as_bytes(), signature_hex, pubkey_hex)
            .map_err(|e| JsValue::from_str(&e.to_string()))
    }
}

/// WASM-exported HalfBlock creation and verification.
#[wasm_bindgen]
pub struct WasmBlock;

#[wasm_bindgen]
impl WasmBlock {
    /// Create a half-block and return it as JSON.
    #[wasm_bindgen(js_name = createBlock)]
    pub fn create_block(
        identity_hex: &str,
        seq: u64,
        link_pubkey: &str,
        link_seq: u64,
        prev_hash: &str,
        block_type: &str,
        transaction_json: &str,
        timestamp: u64,
    ) -> Result<String, JsValue> {
        let secret_bytes = hex::decode(identity_hex)
            .map_err(|e| JsValue::from_str(&format!("invalid identity hex: {e}")))?;
        if secret_bytes.len() != 32 {
            return Err(JsValue::from_str("identity must be 32 bytes"));
        }
        let mut arr = [0u8; 32];
        arr.copy_from_slice(&secret_bytes);
        let identity = CoreIdentity::from_bytes(&arr);

        let bt = BlockType::from_str_loose(block_type)
            .ok_or_else(|| JsValue::from_str("invalid block type"))?;

        let tx: serde_json::Value = serde_json::from_str(transaction_json)
            .map_err(|e| JsValue::from_str(&format!("invalid transaction JSON: {e}")))?;

        let block = create_half_block(
            &identity,
            seq,
            link_pubkey,
            link_seq,
            prev_hash,
            bt,
            tx,
            Some(timestamp),
        );

        serde_json::to_string(&block)
            .map_err(|e| JsValue::from_str(&format!("serialization error: {e}")))
    }

    /// Verify a block (given as JSON). Returns true if valid.
    #[wasm_bindgen(js_name = verifyBlock)]
    pub fn verify_block_json(block_json: &str) -> Result<bool, JsValue> {
        let block: trustchain_core::HalfBlock = serde_json::from_str(block_json)
            .map_err(|e| JsValue::from_str(&format!("invalid block JSON: {e}")))?;
        verify_block(&block)
            .map_err(|e| JsValue::from_str(&format!("verification error: {e}")))
    }
}

/// Get the genesis hash constant.
#[wasm_bindgen(js_name = genesisHash)]
pub fn genesis_hash() -> String {
    GENESIS_HASH.to_string()
}
