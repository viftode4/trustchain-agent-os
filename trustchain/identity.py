"""Ed25519 identity for TrustChain agents."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


class Identity:
    """An Ed25519 keypair representing an agent's identity on TrustChain."""

    def __init__(self, private_key: Ed25519PrivateKey | None = None):
        if private_key is not None:
            self._private_key = private_key
        else:
            self._private_key = Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._public_key

    @property
    def pubkey_bytes(self) -> bytes:
        return self._public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def pubkey_hex(self) -> str:
        return self.pubkey_bytes.hex()

    @property
    def short_id(self) -> str:
        return self.pubkey_hex[:8]

    def sign(self, data: bytes) -> bytes:
        return self._private_key.sign(data)

    @staticmethod
    def verify(data: bytes, signature: bytes, pubkey_bytes: bytes) -> bool:
        """Verify a signature against raw public key bytes."""
        pubkey = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
        try:
            pubkey.verify(signature, data)
            return True
        except Exception:
            return False

    def save(self, path: str) -> None:
        """Persist private key to file."""
        key_bytes = self._private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )
        with open(path, "wb") as f:
            f.write(key_bytes)

    @classmethod
    def load(cls, path: str) -> Identity:
        """Load identity from a persisted private key file."""
        with open(path, "rb") as f:
            key_bytes = f.read()
        private_key = Ed25519PrivateKey.from_private_bytes(key_bytes)
        return cls(private_key=private_key)
