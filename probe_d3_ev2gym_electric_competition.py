#!/usr/bin/env python3
"""Probe the D3 EV2Gym native shared-transformer route, fail closed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from d3_ev2gym_electric_competition_adapter import (
    ADAPTER_ID,
    EV2GymElectricCompetition,
    EV2GymCompetitionActionError,
    EV2GymCompetitionError,
    SCHEMA_VERSION,
    TICK_SECONDS,
    runtime_provenance,
)

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "generated" / "d3_ev2gym_electric_competition_evidence.json"


def _prefix_to_two_connected(route: EV2GymElectricCompetition, seed: int) -> int:
    """Advance a native episode with WAIT until both native ports are live."""
    route.reset(seed)
    for _ in range(route.horizon_steps - 1):
        receipt = route.step({"charger_0_rate": 0.0, "charger_1_rate": 0.0}, TICK_SECONDS)
        if all(port["connected"] for port in receipt["observation"]["ports"]):
            return route._steps
    raise EV2GymCompetitionError("seed did not produce two simultaneous native EVs")


def _run_from_prefix(seed: int, prefix_steps: int, action: dict[str, float]) -> dict[str, Any]:
    route = EV2GymElectricCompetition(horizon_steps=96, seed=seed)
    try:
        route.reset(seed)
        for _ in range(prefix_steps):
            route.step({"charger_0_rate": 0.0, "charger_1_rate": 0.0}, TICK_SECONDS)
        return route.step(action, TICK_SECONDS)
    finally:
        route.close()


def probe(seed: int = 3, horizon_steps: int = 96) -> dict[str, Any]:
    checks = {
        "native_reset": False,
        "two_competing_channels": False,
        "deterministic_reset": False,
        "time_monotone": False,
        "shared_transformer_capacity_observable": False,
        "bidirectional_intervention": False,
        "illegal_action_no_advance": False,
        "invalid_dt_no_advance": False,
        "native_runtime_provenance": False,
    }
    details: dict[str, Any] = {"provenance": runtime_provenance()}
    route: EV2GymElectricCompetition | None = None
    try:
        route = EV2GymElectricCompetition(horizon_steps=horizon_steps, seed=seed)
        initial = route.reset(seed)
        checks["native_reset"] = initial["runtime"] == "official_ev2gym_native"
        schema = route.legal_actions()
        checks["two_competing_channels"] = (
            set(schema["channels"]) == {"charger_0_rate", "charger_1_rate"}
            and schema["channels"]["charger_0_rate"]["index"] == 0
            and schema["channels"]["charger_1_rate"]["index"] == 1
        )
        first = route.reset(seed)
        second = route.reset(seed)
        checks["deterministic_reset"] = first == second
        before = route.observe()
        transition_a = route.step({"charger_0_rate": 1.0, "charger_1_rate": 0.0}, TICK_SECONDS)
        transition_b = route.step({"charger_0_rate": 0.0, "charger_1_rate": 1.0}, TICK_SECONDS)
        checks["time_monotone"] = transition_a["time_seconds"] > before["time_seconds"] and transition_b["time_seconds"] > transition_a["time_seconds"]
        checks["shared_transformer_capacity_observable"] = all(
            key in transition_a["effect"] for key in ("remaining_capacity_kw", "overload_kw", "transformer_power_kw")
        )
        # Native transformer power is the sum of the two port outputs.  The
        # comparison is meaningful even when a generated seed has no EV yet;
        # in that case the result is a valid native zero-load witness, not a
        # fabricated positive coupling claim.
        prefix_steps = _prefix_to_two_connected(route, seed)
        a = _run_from_prefix(seed, prefix_steps, {"charger_0_rate": 1.0, "charger_1_rate": 0.0})
        b = _run_from_prefix(seed, prefix_steps, {"charger_0_rate": 0.0, "charger_1_rate": 1.0})
        both = _run_from_prefix(seed, prefix_steps, {"charger_0_rate": 1.0, "charger_1_rate": 1.0})
        pa = a["effect"]["transformer_power_kw"]
        pb = b["effect"]["transformer_power_kw"]
        p2 = both["effect"]["transformer_power_kw"]
        checks["bidirectional_intervention"] = bool(
            p2 > pa + 1e-9 and p2 > pb + 1e-9
            and both["effect"]["overload_kw"] > a["effect"]["overload_kw"] + 1e-9
            and both["effect"]["overload_kw"] > b["effect"]["overload_kw"] + 1e-9
        )
        route.reset(seed)
        snapshot = (route.observe(), route.env.current_step, route._steps)
        try:
            route.step({"charger_0_rate": 2.0, "charger_1_rate": 0.0}, TICK_SECONDS)
        except EV2GymCompetitionActionError:
            pass
        checks["illegal_action_no_advance"] = snapshot == (route.observe(), route.env.current_step, route._steps)
        try:
            route.step({"charger_0_rate": 0.0, "charger_1_rate": 0.0}, 60.0)
        except EV2GymCompetitionActionError:
            pass
        checks["invalid_dt_no_advance"] = snapshot == (route.observe(), route.env.current_step, route._steps)
        checks["native_runtime_provenance"] = bool(details["provenance"]["native_checkout_sha256"])
        details.update({"initial": initial, "legal_actions": schema, "power_witness": {"prefix_steps": prefix_steps, "port_0_only_kw": pa, "port_1_only_kw": pb, "both_kw": p2, "both_overload_kw": both["effect"]["overload_kw"]}})
    except Exception as exc:
        details["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if route is not None:
            route.close()
    passed = all(checks.values())
    status = "BACKEND_REPLAY_VERIFIED" if passed else "EVIDENCE_PENDING"
    return {
        "schema_version": SCHEMA_VERSION,
        "adapter_id": ADAPTER_ID,
        "status": status,
        "verified": passed,
        "checks": checks,
        "details": details,
        "native_contract": {
            "interface": "reset(seed) -> observe() -> legal_actions() -> step(action, dt_seconds) -> observation",
            "tick_seconds": TICK_SECONDS,
            "two_channels": ["charger_0_rate", "charger_1_rate"],
            "coupling": "official EV2Gym Transformer sums native charger power on one shared transformer",
            "penalty_free": True,
        },
        "provenance": runtime_provenance(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--horizon-steps", type=int, default=96)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = json.dumps(probe(args.seed, args.horizon_steps), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"stale D3 EV2Gym evidence: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    part = args.output.with_name(args.output.name + ".part")
    part.write_text(payload, encoding="utf-8")
    part.replace(args.output)
    print(json.dumps({"status": json.loads(payload)["status"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
