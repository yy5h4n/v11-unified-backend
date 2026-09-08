"""D0 backend: deterministic external events and context transitions.

This is a backend-only route.  It owns a small, explicit state-transition
runtime and an independent exogenous schedule; it does not read or select
responsibilities, Episodes, contracts, or evaluator targets.  A caller can
replay the same schedule and agent actions and obtain the same trajectory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from ..types import BackendCapability, CapabilityStatus, PhysicalProcess, ProcessAdapter, ProcessRequirement

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPLAY_GATE = ROOT / "generated" / "d0_exogenous_context_v1" / "gate_report.json"
TICK_SECONDS = 60
DEFAULT_HORIZON_STEPS = 12
BACKEND_NAME = "d0_exogenous_context_backend_v1"

PROVIDES = frozenset(
    {
        "context.external_event_schedule",
        "context.observable_events",
        "context.state_transition",
        "context.agent_action_effect",
        "replay.deterministic",
        "provenance.schedule_bound",
    }
)


class D0ContextError(RuntimeError):
    """Fail-closed D0 schedule, transition, or provenance error."""


class D0ActionError(ValueError):
    """Malformed or unsupported D0 action."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ExternalContextEvent:
    """One agent-independent event delivered at a discrete tick."""

    step: int
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.step, bool) or not isinstance(self.step, int) or self.step < 1:
            raise D0ContextError("event step must be a positive integer")
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise D0ContextError("event_type must be a non-empty string")
        if not isinstance(self.payload, Mapping):
            raise D0ContextError("event payload must be an object")
        try:
            json.dumps(dict(self.payload), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise D0ContextError("event payload must be JSON-compatible") from exc

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "event_type": self.event_type, "payload": dict(self.payload)}


class ExogenousContextSchedule:
    """Canonical immutable schedule; the agent cannot add or remove events."""

    def __init__(self, events: Sequence[ExternalContextEvent] = ()) -> None:
        values = tuple(events)
        if any(not isinstance(event, ExternalContextEvent) for event in values):
            raise D0ContextError("schedule must contain ExternalContextEvent values")
        self._events = tuple(sorted(values, key=lambda event: (event.step, event.event_type, _digest(event.payload))))
        self._canonical = tuple(event.as_dict() for event in self._events)
        self.schedule_id = _digest(self._canonical)

    @property
    def events(self) -> tuple[ExternalContextEvent, ...]:
        return self._events

    def at(self, step: int) -> tuple[ExternalContextEvent, ...]:
        return tuple(event for event in self._events if event.step == step)

    def as_dict(self) -> dict[str, Any]:
        return {"schedule_id": self.schedule_id, "events": list(self._canonical)}


DEFAULT_SCHEDULE = ExogenousContextSchedule(
    (
        ExternalContextEvent(2, "occupancy_change", {"status": "away", "count": 0}),
        ExternalContextEvent(4, "door_state_change", {"state": "open", "source": "external"}),
        ExternalContextEvent(6, "context_update", {"key": "weather", "value": "rain"}),
        ExternalContextEvent(8, "occupancy_change", {"status": "home", "count": 2}),
    )
)


class D0ContextTrajectory:
    """Stateful backend trajectory with real event/action stepping."""

    _CONTEXT_KEYS = frozenset({"occupancy_status", "occupancy_count", "weather", "time_context"})

    def __init__(self, schedule: ExogenousContextSchedule, horizon_steps: int = DEFAULT_HORIZON_STEPS) -> None:
        if isinstance(horizon_steps, bool) or not isinstance(horizon_steps, int) or horizon_steps < 1:
            raise D0ContextError("horizon_steps must be a positive integer")
        if any(event.step > horizon_steps for event in schedule.events):
            raise D0ContextError("schedule event is beyond trajectory horizon")
        self.schedule = schedule
        self.horizon_steps = horizon_steps
        self._active = False

    def reset(self, *, seed: int = 0) -> dict[str, Any]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise D0ContextError("seed must be an integer")
        self.seed = seed
        self.step_index = 0
        self.context = {
            "occupancy_status": "home",
            "occupancy_count": 2,
            "weather": "clear",
            "time_context": "evening",
        }
        self.devices = {"front_door": "closed", "interior_lights": "off"}
        self._events: list[dict[str, Any]] = []
        self._action_log: list[dict[str, Any]] = []
        self._active = True
        return self.observe()

    def observe(self) -> dict[str, Any]:
        self._require_active()
        return {
            "step": self.step_index,
            "time_seconds": self.step_index * TICK_SECONDS,
            "context": dict(self.context),
            "devices": dict(self.devices),
            "events": [dict(event) for event in self._events],
            "terminal": self.step_index >= self.horizon_steps,
        }

    def legal_actions(self) -> dict[str, Any]:
        """Return the public device-command envelope for an Agent.

        The external event schedule is deliberately absent: it is exogenous
        environment state and cannot be authored or changed by the Agent.
        """
        self._require_active()
        return {
            "type": "context_device_command",
            "commands": {
                "front_door": {"operations": ["open", "close"]},
                "interior_lights": {"operations": ["on", "off"]},
            },
            "schedule_agent_modifiable": False,
        }

    def close(self) -> None:
        """Release the trajectory; no native process requires teardown."""
        self._active = False

    def private_state(self) -> dict[str, Any]:
        self._require_active()
        return {
            "step": self.step_index,
            "seed": self.seed,
            "schedule": self.schedule.as_dict(),
            "context": dict(self.context),
            "devices": dict(self.devices),
            "action_log": [dict(action) for action in self._action_log],
            "events": [dict(event) for event in self._events],
        }

    def state_digest(self) -> str:
        return _digest(self.private_state())

    def step(self, action: Mapping[str, Any]) -> dict[str, Any]:
        self._require_active()
        if self.step_index >= self.horizon_steps:
            raise D0ContextError("trajectory is terminal; call reset() first")
        normalized = self._validate_action(action)
        self._events = []
        self._apply_action(normalized)
        self.step_index += 1
        for event in self.schedule.at(self.step_index):
            self._apply_external_event(event)
        self._events.sort(key=lambda event: (event["source"], event["event_type"]))
        return {
            "observation": self.observe(),
            "transition": {
                "step": self.step_index,
                "action": normalized,
                "events": [dict(event) for event in self._events],
                "state_digest": self.state_digest(),
            },
            "done": self.step_index >= self.horizon_steps,
        }

    def _validate_action(self, action: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(action, Mapping) or action.get("kind") != "act":
            raise D0ActionError("D0 action must be {kind: 'act', command: {...}}")
        if set(action) != {"kind", "command"} or not isinstance(action.get("command"), Mapping):
            raise D0ActionError("D0 action fields must be exactly kind and command")
        command = action["command"]
        if set(command) != {"target", "operation"}:
            raise D0ActionError("D0 command fields must be exactly target and operation")
        target, operation = command["target"], command["operation"]
        if target not in self.devices or operation not in {"on", "off", "open", "close"}:
            raise D0ActionError("unsupported target or operation")
        if target == "front_door" and operation not in {"open", "close"}:
            raise D0ActionError("front_door supports open or close")
        if target == "interior_lights" and operation not in {"on", "off"}:
            raise D0ActionError("interior_lights supports on or off")
        return {"kind": "act", "command": {"target": target, "operation": operation}}

    def _apply_action(self, action: Mapping[str, Any]) -> None:
        command = action["command"]
        target, operation = command["target"], command["operation"]
        value = {"front_door": {"open": "open", "close": "closed"}, "interior_lights": {"on": "on", "off": "off"}}[target][operation]
        self.devices[target] = value
        record = {"source": "agent", "event_type": "action_applied", "target": target, "operation": operation, "step": self.step_index}
        self._events.append(record)
        self._action_log.append(dict(record))

    def _apply_external_event(self, event: ExternalContextEvent) -> None:
        payload = dict(event.payload)
        if event.event_type == "occupancy_change":
            status, count = payload.get("status"), payload.get("count")
            if status not in {"home", "away"} or isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise D0ContextError("invalid occupancy_change payload")
            self.context.update({"occupancy_status": status, "occupancy_count": count})
        elif event.event_type == "door_state_change":
            if payload.get("state") not in {"open", "closed"}:
                raise D0ContextError("invalid door_state_change payload")
            self.devices["front_door"] = payload["state"]
        elif event.event_type == "context_update":
            key, value = payload.get("key"), payload.get("value")
            if key not in self._CONTEXT_KEYS - {"occupancy_status", "occupancy_count"} or not isinstance(value, str) or not value:
                raise D0ContextError("invalid context_update payload")
            self.context[key] = value
        else:
            raise D0ContextError(f"unsupported external event type: {event.event_type}")
        self._events.append({"source": "external", "event_type": event.event_type, "payload": payload, "step": self.step_index})

    def _require_active(self) -> None:
        if not self._active:
            raise D0ContextError("reset() must be called before using trajectory")


class D0ExogenousContextAdapter(ProcessAdapter):
    """Claim-facing D0 adapter, exposed only after its local replay gate passes."""

    backend: ClassVar[str] = BACKEND_NAME

    def __init__(self, schedule: ExogenousContextSchedule | None = None, replay_gate_path: Path | str | None = None, horizon_steps: int = DEFAULT_HORIZON_STEPS) -> None:
        self.schedule = schedule or DEFAULT_SCHEDULE
        self.replay_gate_path = Path(replay_gate_path or DEFAULT_REPLAY_GATE)
        self.horizon_steps = horizon_steps
        self._process: PhysicalProcess | None = None

    def capabilities(self) -> Sequence[BackendCapability]:
        gate = self._load_gate()
        verified = gate is not None and gate.get("verified") is True
        return (
            BackendCapability(
                capability_id="d0_exogenous_context_backend_v1.external_context",
                backend=self.backend,
                provides=tuple(sorted(PROVIDES)),
                status=CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED if verified else CapabilityStatus.DATA_PROBED_PENDING_REPLAY,
                evidence=(str(self.replay_gate_path), "unified_compiler/adapters/d0_exogenous_context.py", "probe_d0_exogenous_context.py"),
                metadata={"schedule_id": self.schedule.schedule_id, "horizon_steps": self.horizon_steps, "replay_gate_verified": verified, "responsibility_free": True},
            ),
        )

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        if not requirements or self._load_gate() is None or self._load_gate().get("verified") is not True:
            return ()
        if self._process is None:
            adapter_hash = _file_sha256(Path(__file__))
            self._process = PhysicalProcess(
                process_id=f"d0-exogenous-context-{self.schedule.schedule_id[:12]}",
                domain="household_context",
                backend=self.backend,
                backend_version="v1",
                source_id="unified_compiler/adapters/d0_exogenous_context.py",
                source_hash=f"sha256:{adapter_hash}",
                horizon_steps=self.horizon_steps,
                observation_interval_seconds=float(TICK_SECONDS),
                provided_capabilities=PROVIDES,
                state_variables=("occupancy_status", "occupancy_count", "weather", "front_door", "interior_lights"),
                action_types=("context_device_command",),
                manifest={"schedule": self.schedule.as_dict(), "adapter_sha256": adapter_hash, "agent_cannot_modify_schedule": True, "backend_only": True},
            )
        return tuple(process for process in (self._process,) if any(req.required_capabilities <= process.provided_capabilities for req in requirements))

    def open_trajectory(self) -> D0ContextTrajectory:
        gate = self._load_gate()
        if gate is None or gate.get("verified") is not True:
            raise D0ContextError("D0 replay gate is not verified; refusing to open trajectory")
        return D0ContextTrajectory(self.schedule, self.horizon_steps)

    def _load_gate(self) -> dict[str, Any] | None:
        if not self.replay_gate_path.is_file():
            return None
        try:
            gate = json.loads(self.replay_gate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise D0ContextError(f"invalid D0 replay gate: {exc}") from exc
        if not isinstance(gate, dict) or gate.get("schema_version") != "d0-exogenous-context-replay-gate-v1":
            raise D0ContextError("unsupported D0 replay gate schema")
        if gate.get("schedule_id") != self.schedule.schedule_id or gate.get("horizon_steps") != self.horizon_steps:
            raise D0ContextError("D0 replay gate schedule or horizon does not match adapter")
        adapter_hash = gate.get("adapter_sha256")
        if not isinstance(adapter_hash, str) or not hmac.compare_digest(adapter_hash, _file_sha256(Path(__file__))):
            raise D0ContextError("D0 replay gate is stale for this adapter source")
        probe_path = ROOT / "probe_d0_exogenous_context.py"
        probe_hash = gate.get("probe_sha256")
        if not isinstance(probe_hash, str) or not hmac.compare_digest(probe_hash, _file_sha256(probe_path)):
            raise D0ContextError("D0 replay gate is stale for this probe source")
        if not isinstance(gate.get("verified"), bool):
            raise D0ContextError("D0 replay gate lacks boolean verified verdict")
        if gate["verified"] and any(gate.get(key) is not True for key in ("runtime_stepping", "deterministic_replay", "action_sensitive", "external_event_sensitive", "provenance_complete")):
            raise D0ContextError("D0 replay gate claims verified without all backend checks")
        return gate


__all__ = ["BACKEND_NAME", "DEFAULT_SCHEDULE", "D0ActionError", "D0ContextError", "D0ContextTrajectory", "D0ExogenousContextAdapter", "ExternalContextEvent", "ExogenousContextSchedule", "PROVIDES"]
