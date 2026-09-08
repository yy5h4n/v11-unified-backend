#!/usr/bin/env python3
"""Assemble pair judgments and build the curator adjudication packet."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PAIRS = ROOT / "SEMANTIC_DEDUP_CANDIDATE_PAIRS_V2_2.jsonl"
FIELDS = {
    "pair_id", "decision", "mutually_substitutable", "distinguishing_dimensions",
    "preferred_responsibility_id", "confidence", "rationale",
}


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, data: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in data), encoding="utf-8")


def validate(data: list[dict], expected: list[dict], coder: str) -> None:
    expected_ids = [row["pair_id"] for row in expected]
    ids = [row.get("pair_id") for row in data]
    if ids != expected_ids or len(ids) != len(set(ids)):
        raise ValueError(f"{coder} pair coverage/order mismatch")
    pair_by_id = {row["pair_id"]: row for row in expected}
    for row in data:
        if set(row) != FIELDS or row["decision"] not in {"MERGE", "KEEP_SEPARATE", "UNCERTAIN"}:
            raise ValueError(f"invalid {coder} row {row.get('pair_id')}")
        pair = pair_by_id[row["pair_id"]]
        allowed = {pair["responsibility_id_a"], pair["responsibility_id_b"]}
        if row["decision"] == "MERGE" and row["preferred_responsibility_id"] not in allowed:
            raise ValueError(f"invalid preferred ID in {coder} {row['pair_id']}")
        if row["decision"] != "MERGE" and row["preferred_responsibility_id"] is not None:
            raise ValueError(f"unexpected preferred ID in {coder} {row['pair_id']}")


def main() -> None:
    pairs = rows(PAIRS)
    luna = [
        *rows(ROOT / "SEMANTIC_DEDUP_JUDGMENTS_LUNA_BATCH_00.jsonl"),
        *rows(ROOT / "SEMANTIC_DEDUP_JUDGMENTS_LUNA_BATCH_01.jsonl"),
    ]
    kimi_root = ROOT / "kimi_isolated_pair_judgment_v2_2"
    kimi = [
        *rows(kimi_root / "batch_00" / "OUTPUT.jsonl"),
        *rows(kimi_root / "batch_01" / "OUTPUT.jsonl"),
        *rows(kimi_root / "batch_02_sub_0" / "OUTPUT.jsonl"),
        *rows(kimi_root / "batch_02_sub_1" / "OUTPUT.jsonl"),
        *rows(kimi_root / "batch_02_sub_2" / "OUTPUT.jsonl"),
        *rows(kimi_root / "batch_03" / "OUTPUT.jsonl"),
    ]
    validate(luna, pairs, "luna")
    validate(kimi, pairs, "kimi")
    luna_out = ROOT / "SEMANTIC_DEDUP_JUDGMENTS_LUNA_V2_2.jsonl"
    kimi_out = ROOT / "SEMANTIC_DEDUP_JUDGMENTS_KIMI_ISOLATED_V2_2.jsonl"
    write_jsonl(luna_out, luna)
    write_jsonl(kimi_out, kimi)
    adjudication = []
    for pair, left, right in zip(pairs, luna, kimi):
        adjudication.append({
            "pair": pair,
            "luna_judgment": left,
            "kimi_judgment": right,
            "coders_agree": left["decision"] == right["decision"],
            "curator_decision": None,
            "curator_preferred_responsibility_id": None,
            "curator_rationale": None,
        })
    packet = ROOT / "SEMANTIC_DEDUP_ADJUDICATION_PACKET_V2_2.jsonl"
    write_jsonl(packet, adjudication)
    decision_agreement = sum(row["coders_agree"] for row in adjudication)
    report = {
        "pairs": len(pairs),
        "luna_decisions": dict(Counter(row["decision"] for row in luna)),
        "kimi_decisions": dict(Counter(row["decision"] for row in kimi)),
        "decision_agreement": decision_agreement,
        "decision_agreement_rate": round(decision_agreement / len(pairs), 6),
        "joint_merge": sum(a["decision"] == b["decision"] == "MERGE" for a, b in zip(luna, kimi)),
        "joint_keep_separate": sum(a["decision"] == b["decision"] == "KEEP_SEPARATE" for a, b in zip(luna, kimi)),
        "disagreements": sum(a["decision"] != b["decision"] for a, b in zip(luna, kimi)),
        "validation": {"valid": True, "pair_ids_aligned": True, "blind_kimi": True},
        "adjudication_packet": packet.name,
    }
    (ROOT / "SEMANTIC_DEDUP_JUDGMENT_AGREEMENT_V2_2.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
