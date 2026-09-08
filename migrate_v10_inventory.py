#!/usr/bin/env python3
"""Export the canonical v10 process inventory into v11.

Writes ONLY under ``generated/legacy_inventory/`` (created on demand) when run
explicitly:

- ``process_inventory.jsonl`` — one canonical PhysicalProcess per line
- ``report.json`` — counts, capability/status summary, artifact references

This script is never run by the test suite; adapter scans are read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unified_compiler.adapters.legacy_v10 import (
    DEFAULT_ARTIFACT_DIR,
    CityLearnV10Adapter,
    EV2GymV10Adapter,
    process_to_record,
)
from unified_compiler.types import CapabilityStatus

OUTPUT_DIR = Path(__file__).resolve().parent / "generated" / "legacy_inventory"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="directory for process_inventory.jsonl and report.json",
    )
    args = parser.parse_args(argv)

    adapters = (CityLearnV10Adapter(), EV2GymV10Adapter())
    records = []
    per_backend: dict[str, int] = {}
    episodes_seen = 0
    for adapter in adapters:
        processes = adapter.processes()
        per_backend[adapter.backend] = len(processes)
        for process in processes:
            records.append(process_to_record(process))
            episodes_seen += len(process.manifest["legacy_episode_ids"])

    capability_status = CapabilityStatus.LEGACY_EXECUTABLE_PENDING_MIGRATION.value
    report = {
        "source_artifacts": str(DEFAULT_ARTIFACT_DIR),
        "total_processes": len(records),
        "processes_per_backend": per_backend,
        "legacy_episodes_mapped": episodes_seen,
        "capability_status": capability_status,
        "note": (
            "Inventory export only. Status stays LEGACY_EXECUTABLE_PENDING_MIGRATION "
            "until real runtime replay is run; no executable verification is claimed."
        ),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    inventory_path = args.output_dir / "process_inventory.jsonl"
    with inventory_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    report_path = args.output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote {len(records)} processes -> {inventory_path}")
    print(f"wrote report -> {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
