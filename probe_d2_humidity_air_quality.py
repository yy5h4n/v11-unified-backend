#!/usr/bin/env python3
"""Build D2 humidity/air-quality backend replay evidence and fail-closed gates.

The probe is deliberately backend-only. It verifies that the pinned
EnergyPlus runtime accepts the ventilation callback action, emits finite IAQ
and humidity observations, and produces deterministic, action-sensitive
replays. It does not assign responsibility, construct episodes, or evaluate
thresholds.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from d2_humidity_air_quality_adapter import EnergyPlusHumidityAirQualityAdapter, sha256

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "generated/d2_humidity_air_quality_v1"
REPORT = BASE / "gate_report.json"
CITYLEARN_MANIFEST = ROOT / "shared_assets/citylearn_v2.5.0/asset_manifest.json"
SUSTAINGYM_BUILDING_ENV = ROOT / "shared_runtime/sustaingym-site-packages/sustaingym/envs/building/env.py"


def save_trace(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = BASE / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": sha256(path)}


def provenance_gate(records: dict[str, dict[str, Any]]) -> bool:
    """Require every replay to identify the same pinned real backend assets."""
    eplus = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64"
    expected = {
        "backend": "EnergyPlus",
        "backend_version": "26.1.0",
        "runtime_sha256": sha256(eplus / "energyplus"),
        "source_model_sha256": sha256(eplus / "ExampleFiles/HeatPumpIAQP_GenericContamControl.idf"),
        "weather_sha256": sha256(eplus / "WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw"),
    }
    return bool(records) and all(
        all(record.get("provenance", {}).get(key) == value for key, value in expected.items())
        and record.get("provenance", {}).get("reset_semantics")
        == "new EnergyPlusAPI state for every run; source model and weather are immutable"
        for record in records.values()
    )


def build() -> dict[str, Any]:
    adapter = EnergyPlusHumidityAirQualityAdapter()
    records: dict[str, dict[str, Any]] = {}
    for label, action in (("ventilation_off", 0.0), ("ventilation_on", 1.0)):
        for replicate in (1, 2):
            adapter.reset(seed=0)
            records[f"{label}_{replicate}"] = adapter.run(action, label, replicate)
    adapter.close()

    off, on = records["ventilation_off_1"], records["ventilation_on_1"]
    same_horizon = len(off["trace"]) == len(on["trace"]) and len(off["trace"]) > 0
    time_aligned = same_horizon and all(
        left["time_key"] == right["time_key"] for left, right in zip(off["trace"], on["trace"])
    )
    deterministic = all(
        records[f"{label}_1"]["trace_digest"] == records[f"{label}_2"]["trace_digest"]
        for label in ("ventilation_off", "ventilation_on")
    )
    pairs = list(zip(off["trace"], on["trace"]))
    deltas = {
        role: max(abs(left["observation"][role] - right["observation"][role]) for left, right in pairs)
        if pairs
        else 0.0
        for role in ("co2_ppm", "generic_contaminant_ppm", "relative_humidity_pct", "zone_temperature_c")
    }
    finite = all(
        math.isfinite(value)
        for record in records.values()
        for row in record["trace"]
        for value in row["observation"].values()
    )
    action_available = {row["action_ventilation_schedule"] for row in off["trace"]} == {0.0} and {
        row["action_ventilation_schedule"] for row in on["trace"]
    } == {1.0}
    action_sensitive = (
        deltas["co2_ppm"] > 1e-6
        or deltas["generic_contaminant_ppm"] > 1e-6
        or deltas["relative_humidity_pct"] > 1e-6
    )
    provenance_ok = provenance_gate(records)

    BASE.mkdir(parents=True, exist_ok=True)
    traces = {
        "ventilation_off": save_trace("ventilation_off_trace", off["trace"]),
        "ventilation_on": save_trace("ventilation_on_trace", on["trace"]),
    }
    blockers = []
    if not same_horizon:
        blockers.append("CAUSAL_CONTRAST_HORIZON_MISMATCH")
    if not time_aligned:
        blockers.append("CAUSAL_CONTRAST_TIME_MISALIGNMENT")
    if not deterministic:
        blockers.append("CROSS_RUN_RESET_REPLAY_MISMATCH")
    if not finite:
        blockers.append("NONFINITE_OBSERVATION")
    if not action_available:
        blockers.append("ACTION_NOT_OBSERVED")
    if not action_sensitive:
        blockers.append("ACTION_INSENSITIVE_IAQ_RESPONSE")
    if not provenance_ok:
        blockers.append("PROVENANCE_GATE_FAILED")

    return {
        "schema_version": "d2-humidity-air-quality-energyplus-backend-gate-v2",
        "physical_process_id": "energyplus:d2_humidity_air_quality_ventilation",
        "backend": "EnergyPlus",
        "backend_version": "26.1.0",
        "status": "REAL_RUNTIME_PROBED" if not blockers else "FAIL_CLOSED",
        "passed": not blockers,
        "runtime_stepping_gate": same_horizon and finite,
        "action_available_gate": action_available,
        "action_sensitivity_gate": action_sensitive,
        "determinism_gate": deterministic,
        "time_alignment_gate": time_aligned,
        "provenance_gate": provenance_ok,
        "replay_arm_count": len(records),
        "replay_arms": {
            name: {"action": record["action"], "replicate": record["replicate"], "trace_digest": record["trace_digest"]}
            for name, record in records.items()
        },
        "official_example_modeled_data": True,
        "household_measured_data": False,
        "source_model_role": "official EnergyPlus HeatPumpIAQP_GenericContamControl example; design-day stepping only (immutable source disables weather-file run period); Zone Air CO2 and Generic Air Contaminant plus Zone RH",
        "alternative_backend_audit": {
            "citylearn_v2.5.0": {
                "asset_manifest_path": str(CITYLEARN_MANIFEST.relative_to(ROOT)),
                "asset_manifest_sha256": sha256(CITYLEARN_MANIFEST),
                "available_without_docker": True,
                "observed_assets": ["battery_choices.yaml", "lbl-tracking_the_sun-res-pv.csv", "weather.epw"],
                "iaq_observables": False,
                "action_stepping_for_iaq": False,
                "gap": "bundled assets are battery/PV/weather only; CityLearn package/runtime is not installed in the base Python path",
            },
            "sustaingym_0.1.7": {
                "building_module_path": str(SUSTAINGYM_BUILDING_ENV.relative_to(ROOT)),
                "building_module_sha256": sha256(SUSTAINGYM_BUILDING_ENV),
                "available_without_docker": True,
                "observed_capability": "RC thermal BuildingEnv with HVAC power action and temperature/weather/occupancy observations",
                "iaq_observables": False,
                "action_stepping_for_iaq": False,
                "gap": "building observations expose temperature and weather but no humidity, CO2, or generic-contaminant state",
            },
        },
        "chosen_runtime_route": "EnergyPlus 26.1 pyenergyplus API; direct callback stepping against official IAQP generic-contaminant example",
        "agent_closed_loop": {
            "interface": ["reset(seed)->observation", "observe()->observation", "step(action, delta_t|steps)->transition"],
            "runtime_mechanism": "persistent EnergyPlusAPI state in a worker thread; callbacks rendezvous at every end-of-zone-timestep reporting boundary",
            "physical_step_seconds": 600,
            "mid_run_action_change": True,
            "prefix_rerun_per_step": False,
            "termination": "pinned two-design-day horizon (288 observed zone timesteps)",
            "limitations": ["pinned official design-day model", "delta_t must be a positive multiple of 600 seconds"],
        },
        "action_adapter": off["provenance"]["action_actuator"],
        "observation_roles": {
            "co2_ppm": "Zone Air CO2 Concentration / NORTH ZONE",
            "generic_contaminant_ppm": "Zone Air Generic Air Contaminant Concentration / NORTH ZONE",
            "relative_humidity_pct": "Zone Air Relative Humidity / NORTH ZONE",
            "zone_temperature_c": "Zone Mean Air Temperature / NORTH ZONE",
        },
        "row_count": len(off["trace"]),
        "maximum_observed_deltas": deltas,
        "trajectory_digests": {name: record["trace_digest"] for name, record in records.items()},
        "causal_trace_refs": traces,
        "exclusion_reasons": blockers,
        "adapter_sha256": sha256(ROOT / "d2_humidity_air_quality_adapter.py"),
        "probe_sha256": sha256(Path(__file__).resolve()),
        "provenance": off["provenance"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != content:
            raise SystemExit("stale D2 humidity/air-quality backend gate report")
        return
    BASE.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
