#!/usr/bin/env python3
"""Build the v2.3 standing-query packets and manifest.

Source-only builder: reads the provisional v2.2 responsibility catalog, the
evidence ledger, and the blind Luna/Kimi v2.2 signature annotations.  It
emits two deterministically balanced packets covering every final v2.2
responsibility exactly once, plus a manifest.  It never generates queries;
query fields are left null for the downstream coders per
STANDING_QUERY_PROTOCOL_V2_3.md.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "PROVISIONAL_AI_RESPONSIBILITY_CATALOG_V2_2.json"
LEDGER = ROOT / "EVIDENCE_LEDGER.jsonl"
LUNA = ROOT / "SIGNATURE_ANNOTATIONS_LUNA_V2_2.jsonl"
KIMI = ROOT / "SIGNATURE_ANNOTATIONS_KIMI_ISOLATED_V2_2.jsonl"
PROTOCOL = ROOT / "STANDING_QUERY_PROTOCOL_V2_3.md"
PACKETS = [
    ROOT / "STANDING_QUERY_PACKET_V2_3_00.jsonl",
    ROOT / "STANDING_QUERY_PACKET_V2_3_01.jsonl",
]
MANIFEST = ROOT / "STANDING_QUERY_PACKET_MANIFEST_V2_3.json"
NUM_PACKETS = len(PACKETS)

QUERY_PLACEHOLDER = {
    "standing_intent_id": None,
    "canonical_query": None,
    "beneficiary": None,
    "lifecycle": None,
    "delegated_outcome": None,
    "duration_boundary": None,
    "profile_dependencies": None,
    "evidence_grounded_constraints": None,
    "configuration_variants_removed": None,
    "quality_flags": None,
    "generation_status": None,
    "rationale": None,
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def indexed_unique(rows: list[dict], key: str, source: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        value = row[key]
        if value in out:
            raise ValueError(f"duplicate {key} in {source}: {value}")
        out[value] = row
    return out


def signature_view(row: dict) -> dict:
    return {
        "responsibility_id": row["responsibility_id"],
        "responsibility_name": row.get("responsibility_name"),
        "coding": row["coding"],
    }


def main() -> None:
    catalog_doc = json.loads(CATALOG.read_text(encoding="utf-8"))
    responsibilities = catalog_doc["responsibilities"]
    ledger = indexed_unique(read_jsonl(LEDGER), "evidence_id", LEDGER.name)
    luna = indexed_unique(read_jsonl(LUNA), "responsibility_id", LUNA.name)
    kimi = indexed_unique(read_jsonl(KIMI), "responsibility_id", KIMI.name)

    anomalies: list[str] = []
    final_ids = [row["responsibility_id"] for row in responsibilities]
    if len(set(final_ids)) != len(final_ids):
        raise ValueError("duplicate responsibility_id in catalog")
    expected = catalog_doc.get("summary", {}).get("final_responsibilities")
    if expected is not None and expected != len(responsibilities):
        anomalies.append(
            f"catalog summary.final_responsibilities={expected} but responsibilities list has {len(responsibilities)}"
        )
    member_union: set[str] = set()
    for row in responsibilities:
        members = row.get("semantic_merge_member_ids") or [row["responsibility_id"]]
        member_union.update(members)
        if row["responsibility_id"] not in members:
            anomalies.append(f"{row['responsibility_id']} not listed in its own semantic_merge_member_ids")
    for source_id in sorted(set(luna) - member_union):
        anomalies.append(f"luna signature id not a final catalog member: {source_id}")
    for source_id in sorted(set(kimi) - member_union):
        anomalies.append(f"kimi signature id not a final catalog member: {source_id}")
    for label, table in (("luna", luna), ("kimi_isolated", kimi)):
        unnamed = sorted(rid for rid, row in table.items() if "responsibility_name" not in row)
        if unnamed:
            anomalies.append(f"{label}: {len(unnamed)} signature rows lack responsibility_name: {unnamed}")

    packet_rows: list[dict] = []
    for row in responsibilities:
        all_evidence_ids = list(row["evidence_ids"])
        jointly_strong = list(row.get("jointly_strong_evidence_ids", []))
        for evidence_id in all_evidence_ids + jointly_strong:
            if evidence_id not in ledger:
                raise ValueError(f"missing ledger evidence_id: {evidence_id}")
        evidence = [
            {"evidence_id": evidence_id, "evidence_text": ledger[evidence_id]["evidence_text"]}
            for evidence_id in all_evidence_ids
        ]
        jointly_strong_evidence = [
            {"evidence_id": evidence_id, "evidence_text": ledger[evidence_id]["evidence_text"]}
            for evidence_id in jointly_strong
        ]
        record = {
            "responsibility_id": row["responsibility_id"],
            "responsibility_name": row["canonical_name"],
            "family": row["family"],
            "catalog_metadata": {
                "catalog_version": catalog_doc["catalog_version"],
                "status": catalog_doc["status"],
                "validation_status": catalog_doc["validation_status"],
                "normalized_key": row["normalized_key"],
                "evidence_level": row["evidence_level"],
                "name_variants": row["name_variants"],
                "compound_review_required": row["compound_review_required"],
                "compound_review_status": row["compound_review_status"],
                "semantic_dedup_status": row.get("semantic_dedup_status"),
                "parent_responsibility_id": row.get("parent_responsibility_id"),
                "independence_unit_ids": row["independence_unit_ids"],
                "study_cluster_ids": row["study_cluster_ids"],
            },
            "evidence_ids": all_evidence_ids,
            "evidence": evidence,
            "jointly_strong_evidence_ids": jointly_strong,
            "jointly_strong_evidence": jointly_strong_evidence,
        }
        members = row.get("semantic_merge_member_ids")
        if members:
            blind_signatures = {"luna": {}, "kimi_isolated": {}}
            missing: list[str] = []
            for member_id in members:
                luna_row = luna.get(member_id)
                kimi_row = kimi.get(member_id)
                if luna_row is None:
                    missing.append(f"luna:{member_id}")
                else:
                    blind_signatures["luna"][member_id] = signature_view(luna_row)
                if kimi_row is None:
                    missing.append(f"kimi_isolated:{member_id}")
                else:
                    blind_signatures["kimi_isolated"][member_id] = signature_view(kimi_row)
            if missing:
                anomalies.append(f"{row['responsibility_id']} missing signatures: {sorted(missing)}")
            record["semantic_merge_member_ids"] = list(members)
            record["semantic_merge_pair_ids"] = list(row.get("semantic_merge_pair_ids", []))
            record["blind_signatures"] = blind_signatures
        record["source_evidence_ids"] = all_evidence_ids
        record["query"] = dict(QUERY_PLACEHOLDER)
        packet_rows.append(record)

    # Deterministic balanced split: catalog order, contiguous halves (71/71).
    base, extra = divmod(len(packet_rows), NUM_PACKETS)
    sizes = [base + (1 if index < extra else 0) for index in range(NUM_PACKETS)]
    packets: list[list[dict]] = []
    offset = 0
    for size in sizes:
        packets.append(packet_rows[offset:offset + size])
        offset += size

    # Validation.
    emitted_ids = [row["responsibility_id"] for packet in packets for row in packet]
    validation = {
        "catalog_responsibility_count": len(responsibilities),
        "emitted_row_count": len(emitted_ids),
        "id_coverage_exact": set(emitted_ids) == set(final_ids),
        "missing_ids": sorted(set(final_ids) - set(emitted_ids)),
        "extra_ids": sorted(set(emitted_ids) - set(final_ids)),
        "unique_ids": len(set(emitted_ids)) == len(emitted_ids),
        "packet_count": len(packets),
        "packet_row_counts": [len(packet) for packet in packets],
        "evidence_references_checked": sum(
            len(row["evidence_ids"]) + len(row["jointly_strong_evidence_ids"]) for row in packet_rows
        ),
        "all_evidence_references_resolved": True,
        "signature_coverage": {
            "member_ids_total": len(member_union),
            "luna_missing": sorted(member_union - set(luna)),
            "kimi_isolated_missing": sorted(member_union - set(kimi)),
        },
        "queries_generated": False,
    }
    if not validation["id_coverage_exact"]:
        raise ValueError(f"coverage mismatch: {validation['missing_ids']} / {validation['extra_ids']}")
    if not validation["unique_ids"]:
        raise ValueError("duplicate responsibility_id across packets")
    if len(packets) != NUM_PACKETS or max(sizes) - min(sizes) > 1:
        raise ValueError("packet split is not balanced")

    packet_infos = []
    for path, packet in zip(PACKETS, packets):
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in packet),
            encoding="utf-8",
        )
        packet_infos.append({
            "file": path.name,
            "rows": len(packet),
            "first_responsibility_id": packet[0]["responsibility_id"] if packet else None,
            "last_responsibility_id": packet[-1]["responsibility_id"] if packet else None,
            "sha256": sha256(path),
        })

    manifest = {
        "manifest_version": "standing-query-packet-v2.3",
        "status": "ai_derived_provisional_not_human_validated",
        "purpose": "Source packets for canonical standing-intent query compilation per STANDING_QUERY_PROTOCOL_V2_3.md; contains no generated queries.",
        "sources": {
            "catalog": {"file": CATALOG.name, "sha256": sha256(CATALOG)},
            "evidence_ledger": {"file": LEDGER.name, "sha256": sha256(LEDGER)},
            "signatures_luna": {"file": LUNA.name, "sha256": sha256(LUNA)},
            "signatures_kimi_isolated": {"file": KIMI.name, "sha256": sha256(KIMI)},
            "protocol": {"file": PROTOCOL.name, "sha256": sha256(PROTOCOL)},
        },
        "packets": packet_infos,
        "split_policy": "catalog order, contiguous balanced halves (71/71), deterministic",
        "validation": validation,
        "source_anomalies": anomalies,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "packets": packet_infos,
        "manifest": MANIFEST.name,
        "validation": validation,
        "source_anomalies": anomalies,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
