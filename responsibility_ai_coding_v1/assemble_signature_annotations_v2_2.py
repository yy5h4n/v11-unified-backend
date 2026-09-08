#!/usr/bin/env python3
"""Assemble and validate batched v2.2 signature annotations."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIELDS = {
    "beneficiary_class",
    "lifecycle",
    "outcome_key",
    "failure_mode",
    "context_key",
    "confidence",
    "rationale",
}


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("coder", choices=("LUNA", "KIMI"))
    args = parser.parse_args()
    packet = rows(ROOT / "SIGNATURE_CODING_PACKET_V2_2.jsonl")
    expected = [row["responsibility_id"] for row in packet]
    if args.coder == "LUNA":
        paths = sorted(ROOT.glob("SIGNATURE_ANNOTATIONS_LUNA_BATCH_*.jsonl"))
    else:
        paths = [
            ROOT / "SIGNATURE_ANNOTATIONS_KIMI_MICROBATCH_00.jsonl",
            ROOT / "SIGNATURE_ANNOTATIONS_KIMI_MICROBATCH_01.jsonl",
            *sorted(ROOT.glob("SIGNATURE_ANNOTATIONS_KIMI_GROUP_*.jsonl")),
        ]
        missing_paths = [path.name for path in paths if not path.exists()]
        if missing_paths:
            raise ValueError(f"missing Kimi annotation batches: {missing_paths}")
    assembled = [row for path in paths for row in rows(path)]
    ids = [row.get("responsibility_id") for row in assembled]
    if ids != expected:
        missing = sorted(set(expected) - set(ids))
        extra = sorted(set(ids) - set(expected))
        raise ValueError(f"ID/order mismatch: missing={missing}, extra={extra}")
    for row in assembled:
        coding = row.get("coding", {})
        if set(coding) != FIELDS:
            raise ValueError(f"bad coding fields for {row.get('responsibility_id')}")
        if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", coding["outcome_key"]):
            raise ValueError(f"bad outcome_key for {row['responsibility_id']}")
    output = ROOT / f"SIGNATURE_ANNOTATIONS_{args.coder}_V2_2.jsonl"
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in assembled),
        encoding="utf-8",
    )
    print(json.dumps({"coder": args.coder, "batches": len(paths), "rows": len(assembled), "output": output.name}))


if __name__ == "__main__":
    main()
