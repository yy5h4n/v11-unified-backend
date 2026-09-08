#!/usr/bin/env python3
"""Split one clustering packet while keeping each independence unit intact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output_prefix")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    rows = [json.loads(line) for line in (root / args.source).read_text(encoding="utf-8").splitlines() if line]
    owner_side: dict[str, int] = {}
    sides = [[], []]
    for row in rows:
        owner = row["independence_unit_id"]
        if owner not in owner_side:
            owner_side[owner] = 0 if len(sides[0]) <= len(sides[1]) else 1
        sides[owner_side[owner]].append(row)
    for index, side in enumerate(sides):
        path = root / f"{args.output_prefix}_{index}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in side:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"source_rows": len(rows), "part_sizes": [len(side) for side in sides]}))


if __name__ == "__main__":
    main()
