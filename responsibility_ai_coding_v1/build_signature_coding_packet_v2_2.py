#!/usr/bin/env python3
"""Build the v2.2 responsibility-signature coding packet.

This builder is intentionally source-only: it reads the provisional catalog,
the evidence ledger, and the public dedup protocol.  It does not inspect
backend/scenario files or any prior annotation output.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "PROVISIONAL_AI_RESPONSIBILITY_CATALOG_V2_1.json"
LEDGER = ROOT / "EVIDENCE_LEDGER.jsonl"
PROTOCOL = ROOT / "ATOMIC_RESPONSIBILITY_DEDUP_PROTOCOL_V2.md"
PACKET = ROOT / "SIGNATURE_CODING_PACKET_V2_2.jsonl"
SCHEMA = ROOT / "SIGNATURE_CODING_SCHEMA_V2_2.json"

ENUMS = {
    "beneficiary_class": [
        "household", "resident", "child", "infant", "older_adult", "visitor",
        "companion_animal", "plant", "property", "grid_society", "unknown",
    ],
    "lifecycle": [
        "MAINTAIN", "GUARD", "ACHIEVE_BY", "PREPARE_FOR", "RECOVER_AFTER_EVENT",
        "OPTIMIZE_UNDER",
    ],
    "failure_mode": [
        "harm", "discomfort", "security_failure", "care_lapse", "waste",
        "shortage", "task_incompletion", "inconvenience", "unknown",
    ],
    "context_key": [
        "always", "occupied", "unoccupied", "night", "departure", "arrival",
        "scheduled", "threshold_event", "hazard_event", "weather_event", "unknown",
    ],
    "confidence": ["high", "medium", "low"],
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def lineage_for(row: dict, by_id: dict[str, dict]) -> list[str]:
    """Return deterministic root-to-parent lineage, supporting multi-parent splits."""
    def ancestry(r: dict, seen: set[str]) -> list[str]:
        rid = r["responsibility_id"]
        if rid in seen:
            raise ValueError(f"parent cycle at {rid}")
        seen = seen | {rid}
        parents = r.get("parent_responsibility_id")
        if not parents:
            return [rid]
        if isinstance(parents, str):
            parents = [parents]
        out: list[str] = []
        for parent_id in parents:
            if parent_id in by_id:
                out.extend(ancestry(by_id[parent_id], seen))
            else:
                out.append(parent_id)
        out.append(rid)
        return list(dict.fromkeys(out))
    return ancestry(row, set())


def main() -> None:
    catalog_doc = json.loads(CATALOG.read_text(encoding="utf-8"))
    responsibilities = catalog_doc["responsibilities"]
    ledger_rows = read_jsonl(LEDGER)
    ledger = {row["evidence_id"]: row for row in ledger_rows}
    if len(ledger) != len(ledger_rows):
        raise ValueError("duplicate evidence_id in evidence ledger")
    by_id = {row["responsibility_id"]: row for row in responsibilities}
    if len(by_id) != len(responsibilities):
        raise ValueError("duplicate responsibility_id in catalog")

    packet: list[dict] = []
    for row in responsibilities:
        evidence = []
        for evidence_id in row["evidence_ids"]:
            if evidence_id not in ledger:
                raise ValueError(f"missing ledger evidence_id: {evidence_id}")
            source = ledger[evidence_id]
            evidence.append({
                "evidence_id": evidence_id,
                "evidence_text": source["evidence_text"],
            })
        packet.append({
            "responsibility_id": row["responsibility_id"],
            "responsibility_name": row["canonical_name"],
            "family": row["family"],
            "evidence_ids": row["evidence_ids"],
            "evidence": evidence,
            "parent_lineage": lineage_for(row, by_id),
            "coding": {
                "beneficiary_class": None,
                "lifecycle": None,
                "outcome_key": None,
                "failure_mode": None,
                "context_key": None,
                "confidence": None,
                "rationale": None,
            },
        })

    protocol_sha256 = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema_version": "signature-coding-v2.2",
        "title": "Responsibility signature coding packet v2.2",
        "description": "One pending coding record per provisional responsibility; AI-derived and not human validated.",
        "source": {
            "catalog": CATALOG.name,
            "evidence_ledger": LEDGER.name,
            "protocol": PROTOCOL.name,
            "protocol_sha256": protocol_sha256,
        },
        "packet_invariants": {
            "expected_responsibility_count": len(responsibilities),
            "one_row_per_responsibility": True,
            "responsibility_id_unique": True,
            "backend_inputs": False,
        },
        "required_fields": [
            "responsibility_id", "responsibility_name", "family", "evidence_ids",
            "evidence", "parent_lineage", "coding",
        ],
        "coding_fields": {
            "beneficiary_class": {"type": ["string", "null"], "enum": ENUMS["beneficiary_class"] + [None], "description": "Who benefits from the outcome."},
            "lifecycle": {"type": ["string", "null"], "enum": ENUMS["lifecycle"] + [None], "description": "Temporal responsibility lifecycle."},
            "outcome_key": {"type": ["string", "null"], "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$", "enum": None, "description": "Device-independent concise snake_case outcome."},
            "failure_mode": {"type": ["string", "null"], "enum": ENUMS["failure_mode"] + [None], "description": "Meaningful failure interpretation."},
            "context_key": {"type": ["string", "null"], "enum": ENUMS["context_key"] + [None], "description": "Context in which the responsibility applies."},
            "confidence": {"type": ["string", "null"], "enum": ENUMS["confidence"] + [None], "description": "Coder confidence in the complete signature."},
            "rationale": {"type": ["string", "null"], "description": "Evidence-grounded concise rationale; null until coded."},
        },
        "identity_signature": ["beneficiary_class", "lifecycle", "outcome_key", "failure_mode", "context_key"],
    }
    PACKET.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in packet), encoding="utf-8")
    SCHEMA.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"responsibilities": len(packet), "packet": PACKET.name, "schema": SCHEMA.name}, ensure_ascii=False))


if __name__ == "__main__":
    main()
