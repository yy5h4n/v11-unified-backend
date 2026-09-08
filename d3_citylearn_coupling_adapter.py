#!/usr/bin/env python3
"""D3: one native CityLearn episode coupling HVAC, PV, battery and net power.

This module intentionally does not use the existing HVAC-only or battery-only
routes.  ``run_coupled_episode`` creates one CityLearnEnv with both action
channels enabled and records the resulting coupled trajectory.  The LSTM
warm-up is private initialization; the returned trajectory is the complete
public episode requested by the caller.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from collections.abc import Mapping
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sys
from typing import Any, Iterator, Sequence

ROOT = Path(__file__).resolve().parent
V5_ROOT = ROOT.parent / "v5_scenario_compiler"
SOURCE_CACHE = V5_ROOT / "source_cache"
SHARED_ASSETS = ROOT / "shared_assets" / "citylearn_v2.5.0"
ASSET_MANIFEST = SHARED_ASSETS / "asset_manifest.json"
DEFAULT_BUILDING = "resstock-amy2018-2021-release-1-102040"
DEFAULT_START = 912
DEFAULT_HORIZON = 24
LSTM_WARMUP_HOURS = 14
CITYLEARN_VERSION = "2.5.0"

OBSERVATIONS = (
    "hour",
    "month",
    "outdoor_dry_bulb_temperature",
    "occupant_count",
    "indoor_dry_bulb_temperature",
    "indoor_dry_bulb_temperature_cooling_set_point",
    "indoor_dry_bulb_temperature_heating_set_point",
    "cooling_demand",
    "heating_demand",
    "cooling_electricity_consumption",
    "heating_electricity_consumption",
    "non_shiftable_load",
    "solar_generation",
    "electrical_storage_soc",
    "electrical_storage_electricity_consumption",
    "net_electricity_consumption",
)
PROVIDES = frozenset(
    {
        "weather.outdoor_temperature",
        "occupancy.count",
        "thermal.zone_temperature",
        "thermal.hvac_action",
        "storage.soc",
        "storage.charge_discharge_action",
        "pv.generation_profile",
        "building.net_electricity",
    }
)


class D3CouplingError(RuntimeError):
    """Fail-closed error for missing, incompatible, or non-causal D3 runs."""


class D3ActionError(ValueError):
    """An Agent action is not a valid CityLearn native action."""


class D3CityLearnAgentRoute:
    """Persistent, native CityLearn route for an Agent-facing D3 episode.

    ``run_coupled_episode`` above is deliberately retained as the historical
    replay/evidence API.  This class is the online boundary: one
    ``CityLearnEnv`` is created by ``reset`` and every ``step`` forwards the
    supplied two-channel action to that same native environment.  The private
    LSTM warm-up only positions the native simulator at the requested source
    row; it is never repeated for a public step.
    """

    # These are the names on the Agent boundary.  CityLearn uses the native
    # names below and expects them in this exact order in ``env.step``.
    public_action_names = ("battery_rate", "hvac_rate")
    action_names = ("electrical_storage", "cooling_or_heating_device")
    tick_seconds = 3600.0

    def __init__(
        self,
        building_id: str = DEFAULT_BUILDING,
        start: int = DEFAULT_START,
        horizon: int = DEFAULT_HORIZON,
        *,
        warmup_steps: int = LSTM_WARMUP_HOURS,
    ) -> None:
        if not isinstance(building_id, str) or not building_id:
            raise D3CouplingError("building_id must be a non-empty string")
        if isinstance(start, bool) or not isinstance(start, int):
            raise D3CouplingError("start must be an integer")
        if isinstance(horizon, bool) or not isinstance(horizon, int):
            raise D3CouplingError("horizon must be an integer")
        if isinstance(warmup_steps, bool) or not isinstance(warmup_steps, int) or warmup_steps < 0:
            raise D3CouplingError("warmup_steps must be a non-negative integer")
        if start < warmup_steps or horizon < 1 or start + horizon > 8760:
            raise D3CouplingError("D3 Agent episode window is outside the annual source trace")
        self.building_id = building_id
        self.start = start
        self.horizon = horizon
        self.warmup_steps = warmup_steps
        self._env: Any | None = None
        self._latest_observation: dict[str, float] | None = None
        self._steps = 0
        self._done = False
        self._seed: int | None = None

    @property
    def env(self) -> Any:
        if self._env is None:
            raise D3CouplingError("reset(seed) must be called before using the D3 Agent route")
        return self._env

    def _new_env(self) -> Any:
        CityLearnEnv, DataSet, pd, yaml = _load_data_prerequisites()
        # Keep the initialization path identical to the verified replay
        # route.  Sizing data is consumed while CityLearnEnv is constructed;
        # the resulting native environment remains the sole simulator for
        # the whole live episode.
        with _sizing_catalogs(DataSet, pd, yaml):
            return CityLearnEnv(
                coupled_schema(self.building_id),
                root_directory=SOURCE_CACHE,
                simulation_start_time_step=self.start - self.warmup_steps,
                simulation_end_time_step=self.start + self.horizon,
                central_agent=True,
                active_actions=list(self.action_names),
                active_observations=list(OBSERVATIONS),
                render_mode="none",
            )

    def reset(self, seed: int = 0) -> dict[str, float]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise D3ActionError("reset seed must be an integer")
        self.close()
        env = self._new_env()
        try:
            observation, _ = env.reset(seed=seed)
            for _ in range(self.warmup_steps):
                _clear_reset_preview(env)
                warmup_hvac = _demand_following_hvac(env)
                observation, _, terminated, truncated, _ = env.step([[0.0, warmup_hvac]])
                if terminated or truncated:
                    raise D3CouplingError("native CityLearn terminated during D3 Agent warm-up")
        except Exception:
            close = getattr(env, "close", None)
            if close is not None:
                close()
            raise
        self._env = env
        self._seed = seed
        self._steps = 0
        self._done = False
        self._latest_observation = _obs(env, observation)
        return deepcopy(self._latest_observation)

    def observe(self) -> dict[str, float]:
        if self._latest_observation is None:
            raise D3CouplingError("reset(seed) must be called before observe()")
        return deepcopy(self._latest_observation)

    def legal_actions(self) -> dict[str, Any]:
        env = self.env
        spaces = getattr(env, "action_space", None)
        if not isinstance(spaces, (list, tuple)) or not spaces:
            raise D3CouplingError("CityLearn native action_space is unavailable")
        space = spaces[0]
        low = getattr(space, "low", None)
        high = getattr(space, "high", None)
        shape = getattr(space, "shape", None)
        if low is None or high is None or shape is None:
            raise D3CouplingError("CityLearn native action space is not a bounded Box")
        lows = [float(value) for value in low]
        highs = [float(value) for value in high]
        if len(lows) != len(self.action_names) or len(highs) != len(self.action_names):
            raise D3CouplingError("native action space does not match D3 channels")
        return {
            "type": "citylearn_native_action",
            "native": True,
            "shape": list(shape),
            "channels": {
                name: {
                    "index": index,
                    "type": "continuous",
                    "range": [lows[index], highs[index]],
                }
                for index, name in enumerate(self.public_action_names)
            },
            "native_action_names": list(self.action_names),
            "public_action_names": list(self.public_action_names),
        }

    @staticmethod
    def _number(value: Any, channel: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise D3ActionError(f"{channel} action must be a finite number")
        result = float(value)
        if not math.isfinite(result) or result < -1.0 or result > 1.0:
            raise D3ActionError(f"{channel} action must be in native [-1, 1]")
        return result

    def _native_action(self, action: Any) -> list[list[float]]:
        if not isinstance(action, Mapping) or set(action) != set(self.public_action_names):
            raise D3ActionError("D3 action must contain exactly battery_rate and hvac_rate")
        values = [self._number(action[name], name) for name in self.public_action_names]
        return [values]

    def _effect(self, index: int) -> dict[str, float]:
        building = self.env.buildings[0]
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

    def step(self, action: Any, dt_seconds: float = tick_seconds) -> dict[str, Any]:
        if self._latest_observation is None:
            raise D3CouplingError("reset(seed) must be called before step()")
        if self._done:
            raise D3ActionError("D3 native episode is already done")
        if isinstance(dt_seconds, bool) or not isinstance(dt_seconds, (int, float)) or not math.isfinite(float(dt_seconds)) or float(dt_seconds) != self.tick_seconds:
            raise D3ActionError(f"D3 route requires dt_seconds={self.tick_seconds:g}")
        native_action = self._native_action(action)
        env = self.env
        index = env.time_step
        try:
            observation, _, terminated, truncated, native_info = env.step(native_action)
        except (ValueError, TypeError) as exc:
            raise D3ActionError(str(exc)) from exc
        self._steps += 1
        self._latest_observation = _obs(env, observation)
        self._done = bool(terminated or truncated)
        effect = self._effect(index)
        action_receipt = {
            name: native_action[0][i]
            for i, name in enumerate(self.public_action_names)
        }
        return {
            "time_seconds": self._steps * self.tick_seconds,
            "observation": deepcopy(self._latest_observation),
            "action": action_receipt,
            "effect": deepcopy(effect),
            "done": self._done,
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "delta_t_seconds": self.tick_seconds,
            "info": {
                "native_action_names": list(self.action_names),
                "native_action": native_action,
                "public_action_names": list(self.public_action_names),
                "source_row": self.start + self._steps - 1,
                "effect": effect,
                "native_info": deepcopy(native_info) if isinstance(native_info, Mapping) else {},
                "single_native_episode": True,
            },
        }

    def close(self) -> None:
        if self._env is not None:
            close = getattr(self._env, "close", None)
            if close is not None:
                close()
        self._env = None
        self._latest_observation = None
        self._steps = 0
        self._done = False



def _sha256(path: Path) -> str:
    if not path.is_file():
        raise D3CouplingError(f"missing provenance file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _runtime_fingerprint() -> dict[str, Any]:
    try:
        import citylearn  # type: ignore
        version = importlib.metadata.version("citylearn")
        package_root = Path(citylearn.__file__).resolve().parent
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        raise D3CouplingError("CityLearn 2.5.0 runtime is unavailable") from exc
    if version != CITYLEARN_VERSION:
        raise D3CouplingError(f"CityLearn version mismatch: expected {CITYLEARN_VERSION}, got {version}")
    files = []
    for path in sorted(package_root.rglob("*.py")):
        files.append((str(path.relative_to(package_root)), _sha256(path)))
    return {
        "citylearn_version": version,
        "python": platform.python_version(),
        "runtime_package": "citylearn",
        "runtime_sha256": _json_digest(files),
        "runtime_file_count": len(files),
    }


def _require_assets(building_id: str) -> dict[str, Any]:
    required = (
        SOURCE_CACHE / "schema.json",
        SOURCE_CACHE / f"{building_id}.csv",
        SOURCE_CACHE / f"{building_id}.pth",
        SHARED_ASSETS / "misc" / "battery_choices.yaml",
        SHARED_ASSETS / "misc" / "lbl-tracking_the_sun-res-pv.csv",
        SHARED_ASSETS / "dataset" / "weather.epw",
        ASSET_MANIFEST,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise D3CouplingError(f"missing CityLearn D3 assets: {missing}")
    try:
        manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise D3CouplingError("invalid CityLearn asset manifest") from exc
    if manifest.get("tag") != f"v{CITYLEARN_VERSION}" or not manifest.get("tag_commit"):
        raise D3CouplingError("asset manifest is not pinned to CityLearn v2.5.0")
    return {
        "citylearn_tag": f"v{CITYLEARN_VERSION}",
        "citylearn_tag_commit": manifest["tag_commit"],
        "asset_manifest_sha256": _sha256(ASSET_MANIFEST),
        "source_schema_sha256": _sha256(SOURCE_CACHE / "schema.json"),
        "source_trace_sha256": _sha256(SOURCE_CACHE / f"{building_id}.csv"),
        "thermal_model_sha256": _sha256(SOURCE_CACHE / f"{building_id}.pth"),
        "weather_sha256": _sha256(SHARED_ASSETS / "dataset" / "weather.epw"),
        "pv_catalog_sha256": _sha256(SHARED_ASSETS / "misc" / "lbl-tracking_the_sun-res-pv.csv"),
        "battery_catalog_sha256": _sha256(SHARED_ASSETS / "misc" / "battery_choices.yaml"),
    }


def _load_data_prerequisites() -> tuple[Any, Any, Any, Any]:
    # Keep the adapter usable from both the v10 venv and the repository's
    # system Python.  The pinned v5 bootstrap resolves CityLearn itself.
    shared_site_packages = (ROOT / "shared_runtime" / "site-packages").resolve()
    if str(shared_site_packages) not in sys.path:
        sys.path.insert(0, str(shared_site_packages))
    bootstrap = V5_ROOT / "runtime_bootstrap.py"
    if not bootstrap.is_file():
        raise D3CouplingError(f"missing pinned runtime bootstrap: {bootstrap}")
    import importlib.util
    spec = importlib.util.spec_from_file_location("d3_citylearn_runtime_bootstrap", bootstrap)
    if spec is None or spec.loader is None:
        raise D3CouplingError("unable to load CityLearn runtime bootstrap")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ensure_citylearn_runtime()
    from citylearn.citylearn import CityLearnEnv  # type: ignore
    from citylearn.data import DataSet  # type: ignore
    import pandas as pd
    import yaml
    return CityLearnEnv, DataSet, pd, yaml


@contextmanager
def _sizing_catalogs(DataSet: Any, pd: Any, yaml: Any) -> Iterator[None]:
    pv_path = SHARED_ASSETS / "misc" / "lbl-tracking_the_sun-res-pv.csv"
    battery_path = SHARED_ASSETS / "misc" / "battery_choices.yaml"
    pv = pd.read_csv(pv_path, low_memory=False)
    raw = yaml.safe_load(battery_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise D3CouplingError("battery sizing catalog is empty")
    rows = []
    for model, record in raw.items():
        if not isinstance(record, dict) or not isinstance(record.get("attributes"), dict):
            raise D3CouplingError("battery sizing catalog contains malformed records")
        rows.append({"model": model, **record["attributes"]})
    battery = pd.DataFrame(rows).set_index("model")
    old_pv, old_battery = DataSet.get_pv_sizing_data, DataSet.get_battery_sizing_data
    DataSet.get_pv_sizing_data = lambda self: pv.copy()  # type: ignore[method-assign]
    DataSet.get_battery_sizing_data = lambda self: battery.copy()  # type: ignore[method-assign]
    try:
        yield
    finally:
        DataSet.get_pv_sizing_data = old_pv
        DataSet.get_battery_sizing_data = old_battery


def coupled_schema(building_id: str) -> dict[str, Any]:
    assets = _require_assets(building_id)
    try:
        schema = json.loads((SOURCE_CACHE / "schema.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise D3CouplingError("invalid CityLearn source schema") from exc
    if building_id not in schema.get("buildings", {}):
        raise D3CouplingError(f"unknown CityLearn building: {building_id}")
    schema = deepcopy(schema)
    schema["buildings"] = {building_id: schema["buildings"][building_id]}
    building = schema["buildings"][building_id]
    if not isinstance(building.get("electrical_storage"), dict) or not isinstance(building.get("pv"), dict):
        raise D3CouplingError("D3 requires both electrical_storage and pv in the native schema")
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
            raise D3CouplingError(f"native schema lacks D3 observation: {name}")
        schema["observations"][name]["active"] = True
    # Keep asset hashes attached to the in-memory native schema for diagnostics.
    schema["_d3_provenance"] = assets
    return schema


def _clear_reset_preview(env: Any) -> None:
    """Clear CityLearn 2.5 reset's device-accounting preview exactly once."""
    if env.time_step != 0:
        return
    building = env.buildings[0]
    devices = [
        building.cooling_device,
        building.heating_device,
        building.dhw_device,
        building._Building__non_shiftable_load_device,
    ]
    for device in devices:
        device._ElectricDevice__electricity_consumption[env.time_step] = 0.0


def _demand_following_hvac(env: Any) -> float:
    building = env.buildings[0]
    index = env.time_step
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


def _obs(env: Any, observation: list[list[float]]) -> dict[str, float]:
    return {name: float(value) for name, value in zip(env.observation_names[0], observation[0])}


def run_coupled_episode(
    building_id: str = DEFAULT_BUILDING,
    start: int = DEFAULT_START,
    horizon: int = DEFAULT_HORIZON,
    *,
    battery_action: float = 0.0,
    hvac_policy: str = "maintain",
) -> dict[str, Any]:
    """Run one complete public episode in one native CityLearnEnv instance."""
    if start < LSTM_WARMUP_HOURS or horizon < 2 or start + horizon > 8760:
        raise D3CouplingError("D3 episode window is outside the annual source trace")
    if not -1.0 <= battery_action <= 1.0:
        raise D3CouplingError("battery action outside native [-1, 1]")
    if hvac_policy not in {"maintain", "off"}:
        raise D3CouplingError(f"unknown HVAC policy: {hvac_policy}")
    CityLearnEnv, DataSet, pd, yaml = _load_data_prerequisites()
    with _sizing_catalogs(DataSet, pd, yaml):
        env = CityLearnEnv(
            coupled_schema(building_id),
            root_directory=SOURCE_CACHE,
            simulation_start_time_step=start - LSTM_WARMUP_HOURS,
            simulation_end_time_step=start + horizon,
            central_agent=True,
            active_actions=["electrical_storage", "cooling_or_heating_device"],
            active_observations=list(OBSERVATIONS),
            render_mode="none",
        )
        observation, _ = env.reset()
        for _ in range(LSTM_WARMUP_HOURS):
            _clear_reset_preview(env)
            warmup_hvac = _demand_following_hvac(env)
            observation, _, terminated, truncated, _ = env.step([[0.0, warmup_hvac]])
            if terminated or truncated:
                raise D3CouplingError("native CityLearn terminated during D3 LSTM warm-up")
        records: list[dict[str, Any]] = []
        for _ in range(horizon):
            source_row = start + len(records)
            index = env.time_step
            public_observation = _obs(env, observation)
            hvac_action = 0.0 if hvac_policy == "off" else _demand_following_hvac(env)
            observation, _, terminated, truncated, _ = env.step([[battery_action, hvac_action]])
            building = env.buildings[0]
            load = float(building.non_shiftable_load[index])
            # CityLearn exposes solar_generation with grid-exchange polarity
            # (generation is negative); D3 evidence reports generation as a
            # positive kWh quantity and applies the sign in the identity.
            solar = -float(building.solar_generation[index])
            cooling = float(building.cooling_device.electricity_consumption[index])
            heating = float(building.heating_device.electricity_consumption[index])
            dhw = float(building.dhw_device.electricity_consumption[index])
            storage = float(building.electrical_storage_electricity_consumption[index])
            net = float(building.net_electricity_consumption[index])
            identity = net - (load + cooling + heating + dhw + storage - solar)
            records.append(
                {
                    "source_row": source_row,
                    "observation": public_observation,
                    "action": {"battery_rate": float(battery_action), "hvac_rate": float(hvac_action)},
                    "effect": {
                        "indoor_temperature_c": float(building.indoor_dry_bulb_temperature[index]),
                        "battery_soc": float(building.electrical_storage.soc[index]),
                        "non_shiftable_load_kwh": load,
                        "solar_generation_kwh": solar,
                        "cooling_electricity_kwh": cooling,
                        "heating_electricity_kwh": heating,
                        "hvac_electricity_kwh": cooling + heating,
                        "dhw_electricity_kwh": dhw,
                        "storage_electricity_kwh": storage,
                        "net_electricity_kwh": net,
                        "net_energy_balance_error_kwh": identity,
                    },
                    "backend_terminated": bool(terminated or truncated),
                }
            )
        if not env.terminated and not env.truncated:
            raise D3CouplingError("native CityLearn did not terminate after full public episode")
    assets = _require_assets(building_id)
    runtime = _runtime_fingerprint()
    return {
        "schema_version": "d3-citylearn-coupled-episode-v1",
        "backend": "CityLearn",
        "backend_version": CITYLEARN_VERSION,
        "building_id": building_id,
        "source_window": {"start": start, "end": start + horizon - 1, "horizon_steps": horizon},
        "private_warmup_steps": LSTM_WARMUP_HOURS,
        "single_native_episode": True,
        "action_sensitive_channels": ["electrical_storage", "cooling_or_heating_device"],
        "hvac_policy": hvac_policy,
        "battery_action": battery_action,
        "deterministic": True,
        "terminated": True,
        "records": records,
        "trajectory_sha256": _json_digest(records),
        "maximum_net_energy_balance_error_kwh": max(abs(r["effect"]["net_energy_balance_error_kwh"]) for r in records),
        "provenance": {**assets, **runtime},
    }


def probe_coupling(
    building_id: str = DEFAULT_BUILDING,
    start: int = DEFAULT_START,
    horizon: int = DEFAULT_HORIZON,
    *,
    condition_start: int = 3408,
) -> dict[str, Any]:
    """Build deterministic and causal contrasts, failing closed on any gap."""
    idle_a = run_coupled_episode(building_id, start, horizon, battery_action=0.0, hvac_policy="maintain")
    idle_b = run_coupled_episode(building_id, start, horizon, battery_action=0.0, hvac_policy="maintain")
    charge = run_coupled_episode(building_id, start, horizon, battery_action=1.0, hvac_policy="maintain")
    hvac_off = run_coupled_episode(building_id, start, horizon, battery_action=0.0, hvac_policy="off")
    condition = run_coupled_episode(building_id, condition_start, horizon, battery_action=0.0, hvac_policy="maintain")
    if idle_a["records"] != idle_b["records"]:
        raise D3CouplingError("D3 deterministic reset/replay mismatch")
    battery_delta = max(abs(a["effect"]["battery_soc"] - b["effect"]["battery_soc"]) for a, b in zip(idle_a["records"], charge["records"]))
    net_delta = max(abs(a["effect"]["net_electricity_kwh"] - b["effect"]["net_electricity_kwh"]) for a, b in zip(idle_a["records"], charge["records"]))
    thermal_delta = max(abs(a["effect"]["indoor_temperature_c"] - b["effect"]["indoor_temperature_c"]) for a, b in zip(idle_a["records"], hvac_off["records"]))
    if battery_delta <= 1e-9 or net_delta <= 1e-9 or thermal_delta <= 1e-9:
        raise D3CouplingError(f"D3 action-insensitive native route: battery={battery_delta}, net={net_delta}, thermal={thermal_delta}")
    if any(r["backend_terminated"] for r in idle_a["records"][:-1]) or not idle_a["records"][-1]["backend_terminated"]:
        raise D3CouplingError("D3 termination boundary is not exactly the full episode")
    if idle_a["maximum_net_energy_balance_error_kwh"] > 1e-5:
        raise D3CouplingError("D3 net-electricity coupling identity failed")
    left = idle_a["records"]
    right = condition["records"]
    condition_deltas = {
        "weather_outdoor_temperature_c": max(abs(a["observation"]["outdoor_dry_bulb_temperature"] - b["observation"]["outdoor_dry_bulb_temperature"]) for a, b in zip(left, right)),
        "occupancy_count": max(abs(a["observation"]["occupant_count"] - b["observation"]["occupant_count"]) for a, b in zip(left, right)),
        "non_shiftable_load_kwh": max(abs(a["effect"]["non_shiftable_load_kwh"] - b["effect"]["non_shiftable_load_kwh"]) for a, b in zip(left, right)),
        "pv_generation_kwh": max(abs(a["effect"]["solar_generation_kwh"] - b["effect"]["solar_generation_kwh"]) for a, b in zip(left, right)),
    }
    if not all(delta > 1e-9 for delta in condition_deltas.values()):
        raise D3CouplingError(f"D3 exogenous condition contrast is incomplete: {condition_deltas}")
    return {
        "schema_version": "d3-citylearn-coupling-probe-v1",
        "passed": True,
        "single_native_episode": True,
        "full_episode_steps": horizon,
        "deterministic_replay": True,
        "battery_soc_delta": battery_delta,
        "net_electricity_delta": net_delta,
        "hvac_temperature_delta_c": thermal_delta,
        "net_energy_balance_error_kwh": idle_a["maximum_net_energy_balance_error_kwh"],
        "exogenous_condition_contrast": {
            "baseline_start": start,
            "condition_start": condition_start,
            "deltas": condition_deltas,
            "strategy_conditions_changed": True,
            "condition_trajectory_sha256": condition["trajectory_sha256"],
        },
        "baseline": idle_a,
        "causal_battery_contrast": charge,
        "causal_hvac_contrast": hvac_off,
    }


class D3CoupledCityLearnAdapter:
    """Minimal typed adapter over the validated D3 native route."""

    backend = "citylearn_d3_coupled"

    def __init__(self, **kwargs: Any):
        self.kwargs = kwargs
        self._report: dict[str, Any] | None = None

    def _ensure(self) -> dict[str, Any]:
        if self._report is None:
            self._report = probe_coupling(**self.kwargs)
        return self._report

    def capabilities(self) -> tuple[Any, ...]:
        from unified_compiler.types import BackendCapability, CapabilityStatus
        report = self._ensure()
        return (BackendCapability(
            capability_id="citylearn_d3.coupled_building_energy",
            backend=self.backend,
            provides=tuple(sorted(PROVIDES)),
            status=CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED,
            evidence=("probe_d3_citylearn_coupling.py",),
            metadata={"horizon_steps": report["full_episode_steps"], "single_native_episode": True},
        ),)

    def scan(self, requirements: Sequence[Any]) -> tuple[Any, ...]:
        from unified_compiler.types import PhysicalProcess
        report = self._ensure()
        if not requirements or not any(req.required_capabilities <= PROVIDES for req in requirements):
            return ()
        baseline = report["baseline"]
        return (PhysicalProcess(
            process_id=f"citylearn-d3://{baseline['building_id']}/rows:{baseline['source_window']['start']}-{baseline['source_window']['end']}",
            domain="building_energy_coupling",
            backend=self.backend,
            backend_version=CITYLEARN_VERSION,
            source_id=f"{baseline['building_id']}:{baseline['source_window']['start']}-{baseline['source_window']['end']}",
            source_hash=f"sha256:{baseline['provenance']['source_trace_sha256']}",
            horizon_steps=baseline["source_window"]["horizon_steps"],
            observation_interval_seconds=3600.0,
            provided_capabilities=PROVIDES,
            state_variables=("indoor_temperature_c", "battery_soc", "net_electricity_kwh"),
            action_types=("battery_rate", "hvac_rate"),
            manifest={"single_native_episode": True, "provenance": baseline["provenance"], "probe": "d3-citylearn-coupling-probe-v1"},
        ),)


# Descriptive aliases keep the D3-only module convenient to consume without
# adding exports to the shared adapter registry.
D3CityLearnCouplingAdapter = D3CoupledCityLearnAdapter
CityLearnD3CouplingAdapter = D3CoupledCityLearnAdapter


if __name__ == "__main__":
    print(json.dumps(probe_coupling(), indent=2, sort_keys=True))
