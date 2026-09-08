#!/usr/bin/env python3
"""Validate atomic propositions, conservatively deduplicate, and aggregate evidence."""

from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT / "ATOMIC_RESPONSIBILITY_DEDUP_PROTOCOL_V2.md"
PROPOSITIONS = ROOT / "ATOMIC_PROPOSITIONS_SEEDED_V2.jsonl"
OUTPUT = ROOT / "ATOMIC_RESPONSIBILITY_CATALOG_V2.json"

FIELDS = {
    "proposition_id", "evidence_id", "is_responsibility_candidate", "family",
    "canonical_name", "objective", "beneficiary_class", "lifecycle",
    "outcome_key", "failure_mode", "context_key", "delegation_variants",
    "authorized_action_summary", "source_support", "exclusion_reason",
    "confidence", "rationale",
}
BENEFICIARIES = {
    "household", "resident", "child", "infant", "older_adult", "visitor",
    "companion_animal", "plant", "property", "grid_society", "unknown",
}
LIFECYCLES = {
    "MAINTAIN", "GUARD", "ACHIEVE_BY", "PREPARE_FOR",
    "RECOVER_AFTER_EVENT", "OPTIMIZE_UNDER",
}
FAILURES = {
    "harm", "discomfort", "security_failure", "care_lapse", "waste",
    "shortage", "task_incompletion", "inconvenience", "unknown",
}
CONTEXTS = {
    "always", "occupied", "unoccupied", "night", "departure", "arrival",
    "scheduled", "threshold_event", "hazard_event", "weather_event", "unknown",
}
DELEGATIONS = {
    "notify_only", "recommend", "act_and_notify", "autonomous_act", "unknown",
}
BANNED_BROAD_OUTCOMES = {
    "maintain_household_safety_and_security", "maintain_household_routine",
    "support_health_and_wellbeing", "maintain_context_appropriate_lighting",
    "maintain_household_cleanliness", "maintain_household_supplies",
    "maintain_companion_animal_care", "maintain_plant_care",
    "support_dependent_care", "support_recreation_and_ambiance",
    "household_automation", "context_appropriate_lighting",
}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def mode_text(values: list[str | None]) -> str | None:
    cleaned = [value for value in values if isinstance(value, str) and value.strip()]
    if not cleaned:
        return None
    counts = collections.Counter(cleaned)
    return sorted(cleaned, key=lambda value: (-counts[value], len(value), value.casefold()))[0]


def signature(row: dict) -> tuple[str, str, str, str, str]:
    return (
        row["beneficiary_class"], row["lifecycle"], row["outcome_key"],
        row["failure_mode"], row["context_key"],
    )


def atomic_id(sig: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\x1f".join(sig).encode("utf-8")).hexdigest()[:12]
    return f"ar_{digest}"


def main() -> None:
    ledger_rows = load_jsonl(ROOT / "EVIDENCE_LEDGER.jsonl")
    ledger = {row["evidence_id"]: row for row in ledger_rows}
    luna = {row["evidence_id"]: row for row in load_jsonl(ROOT / "ANNOTATIONS_LUNA.jsonl")}
    kimi = {row["evidence_id"]: row for row in load_jsonl(ROOT / "ANNOTATIONS_KIMI.jsonl")}
    propositions = load_jsonl(PROPOSITIONS)

    errors: list[str] = []
    proposition_ids: set[str] = set()
    covered_ids: set[str] = set()
    for index, row in enumerate(propositions):
        label = f"row[{index}]"
        if set(row) != FIELDS:
            errors.append(f"{label}: fields differ missing={sorted(FIELDS-set(row))} extra={sorted(set(row)-FIELDS)}")
        proposition_id = row.get("proposition_id")
        if proposition_id in proposition_ids:
            errors.append(f"{label}: duplicate proposition_id {proposition_id}")
        proposition_ids.add(proposition_id)
        evidence_id = row.get("evidence_id")
        if evidence_id not in ledger:
            errors.append(f"{label}: unknown evidence_id {evidence_id}")
            continue
        covered_ids.add(evidence_id)
        if not isinstance(row.get("is_responsibility_candidate"), bool):
            errors.append(f"{label}: is_responsibility_candidate must be bool")
        if row.get("beneficiary_class") not in BENEFICIARIES:
            errors.append(f"{label}: invalid beneficiary_class {row.get('beneficiary_class')}")
        if row.get("lifecycle") not in LIFECYCLES:
            errors.append(f"{label}: invalid lifecycle {row.get('lifecycle')}")
        if row.get("failure_mode") not in FAILURES:
            errors.append(f"{label}: invalid failure_mode {row.get('failure_mode')}")
        if row.get("context_key") not in CONTEXTS:
            errors.append(f"{label}: invalid context_key {row.get('context_key')}")
        variants = row.get("delegation_variants")
        if not isinstance(variants, list) or not set(variants).issubset(DELEGATIONS):
            errors.append(f"{label}: invalid delegation_variants {variants}")
        outcome_key = row.get("outcome_key")
        if not isinstance(outcome_key, str) or not outcome_key or outcome_key.lower() != outcome_key or " " in outcome_key:
            errors.append(f"{label}: outcome_key must be nonempty lowercase snake-case")
        elif outcome_key in BANNED_BROAD_OUTCOMES:
            errors.append(f"{label}: outcome_key is an overbroad family label: {outcome_key}")

    missing_evidence = sorted(set(ledger) - covered_ids)
    if missing_evidence:
        errors.append(f"missing evidence coverage: {missing_evidence}")
    if errors:
        print(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    candidates = [row for row in propositions if row["is_responsibility_candidate"]]
    groups: dict[tuple[str, str, str, str, str], list[dict]] = collections.defaultdict(list)
    for row in candidates:
        groups[signature(row)].append(row)

    atomics = []
    for sig in sorted(groups):
        rows = groups[sig]
        evidence_ids = sorted({row["evidence_id"] for row in rows})
        independence_units = sorted({ledger[eid]["independence_unit_id"] for eid in evidence_ids})
        studies = sorted({ledger[eid]["study_cluster_id"] for eid in evidence_ids})
        jointly_strong = sorted(
            eid for eid in evidence_ids
            if luna[eid]["standing_responsibility_support"] in {"strongly_implies", "entails"}
            and kimi[eid]["standing_responsibility_support"] in {"strongly_implies", "entails"}
        )
        if jointly_strong or (len(independence_units) >= 3 and len(studies) >= 2):
            evidence_level = "well_supported"
        elif len(independence_units) >= 3:
            evidence_level = "recurrent_single_source"
        elif len(independence_units) == 2:
            evidence_level = "repeated_candidate"
        else:
            evidence_level = "isolated_candidate"
        family = mode_text([row["family"] for row in rows])
        atomics.append({
            "atomic_responsibility_id": atomic_id(sig),
            "family": family,
            "canonical_name": mode_text([row["canonical_name"] for row in rows]),
            "objective": mode_text([row["objective"] for row in rows]),
            "signature": {
                "beneficiary_class": sig[0], "lifecycle": sig[1],
                "outcome_key": sig[2], "failure_mode": sig[3], "context_key": sig[4],
            },
            "delegation_variants": sorted({variant for row in rows for variant in row["delegation_variants"]}),
            "authorized_action_summaries": sorted({row["authorized_action_summary"] for row in rows if row["authorized_action_summary"]}),
            "evidence_level": evidence_level,
            "evidence_ids": evidence_ids,
            "independence_unit_ids": independence_units,
            "study_cluster_ids": studies,
            "source_type_counts": dict(collections.Counter(ledger[eid]["source_type"] for eid in evidence_ids)),
            "jointly_strong_evidence_ids": jointly_strong,
            "luna_support_counts": dict(collections.Counter(luna[eid]["standing_responsibility_support"] for eid in evidence_ids)),
            "kimi_support_counts": dict(collections.Counter(kimi[eid]["standing_responsibility_support"] for eid in evidence_ids)),
            "proposition_ids": sorted(row["proposition_id"] for row in rows),
            "name_variants": sorted({row["canonical_name"] for row in rows if row["canonical_name"]}),
        })

    coarse_groups: dict[tuple[str, str, str], list[dict]] = collections.defaultdict(list)
    for atomic in atomics:
        sig = atomic["signature"]
        coarse_groups[(sig["beneficiary_class"], sig["lifecycle"], sig["outcome_key"])].append(atomic)
    near_duplicate_review = [
        {
            "coarse_signature": {"beneficiary_class": key[0], "lifecycle": key[1], "outcome_key": key[2]},
            "atomic_responsibility_ids": [row["atomic_responsibility_id"] for row in rows],
            "retained_distinctions": [
                {"id": row["atomic_responsibility_id"], "failure_mode": row["signature"]["failure_mode"], "context_key": row["signature"]["context_key"]}
                for row in rows
            ],
        }
        for key, rows in sorted(coarse_groups.items()) if len(rows) > 1
    ]

    non_candidates = [row for row in propositions if not row["is_responsibility_candidate"]]
    output = {
        "catalog_version": "atomic-responsibility-v2",
        "validation_status": "provisional_ai_derived",
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "source_summary": {
            "evidence_units": len(ledger),
            "proposition_records": len(propositions),
            "candidate_propositions": len(candidates),
            "non_responsibility_propositions": len(non_candidates),
            "atomic_responsibilities": len(atomics),
            "families": len({row["family"] for row in atomics}),
        },
        "evidence_level_counts": dict(collections.Counter(row["evidence_level"] for row in atomics)),
        "family_counts": dict(collections.Counter(row["family"] for row in atomics)),
        "atomic_responsibilities": atomics,
        "near_duplicate_review": near_duplicate_review,
        "non_responsibility_records": non_candidates,
        "validation": {
            "valid": True,
            "all_223_evidence_ids_covered": covered_ids == set(ledger),
            "duplicate_proposition_ids": [],
            "exact_signature_dedup_only": True,
            "backend_blind": True,
        },
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "valid": True,
        "source_summary": output["source_summary"],
        "evidence_level_counts": output["evidence_level_counts"],
        "near_duplicate_review_groups": len(near_duplicate_review),
        "output": OUTPUT.name,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
