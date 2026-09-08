"""D1: replay-verified actuator and public-sensor faults for SustainGym.

This module is deliberately a composition layer.  SustainGym remains the
only source of thermal transitions; the D1 layer only applies a frozen,
agent-independent actuator health schedule before forwarding an action.
SmartBench is useful for naming fault modes, but is not imported, replayed, or
treated as an interactive backend here.

The public route is fail-closed in two places:

* malformed/overlapping schedules are rejected at construction time;
* a process is not advertised by ``scan`` unless a D1 replay gate binds the
  exact adapter source and schedule and certifies deterministic replay,
  action sensitivity, and a healthy-vs-fault counterfactual.

Fault windows use half-open intervals ``[start_step, end_step)``.  All six
taxonomy modes are exposed only after the accompanying probe has produced
replay evidence against the pinned SustainGym runtime.  Actuator modes alter
the action sent to the real backend; sensor modes alter only public
observations and never the backend latent state.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from ..types import (
    BackendCapability,
    CapabilityStatus,
    PhysicalProcess,
    PhysicalTopology,
    ProcessRequirement,
    ResponsibilityLifecycle,
)
from .sustaingym_building import (
    PINNED_COMMIT,
    SustainGymAdapterError,
    SustainGymBuildingAdapter,
)

D1_PROFILE_NAMES = ("degraded", "failed", "stuck", "intermittent_dropout", "bias", "drift")

V11_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPLAY_GATE = V11_ROOT / "generated" / "d1_fault_replay_gate_v1.json"
_SHA256_RE = frozenset("0123456789abcdef")
_D1_CAPABILITIES = frozenset(
    {
        "thermal.zone_temperature",
        "thermal.hvac_cooling_action",
        "weather.exogenous",
        "occupancy.exogenous",
        "actuator.failure_schedule",
    }
)

class D1FaultError(RuntimeError):
    """D1 schedule, replay-gate, or lifecycle violation (always fail closed)."""


class D1ActionError(ValueError):
    """The requested actuator action is outside the public base envelope."""


@dataclass(frozen=True)
class ActuatorFaultWindow:
    """One exogenous actuator-health interval, active on a half-open range."""

    start_step: int
    end_step: int
    mode: str
    gain: float | None = None
    taxonomy_ref: str = "SmartBench:actuator_fault_taxonomy"

    def __post_init__(self) -> None:
        if (
            isinstance(self.start_step, bool)
            or not isinstance(self.start_step, int)
            or self.start_step < 0
        ):
            raise D1FaultError("fault start_step must be a non-negative integer")
        if (
            isinstance(self.end_step, bool)
            or not isinstance(self.end_step, int)
            or self.end_step <= self.start_step
        ):
            raise D1FaultError("fault end_step must be an integer greater than start_step")
        if self.mode not in D1_PROFILE_NAMES[:4]:
            raise D1FaultError(f"unsupported actuator fault mode {self.mode!r}")
        if self.gain is not None and (not math.isfinite(float(self.gain)) or not 0.0 <= float(self.gain) <= 1.0):
            raise D1FaultError("actuator gain must be finite and in [0, 1]")
        if self.mode == "degraded" and self.gain in (None, 0, 1):
            raise D1FaultError("degraded actuator requires 0 < gain < 1")
        if self.mode == "failed" and self.gain not in (None, 0, 0.0):
            raise D1FaultError("failed actuator must have gain=0")
        if not isinstance(self.taxonomy_ref, str) or not self.taxonomy_ref:
            raise D1FaultError("taxonomy_ref must be a non-empty string")

    @property
    def effective_gain(self) -> float:
        if self.mode == "degraded":
            return float(self.gain if self.gain is not None else 0.5)
        if self.mode == "failed":
            return 0.0
        return 1.0

    def gain_at(self, step_index: int) -> float:
        """Return deterministic gain for an actuator window."""
        if self.mode == "intermittent_dropout":
            elapsed = step_index - self.start_step
            return 0.0 if elapsed % 2 == 0 else 1.0
        return self.effective_gain

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_step": self.start_step,
            "end_step": self.end_step,
            "mode": self.mode,
            "gain": self.effective_gain,
            "taxonomy_ref": self.taxonomy_ref,
        }


@dataclass(frozen=True)
class FaultState:
    """Observable health state at the current action index."""

    step_index: int
    active: bool
    mode: str
    gain: float
    dropout_active: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "active": self.active,
            "mode": self.mode,
            "gain": self.gain,
            "dropout_active": self.dropout_active,
        }


class ActuatorFaultSchedule:
    """Immutable, canonical, deterministic schedule for one D1 episode."""

    def __init__(self, windows: Sequence[ActuatorFaultWindow] = ()) -> None:
        try:
            values = tuple(windows)
        except TypeError as exc:
            raise D1FaultError("fault windows must be an iterable") from exc
        if any(not isinstance(window, ActuatorFaultWindow) for window in values):
            raise D1FaultError("fault windows must contain ActuatorFaultWindow values")
        ordered = tuple(sorted(values, key=lambda item: (item.start_step, item.end_step)))
        for previous, current in zip(ordered, ordered[1:]):
            if current.start_step < previous.end_step:
                raise D1FaultError("fault windows must not overlap")
        self._windows = ordered
        self._canonical = tuple(window.as_dict() for window in ordered)
        payload = json.dumps(self._canonical, sort_keys=True, separators=(",", ":"))
        self.schedule_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def windows(self) -> tuple[ActuatorFaultWindow, ...]:
        return self._windows

    def state_at(self, step_index: int) -> FaultState:
        if isinstance(step_index, bool) or not isinstance(step_index, int) or step_index < 0:
            raise D1FaultError("step_index must be a non-negative integer")
        for window in self._windows:
            if window.start_step <= step_index < window.end_step:
                gain = window.gain_at(step_index)
                return FaultState(
                    step_index, True, window.mode, gain,
                    dropout_active=window.mode == "intermittent_dropout" and gain == 0.0,
                )
            if step_index < window.start_step:
                break
        return FaultState(step_index, False, "healthy", 1.0)

    def as_dict(self) -> dict[str, Any]:
        return {"schedule_id": self.schedule_id, "windows": list(self._canonical)}


DEFAULT_SCHEDULE = ActuatorFaultSchedule(
    (ActuatorFaultWindow(start_step=2, end_step=6, mode="failed"),)
)


@dataclass(frozen=True)
class SensorFaultWindow:
    """One public-sensor fault interval; backend latent state is unchanged."""

    start_step: int
    end_step: int
    mode: str
    variable: str = "zone_temperatures_c"
    offset: float = 1.0
    drift_per_step: float = 0.25
    taxonomy_ref: str = "SmartBench:sensor_fault_taxonomy"

    def __post_init__(self) -> None:
        if not isinstance(self.start_step, int) or isinstance(self.start_step, bool) or self.start_step < 0:
            raise D1FaultError("sensor start_step must be a non-negative integer")
        if not isinstance(self.end_step, int) or isinstance(self.end_step, bool) or self.end_step <= self.start_step:
            raise D1FaultError("sensor end_step must be greater than start_step")
        if self.mode not in ("bias", "drift"):
            raise D1FaultError(f"unsupported sensor fault mode {self.mode!r}")
        if self.variable != "zone_temperatures_c":
            raise D1FaultError("only the probed zone_temperatures_c sensor is supported")
        if not all(math.isfinite(float(value)) for value in (self.offset, self.drift_per_step)):
            raise D1FaultError("sensor fault parameters must be finite")
        if self.mode == "bias" and float(self.offset) == 0.0:
            raise D1FaultError("bias requires a non-zero offset")
        if self.mode == "drift" and float(self.drift_per_step) == 0.0:
            raise D1FaultError("drift requires a non-zero drift_per_step")
        if not isinstance(self.taxonomy_ref, str) or not self.taxonomy_ref:
            raise D1FaultError("taxonomy_ref must be a non-empty string")

    def correction(self, step_index: int) -> float:
        if self.mode == "bias":
            return float(self.offset)
        return float(self.drift_per_step) * float(step_index - self.start_step + 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_step": self.start_step,
            "end_step": self.end_step,
            "mode": self.mode,
            "variable": self.variable,
            "offset": float(self.offset),
            "drift_per_step": float(self.drift_per_step),
            "taxonomy_ref": self.taxonomy_ref,
        }


class SensorFaultSchedule:
    def __init__(self, windows: Sequence[SensorFaultWindow] = ()) -> None:
        values = tuple(windows)
        if any(not isinstance(window, SensorFaultWindow) for window in values):
            raise D1FaultError("sensor windows must contain SensorFaultWindow values")
        ordered = tuple(sorted(values, key=lambda item: (item.start_step, item.end_step)))
        for previous, current in zip(ordered, ordered[1:]):
            if current.start_step < previous.end_step:
                raise D1FaultError("sensor windows must not overlap")
        self._windows = ordered
        canonical = tuple(window.as_dict() for window in ordered)
        self.schedule_id = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def windows(self) -> tuple[SensorFaultWindow, ...]:
        return self._windows

    def state_at(self, step_index: int) -> SensorFaultWindow | None:
        for window in self._windows:
            if window.start_step <= step_index < window.end_step:
                return window
            if step_index < window.start_step:
                break
        return None

    def as_dict(self) -> dict[str, Any]:
        return {"schedule_id": self.schedule_id, "windows": [window.as_dict() for window in self._windows]}


def d1_profile(name: str) -> tuple[ActuatorFaultSchedule, SensorFaultSchedule]:
    if name not in D1_PROFILE_NAMES:
        raise D1FaultError(f"unknown D1 profile {name!r}")
    if name in D1_PROFILE_NAMES[:4]:
        gain = 0.5 if name == "degraded" else None
        return ActuatorFaultSchedule((ActuatorFaultWindow(2, 6, name, gain=gain),)), SensorFaultSchedule()
    return ActuatorFaultSchedule(), SensorFaultSchedule((SensorFaultWindow(2, 6, name),))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value.lower()) <= _SHA256_RE


class D1FaultEpisode:
    """Stateful D1 wrapper around one resettable SustainGym adapter."""

    def __init__(
        self,
        base: SustainGymBuildingAdapter,
        schedule: ActuatorFaultSchedule,
        process_id: str,
        sensors: SensorFaultSchedule | None = None,
    ) -> None:
        self.base = base
        self.schedule = schedule
        self.process_id = process_id
        self.sensors = sensors or SensorFaultSchedule()
        self._step_index = 0
        self._done = True
        self._last_replay_id: str | None = None
        self._last_effective_action: list[float] | None = None

    def reset(self, *, seed: int = 200, t_initial: Sequence[float] | None = None) -> dict[str, Any]:
        try:
            observation = self.base.reset(seed=seed, t_initial=t_initial)
        except Exception as exc:
            raise D1FaultError(f"SustainGym reset failed: {exc}") from exc
        self._step_index = 0
        self._done = False
        base_replay = self.base.replay_id()
        self._last_replay_id = hashlib.sha256(
            f"{base_replay}:{self.schedule.schedule_id}".encode("utf-8")
        ).hexdigest()
        self._last_effective_action = None
        return self._decorate_observation(observation, self.schedule.state_at(0), 0)

    def observe(self) -> dict[str, Any]:
        self._require_active()
        try:
            observation = self.base.observe()
        except Exception as exc:
            raise D1FaultError(f"SustainGym observe failed: {exc}") from exc
        return self._decorate_observation(observation, self.schedule.state_at(self._step_index), self._step_index)

    def legal_actions(self) -> dict[str, Any]:
        self._require_active()
        try:
            return json.loads(json.dumps(self.base.legal_actions()))
        except Exception as exc:
            raise D1FaultError(f"SustainGym legal action schema unavailable: {exc}") from exc

    def step(self, action: Any) -> dict[str, Any]:
        self._require_active()
        if self._done:
            raise D1FaultError("episode is done; call reset() first")
        requested = self._validate_action(action)
        fault = self.schedule.state_at(self._step_index)
        if fault.mode == "stuck":
            # Keep the last physical output.  A schedule beginning at step 0
            # safely holds zero until a prior output exists.
            effective = list(self._last_effective_action or [0.0] * len(requested))
        else:
            effective = [value * fault.gain for value in requested]
        try:
            record = self.base.step(effective)
        except Exception as exc:
            raise D1FaultError(f"SustainGym action application failed: {exc}") from exc
        self._step_index += 1
        self._last_effective_action = list(effective)
        self._done = bool(getattr(self.base, "done", False))
        observation = record.get("observation") if isinstance(record, Mapping) else None
        if not isinstance(observation, Mapping):
            raise D1FaultError("SustainGym transition lacks a public observation")
        result = dict(record)
        result["d1"] = {
            "requested_action": requested,
            "effective_action": effective,
            "fault": fault.as_dict(),
            "sensor_fault": self._sensor_fault_dict(self.sensors.state_at(self._step_index - 1)),
        }
        result["observation"] = self._decorate_observation(
            observation, self.schedule.state_at(self._step_index), self._step_index
        )
        result["step_index"] = self._step_index - 1
        return result

    def private_state(self) -> dict[str, Any]:
        self._require_active()
        return {
            "route": "sustaingym_d1_actuator_fault",
            "process_id": self.process_id,
            "schedule": self.schedule.as_dict(),
            "sensor_schedule": self.sensors.as_dict(),
            "step_index": self._step_index,
            "episode_done": self._done,
            "replay_id": self._last_replay_id,
            "base": self.base.private_state(),
            "provenance": {
                "fault_schedule_is_exogenous": True,
                "smartbench_role": "taxonomy_reference_only",
                "backend": f"SustainGym@{PINNED_COMMIT}",
            },
        }

    @property
    def done(self) -> bool:
        return self._done

    def replay_id(self) -> str:
        if self._last_replay_id is None:
            raise D1FaultError("reset() must be called before replay_id()")
        return self._last_replay_id

    def _require_active(self) -> None:
        if self._last_replay_id is None:
            raise D1FaultError("reset() must be called before using the episode")

    def _validate_action(self, action: Any) -> list[float]:
        try:
            schema = self.base.legal_actions()
            shape = schema.get("shape")
            low = float(schema["cooling"]["minimum"])
            high = float(schema["cooling"]["maximum"])
        except Exception as exc:
            raise D1ActionError(f"base cooling action schema unavailable: {exc}") from exc
        if isinstance(action, (str, bytes, Mapping)):
            raise D1ActionError("D1 cooling action must be a finite numeric sequence")
        try:
            values = [float(value) for value in action]
        except (TypeError, ValueError):
            raise D1ActionError("D1 cooling action must be a finite numeric sequence") from None
        if not isinstance(shape, list) or len(shape) != 1 or len(values) != int(shape[0]):
            raise D1ActionError(f"action shape must be [{shape[0] if shape else '?'}]")
        if any(not math.isfinite(value) or value < low - 1e-12 or value > high + 1e-12 for value in values):
            raise D1ActionError(f"action values must be finite and in [{low}, {high}]")
        return values

    def _decorate_observation(
        self, observation: Mapping[str, Any], fault: FaultState, step_index: int
    ) -> dict[str, Any]:
        result = dict(observation)
        sensor = self.sensors.state_at(step_index)
        if sensor is not None:
            values = result.get(sensor.variable)
            if not isinstance(values, list):
                raise D1FaultError(f"public observation lacks sensor variable {sensor.variable!r}")
            correction = sensor.correction(step_index)
            result[sensor.variable] = [float(value) + correction for value in values]
        result["sensor_health"] = self._sensor_fault_dict(sensor)
        result["actuator_health"] = fault.as_dict()
        return result

    @staticmethod
    def _sensor_fault_dict(sensor: SensorFaultWindow | None) -> dict[str, Any]:
        if sensor is None:
            return {"active": False, "mode": "healthy"}
        return {
            "active": True,
            "mode": sensor.mode,
            "variable": sensor.variable,
            "correction": sensor.correction(sensor.start_step),
        }


class D1SustainGymFaultAdapter:
    """Claim-facing D1 adapter; no process is emitted before the replay gate."""

    backend: ClassVar[str] = "sustaingym_building_d1"

    def __init__(
        self,
        base_adapter: SustainGymBuildingAdapter | None = None,
        schedule: ActuatorFaultSchedule | None = None,
        replay_gate_path: Path | str | None = None,
    ) -> None:
        self.base = base_adapter or SustainGymBuildingAdapter()
        self.schedule = schedule or DEFAULT_SCHEDULE
        self.replay_gate_path = Path(replay_gate_path or DEFAULT_REPLAY_GATE)
        self._process: PhysicalProcess | None = None
        self._episode: D1FaultEpisode | None = None

    def capabilities(self) -> Sequence[BackendCapability]:
        gate = self._load_gate()
        verified = gate is not None and gate.get("verified") is True
        status = (
            CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED
            if verified
            else CapabilityStatus.DATA_PROBED_PENDING_REPLAY
        )
        evidence = ["backend-survey/results/sustaingym.json", str(self.replay_gate_path)]
        return (
            BackendCapability(
                capability_id="sustaingym_building_d1.actuator_fault",
                backend=self.backend,
                provides=tuple(sorted(_D1_CAPABILITIES)),
                status=status,
                evidence=tuple(evidence),
                metadata={
                    "route": "sustaingym_d1_actuator_fault",
                    "schedule_id": self.schedule.schedule_id,
                    "fault_windows": len(self.schedule.windows),
                    "fault_modes": sorted({window.mode for window in self.schedule.windows}),
                    "supported_fault_modes": list(D1_PROFILE_NAMES),
                    "smartbench_role": "taxonomy_reference_only",
                    "replay_gate_verified": verified,
                },
            ),
        )

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        gate = self._load_gate()
        if gate is None or gate.get("verified") is not True:
            return ()
        if not requirements:
            return ()
        if self._process is None:
            base_processes = self.base.scan((self._base_requirement(),))
            if not base_processes:
                return ()
            base_process = base_processes[0]
            manifest = dict(base_process.manifest)
            manifest.update(
                {
                    "d1_route": "sustaingym_d1_actuator_fault",
                    "fault_schedule": self.schedule.as_dict(),
                    "replay_gate": str(self.replay_gate_path),
                    "agent_cannot_modify_schedule": True,
                }
            )
            self._process = replace(
                base_process,
                process_id=f"{base_process.process_id}.d1.{self.schedule.schedule_id[:12]}",
                backend=self.backend,
                provided_capabilities=_D1_CAPABILITIES,
                action_types=("hvac_cooling_power_with_actuator_health",),
                manifest=manifest,
            )
        return tuple(
            process
            for process in (self._process,)
            if process is not None
            and any(req.required_capabilities <= process.provided_capabilities for req in requirements)
        )

    def open_episode(self, process_id: str | None = None) -> D1FaultEpisode:
        processes = self.scan((self._base_requirement(),))
        if not processes:
            raise D1FaultError("D1 replay gate is not verified; refusing to open episode")
        process = processes[0]
        if process_id is not None and process_id != process.process_id:
            raise D1FaultError(f"unknown D1 process {process_id!r}")
        return D1FaultEpisode(
            self.base, self.schedule, process.process_id
        )

    @staticmethod
    def _base_requirement() -> ProcessRequirement:
        return ProcessRequirement(
            requirement_id="d1_base_process",
            responsibility_lifecycle=ResponsibilityLifecycle.RECOVER_AFTER_EVENT,
            physical_topology=PhysicalTopology.THERMAL_DYNAMICS,
            required_capabilities=frozenset({"thermal.zone_temperature"}),
            state_variables=(),
            action_types=(),
        )

    def reset(self, *, seed: int = 200, t_initial: Sequence[float] | None = None) -> dict[str, Any]:
        if self._episode is None:
            self._episode = self.open_episode()
        return self._episode.reset(seed=seed, t_initial=t_initial)

    def observe(self) -> dict[str, Any]:
        if self._episode is None:
            raise D1FaultError("reset() must be called before observe()")
        return self._episode.observe()

    def step(self, action: Any) -> dict[str, Any]:
        if self._episode is None:
            raise D1FaultError("reset() must be called before step()")
        return self._episode.step(action)

    def legal_actions(self) -> dict[str, Any]:
        if self._episode is None:
            raise D1FaultError("reset() must be called before legal_actions()")
        return self._episode.legal_actions()

    def close(self) -> None:
        self._episode = None

    def private_state(self) -> dict[str, Any]:
        if self._episode is None:
            raise D1FaultError("reset() must be called before private_state()")
        return self._episode.private_state()

    def _load_gate(self) -> dict[str, Any] | None:
        if not self.replay_gate_path.is_file():
            return None
        try:
            gate = json.loads(self.replay_gate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise D1FaultError(f"invalid D1 replay gate: {exc}") from exc
        if not isinstance(gate, dict):
            raise D1FaultError("D1 replay gate must be a JSON object")
        if gate.get("schema_version") not in {
            "d1-fault-replay-gate-v1",
        }:
            raise D1FaultError("unsupported D1 replay gate schema")
        if gate.get("backend_commit") != PINNED_COMMIT:
            raise D1FaultError("D1 replay gate backend commit is not pinned")
        if gate.get("schedule_id") != self.schedule.schedule_id:
            raise D1FaultError("D1 replay gate schedule does not match this adapter")
        adapter_hash = gate.get("adapter_sha256")
        if not _valid_digest(adapter_hash):
            raise D1FaultError("D1 replay gate lacks adapter_sha256")
        if not hmac.compare_digest(adapter_hash.lower(), _sha256_file(Path(__file__))):
            raise D1FaultError("D1 replay gate is stale for this adapter source")
        if not isinstance(gate.get("verified"), bool):
            raise D1FaultError("D1 replay gate lacks a boolean verified verdict")
        if gate["verified"]:
            required = (
                "backend_importable",
                "same_reset_same_window_replay",
                "action_sensitive",
                "fault_changes_future_state",
                "fault_changes_feasible_strategy",
                "all_replays_completed",
            )
            if any(gate.get(key) is not True for key in required):
                raise D1FaultError("D1 replay gate claims verified without all causal checks")
        return gate


# Short alias makes the route discoverable without implying a SmartBench backend.
D1FaultAdapter = D1SustainGymFaultAdapter

__all__ = [
    "D1_PROFILE_NAMES",
    "d1_profile",
    "ActuatorFaultWindow",
    "ActuatorFaultSchedule",
    "SensorFaultWindow",
    "SensorFaultSchedule",
    "FaultState",
    "D1ActionError",
    "D1FaultError",
    "D1FaultEpisode",
    "D1SustainGymFaultAdapter",
    "D1FaultAdapter",
]
