#!/usr/bin/env python3
"""Probe the live D3 CityLearn Agent boundary.

This probe is intentionally independent from ``probe_d3_citylearn_coupling``:
it drives the public ``make_agent_backend`` interface and records online
conformance evidence for one persistent native CityLearn environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from unified_compiler.agent_interface import AgentActionError, make_agent_backend

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "generated" / "d3_agent_interface_v1.json"
SCHEMA = "d3-agent-interface-v1"
ROUTE_ID = "d3_citylearn_coupling"
TICK_SECONDS = 3600.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _changed(left: Any, right: Any, *keys: str) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return left != right
    return all(abs(float(left[key]) - float(right[key])) > 1e-9 for key in keys)


def _snapshot(backend: Any) -> tuple[int, int, dict[str, Any]]:
    native = backend.route
    return native.env.time_step, native._steps, backend.observe()


def _effect(transition: dict[str, Any]) -> dict[str, Any]:
    """Read the native causal effect from the common receipt envelope."""
    info = transition.get("info", {})
    effect = info.get("effect") if isinstance(info, dict) else None
    if not isinstance(effect, dict):
        raise RuntimeError("D3 transition receipt is missing info.effect")
    return effect


def probe(*, horizon: int = 2, seed: int = 23) -> dict[str, Any]:
    checks = {
        "initial_receipt": False,
        "legal_actions": False,
        "deterministic_reset": False,
        "time_monotone": False,
        "observe_is_latest": False,
        "same_prefix_mid_trajectory_switch": False,
        "hvac_counterfactual_thermal_and_net": False,
        "battery_counterfactual_soc_and_net": False,
        "illegal_action_no_advance": False,
        "invalid_dt_no_advance": False,
        "same_native_env_instance": False,
        "termination": False,
    }
    backend: Any | None = None
    details: dict[str, Any] = {}
    illegal_error: str | None = None
    dt_error: str | None = None
    try:
        backend = make_agent_backend(ROUTE_ID, horizon=horizon)
        initial = backend.reset(seed=seed)
        legal = backend.legal_actions()
        checks["initial_receipt"] = (
            initial.get("time_seconds") == 0.0
            and initial.get("action") is None
            and isinstance(initial.get("observation"), dict)
        )
        checks["legal_actions"] = (
            legal.get("native") is True
            and legal.get("public_action_names") == ["battery_rate", "hvac_rate"]
            and legal.get("native_action_names") == [
                "electrical_storage",
                "cooling_or_heating_device",
            ]
            and set(legal.get("channels", {})) == {"battery_rate", "hvac_rate"}
        )
        first = backend.reset(seed=seed)
        second_reset = backend.reset(seed=seed)
        checks["deterministic_reset"] = first == second_reset

        env_identity = id(backend.route.env)
        zero = {"battery_rate": 0.0, "hvac_rate": 0.0}
        switched_action = {"battery_rate": 1.0, "hvac_rate": 1.0}
        first_transition = backend.step(zero, TICK_SECONDS)
        second_transition = backend.step(switched_action, TICK_SECONDS)
        checks["time_monotone"] = (
            first_transition["time_seconds"] > first["time_seconds"]
            and second_transition["time_seconds"] > first_transition["time_seconds"]
        )
        checks["observe_is_latest"] = backend.observe() == second_transition["observation"]
        checks["same_native_env_instance"] = id(backend.route.env) == env_identity
        checks["termination"] = bool(second_transition["done"] and second_transition["terminated"])
        details["termination_receipt"] = {
            "done": second_transition["done"],
            "terminated": second_transition["terminated"],
            "truncated": second_transition["truncated"],
        }

        # Identical first action, different second action: both trajectories
        # are driven through the same public interface from the same seed.
        backend.reset(seed=seed)
        backend.step(zero, TICK_SECONDS)
        changed = backend.step(switched_action, TICK_SECONDS)
        backend.reset(seed=seed)
        backend.step(zero, TICK_SECONDS)
        unchanged = backend.step(zero, TICK_SECONDS)
        checks["same_prefix_mid_trajectory_switch"] = (
            _effect(changed) != _effect(unchanged)
        )

        # HVAC and battery counterfactuals are independent same-seed runs.
        backend.reset(seed=seed)
        hvac_base = backend.step({"battery_rate": 0.0, "hvac_rate": 0.0}, TICK_SECONDS)
        backend.reset(seed=seed)
        hvac_alt = backend.step({"battery_rate": 0.0, "hvac_rate": 1.0}, TICK_SECONDS)
        checks["hvac_counterfactual_thermal_and_net"] = _changed(
            _effect(hvac_base), _effect(hvac_alt), "indoor_temperature_c", "net_electricity_kwh"
        )

        backend.reset(seed=seed)
        battery_base = backend.step({"battery_rate": 0.0, "hvac_rate": 0.0}, TICK_SECONDS)
        backend.reset(seed=seed)
        battery_alt = backend.step({"battery_rate": 1.0, "hvac_rate": 0.0}, TICK_SECONDS)
        checks["battery_counterfactual_soc_and_net"] = _changed(
            _effect(battery_base), _effect(battery_alt), "battery_soc", "net_electricity_kwh"
        )

        # Invalid action and invalid dt are checked against the native time
        # step and public observation, proving neither rejection mutates state.
        backend.reset(seed=seed)
        before = _snapshot(backend)
        try:
            backend.step({"battery_rate": 0.0}, TICK_SECONDS)
        except AgentActionError as exc:
            illegal_error = type(exc).__name__
        after = _snapshot(backend)
        checks["illegal_action_no_advance"] = illegal_error is not None and after == before

        before = _snapshot(backend)
        try:
            backend.step(zero, 1800.0)
        except AgentActionError as exc:
            dt_error = type(exc).__name__
        after = _snapshot(backend)
        checks["invalid_dt_no_advance"] = dt_error is not None and after == before

        details.update(
            {
                "initial_observation_keys": sorted(initial["observation"]),
                "legal_actions": legal,
                "time_seconds": [first_transition["time_seconds"], second_transition["time_seconds"]],
                # Object ids are process-local and would make --check
                # nondeterministic; the boolean conformance check above is
                # the durable evidence that the same object survived steps.
                "native_env_identity": "same_object_within_episode",
                "illegal_action_error_type": illegal_error,
                "invalid_dt_error_type": dt_error,
                "hvac_effect_delta": {
                    "indoor_temperature_c": abs(
                        _effect(hvac_base)["indoor_temperature_c"] - _effect(hvac_alt)["indoor_temperature_c"]
                    ),
                    "net_electricity_kwh": abs(
                        _effect(hvac_base)["net_electricity_kwh"] - _effect(hvac_alt)["net_electricity_kwh"]
                    ),
                },
                "battery_effect_delta": {
                    "battery_soc": abs(_effect(battery_base)["battery_soc"] - _effect(battery_alt)["battery_soc"]),
                    "net_electricity_kwh": abs(
                        _effect(battery_base)["net_electricity_kwh"] - _effect(battery_alt)["net_electricity_kwh"]
                    ),
                },
            }
        )
        passed = all(checks.values())
    except Exception as exc:
        details["error"] = f"{type(exc).__name__}: {exc}"
        passed = False
    finally:
        if backend is not None:
            backend.close()

    route_path = ROOT / "d3_citylearn_coupling_adapter.py"
    interface_path = ROOT / "unified_compiler" / "agent_interface.py"
    return {
        "schema_version": SCHEMA,
        "scope": "D3 live Agent interface conformance only",
        "route_id": ROUTE_ID,
        "verified": passed,
        "passed": passed,
        "checks": checks,
        "details": details,
        "agent_closed_loop": {
            "interface": [
                "reset(seed)->receipt",
                "observe()->observation",
                "legal_actions()->schema",
                "step(action, dt_seconds)->transition",
            ],
            "dt_seconds": TICK_SECONDS,
            "native_env_per_reset": True,
            "online_step": True,
        },
        "probe_path": "probe_d3_agent_interface.py",
        "probe_sha256": sha256(Path(__file__)),
        "route_path": "d3_citylearn_coupling_adapter.py",
        "route_sha256": sha256(route_path),
        "route_adapter_path": "d3_citylearn_coupling_adapter.py",
        "route_adapter_sha256": sha256(route_path),
        "public_interface_path": "unified_compiler/agent_interface.py",
        "public_interface_sha256": sha256(interface_path),
        "common_adapter_path": "unified_compiler/agent_interface.py",
        "common_adapter_sha256": sha256(interface_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=2)
    parser.add_argument("--seed", type=int, default=23)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = json.dumps(probe(horizon=args.horizon, seed=args.seed), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"stale D3 Agent interface evidence: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"passed": json.loads(payload)["passed"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
