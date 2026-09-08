#!/usr/bin/env python3
"""Build a source-grounded packet for conservative responsibility auditing."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
        if line
    ]


def main() -> None:
    catalog = json.loads(
        (ROOT / "CONSERVATIVE_DEDUP_RESPONSIBILITY_CATALOG_V2.json").read_text(
            encoding="utf-8"
        )
    )
    ledger = {row["evidence_id"]: row for row in load_jsonl("EVIDENCE_LEDGER.jsonl")}
    kimi = {row["evidence_id"]: row for row in load_jsonl("ANNOTATIONS_KIMI.jsonl")}

    relevant_ids = {
        edge[key]
        for edge in catalog["merge_edges"]
        for key in ("left_evidence_id", "right_evidence_id")
    }
    relevant_ids.update(
        evidence_id
        for row in catalog["responsibilities"]
        if row["compound_review_required"]
        for evidence_id in row["evidence_ids"]
    )

    packet = {
        "policy": {
            "merge_test": "Keep merged only if human objectives are mutually substitutable under the five-field identity signature.",
            "split_test": "Split only if the source contains independently satisfiable or independently failing human outcomes.",
            "do_not_split": [
                "multiple actuators serving one outcome",
                "action plus notification serving one guarded outcome",
                "inseparable trade-off or joint managed state",
            ],
        },
        "merge_edges": catalog["merge_edges"],
        "compound_responsibilities": [
            row
            for row in catalog["responsibilities"]
            if row["compound_review_required"]
        ],
        "evidence": [
            {
                "evidence_id": evidence_id,
                "source": ledger[evidence_id],
                "kimi_annotation": kimi[evidence_id],
            }
            for evidence_id in sorted(relevant_ids)
        ],
    }
    output = ROOT / "CONSERVATIVE_AUDIT_PACKET_V2.json"
    output.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "merge_edges": len(packet["merge_edges"]),
                "compound_responsibilities": len(packet["compound_responsibilities"]),
                "evidence_rows": len(packet["evidence"]),
                "output": output.name,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
