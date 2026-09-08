#!/usr/bin/env python3
"""Replay/causal/feasibility gate for V2.5-supported HVAC candidates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
from typing import Any

from compile_supported_hvac_episodes import ROOT, V10_RELEASE, load_jsonl
from unified_compiler.adapters.legacy_v10 import V10ArtifactIndex
from unified_compiler.adapters.legacy_v10_runtime import V10RuntimeBridge


OUTPUT = ROOT / "generated" / "supported_hvac_replay_gate_v1.json"
TOLERANCE_C = 2.0


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_gate() -> dict[str, Any]:
    private = load_jsonl(V10_RELEASE / "episodes_private.jsonl")
    candidates = sorted(
        (row for row in private if row.get("subset") == "main" and row["backend_binding"]["backend"] == "CityLearn"),
        key=lambda row: row["physical_process_id"],
    )
    bridge = V10RuntimeBridge(V10ArtifactIndex(V10_RELEASE))
    records = []
    for row in candidates:
        episode_id = row["episode_id"]
        maintain_a = bridge.replay(episode_id, policy=lambda _step, _obs: "MAINTAIN_COMFORT")
        maintain_b = bridge.replay(episode_id, policy=lambda _step, _obs: "MAINTAIN_COMFORT")
        eco = bridge.replay(episode_id, policy=lambda _step, _obs: "ECO_OFF")
        deterministic = maintain_a == maintain_b
        completed = maintain_a["completed"] and eco["completed"]
        max_action_delta = max(
            abs(a["effect"]["indoor_temperature_c"] - b["effect"]["indoor_temperature_c"])
            for a, b in zip(maintain_a["transitions"], eco["transitions"])
        )
        occupied = [
            t for t in maintain_a["transitions"]
            if t["observation"]["occupant_count"] > 0
        ]
        violations = [
            abs(t["effect"]["indoor_temperature_c"] - t["observation"]["resident_setpoint_c"])
            for t in occupied
            if abs(t["effect"]["indoor_temperature_c"] - t["observation"]["resident_setpoint_c"]) > TOLERANCE_C + 1e-9
        ]
        passed = deterministic and completed and max_action_delta > 1e-9 and not violations and bool(occupied)
        reasons = []
        if not deterministic:
            reasons.append("NONDETERMINISTIC_REPLAY")
        if not completed:
            reasons.append("INCOMPLETE_REPLAY")
        if max_action_delta <= 1e-9:
            reasons.append("ACTION_INSENSITIVE")
        if not occupied:
            reasons.append("NO_RESPONSIBILITY_OPPORTUNITY")
        if violations:
            reasons.append("FIXED_WITNESS_CONTRACT_INFEASIBLE")
        records.append({
            "process_id": row["physical_process_id"],
            "legacy_episode_id": episode_id,
            "passed": passed,
            "exclusion_reasons": reasons,
            "deterministic": deterministic,
            "completed": completed,
            "occupied_step_count": len(occupied),
            "comfort_violation_count": len(violations),
            "maximum_comfort_deviation_c": max(
                (abs(t["effect"]["indoor_temperature_c"] - t["observation"]["resident_setpoint_c"]) for t in occupied),
                default=0.0,
            ),
            "maximum_action_temperature_delta_c": max_action_delta,
            "trajectory_digests": {
                "fixed_feasibility_witness": digest(maintain_a["transitions"]),
                "causal_contrast": digest(eco["transitions"]),
            },
        })
    passed_count = sum(row["passed"] for row in records)
    return {
        "schema_version": "supported-hvac-replay-gate-v1",
        "candidate_process_count": len(records),
        "passed_count": passed_count,
        "excluded_count": len(records) - passed_count,
        "all_replays_deterministic": all(row["deterministic"] for row in records),
        "all_processes_action_sensitive": all(row["maximum_action_temperature_delta_c"] > 1e-9 for row in records),
        "fixed_witness_policy": "MAINTAIN_COMFORT (private feasibility gate only)",
        "causal_contrast_policy": "ECO_OFF (private action-sensitivity gate only)",
        "comfort_tolerance_c": TOLERANCE_C,
        "comfort_tolerance_status": "construction_witness_parameter_not_responsibility_semantics",
        "runtime_fingerprint": {
            "python": platform.python_version(),
            "numpy": importlib.metadata.version("numpy"),
            "citylearn": importlib.metadata.version("citylearn"),
            "v10_runtime_sha256": hashlib.sha256((ROOT.parent / "v10_diversity_aware_compiler" / "runtime.py").read_bytes()).hexdigest(),
            "v10_public_sha256": hashlib.sha256((V10_RELEASE / "episodes_public.jsonl").read_bytes()).hexdigest(),
            "v10_private_sha256": hashlib.sha256((V10_RELEASE / "episodes_private.jsonl").read_bytes()).hexdigest(),
        },
        "solver_or_agent_output_used_for_membership": False,
        "gold_actions_released": False,
        "processes": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build_gate(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != content:
            raise SystemExit(f"stale replay gate: {OUTPUT}")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
