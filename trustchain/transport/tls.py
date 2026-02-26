"""TLS certificate generation from TrustChain Ed25519 identity.

Generates self-signed X.509 certificates where the subject CN is the
node's Ed25519 public key hex. This links TLS peer authentication to
TrustChain identity verification.
"""

from __future__ import annotations

import datetime
import logging
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from trustchain.identity import Identity

logger = logging.getLogger("trustchain.transport.tls")


def generate_self_signed_cert(
    identity: Identity,
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
    valid_days: int = 365,
) -> Tuple[str, str]:
    """Generate a self-signed TLS certificate from a TrustChain identity.

    The certificate's CN (Common Name) is set to the Ed25519 public key hex,
    creating a verifiable link between TLS and TrustChain identity.

    Note: TLS requires ECDSA or RSA keys (not Ed25519 directly), so we
    generate an ephemeral ECDSA key for TLS, but embed the Ed25519 pubkey
    in the certificate subject for identity linking.

    Args:
        identity: The TrustChain identity whose pubkey becomes the cert CN.
        cert_path: Where to write the PEM certificate. Auto-generated if None.
        key_path: Where to write the PEM private key. Auto-generated if None.
        valid_days: Certificate validity period in days.

    Returns:
        Tuple of (cert_path, key_path) as strings.
    """
    # Generate an ECDSA key for TLS (Ed25519 not universally supported in TLS)
    tls_key = ec.generate_private_key(ec.SECP256R1())

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, identity.pubkey_hex),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TrustChain"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=valid_days))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(
                    __import__("ipaddress").IPv4Address("127.0.0.1")
                ),
            ]),
            critical=False,
        )
        .sign(tls_key, hashes.SHA256())
    )

    # Write to files
    if cert_path is None:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".pem", prefix="tc_cert_", delete=False
        )
        cert_path = tmp.name
        tmp.close()

    if key_path is None:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".pem", prefix="tc_key_", delete=False
        )
        key_path = tmp.name
        tmp.close()

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    with open(key_path, "wb") as f:
        f.write(
            tls_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

    logger.info(
        "Generated TLS cert for %s... -> %s",
        identity.pubkey_hex[:16],
        cert_path,
    )
    return cert_path, key_path


def extract_pubkey_from_cert(cert_path: str) -> Optional[str]:
    """Extract the TrustChain pubkey hex from a certificate's CN field.

    Returns the hex pubkey string, or None if not found.
    """
    with open(cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())

    for attr in cert.subject:
        if attr.oid == NameOID.COMMON_NAME:
            return attr.value
    return None


def verify_peer_cert(cert_path: str, expected_pubkey: str) -> bool:
    """Verify that a peer's TLS certificate matches their TrustChain identity.

    Checks that the certificate's CN matches the expected Ed25519 pubkey hex.
    """
    actual_pubkey = extract_pubkey_from_cert(cert_path)
    if actual_pubkey is None:
        return False
    return actual_pubkey == expected_pubkey
