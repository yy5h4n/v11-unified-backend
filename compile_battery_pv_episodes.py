#!/usr/bin/env python3
"""Bind validated battery/PV processes to typed Responsibility Episodes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from probe_citylearn_battery_replay import ROOT, building_battery_runtime, shared_sizing_catalogs


SCHEMA_VERSION = "responsibility-episode-v11"
CONTRACT_VERSION = "responsibility-contract-v11"
TERMINAL_RESERVE_SOC = 0.20


FAMILIES: dict[str, dict[str, Any]] = {
    "solar_self_consumption": {
        "responsibility": (
            "接下来24小时尽量把自家多余的光伏留在家里使用，少向电网倒送；"
            "不要为了充电额外大量买电，过程结束时电池电量至少保留20%。"
        ),
        "weights": {"grid_import": 0.25, "grid_export": 1.0},
    },
    "grid_import_reduction": {
        "responsibility": (
            "接下来24小时尽量减少家庭从电网买电；可以利用光伏和家用电池，"
            "同时别无谓地把多余电量倒送回电网，过程结束时电池电量至少保留20%。"
        ),
        "weights": {"grid_import": 1.0, "grid_export": 0.25},
    },
    "balanced_grid_exchange": {
        "responsibility": (
            "接下来24小时尽量提高家庭能源自给，既少从电网买电，也少把多余"
            "光伏送回电网；过程结束时电池电量至少保留20%。"
        ),
        "weights": {"grid_import": 1.0, "grid_export": 1.0},
    },
}


def family_for(process: dict[str, Any]) -> str:
    metrics = process["metrics"]
    grid_import = float(metrics["idle_grid_import_kwh"])
    grid_export = float(metrics["idle_grid_export_kwh"])
    if grid_export > 1.5 * grid_import:
        return "solar_self_consumption"
    if grid_import > 1.5 * grid_export:
        return "grid_import_reduction"
    return "balanced_grid_exchange"


def split_by_building(building_ids: list[str]) -> dict[str, str]:
    ordered = sorted(set(building_ids))
    if len(ordered) < 3:
        raise ValueError("at least three buildings are required for leakage-safe splits")
    train_end = max(1, int(len(ordered) * 2 / 3))
    validation_end = max(train_end + 1, int(len(ordered) * 5 / 6))
    return {
        building_id: (
            "train"
            if index < train_end
            else "validation"
            if index < validation_end
            else "test"
        )
        for index, building_id in enumerate(ordered)
    }


def stable_episode_id(process_id: str, family_id: str) -> str:
    digest = hashlib.sha256(f"{process_id}|{family_id}".encode("utf-8")).hexdigest()[:18]
    return f"battery_pv__{family_id}__{digest}"


def contract_clauses(family_id: str) -> list[dict[str, Any]]:
    weights = FAMILIES[family_id]["weights"]
    return [
        {
            "clause_id": "battery_soc_bounds",
            "kind": "HARD_INVARIANT",
            "variable": "battery_soc",
            "op": "between",
            "params": {"lo": 0.0, "hi": 1.0},
            "weight": 1.0,
        },
        {
            "clause_id": "terminal_reserve",
            "kind": "TERMINAL_GOAL",
            "variable": "battery_soc",
            "op": ">=",
            "params": {"value": TERMINAL_RESERVE_SOC},
            "weight": 1.0,
        },
        {
            "clause_id": "grid_import",
            "kind": "CUMULATIVE_SOFT_COST",
            "variable": "grid_import",
            "op": "<=",
            "params": {"value": 0.0},
            "weight": weights["grid_import"],
        },
        {
            "clause_id": "grid_export",
            "kind": "CUMULATIVE_SOFT_COST",
            "variable": "grid_export",
            "op": "<=",
            "params": {"value": 0.0},
            "weight": weights["grid_export"],
        },
    ]


def compile_records(process_pool: dict[str, Any], gate: dict[str, Any]) -> tuple[list, list]:
    if gate["process_pool_digest"] != process_pool["process_pool_digest"]:
        raise RuntimeError("replay gate was produced for a different process pool")
    gate_by_id = {item["process_id"]: item for item in gate["processes"]}
    split_map = split_by_building(
        [process["building_id"] for process in process_pool["processes"]]
    )
    public_records: list[dict[str, Any]] = []
    private_records: list[dict[str, Any]] = []
    with shared_sizing_catalogs():
        for process in process_pool["processes"]:
            process_id = process["process_id"]
            gate_item = gate_by_id.get(process_id)
            if gate_item is None or not gate_item["passed"]:
                raise RuntimeError(f"process lacks a passing replay gate: {process_id}")
            family_id = family_for(process)
            episode_id = stable_episode_id(process_id, family_id)
            runtime = building_battery_runtime(process["building_id"])
            start = int(process["source_start_row"])
            load = float(runtime["load"][start])
            solar = float(runtime["solar"][start])
            split = split_map[process["building_id"]]
            public_records.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "episode_id": episode_id,
                    "family_id": family_id,
                    "split": split,
                    "responsibility": FAMILIES[family_id]["responsibility"],
                    "horizon_steps": 24,
                    "observation_interval_minutes": 60,
                    "initial_observation": {
                        "virtual_hour": start % 24 + 1,
                        "battery_soc": process["battery"]["initial_soc"],
                        "non_shiftable_load_kwh": load,
                        "solar_generation_kwh": solar,
                        "net_electricity_without_storage_kwh": load - solar,
                    },
                    "observation_schema": {
                        "virtual_hour": "integer hour 1-24",
                        "battery_soc": "fraction in [0,1]",
                        "non_shiftable_load_kwh": "measured household load this step",
                        "solar_generation_kwh": "measured PV generation this step",
                        "net_electricity_kwh": "grid exchange after the previous action; positive=import",
                    },
                    "allowed_actions": {
                        "battery_rate": {
                            "type": "continuous",
                            "minimum": -1.0,
                            "maximum": 1.0,
                            "semantics": "negative=discharge, zero=wait, positive=charge",
                        }
                    },
                    "termination": "the bound 24-hour physical process ends",
                }
            )
            private_records.append(
                {
                    "episode_id": episode_id,
                    "process_id": process_id,
                    "contract_id": f"contract::{episode_id}",
                    "split": split,
                    "responsibility_contract": {
                        "contract_version": CONTRACT_VERSION,
                        "role": "PRIMARY",
                        "lifecycle": "OPTIMIZE_UNDER",
                        "physical_topology": "STORAGE_DYNAMICS",
                        "family_id": family_id,
                        "clauses": contract_clauses(family_id),
                        "derived_variables": {
                            "grid_import": "max(net_electricity, 0)",
                            "grid_export": "max(-net_electricity, 0)",
                        },
                        "priority": {
                            "type": "lexicographic",
                            "order": [
                                "hard_violation_count",
                                "hard_deficit",
                                "weighted_soft_cost",
                            ],
                        },
                    },
                    "backend_binding": {
                        "backend": "CityLearn-Battery",
                        "version": "2.5.0",
                        "citylearn_tag_commit": process_pool["assets"]["citylearn_tag_commit"],
                        "pysam_version": "7.1.1.post1",
                        "asset_manifest_ref": process_pool["assets"]["manifest"],
                        "source_schema_ref": process_pool["assets"]["source_schema"],
                        "process_pool_digest": process_pool["process_pool_digest"],
                        "building_id": process["building_id"],
                        "source_start_row": start,
                        "source_end_row": process["source_end_row"],
                        "source_trace_sha256": process["source_trace_sha256"],
                        "battery": process["battery"],
                        "pv_nominal_power_kw": process["pv_nominal_power_kw"],
                    },
                    "selection_lineage": {
                        "method": "requirement-aligned physical opportunity mining followed by replay QA",
                        "selection_metrics": process["metrics"],
                        "season_index": process["season_index"],
                        "solver_or_agent_results_used_for_membership": False,
                        "gold_actions_released": False,
                        "replay_gate_trajectory_digests": gate_item["trajectory_digests"],
                    },
                }
            )
    return public_records, private_records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--process-pool",
        type=Path,
        default=ROOT / "generated" / "battery_pv_process_pool.json",
    )
    parser.add_argument(
        "--replay-gate",
        type=Path,
        default=ROOT / "generated" / "battery_pv_process_replay_gate.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "generated" / "battery_pv_release",
    )
    args = parser.parse_args()
    process_pool = json.loads(args.process_pool.read_text(encoding="utf-8"))
    gate = json.loads(args.replay_gate.read_text(encoding="utf-8"))
    public_records, private_records = compile_records(process_pool, gate)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "episodes_public.jsonl", public_records)
    write_jsonl(args.output_dir / "episodes_private.jsonl", private_records)
    family_counts: dict[str, int] = {}
    split_counts: dict[str, int] = {}
    for record in public_records:
        family_counts[record["family_id"]] = family_counts.get(record["family_id"], 0) + 1
        split_counts[record["split"]] = split_counts.get(record["split"], 0) + 1
    build_report = {
        "schema_version": SCHEMA_VERSION,
        "episode_count": len(public_records),
        "process_count": len({record["process_id"] for record in private_records}),
        "primary_contract_count": len(private_records),
        "family_counts": family_counts,
        "split_counts": split_counts,
        "building_leakage": False,
        "replay_gate_all_passed": gate["all_passed"],
        "gold_actions_released": False,
    }
    (args.output_dir / "build_report.json").write_text(
        json.dumps(build_report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(build_report, sort_keys=True))


if __name__ == "__main__":
    main()
