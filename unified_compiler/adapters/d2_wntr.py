"""Backend-only D2 residential water adapter with an isolated WNTR worker.

The parent process intentionally imports no WNTR/NumPy/Pandas symbols and
does not modify ``sys.path``. Every dependency probe and simulation runs in a
short-lived child process whose path starts with the project-local WNTR
runtime. This prevents its NumPy 1.x ABI from contaminating other backends.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import os
import subprocess
import sys
import threading
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "shared_runtime/wntr-site-packages"
RUNTIME_LOCK = ROOT / "shared_runtime/wntr_runtime_requirements.lock"


class WNTRDependencyError(RuntimeError):
    """Raised when the isolated project-local WNTR runtime is unavailable."""


class WNTRActionError(ValueError):
    """Raised when an isolation-valve action is malformed."""


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_action(action: Any) -> float:
    if isinstance(action, bool):
        return float(action)
    try:
        value = float(action)
    except (TypeError, ValueError) as exc:
        raise WNTRActionError("isolation_valve_open must be 0 or 1") from exc
    if not math.isfinite(value) or value not in (0.0, 1.0):
        raise WNTRActionError("isolation_valve_open must be 0 or 1")
    return value


def _worker_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(RUNTIME_ROOT)
    return env


def _call_worker(mode: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode],
        input=(json.dumps(payload, sort_keys=True) if payload is not None else ""),
        text=True, capture_output=True, cwd=str(ROOT), env=_worker_env(), check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise WNTRDependencyError(f"isolated WNTR worker failed ({completed.returncode}): {detail[-1000:]}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WNTRDependencyError(f"isolated WNTR worker returned invalid JSON: {completed.stdout[-500:]}") from exc
    if not isinstance(result, dict):
        raise WNTRDependencyError("isolated WNTR worker returned a non-object")
    return result


def dependency_status() -> dict[str, Any]:
    """Probe WNTR only in a child interpreter; no parent import side effects."""
    try:
        return _call_worker("--dependency-worker")
    except WNTRDependencyError as exc:
        return {
            "wntr_importable": False, "wntr_version": None,
            "wntr_package_path": str((RUNTIME_ROOT / "wntr/__init__.py").relative_to(ROOT)),
            "wntr_package_init_sha256": sha256(RUNTIME_ROOT / "wntr/__init__.py"),
            "runtime_lock_path": str(RUNTIME_LOCK.relative_to(ROOT)), "runtime_lock_sha256": sha256(RUNTIME_LOCK),
            "import_error": str(exc), "epanet_library_present": False, "epanet_library_candidates": [],
        }


class WNTRResidentialWaterAdapter:
    """Deterministic real-WNTR backend with a persistent online worker.

    ``run`` remains the compatibility whole-trajectory replay.  The
    Agent-facing API keeps one ``WaterNetworkModel`` and one ``WNTRSimulator``
    alive in an isolated child process. Each ``step`` calls WNTR's native
    extended-period solver for exactly one hydraulic timestep, preserving the
    model's tank heads/previous values between calls; it never reruns a
    completed prefix.
    """
    backend_name = "WNTR"
    backend_engine = "WNTRSimulator"

    def __init__(self) -> None:
        status = dependency_status()
        if not status.get("wntr_importable"):
            raise WNTRDependencyError(status.get("import_error", "WNTR unavailable"))
        self._reset_seed: int | None = None
        self._session_proc: subprocess.Popen[str] | None = None
        self._session_lock = threading.Lock()
        self._session_observation: dict[str, float] | None = None
        self._session_time_seconds = 0
        self._session_terminated = False
        self._session_step_seconds = 3600
        self._session_horizon_seconds = 6 * 3600

    def reset(self, seed: int = 0) -> dict[str, Any]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("WNTR reset seed must be an integer")
        self._close_session()
        self._reset_seed = seed
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--session-worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(ROOT), env=_worker_env(), bufsize=1,
        )
        self._session_proc = proc
        ready = self._read_session_message()
        if ready.get("type") != "ready":
            self._close_session()
            raise WNTRDependencyError(f"WNTR session failed to initialize: {ready}")
        initial = self._session_request({"command": "step", "action": 0.0, "duration": 0})
        self._session_observation = dict(initial["observation"])
        self._session_time_seconds = int(initial["time_seconds"])
        self._session_terminated = False
        return dict(self._session_observation)

    def legal_actions(self) -> dict[str, Any]:
        return {"action_isolation_valve_open": {"type": "discrete", "values": [0.0, 1.0]}}

    def close(self) -> None:
        """Stop the isolated live worker without advancing the network."""
        self._close_session()

    def observe(self) -> dict[str, float]:
        if self._session_observation is None:
            raise RuntimeError("call reset(seed) before observe()")
        return dict(self._session_observation)

    def step(self, action: Any, delta_t: float = 3600.0, *, steps: int | None = None) -> dict[str, Any]:
        action_value = validate_action(action)
        if self._session_observation is None or self._session_proc is None:
            raise RuntimeError("call reset(seed) before step()")
        if self._session_terminated:
            raise RuntimeError("WNTR episode is terminated; call reset()")
        if steps is not None:
            if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
                raise ValueError("steps must be a positive integer")
            count = steps
            requested = float(steps * self._session_step_seconds)
        else:
            requested = float(delta_t)
            count = int(round(requested / self._session_step_seconds)) if math.isfinite(requested) else 0
            if count < 1 or abs(requested - count * self._session_step_seconds) > 1e-6:
                raise ValueError("delta_t must be an integer multiple of 3600 seconds")
        result: dict[str, Any] | None = None
        executed = 0
        for _ in range(count):
            if self._session_terminated:
                break
            duration = self._session_time_seconds + self._session_step_seconds
            result = self._session_request({"command": "step", "action": action_value, "duration": duration})
            self._session_observation = dict(result["observation"])
            self._session_time_seconds = int(result["time_seconds"])
            self._session_terminated = bool(result["terminated"])
            executed += 1
        if result is None:
            raise RuntimeError("WNTR episode is terminated; call reset()")
        result["action"] = action_value
        result["delta_t_seconds"] = self._session_step_seconds if executed == 1 else executed * self._session_step_seconds
        result["steps"] = executed
        result["done"] = bool(result["terminated"] or result["truncated"])
        result["info"] = {"backend": "WNTR", "online": True, "persistent_worker": True}
        return result

    def _read_session_message(self) -> dict[str, Any]:
        proc = self._session_proc
        if proc is None or proc.stdout is None:
            raise WNTRDependencyError("WNTR session worker is not running")
        line = proc.stdout.readline()
        if not line:
            detail = proc.stderr.read()[-1000:] if proc.stderr else ""
            raise WNTRDependencyError(f"WNTR session worker exited unexpectedly: {detail}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WNTRDependencyError(f"WNTR session worker returned invalid JSON: {line[-500:]}") from exc
        if not isinstance(value, dict):
            raise WNTRDependencyError("WNTR session worker returned a non-object")
        if value.get("type") == "error":
            raise WNTRDependencyError(str(value.get("error", "WNTR session error")))
        return value

    def _session_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._session_lock:
            proc = self._session_proc
            if proc is None or proc.stdin is None:
                raise WNTRDependencyError("WNTR session worker is not running")
            proc.stdin.write(json.dumps(payload, sort_keys=True) + "\n")
            proc.stdin.flush()
            return self._read_session_message()

    def _close_session(self) -> None:
        proc = self._session_proc
        if proc is None:
            return
        try:
            if proc.stdin is not None and proc.poll() is None:
                proc.stdin.write(json.dumps({"command": "close"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=10.0)
        except (OSError, subprocess.TimeoutExpired):
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
        finally:
            self._session_proc = None
            self._session_observation = None
            self._session_terminated = False

    def run(self, action: Any, label: str = "replay", replicate: int = 1) -> dict[str, Any]:
        valve_open = validate_action(action)
        if not isinstance(label, str) or not label or any(char in label for char in "/\\"):
            raise ValueError("label must be a non-empty path-safe string")
        if isinstance(replicate, bool) or not isinstance(replicate, int) or replicate < 1:
            raise ValueError("replicate must be a positive integer")
        if self._reset_seed is None:
            raise RuntimeError("call reset(seed) before run")
        return _call_worker("--run-worker", {"action": valve_open, "label": label, "replicate": replicate, "seed": self._reset_seed})


def _dependency_worker() -> dict[str, Any]:
    sys.path.insert(0, str(RUNTIME_ROOT))
    try:
        import wntr  # type: ignore
        from wntr.network import WaterNetworkModel  # noqa: F401
        from wntr.sim import WNTRSimulator  # noqa: F401
    except Exception as exc:
        raise RuntimeError(f"WNTR import failed: {exc!r}") from exc
    candidates = (
        RUNTIME_ROOT / "wntr/epanet/libepanet/darwin-arm/libepanet2.dylib",
        RUNTIME_ROOT / "wntr/epanet/libepanet/linux-x64/libepanet.so",
        RUNTIME_ROOT / "wntr/epanet/libepanet/win-x64/epanet2.dll",
    )
    return {
        "wntr_importable": True, "wntr_version": getattr(wntr, "__version__", None),
        "wntr_package_path": str((RUNTIME_ROOT / "wntr/__init__.py").relative_to(ROOT)),
        "wntr_package_init_sha256": sha256(RUNTIME_ROOT / "wntr/__init__.py"),
        "runtime_lock_path": str(RUNTIME_LOCK.relative_to(ROOT)), "runtime_lock_sha256": sha256(RUNTIME_LOCK),
        "import_error": None, "epanet_library_candidates": [str(p.relative_to(ROOT)) for p in candidates],
        "epanet_library_present": any(p.is_file() for p in candidates),
    }


def _set_valve_action(wn: Any, action: float) -> None:
    """Apply the online action to the existing network before one solve."""
    from wntr.network import LinkStatus  # type: ignore
    link = wn.get_link("house_isolation")
    status = LinkStatus.Active if action else LinkStatus.Closed
    link._user_status = status  # noqa: SLF001
    link._internal_status = LinkStatus.Active  # noqa: SLF001


def _session_worker_main() -> None:
    """Serve one-step commands while retaining one WNTR model and simulator."""
    sys.path.insert(0, str(RUNTIME_ROOT))
    try:
        from wntr.sim import WNTRSimulator  # type: ignore
        wn = _build_network(0.0)
        sim = WNTRSimulator(wn)
        print(json.dumps({"type": "ready", "provenance": _dependency_worker()}, separators=(",", ":")), flush=True)
        for line in sys.stdin:
            payload = json.loads(line)
            if payload.get("command") == "close":
                break
            if payload.get("command") != "step":
                raise ValueError("unknown WNTR session command")
            action = validate_action(payload["action"])
            duration = int(payload["duration"])
            if duration < int(wn.sim_time):
                raise ValueError("WNTR session time cannot move backwards")
            _set_valve_action(wn, action)
            wn.options.time.duration = duration
            result = sim.run_sim()
            if not result.time:
                raise RuntimeError("WNTR returned no time point")
            t = int(result.time[-1])
            pressures, flows = result.node["pressure"], result.link["flowrate"]
            leaks, heads = result.node["leak_demand"], result.node["head"]
            obs = {
                "pressure_kitchen_m": float(pressures.loc[t, "kitchen"]), "pressure_bathroom_m": float(pressures.loc[t, "bathroom"]),
                "flow_source_fill_m3_s": float(flows.loc[t, "source_fill"]), "flow_house_isolation_m3_s": float(flows.loc[t, "house_isolation"]),
                "tank_level_m": float(heads.loc[t, "house_tank"] - 25.0), "leak_kitchen_m3_s": float(leaks.loc[t, "kitchen"]),
                "leak_bathroom_m3_s": float(leaks.loc[t, "bathroom"]),
            }
            obs["leak_total_m3_s"] = obs["leak_kitchen_m3_s"] + obs["leak_bathroom_m3_s"]
            if not all(math.isfinite(value) for value in obs.values()):
                raise RuntimeError("real WNTR produced a non-finite live observation")
            print(json.dumps({"observation": obs, "time_seconds": t, "action": action,
                              "terminated": t >= 6 * 3600, "truncated": False}, separators=(",", ":")), flush=True)
    except Exception as exc:
        print(json.dumps({"type": "error", "error": repr(exc)}, separators=(",", ":")), flush=True)
        raise


def _build_network(action: float) -> Any:
    from wntr.network import WaterNetworkModel  # type: ignore
    wn = WaterNetworkModel()
    wn.options.time.duration = 6 * 3600
    wn.options.time.hydraulic_timestep = 3600
    wn.options.time.report_timestep = 3600
    wn.add_reservoir("municipal_source", base_head=50.0, coordinates=(0.0, 0.0))
    wn.add_tank("house_tank", elevation=25.0, init_level=3.0, min_level=0.0, max_level=6.0, diameter=4.0, coordinates=(100.0, 0.0))
    wn.add_junction("kitchen", base_demand=0.0010, elevation=10.0, coordinates=(180.0, 20.0))
    wn.add_junction("bathroom", base_demand=0.0005, elevation=8.0, coordinates=(240.0, -20.0))
    wn.add_pipe("source_fill", "municipal_source", "house_tank", length=100.0, diameter=0.25, roughness=100.0)
    wn.add_valve("house_isolation", "house_tank", "kitchen", diameter=0.20, valve_type="TCV", minor_loss=2.0, initial_setting=1.0, initial_status="Active" if action else "Closed")
    isolation = wn.get_link("house_isolation")
    isolation._user_status = isolation.initial_status  # noqa: SLF001
    wn.add_pipe("house_branch", "kitchen", "bathroom", length=60.0, diameter=0.15, roughness=100.0)
    wn.get_node("kitchen").add_leak(wn, area=0.00020, discharge_coeff=0.75, start_time=0)
    wn.get_node("bathroom").add_leak(wn, area=0.00010, discharge_coeff=0.75, start_time=0)
    return wn


def _run_worker(payload: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(RUNTIME_ROOT))
    from wntr.sim import WNTRSimulator  # type: ignore
    action = validate_action(payload["action"])
    try:
        result = WNTRSimulator(_build_network(action)).run_sim()
    except Exception as exc:
        raise RuntimeError(f"real WNTR simulation failed closed: {exc}") from exc
    rows: list[dict[str, Any]] = []
    pressures, flows = result.node["pressure"], result.link["flowrate"]
    leaks, heads = result.node["leak_demand"], result.node["head"]
    for step, time_seconds in enumerate(pressures.index):
        obs = {
            "pressure_kitchen_m": float(pressures.loc[time_seconds, "kitchen"]), "pressure_bathroom_m": float(pressures.loc[time_seconds, "bathroom"]),
            "flow_source_fill_m3_s": float(flows.loc[time_seconds, "source_fill"]), "flow_house_isolation_m3_s": float(flows.loc[time_seconds, "house_isolation"]),
            "tank_level_m": float(heads.loc[time_seconds, "house_tank"] - 25.0), "leak_kitchen_m3_s": float(leaks.loc[time_seconds, "kitchen"]),
            "leak_bathroom_m3_s": float(leaks.loc[time_seconds, "bathroom"]),
        }
        obs["leak_total_m3_s"] = obs["leak_kitchen_m3_s"] + obs["leak_bathroom_m3_s"]
        if not all(math.isfinite(value) for value in obs.values()):
            raise RuntimeError("real WNTR produced a non-finite observation")
        rows.append({"step": step, "time_seconds": int(time_seconds), "action_isolation_valve_open": action, "observation": obs})
    if not rows:
        raise RuntimeError("real WNTR returned an empty trajectory")
    return {
        "label": payload["label"], "action": action, "replicate": int(payload["replicate"]), "trace": rows, "trace_digest": canonical_digest(rows),
        "provenance": {
            **_dependency_worker(), "backend": "WNTR", "backend_engine": "WNTRSimulator",
            "network_definition": "municipal reservoir + elevated tank + two-junction house with TCV and pressure-dependent leaks",
            "reset_semantics": "new WaterNetworkModel and WNTRSimulator for every run; seed recorded and no stochastic source",
            "action_actuator": {"field": "action_isolation_valve_open", "component": "house_isolation", "type": "TCV", "legal_action_values": [0.0, 1.0], "semantics": "0 closes house isolation valve; 1 opens it"},
            "observation_units": {"pressure": "m", "flow": "m3/s", "tank_level": "m", "leak": "m3/s"},
        },
    }


def _worker_main() -> None:
    try:
        if sys.argv[1] == "--dependency-worker":
            value = _dependency_worker()
            print(json.dumps(value, sort_keys=True, separators=(",", ":")))
        elif sys.argv[1] == "--session-worker":
            _session_worker_main()
            return
        else:
            value = _run_worker(json.loads(sys.stdin.read()))
            print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    except Exception as exc:
        print(f"worker error: {exc!r}", file=sys.stderr)
        raise


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1].endswith("worker"):
    _worker_main()
