"""Native trust query tools exposed to LLMs through the gateway.

v2: Added trustchain_crawl and trustchain_trust_score tools.
When trust_engine is provided, uses v2 BlockStore-based data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from gateway.registry import UpstreamRegistry
    from trustchain.store import RecordStore
    from trustchain.trust import TrustEngine

from trustchain.trust import compute_chain_trust, compute_trust


def register_trust_tools(
    mcp,
    registry: UpstreamRegistry,
    store: RecordStore,
    trust_engine: Optional[TrustEngine] = None,
):
    """Register TrustChain query tools on a FastMCP server instance.

    v2: When trust_engine is provided, uses TrustEngine for scoring
    and BlockStore for data retrieval.
    """

    def _get_trust(pubkey: str) -> float:
        if trust_engine:
            return trust_engine.compute_trust(pubkey)
        return compute_trust(pubkey, store)

    def _get_interaction_count(pubkey: str) -> int:
        if trust_engine:
            return len(trust_engine.store.get_chain(pubkey))
        return len(store.get_records_for(pubkey))

    @mcp.tool(name="trustchain_check_trust")
    async def trustchain_check_trust(server_name: str) -> str:
        """Check the current trust score for an upstream MCP server."""
        identity = registry.identity_for(server_name)
        if identity is None:
            return f"Unknown server: {server_name}. Use trustchain_list_servers to see available servers."

        pubkey = identity.pubkey_hex
        trust = _get_trust(pubkey)
        interaction_count = _get_interaction_count(pubkey)
        threshold = registry.threshold_for(server_name)
        is_bootstrap = interaction_count < 3

        lines = [
            f"Server: {server_name}",
            f"Trust Score: {trust:.3f}",
            f"Threshold: {threshold:.3f}",
            f"Interactions: {interaction_count}",
            f"Status: {'bootstrap (always allowed)' if is_bootstrap else 'established'}",
            f"Public Key: {pubkey[:16]}...",
        ]
        if not is_bootstrap and trust < threshold:
            lines.append("WARNING: Trust is below threshold — future calls may be blocked")
        return "\n".join(lines)

    @mcp.tool(name="trustchain_get_history")
    async def trustchain_get_history(server_name: str, limit: int = 10) -> str:
        """Get recent interaction history with an upstream MCP server."""
        identity = registry.identity_for(server_name)
        if identity is None:
            return f"Unknown server: {server_name}"

        pubkey = identity.pubkey_hex

        # v2: Use BlockStore chain if available
        if trust_engine:
            chain = trust_engine.store.get_chain(pubkey)
            if not chain:
                return f"No interaction history with server '{server_name}'"
            recent = chain[-limit:][::-1]
            lines = [f"Interaction history for '{server_name}' (showing {len(recent)}/{len(chain)}):"]
            lines.append("")
            for i, block in enumerate(recent, 1):
                tx = block.transaction
                lines.append(
                    f"  {i}. type={tx.get('interaction_type', 'unknown')} "
                    f"outcome={tx.get('outcome', 'unknown')} "
                    f"seq={block.sequence_number} hash={block.block_hash[:12]}..."
                )
            return "\n".join(lines)

        # v1: Use RecordStore
        records = store.get_records_for(pubkey)
        if not records:
            return f"No interaction history with server '{server_name}'"

        recent = records[-limit:][::-1]
        lines = [f"Interaction history for '{server_name}' (showing {len(recent)}/{len(records)}):"]
        lines.append("")
        for i, r in enumerate(recent, 1):
            lines.append(
                f"  {i}. type={r.interaction_type} outcome={r.outcome} "
                f"seq={r.seq_a}/{r.seq_b} hash={r.record_hash[:12]}... "
                f"verified={'yes' if r.sig_a and r.sig_b else 'no'}"
            )
        return "\n".join(lines)

    @mcp.tool(name="trustchain_list_servers")
    async def trustchain_list_servers() -> str:
        """List all upstream MCP servers and their current trust scores."""
        names = registry.server_names
        if not names:
            return "No upstream servers configured."

        lines = ["Upstream MCP servers:"]
        lines.append("")
        for name in sorted(names):
            identity = registry.identity_for(name)
            if identity is None:
                continue
            pubkey = identity.pubkey_hex
            trust = _get_trust(pubkey)
            count = _get_interaction_count(pubkey)
            threshold = registry.threshold_for(name)
            status = "bootstrap" if count < 3 else "established"
            tc_url = registry.trustchain_url_for(name)
            tc_info = f" tc_url={tc_url}" if tc_url else ""
            lines.append(
                f"  {name}: trust={trust:.3f} threshold={threshold:.3f} "
                f"interactions={count} status={status}{tc_info}"
            )
        return "\n".join(lines)

    @mcp.tool(name="trustchain_verify_chain")
    async def trustchain_verify_chain(server_name: str) -> str:
        """Verify the blockchain integrity for an upstream MCP server."""
        identity = registry.identity_for(server_name)
        if identity is None:
            return f"Unknown server: {server_name}. Use trustchain_list_servers to see available servers."

        pubkey = identity.pubkey_hex

        # v2: Use TrustEngine chain integrity
        if trust_engine:
            integrity = trust_engine.compute_chain_integrity(pubkey)
            chain_length = trust_engine.store.get_latest_seq(pubkey)
            combined = trust_engine.compute_trust(pubkey)
            status = "VALID" if integrity >= 1.0 else "INTEGRITY ISSUES"
            return (
                f"Server: {server_name}\n"
                f"Chain Length: {chain_length}\n"
                f"Chain Integrity: {integrity:.3f}\n"
                f"Combined Trust: {combined:.3f}\n"
                f"Status: {status}"
            )

        # v1: Use RecordStore + PersonalChain
        records = store.get_records_for(pubkey)
        if not records:
            return f"No chain data for server '{server_name}' (no interactions yet)"

        chain_trust = compute_chain_trust(pubkey, store)

        from trustchain.chain import PersonalChain
        from trustchain.exceptions import ChainError

        try:
            chain = PersonalChain.from_records(pubkey, records)
            chain.validate()
            integrity = chain.integrity_score()
            return (
                f"Server: {server_name}\n"
                f"Chain Length: {chain.length}\n"
                f"Chain Integrity: {integrity:.3f}\n"
                f"Chain Trust: {chain_trust:.3f}\n"
                f"Status: VALID"
            )
        except ChainError as e:
            return (
                f"Server: {server_name}\n"
                f"Chain Trust: {chain_trust:.3f}\n"
                f"Status: INVALID\n"
                f"Error: {e}"
            )

    @mcp.tool(name="trustchain_crawl")
    async def trustchain_crawl(server_name: str) -> str:
        """Crawl a server's TrustChain data to verify its history.

        v2: Uses BlockStore-based crawling if available.
        """
        identity = registry.identity_for(server_name)
        if identity is None:
            return f"Unknown server: {server_name}"

        pubkey = identity.pubkey_hex

        # v2: Use BlockStoreCrawler
        if trust_engine:
            from trustchain.crawler import BlockStoreCrawler
            crawler = BlockStoreCrawler(trust_engine.store)
            report = crawler.detect_tampering()

            block_count = trust_engine.store.get_block_count()
            if report.is_clean:
                return f"Server '{server_name}': chain is clean ({block_count} blocks)"
            else:
                lines = [f"Server '{server_name}': {report.issue_count} issue(s) found"]
                for issue in report.chain_gaps:
                    lines.append(f"  GAP: {issue}")
                for issue in report.hash_breaks:
                    lines.append(f"  HASH BREAK: {issue}")
                for issue in report.signature_failures:
                    lines.append(f"  SIG FAIL: {issue}")
                for issue in report.entanglement_issues:
                    lines.append(f"  ENTANGLE: {issue}")
                for issue in report.orphan_proposals:
                    lines.append(f"  ORPHAN: {issue}")
                return "\n".join(lines)

        # v1: Use ChainCrawler
        records = store.get_records_for(pubkey)
        if not records:
            return f"No chain data for '{server_name}'"

        from trustchain.crawler import ChainCrawler
        crawler = ChainCrawler(records)
        report = crawler.detect_tampering()

        if report.is_clean:
            return f"Server '{server_name}': chain is clean ({len(records)} records)"
        else:
            lines = [f"Server '{server_name}': {report.issue_count} issue(s) found"]
            for issue in report.chain_gaps:
                lines.append(f"  GAP: {issue}")
            for issue in report.hash_breaks:
                lines.append(f"  HASH BREAK: {issue}")
            for issue in report.signature_failures:
                lines.append(f"  SIG FAIL: {issue}")
            for issue in report.entanglement_issues:
                lines.append(f"  ENTANGLE: {issue}")
            return "\n".join(lines)

    @mcp.tool(name="trustchain_trust_score")
    async def trustchain_trust_score(server_name: str) -> str:
        """Get detailed trust score breakdown for a server.

        v2: Shows chain integrity, netflow, and statistical components.
        """
        identity = registry.identity_for(server_name)
        if identity is None:
            return f"Unknown server: {server_name}"

        pubkey = identity.pubkey_hex

        if trust_engine:
            integrity = trust_engine.compute_chain_integrity(pubkey)
            netflow = trust_engine.compute_netflow_score(pubkey)
            statistical = trust_engine.compute_statistical_score(pubkey)
            combined = trust_engine.compute_trust(pubkey)
            lines = [
                f"Server: {server_name}",
                f"Combined Trust: {combined:.3f}",
                f"  Chain Integrity: {integrity:.3f} (weight: 0.3)",
                f"  NetFlow Score: {netflow:.3f} (weight: 0.4)",
                f"  Statistical Score: {statistical:.3f} (weight: 0.3)",
            ]
        else:
            trust = compute_trust(pubkey, store)
            chain_trust = compute_chain_trust(pubkey, store)
            lines = [
                f"Server: {server_name}",
                f"Base Trust: {trust:.3f}",
                f"Chain Trust: {chain_trust:.3f}",
            ]
        return "\n".join(lines)
