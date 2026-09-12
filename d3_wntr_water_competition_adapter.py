"""D3 strong-coupling route for two water services sharing one WNTR network.

The route is deliberately separate from the D2 WNTR adapter.  It exposes a
single persistent ``WaterNetworkModel``/``WNTRSimulator`` in an isolated
worker and lets an Agent change both service valves between native hydraulic
ticks.  Shower and laundry are connected to the same elevated tank and source
pipe, so the demand on either branch changes the pressure/served flow of the
other branch through the solved hydraulic state.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = ROOT / "shared_runtime/wntr-site-packages"
RUNTIME_LOCK = ROOT / "shared_runtime/wntr_runtime_requirements.lock"
TICK_SECONDS = 3600.0
HORIZON_SECONDS = 6 * 3600


class D3WNTRDependencyError(RuntimeError):
    """The pinned isolated WNTR runtime cannot be used."""


class D3WNTRActionError(ValueError):
    """An Agent action is malformed or outside the native action space."""


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _worker_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(RUNTIME_ROOT)
    return env


def _call_worker(mode: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode],
        text=True, capture_output=True, cwd=str(ROOT), env=_worker_env(), check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise D3WNTRDependencyError(f"isolated WNTR worker failed ({completed.returncode}): {detail[-1000:]}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise D3WNTRDependencyError(f"invalid WNTR worker JSON: {completed.stdout[-500:]}") from exc
    if not isinstance(value, dict):
        raise D3WNTRDependencyError("WNTR worker returned a non-object")
    return value


def dependency_status() -> dict[str, Any]:
    try:
        return _call_worker("--dependency-worker")
    except D3WNTRDependencyError as exc:
        return {
            "wntr_importable": False,
            "wntr_version": None,
            "runtime_lock_path": str(RUNTIME_LOCK.relative_to(ROOT)),
            "runtime_lock_sha256": sha256(RUNTIME_LOCK),
            "wntr_package_path": str((RUNTIME_ROOT / "wntr/__init__.py").relative_to(ROOT)),
            "wntr_package_init_sha256": sha256(RUNTIME_ROOT / "wntr/__init__.py"),
            "epanet_library_present": False,
            "import_error": str(exc),
        }


def _validate_binary(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise D3WNTRActionError(f"{name} must be numeric 0 or 1")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise D3WNTRActionError(f"{name} must be numeric 0 or 1") from exc
    if not math.isfinite(result) or result not in (0.0, 1.0):
        raise D3WNTRActionError(f"{name} must be exactly 0 or 1")
    return result


def _validate_action(action: Any) -> dict[str, float]:
    required = {"shower_valve_open", "laundry_valve_open"}
    if not isinstance(action, Mapping) or set(action) != required:
        raise D3WNTRActionError("action must contain exactly shower_valve_open and laundry_valve_open")
    return {name: _validate_binary(action[name], name) for name in sorted(required)}


class D3WNTRWaterCompetitionRoute:
    """Persistent Agent-facing D3 water competition backend."""

    backend_name = "WNTR"
    backend_engine = "WNTRSimulator"
    public_action_names = ("shower_valve_open", "laundry_valve_open")

    def __init__(self) -> None:
        status = dependency_status()
        if not status.get("wntr_importable"):
            raise D3WNTRDependencyError(status.get("import_error", "WNTR unavailable"))
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._observation: dict[str, float] | None = None
        self._time_seconds = 0
        self._done = False

    def reset(self, seed: int = 0) -> dict[str, float]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise D3WNTRActionError("reset seed must be an integer")
        self.close()
        self._proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--session-worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(ROOT), env=_worker_env(), bufsize=1,
        )
        ready = self._read()
        if ready.get("type") != "ready":
            self.close()
            raise D3WNTRDependencyError(f"WNTR session failed to initialize: {ready}")
        initial = self._request({"command": "step", "action": {name: 1.0 for name in self.public_action_names}, "duration": 0})
        self._observation = dict(initial["observation"])
        self._time_seconds = int(initial["time_seconds"])
        self._done = False
        return dict(self._observation)

    def observe(self) -> dict[str, float]:
        if self._observation is None:
            raise D3WNTRDependencyError("call reset(seed) before observe()")
        return dict(self._observation)

    def legal_actions(self) -> dict[str, Any]:
        return {
            "type": "wntr_native_binary_valves",
            "native": True,
            "channels": {
                name: {"index": i, "type": "discrete", "values": [0.0, 1.0]}
                for i, name in enumerate(self.public_action_names)
            },
            "shared_resource": "house_tank_and_municipal_source",
        }

    def step(self, action: Any, dt_seconds: float = TICK_SECONDS) -> dict[str, Any]:
        validated = _validate_action(action)
        if self._observation is None or self._proc is None:
            raise D3WNTRDependencyError("call reset(seed) before step()")
        if self._done:
            raise D3WNTRActionError("D3 WNTR episode is terminated; call reset()")
        if isinstance(dt_seconds, bool) or not isinstance(dt_seconds, (int, float)):
            raise D3WNTRActionError(f"dt_seconds must equal {TICK_SECONDS:g}")
        if not math.isfinite(float(dt_seconds)) or float(dt_seconds) != TICK_SECONDS:
            raise D3WNTRActionError(f"dt_seconds must equal {TICK_SECONDS:g}")
        result = self._request({
            "command": "step", "action": validated,
            "duration": self._time_seconds + int(TICK_SECONDS),
        })
        self._observation = dict(result["observation"])
        self._time_seconds = int(result["time_seconds"])
        self._done = bool(result["terminated"] or result["truncated"])
        result["action"] = validated
        result["done"] = self._done
        result["delta_t_seconds"] = TICK_SECONDS
        result["info"] = {
            "backend": self.backend_name,
            "engine": self.backend_engine,
            "online": True,
            "persistent_native_network": True,
            "shared_resource": "municipal_source -> house_tank -> shower/laundry",
        }
        return result

    def _read(self) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None:
            raise D3WNTRDependencyError("WNTR session is not running")
        line = self._proc.stdout.readline()
        if not line:
            detail = self._proc.stderr.read()[-1000:] if self._proc.stderr else ""
            raise D3WNTRDependencyError(f"WNTR session exited unexpectedly: {detail}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise D3WNTRDependencyError(f"invalid WNTR session JSON: {line[-500:]}") from exc
        if value.get("type") == "error":
            raise D3WNTRDependencyError(str(value.get("error", "WNTR session error")))
        return value

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._proc is None or self._proc.stdin is None:
                raise D3WNTRDependencyError("WNTR session is not running")
            self._proc.stdin.write(json.dumps(payload, sort_keys=True) + "\n")
            self._proc.stdin.flush()
            return self._read()

    def close(self) -> None:
        proc = self._proc
        if proc is not None:
            try:
                if proc.poll() is None and proc.stdin is not None:
                    proc.stdin.write(json.dumps({"command": "close"}) + "\n")
                    proc.stdin.flush()
                    proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        self._proc = None
        self._observation = None
        self._time_seconds = 0
        self._done = False


def _dependency_worker() -> dict[str, Any]:
    sys.path.insert(0, str(RUNTIME_ROOT))
    try:
        import wntr  # type: ignore
        from wntr.sim import WNTRSimulator  # noqa: F401
    except Exception as exc:
        raise RuntimeError(f"WNTR import failed: {exc!r}") from exc
    return {
        "wntr_importable": True,
        "wntr_version": getattr(wntr, "__version__", None),
        "wntr_package_path": str((RUNTIME_ROOT / "wntr/__init__.py").relative_to(ROOT)),
        "wntr_package_init_sha256": sha256(RUNTIME_ROOT / "wntr/__init__.py"),
        "runtime_lock_path": str(RUNTIME_LOCK.relative_to(ROOT)),
        "runtime_lock_sha256": sha256(RUNTIME_LOCK),
        "epanet_library_present": False,
        "import_error": None,
    }


def _set_actions(wn: Any, action: Mapping[str, float]) -> None:
    from wntr.network import LinkStatus  # type: ignore
    for name, key in (("shower_service", "shower_valve_open"), ("laundry_service", "laundry_valve_open")):
        link = wn.get_link(name)
        status = LinkStatus.Active if action[key] else LinkStatus.Closed
        link._user_status = status  # noqa: SLF001
        link._internal_status = LinkStatus.Active  # noqa: SLF001


def _build_network() -> Any:
    from wntr.network import WaterNetworkModel  # type: ignore
    wn = WaterNetworkModel()
    wn.options.time.duration = HORIZON_SECONDS
    wn.options.time.hydraulic_timestep = int(TICK_SECONDS)
    wn.options.time.report_timestep = int(TICK_SECONDS)
    wn.add_reservoir("municipal_source", base_head=50.0, coordinates=(0.0, 0.0))
    wn.add_tank("house_tank", elevation=25.0, init_level=2.0, min_level=0.0, max_level=4.0, diameter=1.6, coordinates=(100.0, 0.0))
    wn.add_junction("shower", base_demand=0.0030, elevation=10.0, coordinates=(180.0, 20.0))
    wn.add_junction("laundry", base_demand=0.0030, elevation=8.0, coordinates=(180.0, -20.0))
    wn.add_pipe("source_fill", "municipal_source", "house_tank", length=250.0, diameter=0.08, roughness=80.0)
    wn.add_valve("shower_service", "house_tank", "shower", diameter=0.10, valve_type="TCV", minor_loss=3.0, initial_setting=1.0, initial_status="Active")
    wn.add_valve("laundry_service", "house_tank", "laundry", diameter=0.10, valve_type="TCV", minor_loss=3.0, initial_setting=1.0, initial_status="Active")
    # Pressure-dependent emitters make service quality a solved hydraulic
    # consequence, not a post-hoc demand label.
    wn.get_node("shower").add_leak(wn, area=0.00035, discharge_coeff=0.75, start_time=0)
    wn.get_node("laundry").add_leak(wn, area=0.00035, discharge_coeff=0.75, start_time=0)
    return wn


def _observation(result: Any, t: int) -> dict[str, float]:
    pressures, flows = result.node["pressure"], result.link["flowrate"]
    leaks, heads = result.node["leak_demand"], result.node["head"]
    # EPANET's linear solve can vary in the final binary ulp across worker
    # processes.  Pin observations at sub-nanometre/sub-nanoflow precision so
    # deterministic reset evidence reflects physical determinism rather than
    # harmless floating-point presentation noise.
    values = {
        "pressure_shower_m": round(float(pressures.loc[t, "shower"]), 12),
        "pressure_laundry_m": round(float(pressures.loc[t, "laundry"]), 12),
        "flow_shower_m3_s": round(float(flows.loc[t, "shower_service"]), 12),
        "flow_laundry_m3_s": round(float(flows.loc[t, "laundry_service"]), 12),
        "flow_source_fill_m3_s": round(float(flows.loc[t, "source_fill"]), 12),
        "tank_level_m": round(float(heads.loc[t, "house_tank"] - 25.0), 12),
        "served_shower_m3_s": round(float(leaks.loc[t, "shower"]), 12),
        "served_laundry_m3_s": round(float(leaks.loc[t, "laundry"]), 12),
    }
    if not all(math.isfinite(value) for value in values.values()):
        raise RuntimeError("real WNTR produced a non-finite D3 observation")
    return values


def _session_worker_main() -> None:
    sys.path.insert(0, str(RUNTIME_ROOT))
    try:
        from wntr.sim import WNTRSimulator  # type: ignore
        wn = _build_network()
        sim = WNTRSimulator(wn)
        result = None
        print(json.dumps({"type": "ready", "provenance": _dependency_worker()}, separators=(",", ":")), flush=True)
        for line in sys.stdin:
            payload = json.loads(line)
            if payload.get("command") == "close":
                break
            if payload.get('command') == 'inspect_native':
                # Private read-only verifier request, not an agent action.
                if result is None:
                    raise RuntimeError('no native hydraulic result to inspect')
                t = int(result.time[-1])
                snapshot = {key: {str(k): float(v) for k, v in result.node[key].loc[t].items()}
                            for key in ('pressure', 'head', 'leak_demand')}
                snapshot['flowrate'] = {str(k): float(v) for k, v in result.link['flowrate'].loc[t].items()}
                print(json.dumps({'native_time_seconds': t, 'native': snapshot}), flush=True)
                continue
            if payload.get("command") != "step":
                raise ValueError("unknown WNTR session command")
            action = _validate_action(payload["action"])
            duration = int(payload["duration"])
            if duration < int(wn.sim_time):
                raise ValueError("WNTR session time cannot move backwards")
            _set_actions(wn, action)
            wn.options.time.duration = duration
            result = sim.run_sim()
            if not result.time:
                raise RuntimeError("WNTR returned no hydraulic time point")
            t = int(result.time[-1])
            obs = _observation(result, t)
            print(json.dumps({
                "observation": obs, "time_seconds": t, "action": action,
                "terminated": t >= HORIZON_SECONDS, "truncated": False,
            }, separators=(",", ":")), flush=True)
    except Exception as exc:
        print(json.dumps({"type": "error", "error": repr(exc)}, separators=(",", ":")), flush=True)
        raise


def main() -> None:
    if len(sys.argv) < 2:
        return
    if sys.argv[1] == "--dependency-worker":
        print(json.dumps(_dependency_worker(), sort_keys=True, separators=(",", ":")))
    elif sys.argv[1] == "--session-worker":
        _session_worker_main()


if __name__ == "__main__":
    main()
