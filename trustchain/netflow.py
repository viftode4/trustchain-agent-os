"""NetFlow — graph-based Sybil resistance for TrustChain v2.

Implements max-flow based trust computation per the TrustChain/Tribler NetFlow
algorithm. Sybil clusters have no real contributions to honest agents, so their
max-flow from seed nodes is ~0.

Reference: Otte, de Vos, Pouwelse (2020) — "TrustChain: A Sybil-resistant
scalable blockchain"
"""

from __future__ import annotations

import copy
import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set

from trustchain.blockstore import BlockStore
from trustchain.exceptions import NetFlowError

logger = logging.getLogger("trustchain.netflow")


class NetFlowTrust:
    """Max-flow based trust computation for Sybil resistance.

    Trust flows from seed nodes (trusted bootstrapping identities) through
    the contribution graph. Agents with real interaction history receive
    trust proportional to their contribution volume. Sybil clusters, having
    no real contributions to honest nodes, receive near-zero trust.
    """

    def __init__(
        self,
        store: BlockStore,
        seed_nodes: List[str],
    ) -> None:
        if not seed_nodes:
            raise NetFlowError(detail="At least one seed node required")
        self.store = store
        self.seeds = seed_nodes

    def build_contribution_graph(self) -> Dict[str, Dict[str, float]]:
        """Build a directed contribution graph from all stored half-blocks.

        Edge weight = total interaction volume between agents (sum of completed
        transactions). Each proposal/agreement pair counts as 1 unit of
        contribution in both directions.

        Returns: {source_pubkey: {target_pubkey: weight}}
        """
        graph: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

        for pubkey in self.store.get_all_pubkeys():
            chain = self.store.get_chain(pubkey)
            for block in chain:
                source = block.public_key
                target = block.link_public_key
                if source == target:
                    continue  # Skip self-loops
                # Each half-block represents a contribution of 0.5
                # (proposal + agreement = 1.0 total per transaction)
                graph[source][target] += 0.5

        return {k: dict(v) for k, v in graph.items()}

    def _bfs_capacity(
        self,
        residual: Dict[str, Dict[str, float]],
        source: str,
        sink: str,
        parent: Dict[str, Optional[str]],
    ) -> float:
        """BFS to find augmenting path in residual graph. Returns bottleneck capacity."""
        visited: Set[str] = {source}
        queue: deque = deque([source])
        parent.clear()
        parent[source] = None

        while queue:
            u = queue.popleft()
            for v, cap in residual.get(u, {}).items():
                if v not in visited and cap > 1e-9:
                    visited.add(v)
                    parent[v] = u
                    if v == sink:
                        # Trace back to find bottleneck
                        path_flow = float("inf")
                        node = sink
                        while parent[node] is not None:
                            prev = parent[node]
                            path_flow = min(path_flow, residual[prev][node])
                            node = prev
                        return path_flow
                    queue.append(v)

        return 0.0

    def _max_flow(
        self,
        graph: Dict[str, Dict[str, float]],
        source: str,
        sink: str,
    ) -> float:
        """Edmonds-Karp max-flow algorithm (BFS-based Ford-Fulkerson)."""
        if source == sink:
            return float("inf")

        # Build residual graph
        residual: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for u in graph:
            for v, cap in graph[u].items():
                residual[u][v] += cap

        total_flow = 0.0
        parent: Dict[str, Optional[str]] = {}

        while True:
            path_flow = self._bfs_capacity(residual, source, sink, parent)
            if path_flow <= 1e-9:
                break

            # Update residual capacities
            node = sink
            while parent[node] is not None:
                prev = parent[node]
                residual[prev][node] -= path_flow
                residual[node][prev] += path_flow
                node = prev

            total_flow += path_flow

        return total_flow

    def _prepare_graph_with_super_source(
        self, graph: Dict[str, Dict[str, float]]
    ) -> tuple:
        """Add a virtual super-source to the graph (on a copy) and compute max_possible.

        Returns (augmented_graph, super_source_name, max_possible_flow).
        """
        super_source = "__super_source__"
        assert super_source not in graph, "Node name collision with virtual super-source"

        augmented = copy.deepcopy(graph)
        augmented[super_source] = {}
        for seed in self.seeds:
            if seed in graph or any(seed in g for g in graph.values()):
                augmented[super_source][seed] = float("inf")

        # max_possible = total outgoing capacity from seeds (not counting super-source edges)
        max_possible = sum(
            sum(graph.get(seed, {}).values())
            for seed in self.seeds
        )

        return augmented, super_source, max_possible

    def compute_trust(self, target_pubkey: str) -> float:
        """Compute trust score for a target agent via max-flow from seeds.

        The score is the sum of max-flow from each seed node to the target,
        normalized to [0.0, 1.0].

        Agents in Sybil clusters have no real contributions to honest nodes,
        so their max-flow from any seed is ~0.
        """
        if target_pubkey in self.seeds:
            return 1.0

        graph = self.build_contribution_graph()
        if not graph:
            return 0.0

        augmented, super_source, max_possible = self._prepare_graph_with_super_source(graph)

        if max_possible <= 0:
            return 0.0

        raw_flow = self._max_flow(augmented, super_source, target_pubkey)

        return min(raw_flow / max_possible, 1.0)

    def compute_all_scores(self) -> Dict[str, float]:
        """Batch computation of trust scores for all known agents.

        Builds the graph once and reuses it for efficiency.
        """
        all_pubkeys = self.store.get_all_pubkeys()
        if not all_pubkeys:
            return {}

        graph = self.build_contribution_graph()
        if not graph:
            return {pk: (1.0 if pk in self.seeds else 0.0) for pk in all_pubkeys}

        augmented, super_source, max_possible = self._prepare_graph_with_super_source(graph)

        scores: Dict[str, float] = {}
        for pk in all_pubkeys:
            if pk in self.seeds:
                scores[pk] = 1.0
            elif max_possible <= 0:
                scores[pk] = 0.0
            else:
                # Each max-flow call needs a fresh graph since _max_flow modifies residual
                raw_flow = self._max_flow(augmented, super_source, pk)
                scores[pk] = min(raw_flow / max_possible, 1.0)

        return scores
