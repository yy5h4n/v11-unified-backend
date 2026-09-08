#!/usr/bin/env python3
"""D3 electric competition over the official EV2Gym native simulator.

The route deliberately exposes two independent charging ports attached to the
same native transformer.  ``EV2Gym`` computes the transformer loading from the
charger outputs; this adapter only projects that native state and validates
the public action envelope.  It never clamps actions, adds an evaluator
penalty, or implements a second power-flow model.

The local checkout is an evidence-bound runtime dependency.  If EV2Gym (or a
dependency imported by its native module) is unavailable, ``reset`` fails
closed and the probe reports ``EVIDENCE_PENDING``.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
EV2GYM_ROOT = ROOT.parent / "v10_diversity_aware_compiler" / ".runtime" / "ev2gym"
ASSET_ROOT = ROOT / "d3_ev2gym_assets"
CONFIG_TEMPLATE = ASSET_ROOT / "competition_config.yaml"
TOPOLOGY = ASSET_ROOT / "shared_transformer_topology.json"
RUNTIME_LOCK = ASSET_ROOT / "runtime_requirements.lock"
SCHEMA_VERSION = "d3-ev2gym-electric-competition-v1"
ADAPTER_ID = "d3_ev2gym_electric_competition.v1"
TICK_SECONDS = 900.0


class EV2GymCompetitionError(RuntimeError):
    """Native runtime is unavailable or returned an invalid state."""


class EV2GymCompetitionActionError(ValueError):
    """The public two-port action is malformed or out of range."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (ValueError, TypeError):
            # Native EV2Gym info/statistics may contain a numpy vector.  Keep
            # its exact values, only projecting the container to JSON types.
            tolist = getattr(value, "tolist", None)
            if callable(tolist):
                return _jsonable(tolist())
    raise EV2GymCompetitionError(f"native state is not JSON serializable: {type(value).__name__}")


def _load_native():
    if not EV2GYM_ROOT.is_dir():
        raise EV2GymCompetitionError(f"EV2Gym checkout missing: {EV2GYM_ROOT}")
    if str(EV2GYM_ROOT) not in sys.path:
        sys.path.insert(0, str(EV2GYM_ROOT))
    try:
        module = importlib.import_module("ev2gym.models.ev2gym_env")
        return module.EV2Gym
    except Exception as exc:
        raise EV2GymCompetitionError(
            "official EV2Gym native runtime is unavailable; install its pinned "
            f"dependencies before claiming D3 electric support ({type(exc).__name__}: {exc})"
        ) from exc


class EV2GymElectricCompetition:
    """Agent-facing native EV2Gym route with two competing action channels."""

    action_names = ("charger_0_rate", "charger_1_rate")
    public_action_names = action_names

    def __init__(self, *, horizon_steps: int = 96, seed: int = 23) -> None:
        if isinstance(horizon_steps, bool) or not isinstance(horizon_steps, int) or not 1 <= horizon_steps <= 96:
            raise EV2GymCompetitionError("horizon_steps must be an integer in [1, 96]")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise EV2GymCompetitionError("seed must be an integer")
        self.horizon_steps = horizon_steps
        self.seed = seed
        self._env: Any | None = None
        self._started = False
        self._steps = 0

    @property
    def env(self) -> Any:
        if self._env is None:
            raise EV2GymCompetitionError("reset(seed) must be called before use")
        return self._env

    def _config(self) -> Path:
        if not CONFIG_TEMPLATE.is_file() or not TOPOLOGY.is_file():
            raise EV2GymCompetitionError("D3 EV2Gym native assets are incomplete")
        # EV2Gym resolves data paths through its package; topology is the only
        # project-local path and is substituted into a private temporary file.
        import tempfile
        text = CONFIG_TEMPLATE.read_text(encoding="utf-8").replace(
            "__TOPOLOGY__", str(TOPOLOGY.resolve())
        )
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        handle.write(text)
        handle.close()
        return Path(handle.name)

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise EV2GymCompetitionActionError("reset seed must be an integer")
            self.seed = seed
        self.close()
        EV2Gym = _load_native()
        config = self._config()
        try:
            self._env = EV2Gym(
                config_file=str(config),
                seed=self.seed,
                generate_rnd_game=True,
                save_replay=False,
                save_plots=False,
                render_mode=None,
                verbose=False,
            )
            observation, _ = self._env.reset(seed=self.seed)
        except Exception:
            self._env = None
            raise
        finally:
            config.unlink(missing_ok=True)
        self._started = True
        self._steps = 0
        return self._receipt(observation, action=None, effect=None, done=False)

    def observe(self) -> dict[str, Any]:
        if not self._started:
            raise EV2GymCompetitionError("reset() required before observe()")
        if bool(getattr(self.env, "done", False)):
            raise EV2GymCompetitionError("native EV2Gym episode is done")
        return self._state()

    def legal_actions(self) -> dict[str, Any]:
        return {
            "native": True,
            "type": "ev2gym_native_port_action",
            "channels": {
                name: {"index": i, "type": "continuous", "range": [0.0, 1.0], "unit": "fraction_of_native_port_max"}
                for i, name in enumerate(self.public_action_names)
            },
            "native_action": "EV2Gym action vector, one native value per port",
            "shared_constraint": "both ports are connected to transformer_0; native transformer loading is shared",
        }

    @staticmethod
    def _value(action: Mapping[str, Any], name: str) -> float:
        value = action.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EV2GymCompetitionActionError(f"{name} must be a finite number in [0, 1]")
        value = float(value)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise EV2GymCompetitionActionError(f"{name} must be a finite number in [0, 1]")
        return value

    def _action(self, action: Any) -> list[float]:
        if not isinstance(action, Mapping) or set(action) != set(self.action_names):
            raise EV2GymCompetitionActionError(
                "action must contain exactly charger_0_rate and charger_1_rate"
            )
        return [self._value(action, name) for name in self.action_names]

    def _state(self) -> dict[str, Any]:
        env = self.env
        transformer = env.transformers[0]
        chargers = env.charging_stations[:2]
        ports = []
        for charger in chargers:
            ev = charger.evs_connected[0] if charger.evs_connected else None
            ports.append({
                "connected": ev is not None,
                "soc": None if ev is None else float(ev.get_soc()),
                "power_kw": float(charger.current_power_output),
                "max_power_kw": float(charger.get_max_power()),
            })
        max_power = float(transformer.max_power[transformer.current_step])
        current_power = float(transformer.current_power)
        return {
            "time_step": int(env.current_step),
            "time_seconds": float(env.current_step * TICK_SECONDS),
            "ports": ports,
            "transformer": {
                "id": int(transformer.id),
                "power_kw": current_power,
                "capacity_kw": max_power,
                "remaining_capacity_kw": max_power - current_power,
                "overloaded": bool(transformer.is_overloaded()),
                "overload_kw": float(transformer.get_how_overloaded()),
            },
            "native_action_mask": [float(x) for x in getattr(env, "observation_mask", [])],
        }

    def _receipt(self, observation: Any, *, action: Any, effect: Any, done: bool) -> dict[str, Any]:
        return {
            "time_seconds": float(self._steps * TICK_SECONDS),
            "action": deepcopy(action),
            "observation": self._state(),
            "effect": _jsonable(effect) if effect is not None else None,
            "done": bool(done),
            "backend_terminated": bool(done),
            "runtime": "official_ev2gym_native",
        }

    def step(self, action: Any, dt_seconds: float = TICK_SECONDS) -> dict[str, Any]:
        if not self._started:
            raise EV2GymCompetitionError("reset() required before step()")
        if self.env.done:
            raise EV2GymCompetitionActionError("native EV2Gym episode is done")
        if isinstance(dt_seconds, bool) or not isinstance(dt_seconds, (int, float)) or float(dt_seconds) != TICK_SECONDS:
            raise EV2GymCompetitionActionError(f"dt_seconds must equal {TICK_SECONDS:g}")
        values = self._action(action)
        before = self._state()
        try:
            _, reward, terminated, truncated, info = self.env.step(values)
        except (AssertionError, ValueError, TypeError) as exc:
            raise EV2GymCompetitionActionError(str(exc)) from exc
        self._steps += 1
        done = bool(terminated or truncated)
        after = self._state() if not done else before
        effect = {
            "transformer_power_kw": after["transformer"]["power_kw"],
            "remaining_capacity_kw": after["transformer"]["remaining_capacity_kw"],
            "overloaded": after["transformer"]["overloaded"],
            "overload_kw": after["transformer"]["overload_kw"],
            "port_power_kw": [port["power_kw"] for port in after["ports"]],
            "native_reward": _jsonable(reward),
            "native_info": _jsonable(info),
            "before": before["transformer"],
        }
        return self._receipt(after, action=action, effect=effect, done=done)

    def close(self) -> None:
        if self._env is not None:
            close = getattr(self._env, "close", None)
            if callable(close):
                close()
        self._env = None
        self._started = False
        self._steps = 0


def runtime_provenance() -> dict[str, Any]:
    return {
        "adapter_id": ADAPTER_ID,
        "schema_version": SCHEMA_VERSION,
        "native_backend": "EV2Gym",
        "native_checkout": str(EV2GYM_ROOT),
        "native_commit": "c8735e776746c476f359724c020f6b7183a94f48",
        "runtime_environment": str(ROOT.parent / "v10_diversity_aware_compiler" / ".runtime" / "venv"),
        "runtime_lockfile": str(RUNTIME_LOCK),
        "runtime_lockfile_sha256": _sha256(RUNTIME_LOCK) if RUNTIME_LOCK.is_file() else None,
        "native_checkout_sha256": _sha256(EV2GYM_ROOT / "ev2gym" / "models" / "ev2gym_env.py") if (EV2GYM_ROOT / "ev2gym" / "models" / "ev2gym_env.py").is_file() else None,
        "config_template_sha256": _sha256(CONFIG_TEMPLATE) if CONFIG_TEMPLATE.is_file() else None,
        "topology_sha256": _sha256(TOPOLOGY) if TOPOLOGY.is_file() else None,
        "status_policy": "EVIDENCE_PENDING unless native reset/step probe passes",
    }
