#!/usr/bin/env python3
"""D3 CityLearn multi-building shared-meter route.

This route deliberately keeps two semantics separate.  CityLearn supplies one
native ``CityLearnEnv`` containing both buildings and computes each building's
transition natively.  The district capacity/headroom values are an explicit
benchmark constraint layered on the native transition: CityLearn 2.5 does not
clip a transformer or reject actions at this threshold.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys
from typing import Any, Iterator

from d3_citylearn_coupling_adapter import (
    CITYLEARN_VERSION,
    DEFAULT_BUILDING,
    LSTM_WARMUP_HOURS,
    ROOT,
    SHARED_ASSETS,
    SOURCE_CACHE,
    _json_digest,
    _load_data_prerequisites,
    _sizing_catalogs,
)

DEFAULT_BUILDINGS = (
    DEFAULT_BUILDING,
    "resstock-amy2018-2021-release-1-103125",
)
DEFAULT_START = 912
DEFAULT_HORIZON = 24
DEFAULT_SHARED_METER_CAPACITY_KWH = 5.0

OBSERVATIONS = (
    "hour",
    "outdoor_dry_bulb_temperature",
    "occupant_count",
    "indoor_dry_bulb_temperature",
    "electrical_storage_soc",
    "net_electricity_consumption",
)
PUBLIC_ACTIONS = ("battery_rate", "hvac_rate")
PROVIDES = frozenset(
    {
        "district.net_electricity",
        "district.peak_capacity",
        "district.shared_meter_headroom",
        "storage.soc",
        "storage.charge_discharge_action",
        "thermal.zone_temperature",
        "thermal.hvac_action",
        "building.net_electricity",
    }
)


class D3MultiBuildingError(RuntimeError):
    """Fail-closed multi-building route error."""


class D3MultiBuildingActionError(ValueError):
    """Invalid public action or time increment."""


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise D3MultiBuildingError(f"missing provenance file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_fingerprint() -> dict[str, Any]:
    try:
        import citylearn  # type: ignore
        version = importlib.metadata.version("citylearn")
        package_root = Path(citylearn.__file__).resolve().parent
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        raise D3MultiBuildingError("CityLearn 2.5.0 runtime is unavailable") from exc
    if version != CITYLEARN_VERSION:
        raise D3MultiBuildingError(f"CityLearn version mismatch: expected {CITYLEARN_VERSION}, got {version}")
    files = [(str(p.relative_to(package_root)), _sha256(p)) for p in sorted(package_root.rglob("*.py"))]
    return {
        "citylearn_version": version,
        "python": platform.python_version(),
        "runtime_package": "citylearn",
        "runtime_sha256": _json_digest(files),
        "runtime_file_count": len(files),
    }


def _assets(building_ids: Sequence[str]) -> dict[str, Any]:
    manifest_path = SHARED_ASSETS / "asset_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise D3MultiBuildingError("invalid CityLearn asset manifest") from exc
    if manifest.get("tag") != f"v{CITYLEARN_VERSION}" or not manifest.get("tag_commit"):
        raise D3MultiBuildingError("asset manifest is not pinned to CityLearn v2.5.0")
    out: dict[str, Any] = {
        "citylearn_tag": f"v{CITYLEARN_VERSION}",
        "citylearn_tag_commit": manifest["tag_commit"],
        "asset_manifest_sha256": _sha256(manifest_path),
        "source_schema_sha256": _sha256(SOURCE_CACHE / "schema.json"),
        "weather_sha256": _sha256(SHARED_ASSETS / "dataset" / "weather.epw"),
        "pv_catalog_sha256": _sha256(SHARED_ASSETS / "misc" / "lbl-tracking_the_sun-res-pv.csv"),
        "battery_catalog_sha256": _sha256(SHARED_ASSETS / "misc" / "battery_choices.yaml"),
    }
    traces: dict[str, str] = {}
    models: dict[str, str] = {}
    for building_id in building_ids:
        for suffix in (".csv", ".pth"):
            path = SOURCE_CACHE / f"{building_id}{suffix}"
            if not path.is_file():
                raise D3MultiBuildingError(f"missing CityLearn multi-building asset: {path}")
            (traces if suffix == ".csv" else models)[building_id] = _sha256(path)
    out["source_trace_sha256_by_building"] = traces
    out["thermal_model_sha256_by_building"] = models
    return out


def multi_building_schema(building_ids: Sequence[str]) -> dict[str, Any]:
    ids = tuple(building_ids)
    if len(ids) < 2 or len(set(ids)) != len(ids) or any(not isinstance(x, str) or not x for x in ids):
        raise D3MultiBuildingError("D3 multi-building route requires at least two distinct building ids")
    _assets(ids)
    try:
        source = json.loads((SOURCE_CACHE / "schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise D3MultiBuildingError("invalid CityLearn source schema") from exc
    buildings = source.get("buildings")
    if not isinstance(buildings, dict) or any(i not in buildings for i in ids):
        missing = [i for i in ids if i not in (buildings or {})]
        raise D3MultiBuildingError(f"unknown CityLearn building ids: {missing}")
    schema = deepcopy(source)
    schema["buildings"] = {i: deepcopy(buildings[i]) for i in ids}
    for building_id in ids:
        building = schema["buildings"][building_id]
        if not isinstance(building.get("electrical_storage"), dict) or not isinstance(building.get("pv"), dict):
            raise D3MultiBuildingError(f"building {building_id} lacks native battery and PV")
        building["pv"].setdefault("autosize_attributes", {})["epw_filepath"] = str(
            (SHARED_ASSETS / "dataset" / "weather.epw").resolve()
        )
        building["inactive_actions"] = []
        building["inactive_observations"] = []
    for metadata in schema["actions"].values():
        metadata["active"] = False
    schema["actions"]["electrical_storage"]["active"] = True
    schema["actions"]["cooling_or_heating_device"]["active"] = True
    for metadata in schema["observations"].values():
        metadata["active"] = False
    for name in OBSERVATIONS:
        if name not in schema["observations"]:
            raise D3MultiBuildingError(f"native schema lacks observation {name}")
        schema["observations"][name]["active"] = True
    schema["_d3_multibuilding_provenance"] = _assets(ids)
    return schema


def _clear_reset_preview_all(env: Any) -> None:
    if env.time_step != 0:
        return
    for building in env.buildings:
        devices = [
            building.cooling_device,
            building.heating_device,
            building.dhw_device,
            building._Building__non_shiftable_load_device,
        ]
        for device in devices:
            device._ElectricDevice__electricity_consumption[env.time_step] = 0.0


def _demand_following_hvac(building: Any, index: int) -> float:
    cooling = float(building.energy_simulation.cooling_demand_without_control[index])
    heating = float(building.energy_simulation.heating_demand_without_control[index])
    outdoor = float(building.weather.outdoor_dry_bulb_temperature[index])
    if cooling > heating and cooling > 0.0:
        maximum = building.cooling_device.get_max_output_power(outdoor, heating=False)
        return -min(cooling / max(float(maximum), 1e-12), 1.0)
    if heating > 0.0:
        maximum = building.heating_device.get_max_output_power(outdoor, heating=True)
        return min(heating / max(float(maximum), 1e-12), 1.0)
    return 0.0


def _native_effect(building: Any, index: int) -> dict[str, float]:
    load = float(building.non_shiftable_load[index])
    solar = -float(building.solar_generation[index])
    cooling = float(building.cooling_device.electricity_consumption[index])
    heating = float(building.heating_device.electricity_consumption[index])
    dhw = float(building.dhw_device.electricity_consumption[index])
    storage = float(building.electrical_storage_electricity_consumption[index])
    net = float(building.net_electricity_consumption[index])
    return {
        "indoor_temperature_c": float(building.indoor_dry_bulb_temperature[index]),
        "battery_soc": float(building.electrical_storage.soc[index]),
        "non_shiftable_load_kwh": load,
        "solar_generation_kwh": solar,
        "hvac_electricity_kwh": cooling + heating,
        "dhw_electricity_kwh": dhw,
        "storage_electricity_kwh": storage,
        "net_electricity_kwh": net,
        "net_energy_balance_error_kwh": net - (load + cooling + heating + dhw + storage - solar),
    }


class D3CityLearnMultiBuildingAgentRoute:
    """Persistent central-agent boundary over one native multi-building env."""

    tick_seconds = 3600.0
    action_names = ("electrical_storage", "cooling_or_heating_device")

    def __init__(
        self,
        building_ids: Sequence[str] = DEFAULT_BUILDINGS,
        start: int = DEFAULT_START,
        horizon: int = DEFAULT_HORIZON,
        *,
        warmup_steps: int = LSTM_WARMUP_HOURS,
        shared_meter_capacity_kwh: float = DEFAULT_SHARED_METER_CAPACITY_KWH,
    ) -> None:
        ids = tuple(building_ids)
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise D3MultiBuildingError("building_ids must contain at least two distinct buildings")
        if any(not isinstance(x, str) or not x for x in ids):
            raise D3MultiBuildingError("building_ids must be non-empty strings")
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (start, horizon, warmup_steps)):
            raise D3MultiBuildingError("start, horizon, and warmup_steps must be integers")
        if start < warmup_steps or horizon < 1 or start + horizon > 8760 or warmup_steps < 0:
            raise D3MultiBuildingError("D3 multi-building episode window is outside annual source trace")
        if isinstance(shared_meter_capacity_kwh, bool) or not isinstance(shared_meter_capacity_kwh, (int, float)) or not math.isfinite(float(shared_meter_capacity_kwh)) or float(shared_meter_capacity_kwh) <= 0:
            raise D3MultiBuildingError("shared meter capacity must be a positive finite number")
        self.building_ids = ids
        self.start = start
        self.horizon = horizon
        self.warmup_steps = warmup_steps
        self.shared_meter_capacity_kwh = float(shared_meter_capacity_kwh)
        self._env: Any | None = None
        self._latest_observation: dict[str, float] | None = None
        self._latest_effects: dict[str, dict[str, float]] = {}
        self._district_peak = 0.0
        self._steps = 0
        self._done = False
        self._seed: int | None = None

    @property
    def env(self) -> Any:
        if self._env is None:
            raise D3MultiBuildingError("reset(seed) must be called before using the route")
        return self._env

    def _new_env(self) -> Any:
        CityLearnEnv, DataSet, pd, yaml = _load_data_prerequisites()
        with _sizing_catalogs(DataSet, pd, yaml):
            return CityLearnEnv(
                multi_building_schema(self.building_ids),
                root_directory=SOURCE_CACHE,
                simulation_start_time_step=self.start - self.warmup_steps,
                simulation_end_time_step=self.start + self.horizon,
                central_agent=True,
                active_actions=list(self.action_names),
                active_observations=list(OBSERVATIONS),
                render_mode="none",
            )

    def _observation(self, observation: list[list[float]], effects: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
        values = observation[0]
        # CityLearn's central-agent contract emits shared observations only
        # once (for the first building), then appends each building's private
        # observations.  Reconstruct the per-building public view without
        # pretending that shared weather/hour values are private channels.
        shared = set(getattr(self.env, "shared_observations", ()))
        # The backend's observation ordering follows the Building observation
        # dictionary, not the caller's ``active_observations`` argument (for
        # example occupant_count may follow indoor temperature).  Read that
        # native order instead of silently swapping values in the public view.
        native_names_by_building = [
            [name for name in building.observations().keys() if name in OBSERVATIONS and (position == 0 or name not in shared)]
            for position, building in enumerate(self.env.buildings)
        ]
        expected = sum(len(names) for names in native_names_by_building)
        if len(values) != expected:
            raise D3MultiBuildingError(f"native central observation length {len(values)} != {expected}; shared={sorted(shared)}")
        result: dict[str, float] = {}
        cursor = 0
        shared_values: dict[str, float] = {}
        for position, (building_id, names) in enumerate(zip(self.building_ids, native_names_by_building)):
            for name in names:
                value = float(values[cursor])
                cursor += 1
                if name in shared:
                    shared_values[name] = value
                result[f"{building_id}.{name}"] = value
        if cursor != len(values):
            raise D3MultiBuildingError("native central observation cursor did not consume all values")
        nets = {i: float(effects[i]["net_electricity_kwh"]) for i in self.building_ids}
        district_net = sum(nets.values())
        result["district_net_kwh"] = district_net
        result["district_peak_kwh"] = float(self._district_peak)
        result["shared_meter_headroom_kwh"] = self.shared_meter_capacity_kwh - district_net
        for building_id in self.building_ids:
            result[f"{building_id}.feasible_headroom_kwh"] = self.shared_meter_capacity_kwh - sum(
                value for other, value in nets.items() if other != building_id
            )
        return result

    def reset(self, seed: int = 0) -> dict[str, float]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise D3MultiBuildingActionError("reset seed must be an integer")
        self.close()
        env = self._new_env()
        try:
            observation, _ = env.reset(seed=seed)
            for _ in range(self.warmup_steps):
                _clear_reset_preview_all(env)
                values: list[float] = []
                for building in env.buildings:
                    values.extend((0.0, _demand_following_hvac(building, env.time_step)))
                observation, _, terminated, truncated, _ = env.step([values])
                if terminated or truncated:
                    raise D3MultiBuildingError("native CityLearn terminated during multi-building warm-up")
        except Exception:
            close = getattr(env, "close", None)
            if close is not None:
                close()
            raise
        self._env = env
        self._seed = seed
        self._steps = 0
        self._done = False
        self._district_peak = 0.0
        index = env.time_step
        self._latest_effects = {building.name: _native_effect(building, index) for building in env.buildings}
        self._latest_observation = self._observation(observation, self._latest_effects)
        return deepcopy(self._latest_observation)

    def observe(self) -> dict[str, float]:
        if self._latest_observation is None:
            raise D3MultiBuildingError("reset(seed) must be called before observe()")
        return deepcopy(self._latest_observation)

    def legal_actions(self) -> dict[str, Any]:
        env = self.env
        spaces = getattr(env, "action_space", None)
        if not isinstance(spaces, (list, tuple)) or not spaces:
            raise D3MultiBuildingError("CityLearn native action_space is unavailable")
        space = spaces[0]
        low, high, shape = getattr(space, "low", None), getattr(space, "high", None), getattr(space, "shape", None)
        if low is None or high is None or shape is None:
            raise D3MultiBuildingError("CityLearn native action space is not a bounded Box")
        lows, highs = [float(x) for x in low], [float(x) for x in high]
        expected = len(self.building_ids) * len(PUBLIC_ACTIONS)
        if len(lows) != expected or len(highs) != expected:
            raise D3MultiBuildingError("native action space does not match multi-building channels")
        channels: dict[str, Any] = {}
        for building_position, building_id in enumerate(self.building_ids):
            for action_position, action_name in enumerate(PUBLIC_ACTIONS):
                index = building_position * len(PUBLIC_ACTIONS) + action_position
                channels[f"{building_id}.{action_name}"] = {
                    "index": index,
                    "type": "continuous",
                    "range": [lows[index], highs[index]],
                }
        current_net = sum(self._latest_effects.get(i, {}).get("net_electricity_kwh", 0.0) for i in self.building_ids)
        feasible = {
            building_id: self.shared_meter_capacity_kwh - sum(
                self._latest_effects.get(other, {}).get("net_electricity_kwh", 0.0)
                for other in self.building_ids if other != building_id
            )
            for building_id in self.building_ids
        }
        return {
            "type": "citylearn_central_joint_action",
            "native": True,
            "shape": list(shape),
            "channels": channels,
            "native_action_names": [name for _ in self.building_ids for name in self.action_names],
            "public_action_names": [f"{i}.{a}" for i in self.building_ids for a in PUBLIC_ACTIONS],
            "shared_meter_constraint": {
                "semantics": "benchmark/shared-meter threshold; external to CityLearn; no native clipping",
                "capacity_kwh": self.shared_meter_capacity_kwh,
                "native_clipping": False,
                "current_district_net_kwh": current_net,
                "shared_meter_headroom_kwh": self.shared_meter_capacity_kwh - current_net,
                "feasible_headroom_kwh_by_building": feasible,
            },
        }

    @staticmethod
    def _number(value: Any, channel: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise D3MultiBuildingActionError(f"{channel} action must be a finite number")
        result = float(value)
        if not math.isfinite(result) or result < -1.0 or result > 1.0:
            raise D3MultiBuildingActionError(f"{channel} action must be in native [-1, 1]")
        return result

    def _native_action(self, action: Any) -> list[list[float]]:
        if not isinstance(action, Mapping) or set(action) != set(self.building_ids):
            raise D3MultiBuildingActionError("action must contain exactly all building ids")
        values: list[float] = []
        for building_id in self.building_ids:
            per_building = action[building_id]
            if not isinstance(per_building, Mapping) or set(per_building) != set(PUBLIC_ACTIONS):
                raise D3MultiBuildingActionError(f"{building_id} action must contain battery_rate and hvac_rate")
            values.extend(self._number(per_building[name], f"{building_id}.{name}") for name in PUBLIC_ACTIONS)
        return [values]

    def step(self, action: Any, dt_seconds: float = tick_seconds) -> dict[str, Any]:
        if self._latest_observation is None:
            raise D3MultiBuildingError("reset(seed) must be called before step()")
        if self._done:
            raise D3MultiBuildingActionError("D3 multi-building episode is already done")
        if isinstance(dt_seconds, bool) or not isinstance(dt_seconds, (int, float)) or not math.isfinite(float(dt_seconds)) or float(dt_seconds) != self.tick_seconds:
            raise D3MultiBuildingActionError(f"D3 route requires dt_seconds={self.tick_seconds:g}")
        native_action = self._native_action(action)
        env = self.env
        index = env.time_step
        observation, _, terminated, truncated, native_info = env.step(native_action)
        self._steps += 1
        self._latest_effects = {building.name: _native_effect(building, index) for building in env.buildings}
        district_net = sum(effect["net_electricity_kwh"] for effect in self._latest_effects.values())
        self._district_peak = max(self._district_peak, district_net)
        self._latest_observation = self._observation(observation, self._latest_effects)
        self._done = bool(terminated or truncated)
        feasible = {
            building_id: self.shared_meter_capacity_kwh - sum(
                effect["net_electricity_kwh"] for other, effect in self._latest_effects.items() if other != building_id
            )
            for building_id in self.building_ids
        }
        district = {
            "district_net_kwh": district_net,
            "district_peak_kwh": self._district_peak,
            "shared_meter_capacity_kwh": self.shared_meter_capacity_kwh,
            "shared_meter_headroom_kwh": self.shared_meter_capacity_kwh - district_net,
            "capacity_violated": district_net > self.shared_meter_capacity_kwh + 1e-9,
            "feasible_headroom_kwh_by_building": feasible,
            "semantics": "benchmark/shared-meter threshold; external to CityLearn; no native clipping",
        }
        action_receipt = {
            building_id: {name: native_action[0][position * 2 + offset] for offset, name in enumerate(PUBLIC_ACTIONS)}
            for position, building_id in enumerate(self.building_ids)
        }
        return {
            "time_seconds": self._steps * self.tick_seconds,
            "observation": deepcopy(self._latest_observation),
            "action": action_receipt,
            "effects": deepcopy(self._latest_effects),
            "district": deepcopy(district),
            "done": self._done,
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "delta_t_seconds": self.tick_seconds,
            "info": {
                "native_action_names": [name for _ in self.building_ids for name in self.action_names],
                "native_action": native_action,
                "source_row": self.start + self._steps - 1,
                "effects": deepcopy(self._latest_effects),
                "district": deepcopy(district),
                "native_info": deepcopy(native_info) if isinstance(native_info, Mapping) else {},
                "single_native_episode": True,
                "native_multi_building_transition": True,
            },
        }

    def close(self) -> None:
        if self._env is not None:
            close = getattr(self._env, "close", None)
            if close is not None:
                close()
        self._env = None
        self._latest_observation = None
        self._latest_effects = {}
        self._district_peak = 0.0
        self._steps = 0
        self._done = False


def _zero_action(building_ids: Sequence[str]) -> dict[str, dict[str, float]]:
    return {i: {"battery_rate": 0.0, "hvac_rate": 0.0} for i in building_ids}


def run_multibuilding_episode(
    building_ids: Sequence[str] = DEFAULT_BUILDINGS,
    start: int = DEFAULT_START,
    horizon: int = DEFAULT_HORIZON,
    *,
    policy: str = "zero",
    shared_meter_capacity_kwh: float = DEFAULT_SHARED_METER_CAPACITY_KWH,
    seed: int = 0,
) -> dict[str, Any]:
    if policy not in {"zero", "building_0_battery", "building_1_battery"}:
        raise D3MultiBuildingError(f"unknown multi-building policy: {policy}")
    route = D3CityLearnMultiBuildingAgentRoute(
        building_ids, start, horizon, shared_meter_capacity_kwh=shared_meter_capacity_kwh
    )
    try:
        route.reset(seed=seed)
        records: list[dict[str, Any]] = []
        for _ in range(horizon):
            action = _zero_action(route.building_ids)
            if policy == "building_0_battery":
                action[route.building_ids[0]]["battery_rate"] = 1.0
            elif policy == "building_1_battery":
                action[route.building_ids[1]]["battery_rate"] = 1.0
            transition = route.step(action)
            records.append(
                {
                    "source_row": transition["info"]["source_row"],
                    "action": transition["action"],
                    "effects": transition["effects"],
                    "district": transition["district"],
                    "backend_terminated": transition["terminated"] or transition["truncated"],
                }
            )
        if not records[-1]["backend_terminated"] or any(r["backend_terminated"] for r in records[:-1]):
            raise D3MultiBuildingError("native CityLearn did not terminate exactly at full public episode")
    finally:
        route.close()
    assets = _assets(tuple(building_ids))
    runtime = _runtime_fingerprint()
    return {
        "schema_version": "d3-citylearn-multibuilding-episode-v1",
        "backend": "CityLearn",
        "backend_version": CITYLEARN_VERSION,
        "building_ids": list(building_ids),
        "source_window": {"start": start, "end": start + horizon - 1, "horizon_steps": horizon},
        "private_warmup_steps": LSTM_WARMUP_HOURS,
        "single_native_episode": True,
        "native_env_instances_per_trajectory": 1,
        "native_multi_building_transition": True,
        "policy": policy,
        "shared_meter_constraint": {
            "capacity_kwh": shared_meter_capacity_kwh,
            "semantics": "benchmark/shared-meter threshold; external to CityLearn; no native clipping",
            "native_clipping": False,
        },
        "records": records,
        "trajectory_sha256": _json_digest(records),
        "provenance": {**assets, **runtime},
    }


def probe_multibuilding(
    building_ids: Sequence[str] = DEFAULT_BUILDINGS,
    start: int = DEFAULT_START,
    horizon: int = DEFAULT_HORIZON,
    *,
    shared_meter_capacity_kwh: float = DEFAULT_SHARED_METER_CAPACITY_KWH,
    seed: int = 37,
) -> dict[str, Any]:
    ids = tuple(building_ids)
    baseline = run_multibuilding_episode(ids, start, horizon, policy="zero", shared_meter_capacity_kwh=shared_meter_capacity_kwh, seed=seed)
    replay = run_multibuilding_episode(ids, start, horizon, policy="zero", shared_meter_capacity_kwh=shared_meter_capacity_kwh, seed=seed)
    building_a = run_multibuilding_episode(ids, start, horizon, policy="building_0_battery", shared_meter_capacity_kwh=shared_meter_capacity_kwh, seed=seed)
    building_b = run_multibuilding_episode(ids, start, horizon, policy="building_1_battery", shared_meter_capacity_kwh=shared_meter_capacity_kwh, seed=seed)
    if baseline["records"] != replay["records"]:
        raise D3MultiBuildingError("multi-building deterministic replay mismatch")
    left, right = baseline["records"], building_a["records"]
    battery_delta = max(abs(right[n]["effects"][ids[0]]["battery_soc"] - left[n]["effects"][ids[0]]["battery_soc"]) for n in range(horizon))
    district_delta = max(abs(right[n]["district"]["district_net_kwh"] - left[n]["district"]["district_net_kwh"]) for n in range(horizon))
    headroom_delta = max(abs(right[n]["district"]["feasible_headroom_kwh_by_building"][ids[1]] - left[n]["district"]["feasible_headroom_kwh_by_building"][ids[1]]) for n in range(horizon))
    native_other_delta = max(abs(right[n]["effects"][ids[1]]["net_electricity_kwh"] - left[n]["effects"][ids[1]]["net_electricity_kwh"]) for n in range(horizon))
    peak = max(r["district"]["district_peak_kwh"] for r in baseline["records"])
    if battery_delta <= 1e-9 or district_delta <= 1e-9 or headroom_delta <= 1e-9:
        raise D3MultiBuildingError(f"multi-building causal gate failed: battery={battery_delta}, district={district_delta}, headroom={headroom_delta}")
    if any(r["backend_terminated"] for r in baseline["records"][:-1]) or not baseline["records"][-1]["backend_terminated"]:
        raise D3MultiBuildingError("multi-building termination boundary failed")
    if any(abs(effect["net_energy_balance_error_kwh"]) > 1e-5 for r in baseline["records"] for effect in r["effects"].values()):
        raise D3MultiBuildingError("native building net-energy identity failed")
    return {
        "schema_version": "d3-citylearn-multibuilding-probe-v1",
        "passed": True,
        "building_ids": list(ids),
        "full_episode_steps": horizon,
        "single_native_episode": True,
        "native_env_instances_per_trajectory": 1,
        "native_multi_building_transition": True,
        "deterministic_replay": True,
        "native_building_0_battery_soc_delta": battery_delta,
        "district_net_delta": district_delta,
        "other_building_native_net_delta": native_other_delta,
        "other_building_feasible_headroom_delta": headroom_delta,
        "baseline_district_peak_kwh": peak,
        "coupling_gate": {
            "passed": headroom_delta > 1e-9,
            "native_multi_building_dynamics": True,
            "native_env_instances_per_trajectory": 1,
            "native_cross_building_physical_feedback": False,
            "native_district_aggregation": True,
            "external_shared_meter_capacity_coupling": True,
            "capacity_semantics": "benchmark/shared-meter threshold; external to CityLearn; no native transformer clipping",
        },
        "shared_meter_constraint": baseline["shared_meter_constraint"],
        "provenance": {
            "adapter_path": "d3_citylearn_multibuilding_adapter.py",
            "adapter_sha256": _sha256(ROOT / "d3_citylearn_multibuilding_adapter.py"),
            "probe_path": "probe_d3_citylearn_multibuilding.py",
            "probe_sha256": _sha256(ROOT / "probe_d3_citylearn_multibuilding.py"),
            "public_runtime": {
                "python": baseline["provenance"]["python"],
                "runtime_package": baseline["provenance"]["runtime_package"],
                "citylearn_version": baseline["provenance"]["citylearn_version"],
                "citylearn_tag_commit": baseline["provenance"]["citylearn_tag_commit"],
                "runtime_sha256": baseline["provenance"]["runtime_sha256"],
                "runtime_file_count": baseline["provenance"]["runtime_file_count"],
            },
        },
        "baseline": baseline,
        "causal_building_0_battery": building_a,
        "causal_building_1_battery": building_b,
    }


class D3CityLearnMultiBuildingAdapter:
    """Typed adapter for the dedicated D3 multi-building route."""

    backend = "citylearn_d3_multibuilding"

    def __init__(self, **kwargs: Any):
        self.kwargs = kwargs
        self._report: dict[str, Any] | None = None

    def _ensure(self) -> dict[str, Any]:
        if self._report is None:
            self._report = probe_multibuilding(**self.kwargs)
        return self._report

    def capabilities(self) -> tuple[Any, ...]:
        from unified_compiler.types import BackendCapability, CapabilityStatus
        report = self._ensure()
        return (BackendCapability(
            capability_id="citylearn_d3.multibuilding_shared_meter",
            backend=self.backend,
            provides=tuple(sorted(PROVIDES)),
            status=CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED,
            evidence=("probe_d3_citylearn_multibuilding.py",),
            metadata={"horizon_steps": report["full_episode_steps"], "single_native_episode": True, "external_capacity_threshold": True},
        ),)

    def scan(self, requirements: Sequence[Any]) -> tuple[Any, ...]:
        from unified_compiler.types import PhysicalProcess
        report = self._ensure()
        if not requirements or not any(req.required_capabilities <= PROVIDES for req in requirements):
            return ()
        baseline = report["baseline"]
        return (PhysicalProcess(
            process_id=f"citylearn-d3-multibuilding://{'+'.join(baseline['building_ids'])}/rows:{baseline['source_window']['start']}-{baseline['source_window']['end']}",
            domain="district_energy_competition",
            backend=self.backend,
            backend_version=CITYLEARN_VERSION,
            source_id=f"{'+'.join(baseline['building_ids'])}:{baseline['source_window']['start']}-{baseline['source_window']['end']}",
            source_hash=f"sha256:{baseline['provenance']['source_schema_sha256']}",
            horizon_steps=baseline["source_window"]["horizon_steps"],
            observation_interval_seconds=3600.0,
            provided_capabilities=PROVIDES,
            state_variables=("district_net_kwh", "district_peak_kwh", "shared_meter_headroom_kwh"),
            action_types=tuple(f"{i}.{a}" for i in baseline["building_ids"] for a in PUBLIC_ACTIONS),
            manifest={
                "single_native_episode": True,
                "native_multi_building_transition": True,
                "external_shared_meter_constraint": baseline["shared_meter_constraint"],
                "coupling_gate": report["coupling_gate"],
                "provenance": baseline["provenance"],
                "probe": "d3-citylearn-multibuilding-probe-v1",
            },
        ),)


D3MultiBuildingCityLearnAdapter = D3CityLearnMultiBuildingAdapter
D3MultiBuildingAdapter = D3CityLearnMultiBuildingAdapter
