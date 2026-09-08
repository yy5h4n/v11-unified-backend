#!/usr/bin/env python3
"""Prepare blinded semantic-dedup pair-judgment packets for v2.2."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PAIR_SOURCE = ROOT / "SEMANTIC_DEDUP_CANDIDATE_PAIRS_V2_2.jsonl"
SCHEMA_PATH = ROOT / "SEMANTIC_DEDUP_JUDGMENT_SCHEMA_V2_2.json"
LUNA_SIZE = 26
KIMI_SIZE = 15


def load_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def chunks(lines: list[str], size: int) -> list[list[str]]:
    return [lines[index:index + size] for index in range(0, len(lines), size)]


def main() -> None:
    lines = load_lines(PAIR_SOURCE)
    schema = {
        "schema_version": "semantic-dedup-judgment-v2.2",
        "status": "ai_proxy_review",
        "decisions": ["MERGE", "KEEP_SEPARATE", "UNCERTAIN"],
        "identity_dimensions": ["beneficiary_class", "lifecycle", "outcome_key", "failure_mode", "context_key"],
        "merge_rule": "MERGE only when the human responsibilities are mutually substitutable across all five identity dimensions.",
        "configuration_variants_not_identity": ["device", "actuator", "threshold value", "exact schedule", "notification channel", "implementation action"],
        "required_output_fields": [
            "pair_id", "decision", "mutually_substitutable",
            "distinguishing_dimensions", "preferred_responsibility_id", "confidence", "rationale",
        ],
        "constraints": {
            "preferred_responsibility_id": "For MERGE choose responsibility_id_a or responsibility_id_b; otherwise null.",
            "do_not_generate_new_responsibility": True,
            "do_not_use_backend": True,
        },
    }
    SCHEMA_PATH.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    luna_manifest = []
    for index, batch in enumerate(chunks(lines, LUNA_SIZE)):
        path = ROOT / f"SEMANTIC_DEDUP_JUDGMENT_LUNA_INPUT_{index:02d}.jsonl"
        path.write_text("\n".join(batch) + "\n", encoding="utf-8")
        luna_manifest.append({"batch": index, "file": path.name, "rows": len(batch)})
    (ROOT / "SEMANTIC_DEDUP_JUDGMENT_LUNA_MANIFEST_V2_2.json").write_text(
        json.dumps(luna_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    kimi_root = ROOT / "kimi_isolated_pair_judgment_v2_2"
    kimi_root.mkdir(exist_ok=True)
    kimi_manifest = []
    for index, batch in enumerate(chunks(lines, KIMI_SIZE)):
        directory = kimi_root / f"batch_{index:02d}"
        directory.mkdir(exist_ok=True)
        (directory / "INPUT.jsonl").write_text("\n".join(batch) + "\n", encoding="utf-8")
        shutil.copyfile(SCHEMA_PATH, directory / "SCHEMA.json")
        shutil.copyfile(ROOT / "ATOMIC_RESPONSIBILITY_DEDUP_PROTOCOL_V2.md", directory / "PROTOCOL.md")
        output = directory / "OUTPUT.jsonl"
        if output.exists():
            output.unlink()
        pair_ids = [json.loads(line)["pair_id"] for line in batch]
        kimi_manifest.append({
            "batch": index, "directory": str(directory), "rows": len(batch),
            "pair_ids": pair_ids, "output": "OUTPUT.jsonl",
        })
    (kimi_root / "MANIFEST.json").write_text(
        json.dumps(kimi_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"pairs": len(lines), "luna_batches": len(luna_manifest), "kimi_batches": len(kimi_manifest)}))


if __name__ == "__main__":
    main()
