#!/usr/bin/env python3
"""Probe CityLearn 2.5.0 battery health mechanisms (backend evidence only)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from unified_compiler.adapters.citylearn_battery_fault import (
    DEFAULT_BUILDING,
    DEFAULT_HORIZON,
    DEFAULT_START,
    DEFAULT_PROFILE_GATE_DIR,
    FAULT_MODES,
    BatteryFaultSchedule,
    CityLearnBatteryFaultError,
    battery_fault_profile,
    run_battery_fault,
    provenance,
    _sha256,
    _runtime_fingerprint,
)

ROOT = Path(__file__).resolve().parent
ADAPTER = ROOT / "unified_compiler" / "adapters" / "citylearn_battery_fault.py"
STEPS = DEFAULT_HORIZON


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _artifact_check(
    destination: Path,
    names: list[str],
    expected_reports: dict[str, dict[str, Any]],
    expected_manifest: dict[str, Any],
    *,
    check_manifest: bool,
) -> None:
    """Compare evidence without creating, truncating, or replacing files."""
    mismatches: list[str] = []
    for name in names:
        path = destination / f"{name}.json"
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            mismatches.append(f"missing {path}")
            continue
        except (OSError, json.JSONDecodeError) as exc:
            mismatches.append(f"invalid {path}: {exc}")
            continue
        if actual != expected_reports[name]:
            mismatches.append(f"stale {path}")
    if check_manifest:
        path = destination / "manifest.json"
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            mismatches.append(f"missing {path}")
        except (OSError, json.JSONDecodeError) as exc:
            mismatches.append(f"invalid {path}: {exc}")
        else:
            if actual != expected_manifest:
                mismatches.append(f"stale {path}")
    if mismatches:
        raise SystemExit("D1 CityLearn evidence check failed: " + "; ".join(mismatches))


def probe_profile(name: str, building: str = DEFAULT_BUILDING, start: int = DEFAULT_START, horizon: int = STEPS) -> dict[str, Any]:
    schedule = battery_fault_profile(name)
    actions = tuple(1.0 if index % 2 == 0 else -0.75 for index in range(horizon))
    faulty_a = run_battery_fault(building, start, horizon, schedule, actions)
    faulty_b = run_battery_fault(building, start, horizon, schedule, actions)
    healthy = run_battery_fault(building, start, horizon, BatteryFaultSchedule(), actions)
    if faulty_a != faulty_b:
        raise CityLearnBatteryFaultError(f"non-deterministic CityLearn replay for {name}")
    fault_rows, healthy_rows = faulty_a["records"], healthy["records"]
    soc_delta = max(abs(a["effect"]["battery_soc"] - b["effect"]["battery_soc"]) for a, b in zip(fault_rows, healthy_rows))
    net_delta = max(abs(a["effect"]["net_electricity_kwh"] - b["effect"]["net_electricity_kwh"]) for a, b in zip(fault_rows, healthy_rows))
    action_delta = max(abs(a["effective_action"] - b["effective_action"]) for a, b in zip(fault_rows, healthy_rows))
    capacity_delta = max(abs(a["effect"]["battery_capacity_kwh"] - b["effect"]["battery_capacity_kwh"]) for a, b in zip(fault_rows, healthy_rows))
    checks = {
        "backend_importable": True,
        "citylearn_2_5_0": faulty_a["backend_version"] == "2.5.0",
        "deterministic_replay": True,
        "healthy_fault_counterfactual": True,
        "all_replays_completed": len(fault_rows) == horizon and all(row["episode_done"] == (i == horizon - 1) for i, row in enumerate(fault_rows)),
        "soc_divergence": soc_delta > 1e-9,
        "net_electricity_divergence": net_delta > 1e-9,
        "effective_action_contrast": action_delta > 1e-9 if name in {"power_derating", "unavailable", "stuck"} else True,
        "capacity_contrast": capacity_delta > 1e-9 if name == "capacity_degradation" else True,
        "provenance_complete": all(isinstance(value, str) and len(value) == 64 for key, value in faulty_a["provenance"].items() if key.endswith("sha256")),
    }
    verified = all(checks.values())
    return {
        "schema_version": "d1-citylearn-battery-fault-replay-gate-v1",
        "backend": "CityLearn", "backend_version": "2.5.0",
        "building_id": building,
        "source_window": {"start": start, "end": start + horizon - 1, "horizon_steps": horizon},
        "profile": name, "schedule_id": schedule.schedule_id,
        "adapter_sha256": _sha256(ADAPTER),
        "checks": checks, **checks,
        "soc_divergence_max": soc_delta,
        "net_electricity_divergence_max": net_delta,
        "effective_action_contrast_max": action_delta,
        "capacity_contrast_max_kwh": capacity_delta,
        "fault_trajectory_sha256": digest({"initial": faulty_a["initial"], "records": fault_rows}),
        "healthy_trajectory_sha256": digest({"initial": healthy["initial"], "records": healthy_rows}),
        "provenance": faulty_a["provenance"],
        "fault_schedule": schedule.as_dict(),
        "verified": verified,
        "status": "BACKEND_REPLAY_VERIFIED" if verified else "EVIDENCE_PENDING",
        "evidence_boundary": {"backend_only": True, "trajectory_not_benchmark_episode": True, "does_not_claim": ["responsibility_alignment", "evaluator_validity", "notification_success", "multi_device_physics"]},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=FAULT_MODES)
    parser.add_argument("--building", default=DEFAULT_BUILDING)
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="verify existing evidence; never write files")
    args = parser.parse_args()
    if args.check and args.output is not None:
        parser.error("--check cannot be combined with --output")
    names = [args.profile] if args.profile else list(FAULT_MODES)
    destination = args.output or DEFAULT_PROFILE_GATE_DIR
    if not args.check:
        destination.mkdir(parents=True, exist_ok=True)
    reports: dict[str, dict[str, Any]] = {}
    expected_reports: dict[str, dict[str, Any]] = {}
    for name in names:
        report = probe_profile(name, args.building, args.start, args.horizon)
        expected_reports[name] = report
        path = destination / (f"{name}.json" if args.output is None or len(names) > 1 else args.output.name)
        reports[name] = {"path": str(path.relative_to(ROOT)), "verified": report["verified"], "trajectory_sha256": report["fault_trajectory_sha256"]}
    manifest = {"schema_version": "d1-citylearn-battery-fault-profiles-v1", "backend_only": True, "backend_version": "2.5.0", "profiles": reports, "all_verified": all(item["verified"] for item in reports.values())}
    if args.check:
        _artifact_check(destination, names, expected_reports, manifest, check_manifest=args.profile is None)
    else:
        for name in names:
            path = destination / (f"{name}.json" if args.output is None or len(names) > 1 else args.output.name)
            path.write_text(json.dumps(expected_reports[name], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.output is None and not args.check:
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    if not manifest["all_verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
