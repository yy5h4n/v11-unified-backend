#!/usr/bin/env python3
"""Extract canonical, hash-pinned EnergyPlus process traces without making Episodes."""
from __future__ import annotations

import argparse, csv, hashlib, json, math, subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent
EPLUS = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64"
BASE = ROOT / "generated/energyplus_direct_probe_v2"
OUT = ROOT / "generated/energyplus_direct_process_pool_v2.json"
WEATHER = EPLUS / "WeatherData/USA_VA_Sterling-Washington.Dulles.Intl.AP.724030_TMY3.epw"

# role -> (EnergyPlus output variable, required entity prefix)
CASES: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {
    "thermal_occupancy_energy": ("5ZoneAirCooledWithSpacesDaylightingIntMass.idf", {
        "zone_temperature": ("Zone Mean Air Temperature", "ZONE 1:"),
        "zone_setpoint": ("Zone Thermostat Heating Setpoint Temperature", "ZONE 1:"),
        "occupancy": ("Zone People Occupant Count", "ZONE 1:"),
        "hvac": ("Zone Air System Sensible Heating Rate", "ZONE 1:"),
        "total_energy": ("Facility Total Building Electricity Demand Rate", "Whole Building:"),
        "weather": ("Site Outdoor Air Drybulb Temperature", "Environment:"),
    }),
    "humidity_iaq_ventilation": ("AirflowNetwork_MultiZone_House_OvercoolDehumid.idf", {
        "humidity": ("Zone Air Relative Humidity", "LIVING ZONE:"),
        "zone_temperature": ("Zone Mean Air Temperature", "LIVING ZONE:"),
        "fan": ("Fan Electricity Rate", "SUPPLY FAN 1:"),
        "control_state": ("Zone Thermostat Cooling Setpoint Temperature", "LIVING ZONE:"),
    }),
    "lighting_blinds": ("PurchAirWithDaylightingAndShadeControl.idf", {
        "illuminance": ("Daylighting Reference Point 1 Illuminance", "WEST ZONE_DAYLCTRL:"),
        "lighting_power": ("Zone Lights Electricity Rate", "WEST ZONE:"),
        "shade_state": ("Surface Shading Device Is On Time Fraction", "ZN001:WALL001:WIN001:"),
        "solar": ("Surface Window Transmitted Solar Radiation Energy", "ZN001:WALL001:WIN001:"),
    }),
}
REQUIRED = {family: set(specifications) for family, (_, specifications) in CASES.items()}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_values(rows: list[dict[str, str]], column: str) -> list[float]:
    result = []
    for row in rows:
        try:
            value = float(row[column])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            result.append(value)
    return result


def parse_timestamps(rows: list[dict[str, str]]) -> list[datetime]:
    result, previous = [], None
    year = 2001
    for row in rows:
        date_part, time_part = " ".join(row["Date/Time"].strip().split()).split(" ", 1)
        hour = int(time_part[:2])
        normalized_time = "00" + time_part[2:] if hour == 24 else time_part
        candidate = datetime.strptime(f"{year}/{date_part} {normalized_time}", "%Y/%m/%d %H:%M:%S")
        if hour == 24:
            candidate += timedelta(days=1)
        while previous is not None and candidate <= previous:
            year += 1
            candidate = candidate.replace(year=year)
        result.append(candidate)
        previous = candidate
    return result


def select_column(headers: list[str], rows: list[dict[str, str]], variable: str, entity: str) -> str | None:
    candidates = [column for column in headers if variable.lower() in column.lower() and column.upper().startswith(entity.upper())]
    candidates.sort(key=lambda column: ("(timestep)" not in column.lower(), column))
    for column in candidates:
        values = finite_values(rows, column)
        if values and max(values) - min(values) > 1e-12 and "(timestep)" in column.lower():
            return column
    return None


def continuous_segments(timestamps: list[datetime], step: int) -> list[tuple[int, int]]:
    boundaries = [0]
    for index, (left, right) in enumerate(zip(timestamps, timestamps[1:]), start=1):
        if int((right - left).total_seconds()) != step:
            boundaries.append(index)
    boundaries.append(len(timestamps))
    return list(zip(boundaries, boundaries[1:]))


def canonical_digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def probe(family: str, case: tuple[str, dict[str, tuple[str, str]]]) -> dict[str, Any]:
    idf_name, specifications = case
    source, executable = EPLUS / "ExampleFiles" / idf_name, EPLUS / "energyplus"
    if not all(path.is_file() for path in (source, WEATHER, executable)):
        raise RuntimeError(f"missing EnergyPlus asset for {family}")
    destination = BASE / family
    destination.mkdir(parents=True, exist_ok=True)
    prepared = destination / "prepared_probe.idf"
    additions = "\n! Probe-only output requests; source model is unchanged.\n" + "".join(
        f"Output:Variable,*,{variable},Timestep;\n" for variable in sorted({value[0] for value in specifications.values()})
    )
    prepared.write_text(source.read_text(encoding="utf-8", errors="replace") + additions, encoding="utf-8")
    run = subprocess.run([str(executable), "-r", "-w", str(WEATHER), "-d", str(destination), str(prepared)], capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError(f"EnergyPlus failed for {family}: {run.stderr[-1000:]}")
    with (destination / "eplusout.csv").open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or "Date/Time" not in rows[0]:
        raise RuntimeError(f"missing timestamped output for {family}")
    timestamps = parse_timestamps(rows)
    differences = [int((right - left).total_seconds()) for left, right in zip(timestamps, timestamps[1:])]
    local_differences = [value for value in differences if 0 < value <= 3600]
    if not local_differences:
        raise RuntimeError(f"cannot infer solver timestep for {family}")
    step = Counter(local_differences).most_common(1)[0][0]
    if 86400 % step:
        raise RuntimeError(f"non-day-divisible timestep for {family}: {step}")
    headers = list(rows[0])
    roles = {role: column for role, (variable, entity) in specifications.items() if (column := select_column(headers, rows, variable, entity))}
    missing = REQUIRED[family] - set(roles)
    if missing:
        raise RuntimeError(f"missing coherent required fields for {family}: {sorted(missing)}")

    trace_rows = [{
        "row": index,
        "timestamp": timestamp.isoformat(),
        "observations": {role: float(row[column]) for role, column in roles.items()},
    } for index, (row, timestamp) in enumerate(zip(rows, timestamps))]
    trace_path = destination / "trace.jsonl"
    trace_path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in trace_rows), encoding="utf-8")

    rows_per_day, windows = 86400 // step, []
    segments = continuous_segments(timestamps, step)
    for segment_start, segment_end in segments:
        for start in range(segment_start, segment_end - rows_per_day + 1, rows_per_day):
            end, window_rows = start + rows_per_day, trace_rows[start:start + rows_per_day]
            ranges = {role: {
                "min": min(row["observations"][role] for row in window_rows),
                "max": max(row["observations"][role] for row in window_rows),
                "mean": mean(row["observations"][role] for row in window_rows),
            } for role in roles}
            windows.append({
                "window_id": f"{family}-w{len(windows):03d}", "start_row": start, "end_row_exclusive": end,
                "coverage_start_timestamp": (timestamps[start] - timedelta(seconds=step)).isoformat(),
                "coverage_end_timestamp": timestamps[end - 1].isoformat(), "row_count": rows_per_day,
                "duration_seconds": rows_per_day * step, "slice_sha256": canonical_digest(window_rows), "ranges": ranges,
            })
    if not windows:
        raise RuntimeError(f"no continuous 24-hour window for {family}")

    return {
        "physical_process_id": "energyplus:" + family, "family": family, "status": "DATA_PROBED_PENDING_REPLAY",
        "runtime_sha256": sha256(executable), "model_sha256": sha256(source), "prepared_model_sha256": sha256(prepared),
        "weather_sha256": sha256(WEATHER), "observability_gate": True, "action_available_gate": False,
        "action_sensitivity_gate": False, "determinism_gate": False, "evaluator_gate": False,
        "gate_blockers": ["ACTION_AVAILABILITY_NOT_YET_PROBED", "ACTION_SENSITIVITY_NOT_YET_PROBED", "CROSS_RUN_DETERMINISM_NOT_YET_PROBED", "RESPONSIBILITY_EVALUATOR_NOT_YET_BOUND"],
        "action_sensitivity_verified": False, "episode_count": 0, "timestep_seconds": step, "frequency": f"PT{step}S",
        "row_count": len(rows), "continuous_segment_count": len(segments), "field_roles": roles,
        "time_basis": "EnergyPlus local simulation time; timezone unspecified",
        "trace": {"path": str(trace_path.relative_to(ROOT)), "sha256": sha256(trace_path), "format": "jsonl", "immutable": False, "integrity": "hash_pinned_regenerable"},
        "windows": windows, "source_trace_ref": str(trace_path.relative_to(ROOT)), "official_example_modeled_data": True,
        "household_measured_data": False, "gold_actions_released": False,
    }


def build() -> dict[str, Any]:
    return {"schema_version": "energyplus-direct-process-pool-v2", "status": "DATA_PROBED_PENDING_REPLAY",
            "action_sensitivity_verified": False, "episode_count": 0, "official_example_modeled_data": True,
            "household_measured_data": False, "gold_actions_released": False,
            "processes": [probe(family, case) for family, case in sorted(CASES.items())]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != content:
            raise SystemExit(f"stale or nondeterministic v2 artifact: {OUT}")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8")
    (BASE / "probe_report.json").write_text(json.dumps({"schema_version": "energyplus-direct-probe-v2", "status": "DATA_PROBED_PENDING_REPLAY", "process_count": 3, "action_sensitivity_verified": False, "episode_count": 0, "official_example_modeled_data": True, "household_measured_data": False}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
