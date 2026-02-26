//! TrustChain Node — standalone binary for running a TrustChain node.

mod config;
mod node;

use clap::{Parser, Subcommand};
use trustchain_core::Identity;

use crate::config::NodeConfig;
use crate::node::Node;

#[derive(Parser)]
#[command(name = "trustchain-node")]
#[command(about = "TrustChain — decentralized trust substrate for the AI agent economy")]
#[command(version)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Generate a new Ed25519 identity keypair.
    Keygen {
        /// Output path for the private key file.
        #[arg(short, long, default_value = "identity.key")]
        output: String,
    },

    /// Start the TrustChain node.
    Run {
        /// Path to TOML configuration file.
        #[arg(short, long, default_value = "node.toml")]
        config: String,
    },

    /// Query a running node's status.
    Status {
        /// HTTP address of the peer to query.
        #[arg(short, long, default_value = "http://127.0.0.1:8202")]
        peer: String,
    },

    /// Send a proposal to a peer.
    Propose {
        /// Public key of the counterparty.
        #[arg(long)]
        peer: String,

        /// Transaction payload as JSON.
        #[arg(long)]
        tx: String,

        /// HTTP address of our own node.
        #[arg(long, default_value = "http://127.0.0.1:8202")]
        node: String,
    },

    /// Print default configuration.
    InitConfig,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Keygen { output } => {
            let identity = Identity::generate();
            identity.save(&output)?;
            println!("Generated Ed25519 identity:");
            println!("  Public key: {}", identity.pubkey_hex());
            println!("  Saved to:   {output}");
        }

        Commands::Run { config: config_path } => {
            let config = if std::path::Path::new(&config_path).exists() {
                NodeConfig::load(&config_path)?
            } else {
                tracing::info!("No config file found, using defaults");
                NodeConfig::default()
            };

            // Set up tracing/logging.
            let filter = tracing_subscriber::EnvFilter::try_new(&config.log_level)
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
            tracing_subscriber::fmt()
                .with_env_filter(filter)
                .init();

            // Load or generate identity.
            let identity = if config.key_path.exists() {
                let id = Identity::load(&config.key_path)?;
                tracing::info!(pubkey = %id.pubkey_hex(), "loaded identity");
                id
            } else {
                let id = Identity::generate();
                id.save(&config.key_path)?;
                tracing::info!(pubkey = %id.pubkey_hex(), "generated new identity");
                id
            };

            let node = Node::new(identity, config);
            node.run().await?;
        }

        Commands::Status { peer } => {
            let url = format!("{peer}/status");
            let resp = reqwest::get(&url).await?.json::<serde_json::Value>().await?;
            println!("{}", serde_json::to_string_pretty(&resp)?);
        }

        Commands::Propose { peer, tx, node } => {
            let transaction: serde_json::Value = serde_json::from_str(&tx)?;
            let body = serde_json::json!({
                "counterparty_pubkey": peer,
                "transaction": transaction,
            });

            let client = reqwest::Client::new();
            let resp = client
                .post(format!("{node}/propose"))
                .json(&body)
                .send()
                .await?
                .json::<serde_json::Value>()
                .await?;
            println!("{}", serde_json::to_string_pretty(&resp)?);
        }

        Commands::InitConfig => {
            println!("{}", NodeConfig::default_toml());
        }
    }

    Ok(())
}
