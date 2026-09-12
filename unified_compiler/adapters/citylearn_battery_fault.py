"""Backend-only CityLearn 2.5.0 battery health/fault route.

The route wraps the *real* CityLearn ``Battery.charge`` implementation used
by the existing battery/PV source route.  A frozen, agent-independent health
schedule changes only the command sent to that battery (or its native
capacity).  No responsibility, episode, query, evaluator, or threshold is
defined here.  The adapter advertises a process only after the independent
probe has certified deterministic replay and a healthy-vs-fault causal
counterfactual.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any, ClassVar, Iterator, Mapping, Sequence

from ..types import BackendCapability, CapabilityStatus, PhysicalProcess, ProcessRequirement

V11_ROOT = Path(__file__).resolve().parents[2]
PROTOTYPES_ROOT = V11_ROOT.parent
V5_ROOT = PROTOTYPES_ROOT / "v5_scenario_compiler"
V10_SITE_PACKAGES = (
    PROTOTYPES_ROOT / "v10_diversity_aware_compiler" / ".runtime" / "venv"
    / "lib" / "python3.13" / "site-packages"
)
SOURCE_CACHE = V5_ROOT / "source_cache"
SHARED_ASSETS = V11_ROOT / "shared_assets" / "citylearn_v2.5.0"
ASSET_MANIFEST = SHARED_ASSETS / "asset_manifest.json"
PV_SIZING = SHARED_ASSETS / "misc" / "lbl-tracking_the_sun-res-pv.csv"
BATTERY_SIZING = SHARED_ASSETS / "misc" / "battery_choices.yaml"
DEFAULT_BUILDING = "resstock-amy2018-2021-release-1-102040"
DEFAULT_START = 912
DEFAULT_HORIZON = 8
CITYLEARN_VERSION = "2.5.0"
PINNED_COMMIT = "29062af6d077409e1c37a3e53a6cac30fd4d02bc"
DEFAULT_REPLAY_GATE = V11_ROOT / "generated" / "d1_citylearn_battery_fault_replay_gate_v1.json"
DEFAULT_PROFILE_GATE_DIR = V11_ROOT / "generated" / "d1_citylearn_battery_fault_profiles_v1"

PROVIDES = frozenset({
    "storage.soc",
    "storage.charge_discharge_action",
    "storage.capacity_degradation",
    "storage.power_derating",
    "storage.unavailable",
    "storage.stuck",
    "building.net_electricity",
    "pv.generation_profile",
})
FAULT_MODES = ("capacity_degradation", "power_derating", "unavailable", "stuck")


class CityLearnBatteryFaultError(RuntimeError):
    """Malformed schedule, missing runtime/evidence, or failed replay."""


class BatteryFaultActionError(ValueError):
    """A command outside CityLearn's normalized battery action envelope."""


@dataclass(frozen=True)
class BatteryFaultWindow:
    """An exogenous health window using half-open ``[start_step, end_step)``."""

    start_step: int
    end_step: int
    mode: str
    factor: float | None = None
    taxonomy_ref: str = "CityLearn:Battery"

    def __post_init__(self) -> None:
        if (isinstance(self.start_step, bool) or not isinstance(self.start_step, int)
                or self.start_step < 0 or isinstance(self.end_step, bool)
                or not isinstance(self.end_step, int) or self.end_step <= self.start_step):
            raise CityLearnBatteryFaultError("fault interval must be non-empty non-negative integers")
        if self.mode not in FAULT_MODES:
            raise CityLearnBatteryFaultError(f"unsupported battery fault mode: {self.mode!r}")
        if self.mode in {"capacity_degradation", "power_derating"}:
            if isinstance(self.factor, bool) or not isinstance(self.factor, (int, float)):
                raise CityLearnBatteryFaultError(f"{self.mode} requires a numeric factor")
            if not math.isfinite(float(self.factor)) or not 0.0 < float(self.factor) < 1.0:
                raise CityLearnBatteryFaultError(f"{self.mode} factor must be in (0, 1)")
        elif self.factor not in (None, 0, 0.0, 1, 1.0):
            raise CityLearnBatteryFaultError(f"{self.mode} does not accept a factor")
        if not isinstance(self.taxonomy_ref, str) or not self.taxonomy_ref:
            raise CityLearnBatteryFaultError("taxonomy_ref must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_step": self.start_step,
            "end_step": self.end_step,
            "mode": self.mode,
            "factor": None if self.factor is None else float(self.factor),
            "taxonomy_ref": self.taxonomy_ref,
        }


@dataclass(frozen=True)
class BatteryHealthState:
    step_index: int
    active: bool
    mode: str
    factor: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "active": self.active,
            "mode": self.mode,
            "factor": self.factor,
        }


class BatteryFaultSchedule:
    """Canonical immutable schedule; future windows are not public state."""

    def __init__(self, windows: Sequence[BatteryFaultWindow] = ()) -> None:
        values = tuple(windows)
        if any(not isinstance(item, BatteryFaultWindow) for item in values):
            raise CityLearnBatteryFaultError("windows must contain BatteryFaultWindow values")
        ordered = tuple(sorted(values, key=lambda item: (item.start_step, item.end_step)))
        for previous, current in zip(ordered, ordered[1:]):
            if current.start_step < previous.end_step:
                raise CityLearnBatteryFaultError("fault windows must not overlap")
        self._windows = ordered
        canonical = tuple(item.as_dict() for item in ordered)
        self.schedule_id = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def windows(self) -> tuple[BatteryFaultWindow, ...]:
        return self._windows

    def state_at(self, step_index: int) -> BatteryHealthState:
        if isinstance(step_index, bool) or not isinstance(step_index, int) or step_index < 0:
            raise CityLearnBatteryFaultError("step_index must be a non-negative integer")
        for window in self._windows:
            if window.start_step <= step_index < window.end_step:
                factor = float(window.factor) if window.factor is not None else 0.0
                return BatteryHealthState(step_index, True, window.mode, factor)
            if step_index < window.start_step:
                break
        return BatteryHealthState(step_index, False, "healthy", 1.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "interval_convention": "half_open_start_inclusive_end_exclusive",
            "agent_can_modify": False,
            "windows": [item.as_dict() for item in self._windows],
        }


def battery_fault_profile(name: str) -> BatteryFaultSchedule:
    """Return a short deterministic mechanism preset (not a task/episode)."""
    if name not in FAULT_MODES:
        raise CityLearnBatteryFaultError(f"unknown profile {name!r}; choose from {FAULT_MODES}")
    factor = {"capacity_degradation": 0.55, "power_derating": 0.45}.get(name)
    return BatteryFaultSchedule((BatteryFaultWindow(2, 6, name, factor),))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise CityLearnBatteryFaultError(f"missing provenance file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_module() -> Any:
    if str(V10_SITE_PACKAGES) not in sys.path:
        sys.path.insert(0, str(V10_SITE_PACKAGES))
    bootstrap_path = V5_ROOT / "runtime_bootstrap.py"
    if not bootstrap_path.is_file():
        raise CityLearnBatteryFaultError(f"missing pinned runtime bootstrap: {bootstrap_path}")
    spec = importlib.util.spec_from_file_location("d1_citylearn_bootstrap", bootstrap_path)
    if spec is None or spec.loader is None:
        raise CityLearnBatteryFaultError("unable to load CityLearn runtime bootstrap")
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    try:
        bootstrap.ensure_citylearn_runtime()
    except Exception as exc:
        raise CityLearnBatteryFaultError(f"CityLearn runtime unavailable: {exc}") from exc
    replay_path = V11_ROOT / "probe_citylearn_battery_replay.py"
    replay_spec = importlib.util.spec_from_file_location("d1_citylearn_battery_replay", replay_path)
    if replay_spec is None or replay_spec.loader is None:
        raise CityLearnBatteryFaultError("unable to load native CityLearn battery route")
    replay = importlib.util.module_from_spec(replay_spec)
    replay_spec.loader.exec_module(replay)
    return replay


def _runtime_fingerprint() -> dict[str, Any]:
    _runtime_module()
    try:
        import citylearn  # type: ignore
        package = Path(citylearn.__file__).resolve().parent if citylearn.__file__ else None
    except ImportError as exc:
        raise CityLearnBatteryFaultError("CityLearn runtime package path unavailable") from exc
    if package is None:
        raise CityLearnBatteryFaultError("CityLearn runtime package path unavailable")
    files = [(str(path.relative_to(package)), _sha256(path)) for path in sorted(package.rglob("*.py"))]
    import importlib.metadata
    version = importlib.metadata.version("citylearn")
    if version != CITYLEARN_VERSION:
        raise CityLearnBatteryFaultError(f"expected CityLearn {CITYLEARN_VERSION}, got {version}")
    return {"citylearn_version": version, "citylearn_tag_commit": PINNED_COMMIT,
            "runtime_sha256": _digest(files), "runtime_file_count": len(files)}


def provenance(building_id: str) -> dict[str, Any]:
    if not ASSET_MANIFEST.is_file():
        raise CityLearnBatteryFaultError("missing CityLearn asset manifest")
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("tag") != f"v{CITYLEARN_VERSION}" or manifest.get("tag_commit") != PINNED_COMMIT:
        raise CityLearnBatteryFaultError("CityLearn assets are not pinned to v2.5.0")
    source = SOURCE_CACHE / f"{building_id}.csv"
    return {
        "backend": "CityLearn",
        "backend_version": CITYLEARN_VERSION,
        "citylearn_tag_commit": PINNED_COMMIT,
        "asset_manifest_sha256": _sha256(ASSET_MANIFEST),
        "source_trace_sha256": _sha256(source),
        "source_trace": str(source.relative_to(PROTOTYPES_ROOT)),
        "pv_catalog_sha256": _sha256(PV_SIZING),
        "battery_catalog_sha256": _sha256(BATTERY_SIZING),
    }


class CityLearnBatteryFaultEpisode:
    """Stateful battery-only replay over a native CityLearn Battery object."""

    def __init__(self, battery: Any, load: Sequence[float], solar: Sequence[float],
                 source_start: int, horizon: int, schedule: BatteryFaultSchedule,
                 process_id: str = "citylearn-d1-battery-fault") -> None:
        if horizon < 1 or source_start < 0 or source_start + horizon > len(load) or len(load) != len(solar):
            raise CityLearnBatteryFaultError("invalid source window")
        self.battery = battery
        self.load = load
        self.solar = solar
        self.source_start = source_start
        self.horizon = horizon
        self.schedule = schedule
        self.process_id = process_id
        self._step_index = 0
        self._done = True
        self._last_effective_action = 0.0
        self._nominal_capacity = 0.0
        self._capacity_factor = 1.0
        self._last_completed_native_index: int | None = None

    def reset(self, *, seed: int = 0) -> dict[str, Any]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise CityLearnBatteryFaultError("seed must be an integer")
        try:
            self.battery.reset()
            self._nominal_capacity = float(self.battery.capacity)
        except Exception as exc:
            raise CityLearnBatteryFaultError(f"native Battery reset failed: {exc}") from exc
        self._capacity_factor = 1.0
        self._step_index = 0
        self._done = False
        self._last_effective_action = 0.0
        self._last_completed_native_index = None
        return self._observation(self.schedule.state_at(0))

    def legal_actions(self) -> dict[str, Any]:
        """The normalized battery action envelope exposed to an Agent."""
        if self._done:
            raise CityLearnBatteryFaultError("reset() must be called before legal_actions()")
        return {
            "type": "battery_charge_discharge_rate",
            "minimum": -1.0,
            "maximum": 1.0,
            "units": "normalized battery action",
            "fault_schedule_agent_modifiable": False,
        }

    def observe(self) -> dict[str, Any]:
        if self._done:
            raise CityLearnBatteryFaultError("episode is done; call reset() first")
        return self._observation(self.schedule.state_at(self._step_index))

    def step(self, action: float) -> dict[str, Any]:
        if self._done:
            raise CityLearnBatteryFaultError("episode is done; call reset() first")
        if isinstance(action, bool) or not isinstance(action, (int, float)) or not math.isfinite(float(action)) or not -1.0 <= float(action) <= 1.0:
            raise BatteryFaultActionError("battery action must be finite and in [-1, 1]")
        requested = float(action)
        health = self.schedule.state_at(self._step_index)
        if health.mode == "capacity_degradation":
            self._capacity_factor = min(self._capacity_factor, health.factor)
            target = self._nominal_capacity * self._capacity_factor
            if float(self.battery.capacity) != target:
                # Battery.capacity is the native CityLearn capacity property;
                # changing it preserves normalized SoC while reducing energy.
                self.battery.capacity = target
        if health.mode == "unavailable":
            effective = 0.0
        elif health.mode == "stuck":
            effective = self._last_effective_action
        elif health.mode == "power_derating":
            effective = requested * health.factor
        else:
            effective = requested
        try:
            self.battery.charge(effective * float(self.battery.nominal_power))
            index = int(self.battery.time_step)
            storage = float(self.battery.electricity_consumption[index])
            soc = float(self.battery.soc[index])
            degraded_capacity = float(self.battery.degraded_capacity)
        except Exception as exc:
            raise CityLearnBatteryFaultError(f"native Battery transition failed: {exc}") from exc
        self._last_completed_native_index = index
        source_row = self.source_start + self._step_index
        load = float(self.load[source_row])
        solar = float(self.solar[source_row])
        record = {
            "step_index": self._step_index,
            "source_row": source_row,
            "requested_action": requested,
            "effective_action": effective,
            "fault": health.as_dict(),
            "observation": self._observation(health),
            "effect": {
                "battery_soc": soc,
                "battery_capacity_kwh": float(self.battery.capacity),
                "battery_degraded_capacity_kwh": degraded_capacity,
                "storage_electricity_kwh": storage,
                "non_shiftable_load_kwh": load,
                "solar_generation_kwh": solar,
                "net_electricity_kwh": load - solar + storage,
            },
        }
        self._last_effective_action = effective
        self._step_index += 1
        self._done = self._step_index >= self.horizon
        try:
            if not self._done:
                self.battery.next_time_step()
        except Exception as exc:
            raise CityLearnBatteryFaultError(f"native Battery time advance failed: {exc}") from exc
        # ``observe()`` is defined as the state available after this native
        # transition.  The old record contained the pre-advance row, which
        # made a step receipt disagree with the subsequent observation.
        record["observation"] = self._observation(self.schedule.state_at(self._step_index))
        record["episode_done"] = self._done
        return record

    def _observation(self, health: BatteryHealthState) -> dict[str, Any]:
        source_row = min(self.source_start + self._step_index, len(self.load) - 1)
        # next_time_step selects an unwritten native buffer slot. The public
        # decision state retains the last completed transition's SOC, while
        # exogenous load/health advance to the upcoming interval.
        soc_index = self._last_completed_native_index
        if soc_index is None:
            soc_index = getattr(self.battery, "time_step", None)
        return {
            "source_row": source_row,
            "battery_soc": float(self.battery.soc[soc_index]) if soc_index is not None else float(self.battery.initial_soc),
            "non_shiftable_load_kwh": float(self.load[source_row]),
            "solar_generation_kwh": float(self.solar[source_row]),
            "battery_health": health.as_dict(),
        }

    def private_state(self) -> dict[str, Any]:
        if self._done:
            raise CityLearnBatteryFaultError("reset() must be called before private_state()")
        return {
            "route": "citylearn_d1_battery_fault",
            "process_id": self.process_id,
            "step_index": self._step_index,
            "schedule": self.schedule.as_dict(),
            "nominal_capacity_kwh": self._nominal_capacity,
            "capacity_factor": self._capacity_factor,
            "last_effective_action": self._last_effective_action,
            "provenance": {"backend": "CityLearn", "backend_version": CITYLEARN_VERSION,
                           "fault_schedule_is_exogenous": True},
        }


def _make_episode(building_id: str, start: int, horizon: int, schedule: BatteryFaultSchedule) -> CityLearnBatteryFaultEpisode:
    try:
        replay = _runtime_module()
        with replay.shared_sizing_catalogs():
            runtime = replay.building_battery_runtime(building_id)
    except Exception as exc:
        if isinstance(exc, CityLearnBatteryFaultError):
            raise
        raise CityLearnBatteryFaultError(f"native CityLearn battery route unavailable: {exc}") from exc
    return CityLearnBatteryFaultEpisode(
        deepcopy(runtime["battery_template"]), runtime["load"], runtime["solar"],
        start, horizon, schedule,
        process_id=f"citylearn-d1-battery-fault://{building_id}/rows:{start}-{start+horizon-1}",
    )


def run_battery_fault(building_id: str = DEFAULT_BUILDING, start: int = DEFAULT_START,
                      horizon: int = DEFAULT_HORIZON, schedule: BatteryFaultSchedule | None = None,
                      actions: Sequence[float] | None = None) -> dict[str, Any]:
    schedule = schedule or BatteryFaultSchedule()
    if horizon < 2 or start < 0 or start + horizon > 8760:
        raise CityLearnBatteryFaultError("battery fault window must fit the annual source trace")
    if actions is None:
        actions = tuple(1.0 for _ in range(horizon))
    if len(actions) != horizon:
        raise CityLearnBatteryFaultError("action horizon does not match replay horizon")
    episode = _make_episode(building_id, start, horizon, schedule)
    initial = episode.reset()
    records = [episode.step(action) for action in actions]
    if not episode._done:
        raise CityLearnBatteryFaultError("battery fault replay did not terminate")
    return {
        "schema_version": "d1-citylearn-battery-fault-trajectory-v1",
        "backend": "CityLearn", "backend_version": CITYLEARN_VERSION,
        "building_id": building_id,
        "source_window": {"start": start, "end": start + horizon - 1, "horizon_steps": horizon},
        "schedule": schedule.as_dict(), "initial": initial, "records": records,
        "trajectory_sha256": _digest({"initial": initial, "records": records}),
        "provenance": {**provenance(building_id), **_runtime_fingerprint()},
        "deterministic": True, "terminated": True,
    }


class CityLearnBatteryFaultAdapter:
    """Fail-closed typed adapter for the verified backend-only route."""

    backend: ClassVar[str] = "citylearn_battery_d1_fault"

    def __init__(self, building_id: str = DEFAULT_BUILDING, start: int = DEFAULT_START,
                 horizon: int = DEFAULT_HORIZON, schedule: BatteryFaultSchedule | None = None,
                 profile: str | None = None, replay_gate_path: Path | str | None = None) -> None:
        if profile is not None and schedule is not None:
            raise CityLearnBatteryFaultError("profile cannot be combined with an explicit schedule")
        self.building_id, self.start, self.horizon = building_id, start, horizon
        self.schedule = battery_fault_profile(profile) if profile is not None else (schedule or BatteryFaultSchedule())
        self.profile = profile
        if replay_gate_path is None and profile is not None:
            replay_gate_path = DEFAULT_PROFILE_GATE_DIR / f"{profile}.json"
        self.replay_gate_path = Path(replay_gate_path or DEFAULT_REPLAY_GATE)
        self._process: PhysicalProcess | None = None
        self._episode: CityLearnBatteryFaultEpisode | None = None

    def _load_gate(self) -> dict[str, Any] | None:
        if not self.replay_gate_path.is_file():
            return None
        try:
            gate = json.loads(self.replay_gate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CityLearnBatteryFaultError(f"invalid D1 CityLearn replay gate: {exc}") from exc
        if not isinstance(gate, dict) or gate.get("schema_version") != "d1-citylearn-battery-fault-replay-gate-v1":
            raise CityLearnBatteryFaultError("unsupported D1 CityLearn replay gate")
        if gate.get("verified") is not True or gate.get("backend_version") != CITYLEARN_VERSION:
            return gate
        if gate.get("schedule_id") != self.schedule.schedule_id or gate.get("building_id") != self.building_id or gate.get("source_window", {}).get("start") != self.start:
            raise CityLearnBatteryFaultError("D1 CityLearn replay gate does not match route parameters")
        if gate.get("adapter_sha256") != _sha256(Path(__file__)):
            raise CityLearnBatteryFaultError("D1 CityLearn replay gate is stale for adapter source")
        required = ("deterministic_replay", "healthy_fault_counterfactual", "soc_divergence", "net_electricity_divergence", "provenance_complete")
        if any(gate.get(key) is not True for key in required):
            raise CityLearnBatteryFaultError("D1 CityLearn gate lacks causal backend checks")
        return gate

    def capabilities(self) -> Sequence[BackendCapability]:
        gate = self._load_gate()
        verified = gate is not None and gate.get("verified") is True
        return (BackendCapability(
            capability_id="citylearn_battery_d1_fault.health",
            backend=self.backend, provides=tuple(sorted(PROVIDES)),
            status=(CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED if verified else CapabilityStatus.DATA_PROBED_PENDING_REPLAY),
            evidence=(str(self.replay_gate_path.relative_to(V11_ROOT)) if self.replay_gate_path.is_relative_to(V11_ROOT) else str(self.replay_gate_path),),
            metadata={"profile": self.profile, "schedule_id": self.schedule.schedule_id, "fault_modes": sorted({w.mode for w in self.schedule.windows}), "citylearn_version": CITYLEARN_VERSION},
        ),)

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        gate = self._load_gate()
        if gate is None or gate.get("verified") is not True or not requirements:
            return ()
        if not any(req.required_capabilities <= PROVIDES for req in requirements):
            return ()
        if self._process is None:
            source_hash = gate.get("provenance", {}).get("source_trace_sha256")
            self._process = PhysicalProcess(
                process_id=f"citylearn-d1-battery-fault://{self.building_id}/rows:{self.start}-{self.start+self.horizon-1}",
                domain="electrical_storage_fault",
                backend=self.backend, backend_version=CITYLEARN_VERSION,
                source_id=f"{self.building_id}:{self.start}-{self.start+self.horizon-1}",
                source_hash=f"sha256:{source_hash}", horizon_steps=self.horizon,
                observation_interval_seconds=3600.0, provided_capabilities=PROVIDES,
                state_variables=("battery_soc", "battery_capacity_kwh", "net_electricity_kwh"),
                action_types=("charge_discharge_rate",),
                manifest={"route": "citylearn_d1_battery_fault", "schedule": self.schedule.as_dict(), "replay_gate": str(self.replay_gate_path.relative_to(V11_ROOT)) if self.replay_gate_path.is_relative_to(V11_ROOT) else str(self.replay_gate_path), "provenance": gate.get("provenance", {}), "gold_actions_released": False},
            )
        return (self._process,)

    def open_episode(self) -> CityLearnBatteryFaultEpisode:
        """Open the verified native CityLearn battery trajectory."""
        gate = self._load_gate()
        if gate is None or gate.get("verified") is not True:
            raise CityLearnBatteryFaultError("D1 CityLearn replay gate is not verified; refusing to open episode")
        return _make_episode(self.building_id, self.start, self.horizon, self.schedule)

    # Keep the convenient Agent-facing lifecycle on the adapter as well.  The
    # episode object remains the owner of native state and fault schedule.
    def reset(self, *, seed: int = 0) -> dict[str, Any]:
        if self._episode is None:
            self._episode = self.open_episode()
        return self._episode.reset(seed=seed)

    def observe(self) -> dict[str, Any]:
        if self._episode is None:
            raise CityLearnBatteryFaultError("reset() must be called before observe()")
        return self._episode.observe()

    def legal_actions(self) -> dict[str, Any]:
        if self._episode is None:
            raise CityLearnBatteryFaultError("reset() must be called before legal_actions()")
        return self._episode.legal_actions()

    def step(self, action: float) -> dict[str, Any]:
        if self._episode is None:
            raise CityLearnBatteryFaultError("reset() must be called before step()")
        return self._episode.step(action)


__all__ = ["BatteryFaultWindow", "BatteryFaultSchedule", "BatteryHealthState", "battery_fault_profile", "CityLearnBatteryFaultEpisode", "CityLearnBatteryFaultAdapter", "CityLearnBatteryFaultError", "BatteryFaultActionError", "FAULT_MODES", "PROVIDES", "run_battery_fault"]
