"""Agent-facing closed-loop boundary for the verified backend routes.

The route adapters in this prototype intentionally keep their native APIs.  A
policy, however, should not need to know whether a route is SustainGym,
CityLearn, EV2Gym, or the workflow runtime.  This module is the small public
boundary shared by D0, D1, and D3:

``reset(seed) -> observe() -> legal_actions() -> step(action, dt)``.

It only normalizes receipts and delegates transitions to a real route.  It
does not provide a simulation fallback and it never exposes the exogenous
fault/context schedule as an agent action.
"""

from __future__ import annotations

import inspect
import math
import json
import hashlib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class AgentInterfaceError(RuntimeError):
    """The route cannot satisfy the Agent-facing protocol."""


class AgentActionError(ValueError):
    """The caller supplied an action outside the route's public schema."""


class AgentLifecycleError(AgentInterfaceError):
    """The facade is not in a state where the requested operation is legal."""


class AgentRuntimeError(AgentInterfaceError):
    """A native backend failed; the episode is poisoned until reset."""


# Stable D3 identifiers exposed to callers and catalog builders.  Aliases are
# kept explicit so a historical id can be read without becoming a second
# independently instantiated route.
D3_AGENT_ROUTE_IDS = (
    "d3_citylearn_multi_system",
    "d3_citylearn_multibuilding_competition",
    "d3_wntr_water_competition",
    "d3_modelica_shared_heat",
    "d3_ev2gym_electric_competition",
    "d3_energyplus_shared_ventilation",
)
D3_AGENT_ROUTE_ALIASES = {
    "citylearn_multi_system": "d3_citylearn_multi_system",
    "citylearn_multibuilding_competition": "d3_citylearn_multibuilding_competition",
    "d3_citylearn_coupling": "d3_citylearn_multi_system",
    **{route_id: route_id for route_id in D3_AGENT_ROUTE_IDS},
}


@runtime_checkable
class AgentClosedLoopBackend(Protocol):
    """Minimal backend surface consumed by an Agent."""

    def reset(self, seed: int = 0) -> Mapping[str, Any]: ...
    def observe(self) -> Mapping[str, Any]: ...
    def legal_actions(self) -> Mapping[str, Any]: ...
    def step(self, action: Any, dt_seconds: float | None = None) -> Mapping[str, Any]: ...


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, Mapping):
        return all(_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(item) for item in value)
    return True


def _copy_observation(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentInterfaceError("route observation must be an object")
    result = deepcopy(dict(value))
    if not _finite(result):
        raise AgentInterfaceError("route observation contains NaN or infinity")
    return result


def _call_reset(route: Any, seed: int) -> Any:
    """Call native reset while accommodating legacy routes without seed."""
    try:
        signature = inspect.signature(route.reset)
    except (TypeError, ValueError):
        signature = None
    if signature is not None and "seed" not in signature.parameters:
        return route.reset()
    # Never retry after entering native reset: a TypeError mentioning "seed"
    # can originate after hidden state mutation, not just argument binding.
    return route.reset(seed=seed)


def _route_observation(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping) and isinstance(raw.get("observation"), Mapping):
        raw = raw["observation"]
    return _copy_observation(raw)


def _time_from_observation(observation: Mapping[str, Any], *, previous: float, tick: float) -> float:
    for key in ("time_seconds", "timestamp_seconds"):
        value = observation.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            candidate = float(value)
            if math.isfinite(candidate):
                return candidate
    step = observation.get("step", observation.get("step_index"))
    if isinstance(step, (int, float)) and not isinstance(step, bool):
        candidate = float(step) * tick
        if math.isfinite(candidate):
            # D1 episode records use the index before advancing while the
            # returned observation is already the next public state.
            return max(previous + tick, candidate)
    return previous + tick


def _done_from(raw: Mapping[str, Any], observation: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    terminated = bool(raw.get("terminated", raw.get("terminal", False)))
    truncated = bool(raw.get("truncated", False))
    done = bool(raw.get("done", raw.get("episode_done", False))) or terminated or truncated
    if bool(observation.get("terminal", False)):
        terminated = True
        done = True
    return done, terminated, truncated


def _validate_action_payload(value: Any, path: str = "action") -> None:
    """Reject non-finite numeric action values before entering native code."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise AgentActionError(f"{path} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_action_payload(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_action_payload(item, f"{path}[{index}]")


def _native_step(route: Any, action: Any, dt_seconds: float) -> Any:
    """Call one native transition using its declared time parameter."""
    try:
        params = inspect.signature(route.step).parameters
    except (TypeError, ValueError):
        params = {}
    if "dt_seconds" in params:
        return route.step(action, dt_seconds=dt_seconds)
    if "delta_t" in params:
        return route.step(action, delta_t=dt_seconds)
    if "dt" in params:
        return route.step(action, dt=dt_seconds)
    return route.step(action)

def _state_digest(route: Any) -> str | None:
    try:
        value = route.observe()
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
    except Exception:
        return None

def _normalize_step_receipt(raw: Mapping[str, Any], observation: dict[str, Any], action: Any, previous: float, origin: float, tick: float) -> dict[str, Any]:
    raw_time = raw.get("time_seconds")
    if isinstance(raw_time, (int, float)) and not isinstance(raw_time, bool) and math.isfinite(float(raw_time)):
        time_seconds = float(raw_time) - origin
    else:
        time_seconds = _time_from_observation(observation, previous=previous, tick=tick)
    if time_seconds <= previous or not math.isfinite(time_seconds):
        raise AgentInterfaceError("route time must increase strictly")
    done, terminated, truncated = _done_from(raw, observation)
    raw_info = raw.get("info", {})
    if not isinstance(raw_info, Mapping): raw_info = {}
    allowed_info = {"seed", "route", "accepted", "error_code", "transition", "effect", "d1", "d1_fault", "events", "state_digest", "native_time_origin", "execution_mode", "native_pid", "native_start_count", "native_action"}
    info = {str(key): deepcopy(value) for key, value in raw_info.items() if key in allowed_info}
    for key in ("transition", "effect", "d1", "d1_fault", "events", "state_digest", "native_time_origin", "execution_mode", "native_pid", "native_start_count", "native_action"):
        if key in raw and key not in info: info[key] = deepcopy(raw[key])
    return {"time_seconds": time_seconds, "observation": deepcopy(observation), "action": deepcopy(action), "done": done, "terminated": terminated, "truncated": truncated, "info": info, "delta_t_seconds": time_seconds - previous}


@dataclass
class AgentReceiptAdapter:
    """Thin normalizer around one already-instantiated, real route."""

    route: Any
    tick_seconds: float = 1.0
    accepted_dt_seconds: tuple[float, ...] | None = None
    action_validator: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.tick_seconds, (int, float)) or isinstance(self.tick_seconds, bool) or not math.isfinite(float(self.tick_seconds)) or self.tick_seconds <= 0:
            raise AgentInterfaceError("tick_seconds must be a positive finite number")
        self.tick_seconds = float(self.tick_seconds)
        if self.accepted_dt_seconds is None:
            self.accepted_dt_seconds = (self.tick_seconds,)
        else:
            self.accepted_dt_seconds = tuple(float(value) for value in self.accepted_dt_seconds)
            if not self.accepted_dt_seconds or any(not math.isfinite(value) or value <= 0 for value in self.accepted_dt_seconds):
                raise AgentInterfaceError("accepted_dt_seconds must contain positive finite values")
        self._time_seconds = 0.0
        self._started = False
        self._terminal = False
        self._last_observation: dict[str, Any] | None = None
        self._closed = False
        self._poisoned = False
        self._native_time_origin: float | None = None

    def reset(self, seed: int = 0) -> dict[str, Any]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise AgentInterfaceError("reset seed must be an integer")
        self._closed = False
        self._poisoned = False
        try:
            raw = _call_reset(self.route, seed)
        except Exception as exc:
            self._started = False
            self._terminal = True
            raise AgentRuntimeError(f"native route reset failed: {exc}") from exc
        try:
            observation = _route_observation(raw)
        except Exception as exc:
            self._poisoned = True; self._started = False; self._terminal = True
            raise AgentRuntimeError(f"native reset receipt failed: {exc}") from exc
        raw_mapping = raw if isinstance(raw, Mapping) else {}
        done, terminated, truncated = _done_from(raw_mapping, observation)
        native_time = raw_mapping.get("native_time_seconds") if isinstance(raw_mapping, Mapping) else None
        if native_time is None:
            native_time = raw_mapping.get("time_seconds") if isinstance(raw_mapping, Mapping) else None
        if not isinstance(native_time, (int, float)) or isinstance(native_time, bool) or not math.isfinite(float(native_time)):
            native_time = observation.get("time_seconds") if isinstance(observation, Mapping) else None
        if not isinstance(native_time, (int, float)) or isinstance(native_time, bool) or not math.isfinite(float(native_time)):
            native_time = getattr(self.route, "_time_seconds", None)
        self._native_time_origin = float(native_time) if isinstance(native_time, (int, float)) and math.isfinite(float(native_time)) else 0.0
        self._time_seconds = 0.0
        self._started = True
        self._terminal = done
        self._last_observation = observation
        return {
            "time_seconds": 0.0,
            "observation": deepcopy(observation),
            "action": None,
            "done": done,
            "terminated": terminated,
            "truncated": truncated,
            "info": {"seed": seed, "route": type(self.route).__name__},
        }

    def observe(self) -> dict[str, Any]:
        if self._closed or self._poisoned or not self._started:
            raise AgentLifecycleError("reset(seed) must be called before observe()")
        if self._terminal:
            # A terminal receipt is the authoritative last native snapshot.
            # Some native APIs forbid querying after done; reading this copy
            # must neither restart the simulator nor lose the final result.
            return deepcopy(self._last_observation)
        observation = _route_observation(self.route.observe())
        self._last_observation = observation
        return deepcopy(observation)

    def legal_actions(self) -> dict[str, Any]:
        if self._closed or self._poisoned or not self._started:
            raise AgentLifecycleError("reset(seed) must be called before legal_actions()")
        try:
            actions = self.route.legal_actions()
        except Exception as exc:
            raise AgentInterfaceError(f"route legal_actions() failed: {exc}") from exc
        if not isinstance(actions, Mapping) or not actions:
            raise AgentInterfaceError("route legal_actions() must return a non-empty object")
        return deepcopy(dict(actions))

    def step(self, action: Any, dt_seconds: float | None = None) -> dict[str, Any]:
        if self._closed or self._poisoned or not self._started:
            raise AgentLifecycleError("reset(seed) must be called before step()")
        if self._terminal:
            raise AgentLifecycleError("cannot step a terminal route; call reset(seed) first")
        effective_dt = self.tick_seconds if dt_seconds is None else dt_seconds
        if isinstance(effective_dt, bool) or not isinstance(effective_dt, (int, float)) or not math.isfinite(float(effective_dt)) or float(effective_dt) <= 0:
            raise AgentActionError("dt_seconds must be a positive finite number")
        if not any(abs(float(effective_dt) - accepted) <= 1e-9 for accepted in self.accepted_dt_seconds):
            accepted = ", ".join(f"{value:g}" for value in self.accepted_dt_seconds)
            raise AgentActionError(f"route requires dt_seconds in {{{accepted}}}")
        _validate_action_payload(action)
        if self.action_validator is not None:
            try:
                self.action_validator(deepcopy(action))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise AgentActionError(str(exc)) from exc
        try:
            raw = _native_step(self.route, action, float(effective_dt))
            if not isinstance(raw, Mapping):
                raise AgentInterfaceError("route transition must be an object")
            observation = _route_observation(raw)
        except Exception as exc:
            # A public observation digest cannot prove that a native call did
            # not mutate hidden solver state before raising.  Only preflight
            # validation above is safely reusable; every exception from the
            # native entry point poisons this episode.
            self._poisoned = True
            self._terminal = True
            # Once native execution has started, a ValueError/TypeError may
            # come from solver internals after mutation. It is not evidence
            # that the model submitted an invalid action.
            raise AgentRuntimeError(f"native route step or receipt failed: {exc}") from exc
        try:
            result = _normalize_step_receipt(raw, observation, action, self._time_seconds, float(self._native_time_origin or 0.0), self.tick_seconds)
        except Exception as exc:
            self._poisoned = True; self._terminal = True
            raise AgentRuntimeError(f"native receipt validation failed: {exc}") from exc
        time_seconds = result["time_seconds"]
        self._time_seconds = time_seconds
        self._last_observation = observation
        self._terminal = bool(result["done"])
        return result

    def close(self) -> None:
        close = getattr(self.route, "close", None)
        if close is not None:
            close()
        self._closed = True
        self._poisoned = False
        self._started = False
        self._terminal = True
        self._last_observation = None


class HarnessD1AgentBackend(AgentReceiptAdapter):
    """Agent facade for the real two-phase Harness V2 workflow backend."""

    def __init__(self, backend: Any, episode_spec: Any, *, tick_seconds: float = 60.0) -> None:
        if isinstance(tick_seconds, bool) or not isinstance(tick_seconds, (int, float)) or not math.isfinite(float(tick_seconds)) or float(tick_seconds) != 60.0:
            raise AgentInterfaceError("Harness D1 workflow tick_seconds is fixed at 60")
        self.episode_spec = episode_spec
        self._episode_seed = getattr(episode_spec, "seed", 0)
        self._last_view: Any | None = None
        super().__init__(backend, tick_seconds=tick_seconds)

    def reset(self, seed: int = 0) -> dict[str, Any]:
        from harness_v2.core import EpisodeSpec

        if not isinstance(seed, int) or isinstance(seed, bool):
            raise AgentInterfaceError("reset seed must be an integer")
        spec = self.episode_spec
        if isinstance(spec, EpisodeSpec):
            spec = EpisodeSpec(spec.episode_id, deepcopy(spec.public_bootstrap), seed, spec.max_decisions)
        self._started = False
        self._terminal = True
        self._last_view = None
        try:
            raw = self.route.reset(spec)
        except Exception as exc:
            self._poisoned = True
            raise AgentRuntimeError(f"native workflow reset failed: {exc}") from exc
        self._closed = False
        self._poisoned = False
        self._last_view = raw
        observation = _copy_observation(raw.public_observation)
        self._time_seconds = 0.0
        self._started = True
        self._terminal = bool(getattr(raw, "terminal", False))
        self._last_observation = observation
        return {"time_seconds": 0.0, "observation": observation, "action": None, "done": self._terminal, "terminated": self._terminal, "truncated": False, "info": {"seed": seed, "route": "harness_v2_workflow"}}

    def observe(self) -> dict[str, Any]:
        if self._poisoned or self._closed or not self._started:
            raise AgentInterfaceError("reset(seed) must be called before observe()")
        if self._last_view is None:
            raise AgentInterfaceError("workflow backend has no current public view")
        observation = _copy_observation(self._last_view.public_observation)
        self._last_observation = observation
        return observation

    def legal_actions(self) -> dict[str, Any]:
        if self._poisoned or self._closed or not self._started:
            raise AgentInterfaceError("reset(seed) must be called before legal_actions()")
        from harness_v2.workflow_backend import ALLOWED_COMMANDS, PARAMETER_SCHEMAS
        return {"type": "harness_agent_action", "commands": {device: {capability: {operation: deepcopy(PARAMETER_SCHEMAS.get((capability, operation), {"type": "object", "maxProperties": 0})) for operation in ops} for capability, ops in caps.items()} for device, caps in ALLOWED_COMMANDS.items()}, "wait": {"modes": ["for", "until", "until_event"]}, "verified": True}

    def _preflight(self, action: Any) -> None:
        """Pure protocol validation. Device availability is an execution result."""
        from harness_v2.core import validate_agent_action
        from harness_v2.workflow_backend import WorkflowBackend, MAX_ACTIONS_PER_TICK
        from datetime import datetime
        try:
            _validate_action_payload(action)
            validate_agent_action(action)
            if action['kind'] == 'act':
                commands = action['commands']
                if len(commands) > MAX_ACTIONS_PER_TICK:
                    raise ValueError('TOO_MANY_COMMANDS')
                devices = []
                for command in commands:
                    if not isinstance(command, dict) or set(command) != {'device_id','capability','operation','parameters'}:
                        raise ValueError('command fields must be device_id, capability, operation, parameters')
                    if not all(isinstance(command[k], str) for k in ('device_id','capability','operation')):
                        raise ValueError('device_id, capability and operation must be strings')
                    devices.append(command['device_id'])
                    # Call the static command checks, not the fault wrapper's
                    # availability checks. A jam is not malformed JSON.
                    error = WorkflowBackend._validate_command(self.route, command, check_state=False)
                    if error:
                        raise ValueError(error)
                if len(devices) != len(set(devices)):
                    raise ValueError('DUPLICATE_DEVICE_COMMAND')
            if action['kind'] == 'install_rule':
                error = self.route._validate_rule(action['rule'])
                if error:
                    raise ValueError(error)
            if action['kind'] == 'cancel_rule' and action['rule_id'] not in self.route._rules:
                raise ValueError('UNKNOWN_RULE')
            if action['kind'] == 'wait' and action['mode'] == 'until':
                target = datetime.fromisoformat(action['timestamp'].replace('Z','+00:00'))
                now = self.route._now()
                if target.tzinfo is None or target <= now:
                    raise ValueError('INVALID_WAIT_TIMESTAMP')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise AgentActionError(str(exc)) from exc

    def step(self, action: Any, dt_seconds: float | None = None) -> dict[str, Any]:
        if self._poisoned or self._closed or not self._started:
            raise AgentInterfaceError("reset(seed) must be called before step()")
        if self._terminal:
            raise AgentInterfaceError("cannot step a terminal workflow; call reset(seed) first")
        if dt_seconds is not None:
            if isinstance(dt_seconds, bool) or not isinstance(dt_seconds, (int, float)) or not math.isfinite(float(dt_seconds)) or float(dt_seconds) <= 0:
                raise AgentActionError("dt_seconds must be a positive finite number")
            if abs(float(dt_seconds) - self.tick_seconds) > 1e-9:
                raise AgentActionError(f"route requires dt_seconds={self.tick_seconds:g}")
        self._preflight(action)
        try:
            outcome = self.route.execute_atomic(action)
            recoverable = outcome.error_code in {
                'FAULT_DEVICE_OFFLINE', 'FAULT_DEVICE_STUCK', 'FAULT_DEVICE_JAMMED',
                'INVALID_STATE', 'BATTERY_TOO_LOW',
            }
            if not outcome.accepted and not recoverable:
                raise AgentActionError(outcome.error_code or "workflow action rejected")
            # No replacement command or automatic retry: advance the native
            # world after this attempted action and return its real outcome.
            view = self.route.advance(action)
        except (ValueError, TypeError) as exc:
            self._poisoned = True; self._terminal = True
            raise AgentActionError(str(exc)) from exc
        except Exception as exc:
            self._poisoned = True; self._terminal = True
            raise AgentInterfaceError(f"native workflow step failed: {exc}") from exc
        try:
            observation = _copy_observation(view.public_observation)
        except Exception as exc:
            self._poisoned = True; self._terminal = True
            raise AgentInterfaceError(f"workflow receipt validation failed: {exc}") from exc
        self._last_view = view
        step_value = observation.get("step")
        if isinstance(step_value, bool) or not isinstance(step_value, (int, float)) or not math.isfinite(float(step_value)):
            self._poisoned = True; self._terminal = True
            raise AgentInterfaceError("workflow observation must expose a finite numeric step")
        time_seconds = float(step_value) * 60.0
        if time_seconds <= self._time_seconds or not math.isfinite(time_seconds):
            self._poisoned = True; self._terminal = True
            raise AgentInterfaceError("workflow clock must increase strictly")
        done = bool(view.terminal)
        self._last_observation = observation
        self._terminal = done
        previous_time = self._time_seconds
        result = {"time_seconds": time_seconds, "observation": observation, "action": deepcopy(action), "done": done, "terminated": done, "truncated": False, "info": {"accepted": outcome.accepted, "error_code": outcome.error_code, "execution_status": "accepted" if outcome.accepted else "device_rejected", "feedback": deepcopy(outcome.public_feedback)}, "delta_t_seconds": time_seconds - previous_time}
        self._time_seconds = time_seconds
        return result


def _make_agent_backend(route_id: str, **kwargs: Any) -> AgentReceiptAdapter:
    """Construct an Agent facade from a stable route identifier."""
    from .route_registry import canonical_route_id
    route_id = canonical_route_id(route_id)

    def reject_unknown(allowed: set[str]) -> None:
        unknown = sorted(set(kwargs) - allowed)
        if unknown:
            raise AgentActionError(f"{route_id}: unknown constructor argument(s): {', '.join(unknown)}")
    # D3 route ids are intentionally explicit and version-independent.  Keep
    # the historical ``d3_citylearn_coupling`` spelling as an alias, but make
    # the route family discoverable from one canonical table.  The values are
    # factories rather than instantiated backends so importing this module
    # never starts a native simulator.
    d3_route = D3_AGENT_ROUTE_ALIASES.get(route_id)
    if d3_route == "d3_citylearn_multi_system":
        # D3 is an existing native CityLearn coupling route.  The route owns
        # one persistent CityLearnEnv per reset; this receipt adapter only
        # enforces the common time/transition envelope.
        reject_unknown({"horizon", "horizon_steps", "seed", "tick_seconds", "config_path"})
        from d3_citylearn_coupling_adapter import D3CityLearnAgentRoute
        return AgentReceiptAdapter(D3CityLearnAgentRoute(**kwargs), tick_seconds=3600.0)
    if d3_route == "d3_citylearn_multibuilding_competition":
        reject_unknown({"horizon_steps", "seed", "building_ids", "config_path"})
        from d3_citylearn_multibuilding_adapter import D3CityLearnMultiBuildingAgentRoute
        return AgentReceiptAdapter(D3CityLearnMultiBuildingAgentRoute(**kwargs), tick_seconds=3600.0)
    if d3_route == "d3_wntr_water_competition":
        reject_unknown(set())
        from d3_wntr_water_competition_adapter import D3WNTRWaterCompetitionRoute
        return AgentReceiptAdapter(D3WNTRWaterCompetitionRoute(), tick_seconds=3600.0)
    if d3_route == "d3_modelica_shared_heat":
        reject_unknown({"horizon_seconds"})
        from d3_modelica_shared_heat_adapter import D3ModelicaSharedHeatRoute
        modelica_kwargs = {
            key: value for key, value in kwargs.items()
            if key in {"horizon_seconds"}
        }
        return AgentReceiptAdapter(D3ModelicaSharedHeatRoute(**modelica_kwargs), tick_seconds=60.0)
    if d3_route == "d3_ev2gym_electric_competition":
        reject_unknown({"horizon_steps", "seed"})
        from d3_ev2gym_electric_competition_adapter import EV2GymElectricCompetition
        ev_kwargs = {
            key: value for key, value in kwargs.items()
            if key in {"horizon_steps", "seed"}
        }
        return AgentReceiptAdapter(EV2GymElectricCompetition(**ev_kwargs), tick_seconds=900.0)
    if d3_route == "d3_energyplus_shared_ventilation":
        reject_unknown(set())
        from d3_energyplus_shared_ventilation_adapter import D3EnergyPlusSharedVentilationRoute
        return AgentReceiptAdapter(D3EnergyPlusSharedVentilationRoute(), tick_seconds=900.0)

    # D2 already has a verified closed-loop normalizer.  Keep one public
    # factory for callers while preserving the D2 module's route semantics.
    if route_id in {"energyplus_iaq", "wntr_residential_water", "fds_smoke_fire", "modelica_buildings_aixlib"}:
        from .d2_closed_loop import make_d2_backend
        allowed = {"energyplus_iaq": {"run_root"}, "wntr_residential_water": set(), "fds_smoke_fire": {"run_root", "runtime", "timeout_s", "timeout_seconds", "require_online"}, "modelica_buildings_aixlib": {"run_root"}}[route_id]
        reject_unknown(allowed)
        if route_id == "fds_smoke_fire" and kwargs.get("require_online"):
            raise AgentInterfaceError("fds_smoke_fire supports prefix_replay only; synchronous online stepping is unavailable")
        from .route_registry import route_metadata
        d2_kwargs = dict(kwargs)
        d2_kwargs.pop("require_online", None)
        if "timeout_seconds" in d2_kwargs:
            d2_kwargs["timeout_s"] = d2_kwargs.pop("timeout_seconds")
        d2 = make_d2_backend(route_id, **d2_kwargs)
        metadata = route_metadata(route_id)
        accepted = tuple(metadata.get("accepted_dt_seconds", [metadata["cadence_seconds"]]))
        return AgentReceiptAdapter(d2, tick_seconds=metadata["cadence_seconds"], accepted_dt_seconds=accepted)
    if route_id == "d0_exogenous_context":
        reject_unknown({"schedule", "horizon_steps", "horizon_seconds"})
        if "horizon_seconds" in kwargs:
            seconds = kwargs.pop("horizon_seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0 or seconds % 60:
                raise AgentActionError("D0 horizon_seconds must be a positive multiple of 60")
            if "horizon_steps" in kwargs:
                raise AgentActionError("specify only one D0 horizon")
            kwargs["horizon_steps"] = int(seconds / 60)
        from .adapters.d0_exogenous_context import D0ExogenousContextAdapter
        route = D0ExogenousContextAdapter(**kwargs).open_trajectory()
        return AgentReceiptAdapter(route, tick_seconds=60.0)
    if route_id == "d1_sustaingym_fault":
        reject_unknown({"base_adapter", "schedule", "replay_gate_path"})
        from .adapters.d1_fault_mechanism import D1SustainGymFaultAdapter
        route = D1SustainGymFaultAdapter(**kwargs)
        return AgentReceiptAdapter(route, tick_seconds=300.0)
    if route_id == "d1_citylearn_battery_fault":
        reject_unknown({"profile", "schedule", "replay_gate_path", "seed"})
        from .adapters.citylearn_battery_fault import CityLearnBatteryFaultAdapter
        city_kwargs = dict(kwargs)
        # The profile gates are the verified D1 evidence artifacts.  A bare
        # factory call therefore chooses one verified fault profile instead of
        # silently constructing an un-gated healthy replay.
        if not any(key in city_kwargs for key in ("profile", "schedule", "replay_gate_path")):
            city_kwargs["profile"] = "power_derating"
        route = CityLearnBatteryFaultAdapter(**city_kwargs).open_episode()
        return AgentReceiptAdapter(route, tick_seconds=3600.0)
    if route_id == "d1_ev2gym_fault":
        reject_unknown({"claim_adapter", "replay_gate_path", "schedule", "process_id"})
        from .adapters.ev2gym_fault import EV2GymFaultAdapter
        adapter = EV2GymFaultAdapter(**{key: value for key, value in kwargs.items() if key in {"claim_adapter", "replay_gate_path", "schedule"}})
        process_id = kwargs.get("process_id")
        if process_id is None:
            process_id = adapter.processes()[0].process_id
        return AgentReceiptAdapter(adapter.trajectory(process_id), tick_seconds=900.0)
    if route_id == "d1_discrete_device_fault":
        reject_unknown({"schedule", "backend_factory", "episode_spec"})
        from harness_v2.core import EpisodeSpec
        from .adapters.d1_discrete_device_fault import DiscreteDeviceFaultAdapter
        adapter = DiscreteDeviceFaultAdapter(**{key: value for key, value in kwargs.items() if key in {"schedule", "backend_factory"}})
        spec = kwargs.get("episode_spec") or EpisodeSpec("agent-d1-discrete", {"horizon_seconds": 600}, 0)
        return HarnessD1AgentBackend(adapter.open_backend(), spec)
    raise KeyError(f"unknown Agent route: {route_id}")


def make_agent_backend(route_id: str, **kwargs: Any) -> AgentReceiptAdapter:
    """Bind audited, side-effect-free validation before entering native code."""
    from importlib import import_module
    from .route_registry import canonical_route_id
    route_id = canonical_route_id(route_id)
    facade = _make_agent_backend(route_id, **kwargs)
    modules = {
        "energyplus_iaq": ("d2_humidity_air_quality_adapter", "validate_action"),
        "wntr_residential_water": ("unified_compiler.adapters.d2_wntr", "validate_action"),
        "fds_smoke_fire": ("d2_fds_adapter", "validate_action"),
        "modelica_buildings_aixlib": ("d2_modelica_buildings_aixlib_adapter", "validate_action"),
        "d3_modelica_shared_heat": ("d3_modelica_shared_heat_adapter", "validate_action"),
        "d3_energyplus_shared_ventilation": ("d3_energyplus_shared_ventilation_adapter", "validate_action"),
        "d3_wntr_water_competition": ("d3_wntr_water_competition_adapter", "_validate_action"),
    }
    if route_id in modules:
        module, name = modules[route_id]
        facade.action_validator = getattr(import_module(module), name)
        if route_id in {"energyplus_iaq", "wntr_residential_water", "fds_smoke_fire", "modelica_buildings_aixlib"}:
            native_validator = facade.action_validator
            def validate_scalar(action: Any) -> None:
                if isinstance(action, bool) or not isinstance(action, (int, float)):
                    raise ValueError("action must be a JSON number, not a string or boolean")
                native_validator(action)
            facade.action_validator = validate_scalar
    elif route_id == "d0_exogenous_context":
        facade.action_validator = facade.route._validate_action
    elif route_id in {"d3_citylearn_multi_system", "d3_citylearn_multibuilding_competition"}:
        facade.action_validator = facade.route._native_action
    elif route_id == "d3_ev2gym_electric_competition":
        facade.action_validator = facade.route._action
    elif route_id == "d1_ev2gym_fault":
        def validate_charge(action: Any) -> None:
            if not isinstance(action, Mapping):
                raise ValueError("EV action must be an object")
            if action.get("type") == "SET_CHARGE_POWER":
                if set(action) != {"type", "kw"} or isinstance(action.get("kw"), bool) or not isinstance(action.get("kw"), (int, float)):
                    raise ValueError("SET_CHARGE_POWER requires exactly type and numeric kw")
            elif action.get("type") == "WAIT" and set(action) != {"type"}:
                raise ValueError("WAIT requires exactly type")
            facade.route._requested_power(action)
        facade.action_validator = validate_charge
    elif route_id == "d1_sustaingym_fault":
        def validate_cooling(action: Any) -> None:
            if not isinstance(action, (list, tuple)) or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in action):
                raise ValueError("cooling action must be a numeric sequence")
            facade.route._episode._validate_action(action)
            # The D1 wrapper's coarse bounds do not cover native zero-HVAC
            # zones. Check the base's pure mask/Box validator before stepping.
            facade.route._episode.base._validate_action(action)
        facade.action_validator = validate_cooling
    elif route_id == "d1_citylearn_battery_fault":
        def validate_battery(action: Any) -> None:
            if isinstance(action, bool) or not isinstance(action, (int, float)) or not math.isfinite(action) or not -1 <= action <= 1:
                raise ValueError("battery action must be finite and in [-1, 1]")
        facade.action_validator = validate_battery
    return facade


__all__ = ["AgentActionError", "AgentClosedLoopBackend", "AgentInterfaceError", "AgentReceiptAdapter", "HarnessD1AgentBackend", "D3_AGENT_ROUTE_IDS", "D3_AGENT_ROUTE_ALIASES", "make_agent_backend"]
