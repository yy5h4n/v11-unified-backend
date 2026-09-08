#!/usr/bin/env python3
"""Prepare isolated Kimi signature-coding directories with no peer annotations."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "SIGNATURE_CODING_PACKET_V2_2.jsonl"
ISOLATED_ROOT = ROOT / "kimi_isolated_signature_v2_2"
BATCH_SIZE = 15


def main() -> None:
    lines = [line for line in SOURCE.read_text(encoding="utf-8").splitlines() if line]
    batches = [lines[index:index + BATCH_SIZE] for index in range(0, len(lines), BATCH_SIZE)]
    manifest = []
    ISOLATED_ROOT.mkdir(exist_ok=True)
    for index, batch in enumerate(batches):
        directory = ISOLATED_ROOT / f"batch_{index:02d}"
        directory.mkdir(exist_ok=True)
        input_path = directory / "INPUT.jsonl"
        input_path.write_text("\n".join(batch) + "\n", encoding="utf-8")
        shutil.copyfile(ROOT / "SIGNATURE_CODING_SCHEMA_V2_2.json", directory / "SCHEMA.json")
        shutil.copyfile(ROOT / "ATOMIC_RESPONSIBILITY_DEDUP_PROTOCOL_V2.md", directory / "PROTOCOL.md")
        output_path = directory / "OUTPUT.jsonl"
        if output_path.exists():
            output_path.unlink()
        ids = [json.loads(line)["responsibility_id"] for line in batch]
        manifest.append({
            "batch": index,
            "directory": str(directory),
            "rows": len(batch),
            "responsibility_ids": ids,
            "allowed_inputs": ["INPUT.jsonl", "SCHEMA.json", "PROTOCOL.md"],
            "output": "OUTPUT.jsonl",
        })
    path = ISOLATED_ROOT / "MANIFEST.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"batches": len(batches), "rows": len(lines), "manifest": str(path)}))


if __name__ == "__main__":
    main()
