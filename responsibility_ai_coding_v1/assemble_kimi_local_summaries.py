#!/usr/bin/env python3
"""Verify Kimi local clustering coverage and assemble global-merge input."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
JOBS = [
    ("CLUSTERING_PACKET_PART_0_SUB_0.jsonl", "KIMI_LOCAL_CLUSTERS_0_SUB_0.json"),
    ("CLUSTERING_PACKET_PART_0_SUB_1.jsonl", "KIMI_LOCAL_CLUSTERS_0_SUB_1.json"),
    ("CLUSTERING_PACKET_PART_1.jsonl", "KIMI_LOCAL_CLUSTERS_1.json"),
    ("CLUSTERING_PACKET_PART_2.jsonl", "KIMI_LOCAL_CLUSTERS_2.json"),
    ("CLUSTERING_PACKET_PART_3.jsonl", "KIMI_LOCAL_CLUSTERS_3.json"),
    ("CLUSTERING_PACKET_PART_4_SUB_0.jsonl", "KIMI_LOCAL_CLUSTERS_4_SUB_0.json"),
    ("CLUSTERING_PACKET_PART_4_SUB_1.jsonl", "KIMI_LOCAL_CLUSTERS_4_SUB_1.json"),
    ("CLUSTERING_PACKET_PART_5.jsonl", "KIMI_LOCAL_CLUSTERS_5.json"),
    ("CLUSTERING_PACKET_PART_6.jsonl", "KIMI_LOCAL_CLUSTERS_6.json"),
    ("CLUSTERING_PACKET_PART_7.jsonl", "KIMI_LOCAL_CLUSTERS_7.json"),
    ("CLUSTERING_PACKET_PART_8.jsonl", "KIMI_LOCAL_CLUSTERS_8.json"),
]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    errors: list[str] = []
    jobs = []
    all_expected: set[str] = set()
    all_covered: set[str] = set()
    for packet_name, result_name in JOBS:
        packet = read_jsonl(ROOT / packet_name)
        result = json.loads((ROOT / result_name).read_text(encoding="utf-8"))
        expected = {row["evidence_id"] for row in packet}
        covered: set[str] = set()
        for cluster in result.get("clusters", []):
            covered.update(cluster.get("evidence_ids", []))
        for group in result.get("excluded_groups", []):
            covered.update(group.get("evidence_ids", []))
        if covered != expected:
            errors.append(
                f"{result_name}: missing={sorted(expected-covered)}, "
                f"unexpected={sorted(covered-expected)}"
            )
        overlap = all_expected & expected
        if overlap:
            errors.append(f"{packet_name}: packet overlap {sorted(overlap)}")
        all_expected.update(expected)
        all_covered.update(covered)
        jobs.append({
            "packet": packet_name,
            "result": result_name,
            "input_count": len(expected),
            "clusters": result.get("clusters", []),
            "excluded_groups": result.get("excluded_groups", []),
        })

    master_rows = read_jsonl(ROOT / "CLUSTERING_PACKET_COMPACT.jsonl")
    master_ids = {row["evidence_id"] for row in master_rows}
    if all_expected != master_ids:
        errors.append(
            f"global packets mismatch master: missing={sorted(master_ids-all_expected)}, "
            f"unexpected={sorted(all_expected-master_ids)}"
        )
    output = {
        "protocol_version": "responsibility-catalog-induction-v1",
        "coder": "kimi-k3-proxy-partitioned",
        "blinding_statement": "Local jobs saw no Luna cluster catalog or backend artifacts.",
        "verification": {
            "valid": not errors,
            "master_evidence_count": len(master_ids),
            "covered_evidence_count": len(all_covered),
            "errors": errors,
        },
        "evidence_index": [
            {
                "evidence_id": row["evidence_id"],
                "independence_unit_id": row["independence_unit_id"],
                "study_cluster_id": row["study_cluster_id"],
                "luna_support": row["luna"]["standing_responsibility_support"],
                "kimi_support": row["kimi"]["standing_responsibility_support"],
                "temporal_scope": row["kimi"]["temporal_scope"],
                "delegation_acceptance": row["kimi"]["delegation_acceptance"],
                "authorized_action_scope": row["kimi"]["authorized_action_scope"],
            }
            for row in master_rows
        ],
        "local_jobs": jobs,
    }
    (ROOT / "KIMI_LOCAL_SUMMARIES.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output["verification"], ensure_ascii=False, indent=2))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
