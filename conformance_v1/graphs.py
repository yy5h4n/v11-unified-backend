"""Semantic and physical provenance graphs plus the reference splitter.

Normative doc section 5 defines ``independence_unit_id`` as the connected
component obtained by joining evidence that shares a household, participant,
longitudinal subject, copied-rule ancestor or participant-authored template
ancestor; study/recruitment-pool membership is recorded but does NOT merge
households.  Section 11 defines two non-interchangeable graphs (semantic and
physical) and the track-specific splitter over connected components with a
frozen seed and deterministic quota solver.
"""

from __future__ import annotations

import random
from typing import Any, Iterable

from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1 import enums


class UnionFind:
    def __init__(self, nodes: Iterable[str]):
        self.parent = {n: n for n in nodes}
        self.rank = {n: 0 for n in nodes}

    def find(self, n: str) -> str:
        while self.parent[n] != n:
            self.parent[n] = self.parent[self.parent[n]]
            n = self.parent[n]
        return n

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1

    def components(self) -> list[list[str]]:
        groups: dict[str, list[str]] = {}
        for n in self.parent:
            groups.setdefault(self.find(n), []).append(n)
        return [sorted(g) for g in groups.values()]


_SEMANTIC_KEYS = ("household_id", "participant_id", "longitudinal_subject", "template_ancestor", "copied_rule_ancestor")


def _unit_key(u: dict, key: str) -> Any:
    return u.get(key) or u.get("provenance", {}).get(key)


def build_semantic_provenance_graph(units: list[dict]) -> dict[str, Any]:
    """Semantic graph nodes are evidence units; edges join independence-relevant
    provenance fields only.  Study and recruitment pool are clustering variables
    and never merge otherwise distinct households."""
    ids = [u["id"] for u in units]
    uf = UnionFind(ids)
    edges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key in _SEMANTIC_KEYS:
        anchor: dict[Any, str] = {}
        for u in units:
            val = _unit_key(u, key)
            if val is None:
                continue
            if val in anchor:
                a, b = sorted([u["id"], anchor[val]])
                if (a, b) not in seen:
                    seen.add((a, b))
                    uf.union(a, b)
                    edges.append((a, b, key))
            else:
                anchor[val] = u["id"]
    return {
        "kind": "semantic_provenance_graph",
        "nodes": ids,
        "edges": [{"a": a, "b": b, "relation": r} for (a, b, r) in edges],
        "components": uf.components(),
        "independence_unit_ids": ["IU-" + "-".join(c) for c in uf.components()],
    }


_PHYSICAL_KEYS = ("building_id", "vehicle_id", "exogenous_trace_ref", "simulator_config_id", "weather_ref", "generator_seed", "model_ancestor")


def build_physical_provenance_graph(processes: list[dict]) -> dict[str, Any]:
    """Physical graph nodes are processes; edges join shared building/vehicle,
    overlapping source intervals, exogenous traces, weather, simulator
    configuration, generator seeds and model ancestry."""
    ids = [p["id"] for p in processes]
    uf = UnionFind(ids)
    edges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for key in _PHYSICAL_KEYS:
        anchor: dict[Any, str] = {}
        for p in processes:
            val = p.get("physical_meta", {}).get(key)
            if val is None:
                continue
            if val in anchor:
                a, b = sorted([p["id"], anchor[val]])
                if (a, b) not in seen:
                    seen.add((a, b))
                    uf.union(a, b)
                    edges.append((a, b, key))
            else:
                anchor[val] = p["id"]
    return {
        "kind": "physical_provenance_graph",
        "nodes": ids,
        "edges": [{"a": a, "b": b, "relation": r} for (a, b, r) in edges],
        "components": uf.components(),
    }


def partition_semantic(units: list[dict], seed: int, ratios: dict[str, float]) -> dict[str, str]:
    """Partition evidence by independence component (not unit) into
    semantic_discovery / semantic_development / semantic_confirmatory.

    Components are ordered stably and shuffled with the frozen seed; the
    confirmatory partition is held out untouched."""
    graph = build_semantic_provenance_graph(units)
    components = graph["components"]
    rng = random.Random(seed)
    order = sorted(components, key=lambda c: (len(c), c))
    rng.shuffle(order)
    total = sum(len(c) for c in order)
    disc = max(0, int(round(ratios.get("discovery", 0.0) * total)))
    dev = max(0, int(round(ratios.get("development", 0.0) * total)))
    assign: dict[str, str] = {}
    for comp in order:
        if disc > 0:
            part = "semantic_discovery"
            disc -= len(comp)
        elif dev > 0:
            part = "semantic_development"
            dev -= len(comp)
        else:
            part = "semantic_confirmatory"
        for uid in comp:
            assign[uid] = part
    return assign


def assign_splits(
    episodes: list[dict],
    track: str,
    config: dict,
    seed: int,
    responsibility_of: dict[str, str] | None = None,
    process_of: dict[str, str] | None = None,
    paraphrase_of: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Return (episode_id -> split, reason_codes).

    Builds the track-specific conflict graph over episodes, computes transitive
    closure, and assigns components deterministically with a frozen seed and a
    greedy quota solver.  Infeasible quotas produce QUOTA_INFEASIBLE (the
    affected responsibility becomes ``preview_only``)."""
    if track not in enums.TRACKS:
        raise ConformanceError("INVALID_TRANSITION", f"unknown track {track!r}")
    ids = [e["id"] for e in episodes]
    uf = UnionFind(ids)
    resp_of: dict[str, str] = dict(responsibility_of or {})
    proc_of: dict[str, str] = dict(process_of or {})
    para_of: dict[str, str] = dict(paraphrase_of or {})

    def _link(a: str, b: str) -> None:
        if a and b:
            uf.union(a, b)

    if track == "held_out_responsibility_family":
        for e in episodes:
            for f in episodes:
                if resp_of.get(e["id"]) and resp_of.get(e["id"]) == resp_of.get(f["id"]):
                    _link(e["id"], f["id"])
    if track == "seen_responsibility_unseen_process":
        for e in episodes:
            for f in episodes:
                if proc_of.get(e["id"]) and proc_of.get(e["id"]) == proc_of.get(f["id"]):
                    _link(e["id"], f["id"])
    if track == "unseen_paraphrase":
        for e in episodes:
            for f in episodes:
                if para_of.get(e["id"]) and para_of.get(e["id"]) == para_of.get(f["id"]):
                    _link(e["id"], f["id"])

    components = uf.components()
    rng = random.Random(seed)
    order = sorted(components, key=lambda c: (len(c), c))
    rng.shuffle(order)
    splits = ("train", "dev", "test")
    loads = {s: 0 for s in splits}
    assignment: dict[str, str] = {}
    for comp in order:
        target = min(loads, key=lambda s: (loads[s], splits.index(s)))
        for eid in comp:
            assignment[eid] = target
            loads[target] += 1

    reason_codes: set[str] = set()
    min_split = config.get("quotas", {}).get("per_split_min_episodes", {})
    min_stratum = config.get("quotas", {}).get("per_stratum_min_episodes", {})
    for eid in ids:
        for s in splits:
            if assignment[eid] == s and loads[s] < min_split.get(s, 0):
                reason_codes.add("QUOTA_INFEASIBLE")
    for stratum, minimum in min_stratum.items():
        count = sum(1 for e in episodes if e.get("physical_label") == stratum)
        if count < minimum:
            reason_codes.add("QUOTA_INFEASIBLE")
    return assignment, sorted(reason_codes)


def split_leakage_audit(
    episodes: list[dict],
    assignment: dict[str, str],
    physical_component_of: dict[str, str],
) -> list[str]:
    """Raise SPLIT_LEAKAGE if any physical connected component spans splits."""
    codes: set[str] = set()
    by_component: dict[str, set[str]] = {}
    for e in episodes:
        comp = physical_component_of.get(e["id"], e.get("physical_process_id", e["id"]))
        by_component.setdefault(comp, set()).add(assignment.get(e["id"], "none"))
    for comp, split_set in by_component.items():
        if len(split_set) > 1:
            codes.add("SPLIT_LEAKAGE")
    return sorted(codes)
