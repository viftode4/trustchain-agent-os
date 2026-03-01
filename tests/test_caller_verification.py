"""Tests for caller challenge-response verification in trust_tools.

Covers:
- verify_caller() helper: valid sig, invalid sig, malformed hex, wrong length
- _check_caller_auth() behaviour: missing sig (warning), invalid sig (error), valid sig (pass)
- Integration: tool calls with and without signatures via FastMCP Client
"""

from __future__ import annotations

import logging

import pytest

from trustchain.identity import Identity
from trustchain.store import RecordStore

from gateway.config import UpstreamServer
from gateway.registry import UpstreamRegistry
from gateway.trust_tools import _check_caller_auth, register_trust_tools, verify_caller


# ---------------------------------------------------------------------------
# verify_caller unit tests
# ---------------------------------------------------------------------------

class TestVerifyCaller:
    """Unit tests for the verify_caller() helper."""

    def _make_signature(self, identity: Identity, pubkey: str, tool: str, nonce: str) -> str:
        message = f"{pubkey}:{tool}:{nonce}".encode("utf-8")
        return identity.sign(message).hex()

    def test_valid_signature_returns_true(self):
        identity = Identity()
        pubkey = identity.pubkey_hex
        nonce = "1234567890"
        tool = "trustchain_check_trust"
        sig = self._make_signature(identity, pubkey, tool, nonce)

        assert verify_caller(pubkey, sig, nonce, tool) is True

    def test_wrong_key_returns_false(self):
        """Signature from a different key must not verify."""
        signer = Identity()
        claimant = Identity()  # different key
        pubkey = claimant.pubkey_hex
        nonce = "abc"
        tool = "trustchain_list_servers"

        # Sign with the *signer* but claim the *claimant* pubkey
        message = f"{pubkey}:{tool}:{nonce}".encode("utf-8")
        sig = signer.sign(message).hex()

        assert verify_caller(pubkey, sig, nonce, tool) is False

    def test_tampered_message_returns_false(self):
        identity = Identity()
        pubkey = identity.pubkey_hex
        nonce = "nonce1"
        tool = "trustchain_get_history"
        sig = self._make_signature(identity, pubkey, tool, nonce)

        # Change nonce after signing — message no longer matches
        assert verify_caller(pubkey, sig, "different_nonce", tool) is False

    def test_tampered_tool_name_returns_false(self):
        identity = Identity()
        pubkey = identity.pubkey_hex
        nonce = "nonce2"
        tool = "trustchain_verify_chain"
        sig = self._make_signature(identity, pubkey, tool, nonce)

        assert verify_caller(pubkey, sig, nonce, "trustchain_crawl") is False

    def test_malformed_pubkey_hex_returns_false(self):
        identity = Identity()
        nonce = "n"
        tool = "trustchain_check_trust"
        sig = self._make_signature(identity, identity.pubkey_hex, tool, nonce)

        assert verify_caller("not-valid-hex!!", sig, nonce, tool) is False

    def test_malformed_signature_hex_returns_false(self):
        identity = Identity()
        pubkey = identity.pubkey_hex
        tool = "trustchain_check_trust"

        assert verify_caller(pubkey, "ZZZZ", "nonce", tool) is False

    def test_pubkey_wrong_length_returns_false(self):
        """A 16-byte pubkey (truncated) should be rejected."""
        identity = Identity()
        pubkey = identity.pubkey_hex  # 32 bytes = 64 hex chars
        # Truncate to 16 bytes worth of hex
        short_pubkey = pubkey[:32]
        nonce = "n"
        tool = "trustchain_check_trust"

        message = f"{pubkey}:{tool}:{nonce}".encode("utf-8")
        sig = identity.sign(message).hex()

        assert verify_caller(short_pubkey, sig, nonce, tool) is False

    def test_signature_wrong_length_returns_false(self):
        """A 32-byte signature (half length) should be rejected."""
        identity = Identity()
        pubkey = identity.pubkey_hex
        nonce = "n"
        tool = "trustchain_check_trust"

        message = f"{pubkey}:{tool}:{nonce}".encode("utf-8")
        full_sig = identity.sign(message).hex()
        # Truncate to 32 bytes worth
        half_sig = full_sig[:64]

        assert verify_caller(pubkey, half_sig, nonce, tool) is False

    def test_empty_nonce_still_verifies(self):
        """Empty nonce is allowed — the scheme still binds pubkey + tool."""
        identity = Identity()
        pubkey = identity.pubkey_hex
        tool = "trustchain_trust_score"
        nonce = ""
        sig = self._make_signature(identity, pubkey, tool, nonce)

        assert verify_caller(pubkey, sig, nonce, tool) is True

    def test_different_nonce_values_produce_different_sigs(self):
        """Nonces must make signatures non-replayable across requests."""
        identity = Identity()
        pubkey = identity.pubkey_hex
        tool = "trustchain_check_trust"

        sig1 = self._make_signature(identity, pubkey, tool, "nonce_a")
        sig2 = self._make_signature(identity, pubkey, tool, "nonce_b")

        assert sig1 != sig2
        # sig1 must NOT verify for nonce_b
        assert verify_caller(pubkey, sig1, "nonce_b", tool) is False


# ---------------------------------------------------------------------------
# _check_caller_auth unit tests
# ---------------------------------------------------------------------------

class TestCheckCallerAuth:
    """Unit tests for the _check_caller_auth() dispatch helper."""

    def _sign(self, identity: Identity, tool: str, nonce: str) -> str:
        msg = f"{identity.pubkey_hex}:{tool}:{nonce}".encode("utf-8")
        return identity.sign(msg).hex()

    def test_no_pubkey_passes_silently(self):
        result = _check_caller_auth("", "", "", "trustchain_check_trust")
        assert result is None

    def test_missing_signature_returns_none_with_warning(self, caplog):
        identity = Identity()
        with caplog.at_level(logging.WARNING, logger="trustchain.gateway.tools"):
            result = _check_caller_auth(
                identity.pubkey_hex, "", "", "trustchain_check_trust"
            )
        assert result is None
        assert "DEPRECATION" in caplog.text

    def test_valid_signature_returns_none(self):
        identity = Identity()
        tool = "trustchain_get_history"
        nonce = "ts_999"
        sig = self._sign(identity, tool, nonce)

        result = _check_caller_auth(identity.pubkey_hex, sig, nonce, tool)
        assert result is None

    def test_invalid_signature_returns_error_string(self):
        identity = Identity()
        other = Identity()
        tool = "trustchain_verify_chain"
        nonce = "ts_1"
        # Sign with *other* key but claim *identity* pubkey
        msg = f"{identity.pubkey_hex}:{tool}:{nonce}".encode("utf-8")
        bad_sig = other.sign(msg).hex()

        result = _check_caller_auth(identity.pubkey_hex, bad_sig, nonce, tool)
        assert result is not None
        assert "Caller verification failed" in result
        assert tool in result

    def test_missing_nonce_emits_warning_but_passes(self, caplog):
        identity = Identity()
        tool = "trustchain_crawl"
        nonce = ""
        # Sign with empty nonce — still a valid signature for that empty nonce
        msg = f"{identity.pubkey_hex}:{tool}:".encode("utf-8")
        sig = identity.sign(msg).hex()

        with caplog.at_level(logging.WARNING, logger="trustchain.gateway.tools"):
            result = _check_caller_auth(identity.pubkey_hex, sig, nonce, tool)

        # Signature is valid even with empty nonce — should pass
        assert result is None
        assert "DEPRECATION" in caplog.text  # warns about missing nonce

    def test_whitespace_only_signature_treated_as_absent(self, caplog):
        identity = Identity()
        with caplog.at_level(logging.WARNING, logger="trustchain.gateway.tools"):
            result = _check_caller_auth(
                identity.pubkey_hex, "   ", "", "trustchain_list_servers"
            )
        assert result is None
        assert "DEPRECATION" in caplog.text


# ---------------------------------------------------------------------------
# Integration: tools through FastMCP Client
# ---------------------------------------------------------------------------

class TestTrustToolsWithVerification:
    """Integration tests: call MCP tools with / without signatures."""

    def _setup(self):
        store = RecordStore()
        gw_identity = Identity()
        registry = UpstreamRegistry(gw_identity)
        config = UpstreamServer(name="test_server", command="echo", trust_threshold=0.2)
        registry.register_server(config)
        return store, registry

    def _make_server(self, store, registry):
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_trust_tools(mcp, registry, store)
        return mcp

    def _make_caller_sig(
        self, identity: Identity, tool: str, nonce: str
    ) -> str:
        msg = f"{identity.pubkey_hex}:{tool}:{nonce}".encode("utf-8")
        return identity.sign(msg).hex()

    # -- trustchain_check_trust --

    @pytest.mark.asyncio
    async def test_check_trust_no_sig_passes(self):
        """Backward compat: no signature → tool still works (warning only)."""
        store, registry = self._setup()
        mcp = self._make_server(store, registry)

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                "trustchain_check_trust", {"server_name": "test_server"}
            )
        assert "test_server" in result.content[0].text

    @pytest.mark.asyncio
    async def test_check_trust_valid_sig_passes(self):
        store, registry = self._setup()
        mcp = self._make_server(store, registry)
        caller = Identity()
        tool = "trustchain_check_trust"
        nonce = "42"
        sig = self._make_caller_sig(caller, tool, nonce)

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                tool,
                {
                    "server_name": "test_server",
                    "caller_pubkey": caller.pubkey_hex,
                    "caller_signature": sig,
                    "caller_nonce": nonce,
                },
            )
        text = result.content[0].text
        assert "test_server" in text
        assert "Trust Score" in text

    @pytest.mark.asyncio
    async def test_check_trust_invalid_sig_rejected(self):
        store, registry = self._setup()
        mcp = self._make_server(store, registry)
        caller = Identity()
        evil = Identity()  # different key
        tool = "trustchain_check_trust"
        nonce = "99"
        # Sign with evil key, claim caller pubkey
        msg = f"{caller.pubkey_hex}:{tool}:{nonce}".encode("utf-8")
        bad_sig = evil.sign(msg).hex()

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                tool,
                {
                    "server_name": "test_server",
                    "caller_pubkey": caller.pubkey_hex,
                    "caller_signature": bad_sig,
                    "caller_nonce": nonce,
                },
            )
        text = result.content[0].text
        assert "Caller verification failed" in text

    # -- trustchain_list_servers --

    @pytest.mark.asyncio
    async def test_list_servers_valid_sig_passes(self):
        store, registry = self._setup()
        mcp = self._make_server(store, registry)
        caller = Identity()
        tool = "trustchain_list_servers"
        nonce = "ts1"
        sig = self._make_caller_sig(caller, tool, nonce)

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                tool,
                {
                    "caller_pubkey": caller.pubkey_hex,
                    "caller_signature": sig,
                    "caller_nonce": nonce,
                },
            )
        assert "test_server" in result.content[0].text

    @pytest.mark.asyncio
    async def test_list_servers_invalid_sig_rejected(self):
        store, registry = self._setup()
        mcp = self._make_server(store, registry)
        caller = Identity()
        tool = "trustchain_list_servers"
        nonce = "ts2"
        # Use a completely random invalid sig
        bad_sig = "aa" * 64  # 64 bytes of 0xAA — syntactically valid hex, wrong value

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                tool,
                {
                    "caller_pubkey": caller.pubkey_hex,
                    "caller_signature": bad_sig,
                    "caller_nonce": nonce,
                },
            )
        assert "Caller verification failed" in result.content[0].text

    # -- trustchain_get_history --

    @pytest.mark.asyncio
    async def test_get_history_valid_sig_passes(self):
        store, registry = self._setup()
        mcp = self._make_server(store, registry)
        caller = Identity()
        tool = "trustchain_get_history"
        nonce = "h1"
        sig = self._make_caller_sig(caller, tool, nonce)

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                tool,
                {
                    "server_name": "test_server",
                    "caller_pubkey": caller.pubkey_hex,
                    "caller_signature": sig,
                    "caller_nonce": nonce,
                },
            )
        # Either "No interaction history" or actual history — either is a pass
        assert result.content[0].text  # non-empty response

    @pytest.mark.asyncio
    async def test_get_history_invalid_sig_rejected(self):
        store, registry = self._setup()
        mcp = self._make_server(store, registry)
        caller = Identity()
        tool = "trustchain_get_history"
        nonce = "h2"
        bad_sig = "bb" * 64

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                tool,
                {
                    "server_name": "test_server",
                    "caller_pubkey": caller.pubkey_hex,
                    "caller_signature": bad_sig,
                    "caller_nonce": nonce,
                },
            )
        assert "Caller verification failed" in result.content[0].text

    # -- trustchain_verify_chain --

    @pytest.mark.asyncio
    async def test_verify_chain_invalid_sig_rejected(self):
        store, registry = self._setup()
        mcp = self._make_server(store, registry)
        caller = Identity()
        tool = "trustchain_verify_chain"
        nonce = "vc1"
        bad_sig = "cc" * 64

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                tool,
                {
                    "server_name": "test_server",
                    "caller_pubkey": caller.pubkey_hex,
                    "caller_signature": bad_sig,
                    "caller_nonce": nonce,
                },
            )
        assert "Caller verification failed" in result.content[0].text

    # -- trustchain_trust_score --

    @pytest.mark.asyncio
    async def test_trust_score_invalid_sig_rejected(self):
        store, registry = self._setup()
        mcp = self._make_server(store, registry)
        caller = Identity()
        tool = "trustchain_trust_score"
        nonce = "sc1"
        bad_sig = "dd" * 64

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                tool,
                {
                    "server_name": "test_server",
                    "caller_pubkey": caller.pubkey_hex,
                    "caller_signature": bad_sig,
                    "caller_nonce": nonce,
                },
            )
        assert "Caller verification failed" in result.content[0].text

    # -- trustchain_crawl (no upstream data so just checking sig path) --

    @pytest.mark.asyncio
    async def test_crawl_invalid_sig_rejected(self):
        store, registry = self._setup()
        mcp = self._make_server(store, registry)
        caller = Identity()
        tool = "trustchain_crawl"
        nonce = "cr1"
        bad_sig = "ee" * 64

        from fastmcp import Client

        async with Client(mcp) as client:
            result = await client.call_tool(
                tool,
                {
                    "server_name": "test_server",
                    "caller_pubkey": caller.pubkey_hex,
                    "caller_signature": bad_sig,
                    "caller_nonce": nonce,
                },
            )
        assert "Caller verification failed" in result.content[0].text
