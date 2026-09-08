#!/usr/bin/env python3
"""Assemble and validate blind Kimi annotations from isolated directories."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ISOLATED = ROOT / "kimi_isolated_signature_v2_2"
MANIFEST = ISOLATED / "MANIFEST.json"
OUTPUT = ROOT / "SIGNATURE_ANNOTATIONS_KIMI_ISOLATED_V2_2.jsonl"
AUDIT = ROOT / "KIMI_ISOLATION_AUDIT_V2_2.json"
FIELDS = {
    "beneficiary_class", "lifecycle", "outcome_key", "failure_mode",
    "context_key", "confidence", "rationale",
}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assembled = []
    batch_checks = []
    for item in manifest:
        directory = Path(item["directory"])
        rows = load_jsonl(directory / item["output"])
        ids = [row.get("responsibility_id") for row in rows]
        valid = ids == item["responsibility_ids"]
        for row in rows:
            coding = row.get("coding", {})
            valid = valid and set(coding) == FIELDS
            valid = valid and bool(re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", coding.get("outcome_key", "")))
        batch_checks.append({"batch": item["batch"], "rows": len(rows), "valid": valid})
        if not valid:
            raise ValueError(f"invalid isolated batch {item['batch']}")
        assembled.extend(rows)
    expected_ids = [rid for item in manifest for rid in item["responsibility_ids"]]
    ids = [row["responsibility_id"] for row in assembled]
    if ids != expected_ids or len(ids) != len(set(ids)) or len(ids) != 167:
        raise ValueError("isolated assembly coverage/order failure")
    OUTPUT.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in assembled),
        encoding="utf-8",
    )
    audit = {
        "status": "blind_isolated_ai_proxy_coding",
        "valid": True,
        "rows": len(assembled),
        "unique_ids": len(set(ids)),
        "batch_checks": batch_checks,
        "isolation_policy": {
            "per_batch_directory": True,
            "allowed_inputs": ["INPUT.jsonl", "SCHEMA.json", "PROTOCOL.md"],
            "peer_annotations_present": False,
            "backend_inputs_present": False,
        },
        "supersedes_nonblind_artifact": "SIGNATURE_ANNOTATIONS_KIMI_V2_2.jsonl",
        "output": OUTPUT.name,
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(assembled), "output": OUTPUT.name, "audit": AUDIT.name}))


if __name__ == "__main__":
    main()
