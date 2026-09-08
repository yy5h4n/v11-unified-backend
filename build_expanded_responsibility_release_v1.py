#!/usr/bin/env python3
"""Combine certified component releases without changing Episode identity."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"
COMPONENTS = {
    "energyplus_generic_comfort_v1": GENERATED / "energyplus_responsibility_release_v1",
    "simuhome_kitchen_evening_v1": GENERATED / "simuhome_kitchen_evening_release_v1",
    "simuhome_multiroom_evening_v1": GENERATED / "simuhome_multiroom_evening_release_v1",
}
OUT = GENERATED / "expanded_responsibility_release_v1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def main(out: Path | str = OUT) -> dict[str, Any]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    public_rows: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    component_lineage: dict[str, Any] = {}
    for name, directory in COMPONENTS.items():
        report = json.loads((directory / "build_report.json").read_text(encoding="utf-8"))
        if not (report.get("passed") is True or report.get("status") == "PASS"):
            raise RuntimeError(f"component_not_passed:{name}")
        component_public = read_jsonl(directory / "episodes_public.jsonl")
        component_private = read_jsonl(directory / "episodes_private.jsonl")
        if len(component_public) != len(component_private) or not component_public:
            raise RuntimeError(f"component_cardinality_invalid:{name}")
        public_rows.extend(component_public)
        private_rows.extend(component_private)
        component_lineage[name] = {
            "directory": str(directory.relative_to(ROOT)),
            "episode_count": len(component_public),
            "public_sha256": sha256_file(directory / "episodes_public.jsonl"),
            "private_sha256": sha256_file(directory / "episodes_private.jsonl"),
            "build_report_sha256": sha256_file(directory / "build_report.json"),
            "replay_gate_sha256": sha256_file(directory / "replay_gate.json"),
        }

    public_rows.sort(key=lambda row: row["episode_id"])
    private_rows.sort(key=lambda row: row["episode_id"])
    public_ids = [row["episode_id"] for row in public_rows]
    private_ids = [row["episode_id"] for row in private_rows]
    if len(set(public_ids)) != len(public_ids) or set(public_ids) != set(private_ids):
        raise RuntimeError("episode_identity_collision_or_mismatch")
    physical_ids = [row["physical_process_id"] for row in private_rows]
    if len(set(physical_ids)) != len(physical_ids):
        raise RuntimeError("physical_process_collision")
    if any("gold_actions" in row for row in public_rows):
        raise RuntimeError("public_gold_leakage")
    if any(not row.get("qa_verdicts", {}).get("passed", False) for row in private_rows):
        raise RuntimeError("private_episode_without_passed_gate")

    write_jsonl(out / "episodes_public.jsonl", public_rows)
    write_jsonl(out / "episodes_private.jsonl", private_rows)
    counts = dict(sorted(Counter(row["responsibility_id"] for row in public_rows).items()))
    report = {
        "schema_version": "expanded-responsibility-release-v1",
        "status": "PASS",
        "passed": True,
        "component_release_count": len(COMPONENTS),
        "responsibility_count": len(counts),
        "episode_count": len(public_rows),
        "responsibility_episode_counts": counts,
        "all_episode_ids_unique": True,
        "all_primary_processes_unique": True,
        "gold_actions_released_publicly": False,
        "split_counts": dict(sorted(Counter(row.get("split", "none") for row in public_rows).items())),
        "component_lineage": component_lineage,
        "public_sha256": sha256_file(out / "episodes_public.jsonl"),
        "private_sha256": sha256_file(out / "episodes_private.jsonl"),
        "scope": "provisional multi-backend pilot; benchmark sandbox only",
        "backend_fidelity_strata": {
            "energyplus_calibrated_building_simulation": {
                "episode_count": 30,
                "responsibility_ids": ["rd_37104b57370a"],
            },
            "simuhome_synthetic_room_state_aggregator": {
                "episode_count": sum(1 for row in public_rows if row["episode_id"].startswith("simuhome_")),
                "responsibility_ids": sorted({row["responsibility_id"] for row in public_rows if row["episode_id"].startswith("simuhome_")}),
            },
        },
        "pooled_cross_stratum_metric_allowed": False,
        "redistribution_status": "internal_preview_only; SimuHome license not found and authorization remains unknown",
        "limitations": [
            "30 generic-comfort Episodes share one EnergyPlus model/weather lineage",
            "10 kitchen/multi-room evening Episodes share one synthetic SimuHome implementation lineage",
            "the two backends have different fidelity and Episode schemas, retained explicitly in each record",
            "responsibility admission remains an AI-coded pilot accepted by the project owner",
            "no train/dev/test split is claimed for either connected backend lineage",
        ],
    }
    (out / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "DATASET_CARD.md").write_text(
        "# Expanded Responsibility Episode Release v1\n\n"
        "This provisional collection combines three independently certified component releases "
        "without rewriting their Episode identities or backend lineage. It contains 30 EnergyPlus "
        "generic-comfort Episodes, 3 SimuHome kitchen-evening Episodes, and 7 SimuHome multi-room-evening "
        "Episodes. Every physical window has one primary responsibility.\n\n"
        "The public-shaped file excludes gold trajectories but is an internal preview, not an authorized "
        "redistribution artifact. Private records retain contracts, source hashes, "
        "replay certificates, and QA verdicts. The collection is preview-only and benchmark-sandbox-only.\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    main()
