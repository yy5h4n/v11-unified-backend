#!/usr/bin/env python3
"""Probe the native EV2Gym charger-fault backend and write standalone evidence.

The probe only emits backend trajectory evidence.  It does not construct a
benchmark Episode, responsibility contract, query, evaluator, or threshold.
If the pinned native runtime cannot be imported/replayed, a pending gate is
written instead of claiming executability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from unified_compiler.adapters.ev2gym_claim import EV2GymClaimAdapter
from unified_compiler.adapters.ev2gym_fault import (
    ADAPTER_ID,
    EV2GymFaultTrajectory,
    PROFILE_SCHEDULES,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "generated" / "ev2gym_fault_replay_gate_v1.json"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _run(base: EV2GymClaimAdapter, episode_id: str, schedule_name: str, actions: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    private = base.index.private_record(episode_id)
    battery_kwh = float(private["backend_binding"]["battery_kwh"])
    trajectory = EV2GymFaultTrajectory(
        base.episode_runtime(episode_id), PROFILE_SCHEDULES[schedule_name], episode_id,
        battery_kwh=battery_kwh,
    )
    initial = trajectory.reset()
    transitions = []
    for action in actions:
        if trajectory.done:
            break
        transitions.append(trajectory.step(action))
    if not trajectory.done:
        raise RuntimeError(f"native trajectory did not terminate: {episode_id}")
    delivered = sum(float(row["effect"]["delivered_charging_kwh"] or 0.0) for row in transitions)
    final_soc = transitions[-1]["effect"].get("vehicle_soc_after") if transitions else None
    # Keep the complete trajectory only in memory for causal checks.  Evidence
    # stores its digest plus a compact witness, never all observations.
    active = [
        {
            "step": row["d1_fault"]["health"]["step_index"],
            "mode": row["d1_fault"]["health"]["mode"],
            "availability": row["d1_fault"]["health"]["availability"],
            "charger_max_power_kw": row["observation"].get("charger_max_power_kw"),
            "requested_charge_power_kw": row["d1_fault"]["requested_charge_power_kw"],
            "effective_charge_power_kw": row["d1_fault"]["effective_charge_power_kw"],
            "delivered_charging_kwh": row["effect"].get("delivered_charging_kwh"),
            "vehicle_soc_before": row["effect"].get("vehicle_soc_before"),
            "vehicle_soc_after": row["effect"].get("vehicle_soc_after"),
        }
        for row in transitions
        if row["d1_fault"]["health"]["active"]
    ]
    witness = active[:2] + (active[-1:] if len(active) > 2 else [])
    return {
        "initial_state": {
            "charger_max_power_kw": initial.get("charger_max_power_kw"),
            "vehicle_connected": initial.get("vehicle_connected"),
            "vehicle_soc": initial.get("vehicle_soc"),
        },
        "steps_completed": len(transitions),
        "delivered_charging_kwh": round(delivered, 6),
        "final_soc": final_soc,
        "trajectory_sha256": digest({"initial": initial, "transitions": transitions}),
        "fault_witness": witness,
    }, transitions


def build_gate() -> dict[str, Any]:
    gate: dict[str, Any] = {
        "schema_version": "ev2gym-d1-fault-replay-gate-v1",
        "adapter_id": ADAPTER_ID,
        "base_adapter_id": "unified_compiler.adapters.ev2gym_claim.v1",
        "adapter_sha256": hashlib.sha256((ROOT / "unified_compiler" / "adapters" / "ev2gym_fault.py").read_bytes()).hexdigest(),
        "schedule_ids": {name: schedule.schedule_id for name, schedule in PROFILE_SCHEDULES.items()},
        "backend": "EV2Gym",
        "scope": "backend trajectory replay evidence only",
        "evidence_boundary": {
            "frozen_v10_artifacts_role": "pinned native EV2Gym config and source window only",
            "new_benchmark_episode_selected_or_generated": False,
            "does_not_claim": [
                "responsibility_alignment",
                "benchmark_validity",
                "query_or_evaluator_behavior",
            ],
        },
        "verified": False,
        "status": "EVIDENCE_PENDING",
    }
    try:
        base = EV2GymClaimAdapter()
        verification = base.verification()
        processes = base.processes()
        process = next(item for item in processes if item.backend == "EV2Gym")
        episode_id = sorted(process.manifest["legacy_episode_ids"])[0]
        horizon = process.horizon_steps
        actions = [{"type": "SET_CHARGE_POWER", "kw": 3.68}] * horizon
        healthy, healthy_transitions = _run(base, episode_id, "healthy", actions)
        profile_runs = {name: _run(base, episode_id, name, actions) for name in ("derated", "outage", "intermittent")}
        profiles = {name: run[0] for name, run in profile_runs.items()}
        deterministic = {}
        divergence = {}
        for name, (trace, transitions) in profile_runs.items():
            repeat, _ = _run(base, episode_id, name, actions)
            deterministic[name] = trace["trajectory_sha256"] == repeat["trajectory_sha256"]
            divergence[name] = {
                "delivered_charging_kwh": round(healthy["delivered_charging_kwh"] - trace["delivered_charging_kwh"], 6),
                "final_soc": None if healthy["final_soc"] is None or trace["final_soc"] is None else round(float(healthy["final_soc"]) - float(trace["final_soc"]), 6),
                "effective_action_differs": any(
                    a["d1_fault"]["effective_charge_power_kw"] != b["d1_fault"]["effective_charge_power_kw"]
                    for a, b in zip(healthy_transitions, transitions)
                ),
            }
        gate.update({
            "native_verification": verification,
            "probe_process_id": process.process_id,
            "probe_episode_id": episode_id,
            "probe_horizon_steps": horizon,
            "healthy": healthy,
            "profiles": profiles,
            "deterministic_replay": deterministic,
            "healthy_vs_fault_counterfactual": divergence,
            "verified": all(deterministic.values()) and all(item["effective_action_differs"] for item in divergence.values()),
        })
        gate["status"] = "BACKEND_REPLAY_VERIFIED" if gate["verified"] else "EVIDENCE_PENDING"
    except Exception as exc:
        gate["status"] = "EVIDENCE_PENDING"
        gate["pending_reason"] = f"{type(exc).__name__}: {exc}"
    return gate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="rebuild in memory and fail if output is stale; never write")
    args = parser.parse_args()
    gate = build_gate()
    if args.check:
        if not args.output.is_file():
            print(json.dumps({"path": str(args.output), "check": "missing"}, sort_keys=True), file=sys.stderr)
            raise SystemExit(1)
        try:
            current = json.loads(args.output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"stale or invalid gate: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        if current != gate:
            print(json.dumps({"path": str(args.output), "check": "stale"}, sort_keys=True), file=sys.stderr)
            raise SystemExit(1)
        print(json.dumps({"path": str(args.output), "check": "ok", "verified": gate.get("verified")}, sort_keys=True))
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(args.output), "verified": gate["verified"], "status": gate["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
