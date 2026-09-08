#!/usr/bin/env python3
"""Verify a real EnergyPlus thermostat intervention by causal contrast."""
from __future__ import annotations

import argparse, csv, hashlib, json, re, shutil, subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
EPLUS = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64"
EXE = EPLUS / "energyplus"
SOURCE = EPLUS / "ExampleFiles/5ZoneAirCooledWithSpacesDaylightingIntMass.idf"
WEATHER = EPLUS / "WeatherData/USA_VA_Sterling-Washington.Dulles.Intl.AP.724030_TMY3.epw"
BASE = ROOT / "generated/energyplus_thermal_action_v1"
REPORT = BASE / "gate_report.json"
VARIABLES = {
    "zone_temperature_c": "Zone Mean Air Temperature",
    "heating_setpoint_c": "Zone Thermostat Heating Setpoint Temperature",
    "heating_rate_w": "Zone Air System Sensible Heating Rate",
}


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_text(delta_c: float) -> tuple[str, dict[str, Any]]:
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r"(Schedule:Compact,\s*\n\s*Htg-SetP-Sch,.*?)(?=\n\s*Schedule:Compact,)", re.I | re.S)
    match = pattern.search(text)
    if not match:
        raise RuntimeError("Htg-SetP-Sch block not found")
    original = match.group(1)
    values = [float(value) for value in re.findall(r"Until:\s*[^,]+,\s*([-+]?\d+(?:\.\d+)?)", original, re.I)]
    if sorted(set(values)) != [16.7, 22.2]:
        raise RuntimeError(f"unexpected heating schedule values: {values}")
    changed = re.sub(
        r"(Until:\s*[^,]+,\s*)(16\.7|22\.2)(\s*[,;])",
        lambda m: m.group(1) + f"{float(m.group(2)) + delta_c:.1f}" + m.group(3),
        original,
        flags=re.I,
    )
    if delta_c and changed == original:
        raise RuntimeError("thermostat intervention made no model change")
    prepared = text[:match.start()] + changed + text[match.end():]
    prepared += "\n! Causal probe outputs.\n" + "".join(f"Output:Variable,ZONE 1,{name},Timestep;\n" for name in VARIABLES.values())
    return prepared, {
        "actuator_semantics": "raise Htg-SetP-Sch heating setpoint schedule",
        "delta_c": delta_c,
        "original_values_c": sorted(set(values)),
        "intervened_values_c": sorted({value + delta_c for value in values}),
        "scope": "shared thermostat schedule used by Zone 1",
    }


def select(headers: list[str], variable: str) -> str:
    candidates = [h for h in headers if h.upper().startswith("ZONE 1:") and variable.lower() in h.lower() and "(timestep)" in h.lower()]
    if len(candidates) != 1:
        raise RuntimeError(f"expected one Zone 1 timestep column for {variable}, got {candidates}")
    return candidates[0]


def run_variant(name: str, delta_c: float, replicate: int) -> dict[str, Any]:
    destination = BASE / f"{name}_{replicate}"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    text, mutation = model_text(delta_c)
    model = destination / "model.idf"
    model.write_text(text, encoding="utf-8")
    completed = subprocess.run([str(EXE), "-r", "-w", str(WEATHER), "-d", str(destination), str(model)], capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(f"EnergyPlus failed for {name}_{replicate}: {completed.stderr[-1000:]}")
    with (destination / "eplusout.csv").open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    headers = list(rows[0])
    columns = {role: select(headers, variable) for role, variable in VARIABLES.items()}
    trace = [{"date_time": row["Date/Time"].strip(), **{role: float(row[column]) for role, column in columns.items()}} for row in rows]
    return {"name": name, "replicate": replicate, "model_sha256": sha(model), "mutation": mutation, "row_count": len(trace), "trace_digest": digest(trace), "trace": trace}


def build() -> dict[str, Any]:
    if not all(path.is_file() for path in (EXE, SOURCE, WEATHER)):
        raise RuntimeError("missing pinned EnergyPlus runtime inputs")
    baseline_a, baseline_b = run_variant("baseline", 0.0, 1), run_variant("baseline", 0.0, 2)
    # +1 C stays below the model's 23.9 C occupied cooling setpoint, preserving
    # the dual-setpoint deadband while remaining a measurable intervention.
    intervention_a, intervention_b = run_variant("raise_setpoint_1c", 1.0, 1), run_variant("raise_setpoint_1c", 1.0, 2)
    deterministic = baseline_a["trace_digest"] == baseline_b["trace_digest"] and intervention_a["trace_digest"] == intervention_b["trace_digest"]
    if not (baseline_a["row_count"] == intervention_a["row_count"] > 0):
        raise RuntimeError("causal contrast horizons do not match")
    pairs = list(zip(baseline_a["trace"], intervention_a["trace"]))
    max_temperature_delta = max(abs(left["zone_temperature_c"] - right["zone_temperature_c"]) for left, right in pairs)
    max_setpoint_delta = max(abs(left["heating_setpoint_c"] - right["heating_setpoint_c"]) for left, right in pairs)
    max_heating_rate_delta = max(abs(left["heating_rate_w"] - right["heating_rate_w"]) for left, right in pairs)
    action_available = intervention_a["model_sha256"] != baseline_a["model_sha256"] and max_setpoint_delta >= 0.99
    action_sensitive = max_temperature_delta > 1e-6 and max_heating_rate_delta > 1e-6
    passed = deterministic and action_available and action_sensitive
    reasons = []
    if not deterministic: reasons.append("NONDETERMINISTIC_CROSS_RUN_REPLAY")
    if not action_available: reasons.append("ACTION_MUTATION_NOT_OBSERVED")
    if not action_sensitive: reasons.append("ACTION_INSENSITIVE_PHYSICAL_RESPONSE")
    return {
        "schema_version": "energyplus-thermal-action-gate-v1", "backend": "EnergyPlus", "backend_version": "26.1.0",
        "physical_process_id": "energyplus:thermal_occupancy_energy", "passed": passed,
        "action_available_gate": action_available, "action_sensitivity_gate": action_sensitive, "determinism_gate": deterministic,
        "evaluator_gate": False, "episode_eligible": False, "episode_count": 0, "exclusion_reasons": reasons + ["RESPONSIBILITY_EVALUATOR_NOT_YET_BOUND"],
        "intervention": intervention_a["mutation"], "maximum_zone_temperature_delta_c": max_temperature_delta,
        "maximum_heating_setpoint_delta_c": max_setpoint_delta, "maximum_heating_rate_delta_w": max_heating_rate_delta,
        "row_count": baseline_a["row_count"], "trajectory_digests": {
            "baseline_replicate_1": baseline_a["trace_digest"], "baseline_replicate_2": baseline_b["trace_digest"],
            "intervention_replicate_1": intervention_a["trace_digest"], "intervention_replicate_2": intervention_b["trace_digest"],
        },
        "runtime_sha256": sha(EXE), "source_model_sha256": sha(SOURCE), "weather_sha256": sha(WEATHER),
        "gold_actions_released": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != content:
            raise SystemExit(f"stale thermal action gate: {REPORT}")
        return
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(content, encoding="utf-8")


if __name__ == "__main__": main()
