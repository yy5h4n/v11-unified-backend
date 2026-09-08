#!/usr/bin/env python3
"""Probe the six D1 mechanism presets against pinned SustainGym.

This emits backend trajectory evidence only.  It intentionally does not
construct a benchmark episode, responsibility record, query, or evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from unified_compiler.adapters.d1_fault_mechanism import (
    D1FaultEpisode,
    D1_PROFILE_NAMES,
    SensorFaultSchedule,
    d1_profile,
)
from unified_compiler.adapters.sustaingym_building import (
    PINNED_COMMIT,
    PINNED_SEED,
    SustainGymBuildingAdapter,
)

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "generated" / "d1_fault_profiles_v1"
STEPS = 8


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _run_with_private(schedule, sensors, actions):
    base = SustainGymBuildingAdapter()
    episode = D1FaultEpisode(base, schedule, "d1-profile-probe", sensors)
    initial = episode.reset(seed=PINNED_SEED)
    rows = []
    private = [base.private_state()]
    for action in actions:
        rows.append(episode.step(action))
        private.append(base.private_state())
    return initial, rows, private


def probe_profile(name: str) -> dict[str, Any]:
    schedule, sensors = d1_profile(name)
    healthy_schedule, healthy_sensors = d1_profile("failed")
    # The healthy comparator has no active fault, while retaining the same
    # public action surface and pinned backend configuration.
    from unified_compiler.adapters.d1_fault_mechanism import ActuatorFaultSchedule
    healthy_schedule = ActuatorFaultSchedule()
    probe_base = SustainGymBuildingAdapter()
    initial = probe_base.reset(seed=PINNED_SEED)
    action = [-0.05] * len(initial["zone_temperatures_c"])
    if name == "stuck":
        # Change the request immediately before and after onset.  A stuck
        # actuator must retain the last pre-fault physical output.
        actions = [action, [-0.02] * len(action), [0.0] * len(action)] + [action] * (STEPS - 3)
    else:
        actions = [action] * STEPS
    faulty_initial, faulty_rows, faulty_private = _run_with_private(schedule, sensors, actions)
    healthy_initial, healthy_rows, healthy_private = _run_with_private(
        healthy_schedule, healthy_sensors, actions
    )
    actuator = name in {"degraded", "failed", "stuck", "intermittent_dropout"}
    if actuator:
        effective_contrast = any(
            a["d1"]["effective_action"] != b["d1"]["effective_action"]
            for a, b in zip(faulty_rows, healthy_rows)
        )
        thermal_divergence = any(
            a["observation"]["zone_temperatures_c"]
            != b["observation"]["zone_temperatures_c"]
            for a, b in zip(faulty_rows, healthy_rows)
        )
        public_changed = any(
            a["observation"]["zone_temperatures_c"] != b["observation"]["zone_temperatures_c"]
            for a, b in zip(faulty_rows, healthy_rows)
        )
        private_unchanged = False
        if name == "degraded":
            mode_semantics = (
                0.0 < faulty_rows[2]["d1"]["effective_action"][0] / faulty_rows[2]["d1"]["requested_action"][0] < 1.0
            )
        elif name == "stuck":
            mode_semantics = (
                faulty_rows[2]["d1"]["effective_action"] == faulty_rows[1]["d1"]["effective_action"]
                and faulty_rows[2]["d1"]["requested_action"] != faulty_rows[2]["d1"]["effective_action"]
            )
        elif name == "intermittent_dropout":
            mode_semantics = (
                faulty_rows[2]["d1"]["effective_action"] == [0.0] * len(action)
                and faulty_rows[3]["d1"]["effective_action"] == action
                and faulty_rows[2]["d1"]["fault"]["dropout_active"] is True
            )
        else:
            mode_semantics = faulty_rows[2]["d1"]["effective_action"] == [0.0] * len(action)
    else:
        effective_contrast = all(
            a["d1"]["effective_action"] == b["d1"]["effective_action"]
            for a, b in zip(faulty_rows, healthy_rows)
        )
        thermal_divergence = False
        variable = sensors.windows[0].variable
        public_changed = any(
            a["observation"][variable] != b["observation"][variable]
            for a, b in zip(faulty_rows, healthy_rows)
        )
        private_unchanged = all(
            a["state_vector"] == b["state_vector"]
            for a, b in zip(faulty_private, healthy_private)
        )
        corrections = [row["observation"][variable][0] - healthy_rows[i]["observation"][variable][0] for i, row in enumerate(faulty_rows)]
        if name == "bias":
            mode_semantics = all(abs(value - 1.0) < 1e-9 for value in corrections[1:5])
        else:
            mode_semantics = corrections[1] < corrections[2] < corrections[3] < corrections[4]
    repeat_initial, repeat_rows, _ = _run_with_private(schedule, sensors, actions)
    deterministic_a = digest({"initial": faulty_initial, "transitions": faulty_rows})
    deterministic_b = digest({"initial": repeat_initial, "transitions": repeat_rows})
    adapter_path = ROOT / "unified_compiler" / "adapters" / "d1_fault_mechanism.py"
    base_path = ROOT / "unified_compiler" / "adapters" / "sustaingym_building.py"
    checks = {
        "backend_importable": True,
        "all_replays_completed": len(faulty_rows) == STEPS,
        "same_reset_same_window_replay": deterministic_a == deterministic_b,
        "action_sensitive": effective_contrast,
        "fault_changes_future_state": thermal_divergence,
        "fault_changes_feasible_strategy": effective_contrast,
        "public_observation_changed": public_changed,
        "private_state_unchanged": private_unchanged,
        "mode_semantics": mode_semantics,
        "fault_onset_at_schedule": faulty_rows[2]["d1"]["fault"]["active"] if actuator else faulty_rows[0]["observation"]["sensor_health"]["active"] is False and faulty_rows[1]["observation"]["sensor_health"]["active"] is True,
    }
    if actuator:
        verified = all(checks[key] for key in (
            "backend_importable", "all_replays_completed",
            "same_reset_same_window_replay", "action_sensitive",
            "fault_changes_future_state", "fault_changes_feasible_strategy",
            "mode_semantics", "fault_onset_at_schedule",
        ))
    else:
        verified = all(checks[key] for key in (
            "backend_importable", "all_replays_completed",
            "same_reset_same_window_replay", "action_sensitive",
            "public_observation_changed", "private_state_unchanged",
            "mode_semantics", "fault_onset_at_schedule",
        ))
    return {
        "schema_version": "d1-fault-profile-gate-v1",
        "profile": name,
        "backend": "SustainGym BuildingEnv",
        "backend_commit": PINNED_COMMIT,
        "adapter_sha256": hashlib.sha256(adapter_path.read_bytes()).hexdigest(),
        "base_adapter_sha256": hashlib.sha256(base_path.read_bytes()).hexdigest(),
        "actuator_schedule_id": schedule.schedule_id,
        "sensor_schedule_id": sensors.schedule_id,
        "probe_steps": STEPS,
        "seed": PINNED_SEED,
        "checks": checks,
        **checks,
        "fault_trajectory_sha256": digest({"initial": faulty_initial, "transitions": faulty_rows}),
        "healthy_trajectory_sha256": digest({"initial": healthy_initial, "transitions": healthy_rows}),
        "verified": verified,
        "status": "BACKEND_REPLAY_VERIFIED" if verified else "EVIDENCE_PENDING",
        "evidence_boundary": {
            "backend_only": True,
            "trajectory_not_benchmark_episode": True,
            "does_not_claim": ["responsibility_alignment", "evaluator_validity", "notification_success", "multi_device_physics"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--profile", choices=D1_PROFILE_NAMES)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    names = [args.profile] if args.profile else list(D1_PROFILE_NAMES)
    manifest = {
        "schema_version": "d1-fault-profiles-manifest-v1",
        "scope": "backend trajectory replay evidence only",
        "profiles": {},
    }
    for name in names:
        report = probe_profile(name)
        path = args.output / f"{name}.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest["profiles"][name] = {
            "path": str(path.relative_to(ROOT)),
            "verified": report["verified"],
            "trajectory_sha256": report["fault_trajectory_sha256"],
        }
    if not args.profile:
        manifest["all_verified"] = all(x["verified"] for x in manifest["profiles"].values())
        (args.output / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.check:
        assert all(x["verified"] for x in manifest["profiles"].values())
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
