#!/usr/bin/env python3
"""Small, real-FDS backend adapter for smoke propagation.

The adapter intentionally has no numerical fallback.  A run is either made by
the Fire Dynamics Simulator executable and parsed from its device CSV, or it
fails closed.  The door action is compiled into a fresh two-room FDS input for
each run; this is the supported offline control surface for this probe and is
not a surrogate transition model.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
RUN_ROOT = ROOT / "generated/d2_fds_v1/runtime_runs"
MODEL_TEMPLATE = ROOT / "d2_fds_assets/d2_fds_two_room_template.fds"


class FDSRuntimeUnavailable(RuntimeError):
    """Raised when a real FDS executable is not available or cannot run."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _provenance_path(path: Path) -> str:
    """Use a stable project-relative path when possible, else preserve temp roots."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def validate_action(action: float) -> float:
    value = float(action)
    if not math.isfinite(value) or value not in (0.0, 1.0):
        raise ValueError("FDS door action must be exactly 0.0 (closed) or 1.0 (open)")
    return value


def _candidate_paths() -> list[Path]:
    """Return project-local and PATH candidates, without installing anything."""
    values: list[str] = []
    configured = os.environ.get("FDS_EXECUTABLE")
    if configured:
        values.append(configured)
    values.extend(
        [
            str(ROOT / "shared_runtime/fds/bin/fds"),
            str(ROOT / "shared_runtime/fds/fds"),
        ]
    )
    for name in ("fds", "fds_mpi"):
        found = shutil.which(name)
        if found:
            values.append(found)
    result: list[Path] = []
    for value in values:
        path = Path(value).expanduser()
        if path not in result:
            result.append(path)
    return result


def discover_fds_runtime(runtime: str | Path | None = None) -> dict[str, Any]:
    """Inspect a candidate executable; never downloads or installs a runtime."""
    candidates = [Path(runtime).expanduser()] if runtime else _candidate_paths()
    checked = []
    for path in candidates:
        record = {
            "path": str(path),
            "exists": path.is_file(),
            "executable": os.access(path, os.X_OK),
        }
        if record["exists"] and record["executable"]:
            record["sha256"] = sha256(path)
            version = subprocess.run(
                [str(path), "-version"], capture_output=True, text=True, timeout=30, check=False
            )
            version_text = (version.stdout or version.stderr).strip()
            record["version_output"] = version_text
            record["host_architecture"] = platform.machine()
            record["binary_architecture"] = "x86_64" if "x86_64" in subprocess.run(
                ["file", str(path)], capture_output=True, text=True, check=False
            ).stdout else "unknown"
            record["execution_mode"] = (
                "Rosetta 2 translation"
                if record["host_architecture"] == "arm64" and record["binary_architecture"] == "x86_64"
                else "native"
            )
            record["selected"] = True
            checked.append(record)
            return {
                "available": True,
                "selected_path": str(path),
                "checked_candidates": checked,
                "blocker": None,
            }
        record["selected"] = False
        checked.append(record)
    return {
        "available": False,
        "selected_path": None,
        "checked_candidates": checked,
        "blocker": "FDS_RUNTIME_MISSING",
    }


def _model_text(action: float) -> str:
    """Build a minimal two-room smoke/fire/door FDS input deck.

    Room 1 contains a small propane burner.  A partition at x=2 m separates
    room 2; action 1 removes the inert door panel, while action 0 keeps it.
    Device CSV values in room 2 provide temperature and visibility evidence.
    """
    if not MODEL_TEMPLATE.is_file():
        raise FDSRuntimeUnavailable(f"FDS model template is missing: {MODEL_TEMPLATE}")
    door_panel = (
        "&OBST XB=1.95,2.05,0.70,1.30,0.00,2.00, SURF_ID='INERT' /\n"
        if action == 0.0
        else "! door panel absent: the 0.60 m opening is open\n"
    )
    return MODEL_TEMPLATE.read_text(encoding="utf-8").replace(
        "{{DOOR_ACTION_COMMENT}}", f"action door_open_fraction={action:.1f}"
    ).replace("{{DOOR_PANEL}}", door_panel)


def _finite(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite FDS device value")
    return parsed


def _read_device_csv(path: Path, action: float) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FDSRuntimeUnavailable(f"FDS device output is missing: {path.name}")
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        # FDS DEVC CSV emits a units row before the named header, e.g.
        # ``s,C,m,m/s`` followed by ``Time,...``.  Consume that metadata row
        # explicitly; treating it as the header makes every real FDS run look
        # like a missing-device failure.
        first_line = handle.readline()
        if not first_line:
            raise FDSRuntimeUnavailable("FDS device output is empty")
        rows = list(csv.DictReader(handle, skipinitialspace=True))
    if not rows:
        raise FDSRuntimeUnavailable("FDS device output contains no rows")
    required = ("Time", "ROOM_B_TEMPERATURE", "ROOM_B_VISIBILITY", "ROOM_B_VELOCITY")
    if any(column not in rows[0] for column in required):
        raise FDSRuntimeUnavailable(f"FDS device output lacks required columns: {required}")
    trace = []
    for index, row in enumerate(rows):
        observation = {
            "room_b_temperature_c": _finite(row["ROOM_B_TEMPERATURE"]),
            "room_b_visibility_m": _finite(row["ROOM_B_VISIBILITY"]),
            "room_b_velocity_mps": _finite(row["ROOM_B_VELOCITY"]),
        }
        trace.append(
            {
                "step": index,
                "time_s": _finite(row["Time"]),
                "action_door_open_fraction": action,
                "observation": observation,
            }
        )
    return trace


def _interactive_model_text(schedule: list[tuple[float, float]], horizon_s: float) -> str:
    """Build an FDS deck with a time-controlled door action schedule.

    FDS does not expose a Python ``step`` API.  Its official control surface
    is a ``DEVC`` time signal feeding a ``CTRL``/``RAMP``; the obstruction is
    therefore opened/closed by FDS itself while the fire and flow solver runs.
    A step call regenerates the *input schedule* and replays the accumulated
    trajectory from time zero.  This is deliberately slower than an in-place
    simulator, but preserves real FDS physics and avoids pretending that a
    hand-written state update is a physical transition.
    """
    if not schedule:
        raise ValueError("interactive FDS schedule must contain an initial action")
    if not math.isfinite(horizon_s) or horizon_s < 0.0:
        raise ValueError("interactive FDS horizon must be finite and non-negative")
    previous = -1.0
    # A second command at the same instant is a normal occurrence when the
    # first online action is issued after reset.  FDS ramps require strictly
    # increasing time points, so retain the last command at each timestamp.
    normalized: dict[float, float] = {}
    ramp_rows: list[str] = []
    for time_s, action in schedule:
        if not math.isfinite(time_s) or time_s < 0.0 or time_s < previous:
            raise ValueError("interactive FDS schedule times must be finite and monotone")
        if time_s > horizon_s:
            raise ValueError("interactive FDS action schedule cannot extend past horizon")
        previous = time_s
        normalized[time_s] = action
    ordered_schedule = sorted(normalized.items())
    for time_s, action in ordered_schedule:
        # For an OBST, FDS interprets a true controller (positive RAMP value)
        # as "obstruction exists".  Therefore agent action 0 (closed) maps to
        # +1 and action 1 (open) maps to -1.  All propagation remains in FDS.
        control_value = -1.0 if action == 1.0 else 1.0
        ramp_rows.append(f"&RAMP ID='DOOR_RAMP', T={time_s:.9f}, F={control_value:.1f} /")
    if len(ramp_rows) == 1:
        # FDS rejects a one-point RAMP.  The epsilon endpoint is only a
        # definition of the constant control and does not advance physics.
        time_s, action = ordered_schedule[0]
        epsilon_time = max(horizon_s, time_s) + max(1.0e-6, horizon_s * 1.0e-6)
        control_value = -1.0 if action == 1.0 else 1.0
        ramp_rows.append(f"&RAMP ID='DOOR_RAMP', T={epsilon_time:.9f}, F={control_value:.1f} /")
    base = MODEL_TEMPLATE.read_text(encoding="utf-8")
    dynamic_door = "\n".join(
        [
            "&OBST XB=1.95,2.05,0.70,1.30,0.00,2.00, SURF_ID='INERT', CTRL_ID='DOOR_CTRL' /",
            "&DEVC ID='STEP_CLOCK', XYZ=0.10,0.10,0.10, QUANTITY='TIME' /",
            "&CTRL ID='DOOR_CTRL', FUNCTION_TYPE='CUSTOM', INPUT_ID='STEP_CLOCK', RAMP_ID='DOOR_RAMP' /",
            *ramp_rows,
        ]
    )
    return (
        base.replace("&TIME T_END=4.0 /", f"&TIME T_END={horizon_s:.9f} /")
        .replace("{{DOOR_ACTION_COMMENT}}", "interactive schedule controlled by official FDS DEVC/CTRL/RAMP")
        .replace("{{DOOR_PANEL}}", dynamic_door)
    )
class FDSSmokePropagationAdapter:
    """Fresh-process adapter over a pinned, externally supplied FDS binary."""

    backend = "FDS"
    backend_version = "unknown-until-runtime-probe"
    action_spec = {
        "name": "door_open_fraction",
        "legal_action_range": [0.0, 1.0],
        "legal_values": [0.0, 1.0],
        "implementation": "official FDS DEVC/CTRL/RAMP schedule controlling inert door obstruction",
        "replay_semantics": "step(action, dt_seconds) reruns the accumulated schedule through real FDS; no online continuation is claimed",
            "execution_capability": "prefix_replay",
            "online_step_supported": False,
    }

    def __init__(self, run_root: Path = RUN_ROOT, runtime: str | Path | None = None, timeout_s: float = 300.0) -> None:
        self.run_root = Path(run_root)
        self.runtime_override = runtime
        self.timeout_s = float(timeout_s)
        self._last_trace_digest: str | None = None
        self._interactive_time_s = 0.0
        self._interactive_schedule: list[tuple[float, float]] = []
        self._interactive_observation: dict[str, float] | None = None
        self._interactive_trace: list[dict[str, Any]] = []

    def reset(self, seed: int = 0) -> dict[str, Any]:
        if int(seed) != seed:
            raise ValueError("FDS reset seed must be an integer")
        self._last_trace_digest = None
        self._interactive_time_s = 0.0
        self._interactive_schedule = []
        self._interactive_observation = None
        self._interactive_trace = []
        # Keep the public backend boundary identical to the other D2 routes:
        # reset returns only the initial observation.  Runtime/provenance
        # details remain available on step() and are never mixed into the
        # agent-visible state.
        return self.observe()

    def legal_actions(self) -> dict[str, Any]:
        """Return the action contract exposed to an online agent."""
        return dict(self.action_spec)

    def observe(self) -> dict[str, float]:
        """Return the current physical observation, computed by real FDS.

        The first observation is obtained from a zero-duration FDS run.  It is
        not synthesized from defaults, which is important for a closed-loop
        agent: an unavailable runtime fails closed instead of returning a
        plausible-looking state.
        """
        if not self._interactive_schedule:
            result = self._ensure_interactive_run([(0.0, 0.0)], 0.0)
            self._interactive_schedule = [(0.0, 0.0)]
            self._interactive_observation = dict(result["observation"])
            self._interactive_trace = result["trace"]
        if self._interactive_observation is None:
            raise FDSRuntimeUnavailable("FDS did not produce an initial observation")
        return dict(self._interactive_observation)

    def step(self, action: float, dt_seconds: float) -> dict[str, Any]:
        """Advance the real FDS trajectory after applying ``action``.

        Since FDS's supported external control boundary is a time-dependent
        ``DEVC``/``CTRL`` schedule rather than an in-process callback, each
        call replays the accumulated schedule from t=0.  The returned state is
        always parsed from the terminal FDS device row; no surrogate update or
        state interpolation is used.
        """
        action = validate_action(action)
        dt = float(dt_seconds)
        if not math.isfinite(dt) or dt <= 0.0:
            raise ValueError("FDS step dt_seconds must be a finite positive number")
        if not self._interactive_schedule:
            self.observe()
        next_time = self._interactive_time_s + dt
        if next_time > 4.0 + 1e-9:
            raise ValueError("FDS interactive horizon is limited to 4.0 seconds for this model")
        # The new command takes effect at the beginning of this interval.
        schedule = [*self._interactive_schedule, (self._interactive_time_s, action)]
        result = self._ensure_interactive_run(schedule, next_time)
        self._interactive_schedule = schedule
        self._interactive_time_s = next_time
        self._interactive_observation = dict(result["observation"])
        self._interactive_trace = result["trace"]
        return {
            "time_seconds": next_time,
            "observation": dict(result["observation"]),
            "action": action,
            "done": math.isclose(next_time, 4.0, abs_tol=1e-9),
            "terminal": math.isclose(next_time, 4.0, abs_tol=1e-9),
            "trace_digest": result["trace_digest"],
            "provenance": result["provenance"],
        }

    def _ensure_interactive_run(
        self, schedule: list[tuple[float, float]], horizon_s: float
    ) -> dict[str, Any]:
        runtime = discover_fds_runtime(self.runtime_override)
        if not runtime["available"]:
            raise FDSRuntimeUnavailable("FDS runtime unavailable; refusing surrogate execution")
        # Every transition owns a unique directory.  Reusing a schedule/time
        # name and deleting it could destroy a concurrent episode's files.
        output = self.run_root / f"interactive_{os.getpid()}_{uuid.uuid4().hex}"
        output.mkdir(parents=True, exist_ok=True)
        input_path = output / "d2_fds_two_room_interactive.fds"
        # A zero-duration FDS run is rejected by the solver.  We still obtain
        # the exact t=0 device row from a minimal real run and return that row
        # as the reset observation.
        simulation_horizon = max(horizon_s, 0.01)
        deck = _interactive_model_text(schedule, simulation_horizon).replace(
            "&HEAD CHID='d2_fds_two_room'",
            "&HEAD CHID='d2_fds_two_room_interactive'",
        )
        input_path.write_text(deck, encoding="utf-8")
        try:
            completed = subprocess.run(
                [runtime["selected_path"], input_path.name],
                cwd=output,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FDSRuntimeUnavailable(f"FDS interactive process could not complete: {exc}") from exc
        if completed.returncode != 0:
            raise FDSRuntimeUnavailable(
                f"FDS interactive run exited with code {completed.returncode}: {completed.stderr[-500:]}"
            )
        trace_path = output / "d2_fds_two_room_interactive_devc.csv"
        trace = _read_device_csv(trace_path, schedule[-1][1])
        # Preserve the action that was active at each physical sample rather
        # than labelling the entire replay with its terminal command.
        for row in trace:
            active_action = schedule[0][1]
            for transition_time, transition_action in schedule:
                if transition_time <= row["time_s"] + 1e-8:
                    active_action = transition_action
                else:
                    break
            row["action_door_open_fraction"] = active_action
        if not trace:
            raise FDSRuntimeUnavailable("FDS interactive device output contains no rows")
        # FDS emits a final row at T_END; require it to be sufficiently close
        # instead of silently returning an earlier observation.
        terminal = trace[-1]
        if horizon_s == 0.0:
            terminal = trace[0]
        elif not math.isclose(float(terminal["time_s"]), horizon_s, abs_tol=1e-5):
            raise FDSRuntimeUnavailable(
                f"FDS interactive trace ended at {terminal['time_s']} rather than requested {horizon_s}"
            )
        return {
            "observation": dict(terminal["observation"]),
            "trace": trace,
            "trace_digest": canonical_digest(trace),
            "provenance": {
                "backend": self.backend,
                "runtime_version": runtime["checked_candidates"][0].get("version_output"),
                "runtime_architecture": runtime["checked_candidates"][0].get("binary_architecture"),
                "host_architecture": runtime["checked_candidates"][0].get("host_architecture"),
                "execution_mode": runtime["checked_candidates"][0].get("execution_mode"),
                "runtime_path": runtime["selected_path"],
                "runtime_sha256": runtime["checked_candidates"][0].get("sha256"),
                "input_deck_sha256": sha256(input_path),
                "source_template_sha256": sha256(MODEL_TEMPLATE),
                "source_template": str(MODEL_TEMPLATE.relative_to(ROOT)),
                "input_deck": _provenance_path(input_path),
                "action_schedule": [{"time_seconds": t, "door_open_fraction": a} for t, a in schedule],
                "requested_horizon_seconds": horizon_s,
                "simulation_horizon_seconds": simulation_horizon,
                "reset_semantics": "fresh FDS process replay from t=0; official DEVC/CTRL/RAMP action schedule",
                "interactive_step": False,
                "continuation_mode": "full_history_real_backend_replay_not_online",
                "surrogate_fallback": False,
                "output_file": "d2_fds_two_room_interactive_devc.csv",
            },
        }

    def run(self, action: float, label: str, replicate: int = 1) -> dict[str, Any]:
        action = validate_action(action)
        if not label or any(char in label for char in "/\\"):
            raise ValueError("label must be a non-empty path-safe token")
        if int(replicate) != replicate or int(replicate) < 1:
            raise ValueError("replicate must be a positive integer")
        runtime = discover_fds_runtime(self.runtime_override)
        if not runtime["available"]:
            raise FDSRuntimeUnavailable("FDS runtime unavailable; refusing surrogate execution")

        output = self.run_root / f"{label}_{int(replicate)}_{os.getpid()}_{uuid.uuid4().hex}"
        output.mkdir(parents=True, exist_ok=True)
        input_path = output / "d2_fds_two_room.fds"
        input_path.write_text(_model_text(action), encoding="utf-8")
        try:
            completed = subprocess.run(
                [runtime["selected_path"], input_path.name],
                cwd=output,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FDSRuntimeUnavailable(f"FDS process could not complete: {exc}") from exc
        if completed.returncode != 0:
            raise FDSRuntimeUnavailable(f"FDS exited with code {completed.returncode}: {completed.stderr[-500:]}")
        trace = _read_device_csv(output / "d2_fds_two_room_devc.csv", action)
        trace_digest = canonical_digest(trace)
        self._last_trace_digest = trace_digest
        return {
            "label": label,
            "action": action,
            "replicate": int(replicate),
            "trace": trace,
            "trace_digest": trace_digest,
            "provenance": {
                "backend": self.backend,
                "runtime_version": runtime["checked_candidates"][0].get("version_output"),
                "runtime_architecture": runtime["checked_candidates"][0].get("binary_architecture"),
                "host_architecture": runtime["checked_candidates"][0].get("host_architecture"),
                "execution_mode": runtime["checked_candidates"][0].get("execution_mode"),
                "runtime_path": runtime["selected_path"],
                "runtime_sha256": runtime["checked_candidates"][0].get("sha256"),
                "input_deck_sha256": sha256(input_path),
                "source_template_sha256": sha256(MODEL_TEMPLATE),
                "source_template": str(MODEL_TEMPLATE.relative_to(ROOT)),
                "input_deck": str(input_path.relative_to(ROOT)),
                "action_spec": self.action_spec,
                "reset_semantics": "new FDS process and freshly generated immutable deck for every run",
                "output_file": "d2_fds_two_room_devc.csv",
            },
        }


__all__ = [
    "FDSRuntimeUnavailable",
    "FDSSmokePropagationAdapter",
    "canonical_digest",
    "discover_fds_runtime",
    "sha256",
    "validate_action",
]
