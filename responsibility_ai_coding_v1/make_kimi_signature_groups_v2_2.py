#!/usr/bin/env python3
"""Create deterministic Kimi coding groups after the first two microbatches."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "SIGNATURE_CODING_PACKET_V2_2.jsonl"
START = 10
GROUP_SIZE = 15


def main() -> None:
    rows = [line for line in SOURCE.read_text(encoding="utf-8").splitlines() if line]
    groups = [rows[index:index + GROUP_SIZE] for index in range(START, len(rows), GROUP_SIZE)]
    manifest = []
    for number, group in enumerate(groups, start=2):
        path = ROOT / f"SIGNATURE_CODING_KIMI_GROUP_V2_2_{number:02d}.jsonl"
        path.write_text("\n".join(group) + "\n", encoding="utf-8")
        ids = [json.loads(line)["responsibility_id"] for line in group]
        manifest.append({"group": number, "file": path.name, "rows": len(group), "responsibility_ids": ids})
    output = ROOT / "SIGNATURE_CODING_KIMI_GROUP_MANIFEST_V2_2.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"groups": len(groups), "rows": sum(len(group) for group in groups), "manifest": output.name}))


if __name__ == "__main__":
    main()
