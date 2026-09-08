#!/usr/bin/env python3
"""Build a lightweight, backend-blind packet for global semantic cluster merge."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> None:
    source = json.loads((ROOT / "KIMI_LOCAL_SUMMARIES.json").read_text(encoding="utf-8"))
    entries = []
    serial = 0
    for job in source["local_jobs"]:
        for kind in ("clusters", "excluded_groups"):
            for item in job[kind]:
                serial += 1
                entries.append({
                    "entry_id": f"local_{serial:03d}",
                    "origin_result": job["result"],
                    "local_kind": kind,
                    "local_name": item.get("canonical_name") or item.get("group_name"),
                    "objective_or_reason": item.get("objective") or item.get("reason"),
                    "beneficiary": item.get("beneficiary"),
                    "lifecycle": item.get("lifecycle"),
                    "meaningful_failure": item.get("meaningful_failure"),
                    "authorized_action_scope": item.get("authorized_action_scope"),
                    "evidence_ids": item.get("evidence_ids", []),
                    "local_status": item.get("proposed_status", "excluded_local_group"),
                    "boundary_notes": item.get("boundary_notes"),
                })
    output = {
        "protocol_version": "responsibility-catalog-induction-v1",
        "coder": "kimi-k3-proxy-partitioned",
        "instruction": "Merge entries only by maintained human outcome, beneficiary, lifecycle and failure meaning; local exclusion is not final.",
        "entries": entries,
    }
    path = ROOT / "KIMI_GLOBAL_MERGE_PACKET.json"
    path.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"entry_count": len(entries), "bytes": path.stat().st_size}))


if __name__ == "__main__":
    main()
