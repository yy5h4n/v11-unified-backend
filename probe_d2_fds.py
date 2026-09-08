#!/usr/bin/env python3
"""Probe FDS smoke/fire/door propagation and write a backend-only gate.

The probe performs two fresh-process action contrasts (door closed/open), each
with a deterministic reset replay.  If no real FDS executable is available,
the result is explicitly ``EVIDENCE_PENDING`` and no synthetic trace is
written.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from d2_fds_adapter import (
    FDSRuntimeUnavailable,
    FDSSmokePropagationAdapter,
    MODEL_TEMPLATE,
    discover_fds_runtime,
    sha256,
)

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "generated/d2_fds_v1"
REPORT = BASE / "gate_report.json"
RUNTIME_METADATA = ROOT / "shared_runtime/fds/metadata/FDS_RUNTIME_PROVENANCE.json"


def save_trace(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = BASE / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": sha256(path)}


def _finite_trace(record: dict[str, Any]) -> bool:
    return bool(record.get("trace")) and all(
        math.isfinite(float(value))
        for row in record["trace"]
        for value in row["observation"].values()
    )


def _valid_time_axis(trace: list[dict[str, Any]]) -> bool:
    """FDS may adapt its timestep after a geometry contrast; require a valid
    monotone axis rather than byte-for-byte equal samples between runs."""
    if not trace:
        return False
    times = [float(row["time_s"]) for row in trace]
    return (
        math.isfinite(times[0])
        and math.isfinite(times[-1])
        and abs(times[0]) <= 1e-9
        and all(right >= left for left, right in zip(times, times[1:]))
    )


def _interpolate(trace: list[dict[str, Any]], time_s: float, role: str) -> float:
    """Linearly sample a real FDS trace on the other run's adaptive axis."""
    if time_s <= trace[0]["time_s"]:
        return float(trace[0]["observation"][role])
    if time_s >= trace[-1]["time_s"]:
        return float(trace[-1]["observation"][role])
    for left, right in zip(trace, trace[1:]):
        t0, t1 = left["time_s"], right["time_s"]
        if t0 <= time_s <= t1:
            if t1 == t0:
                return float(right["observation"][role])
            fraction = (time_s - t0) / (t1 - t0)
            v0 = float(left["observation"][role])
            v1 = float(right["observation"][role])
            return v0 + fraction * (v1 - v0)
    raise RuntimeError("FDS trace time interpolation failed")


def _common_backend_provenance(records: dict[str, dict[str, Any]]) -> bool:
    provenances = [record.get("provenance", {}) for record in records.values()]
    if not provenances:
        return False
    runtime_hashes = {item.get("runtime_sha256") for item in provenances}
    return (
        all(item.get("backend") == "FDS" for item in provenances)
        and len(runtime_hashes) == 1
        and all(item.get("reset_semantics") == "new FDS process and freshly generated immutable deck for every run" for item in provenances)
    )


def build() -> dict[str, Any]:
    BASE.mkdir(parents=True, exist_ok=True)
    if not RUNTIME_METADATA.is_file():
        raise FDSRuntimeUnavailable(f"FDS runtime provenance metadata is missing: {RUNTIME_METADATA}")
    runtime_metadata = json.loads(RUNTIME_METADATA.read_text(encoding="utf-8"))
    runtime = discover_fds_runtime()
    adapter = FDSSmokePropagationAdapter()
    records: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    if runtime["available"]:
        for label, action in (("door_closed", 0.0), ("door_open", 1.0)):
            for replicate in (1, 2):
                key = f"{label}_{replicate}"
                try:
                    adapter.reset(seed=0)
                    records[key] = adapter.run(action, label, replicate)
                except (FDSRuntimeUnavailable, OSError, ValueError, RuntimeError) as exc:
                    errors[key] = str(exc)
    arms = {
        key: {
            "action": record["action"],
            "replicate": record["replicate"],
            "trace_digest": record["trace_digest"],
        }
        for key, record in records.items()
    }
    completed = len(records) == 4
    traces_ok = completed and all(_finite_trace(record) for record in records.values())
    deterministic = completed and all(
        records[f"{label}_1"]["trace_digest"] == records[f"{label}_2"]["trace_digest"]
        for label in ("door_closed", "door_open")
    )
    closed_trace = records["door_closed_1"]["trace"] if completed else []
    open_trace = records["door_open_1"]["trace"] if completed else []
    same_horizon = completed and _valid_time_axis(closed_trace) and _valid_time_axis(open_trace) and math.isclose(
        closed_trace[-1]["time_s"], open_trace[-1]["time_s"], rel_tol=0.0, abs_tol=1e-6
    )
    # FDS chooses adaptive timesteps independently for each geometry.  The
    # traces therefore need interpolation onto a common time axis, not exact
    # row-wise timestamp equality.
    time_aligned = same_horizon
    action_observed = completed and {
        row["action_door_open_fraction"] for row in records["door_closed_1"]["trace"]
    } == {0.0} and {
        row["action_door_open_fraction"] for row in records["door_open_1"]["trace"]
    } == {1.0}
    deltas = {"room_b_temperature_c": 0.0, "room_b_visibility_m": 0.0, "room_b_velocity_mps": 0.0}
    if time_aligned:
        comparison_times = sorted({
            row["time_s"] for row in closed_trace + open_trace
        })
        deltas = {
            role: max(
                abs(_interpolate(closed_trace, time_s, role) - _interpolate(open_trace, time_s, role))
                for time_s in comparison_times
            )
            for role in deltas
        }
    action_sensitive = action_observed and (
        deltas["room_b_visibility_m"] > 1e-6
        or deltas["room_b_temperature_c"] > 1e-6
        or deltas["room_b_velocity_mps"] > 1e-6
    )
    provenance_ok = _common_backend_provenance(records)
    blockers = []
    if not runtime["available"]:
        blockers.append(runtime["blocker"] or "FDS_RUNTIME_MISSING")
    if errors:
        blockers.append("FDS_RUN_FAILED")
    if not completed:
        blockers.append("FDS_REPLAY_INCOMPLETE")
    if not traces_ok:
        blockers.append("FDS_DEVICE_TRACE_MISSING_OR_NONFINITE")
    if not action_observed:
        blockers.append("FDS_ACTION_NOT_OBSERVED")
    if not same_horizon:
        blockers.append("FDS_CONTRAST_HORIZON_MISMATCH")
    if not time_aligned:
        blockers.append("FDS_CONTRAST_TIME_MISALIGNMENT")
    if not action_sensitive:
        blockers.append("FDS_ACTION_INSENSITIVE_SMOKE_RESPONSE")
    if not deterministic:
        blockers.append("FDS_RESET_REPLAY_MISMATCH")
    if not provenance_ok:
        blockers.append("FDS_PROVENANCE_GATE_FAILED")
    for name, record in records.items():
        if name.endswith("_1") and record.get("trace"):
            save_trace(name.removesuffix("_1") + "_trace", record["trace"])
    return {
        "schema_version": "d2-fds-smoke-fire-door-backend-gate-v1",
        "physical_process_id": "fds:d2_smoke_fire_ventilation_door_propagation",
        "backend": "FDS",
        "backend_version": "runtime-pinned-at-probe-time",
        "status": "REAL_RUNTIME_PROBED" if not blockers else "EVIDENCE_PENDING",
        "passed": not blockers,
        "runtime_probe": runtime,
        "runtime_provenance": runtime_metadata,
        "runtime_stepping_gate": completed and traces_ok,
        "action_available_gate": action_observed,
        "action_sensitivity_gate": action_sensitive,
        "determinism_gate": deterministic,
        "time_alignment_gate": time_aligned,
        "provenance_gate": provenance_ok,
        "replay_arm_count": len(records),
        "replay_arms": arms,
        "errors": errors,
        "action_adapter": FDSSmokePropagationAdapter.action_spec,
        "observation_roles": {
            "room_b_temperature_c": "FDS DEVC ROOM_B_TEMPERATURE / TEMPERATURE",
            "room_b_visibility_m": "FDS DEVC ROOM_B_VISIBILITY / VISIBILITY (smoke proxy emitted by FDS)",
            "room_b_velocity_mps": "FDS DEVC ROOM_B_VELOCITY / VELOCITY",
        },
        "model_role": "minimal two-room FDS fire source with inert partition and generated door panel contrast",
        "model_template": {
            "path": str(MODEL_TEMPLATE.relative_to(ROOT)),
            "sha256": sha256(MODEL_TEMPLATE),
        },
        "official_fds_physics": True,
        "surrogate_fallback": False,
        "maximum_observed_deltas": deltas,
        "trajectory_digests": {key: record["trace_digest"] for key, record in records.items()},
        "causal_trace_refs": {
            name.removesuffix("_1"): {
                "path": str((BASE / f"{name.removesuffix('_1')}_trace.jsonl").relative_to(ROOT)),
                "row_count": len(record["trace"]),
                "sha256": sha256(BASE / f"{name.removesuffix('_1')}_trace.jsonl"),
            }
            for name, record in records.items()
            if name.endswith("_1") and record.get("trace")
        },
        "exclusion_reasons": blockers,
        "adapter_sha256": sha256(ROOT / "d2_fds_adapter.py"),
        "probe_sha256": sha256(Path(__file__).resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != content:
            raise SystemExit("stale D2 FDS backend gate report")
        return
    REPORT.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
