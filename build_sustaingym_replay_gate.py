#!/usr/bin/env python3
"""Build the fail-closed replay certificate for the pinned SustainGym adapter.

The recorded causal probe is necessary evidence, but it is not an executable
certificate.  This builder imports the real pinned backend, runs the bounded
off and cooling policies twice from the same initial state, and only emits a
verified verdict when both replays terminate, match exactly, and cooling has a
measurable effect.  Missing optional dependencies or any backend exception
produce a pending (``verified: false``) artifact instead of a claim.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
from typing import Any, Callable

import numpy as np

from unified_compiler.adapters.sustaingym_building import (
    DEFAULT_PROBE_RESULT,
    DEFAULT_PROBE_SCRIPT,
    DEFAULT_REPLAY_GATE,
    PINNED_COMMIT,
    PINNED_EPISODE_LEN,
    PINNED_SEED,
    PROBED_COOLING_MIN,
    SustainGymAdapterError,
    SustainGymBuildingAdapter,
)


ROOT = Path(__file__).resolve().parent
_DIGEST_TOLERANCE = 1e-9
PHYSICALLY_PLAUSIBLE_ZONE_TEMPERATURE_C = (0.0, 50.0)


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.parents[2]))
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_sha256(path: Path) -> str:
    try:
        return _sha256(path)
    except OSError:
        return ""


def _runtime_source_commit() -> str | None:
    """Resolve the source checkout commit; wheels without VCS provenance fail closed."""
    module = importlib.import_module("sustaingym")
    source = getattr(module, "__file__", None)
    if not source:
        return None
    for parent in (Path(source).resolve(), *Path(source).resolve().parents):
        git_marker = parent / ".git"
        if not git_marker.exists():
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(parent), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        commit = result.stdout.strip()
        return commit if commit else None
    return None


def _rollout(
    adapter: SustainGymBuildingAdapter,
    t_initial: tuple[float, ...],
    action_factory: Callable[[np.ndarray], np.ndarray],
) -> dict[str, Any]:
    """Run one complete public replay through the real adapter."""
    initial = adapter.reset(seed=PINNED_SEED, t_initial=t_initial)
    private = adapter.private_state()
    ac_map = np.asarray(private["ac_map"], dtype=np.float64)
    action = np.asarray(action_factory(ac_map), dtype=np.float64)
    if action.shape != ac_map.shape:
        raise SustainGymAdapterError("replay action factory returned the wrong shape")
    if not np.all(np.isfinite(action)):
        raise SustainGymAdapterError("replay action factory returned non-finite values")

    transitions: list[dict[str, Any]] = []
    temperatures: list[float] = [
        float(value) for value in initial["zone_temperatures_c"]
    ]
    for _ in range(PINNED_EPISODE_LEN):
        step = adapter.step(action.tolist())
        temperatures.extend(
            float(value)
            for value in step["observation"]["zone_temperatures_c"]
        )
        transitions.append({"action": action.tolist(), "transition": step})
    if not adapter.done or not transitions[-1]["transition"]["episode_done"]:
        raise SustainGymAdapterError("replay did not terminate at the pinned horizon")
    finite = bool(np.all(np.isfinite(temperatures)))
    lower, upper = PHYSICALLY_PLAUSIBLE_ZONE_TEMPERATURE_C
    plausible = finite and all(lower <= value <= upper for value in temperatures)
    return {
        "initial": initial,
        "transitions": transitions,
        "temperature_evidence": {
            "finite": finite,
            "physically_plausible": plausible,
            "bounds_c": [lower, upper],
            "minimum_c": min(temperatures, default=None),
            "maximum_c": max(temperatures, default=None),
            "out_of_bounds_count": sum(
                not lower <= value <= upper for value in temperatures
            ),
        },
        "trajectory_digest": canonical_digest(
            {"initial": initial, "transitions": transitions}
        ),
    }


def _max_temperature_delta(left: dict[str, Any], right: dict[str, Any]) -> float:
    a = [
        left["initial"]["zone_temperatures_c"],
        *[
            x["transition"]["observation"]["zone_temperatures_c"]
            for x in left["transitions"]
        ],
    ]
    b = [
        right["initial"]["zone_temperatures_c"],
        *[
            x["transition"]["observation"]["zone_temperatures_c"]
            for x in right["transitions"]
        ],
    ]
    if len(a) != len(b):
        raise SustainGymAdapterError("replay trajectories have different lengths")
    deltas = []
    for first, second in zip(a, b):
        ta = first
        tb = second
        if len(ta) != len(tb):
            raise SustainGymAdapterError("replay trajectories have different zone counts")
        deltas.extend(abs(float(x) - float(y)) for x, y in zip(ta, tb))
    return max(deltas, default=0.0)


def _base_gate(adapter: SustainGymBuildingAdapter) -> dict[str, Any]:
    return {
        "schema_version": "sustaingym-replay-gate-v1",
        "backend": {
            "name": "SustainGym BuildingEnv",
            "commit": PINNED_COMMIT,
            "probe_result": _relative(DEFAULT_PROBE_RESULT),
            "probe_script": _relative(DEFAULT_PROBE_SCRIPT),
        },
        "backend_runtime_commit": None,
        "adapter_sha256": adapter.adapter_sha256,
        "probe_result_sha256": _safe_sha256(adapter.probe_result_path),
        "probe_script_sha256": _safe_sha256(adapter.probe_script_path),
        "pinned": adapter.pinned_config(),
        "probe_evidence_valid": False,
        "backend_importable": False,
        "all_replays_completed": False,
        "all_replays_deterministic": False,
        "action_sensitive": False,
        "all_temperatures_finite": False,
        "all_temperatures_physically_plausible": False,
        "verified": False,
        "status": "DATA_PROBED_PENDING_REPLAY",
        "failure_reasons": [],
        "replays": {},
        "gold_actions_released": False,
    }


def build_gate(
    probe_result_path: Path | str | None = None,
    probe_script_path: Path | str | None = None,
) -> dict[str, Any]:
    """Return a replay gate; every failed check is represented as unverified."""
    adapter = SustainGymBuildingAdapter(probe_result_path=probe_result_path)
    if probe_script_path is not None:
        adapter.probe_script_path = Path(probe_script_path)
    gate = _base_gate(adapter)
    try:
        adapter._ensure_probe()  # evidence validation is intentionally fail-closed
        gate["probe_evidence_valid"] = True
        adapter._import_backend()  # import check is separate from the rollouts
        runtime_commit = _runtime_source_commit()
        gate["backend_runtime_commit"] = runtime_commit
        if runtime_commit != PINNED_COMMIT:
            raise SustainGymAdapterError(
                "backend source commit cannot be verified as the pinned commit"
            )
        gate["backend_importable"] = True

        # The pinned probe used target + 3 C as its initial state.  Read the
        # target from the real backend through the adapter, then replay only
        # with the explicit bounded state; no initial-state vector is invented.
        adapter.reset(seed=PINNED_SEED)
        target = tuple(float(v) + 3.0 for v in adapter.private_state()["target_setpoints_c"])
        off_factory = lambda ac_map: np.zeros_like(ac_map)
        cool_factory = lambda ac_map: np.where(ac_map == 0.0, 0.0, PROBED_COOLING_MIN)
        off_a = _rollout(SustainGymBuildingAdapter(probe_result_path=adapter.probe_result_path), target, off_factory)
        off_b = _rollout(SustainGymBuildingAdapter(probe_result_path=adapter.probe_result_path), target, off_factory)
        cool_a = _rollout(SustainGymBuildingAdapter(probe_result_path=adapter.probe_result_path), target, cool_factory)
        cool_b = _rollout(SustainGymBuildingAdapter(probe_result_path=adapter.probe_result_path), target, cool_factory)

        deterministic = off_a == off_b and cool_a == cool_b
        completed = all(
            len(item["transitions"]) == PINNED_EPISODE_LEN
            for item in (off_a, off_b, cool_a, cool_b)
        )
        sensitivity = _max_temperature_delta(off_a, cool_a)
        all_temperature_evidence = [
            item["temperature_evidence"]
            for item in (off_a, off_b, cool_a, cool_b)
        ]
        temperatures_finite = all(item["finite"] for item in all_temperature_evidence)
        temperatures_plausible = all(
            item["physically_plausible"] for item in all_temperature_evidence
        )
        gate.update(
            {
                "all_replays_completed": completed,
                "all_replays_deterministic": deterministic,
                "action_sensitive": sensitivity > _DIGEST_TOLERANCE,
                "all_temperatures_finite": temperatures_finite,
                "all_temperatures_physically_plausible": temperatures_plausible,
                "maximum_zone_temperature_delta_c": sensitivity,
                "temperature_bounds_c": list(PHYSICALLY_PLAUSIBLE_ZONE_TEMPERATURE_C),
                "initialization": {
                    "policy": "target_setpoints_c + 3.0 (pinned probe policy)",
                    "zone_count": len(target),
                    "initial_state_digest": canonical_digest(list(target)),
                },
                "replays": {
                    "off_a": {
                        "trajectory_digest": off_a["trajectory_digest"],
                        "temperature_evidence": off_a["temperature_evidence"],
                    },
                    "off_b": {
                        "trajectory_digest": off_b["trajectory_digest"],
                        "temperature_evidence": off_b["temperature_evidence"],
                    },
                    "cooling_a": {
                        "trajectory_digest": cool_a["trajectory_digest"],
                        "temperature_evidence": cool_a["temperature_evidence"],
                    },
                    "cooling_b": {
                        "trajectory_digest": cool_b["trajectory_digest"],
                        "temperature_evidence": cool_b["temperature_evidence"],
                    },
                },
            }
        )
        if not deterministic:
            gate["failure_reasons"].append("NONDETERMINISTIC_REPLAY")
        if not completed:
            gate["failure_reasons"].append("INCOMPLETE_REPLAY")
        if sensitivity <= _DIGEST_TOLERANCE:
            gate["failure_reasons"].append("ACTION_INSENSITIVE")
        if not temperatures_finite:
            gate["failure_reasons"].append("NONFINITE_TEMPERATURE")
        if not temperatures_plausible:
            gate["failure_reasons"].append("PHYSICALLY_IMPLAUSIBLE_TEMPERATURE")
    except ImportError:
        gate["failure_reasons"].append("BACKEND_UNAVAILABLE")
    except (OSError, SustainGymAdapterError, KeyError, TypeError, ValueError) as exc:
        gate["failure_reasons"].append(
            (
                "UNPINNED_BACKEND_RUNTIME"
                if "commit" in str(exc).lower()
                else "BACKEND_UNAVAILABLE"
                if not gate["backend_importable"]
                else "REPLAY_GATE_ERROR"
            )
        )
        gate["error_type"] = type(exc).__name__

    gate["verified"] = all(
        gate[key] is True
        for key in (
            "probe_evidence_valid",
            "backend_importable",
            "all_replays_completed",
            "all_replays_deterministic",
            "action_sensitive",
            "all_temperatures_finite",
            "all_temperatures_physically_plausible",
        )
    )
    gate["status"] = (
        "EXECUTABLE_REPLAY_VERIFIED"
        if gate["verified"]
        else "DATA_PROBED_PENDING_REPLAY"
    )
    gate["failure_reasons"] = sorted(set(gate["failure_reasons"]))
    return gate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the generated artifact is stale")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPLAY_GATE)
    args = parser.parse_args()
    content = json.dumps(build_gate(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != content:
            raise SystemExit(f"stale SustainGym replay gate: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
