//! TOML-based node configuration.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Node configuration, loadable from a TOML file.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeConfig {
    /// Listen address for QUIC transport.
    #[serde(default = "default_quic_addr")]
    pub quic_addr: String,

    /// Listen address for gRPC service.
    #[serde(default = "default_grpc_addr")]
    pub grpc_addr: String,

    /// Listen address for HTTP REST API.
    #[serde(default = "default_http_addr")]
    pub http_addr: String,

    /// Listen address for the transparent HTTP proxy (agent sidecar).
    /// Agents set HTTP_PROXY=http://<this address> to get automatic trust recording.
    #[serde(default = "default_proxy_addr")]
    pub proxy_addr: String,

    /// Path to the Ed25519 identity key file.
    #[serde(default = "default_key_path")]
    pub key_path: PathBuf,

    /// Path to the SQLite database.
    #[serde(default = "default_db_path")]
    pub db_path: PathBuf,

    /// Bootstrap node addresses for peer discovery.
    #[serde(default)]
    pub bootstrap_nodes: Vec<String>,

    /// Minimum signers for CHECO consensus checkpoints.
    #[serde(default = "default_min_signers")]
    pub min_signers: usize,

    /// Log level.
    #[serde(default = "default_log_level")]
    pub log_level: String,
}

impl Default for NodeConfig {
    fn default() -> Self {
        Self {
            quic_addr: default_quic_addr(),
            grpc_addr: default_grpc_addr(),
            http_addr: default_http_addr(),
            proxy_addr: default_proxy_addr(),
            key_path: default_key_path(),
            db_path: default_db_path(),
            bootstrap_nodes: vec![],
            min_signers: default_min_signers(),
            log_level: default_log_level(),
        }
    }
}

impl NodeConfig {
    /// Load configuration from a TOML file.
    pub fn load(path: &str) -> anyhow::Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config: Self = toml::from_str(&content)?;
        Ok(config)
    }

    /// Generate a default config file as a string.
    pub fn default_toml() -> String {
        toml::to_string_pretty(&Self::default()).unwrap()
    }
}

fn default_quic_addr() -> String {
    "0.0.0.0:8200".to_string()
}

fn default_grpc_addr() -> String {
    "0.0.0.0:8201".to_string()
}

fn default_http_addr() -> String {
    "0.0.0.0:8202".to_string()
}

fn default_proxy_addr() -> String {
    "127.0.0.1:8203".to_string()
}

fn default_key_path() -> PathBuf {
    PathBuf::from("identity.key")
}

fn default_db_path() -> PathBuf {
    PathBuf::from("trustchain.db")
}

fn default_min_signers() -> usize {
    1
}

fn default_log_level() -> String {
    "info".to_string()
}
