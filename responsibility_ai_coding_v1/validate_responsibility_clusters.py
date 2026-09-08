#!/usr/bin/env python3
"""Structural and admission-threshold validator for responsibility clusters."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


REQUIRED_CLUSTER_FIELDS = {
    "canonical_name", "objective", "beneficiary", "lifecycle",
    "meaningful_failure", "authorized_action_scope", "evidence_ids",
    "independence_unit_ids", "study_cluster_ids", "admission_status",
    "admission_path", "admission_rationale", "confidence", "boundary_notes",
    "coding_disagreements",
}
ALLOWED_STATUSES = {
    "admitted_provisional", "candidate_needs_confirmation",
    "excluded_from_responsibility_catalog",
}
BANNED_KEYS = {
    "backend", "backend_capability", "simulator", "episode_count",
    "physical_process", "baseline_result",
}


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    catalog_path = args.catalog if args.catalog.is_absolute() else root / args.catalog

    ledger_rows = load_jsonl(root / "EVIDENCE_LEDGER.jsonl")
    ledger = {row["evidence_id"]: row for row in ledger_rows}
    luna = {row["evidence_id"]: row for row in load_jsonl(root / "ANNOTATIONS_LUNA.jsonl")}
    kimi = {row["evidence_id"]: row for row in load_jsonl(root / "ANNOTATIONS_KIMI.jsonl")}
    data = json.loads(catalog_path.read_text(encoding="utf-8"))

    errors: list[str] = []
    warnings: list[str] = []
    clusters = data.get("clusters")
    if not isinstance(clusters, list):
        raise SystemExit("clusters must be a list")

    names: set[str] = set()
    for index, cluster in enumerate(clusters):
        label = f"cluster[{index}]"
        missing = REQUIRED_CLUSTER_FIELDS - set(cluster)
        if missing:
            errors.append(f"{label}: missing fields {sorted(missing)}")
        banned = BANNED_KEYS & set(cluster)
        if banned:
            errors.append(f"{label}: backend-derived keys forbidden {sorted(banned)}")

        name = cluster.get("canonical_name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}: invalid canonical_name")
        elif name.casefold() in names:
            errors.append(f"{label}: duplicate canonical_name {name!r}")
        else:
            names.add(name.casefold())

        evidence_ids = as_list(cluster.get("evidence_ids"))
        if len(evidence_ids) != len(set(evidence_ids)):
            errors.append(f"{label}: duplicate evidence_ids")
        unknown = sorted(set(evidence_ids) - set(ledger))
        if unknown:
            errors.append(f"{label}: unknown evidence_ids {unknown}")
            continue

        expected_independence = sorted({ledger[eid]["independence_unit_id"] for eid in evidence_ids})
        expected_studies = sorted({ledger[eid]["study_cluster_id"] for eid in evidence_ids})
        if sorted(as_list(cluster.get("independence_unit_ids"))) != expected_independence:
            errors.append(f"{label}: independence_unit_ids do not equal ledger-derived set")
        if sorted(as_list(cluster.get("study_cluster_ids"))) != expected_studies:
            errors.append(f"{label}: study_cluster_ids do not equal ledger-derived set")

        status = cluster.get("admission_status")
        path = cluster.get("admission_path")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{label}: invalid admission_status {status!r}")
        jointly_strong = [
            eid for eid in evidence_ids
            if luna[eid]["standing_responsibility_support"] in {"strongly_implies", "entails"}
            and kimi[eid]["standing_responsibility_support"] in {"strongly_implies", "entails"}
        ]
        if status == "admitted_provisional" and path == "direct":
            if not jointly_strong:
                errors.append(f"{label}: direct admission lacks jointly strong evidence")
        elif status == "admitted_provisional" and path == "convergent":
            if len(expected_independence) < 3 or len(expected_studies) < 2:
                errors.append(f"{label}: convergent admission needs >=3 independence units and >=2 studies")
        elif status == "candidate_needs_confirmation":
            if len(expected_independence) < 3:
                errors.append(f"{label}: confirmation candidate needs >=3 independence units")
        elif status == "admitted_provisional":
            errors.append(f"{label}: admitted cluster has invalid path {path!r}")

        if len(evidence_ids) == 0:
            errors.append(f"{label}: empty evidence cluster")
        if status != "excluded_from_responsibility_catalog" and len(expected_studies) == 1:
            warnings.append(f"{label}: retained cluster relies on one study cluster")

    report = {
        "catalog": catalog_path.name,
        "valid": not errors,
        "cluster_count": len(clusters),
        "status_counts": dict(collections.Counter(c.get("admission_status") for c in clusters)),
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
