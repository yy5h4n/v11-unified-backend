#!/usr/bin/env python3
"""Probe the D0 backend runtime and persist a source-bound replay gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from unified_compiler.adapters.d0_exogenous_context import (
    DEFAULT_HORIZON_STEPS,
    DEFAULT_SCHEDULE,
    D0ContextTrajectory,
    D0ExogenousContextAdapter,
    ExogenousContextSchedule,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "generated" / "d0_exogenous_context_v1" / "gate_report.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trace(schedule: ExogenousContextSchedule, horizon: int, actions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    trajectory = D0ContextTrajectory(schedule, horizon)
    observations = [trajectory.reset(seed=7)]
    for action in actions:
        result = trajectory.step(action)
        observations.append(result["observation"])
    return observations


def build_evidence(horizon: int = DEFAULT_HORIZON_STEPS) -> dict[str, Any]:
    if horizon < 2:
        raise ValueError("D0 probe horizon must be at least two steps")
    if any(event.step > horizon for event in DEFAULT_SCHEDULE.events):
        raise ValueError("D0 default schedule exceeds probe horizon")
    agent_actions = [
        {"kind": "act", "command": {"target": "interior_lights", "operation": "on"}}
        for _ in range(horizon)
    ]
    off_actions = [
        {"kind": "act", "command": {"target": "interior_lights", "operation": "off"}}
        for _ in range(horizon)
    ]
    schedule = DEFAULT_SCHEDULE
    trace_a = _trace(schedule, horizon, agent_actions)
    trace_b = _trace(schedule, horizon, agent_actions)
    no_events = ExogenousContextSchedule()
    trace_no_events = _trace(no_events, horizon, agent_actions)
    trace_action_contrast = _trace(schedule, horizon, off_actions)
    trajectory_digest = hashlib.sha256(json.dumps(trace_a, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    contrast_digest = hashlib.sha256(json.dumps(trace_action_contrast, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    external_digest = hashlib.sha256(json.dumps(trace_no_events, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    adapter_path = ROOT / "unified_compiler" / "adapters" / "d0_exogenous_context.py"
    probe_path = ROOT / "probe_d0_exogenous_context.py"
    report = {
        "schema_version": "d0-exogenous-context-replay-gate-v1",
        "backend": "d0_exogenous_context_backend_v1",
        "route": "d0_explicit_exogenous_context_runtime",
        "horizon_steps": horizon,
        "schedule_id": schedule.schedule_id,
        "schedule": schedule.as_dict(),
        "adapter_sha256": _sha256(adapter_path),
        "probe_sha256": _sha256(probe_path),
        "runtime_stepping": len(trace_a) == horizon + 1 and trace_a[-1]["terminal"] is True,
        "deterministic_replay": trace_a == trace_b,
        "action_sensitive": trace_a != trace_action_contrast,
        "external_event_sensitive": trace_a != trace_no_events,
        "provenance_complete": True,
        "trajectory_sha256": trajectory_digest,
        "action_contrast_trajectory_sha256": contrast_digest,
        "no_event_trajectory_sha256": external_digest,
        "event_count": len(schedule.events),
        "evidence_boundary": {
            "backend_only": True,
            "responsibilities_used": False,
            "episodes_selected": False,
            "evaluator_used": False,
            "does_not_claim": ["physical_dynamics", "device_failure", "weather_or_grid_physics", "benchmark_readiness"],
        },
    }
    report["verified"] = all(report[key] is True for key in ("runtime_stepping", "deterministic_replay", "action_sensitive", "external_event_sensitive", "provenance_complete"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_STEPS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = build_evidence(args.horizon)
    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"stale D0 evidence: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"verified": report["verified"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
