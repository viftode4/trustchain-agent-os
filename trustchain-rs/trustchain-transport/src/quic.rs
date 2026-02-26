//! QUIC transport implementation using Quinn.
//!
//! Provides low-latency, encrypted node-to-node communication.

use std::net::SocketAddr;

use quinn::Endpoint;
use tokio::sync::mpsc;

use crate::tls;
use crate::transport::TransportError;

/// QUIC transport for TrustChain node-to-node communication.
pub struct QuicTransport {
    endpoint: Endpoint,
    our_pubkey: String,
}

impl QuicTransport {
    /// Create a new QUIC transport that listens on the given address.
    pub async fn bind(
        listen_addr: SocketAddr,
        trustchain_pubkey: &str,
    ) -> Result<Self, TransportError> {
        let server_config = make_server_config(trustchain_pubkey)?;
        let client_config = make_client_config()?;

        let mut endpoint = Endpoint::server(server_config, listen_addr)
            .map_err(|e| TransportError::Connection(format!("failed to bind QUIC: {e}")))?;
        endpoint.set_default_client_config(client_config);

        log::info!(
            "QUIC transport listening on {}",
            endpoint.local_addr().unwrap()
        );

        Ok(Self {
            endpoint,
            our_pubkey: trustchain_pubkey.to_string(),
        })
    }

    /// Get the local address this transport is bound to.
    pub fn local_addr(&self) -> Result<SocketAddr, TransportError> {
        self.endpoint
            .local_addr()
            .map_err(|e| TransportError::Connection(e.to_string()))
    }

    /// Send a raw message to a peer.
    pub async fn send_message(
        &self,
        addr: SocketAddr,
        data: &[u8],
    ) -> Result<Vec<u8>, TransportError> {
        let connection = self
            .endpoint
            .connect(addr, "localhost")
            .map_err(|e| TransportError::Connection(format!("QUIC connect error: {e}")))?
            .await
            .map_err(|e| TransportError::Connection(format!("QUIC handshake error: {e}")))?;

        let (mut send, mut recv) = connection
            .open_bi()
            .await
            .map_err(|e| TransportError::Send(format!("QUIC stream open error: {e}")))?;

        // Send length-prefixed message.
        let len = (data.len() as u32).to_be_bytes();
        send.write_all(&len)
            .await
            .map_err(|e| TransportError::Send(e.to_string()))?;
        send.write_all(data)
            .await
            .map_err(|e| TransportError::Send(e.to_string()))?;
        send.finish()
            .map_err(|e| TransportError::Send(e.to_string()))?;

        // Read response.
        let response = recv
            .read_to_end(16 * 1024 * 1024) // 16 MB max
            .await
            .map_err(|e| TransportError::Receive(e.to_string()))?;

        Ok(response)
    }

    /// Start accepting incoming connections and dispatch messages.
    pub async fn accept_loop(
        &self,
        tx: mpsc::Sender<(Vec<u8>, mpsc::Sender<Vec<u8>>)>,
    ) -> Result<(), TransportError> {
        loop {
            let incoming = self
                .endpoint
                .accept()
                .await
                .ok_or_else(|| TransportError::Connection("endpoint closed".to_string()))?;

            let connection = incoming
                .await
                .map_err(|e| TransportError::Connection(format!("accept error: {e}")))?;

            let tx = tx.clone();
            tokio::spawn(async move {
                loop {
                    let stream = match connection.accept_bi().await {
                        Ok(s) => s,
                        Err(_) => break, // Connection closed.
                    };
                    let (send, mut recv) = stream;
                    let tx = tx.clone();

                    tokio::spawn(async move {
                        // Read length-prefixed message.
                        let mut len_buf = [0u8; 4];
                        if recv.read_exact(&mut len_buf).await.is_err() {
                            return;
                        }
                        let len = u32::from_be_bytes(len_buf) as usize;
                        if len > 16 * 1024 * 1024 {
                            return; // Too large.
                        }

                        let data = match recv.read_to_end(len).await {
                            Ok(d) => d,
                            Err(_) => return,
                        };

                        // Set up response channel.
                        let (resp_tx, mut resp_rx) = mpsc::channel::<Vec<u8>>(1);

                        if tx.send((data, resp_tx)).await.is_err() {
                            return;
                        }

                        // Send response back.
                        if let Some(response) = resp_rx.recv().await {
                            let mut send = send;
                            let _ = send.write_all(&response).await;
                            let _ = send.finish();
                        }
                    });
                }
            });
        }
    }

    /// Shut down the QUIC endpoint.
    pub fn shutdown(&self) {
        self.endpoint
            .close(quinn::VarInt::from_u32(0), b"shutdown");
    }

    /// Get our public key.
    pub fn pubkey(&self) -> &str {
        &self.our_pubkey
    }
}

fn make_server_config(pubkey: &str) -> Result<quinn::ServerConfig, TransportError> {
    let tls_config = tls::build_server_config(pubkey)
        .map_err(|e| TransportError::Tls(e.to_string()))?;

    let quic_server_config = quinn::crypto::rustls::QuicServerConfig::try_from(tls_config)
        .map_err(|e| TransportError::Tls(e.to_string()))?;

    let config = quinn::ServerConfig::with_crypto(std::sync::Arc::new(quic_server_config));
    Ok(config)
}

fn make_client_config() -> Result<quinn::ClientConfig, TransportError> {
    let tls_config = tls::build_client_config()
        .map_err(|e| TransportError::Tls(e.to_string()))?;

    let quic_client_config = quinn::crypto::rustls::QuicClientConfig::try_from(tls_config)
        .map_err(|e| TransportError::Tls(e.to_string()))?;

    let config = quinn::ClientConfig::new(std::sync::Arc::new(quic_client_config));
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_quic_bind() {
        let addr: SocketAddr = "127.0.0.1:0".parse().unwrap();
        let pubkey = "a".repeat(64);
        let transport = QuicTransport::bind(addr, &pubkey).await.unwrap();
        let local = transport.local_addr().unwrap();
        assert_ne!(local.port(), 0);
        transport.shutdown();
    }

    #[tokio::test]
    async fn test_quic_roundtrip() {
        let addr: SocketAddr = "127.0.0.1:0".parse().unwrap();
        let pubkey = "a".repeat(64);
        let server = QuicTransport::bind(addr, &pubkey).await.unwrap();
        let server_addr = server.local_addr().unwrap();

        let (tx, mut rx) = mpsc::channel(10);

        // Start server in background.
        let server_handle = tokio::spawn(async move {
            let _ = server.accept_loop(tx).await;
        });

        // Give server time to start.
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;

        // Handle incoming messages in background.
        tokio::spawn(async move {
            while let Some((data, resp_tx)) = rx.recv().await {
                // Echo back.
                let _ = resp_tx.send(data).await;
            }
        });

        // Client sends a message.
        let client_addr: SocketAddr = "127.0.0.1:0".parse().unwrap();
        let client = QuicTransport::bind(client_addr, &pubkey).await.unwrap();

        let msg = b"hello trustchain";
        let response = client.send_message(server_addr, msg).await.unwrap();
        assert_eq!(response, msg);

        client.shutdown();
        server_handle.abort();
    }
}
