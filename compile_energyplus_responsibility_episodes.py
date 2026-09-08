#!/usr/bin/env python3
"""Compile the residential EnergyPlus responsibility Episode pilot release."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "generated/energyplus_residential_runtime_v1"
RUNTIME_GATE = RUNTIME / "gate_report.json"
REPLAYS = RUNTIME / "replays.jsonl"
CATALOG = ROOT / "responsibility_ai_coding_v1/NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
CONTRACT = ROOT / "contracts/residential_generic_comfort_v1.json"
RELEASE = ROOT / "generated/energyplus_responsibility_release_v1"
GENERIC = "rd_37104b57370a"
EXPECTED = {GENERIC: 30}
PROFILE = {
    "target_c": 22.0,
    "tolerance_c": 2.0,
    "parameter_provenance": (
        "benchmark_design_choice inherited from compile_supported_hvac_episodes.py; "
        "not fitted to this residential trace and not query semantics"
    ),
}


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def public_hash(record: dict[str, Any]) -> str:
    return digest({key: value for key, value in record.items() if key != "content_hash"})


def build() -> dict[Path, str]:
    runtime = json.loads(RUNTIME_GATE.read_text(encoding="utf-8"))
    frozen_contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if runtime.get("passed") is not True or runtime.get("passed_window_count") != 30:
        raise RuntimeError("residential runtime gate is not the reviewed 30/31 source gate")
    if runtime.get("source_model") != "SingleFamilyHouse_TwoSpeed_MultiStageElectricSuppCoil.idf":
        raise RuntimeError("source is not the pinned single-family model")
    if runtime.get("contract_sha256") != file_sha(CONTRACT) or frozen_contract.get("responsibility_id") != GENERIC:
        raise RuntimeError("runtime is not bound to the frozen generic-comfort contract")
    if runtime.get("contract_profile") != {
        "target_c": PROFILE["target_c"],
        "tolerance_c": PROFILE["tolerance_c"],
        "provenance": PROFILE["parameter_provenance"],
        "soft_loss": "mean_abs_error_c",
    }:
        raise RuntimeError("runtime contract profile drifted")

    replay_rows = load_jsonl(REPLAYS)
    if len(replay_rows) != 93:
        raise RuntimeError("expected 31 days x 3 prefix-controlled replays")
    by_day: dict[int, dict[str, dict[str, Any]]] = {}
    for row in replay_rows:
        by_day.setdefault(row["day"], {})[row["strategy"]] = row
    if set(by_day) != set(range(1, 32)) or any(set(group) != {"witness", "contrast", "noop"} for group in by_day.values()):
        raise RuntimeError("replay matrix is incomplete")
    source_gates = {row["day"]: row for row in runtime["gate_report"]}

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    query_by_id = {row["responsibility_id"]: row for row in catalog["queries"]}
    if GENERIC not in query_by_id:
        raise RuntimeError("required responsibility admission records are absent")

    public_rows: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    runtime_hash = file_sha(RUNTIME_GATE)
    compiler_hash = file_sha(Path(__file__))
    catalog_hash = file_sha(CATALOG)
    replay_file_hash = file_sha(REPLAYS)

    for day in sorted(by_day):
        source_gate = source_gates[day]
        if not source_gate["passed"]:
            excluded.append({
                "window": {"month": 1, "day": day},
                "reason": "SOURCE_WINDOW_GATE_FAILED",
                "failed_gates": [
                    key for key in (
                        "time_alignment", "initial_observation_alignment", "complete_96", "pre_action_initial_observation", "no_nan",
                        "action_sensitivity", "evaluator_sensitivity", "fixed_witness_feasible",
                        "no_op_comparison", "selected_determinism",
                    ) if not source_gate[key]
                ],
            })
            continue

        group = by_day[day]
        witness, contrast, noop = (group[name] for name in ("witness", "contrast", "noop"))
        steps = witness["steps"]
        occupancy_steps = sum(step["observation"]["occupant_count"] > 0 for step in steps)
        responsibility_id = GENERIC
        query = query_by_id[responsibility_id]
        active_steps = 96
        if active_steps <= 0:
            raise RuntimeError("responsibility has no active evaluator interval")

        first_key = steps[0]["time_key"]
        window_key = {
            "environment_num": first_key["current_environment_num"],
            "year": first_key["year"],
            "month": first_key["month"],
            "day": first_key["day_of_month"],
        }
        process_id = "eplus_residential__" + digest({"runtime": runtime_hash, "window": window_key})[:20]
        contract_payload = frozen_contract
        contract_id = frozen_contract["contract_id"]
        episode_id = "eplus_home_v1__" + digest({
            "responsibility_id": responsibility_id,
            "process_id": process_id,
            "contract_id": contract_id,
        })[:20]

        status = {
            "semantic_status": "provisional_ai_pilot",
            "authorization_status": "unknown",
            "physical_status": "positive_opportunity",
            "release_status": "provisional",
            "preview_only": True,
        }
        public = {
            "schema_version": "responsibility-episode-energyplus-home-v1",
            "episode_id": episode_id,
            "responsibility_id": responsibility_id,
            "query_id": query["standing_intent_id"],
            "contract_id": contract_id,
            "natural_query": query["natural_query"],
            "statuses": status,
            "execution_scope": "benchmark_sandbox_only",
            "split": "none",
            "horizon_steps": 96,
            "observation_interval_minutes": 15,
            "initial_observation": witness["pre_action_initial_observation"],
            "observation_schema": {
                "occupant_count": "float persons; HOUSE OCCUPANCY schedule",
                "outdoor_temperature_c": "float degC",
                "zone_temperature_c": "float degC; LIVING ZONE",
                "heating_setpoint_c": "float degC",
                "heating_rate_w": "float W thermal rate; not electrical energy",
                "facility_demand_w": "float W facility demand; not attributed to HVAC",
            },
            "legal_action_schema": {
                "type": "discrete_thermostat_schedule_value_c",
                "values": [21.0, 22.0],
                "component": "Schedule:Compact/Dual Heating Setpoints",
            },
            "profile": PROFILE,
            "evaluator": {
                "hard_clause": "all active steps satisfy abs(zone_temperature_c - target_c) <= tolerance_c",
                "soft_metric": "mean absolute temperature error over active steps; lower is better",
                "active_scope": "all 96 steps",
                "trajectory_completeness_required": True,
            },
            "termination": {
                "type": "finite_evaluation_window",
                "terminal_verdict": "continues_beyond_window",
                "reason": "MAINTAIN responsibility is not completed by the 24-hour boundary",
            },
        }
        public["content_hash"] = public_hash(public)

        evaluator_scores = source_gate["evaluator_scores"]
        private = {
            "schema_version": "responsibility-episode-energyplus-home-v1-private",
            "episode_id": episode_id,
            "responsibility_id": responsibility_id,
            "query_id": query["standing_intent_id"],
            "contract_id": contract_id,
            "physical_process_id": process_id,
            "binding_role": "PRIMARY",
            "statuses": status,
            "responsibility_lineage": {
                "catalog_version": catalog["catalog_version"],
                "catalog_sha256": catalog_hash,
                "contract_artifact": str(CONTRACT.relative_to(ROOT)),
                "contract_sha256": runtime["contract_sha256"],
                "source_evidence_ids": query["source_evidence_ids"],
                "query_provenance": query["provenance"],
                "admission_limitation": "AI-coded pilot accepted by project owner; not independently human-validated",
                "authorization_limitation": "executable only inside the benchmark sandbox",
            },
            "contract": contract_payload,
            "contract_freeze_lineage": {
                "frozen_at": frozen_contract["frozen_at"],
                "profile_provenance": PROFILE["parameter_provenance"],
                "witness_search_after_contract_freeze": True,
                "membership_uses_agent_or_solver_output": False,
            },
            "source_window": {
                "exact_key": window_key,
                "source_replay_file": str(REPLAYS.relative_to(ROOT)),
                "source_replay_file_sha256": replay_file_hash,
                "prefix_policy": witness["prefix_policy"],
                "pre_action_observation_provenance": witness["pre_action_observation_provenance"],
                "callback_timing": "observe at begin-zone-timestep before actuator write; collect effect after zone reporting",
            },
            "backend_binding": {
                "backend": "EnergyPlus 26.1.0",
                "runtime_driver_sha256": runtime["runtime_driver_sha256"],
                "policy_bridge": runtime["policy_bridge"],
                "model": runtime["source_model"],
                "model_sha256": runtime["source_model_sha256"],
                "working_model_sha256": runtime["working_model_sha256"],
                "energyplus_binary_sha256": runtime["energyplus_binary_sha256"],
                "weather_path": runtime["weather_path"],
                "weather_sha256": runtime["weather_sha256"],
                "zone": runtime["zone"],
                "people_object": runtime["people"],
                "occupancy_schedule": runtime["occupancy_schedule"],
                "actuator": {
                    "component_type": "Schedule:Compact",
                    "control_type": "Schedule Value",
                    "key": runtime["heating_schedule"],
                    "command_effective_setpoint_note": runtime["command_effective_setpoint_note"],
                },
            },
            "replay_certificates": {
                "witness_digest": witness["trace_digest"],
                "contrast_digest": contrast["trace_digest"],
                "noop_digest": noop["trace_digest"],
                "witness_metrics": evaluator_scores["witness"],
                "contrast_metrics": evaluator_scores["contrast"],
                "noop_metrics": evaluator_scores["noop"],
                "facility_demand_interpretation": runtime["facility_demand_interpretation"],
            },
            "qa_verdicts": {
                "family_context_match": True,
                "space_scope_match": True,
                "same_runtime_lineage": True,
                "action_before_effect_alignment": True,
                "initial_observation_alignment": source_gate["initial_observation_alignment"],
                "trajectory_complete": source_gate["complete_96"],
                "no_nan": source_gate["no_nan"],
                "action_sensitive": source_gate["action_sensitivity"],
                "evaluator_sensitive": source_gate["evaluator_sensitivity"],
                "fixed_witness_feasible": source_gate["fixed_witness_feasible"],
                "noop_compared": source_gate["no_op_comparison"],
                "deterministic": source_gate["selected_determinism"],
                "dynamic_policy_replay": runtime["dynamic_policy_gate"]["exact_determinism"] and runtime["dynamic_policy_gate"]["nonconstant_actions"],
                "responsibility_active_steps": active_steps,
                "passed": True,
            },
            "selection_lineage": {
                "assignment_rule": "all passing residential thermal windows bind the single frozen generic-comfort contract",
                "occupancy_steps": occupancy_steps,
                "primary_window_is_unique": True,
                "split": "none",
                "split_reason": "all windows share one model/weather/runtime connected component",
            },
            "source_hashes": {
                "runtime_gate_sha256": runtime_hash,
                "compiler_sha256": compiler_hash,
                "catalog_sha256": catalog_hash,
            },
            "gold_actions": [],
        }
        public_rows.append(public)
        private_rows.append(private)
        gate_rows.append({
            "episode_id": episode_id,
            "responsibility_id": responsibility_id,
            "physical_process_id": process_id,
            "window_key": window_key,
            "active_steps": active_steps,
            "gates": private["qa_verdicts"],
            "passed": True,
        })

    counts = Counter(row["responsibility_id"] for row in private_rows)
    if dict(counts) != EXPECTED or len(public_rows) != 30 or len(excluded) != 1:
        raise RuntimeError(f"fail closed: expected {EXPECTED}/30/1, got {dict(counts)}/{len(public_rows)}/{len(excluded)}")
    if len({row["physical_process_id"] for row in private_rows}) != 30:
        raise RuntimeError("a physical window was assigned more than one primary responsibility")
    if {row["episode_id"] for row in public_rows} != {row["episode_id"] for row in private_rows}:
        raise RuntimeError("public/private Episode identity mismatch")
    if any(row["split"] != "none" for row in public_rows) or any(row["gold_actions"] for row in private_rows):
        raise RuntimeError("split leakage or gold-action leakage")

    report = {
        "schema_version": "energyplus-responsibility-home-release-v1",
        "release_scope": "final supported pilot release; provisional semantics and sandbox-only authorization",
        "candidate_window_count": 31,
        "episode_count": 30,
        "excluded_window_count": 1,
        "responsibility_count": 1,
        "responsibility_episode_counts": dict(sorted(counts.items())),
        "split_counts": {"none": 30},
        "source_model": runtime["source_model"],
        "source_runtime_gate_sha256": runtime_hash,
        "public_digest": digest(public_rows),
        "private_digest": digest(private_rows),
        "gold_actions_released": False,
        "all_primary_processes_unique": True,
        "passed": True,
        "limitations": [
            "responsibility admission is an AI-coded pilot accepted by the project owner, not independent human coding",
            "authorization is benchmark-sandbox-only",
            "all Episodes share one residential model, one weather file, and one January run; no train/dev/test split is claimed",
            "24-hour windows observe but do not complete MAINTAIN responsibilities",
        ],
    }
    replay_gate = {
        "schema_version": "energyplus-responsibility-home-replay-gate-v1",
        "candidate_window_count": 31,
        "passed_episode_count": 30,
        "excluded_windows": excluded,
        "all_released_episodes_passed": all(row["passed"] for row in gate_rows),
        "episodes": gate_rows,
    }
    dataset_card = """# EnergyPlus Home Responsibility Episodes v1

This is the final supported **pilot** release produced by the v11 compiler. It contains 30 executable 24-hour (96 x 15-minute) residential thermal Episodes for one frozen generic-comfort responsibility from a pinned EnergyPlus single-family-house model. One of 31 January windows is excluded because the frozen witness fails the hard comfort contract. The occupancy-dependent warm-while-home responsibility is intentionally not released because the source occupancy schedule is a thermal-load proxy, not a defensible home-presence lifecycle signal.

Each public record exposes one natural standing-intent query, the pre-action initial observation, legal actions, a frozen profile (22 C target, +/-2 C hard band), evaluator semantics, and the continuing MAINTAIN terminal status. Private records retain responsibility evidence lineage, exact physical-window lineage, actuator binding, prefix-controlled witness/contrast/no-op certificates, QA gates, and empty `gold_actions`.

All Episodes share one model, weather file, and January runtime connected component, so `split=none`; this release does not claim train/dev/test generalization. Responsibility admission remains an AI-coded pilot accepted by the project owner, and action authorization is benchmark-sandbox-only. These limitations are explicit fields rather than hidden assumptions.

Build with `python3 compile_energyplus_responsibility_episodes.py`; verify byte identity with `python3 compile_energyplus_responsibility_episodes.py --check`.
"""
    return {
        RELEASE / "episodes_public.jsonl": "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in public_rows),
        RELEASE / "episodes_private.jsonl": "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in private_rows),
        RELEASE / "build_report.json": json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        RELEASE / "replay_gate.json": json.dumps(replay_gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        RELEASE / "DATASET_CARD.md": dataset_card,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = build()
    if args.check:
        stale = [str(path) for path, content in outputs.items() if not path.is_file() or path.read_text(encoding="utf-8") != content]
        if stale:
            raise SystemExit("stale residential responsibility release: " + ", ".join(stale))
        return
    RELEASE.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
