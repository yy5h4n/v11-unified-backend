#!/usr/bin/env python3
"""Validate and join Kimi sub-batches without changing annotation content."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", choices=("01", "05"))
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    packet_path = root / f"CODING_PACKET_BATCH_{args.batch}.jsonl"
    part_paths = sorted(root.glob(f"ANNOTATIONS_KIMI_BATCH_{args.batch}_PART_*.jsonl"))
    if not part_paths:
        raise SystemExit(f"No annotation parts found for batch {args.batch}")

    packet = load_jsonl(packet_path)
    annotations = [row for path in part_paths for row in load_jsonl(path)]
    expected_ids = [row["evidence_id"] for row in packet]
    actual_ids = [row.get("evidence_id") for row in annotations]
    if actual_ids != expected_ids:
        raise SystemExit(
            f"Refusing merge: ordered IDs differ for batch {args.batch}; "
            f"expected={len(expected_ids)}, actual={len(actual_ids)}"
        )

    output_path = root / f"ANNOTATIONS_KIMI_BATCH_{args.batch}.jsonl"
    with output_path.open("w", encoding="utf-8") as handle:
        for row in annotations:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"output": output_path.name, "rows": len(annotations), "ordered_ids_equal": True}))


if __name__ == "__main__":
    main()
