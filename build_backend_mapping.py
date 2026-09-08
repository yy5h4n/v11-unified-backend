#!/usr/bin/env python3
"""Build the capability-derived responsibility-to-backend mapping V2."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from responsibility_backend_matcher import (
    DIMENSIONS,
    MATCH_STATUSES,
    compile_requirement,
    default_backend_profiles,
    match_requirement,
)

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "responsibility_ai_coding_v1" / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
CONTRACTS = ROOT / "responsibility_ai_coding_v1" / "RESPONSIBILITY_CAPABILITY_CONTRACTS_V1.json"
GENERATED = ROOT / "generated"
INVENTORY_PATH = GENERATED / "backend_capability_inventory_v2.json"
MAPPING_PATH = GENERATED / "responsibility_backend_mapping_v2.json"
REPORT_PATH = GENERATED / "responsibility_backend_mapping_report_v2.md"
STATUSES = MATCH_STATUSES
WORKSPACE = ROOT.parents[4]


def digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_ref(reference: str) -> Path:
    if reference.startswith("project://"):
        return ROOT / reference.removeprefix("project://")
    if reference.startswith("workspace://"):
        return WORKSPACE / reference.removeprefix("workspace://")
    raise ValueError(f"unsupported evidence reference: {reference}")


def resolved_process_count(profile) -> int:
    if profile.process_count_source is None:
        return 0
    reference, field = profile.process_count_source
    data = json.loads(resolve_ref(reference).read_text(encoding="utf-8"))
    if field == "unique_physical_process_id":
        episodes = data.get("episodes")
        if not isinstance(episodes, list):
            raise ValueError(f"{reference} has no episodes list")
        process_ids = [row.get("physical_process_id") for row in episodes]
        if any(not isinstance(value, str) or not value for value in process_ids):
            raise ValueError(f"{reference} has an invalid physical_process_id")
        return len(set(process_ids))
    return int(data[field])


def capability_inventory() -> dict[str, Any]:
    profiles = default_backend_profiles()
    for profile in profiles:
        missing_evidence = [reference for reference in profile.evidence_refs if not resolve_ref(reference).is_file()]
        if missing_evidence:
            raise FileNotFoundError(f"missing backend evidence for {profile.backend_id}: {missing_evidence}")
    rows = [
        {
            "backend_id": profile.backend_id,
            "domains": sorted(profile.domains),
            "capabilities": profile.capabilities.as_dict(),
            "verification_status": profile.verification_status,
            "harness_v2_verified": profile.harness_v2_verified,
            "evidence_refs": list(profile.evidence_refs),
            "process_count": resolved_process_count(profile),
            "process_count_source": profile.process_count_source,
        }
        for profile in profiles
    ]
    inventory = {
        "schema_version": "backend-capability-inventory-v2",
        "policy": {
            "full_requires_exact_dimension_coverage": True,
            "full_requires_harness_v2_review": True,
            "physical_process_is_not_responsibility": True,
        },
        "dimensions": list(DIMENSIONS),
        "backends": rows,
        "summary": {
            "backend_entry_count": len(rows),
            "harness_v2_verified_backend_count": sum(row["harness_v2_verified"] for row in rows),
            "indexed_process_count": sum(row["process_count"] for row in rows),
        },
    }
    inventory["content_sha256"] = digest(inventory)
    return inventory


def map_responsibility(row: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    requirement = compile_requirement(row, contract)
    result = match_requirement(requirement, default_backend_profiles())
    selected = next(
        (item for item in result["backend_assessments"] if item["backend_id"] == result["selected_backend"]),
        None,
    )
    missing = [] if selected is None else [
        *(
            f"{dimension}:{capability}"
            for dimension, capabilities in selected["missing"].items()
            for capability in capabilities
        ),
        *(f"readiness:{gap}" for gap in selected["readiness_gaps"]),
    ]
    basis = {
        "EXACT_REVIEWED_MATCH": "A reviewed responsibility Contract is exactly covered by a reviewed Harness V2 backend.",
        "CAUSAL_CORE_OVERLAP_WITH_GAPS": "A backend overlaps observation, action, dynamics and evaluator causal cores, but capability or readiness gaps remain.",
        "MISSING_OPERATIONAL_CONTRACT": "The responsibility has no structured operational Contract and therefore fails closed.",
        "NO_CAUSAL_CORE_OVERLAP": "Installed same-domain backends do not overlap every causal core required for PARTIAL support.",
    }[result["reason_code"]]
    capabilities = requirement.capabilities.as_dict()
    return {
        "responsibility_id": row["responsibility_id"],
        "standing_intent_id": row["standing_intent_id"],
        "family": row["family"],
        "lifecycle": row["lifecycle"],
        "delegated_outcome": row["delegated_outcome"],
        "natural_query": row["natural_query"],
        "support_status": result["status"],
        "compiled_domain": requirement.domain,
        "contract_status": requirement.contract_status,
        "contract_source_refs": list(requirement.source_refs),
        "requirements": capabilities,
        "required_observations": capabilities["observations"],
        "required_actions": capabilities["actions"],
        "required_dynamics": capabilities["dynamics"],
        "required_events": capabilities["events"],
        "required_evaluator_primitives": capabilities["evaluators"],
        "required_spatial_resolution": capabilities["spatial"],
        "required_temporal_semantics": capabilities["temporal"],
        "episode_inputs": list(requirement.episode_inputs),
        "requirement_derivation": [f"structured_contract:{requirement.contract_status}"],
        "selected_backend": result["selected_backend"],
        "candidate_backends": [item["backend_id"] for item in result["backend_assessments"] if item["core_overlap"]],
        "same_domain_backends": [item["backend_id"] for item in result["backend_assessments"]],
        "backend_assessments": result["backend_assessments"],
        "missing_capabilities": missing,
        "decision_basis": basis,
        "evidence_refs": [] if selected is None else selected["evidence_refs"],
        "identity_preserved": True,
        "episode_release_status": "NOT_COMPILED",
    }


def build() -> tuple[dict[str, Any], dict[str, Any], str]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    contract_catalog = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    contracts = {row["responsibility_id"]: row for row in contract_catalog["contracts"]}
    if set(contracts) != {row["responsibility_id"] for row in catalog["queries"]}:
        raise ValueError("responsibility capability Contract coverage mismatch")
    inventory = capability_inventory()
    mappings = [map_responsibility(row, contracts[row["responsibility_id"]]) for row in catalog["queries"]]
    counts = Counter(item["support_status"] for item in mappings)
    mapping = {
        "schema_version": "responsibility-backend-mapping-v2",
        "source_catalog": str(CATALOG.relative_to(ROOT)),
        "source_catalog_sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
        "source_contracts": str(CONTRACTS.relative_to(ROOT)),
        "source_contracts_sha256": hashlib.sha256(CONTRACTS.read_bytes()).hexdigest(),
        "policy": {
            "direction": "responsibility_semantics_to_capability_vector_to_backend",
            "responsibility_id_allowlists_forbidden": True,
            "fail_closed": True,
            "full_requires_harness_v2_review": True,
            "episode_admission_is_separate": True,
        },
        "summary": {
            "responsibility_count": len(mappings),
            "status_counts": {status: counts.get(status, 0) for status in sorted(STATUSES)},
            "episode_count": 0,
        },
        "mappings": mappings,
    }
    mapping["content_sha256"] = digest(mapping)
    return inventory, mapping, render_report(inventory, mapping)


def render_report(inventory: dict[str, Any], mapping: dict[str, Any]) -> str:
    counts = mapping["summary"]["status_counts"]
    full = [row for row in mapping["mappings"] if row["support_status"] == "FULL"]
    partial = [row for row in mapping["mappings"] if row["support_status"] == "PARTIAL"]
    lines = [
        "# Responsibility → Backend Capability Mapping V2",
        "",
        "This mapping is computed from semantic requirements and backend capability vectors. No responsibility ID is allow-listed. FULL means exact capability coverage by an independently reviewed Harness V2 adapter; it does not itself release an Episode.",
        "",
        "## Counts",
        "",
        f"- Responsibilities: {mapping['summary']['responsibility_count']}",
        f"- Registered installed/probed backends: {inventory['summary']['backend_entry_count']}",
        f"- Harness V2 reviewed backends: {inventory['summary']['harness_v2_verified_backend_count']}",
        f"- FULL: {counts['FULL']}",
        f"- PARTIAL: {counts['PARTIAL']}",
        f"- UNSUPPORTED: {counts['UNSUPPORTED']}",
        "- Episodes released by this stage: 0",
        "",
        "## FULL matches",
        "",
    ]
    lines.extend(f"- `{row['responsibility_id']}` — {row['natural_query']} → `{row['selected_backend']}`" for row in full)
    lines.extend(("", "## PARTIAL matches", ""))
    lines.extend(
        f"- `{row['responsibility_id']}` — `{row['compiled_domain']}` → `{row['selected_backend']}`; missing: {', '.join(row['missing_capabilities'])}"
        for row in partial
    )
    lines.extend(("", "## Boundary", "", "Query generation, physical-window mining, no-op/oracle diagnostics, and Episode admission occur after this mapping stage.", ""))
    return "\n".join(lines)


def serialized_outputs() -> dict[Path, str]:
    inventory, mapping, report = build()
    return {
        INVENTORY_PATH: json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        MAPPING_PATH: json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        REPORT_PATH: report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated artifacts differ")
    args = parser.parse_args()
    outputs = serialized_outputs()
    if args.check:
        stale = [str(path) for path, content in outputs.items() if not path.is_file() or path.read_text(encoding="utf-8") != content]
        if stale:
            raise SystemExit("stale backend mapping artifacts: " + ", ".join(stale))
        return
    GENERATED.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
