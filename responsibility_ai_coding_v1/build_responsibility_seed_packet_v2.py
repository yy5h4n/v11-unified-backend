#!/usr/bin/env python3
"""Build conservative responsibility seeds from existing independent codings."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / name).read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    ledger = {row["evidence_id"]: row for row in load_jsonl("EVIDENCE_LEDGER.jsonl")}
    luna = {row["evidence_id"]: row for row in load_jsonl("ANNOTATIONS_LUNA.jsonl")}
    kimi = {row["evidence_id"]: row for row in load_jsonl("ANNOTATIONS_KIMI.jsonl")}
    rows = []
    for evidence_id, evidence in ledger.items():
        seeds = []
        for coder, annotation in (("luna", luna[evidence_id]), ("kimi", kimi[evidence_id])):
            candidate = annotation.get("candidate_responsibility")
            if candidate:
                seeds.append({"coder": coder, "text": candidate})
        rows.append({
            "evidence_id": evidence_id,
            "evidence_text": evidence["evidence_text"],
            "evidence_limits": evidence["evidence_limits"],
            "independence_unit_id": evidence["independence_unit_id"],
            "study_cluster_id": evidence["study_cluster_id"],
            "candidate_seeds": seeds,
            "luna_support": luna[evidence_id]["standing_responsibility_support"],
            "kimi_support": kimi[evidence_id]["standing_responsibility_support"],
        })
    path = ROOT / "RESPONSIBILITY_SEED_PACKET_V2.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({
        "evidence_units": len(rows),
        "with_any_seed": sum(bool(row["candidate_seeds"]) for row in rows),
        "without_seed": sum(not row["candidate_seeds"] for row in rows),
        "seed_mentions": sum(len(row["candidate_seeds"]) for row in rows),
    }))


if __name__ == "__main__":
    main()
