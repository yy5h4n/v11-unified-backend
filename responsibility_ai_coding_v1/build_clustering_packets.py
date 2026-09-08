#!/usr/bin/env python3
"""Build backend-blind compact packets for partitioned responsibility induction."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_jsonl(name: str) -> list[dict]:
    with (ROOT / name).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def dump_jsonl(name: str, rows: list[dict]) -> None:
    with (ROOT / name).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> None:
    ledger_rows = load_jsonl("EVIDENCE_LEDGER.jsonl")
    luna = {row["evidence_id"]: row for row in load_jsonl("ANNOTATIONS_LUNA.jsonl")}
    kimi = {row["evidence_id"]: row for row in load_jsonl("ANNOTATIONS_KIMI.jsonl")}
    compact = []
    fields = (
        "standing_responsibility_support", "temporal_scope", "beneficiary",
        "failure_meaning", "delegation_acceptance", "authorized_action_scope",
        "candidate_responsibility",
    )
    for row in ledger_rows:
        evidence_id = row["evidence_id"]
        compact.append({
            "evidence_id": evidence_id,
            "source_type": row["source_type"],
            "independence_unit_id": row["independence_unit_id"],
            "study_cluster_id": row["study_cluster_id"],
            "partition": row["partition"],
            "evidence_text": row["evidence_text"],
            "authorship_or_status": row["authorship_or_status"],
            "evidence_limits": row["evidence_limits"],
            "luna": {field: luna[evidence_id][field] for field in fields},
            "kimi": {field: kimi[evidence_id][field] for field in fields},
        })

    partition_count = 9
    owner_to_partition: dict[str, int] = {}
    next_partition = 0
    packets = [[] for _ in range(partition_count)]
    for row in compact:
        owner = row["independence_unit_id"]
        if owner not in owner_to_partition:
            owner_to_partition[owner] = next_partition
            next_partition = (next_partition + 1) % partition_count
        packets[owner_to_partition[owner]].append(row)

    dump_jsonl("CLUSTERING_PACKET_COMPACT.jsonl", compact)
    for index, packet in enumerate(packets):
        dump_jsonl(f"CLUSTERING_PACKET_PART_{index}.jsonl", packet)
    print(json.dumps({"total": len(compact), "part_sizes": [len(p) for p in packets]}))


if __name__ == "__main__":
    main()
