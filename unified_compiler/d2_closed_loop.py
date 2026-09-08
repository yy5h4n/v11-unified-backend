"""Common Agent-facing protocol for the D2 physical backends.

The physical adapters intentionally remain backend-specific (EnergyPlus,
WNTR, FDS and Modelica).  This module is the small, dependency-free boundary
between those adapters and an Agent: it normalizes reset/step receipts and
enforces the invariants that make a live closed loop meaningful.  It does not
simulate anything and never supplies a fallback transition.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


class D2ProtocolError(ValueError):
    """Malformed or non-monotone backend receipt."""


@runtime_checkable
class D2ClosedLoopBackend(Protocol):
    """Minimal interface consumed by an Agent."""

    def reset(self, seed: int = 0) -> Mapping[str, Any]: ...
    def observe(self) -> Mapping[str, Any]: ...
    def legal_actions(self) -> Mapping[str, Any]: ...
    def step(self, action: Any, dt_seconds: float) -> Mapping[str, Any]: ...


def _observation(raw: Any) -> dict[str, Any]:
    # The four D2 routes return the initial observation from reset().  Accepting
    # an already-wrapped receipt keeps this normalizer useful for callers that
    # persist protocol receipts without weakening validation.
    if isinstance(raw, Mapping) and isinstance(raw.get("observation"), Mapping):
        raw = raw["observation"]
    if not isinstance(raw, Mapping):
        raise D2ProtocolError("backend observation must be a mapping")
    result = dict(raw)
    # Physical routes expose scalar channels.  Nested values are allowed for
    # future backends, but NaN/Inf must never cross the Agent boundary.
    def finite(value: Any) -> bool:
        if isinstance(value, bool):
            return True
        if isinstance(value, (int, float)):
            return math.isfinite(float(value))
        if isinstance(value, Mapping):
            return all(finite(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return all(finite(v) for v in value)
        return True
    if not all(finite(value) for value in result.values()):
        raise D2ProtocolError("backend observation contains a non-finite value")
    return result


def normalize_reset(raw: Any, *, seed: int) -> dict[str, Any]:
    """Convert a route reset result into a JSON-safe initial receipt."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise D2ProtocolError("reset seed must be an integer")
    receipt = dict(raw) if isinstance(raw, Mapping) else {}
    observation = _observation(raw)
    return {
        "time_seconds": 0.0,
        "observation": observation,
        "action": None,
        "done": False,
        "terminated": False,
        "truncated": False,
        "info": {"seed": seed, **dict(receipt.get("info", {}))},
    }


def normalize_transition(raw: Any, *, action: Any, previous_time: float) -> dict[str, Any]:
    """Validate and normalize one backend step receipt.

    A backend may call its terminal field ``terminal`` or ``terminated``;
    callers always receive all three conventional fields.
    """
    if not isinstance(raw, Mapping):
        raise D2ProtocolError("backend transition must be a mapping")
    if "time_seconds" not in raw:
        raise D2ProtocolError("backend transition is missing time_seconds")
    try:
        time_seconds = float(raw["time_seconds"])
    except (TypeError, ValueError) as exc:
        raise D2ProtocolError("time_seconds must be numeric") from exc
    if not math.isfinite(time_seconds) or time_seconds <= previous_time:
        raise D2ProtocolError("backend time_seconds must increase strictly")
    done = bool(raw.get("done", raw.get("terminal", raw.get("terminated", False))))
    terminated = bool(raw.get("terminated", raw.get("terminal", done)))
    truncated = bool(raw.get("truncated", False))
    info = raw.get("info", {})
    if not isinstance(info, Mapping):
        raise D2ProtocolError("transition info must be a mapping")
    result = {
        "time_seconds": time_seconds,
        "observation": _observation(raw.get("observation")),
        "action": raw.get("action", action),
        "done": done or terminated or truncated,
        "terminated": terminated,
        "truncated": truncated,
        "info": dict(info),
    }
    if isinstance(raw.get("provenance"), Mapping):
        result["provenance"] = dict(raw["provenance"])
    if "delta_t_seconds" in raw:
        try:
            result["delta_t_seconds"] = float(raw["delta_t_seconds"])
        except (TypeError, ValueError) as exc:
            raise D2ProtocolError("delta_t_seconds must be numeric") from exc
    else:
        result["delta_t_seconds"] = time_seconds - previous_time
    if "steps" in raw:
        result["steps"] = raw["steps"]
    return result


class D2ClosedLoopAdapter:
    """Normalize one of the four existing real backend adapters.

    This wrapper is deliberately thin: ``step`` delegates exactly once to
    the native adapter, so it cannot turn a replay-only route into a fake
    interactive simulator.
    """

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self._time_seconds = 0.0
        self._reset = False
        self._poisoned = False

    def reset(self, seed: int = 0) -> dict[str, Any]:
        self._poisoned = False
        try:
            raw = self.backend.reset(seed=seed)
            result = normalize_reset(raw, seed=seed)
        except Exception:
            self._reset = False
            self._poisoned = True
            raise
        native_origin = getattr(self.backend, "_session_time_seconds", None)
        if isinstance(native_origin, (int, float)) and math.isfinite(float(native_origin)):
            result["native_time_seconds"] = float(native_origin)
        self._time_seconds = 0.0
        self._reset = True
        return result

    def observe(self) -> dict[str, Any]:
        if not self._reset or self._poisoned:
            raise RuntimeError("call reset(seed) before observe()")
        return _observation(self.backend.observe())

    def legal_actions(self) -> Mapping[str, Any]:
        if not self._reset or self._poisoned:
            raise D2ProtocolError("call reset(seed) before legal_actions()")
        actions = self.backend.legal_actions()
        if not isinstance(actions, Mapping) or not actions:
            raise D2ProtocolError("backend legal_actions() must return a non-empty mapping")
        return dict(actions)

    def step(self, action: Any, dt_seconds: float) -> dict[str, Any]:
        if not self._reset or self._poisoned:
            raise RuntimeError("call reset(seed) before step()")
        try:
            result = normalize_transition(self.backend.step(action, dt_seconds), action=action, previous_time=self._time_seconds)
        except Exception:
            # A public observation cannot prove that a native failure did not
            # mutate hidden solver state.  Require reset after every native
            # entry failure, including ValueError from backend validation.
            self._reset = False
            self._poisoned = True
            raise
        self._time_seconds = result["time_seconds"]
        return result

    def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if close is not None:
            close()
        self._reset = False
        self._poisoned = False


def make_d2_backend(route_id: str, **kwargs: Any) -> D2ClosedLoopAdapter:
    """Construct a normalized route lazily, without importing runtimes early."""
    if route_id == "energyplus_iaq":
        from d2_humidity_air_quality_adapter import EnergyPlusHumidityAirQualityAdapter
        return D2ClosedLoopAdapter(EnergyPlusHumidityAirQualityAdapter(**kwargs))
    if route_id == "wntr_residential_water":
        from .adapters.d2_wntr import WNTRResidentialWaterAdapter
        return D2ClosedLoopAdapter(WNTRResidentialWaterAdapter(**kwargs))
    if route_id == "fds_smoke_fire":
        from d2_fds_adapter import FDSSmokePropagationAdapter
        return D2ClosedLoopAdapter(FDSSmokePropagationAdapter(**kwargs))
    if route_id == "modelica_buildings_aixlib":
        from d2_modelica_buildings_aixlib_adapter import ModelicaBuildingsAixLibAdapter
        return D2ClosedLoopAdapter(ModelicaBuildingsAixLibAdapter(**kwargs))
    raise KeyError(f"unknown D2 route: {route_id}")


__all__ = ["D2ClosedLoopBackend", "D2ClosedLoopAdapter", "D2ProtocolError", "make_d2_backend", "normalize_reset", "normalize_transition"]
