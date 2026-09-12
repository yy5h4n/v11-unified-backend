#!/usr/bin/env python3
"""Agent-facing D3 Modelica route with a finite shared heat source.

The route keeps one FMI 2.0 CoSimulation instance alive for the whole episode.
Both service commands are sent through FMI ``setReal`` before every native
``fmi2DoStep``.  The thermal zones and DHW storage therefore remain native
Modelica/Buildings/AixLib states; this module contains no thermal surrogate.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import platform
import shutil
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from d2_modelica_buildings_aixlib_adapter import (
    MODEL_ROOT, REQUIRED_LIBRARIES, _library_root, _omc_from_environment,
    _runtime_environment, probe_runtime, sha256,
)

ROOT = Path(__file__).resolve().parent
D3_ROOT = ROOT / "shared_assets" / "modelica_d3_shared_heat_v1"
MODEL_SOURCE = ROOT / "environment_repairs_v1/models/D3SharedHeat.mo"
FMU_ROOT = ROOT / "environment_repairs_v1/build/d3/active"
MODEL_NAME = "D3SharedHeat.SharedHeatPumpTwoService"
COMPILE_SCRIPT = ROOT / "tools/compile_repaired_modelica.py"
ADAPTER_SCRIPT = Path(__file__).resolve()
DEFAULT_DT_SECONDS = 60.0
DEFAULT_HORIZON_SECONDS = 3600.0
ACTION_NAMES = ("space_heating_request", "dhw_request")
OBSERVATION_NAMES = (
    "room_a_temperature_c", "room_b_temperature_c", "dhw_temperature_c",
    "allocated_space_heat_w", "allocated_dhw_heat_w",
    "shared_heat_pump_capacity_used_w", "service_shortfall_w",
)
NATIVE_VARIABLE_NAMES = {
    "space_heating_request": "spaceHeatingRequest",
    "dhw_request": "dhwRequest",
    "room_a_temperature_c": "roomATemperature",
    "room_b_temperature_c": "roomBTemperature",
    "dhw_temperature_c": "dhwTemperature",
    "allocated_space_heat_w": "allocatedSpaceHeatW",
    "allocated_dhw_heat_w": "allocatedDhwHeatW",
    "shared_heat_pump_capacity_used_w": "sharedHeatPumpCapacityUsedW",
    "service_shortfall_w": "serviceShortfallW",
}


class D3ModelicaError(RuntimeError):
    pass


class D3ModelicaActionError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _tree_sha256(root: Path) -> str | None:
    """Content digest for the complete compiled FMU tree, including binary."""
    if not root.is_dir():
        return None
    entries: list[tuple[str, str]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        entries.append((str(path.relative_to(root)), sha256(path)))
    return _digest(entries)


def _binary(root: Path = FMU_ROOT) -> Path | None:
    system = platform.system()
    suffixes = {"Darwin": (".dylib",), "Linux": (".so",), "Windows": (".dll",)}.get(system, (".so", ".dylib", ".dll"))
    for directory in sorted((root / "binaries").glob("*")):
        for suffix in suffixes:
            path = directory / f"D3SharedHeat{suffix}"
            if path.is_file():
                return path
    return None


def _variables(description: Path) -> tuple[str, dict[str, int]]:
    try:
        xml = ET.parse(description).getroot()
        model_identifier = xml.find("./{*}CoSimulation").attrib["modelIdentifier"]
        refs: dict[str, int] = {}
        for variable in xml.findall("./{*}ModelVariables/{*}ScalarVariable"):
            name = variable.attrib.get("name", "")
            real = variable.find("./{*}Real")
            public_names = [public for public, native in NATIVE_VARIABLE_NAMES.items() if native == name]
            if real is not None and public_names:
                refs[public_names[0]] = int(variable.attrib["valueReference"])
        needed = set(ACTION_NAMES + OBSERVATION_NAMES)
        missing = sorted(needed - set(refs))
        if missing:
            raise D3ModelicaError(f"D3 FMU modelDescription lacks variables: {missing}")
        return model_identifier, refs
    except (ET.ParseError, KeyError, ValueError) as exc:
        raise D3ModelicaError(f"invalid D3 FMU modelDescription: {description}") from exc


_Logger = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p)
_Allocator = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t)
_Deallocator = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class _Callbacks(ctypes.Structure):
    _fields_ = [("logger", _Logger), ("allocateMemory", _Allocator), ("freeMemory", _Deallocator),
                ("stepFinished", ctypes.c_void_p), ("componentEnvironment", ctypes.c_void_p)]


class _Session:
    """Minimal FMI2 CS binding; every action is a native FMI setReal."""
    Real = ctypes.c_double
    VR = ctypes.c_uint32
    Component = ctypes.c_void_p

    def __init__(self, root: Path = FMU_ROOT) -> None:
        binary = _binary(root)
        description = root / "modelDescription.xml"
        if binary is None or not description.is_file():
            raise D3ModelicaError("compiled D3 Modelica FMU unavailable; run compile_d3_modelica_shared_heat_fmu.py")
        self.root = root
        self.binary = binary
        self.model_identifier, self.refs = _variables(description)
        self.guid = ET.parse(description).getroot().attrib.get("guid")
        if not self.guid:
            raise D3ModelicaError("D3 FMU has no GUID")
        self.lib = ctypes.CDLL(str(binary))
        libc = ctypes.CDLL(None)
        libc.malloc.restype = ctypes.c_void_p
        libc.malloc.argtypes = [ctypes.c_size_t]
        libc.free.argtypes = [ctypes.c_void_p]
        # Keep FMI diagnostics visible while developing the native route.  In
        # normal probe output the runtime itself remains the authority; these
        # messages are only emitted on a failed native call.
        def _log(_env: Any, _instance: bytes, status: int, category: bytes, message: bytes) -> None:
            if int(status) >= 2:
                print(f"[D3 Modelica FMI {int(status)}] {category!r}: {message!r}")
        self._logger = _Logger(_log)
        self._allocator = _Allocator(lambda n, size: libc.malloc(n * size))
        self._deallocator = _Deallocator(lambda ptr: libc.free(ptr))
        self._callbacks = _Callbacks(self._logger, self._allocator, self._deallocator, None, None)
        self._libc = libc
        self.component: ctypes.c_void_p | None = None
        self.time = 0.0
        P, I, D = ctypes.c_void_p, ctypes.c_int, ctypes.c_double
        VR = ctypes.POINTER(self.VR)
        RP = ctypes.POINTER(self.Real)
        self.instantiate = self._fn("fmi2Instantiate", P, [ctypes.c_char_p, I, ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_Callbacks), I, I])
        self.setup = self._fn("fmi2SetupExperiment", I, [P, I, D, D, I, D])
        self.enter = self._fn("fmi2EnterInitializationMode", I, [P])
        self.exit = self._fn("fmi2ExitInitializationMode", I, [P])
        self.set_real = self._fn("fmi2SetReal", I, [P, VR, ctypes.c_size_t, RP])
        self.get_real = self._fn("fmi2GetReal", I, [P, VR, ctypes.c_size_t, RP])
        self.do_step = self._fn("fmi2DoStep", I, [P, D, D, I])
        self.terminate = self._fn("fmi2Terminate", I, [P])
        self.free_instance = self._fn("fmi2FreeInstance", None, [P])

    def _fn(self, name: str, result: Any, args: list[Any]) -> Any:
        fn = getattr(self.lib, name)
        fn.restype, fn.argtypes = result, args
        return fn

    @staticmethod
    def _check(status: int, operation: str) -> None:
        if int(status) > 1:
            raise D3ModelicaError(f"{operation} returned FMI status {int(status)}")

    def start(self, horizon_seconds: float = DEFAULT_HORIZON_SECONDS) -> None:
        resource = self.root.resolve().as_uri().encode()
        self.component = self.instantiate(b"d3_shared_heat_agent", 1, self.guid.encode(), resource, ctypes.byref(self._callbacks), 0, 0)
        if not self.component:
            raise D3ModelicaError("fmi2Instantiate returned NULL")
        try:
            # Stop time is the FMI experiment boundary, not the Agent tick;
            # keeping it beyond the public horizon allows multiple doStep calls.
            self._check(self.setup(self.component, 0, 0.0, 0.0, 1, horizon_seconds), "fmi2SetupExperiment")
            self._check(self.enter(self.component), "fmi2EnterInitializationMode")
            self.set_actions(0.0, 0.0)
            self._check(self.exit(self.component), "fmi2ExitInitializationMode")
        except Exception:
            self.close()
            raise

    def set_actions(self, space: float, dhw: float) -> None:
        if self.component is None:
            raise D3ModelicaError("FMI session is not initialized")
        refs = (self.VR * 2)(self.refs[ACTION_NAMES[0]], self.refs[ACTION_NAMES[1]])
        values = (self.Real * 2)(space, dhw)
        self._check(self.set_real(self.component, refs, 2, values), "fmi2SetReal(actions)")

    def observation(self) -> dict[str, float]:
        if self.component is None:
            raise D3ModelicaError("FMI session is not initialized")
        refs = (self.VR * len(OBSERVATION_NAMES))(*(self.refs[n] for n in OBSERVATION_NAMES))
        values = (self.Real * len(OBSERVATION_NAMES))()
        self._check(self.get_real(self.component, refs, len(OBSERVATION_NAMES), values), "fmi2GetReal(observations)")
        result = {name: float(value) for name, value in zip(OBSERVATION_NAMES, values)}
        if not all(math.isfinite(value) for value in result.values()):
            raise D3ModelicaError("D3 FMU returned non-finite observation")
        return result

    def step(self, space: float, dhw: float, dt: float) -> None:
        if self.component is None:
            raise D3ModelicaError("FMI session is not initialized")
        self.set_actions(space, dhw)
        end = self.time + dt
        while self.time < end - 1e-9:
            substep = min(1.0, end-self.time)
            self._check(self.do_step(self.component, self.time, substep, 1), "fmi2DoStep")
            self.time += substep

    def close(self) -> None:
        if self.component is not None:
            try:
                self.terminate(self.component)
            finally:
                self.free_instance(self.component)
                self.component = None


def validate_action(action: Any) -> tuple[float, float]:
    if not isinstance(action, Mapping) or set(action) != set(ACTION_NAMES):
        raise D3ModelicaActionError(f"action must contain exactly {ACTION_NAMES}")
    values = []
    for name in ACTION_NAMES:
        value = action[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
            raise D3ModelicaActionError(f"{name} must be finite in [0, 1]")
        values.append(float(value))
    return values[0], values[1]


def probe_runtime_d3() -> dict[str, Any]:
    base = probe_runtime()
    modelica = {"available": MODEL_SOURCE.is_file(), "path": str(MODEL_SOURCE.relative_to(ROOT)), "sha256": sha256(MODEL_SOURCE) if MODEL_SOURCE.is_file() else None}
    fmu = {"available": _binary() is not None and (FMU_ROOT / "modelDescription.xml").is_file(), "path": str(FMU_ROOT.relative_to(ROOT))}
    blockers = list(base["blockers"])
    if not modelica["available"]:
        blockers.append("D3_MODELICA_SOURCE_MISSING")
    if not fmu["available"]:
        blockers.append("D3_MODELICA_FMU_MISSING")
    return {"schema_version": "d3-modelica-shared-heat-runtime-probe-v1", "backend": "Modelica", "model": modelica, "fmu": fmu, "blockers": sorted(set(blockers)), "available": not blockers, "surrogate_model_used": False}


class D3ModelicaSharedHeatRoute:
    """One real FMI session with independent space/DHW actions and shared capacity."""
    def __init__(self, *, horizon_seconds: float = DEFAULT_HORIZON_SECONDS) -> None:
        if isinstance(horizon_seconds, bool) or not isinstance(horizon_seconds, (int, float)) or not math.isfinite(horizon_seconds) or horizon_seconds <= 0 or horizon_seconds % DEFAULT_DT_SECONDS:
            raise D3ModelicaActionError("horizon_seconds must be a positive multiple of the 60 second tick")
        self.horizon_seconds = float(horizon_seconds)
        self._session: _Session | None = None
        self._latest: dict[str, float] | None = None
        self._done = False

    @property
    def session(self) -> _Session:
        if self._session is None:
            raise D3ModelicaError("reset() must be called before use")
        return self._session

    def reset(self, seed: int = 0) -> dict[str, float]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise D3ModelicaActionError("seed must be an integer")
        self.close()
        self._session = _Session()
        self._session.start(self.horizon_seconds)
        self._latest = self._session.observation()
        self._done = False
        return deepcopy(self._latest)

    def observe(self) -> dict[str, float]:
        if self._latest is None:
            raise D3ModelicaError("reset() must be called before observe()")
        return deepcopy(self._latest)

    def legal_actions(self) -> dict[str, Any]:
        return {"native": True, "type": "fmi2_real", "channels": {name: {"range": [0.0, 1.0], "unit": "1"} for name in ACTION_NAMES},
                "shared_capacity_w": 1500.0,
                "request_full_scale_w": {"space_heating_request":1800.0,"dhw_request":1200.0},
                "allocation": "proportional when total requested heat exceeds shared capacity"}

    def step(self, action: Any, dt_seconds: float = DEFAULT_DT_SECONDS) -> dict[str, Any]:
        if self._latest is None:
            raise D3ModelicaError("reset() must be called before step()")
        if self._done:
            raise D3ModelicaActionError("D3 episode is already done")
        if isinstance(dt_seconds, bool) or not isinstance(dt_seconds, (int, float)) or not math.isfinite(float(dt_seconds)) or float(dt_seconds) <= 0:
            raise D3ModelicaActionError("dt_seconds must be finite and positive")
        dt = float(dt_seconds)
        space, dhw = validate_action(action)
        remaining = self.horizon_seconds - self.session.time
        if dt > remaining + 1e-9:
            raise D3ModelicaActionError("dt_seconds would pass the episode horizon")
        before = self.session.time
        self.session.step(space, dhw, dt)
        self._latest = self.session.observation()
        self._done = self.session.time >= self.horizon_seconds - 1e-9
        return {"time_seconds": self.session.time, "delta_t_seconds": dt, "action": {ACTION_NAMES[0]: space, ACTION_NAMES[1]: dhw}, "observation": deepcopy(self._latest), "done": self._done, "info": {"native_fmi_do_step": True, "previous_time_seconds": before, "model": MODEL_NAME}}

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
        self._session = None
        self._latest = None
        self._done = False


def probe_d3_modelica(*, steps: int = 3, dt_seconds: float = DEFAULT_DT_SECONDS) -> dict[str, Any]:
    if steps < 2 or int(steps) != steps:
        raise D3ModelicaActionError("steps must be an integer >= 2")
    route = D3ModelicaSharedHeatRoute(horizon_seconds=float(steps) * float(dt_seconds))
    try:
        route.reset(seed=0)
        baseline = [route.step({"space_heating_request": 0.0, "dhw_request": 0.0}, dt_seconds) for _ in range(steps)]
        route.reset(seed=0)
        space = route.step({"space_heating_request": 1.0, "dhw_request": 1.0}, dt_seconds)
        route.reset(seed=0)
        dhw = route.step({"space_heating_request": 0.0, "dhw_request": 1.0}, dt_seconds)
        route.reset(seed=0)
        split = route.step({"space_heating_request": 1.0, "dhw_request": 0.0}, dt_seconds)
        route.reset(seed=0)
        repeat = route.step({"space_heating_request": 0.0, "dhw_request": 0.0}, dt_seconds)
        if baseline[0] != repeat:
            raise D3ModelicaError("D3 Modelica reset is not deterministic")
        # Both requests are active under the combined action, so finite source
        # capacity must leave an observable service shortfall.
        combined_shortfall = space["observation"]["service_shortfall_w"]
        if combined_shortfall <= 1e-9:
            raise D3ModelicaError("shared heat-pump capacity did not bind under combined request")
        if abs(space["observation"]["allocated_dhw_heat_w"] - dhw["observation"]["allocated_dhw_heat_w"]) <= 1e-9:
            raise D3ModelicaError("space request did not change DHW allocation")
        if abs(space["observation"]["allocated_space_heat_w"] - split["observation"]["allocated_space_heat_w"]) <= 1e-9:
            raise D3ModelicaError("DHW request did not change space allocation")
        return {"schema_version": "d3-modelica-shared-heat-probe-v1", "passed": True, "native_fmi_do_step": True, "steps": int(steps), "deterministic_reset": True, "shared_capacity_gate": True, "baseline": baseline, "combined": space, "dhw_only": dhw, "space_only": split, "provenance": {
            "model_source": str(MODEL_SOURCE.relative_to(ROOT)),
            "model_source_sha256": sha256(MODEL_SOURCE),
            "adapter": str(ADAPTER_SCRIPT.relative_to(ROOT)),
            "adapter_sha256": sha256(ADAPTER_SCRIPT),
            "compiler": str(COMPILE_SCRIPT.relative_to(ROOT)),
            "compiler_sha256": sha256(COMPILE_SCRIPT),
            "fmu_root": str(FMU_ROOT.relative_to(ROOT)),
            "fmu_sha256": _tree_sha256(FMU_ROOT),
            "runtime": probe_runtime_d3(),
        }}
    finally:
        route.close()


if __name__ == "__main__":
    print(json.dumps(probe_d3_modelica(), indent=2, sort_keys=True))
