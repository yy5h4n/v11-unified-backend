#!/usr/bin/env python3
"""Probe real Harness V2 discrete-device fault transitions.

The output is replay evidence for the backend route only.  It contains no
responsibility, query, gold action, dataset episode, evaluator, or threshold.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from harness_v2.core import EpisodeSpec
from unified_compiler.adapters.d1_discrete_device_fault import (
    DiscreteDeviceFaultBackend,
    DiscreteFaultSchedule,
    DiscreteFaultWindow,
    SUPPORTED_DEVICE_IDS,
)


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "generated" / "d1_discrete_device_fault_v1" / "replay_gate.json"
MODES = ("offline", "stuck", "jammed", "slowdown")
COMMANDS = {
    "front_door_lock.main": ("lock.control", "unlock"),
    "garage_door.main": ("garage.door", "open"),
    "laundry.washer": ("laundry.control", "load"),
    "dishwasher.main": ("dishwasher.control", "load"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _episode() -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="d1-discrete-device-fault-probe",
        public_bootstrap={"scenario_type": "generic_household_workflow", "horizon_seconds": 600},
        seed=20260905,
    )


def _action(device_id: str, operation: str | None = None) -> dict[str, Any]:
    capability, default_operation = COMMANDS[device_id]
    return {
        "kind": "act",
        "commands": [{
            "device_id": device_id,
            "capability": capability,
            "operation": operation or default_operation,
            "parameters": {},
        }],
    }


def _run(device_id: str, mode: str | None, *, plan: str | None = None) -> dict[str, Any]:
    windows = [] if mode is None else [DiscreteFaultWindow(device_id, 0, 4, mode, slowdown_factor=2 if mode == "slowdown" else 1)]
    backend = DiscreteDeviceFaultBackend(DiscreteFaultSchedule(windows))
    reset = backend.reset(_episode())
    rows: list[dict[str, Any]] = [{"kind": "observation", "value": deepcopy(reset.public_observation)}]
    actions = [_action(device_id)]
    # A cycle's loading command is setup, not a benchmark label.  Starting it
    # at the next tick makes the slowdown observable in a real transition.
    if (plan or mode) == "slowdown" and device_id in {"laundry.washer", "dishwasher.main"}:
        actions = [_action(device_id), {"kind": "act", "commands": []}]
        start = _action(device_id, "start")
        actions.insert(1, {"kind": "wait", "mode": "for", "duration_seconds": 60})
        actions.insert(2, start)
    for action in actions:
        before = backend.state_digest()
        outcome = backend.execute_atomic(action)
        after = backend.state_digest()
        rows.append({
            "kind": "action",
            "action": deepcopy(action),
            "accepted": outcome.accepted,
            "error_code": outcome.error_code,
            "state_digest_before": before,
            "state_digest_after": after,
            "feedback": deepcopy(outcome.public_feedback),
        })
        if outcome.accepted:
            step = backend.advance(action)
            rows.append({"kind": "observation", "value": deepcopy(step.public_observation)})
    return {
        "device_id": device_id,
        "mode": mode or "healthy",
        "schedule": backend.schedule.as_dict(),
        "trace": rows,
        "trace_sha256": _digest(rows),
        "provenance": {
            "backend_only": True,
            "schedule_agent_modifiable": False,
            "workflow_state_machine": "harness_v2.workflow_backend.WorkflowBackend",
        },
    }


def _physical_observations(trace: dict[str, Any]) -> list[dict[str, Any]]:
    device = trace["device_id"]
    return [
        row["value"]["devices"][device]
        for row in trace["trace"]
        if row["kind"] == "observation"
    ]


def _compact_run(run: dict[str, Any]) -> dict[str, Any]:
    """Keep only target-device evidence; never persist full public views."""
    device_id = run["device_id"]
    observations = []
    actions = []
    for row in run["trace"]:
        if row["kind"] == "observation":
            value = row["value"]
            target = value["devices"][device_id]
            observations.append({
                "step": target.get("attributes", {}).get("step", value.get("step")),
                "state": target["state"],
                "attributes": deepcopy(target.get("attributes", {})),
                "active_fault": deepcopy(value.get("active_device_faults", {}).get(device_id)),
            })
        else:
            action = row["action"]
            commands = action.get("commands", []) if isinstance(action, dict) else []
            command = commands[0] if commands else {}
            actions.append({
                "operation": command.get("operation"),
                "accepted": row["accepted"],
                "error_code": row["error_code"],
                "state_digest_unchanged": row["state_digest_before"] == row["state_digest_after"],
                "feedback": {
                    key: value
                    for key, value in row.get("feedback", {}).items()
                    if key in {"status", "error_code", "applied", "action_cost", "cost_unit"}
                },
            })
    return {
        "device_id": device_id,
        "mode": run["mode"],
        "schedule": run["schedule"],
        "trace_sha256": run["trace_sha256"],
        "actions": actions,
        "target_device_state_sequence": observations,
        "provenance": run["provenance"],
    }


def build_evidence() -> dict[str, Any]:
    cases: dict[str, dict[str, Any]] = {}
    for device_id in sorted(SUPPORTED_DEVICE_IDS):
        for mode in MODES:
            if mode == "slowdown" and device_id == "front_door_lock.main":
                continue  # a lock has no timed operation to slow down
            faulty = _run(device_id, mode)
            healthy = _run(device_id, None, plan=mode)
            repeated = _run(device_id, mode, plan=mode)
            fault_actions = [row for row in faulty["trace"] if row["kind"] == "action"]
            healthy_actions = [row for row in healthy["trace"] if row["kind"] == "action"]
            if mode in {"offline", "stuck", "jammed"}:
                first_fault = fault_actions[0]
                first_healthy = healthy_actions[0]
                rejected_without_mutation = (
                    not first_fault["accepted"]
                    and first_fault["error_code"] == {
                        "offline": "FAULT_DEVICE_OFFLINE",
                        "stuck": "FAULT_DEVICE_STUCK",
                        "jammed": "FAULT_DEVICE_JAMMED",
                    }[mode]
                    and first_fault["state_digest_before"] == first_fault["state_digest_after"]
                )
                same_action_diverges = first_fault["accepted"] != first_healthy["accepted"]
            else:
                # The final action is the timed cycle start for washer/dishwasher.
                fault_start = fault_actions[-1]
                healthy_start = healthy_actions[-1]
                fault_obs = _physical_observations(faulty)[-1]
                healthy_obs = _physical_observations(healthy)[-1]
                rejected_without_mutation = False
                rejected_without_mutation = fault_start["accepted"] and healthy_start["accepted"]
                same_action_diverges = fault_obs != healthy_obs
            deterministic = faulty["trace_sha256"] == repeated["trace_sha256"]
            compact_fault = _compact_run(faulty)
            compact_healthy = _compact_run(healthy)
            witness = {
                "same_requested_operations": [item["operation"] for item in compact_fault["actions"]]
                == [item["operation"] for item in compact_healthy["actions"]],
                "fault_action_outcomes": compact_fault["actions"],
                "healthy_action_outcomes": compact_healthy["actions"],
                "fault_trace_sha256": compact_fault["trace_sha256"],
                "healthy_trace_sha256": compact_healthy["trace_sha256"],
            }
            cases[f"{device_id}:{mode}"] = {
                "fault": compact_fault,
                "healthy": compact_healthy,
                "witness": witness,
                "checks": {
                    "deterministic_replay": deterministic,
                    "same_action_diverges": same_action_diverges,
                    "fault_transition_verified": rejected_without_mutation,
                },
            }
    adapter_path = ROOT / "unified_compiler" / "adapters" / "d1_discrete_device_fault.py"
    workflow_path = ROOT / "harness_v2" / "workflow_backend.py"
    probe_path = ROOT / "probe_d1_discrete_device_fault.py"
    checks = {
        "all_cases_completed": bool(cases) and all(
            item["checks"]["fault_transition_verified"] for item in cases.values()
        ),
        "deterministic_replay": all(item["checks"]["deterministic_replay"] for item in cases.values()),
        "same_action_healthy_fault_divergence": all(
            item["checks"]["same_action_diverges"] for item in cases.values()
        ),
        "provenance_complete": all(path.is_file() for path in (adapter_path, workflow_path, probe_path)),
    }
    return {
        "schema_version": "d1-discrete-device-fault-replay-gate-v1",
        "scope": "backend trajectory replay evidence only",
        "devices": sorted(SUPPORTED_DEVICE_IDS),
        "fault_modes": list(MODES),
        "cases": cases,
        "checks": checks,
        "verified": all(checks.values()),
        "provenance": {
            "adapter_path": str(adapter_path.relative_to(ROOT)),
            "adapter_sha256": _sha256(adapter_path),
            "workflow_backend_path": str(workflow_path.relative_to(ROOT)),
            "workflow_backend_sha256": _sha256(workflow_path),
            "probe_path": str(probe_path.relative_to(ROOT)),
            "probe_sha256": _sha256(probe_path),
            "workflow_backend_class": "WorkflowBackend",
            "state_machine_reused": True,
            "backend_only": True,
            "does_not_claim": [
                "responsibility_alignment", "gold_action", "dataset_quality",
                "evaluator_validity", "hardware_diagnostics", "failure_rate",
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = build_evidence()
    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"stale D1 discrete-device evidence: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"verified": report["verified"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
