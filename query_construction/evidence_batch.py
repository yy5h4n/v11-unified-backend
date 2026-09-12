"""Fail-closed records for evidence-grounded query construction batches.

This module deliberately does not infer that a plausible query is human-authored,
that a route label proves support, or that a passing model run proves admission.
It validates provenance and keeps the independent evidence gates separate.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .evidence_execution import verify_execution_file


EVIDENCE_CLASSES = {
    "human_explicit",
    "domain_or_product_inferred",
    "evidence_grounded_synthetic",
    "pure_hypothetical",
}
CONSTRUCTION_MODES = {
    "verbatim_delegation",
    "faithful_paraphrase",
    "evidence_derived",
    "evidence_composite",
    "hypothetical",
}
GATE_NAMES = (
    "need_basis",
    "rewrite_fidelity",
    "backend_support",
    "scenario_feasibility",
    "evaluation_validity",
    "mechanism_relevance",
    "native_execution",
    "model_execution",
)
GATE_STATUSES = {"passed", "failed", "pending", "not_applicable"}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def _exact_keys(value: Any, required: set[str], optional: set[str], label: str) -> None:
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ValueError(f"{label} has missing or unexpected fields")


def _validate_source(source: dict[str, Any]) -> None:
    _exact_keys(source, {
        "id", "title", "url", "publisher", "retrieved_at", "collection_kind",
        "evidence_class", "license_status", "redistribution_note", "source_text",
        "locator", "exposure",
    }, {"published_at"}, "source")
    for key in set(source) - {"published_at"}:
        _text(source[key], f"source.{key}")
    if source["evidence_class"] not in EVIDENCE_CLASSES - {"evidence_grounded_synthetic", "pure_hypothetical"}:
        raise ValueError("a source is evidence, not a synthetic query")
    parsed = urlparse(source["url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source.url must be an absolute public HTTP(S) locator")
    if source["exposure"] not in {"development", "validation_frozen_before_read", "previously_exposed"}:
        raise ValueError("unknown source exposure status")


def _validate_quote(source: dict[str, Any], citation: dict[str, Any]) -> None:
    _exact_keys(citation, {"source_id", "quote", "contribution"}, set(), "citation")
    quote = _text(citation["quote"], "citation.quote")
    _text(citation["contribution"], "citation.contribution")
    if citation["source_id"] != source["id"] or source["source_text"].count(quote) != 1:
        raise ValueError("citation must be one unique exact substring of its source_text")


def _validate_gate(name: str, gate: dict[str, Any]) -> None:
    _exact_keys(gate, {"status", "evidence", "owner"}, set(), f"gate.{name}")
    if gate["status"] not in GATE_STATUSES:
        raise ValueError(f"gate.{name} has unknown status")
    _text(gate["evidence"], f"gate.{name}.evidence")
    if gate["owner"] not in {"data", "infrastructure", "model", "unresolved"}:
        raise ValueError(f"gate.{name} has unknown failure owner")
    if name != "model_execution" and gate["owner"] == "model":
        raise ValueError("only model_execution may attribute a gate to the model")


def validate_batch(document: dict[str, Any], *, base_dir: Path | None = None) -> dict[str, Any]:
    """Validate a frozen batch and return denominator-explicit accounting."""
    _exact_keys(document, {"schema", "batch_id", "frozen_at", "sources", "items", "costs"},
                {"notes"}, "batch")
    if document["schema"] != "evidence-query-batch.v1":
        raise ValueError("unsupported evidence batch schema")
    _text(document["batch_id"], "batch_id")
    _text(document["frozen_at"], "frozen_at")
    if not isinstance(document["sources"], list) or not document["sources"]:
        raise ValueError("nonempty sources required")
    if not isinstance(document["items"], list) or not document["items"]:
        raise ValueError("nonempty items required")
    sources: dict[str, dict[str, Any]] = {}
    for source in document["sources"]:
        _validate_source(source)
        if source["id"] in sources:
            raise ValueError("duplicate source id")
        sources[source["id"]] = source

    item_ids: set[str] = set()
    terminal = Counter()
    evidence_classes = Counter()
    splits = Counter()
    owners = Counter()
    admitted = 0
    for item in document["items"]:
        _exact_keys(item, {
            "id", "split", "query", "classification", "construction", "citations",
            "scenario", "public_conditions", "gates", "decision", "decision_reason",
        }, {"execution_evidence"}, "item")
        identifier = _text(item["id"], "item.id")
        if identifier in item_ids:
            raise ValueError("duplicate item id")
        item_ids.add(identifier)
        if item["split"] not in {"development", "validation"}:
            raise ValueError("item.split must be development or validation")
        if item["classification"] not in EVIDENCE_CLASSES:
            raise ValueError("unknown item evidence classification")
        if item["decision"] not in {"accepted_core", "accepted_calibration", "rejected", "pending"}:
            raise ValueError("unknown item decision")
        _text(item["query"], "item.query")
        _text(item["decision_reason"], "item.decision_reason")
        construction = item["construction"]
        _exact_keys(construction, {"mode", "changes", "uncertainties"}, set(), "construction")
        if construction["mode"] not in CONSTRUCTION_MODES:
            raise ValueError("unknown construction mode")
        for key in ("changes", "uncertainties"):
            if not isinstance(construction[key], list) or any(not isinstance(x, str) or not x.strip() for x in construction[key]):
                raise ValueError(f"construction.{key} must be a text list")
        if construction["mode"] == "hypothetical" and item["classification"] != "pure_hypothetical":
            raise ValueError("hypothetical construction must remain classified hypothetical")
        if item["classification"] == "evidence_grounded_synthetic":
            if construction["mode"] not in {"evidence_derived", "evidence_composite"} or not construction["changes"]:
                raise ValueError("evidence-grounded synthesis must declare a constructive mode and changes")
        if construction["mode"] == "evidence_composite" and len({c["source_id"] for c in item["citations"]}) < 2:
            raise ValueError("evidence_composite requires at least two sources")
        if item["classification"] == "human_explicit":
            if construction["mode"] not in {"verbatim_delegation", "faithful_paraphrase"}:
                raise ValueError("constructed meaning cannot be labelled human_explicit")
            if any(sources[c["source_id"]]["evidence_class"] != "human_explicit" for c in item["citations"]):
                raise ValueError("human_explicit item cites non-human evidence")
        if not isinstance(item["citations"], list) or not item["citations"]:
            raise ValueError("each item needs source citations")
        for citation in item["citations"]:
            if citation.get("source_id") not in sources:
                raise ValueError("citation references unknown source")
            _validate_quote(sources[citation["source_id"]], citation)
        if item["classification"] == "domain_or_product_inferred" and not any(
                sources[c["source_id"]]["evidence_class"] == "domain_or_product_inferred"
                for c in item["citations"]):
            raise ValueError("product/domain inference needs at least one product/domain source")
        if item["split"] == "validation" and any(
                sources[c["source_id"]]["exposure"] != "validation_frozen_before_read"
                for c in item["citations"]):
            raise ValueError("validation item uses a source not frozen before reading")

        scenario = item["scenario"]
        _exact_keys(scenario, {"route_id", "change_scope", "binding", "limitations"}, set(), "scenario")
        if scenario["change_scope"] not in {"reuse", "limited_adaptation", "new_backend", "unmatched"}:
            raise ValueError("unknown scenario change scope")
        _text(scenario["route_id"], "scenario.route_id")
        if not isinstance(scenario["binding"], dict) or not scenario["binding"]:
            raise ValueError("scenario.binding must be explicit")
        if not isinstance(scenario["limitations"], list) or any(not isinstance(x, str) or not x.strip() for x in scenario["limitations"]):
            raise ValueError("scenario.limitations must be a text list")
        if not isinstance(item["public_conditions"], list):
            raise ValueError("public_conditions must be a list")
        if set(item["gates"]) != set(GATE_NAMES):
            raise ValueError("all independent gates must be present exactly once")
        for name in GATE_NAMES:
            _validate_gate(name, item["gates"][name])

        required = [name for name in GATE_NAMES if name != "model_execution"]
        objective_pass = all(
            item["gates"][name]["status"] == "passed"
            or (item["decision"] == "accepted_calibration"
                and name == "mechanism_relevance"
                and item["gates"][name]["status"] == "not_applicable")
            for name in required
        )
        if item["decision"].startswith("accepted") and not objective_pass:
            raise ValueError("accepted item has an unpassed non-model validity gate")
        if item["decision"] == "accepted_core":
            if item["gates"]["mechanism_relevance"]["status"] != "passed":
                raise ValueError("core item requires mechanism relevance evidence")
        if item["decision"] == "accepted_calibration" and item["gates"]["mechanism_relevance"]["status"] != "not_applicable":
            raise ValueError("calibration item must explicitly mark mechanism relevance not_applicable")
        objective_statuses = [item["gates"][name]["status"] for name in required]
        if item["decision"] == "rejected" and "failed" not in objective_statuses:
            raise ValueError("rejected item requires at least one failed objective gate")
        if item["decision"] == "pending" and (
                "pending" not in objective_statuses or "failed" in objective_statuses):
            raise ValueError("pending item requires unresolved but no failed objective gate")
        if item["decision"].startswith("accepted"):
            admitted += 1
        for gate in item["gates"].values():
            if gate["status"] == "failed":
                owners[gate["owner"]] += 1
        if "execution_evidence" in item:
            evidence = item["execution_evidence"]
            _exact_keys(evidence, {"path", "sha256", "scope", "verification"}, set(), "execution_evidence")
            path = Path(_text(evidence["path"], "execution_evidence.path"))
            _text(evidence["scope"], "execution_evidence.scope")
            verification = evidence["verification"]
            _exact_keys(verification, {"mode", "claim"}, set(), "execution_evidence.verification")
            _text(verification["claim"], "execution_evidence.verification.claim")
            if verification["mode"] not in {"recomputed", "digest_only_legacy"}:
                raise ValueError("unknown execution evidence verification mode")
            if verification["mode"] == "digest_only_legacy" and item["split"] != "development":
                raise ValueError("frozen validation evidence must be recomputed, not digest-only")
            if base_dir is not None:
                resolved = path if path.is_absolute() else base_dir / path
                if not resolved.is_file():
                    raise ValueError(f"missing execution evidence: {resolved}")
                digest = sha256(resolved.read_bytes()).hexdigest()
                if evidence["sha256"] != digest:
                    raise ValueError(f"stale execution evidence digest: {resolved}")
                if verification["mode"] == "recomputed":
                    report = verify_execution_file(resolved)
                    if report.get("item_id") != item["id"] or report.get("route_id") != scenario["route_id"]:
                        raise ValueError("execution evidence item or route mismatch")
                    if report.get(verification["claim"]) is not True:
                        raise ValueError("recomputed execution evidence does not prove its declared claim")
        elif item["decision"].startswith("accepted"):
            raise ValueError("accepted item requires execution evidence")
        terminal[item["decision"]] += 1
        evidence_classes[item["classification"]] += 1
        splits[item["split"]] += 1

    costs = document["costs"]
    _exact_keys(costs, {"provider_calls", "provider_tokens", "native_runs", "unknown_cost_events", "scope"}, set(), "costs")
    for key in ("provider_calls", "provider_tokens", "native_runs", "unknown_cost_events"):
        if isinstance(costs[key], bool) or not isinstance(costs[key], int) or costs[key] < 0:
            raise ValueError(f"costs.{key} must be a nonnegative integer")
    _text(costs["scope"], "costs.scope")
    return {
        "schema": "evidence-query-batch-audit.v1",
        "batch_id": document["batch_id"],
        "attempted_items": len(document["items"]),
        "accepted_items": admitted,
        "acceptance_rate": admitted / len(document["items"]),
        "rejected_items": terminal["rejected"],
        "rejection_rate": terminal["rejected"] / len(document["items"]),
        "pending_items": terminal["pending"],
        "decision_counts": dict(terminal),
        "classification_counts": dict(evidence_classes),
        "split_counts": dict(splits),
        "failed_gate_owner_counts": dict(owners),
        "costs": costs,
        "claim_boundary": "batch validity gates and provenance only; no population prevalence or model capability inference",
    }


def load_and_validate(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return document, validate_batch(document, base_dir=path.parent)
