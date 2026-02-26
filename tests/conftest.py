"""Shared test fixtures for TrustChain Agent OS."""

import pytest

from trustchain.identity import Identity
from trustchain.record import create_record
from trustchain.store import RecordStore


@pytest.fixture
def store():
    """Fresh in-memory RecordStore."""
    return RecordStore()


@pytest.fixture
def identity_a():
    """Ed25519 identity for agent A."""
    return Identity()


@pytest.fixture
def identity_b():
    """Ed25519 identity for agent B."""
    return Identity()


@pytest.fixture
def identity_c():
    """Ed25519 identity for agent C."""
    return Identity()


@pytest.fixture
def populated_store(store, identity_a, identity_b):
    """RecordStore with 5 completed interactions between A and B."""
    for i in range(5):
        record = create_record(
            identity_a=identity_a,
            identity_b=identity_b,
            seq_a=i,
            seq_b=i,
            prev_hash_a=store.last_hash_for(identity_a.pubkey_hex),
            prev_hash_b=store.last_hash_for(identity_b.pubkey_hex),
            interaction_type="service",
            outcome="completed",
        )
        store.add_record(record)
    return store
