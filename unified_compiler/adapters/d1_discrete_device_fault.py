"""D1 backend-only fault wrapper for Harness V2 discrete household devices.

The wrapped :class:`WorkflowBackend` is the source of all normal transitions
for the front-door lock, garage door, washer, and dishwasher.  This module
adds only an exogenous, immutable health schedule around that real state
machine.  It deliberately does not construct an Episode/query, choose a
responsibility, or evaluate a policy.

Fault windows are half-open ``[start_step, end_step)`` intervals.  ``offline``
rejects commands and freezes an active device; ``stuck`` and ``jammed`` do the
same with distinct observable error codes; ``slowdown`` preserves command
acceptance while adding deterministic time to an active operation.  The
schedule is agent-independent and is never exposed ahead of its onset.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
from typing import Any, Callable, ClassVar, Sequence

from harness_v2.core import BackendStep, EpisodeSpec
from harness_v2.workflow_backend import ALLOWED_COMMANDS, DEVICE_SPECS, TICK_SECONDS, WorkflowBackend


SUPPORTED_DEVICE_IDS = frozenset(
    {"front_door_lock.main", "garage_door.main", "laundry.washer", "dishwasher.main"}
)
SUPPORTED_MODES = frozenset({"offline", "stuck", "jammed", "slowdown"})
_ERROR_CODES = {
    "offline": "FAULT_DEVICE_OFFLINE",
    "stuck": "FAULT_DEVICE_STUCK",
    "jammed": "FAULT_DEVICE_JAMMED",
}


class D1DiscreteFaultError(RuntimeError):
    """Fail-closed schedule or backend lifecycle error."""


class D1DiscreteActionError(ValueError):
    """Malformed command rejected before the wrapped backend can mutate."""


@dataclass(frozen=True)
class DiscreteFaultWindow:
    """One immutable device-health interval."""

    device_id: str
    start_step: int
    end_step: int
    mode: str
    slowdown_factor: float = 1.0
    taxonomy_ref: str = "SmartBench:discrete_device_fault_taxonomy"

    def __post_init__(self) -> None:
        if self.device_id not in SUPPORTED_DEVICE_IDS:
            raise D1DiscreteFaultError(f"unsupported discrete device: {self.device_id!r}")
        if (
            isinstance(self.start_step, bool)
            or not isinstance(self.start_step, int)
            or self.start_step < 0
            or isinstance(self.end_step, bool)
            or not isinstance(self.end_step, int)
            or self.end_step <= self.start_step
        ):
            raise D1DiscreteFaultError("fault interval must use non-negative integers with end > start")
        if self.mode not in SUPPORTED_MODES:
            raise D1DiscreteFaultError(f"fault mode must be one of {sorted(SUPPORTED_MODES)}")
        if isinstance(self.slowdown_factor, bool) or not isinstance(self.slowdown_factor, (int, float)):
            raise D1DiscreteFaultError("slowdown_factor must be numeric")
        if not math.isfinite(float(self.slowdown_factor)):
            raise D1DiscreteFaultError("slowdown_factor must be finite")
        if self.mode == "slowdown":
            if float(self.slowdown_factor) <= 1.0:
                raise D1DiscreteFaultError("slowdown_factor must be > 1 for slowdown")
        elif float(self.slowdown_factor) != 1.0:
            raise D1DiscreteFaultError("non-slowdown faults must use slowdown_factor=1")
        if not isinstance(self.taxonomy_ref, str) or not self.taxonomy_ref:
            raise D1DiscreteFaultError("taxonomy_ref must be a non-empty string")

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "start_step": self.start_step,
            "end_step": self.end_step,
            "mode": self.mode,
            "slowdown_factor": float(self.slowdown_factor),
            "taxonomy_ref": self.taxonomy_ref,
        }


class DiscreteFaultSchedule:
    """Canonical, deterministic, non-overlapping schedule for four devices."""

    def __init__(self, windows: Sequence[DiscreteFaultWindow] = ()) -> None:
        try:
            values = tuple(windows)
        except TypeError as exc:
            raise D1DiscreteFaultError("fault windows must be iterable") from exc
        if any(not isinstance(item, DiscreteFaultWindow) for item in values):
            raise D1DiscreteFaultError("fault windows must contain DiscreteFaultWindow values")
        ordered = tuple(sorted(values, key=lambda item: (item.device_id, item.start_step, item.end_step, item.mode)))
        for previous, current in zip(ordered, ordered[1:]):
            if current.device_id == previous.device_id and current.start_step < previous.end_step:
                raise D1DiscreteFaultError(f"fault windows overlap for {current.device_id}")
        self._windows = ordered
        self._canonical = tuple(item.as_dict() for item in ordered)
        payload = json.dumps(self._canonical, sort_keys=True, separators=(",", ":"))
        self.schedule_id = sha256(payload.encode("utf-8")).hexdigest()

    @property
    def windows(self) -> tuple[DiscreteFaultWindow, ...]:
        return self._windows

    def active(self, device_id: str, step: int) -> DiscreteFaultWindow | None:
        if device_id not in SUPPORTED_DEVICE_IDS:
            raise D1DiscreteFaultError(f"unsupported discrete device: {device_id!r}")
        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            raise D1DiscreteFaultError("step must be a non-negative integer")
        for window in self._windows:
            if window.device_id != device_id:
                continue
            if window.start_step <= step < window.end_step:
                return window
            if window.start_step > step:
                break
        return None

    def as_dict(self) -> dict[str, Any]:
        return {"schedule_id": self.schedule_id, "windows": list(self._canonical)}


class DiscreteDeviceFaultBackend(WorkflowBackend):
    """Workflow V2 state machine with deterministic discrete-device faults."""

    backend_name: ClassVar[str] = "harness_v2_workflow_discrete_faults"

    def __init__(
        self,
        schedule: DiscreteFaultSchedule | None = None,
        *,
        private_scenario_type: str | None = None,
        private_config: dict[str, int] | None = None,
        private_horizon_seconds: int | None = None,
    ) -> None:
        if schedule is not None and not isinstance(schedule, DiscreteFaultSchedule):
            raise D1DiscreteFaultError("schedule must be a DiscreteFaultSchedule")
        super().__init__(
            private_scenario_type=private_scenario_type,
            private_config=private_config,
            private_horizon_seconds=private_horizon_seconds,
        )
        self.schedule = schedule or DiscreteFaultSchedule()

    def reset(self, episode: EpisodeSpec) -> BackendStep:
        # ``WorkflowBackend.reset`` returns ``_step_view``; initialize this
        # first because our override enriches both the initial and later views.
        self._fault_provenance = {
            "schedule_id": self.schedule.schedule_id,
            "schedule": self.schedule.as_dict(),
            "agent_can_modify_schedule": False,
            "interval_convention": "half_open_start_inclusive_end_exclusive",
        }
        step = super().reset(episode)
        return step

    def legal_actions(self) -> dict[str, Any]:
        """Return the complete public command schema for the workflow Agent.

        ``WorkflowBackend`` historically received already-validated actions
        from the Harness runner.  Exposing its existing command tables here
        makes the same real backend directly usable by an Agent without
        inventing a second action vocabulary.
        """
        if not hasattr(self, "_devices"):
            raise D1DiscreteFaultError("reset() must be called before legal_actions()")
        from harness_v2.workflow_backend import PARAMETER_SCHEMAS
        return {
            "type": "harness_agent_action",
            "commands": {
                device_id: {
                    capability: {
                        operation: deepcopy(PARAMETER_SCHEMAS.get((capability, operation), {"type": "object", "maxProperties": 0}))
                        for operation in operations
                    }
                    for capability, operations in capabilities.items()
                }
                for device_id, capabilities in ALLOWED_COMMANDS.items()
            },
            "wait": {"modes": ["for", "until", "until_event"]},
            "fault_schedule_agent_modifiable": False,
        }

    def state_digest(self) -> str:
        payload = {"workflow_state_digest": super().state_digest(), "fault_schedule": self.schedule.as_dict()}
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _validate_command(self, command: dict[str, Any], *, check_state: bool = True) -> str | None:
        error = super()._validate_command(command, check_state=check_state)
        if error is not None:
            return error
        device_id = command.get("device_id")
        # The wrapped workflow exposes more devices than this fault adapter can
        # perturb (for example, the notification service).  Those commands
        # must pass through unchanged; only the four explicitly faultable
        # devices participate in the health schedule.
        if device_id not in SUPPORTED_DEVICE_IDS:
            return None
        # Installing a future rule checks syntax, not current availability.
        # Its command is health-checked again at the actual firing tick.
        if not check_state:
            return None
        window = self.schedule.active(device_id, self._step)
        if window is not None and window.mode in _ERROR_CODES:
            return _ERROR_CODES[window.mode]
        return None

    def _apply_commands(self, commands: list[dict[str, Any]], *, source: str, rule_id: str | None = None) -> list[dict[str, Any]]:
        applied = super()._apply_commands(commands, source=source, rule_id=rule_id)
        # A slowdown applies at command acceptance as well as on subsequent
        # ticks.  This keeps a cycle started during a slowdown measurably slower
        # without replacing the workflow state machine's transition logic.
        for item in applied:
            command = item.get("command", {})
            device_id = command.get("device_id")
            if device_id not in SUPPORTED_DEVICE_IDS:
                continue
            operation = command.get("operation")
            window = self.schedule.active(device_id, self._step)
            if window is None or window.mode != "slowdown":
                continue
            if operation in {"start", "start_cleaning", "open", "close"}:
                device = self._devices[device_id]
                if device.get("remaining_seconds", 0) > 0:
                    device["remaining_seconds"] = int(
                        math.ceil(float(device["remaining_seconds"]) * window.slowdown_factor)
                    )
        return applied

    def _process_dynamics(self) -> None:
        # Apply health effects immediately before the real workflow dynamics.
        # Adding one deterministic overhead per tick gives slowdown factor f:
        # normal decrement T becomes T/f in effective wall-clock time.  Frozen
        # device dynamics are offset by one tick, while unrelated devices still
        # run through WorkflowBackend unchanged.
        for device_id in SUPPORTED_DEVICE_IDS:
            device = self._devices[device_id]
            window = self.schedule.active(device_id, self._step)
            if window is None or device["state"] not in DEVICE_SPECS[device_id]["active_states"]:
                continue
            if window.mode in {"offline", "stuck", "jammed"}:
                if device.get("remaining_seconds", 0) > 0:
                    device["remaining_seconds"] += TICK_SECONDS
            elif window.mode == "slowdown":
                overhead = math.ceil(TICK_SECONDS * (float(window.slowdown_factor) - 1.0))
                device["remaining_seconds"] += overhead
        super()._process_dynamics()

    def _step_view(self, terminal: bool) -> BackendStep:
        view = super()._step_view(terminal)
        public = deepcopy(view.public_observation)
        private = deepcopy(view.private_state)
        active_faults: dict[str, dict[str, Any]] = {}
        for device_id in sorted(SUPPORTED_DEVICE_IDS):
            window = self.schedule.active(device_id, self._step)
            if window is None:
                continue
            active_faults[device_id] = {
                "mode": window.mode,
                "step": self._step,
                "taxonomy_ref": window.taxonomy_ref,
                **({"slowdown_factor": float(window.slowdown_factor)} if window.mode == "slowdown" else {}),
            }
            if device_id in public.get("devices", {}):
                availability = "degraded" if window.mode == "slowdown" else window.mode
                public["devices"][device_id]["availability"] = availability
                for inventory_device in public.get("inventory", {}).get("devices", []):
                    if inventory_device.get("device_id") == device_id:
                        inventory_device["availability"] = availability
        public["active_device_faults"] = active_faults
        private["fault_schedule"] = deepcopy(self._fault_provenance)
        private["active_device_faults"] = deepcopy(active_faults)
        return BackendStep(public, private, view.terminal)


class DiscreteDeviceFaultAdapter:
    """Small adapter factory; no benchmark admission or evaluator behavior."""

    backend = DiscreteDeviceFaultBackend.backend_name

    def __init__(
        self,
        schedule: DiscreteFaultSchedule | None = None,
        *,
        backend_factory: Callable[..., DiscreteDeviceFaultBackend] = DiscreteDeviceFaultBackend,
    ) -> None:
        if not callable(backend_factory):
            raise D1DiscreteFaultError("backend_factory must be callable")
        self.schedule = schedule or DiscreteFaultSchedule()
        self._backend_factory = backend_factory

    def open_backend(self, **kwargs: Any) -> DiscreteDeviceFaultBackend:
        backend = self._backend_factory(schedule=self.schedule, **kwargs)
        if not isinstance(backend, DiscreteDeviceFaultBackend):
            raise D1DiscreteFaultError("backend_factory returned an incompatible backend")
        return backend

    # Naming mirrors other local adapters while keeping this route a plain
    # backend factory rather than a claim/evaluator adapter.
    open_episode = open_backend

    def provenance(self) -> dict[str, Any]:
        backend_path = Path(__file__).resolve().parents[2] / "harness_v2" / "workflow_backend.py"
        if not backend_path.is_file():
            raise D1DiscreteFaultError(f"missing workflow backend source: {backend_path}")
        adapter_path = Path(__file__).resolve()
        return {
            "backend": DiscreteDeviceFaultBackend.backend_name,
            "source_backend": "harness_v2.workflow_backend.WorkflowBackend",
            "source_backend_path": str(backend_path),
            "source_backend_sha256": sha256(backend_path.read_bytes()).hexdigest(),
            "adapter_path": str(adapter_path),
            "adapter_sha256": sha256(adapter_path.read_bytes()).hexdigest(),
            "python": platform.python_version(),
            "supported_devices": sorted(SUPPORTED_DEVICE_IDS),
            "supported_fault_modes": sorted(SUPPORTED_MODES),
            "fault_schedule": self.schedule.as_dict(),
            "backend_only": True,
            "agent_can_modify_schedule": False,
        }


# Explicit aliases make the independent route discoverable without changing
# the central adapter registry.
D1DiscreteDeviceFaultAdapter = DiscreteDeviceFaultAdapter
D1DiscreteDeviceFaultBackend = DiscreteDeviceFaultBackend
D1DiscreteFaultAdapter = DiscreteDeviceFaultAdapter
D1DiscreteFaultBackend = DiscreteDeviceFaultBackend

__all__ = [
    "D1DiscreteActionError",
    "D1DiscreteDeviceFaultAdapter",
    "D1DiscreteDeviceFaultBackend",
    "D1DiscreteFaultAdapter",
    "D1DiscreteFaultBackend",
    "D1DiscreteFaultError",
    "DiscreteDeviceFaultAdapter",
    "DiscreteDeviceFaultBackend",
    "DiscreteFaultSchedule",
    "DiscreteFaultWindow",
    "SUPPORTED_DEVICE_IDS",
    "SUPPORTED_MODES",
]
