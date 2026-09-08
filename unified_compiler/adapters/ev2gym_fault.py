"""D1 charger-fault adapter for the verified EV2Gym route.

This module is a backend composition layer over :mod:`ev2gym_claim`.  It does
not create benchmark Episodes, responsibilities, labels, queries, or
evaluators.  A frozen charger-health schedule transforms only the public
``SET_CHARGE_POWER``/``WAIT`` action before forwarding it to the already
verified v10 ``EVRuntime``.  The same schedule is used for every replay of an
episode, and is therefore exogenous to the caller.

The adapter is deliberately fail closed: the base EV2Gym claim must verify,
and a probe gate must bind this exact source, the base route, and the schedule
library.  A missing native runtime is represented as ``pending`` and never
causes a process to be advertised as executable.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from ..types import BackendCapability, CapabilityStatus, PhysicalProcess, ProcessRequirement
from .ev2gym_claim import (
    EV2GymClaimAdapter,
    EV2GymClaimError,
    EV2GymClaimEpisode,
    _validate_action,
)

V11_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPLAY_GATE = V11_ROOT / "generated" / "ev2gym_fault_replay_gate_v1.json"
ADAPTER_ID = "unified_compiler.adapters.ev2gym_fault.v1"
FAULT_MODES = ("derated", "outage", "intermittent")
_INTERMITTENT_MODES = frozenset(("intermittent", "intermittent_dropout"))
PROVIDES = frozenset(
    {
        "ev.soc",
        "ev.charge_action",
        "ev.charger_power_derating",
        "ev.charger_outage",
        "ev.charger_intermittent_availability",
        "ev.delivered_charging",
    }
)


class EV2GymFaultError(RuntimeError):
    """Malformed schedule, unavailable native route, or replay-gate failure."""


class EV2GymFaultActionError(ValueError):
    """An action is not legal on the underlying EV2Gym public action surface."""


@dataclass(frozen=True)
class ChargerFaultWindow:
    """One exogenous charger-health window, active on ``[start_step,end_step)``."""

    start_step: int
    end_step: int
    mode: str
    derating_gain: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.start_step, bool)
            or not isinstance(self.start_step, int)
            or self.start_step < 0
            or isinstance(self.end_step, bool)
            or not isinstance(self.end_step, int)
            or self.end_step <= self.start_step
        ):
            raise EV2GymFaultError("charger fault interval must be non-empty non-negative integers")
        if self.mode not in (*FAULT_MODES, "intermittent_dropout"):
            raise EV2GymFaultError(f"charger fault mode must be one of {FAULT_MODES}")
        if self.mode == "derated":
            if isinstance(self.derating_gain, bool) or not isinstance(self.derating_gain, (int, float)):
                raise EV2GymFaultError("derated charger requires a numeric derating_gain")
            if not math.isfinite(float(self.derating_gain)) or not 0.0 < float(self.derating_gain) < 1.0:
                raise EV2GymFaultError("derating_gain must be finite and in (0, 1)")
        elif self.derating_gain not in (None, 0, 0.0, 1, 1.0):
            raise EV2GymFaultError(f"{self.mode} charger does not accept derating_gain")

    def availability_at(self, step_index: int) -> float:
        """Return effective charger availability; intermittent is deterministic 0/1."""
        if self.mode == "outage":
            return 0.0
        if self.mode == "derated":
            return float(self.derating_gain)
        # Fixed one-step available / one-step unavailable pattern, anchored to
        # the window onset and independent of action or RNG state.
        return 0.0 if (step_index - self.start_step) % 2 == 0 else 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_step": self.start_step,
            "end_step": self.end_step,
            "mode": self.mode,
            "derating_gain": None if self.derating_gain is None else float(self.derating_gain),
            "interval_convention": "half_open_start_inclusive_end_exclusive",
        }


@dataclass(frozen=True)
class ChargerHealth:
    step_index: int
    active: bool
    mode: str
    availability: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "active": self.active,
            "mode": self.mode,
            "availability": self.availability,
        }


class ChargerFaultSchedule:
    """Canonical immutable and agent-independent charger schedule."""

    def __init__(self, windows: Sequence[ChargerFaultWindow] = ()) -> None:
        try:
            values = tuple(windows)
        except TypeError as exc:
            raise EV2GymFaultError("charger windows must be an iterable") from exc
        if any(not isinstance(item, ChargerFaultWindow) for item in values):
            raise EV2GymFaultError("charger windows must contain ChargerFaultWindow values")
        ordered = tuple(sorted(values, key=lambda item: (item.start_step, item.end_step)))
        if any(current.start_step < previous.end_step for previous, current in zip(ordered, ordered[1:])):
            raise EV2GymFaultError("charger fault windows must not overlap")
        self._windows = ordered
        self._canonical = tuple(item.as_dict() for item in ordered)
        self.schedule_id = hashlib.sha256(
            json.dumps(self._canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @property
    def windows(self) -> tuple[ChargerFaultWindow, ...]:
        return self._windows

    def health_at(self, step_index: int) -> ChargerHealth:
        if isinstance(step_index, bool) or not isinstance(step_index, int) or step_index < 0:
            raise EV2GymFaultError("step_index must be a non-negative integer")
        for window in self._windows:
            if window.start_step <= step_index < window.end_step:
                return ChargerHealth(step_index, True, window.mode, window.availability_at(step_index))
            if step_index < window.start_step:
                break
        return ChargerHealth(step_index, False, "healthy", 1.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "windows": list(self._canonical),
            "agent_can_modify": False,
        }


DEFAULT_SCHEDULE = ChargerFaultSchedule((ChargerFaultWindow(2, 6, "outage"),))
PROFILE_SCHEDULES = {
    "derated": ChargerFaultSchedule((ChargerFaultWindow(2, 6, "derated", 0.5),)),
    "outage": DEFAULT_SCHEDULE,
    "intermittent": ChargerFaultSchedule((ChargerFaultWindow(2, 8, "intermittent"),)),
    "healthy": ChargerFaultSchedule(),
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise EV2GymFaultError(f"non-serializable value in fault trajectory: {type(value).__name__}")


class EV2GymFaultTrajectory:
    """Backend trajectory wrapper over one verified EV2Gym claim episode."""

    def __init__(
        self,
        base: EV2GymClaimEpisode,
        schedule: ChargerFaultSchedule,
        process_id: str,
        *,
        battery_kwh: float | None = None,
    ) -> None:
        self.base = base
        self.schedule = schedule
        self.process_id = process_id
        self.battery_kwh = battery_kwh
        self._step_index = 0
        self._previous_requested_kw: float | None = None
        self._started = False

    @property
    def replay_id(self) -> str:
        return f"sha256:{hashlib.sha256(json.dumps({'base': self.base.replay_id, 'schedule_id': self.schedule.schedule_id}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}"

    @property
    def done(self) -> bool:
        return self.base.done

    def reset(self) -> dict[str, Any]:
        self._step_index = 0
        self._previous_requested_kw = None
        observation = _jsonable(self.base.reset())
        self._started = True
        return self._fault_observation(observation, self.schedule.health_at(0))

    def observe(self) -> dict[str, Any]:
        if not self._started:
            raise EV2GymFaultError("reset() required before observe()")
        if self.done:
            raise EV2GymFaultError(f"trajectory {self.process_id} terminated")
        return self._fault_observation(_jsonable(self.base.observe()), self.schedule.health_at(self._step_index))

    def legal_actions(self) -> dict[str, Any]:
        """Return the unchanged verified EV2Gym action schema."""
        return _jsonable(self.base.legal_actions())

    def _fault_observation(self, observation: Any, health: ChargerHealth) -> dict[str, Any]:
        if not isinstance(observation, dict):
            raise EV2GymFaultError("EV2Gym observation is not a mapping")
        result = deepcopy(observation)
        nominal = result.get("charger_max_power_kw")
        if nominal is not None:
            try:
                result["charger_max_power_kw"] = round(float(nominal) * health.availability, 6)
            except (TypeError, ValueError):
                raise EV2GymFaultError("EV2Gym charger_max_power_kw is not numeric") from None
        return result

    def _requested_power(self, action: Any) -> float:
        actions = self.legal_actions()
        require_mapping = any(
            isinstance(spec, Mapping)
            and any(isinstance(c, Mapping) and (c.get("minimum") is not None or c.get("maximum") is not None) for c in spec.values())
            for spec in actions.values()
        )
        try:
            _validate_action(action, actions, self._step_index, require_mapping)
        except Exception as exc:
            raise EV2GymFaultActionError(str(exc)) from exc
        if not isinstance(action, Mapping):
            raise EV2GymFaultActionError("EV2Gym action must be a mapping")
        action_type = action.get("type")
        if action_type == "SET_CHARGE_POWER":
            try:
                value = float(action["kw"])
            except (KeyError, TypeError, ValueError):
                raise EV2GymFaultActionError("SET_CHARGE_POWER requires numeric kw") from None
        elif action_type == "WAIT":
            value = 0.0 if self._previous_requested_kw is None else self._previous_requested_kw
        else:
            raise EV2GymFaultActionError(f"unsupported EV2Gym action type: {action_type!r}")
        if not math.isfinite(value):
            raise EV2GymFaultActionError("charge power must be finite")
        return value

    def step(self, action: Any) -> dict[str, Any]:
        if not self._started:
            raise EV2GymFaultError("reset() required before step()")
        if self.done:
            raise EV2GymFaultError(f"trajectory {self.process_id} terminated")
        health = self.schedule.health_at(self._step_index)
        requested_kw = self._requested_power(action)
        effective_kw = round(requested_kw * health.availability, 6)
        # Sending SET_CHARGE_POWER also for WAIT makes the wrapper's requested
        # mode explicit and prevents a faulted previous effective value from
        # leaking across recovery.  This is still the native public action.
        transition = _jsonable(self.base.step({"type": "SET_CHARGE_POWER", "kw": effective_kw}))
        if not isinstance(transition, dict) or not isinstance(transition.get("effect"), Mapping):
            raise EV2GymFaultError("EV2Gym transition is malformed")
        # Some frozen EV2Gym runtimes put the pre-action observation in the
        # transition record.  Query the native runtime once more after the
        # action so the public receipt is the state an Agent observes next.
        # At terminality the native episode intentionally rejects observe(),
        # so retain its final transition observation as the only available
        # public state.
        next_index = self._step_index + 1
        next_health = self.schedule.health_at(next_index)
        try:
            native_observation = self.base.observe() if not self.base.done else transition.get("observation")
        except Exception:
            native_observation = transition.get("observation")
        if not isinstance(native_observation, Mapping):
            raise EV2GymFaultError("EV2Gym transition lacks a public observation")
        transition["observation"] = self._fault_observation(native_observation, next_health)
        effect = dict(transition["effect"])
        delivered = effect.get("charged_energy_kwh")
        if delivered is None:
            before, after = effect.get("vehicle_soc_before"), effect.get("vehicle_soc_after")
            if self.battery_kwh is not None and before is not None and after is not None:
                delivered = max(0.0, float(after) - float(before)) * float(self.battery_kwh)
        effect["delivered_charging_kwh"] = None if delivered is None else round(float(delivered), 6)
        transition["effect"] = effect
        transition["d1_fault"] = {
            "schedule_id": self.schedule.schedule_id,
            "health": health.as_dict(),
            "requested_action": _jsonable(action),
            "requested_charge_power_kw": round(requested_kw, 6),
            "effective_charge_power_kw": effective_kw,
        }
        self._previous_requested_kw = requested_kw
        self._step_index += 1
        return transition

    def private_state(self) -> dict[str, Any]:
        state = dict(self.base.private_state())
        # Keep only replay bookkeeping; no source traces or hidden contract.
        return {
            "backend": "EV2Gym",
            "process_id": self.process_id,
            "base_replay_id": self.base.replay_id,
            "replay_id": self.replay_id,
            "schedule_id": self.schedule.schedule_id,
            "step_index": self._step_index,
            "done": self.done,
            "started": self._started,
            "base_backend_version": state.get("backend_version"),
            "contract_exposed": False,
        }


class EV2GymFaultAdapter:
    """Fail-closed process adapter over verified EV2Gym charger trajectories."""

    backend: ClassVar[str] = "EV2Gym-D1-charger-fault"
    domain: ClassVar[str] = "ev_charging"

    def __init__(
        self,
        claim_adapter: EV2GymClaimAdapter | None = None,
        replay_gate_path: Path | str | None = None,
        schedule: ChargerFaultSchedule | None = None,
    ) -> None:
        self.claim_adapter = claim_adapter or EV2GymClaimAdapter()
        self.replay_gate_path = Path(replay_gate_path or DEFAULT_REPLAY_GATE)
        self.schedule = schedule or DEFAULT_SCHEDULE
        self.scan_calls = 0
        self._processes: tuple[PhysicalProcess, ...] | None = None
        self._verification: dict[str, Any] | None = None

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise EV2GymFaultError(f"missing EV2Gym D1 replay gate: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EV2GymFaultError(f"invalid EV2Gym D1 replay gate: {path}") from exc
        if not isinstance(value, dict):
            raise EV2GymFaultError("EV2Gym D1 replay gate must be an object")
        return value

    def _verify_gate(self) -> dict[str, Any]:
        gate = self._load_json(self.replay_gate_path)
        if gate.get("schema_version") != "ev2gym-d1-fault-replay-gate-v1":
            raise EV2GymFaultError("EV2Gym D1 replay gate schema mismatch")
        source = Path(__file__)
        if gate.get("adapter_sha256") != _sha256_file(source):
            raise EV2GymFaultError("EV2Gym D1 replay gate does not bind this adapter source")
        if gate.get("base_adapter_id") != "unified_compiler.adapters.ev2gym_claim.v1":
            raise EV2GymFaultError("EV2Gym D1 replay gate base adapter mismatch")
        schedules = gate.get("schedule_ids")
        if not isinstance(schedules, Mapping) or any(schedules.get(name) != PROFILE_SCHEDULES[name].schedule_id for name in PROFILE_SCHEDULES):
            raise EV2GymFaultError("EV2Gym D1 replay gate schedule library mismatch")
        if gate.get("verified") is not True:
            raise EV2GymFaultError(f"EV2Gym D1 replay evidence is pending: {gate.get('status', 'unknown')}")
        return gate

    def _ensure_verified(self) -> None:
        if self._verification is not None:
            return
        # Base verification executes the pinned native probe.  Do not turn an
        # unavailable runtime into a positive claim.
        base_capabilities = tuple(self.claim_adapter.capabilities())
        if not base_capabilities or any(
            capability.status is not CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED
            for capability in base_capabilities
        ):
            raise EV2GymFaultError("verified EV2Gym claim route is not executable")
        gate = self._verify_gate()
        self._verification = {"status": "EXECUTABLE_REPLAY_VERIFIED", "gate": gate}

    def capabilities(self) -> Sequence[BackendCapability]:
        try:
            self._ensure_verified()
        except (EV2GymClaimError, EV2GymFaultError) as exc:
            return (
                BackendCapability(
                    capability_id="ev2gym_d1_fault.charger",
                    backend=self.backend,
                    provides=tuple(sorted(PROVIDES)),
                    status=CapabilityStatus.DATA_PROBED_PENDING_REPLAY,
                    evidence=(str(self.replay_gate_path),),
                    metadata={"adapter_id": ADAPTER_ID, "reason": str(exc)},
                ),
            )
        return (
            BackendCapability(
                capability_id="ev2gym_d1_fault.charger",
                backend=self.backend,
                provides=tuple(sorted(PROVIDES)),
                status=CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED,
                evidence=(str(self.replay_gate_path), "ev2gym_claim.v1 pinned route"),
                metadata={"adapter_id": ADAPTER_ID, "schedule_id": self.schedule.schedule_id},
            ),
        )

    def verification(self) -> Mapping[str, Any]:
        """Return the verified gate record, or raise while remaining fail closed."""
        self._ensure_verified()
        return dict(self._verification or {})

    def _build_processes(self) -> tuple[PhysicalProcess, ...]:
        self._ensure_verified()
        built: list[PhysicalProcess] = []
        for base_process in self.claim_adapter.processes():
            manifest = {
                "base_process_id": base_process.process_id,
                "base_backend": "EV2Gym",
                "adapter_id": ADAPTER_ID,
                "fault_schedule": self.schedule.as_dict(),
                "fault_modes": list(FAULT_MODES),
                "provenance": {
                    "base_source_hash": base_process.source_hash,
                    "base_backend_version": base_process.backend_version,
                    "replay_gate": self.replay_gate_path.name,
                },
                "gold_actions_released": False,
                "contract_exposed": False,
            }
            source_hash = "sha256:" + hashlib.sha256(
                json.dumps(
                    {"base": base_process.source_hash, "schedule": self.schedule.schedule_id, "adapter": ADAPTER_ID},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            built.append(
                PhysicalProcess(
                    process_id=f"{base_process.process_id}__d1_{self.schedule.schedule_id[:12]}",
                    domain=self.domain,
                    backend=self.backend,
                    backend_version=base_process.backend_version,
                    source_id=f"ev2gym-d1://{base_process.process_id}/{self.schedule.schedule_id[:12]}",
                    source_hash=source_hash,
                    horizon_steps=base_process.horizon_steps,
                    observation_interval_seconds=base_process.observation_interval_seconds,
                    provided_capabilities=PROVIDES,
                    state_variables=tuple(sorted(set(base_process.state_variables) | {"delivered_charging_kwh"})),
                    action_types=base_process.action_types,
                    manifest=manifest,
                )
            )
        return tuple(built)

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        self.scan_calls += 1
        capability = self.capabilities()[0]
        if capability.status is not CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED:
            return ()
        if self._processes is None:
            self._processes = self._build_processes()
        if not requirements:
            return ()
        return tuple(process for process in self._processes if any(req.required_capabilities <= process.provided_capabilities for req in requirements))

    def processes(self) -> tuple[PhysicalProcess, ...]:
        """Return the schedule-bound process inventory after verification."""
        if self._processes is None:
            self._processes = self._build_processes()
        return self._processes

    def trajectory(self, process_id: str, *, schedule: ChargerFaultSchedule | None = None) -> EV2GymFaultTrajectory:
        self._ensure_verified()
        base_id = process_id.split("__d1_", 1)[0]
        base_process = next((item for item in self.claim_adapter.processes() if item.process_id == base_id), None)
        if base_process is None:
            raise EV2GymFaultError(f"unknown EV2Gym D1 process: {process_id}")
        episode_ids = base_process.manifest.get("legacy_episode_ids", [])
        if not episode_ids:
            raise EV2GymFaultError(f"process {base_id} has no replay episode")
        base_episode = self.claim_adapter.episode_runtime(sorted(episode_ids)[0])
        battery_kwh = None
        try:
            binding = self.claim_adapter.index.private_record(sorted(episode_ids)[0])["backend_binding"]
            battery_kwh = float(binding["battery_kwh"])
        except (KeyError, TypeError, ValueError):
            pass
        return EV2GymFaultTrajectory(base_episode, schedule or self.schedule, base_id, battery_kwh=battery_kwh)

    # Naming aliases keep this backend adapter convenient without introducing
    # a second runtime or a benchmark-level Episode abstraction.
    open_trajectory = trajectory

    def healthy_fault_counterfactual(self, process_id: str, actions: Sequence[Any], *, schedule: ChargerFaultSchedule | None = None) -> dict[str, Any]:
        """Replay identical requested actions on healthy and faulted routes."""
        chosen = schedule or self.schedule
        faulty = self.trajectory(process_id, schedule=chosen)
        healthy = self.trajectory(process_id, schedule=PROFILE_SCHEDULES["healthy"])
        faulty_initial, healthy_initial = faulty.reset(), healthy.reset()
        faulty_rows: list[dict[str, Any]] = []
        healthy_rows: list[dict[str, Any]] = []
        for action in actions:
            if faulty.done or healthy.done:
                break
            faulty_rows.append(faulty.step(action))
            healthy_rows.append(healthy.step(action))
        if not faulty.done or not healthy.done:
            raise EV2GymFaultError("counterfactual action sequence ended before native trajectory termination")
        delivered_fault = sum(float(row["effect"]["delivered_charging_kwh"] or 0.0) for row in faulty_rows)
        delivered_healthy = sum(float(row["effect"]["delivered_charging_kwh"] or 0.0) for row in healthy_rows)
        soc_fault = faulty_rows[-1]["effect"].get("vehicle_soc_after") if faulty_rows else None
        soc_healthy = healthy_rows[-1]["effect"].get("vehicle_soc_after") if healthy_rows else None
        return {
            "schema_version": "ev2gym-d1-fault-counterfactual-v1",
            "process_id": process_id,
            "schedule_id": chosen.schedule_id,
            "faulty": {"initial": faulty_initial, "transitions": faulty_rows, "delivered_charging_kwh": round(delivered_fault, 6)},
            "healthy": {"initial": healthy_initial, "transitions": healthy_rows, "delivered_charging_kwh": round(delivered_healthy, 6)},
            "divergence": {
                "delivered_charging_kwh": round(delivered_healthy - delivered_fault, 6),
                "final_soc": None if soc_fault is None or soc_healthy is None else round(float(soc_healthy) - float(soc_fault), 6),
            },
            "provenance": {"adapter_id": ADAPTER_ID, "base_route": "ev2gym_claim.v1", "schedule_agent_modifiable": False},
        }

    replay_counterfactual = healthy_fault_counterfactual
    counterfactual = healthy_fault_counterfactual


# Explicit charger-prefixed aliases make the backend intent discoverable to
# callers while retaining the short v11 names used by the probe.
EV2GymChargerFaultAdapter = EV2GymFaultAdapter
EV2GymChargerFaultError = EV2GymFaultError
EV2GymChargerFaultActionError = EV2GymFaultActionError
EV2GymChargerFaultTrajectory = EV2GymFaultTrajectory
