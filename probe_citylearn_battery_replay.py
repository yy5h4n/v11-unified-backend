#!/usr/bin/env python3
"""Causal replay gate for the shared CityLearn battery/PV process."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys
from typing import Iterator


ROOT = Path(__file__).resolve().parent
PROTOTYPES = ROOT.parent
SOURCE_CACHE = PROTOTYPES / "v5_scenario_compiler" / "source_cache"
SHARED_ASSETS = ROOT / "shared_assets" / "citylearn_v2.5.0"
SHARED_SITE_PACKAGES = ROOT / "shared_runtime" / "site-packages"
PV_SIZING = SHARED_ASSETS / "misc" / "lbl-tracking_the_sun-res-pv.csv"
BATTERY_SIZING = SHARED_ASSETS / "misc" / "battery_choices.yaml"
WEATHER_EPW = SHARED_ASSETS / "dataset" / "weather.epw"
ASSET_MANIFEST = SHARED_ASSETS / "asset_manifest.json"
DEFAULT_BUILDING = "resstock-amy2018-2021-release-1-102040"
DEFAULT_START = 3150
DEFAULT_STEPS = 12
OBSERVATIONS = (
    "hour",
    "non_shiftable_load",
    "solar_generation",
    "electrical_storage_soc",
    "electrical_storage_electricity_consumption",
    "net_electricity_consumption",
)

# PySAM's macOS extension resolves its companion libraries relative to the
# real ASCII runtime path.  Resolve the durable runtime symlink before adding
# it to sys.path; keeping the project source under a non-ASCII Documents path
# otherwise causes a native-loader crash.
_SHARED_SITE_PACKAGES_REAL = SHARED_SITE_PACKAGES.resolve()
if str(_SHARED_SITE_PACKAGES_REAL) not in sys.path:
    sys.path.insert(0, str(_SHARED_SITE_PACKAGES_REAL))

import pandas as pd  # noqa: E402
import yaml  # noqa: E402
from citylearn.citylearn import CityLearnEnv  # noqa: E402
from citylearn.data import DataSet  # noqa: E402


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_assets() -> dict:
    required = (PV_SIZING, BATTERY_SIZING, WEATHER_EPW, ASSET_MANIFEST)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"missing shared CityLearn assets: {missing}")
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("tag") != "v2.5.0":
        raise RuntimeError("shared asset manifest is not pinned to CityLearn v2.5.0")
    return manifest


def battery_sizing_frame() -> pd.DataFrame:
    raw = yaml.safe_load(BATTERY_SIZING.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise RuntimeError("battery sizing catalog is empty or malformed")
    rows = [
        {"model": model, **record["attributes"]}
        for model, record in raw.items()
        if isinstance(record, dict) and isinstance(record.get("attributes"), dict)
    ]
    if len(rows) != len(raw):
        raise RuntimeError("battery sizing catalog contains malformed records")
    return pd.DataFrame(rows).set_index("model")


@contextmanager
def shared_sizing_catalogs() -> Iterator[None]:
    pv_frame = pd.read_csv(PV_SIZING, low_memory=False)
    battery_frame = battery_sizing_frame()
    original_pv = DataSet.get_pv_sizing_data
    original_battery = DataSet.get_battery_sizing_data
    DataSet.get_pv_sizing_data = lambda self: pv_frame.copy()  # type: ignore[method-assign]
    DataSet.get_battery_sizing_data = lambda self: battery_frame.copy()  # type: ignore[method-assign]
    try:
        yield
    finally:
        DataSet.get_pv_sizing_data = original_pv
        DataSet.get_battery_sizing_data = original_battery


def battery_schema(building_id: str) -> dict:
    schema = json.loads((SOURCE_CACHE / "schema.json").read_text(encoding="utf-8"))
    if building_id not in schema.get("buildings", {}):
        raise ValueError(f"unknown CityLearn building: {building_id}")
    schema = deepcopy(schema)
    schema["buildings"] = {building_id: schema["buildings"][building_id]}
    building = schema["buildings"][building_id]
    if not isinstance(building.get("electrical_storage"), dict):
        raise RuntimeError("building has no configured electrical storage")
    if not isinstance(building.get("pv"), dict):
        raise RuntimeError("building has no configured PV")

    building["cooling_storage"] = None
    building["heating_storage"] = None
    building["dhw_storage"] = None
    building["inactive_actions"] = []
    building["inactive_observations"] = []
    building["pv"].setdefault("autosize_attributes", {})["epw_filepath"] = str(
        WEATHER_EPW.resolve()
    )

    for metadata in schema["actions"].values():
        metadata["active"] = False
    schema["actions"]["electrical_storage"]["active"] = True
    for metadata in schema["observations"].values():
        metadata["active"] = False
    for name in OBSERVATIONS:
        if name not in schema["observations"]:
            raise RuntimeError(f"CityLearn schema lacks required observation: {name}")
        schema["observations"][name]["active"] = True
    return schema


def observation_dict(env: CityLearnEnv, observation: list[list[float]]) -> dict[str, float]:
    return {
        name: float(value)
        for name, value in zip(env.observation_names[0], observation[0])
    }


@lru_cache(maxsize=None)
def building_battery_runtime(building_id: str) -> dict:
    """Load one fixed full-year battery/PV configuration for a residence.

    Battery/PV episodes reuse the real annual load and PV profiles but do not
    activate the unrelated HVAC dynamics.  Full-year sizing also guarantees
    that the same residence has one battery and PV system across all windows.
    """
    env = CityLearnEnv(
        battery_schema(building_id),
        root_directory=SOURCE_CACHE,
        simulation_start_time_step=0,
        simulation_end_time_step=8759,
        central_agent=True,
        active_actions=["electrical_storage"],
        active_observations=list(OBSERVATIONS),
        render_mode="none",
    )
    env.reset()
    building = env.buildings[0]
    load = building.energy_simulation.non_shiftable_load.copy()
    solar = building.pv.get_generation(
        building.energy_simulation.solar_generation
    ).copy()
    if len(load) != 8760 or len(solar) != 8760:
        raise RuntimeError(f"incomplete annual battery/PV profile: {building_id}")
    return {
        "battery_template": deepcopy(building.electrical_storage),
        "load": load,
        "solar": solar,
        "pv_nominal_power": float(building.pv.nominal_power),
    }


def run_policy(building_id: str, start: int, steps: int, action: float) -> dict:
    if not -1.0 <= action <= 1.0:
        raise ValueError(f"battery action outside [-1, 1]: {action}")
    runtime = building_battery_runtime(building_id)
    battery = deepcopy(runtime["battery_template"])
    battery.reset()
    metadata = {
        "action_names": ["electrical_storage"],
        "capacity": float(battery.capacity),
        "nominal_power": float(battery.nominal_power),
        "initial_soc": float(battery.soc[battery.time_step]),
        "energy_init": float(battery.energy_init),
        "degraded_capacity": float(battery.degraded_capacity),
        "available_nominal_power": float(battery.available_nominal_power),
        "max_input_power": float(battery.get_max_input_power()),
        "pv_nominal_power": runtime["pv_nominal_power"],
    }
    records: list[dict] = []
    for action_index in range(steps):
        source_step = start + action_index
        battery.charge(action * battery.nominal_power)
        storage_electricity = float(battery.electricity_consumption[action_index])
        load = float(runtime["load"][source_step])
        solar = float(runtime["solar"][source_step])
        records.append(
            {
                "source_step": source_step,
                "action": action,
                "soc": float(battery.soc[action_index]),
                "storage_electricity": storage_electricity,
                "non_shiftable_load": load,
                "solar_generation": solar,
                "net_electricity": load - solar + storage_electricity,
            }
        )
        if action_index + 1 < steps:
            battery.next_time_step()
    return {"terminated": len(records) == steps, "metadata": metadata, "records": records}


def max_series_delta(left: list[dict], right: list[dict], key: str) -> float:
    if len(left) != len(right):
        raise RuntimeError("policy replays have different horizons")
    return max(abs(float(a[key]) - float(b[key])) for a, b in zip(left, right))


def validate_window(building: str, start: int, steps: int) -> dict:
    idle_a = run_policy(building, start, steps, 0.0)
    idle_b = run_policy(building, start, steps, 0.0)
    charge_a = run_policy(building, start, steps, 1.0)
    charge_b = run_policy(building, start, steps, 1.0)
    if idle_a != idle_b or charge_a != charge_b:
        raise RuntimeError(f"CityLearn battery replay is not deterministic: {building}@{start}")
    if not idle_a["terminated"] or not charge_a["terminated"]:
        raise RuntimeError(f"CityLearn battery replay did not terminate: {building}@{start}")

    idle_records = idle_a["records"]
    charge_records = charge_a["records"]
    soc_delta = max_series_delta(idle_records, charge_records, "soc")
    net_delta = max_series_delta(idle_records, charge_records, "net_electricity")
    if soc_delta <= 1e-9 or net_delta <= 1e-9:
        raise RuntimeError(
            f"battery action had no causal effect for {building}@{start}: "
            f"soc_delta={soc_delta}, net_delta={net_delta}, "
            f"metadata={charge_a['metadata']}, first_steps={charge_records[:2]}"
        )
    return {
        "building": building,
        "window": {"start": start, "end": start + steps - 1, "steps": steps},
        "deterministic": True,
        "terminated": True,
        "maximum_soc_delta": soc_delta,
        "maximum_net_electricity_delta": net_delta,
        "battery": charge_a["metadata"],
        "trajectory_digests": {
            "idle": canonical_digest(idle_records),
            "charge": canonical_digest(charge_records),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building", default=DEFAULT_BUILDING)
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--all-v10-windows", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.start < 0 or args.steps < 2:
        raise SystemExit("--start must be non-negative and --steps must be at least 2")

    manifest = require_assets()
    with shared_sizing_catalogs():
        if args.all_v10_windows:
            from unified_compiler.adapters.citylearn_shared import CityLearnSourceIndex

            windows = [
                (bundle.building_id, bundle.source_start_row, bundle.horizon_steps)
                for bundle in CityLearnSourceIndex().bundles()
            ]
        else:
            windows = [(args.building, args.start, args.steps)]
        window_reports = [validate_window(*window) for window in windows]

    report = {
        "backend": "CityLearn",
        "backend_version": "2.5.0",
        "policies": {"idle_action": 0.0, "charge_action": 1.0},
        "deterministic": True,
        "terminated": True,
        "window_count": len(window_reports),
        "building_count": len({item["building"] for item in window_reports}),
        "minimum_maximum_soc_delta": min(
            item["maximum_soc_delta"] for item in window_reports
        ),
        "minimum_maximum_net_electricity_delta": min(
            item["maximum_net_electricity_delta"] for item in window_reports
        ),
        "windows": window_reports,
        "assets": {
            "tag_commit": manifest["tag_commit"],
            "manifest": str(ASSET_MANIFEST.relative_to(ROOT)),
            "source_schema": str((SOURCE_CACHE / "schema.json").relative_to(ROOT.parent)),
        },
    }
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name + ".part")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(args.output)
    print(payload)


if __name__ == "__main__":
    main()
