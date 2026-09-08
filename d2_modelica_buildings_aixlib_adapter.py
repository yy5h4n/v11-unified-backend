#!/usr/bin/env python3
"""Strict backend adapter for a Modelica Buildings/AixLib D2 route.

This module is intentionally independent from the shared adapter registry.  It
only describes the boundary needed to run a real OpenModelica translation and
simulation.  It never contains a surrogate thermal model: when the pinned
compiler, libraries, or model source are absent, calls fail closed.

The model contract is a two-room thermo-hygrometric model with a radiator
valve action.  The authoritative source is kept under
``shared_assets/modelica_d2`` and must import both the Buildings and AixLib
libraries.  A future probe may use the same adapter without changing the
action/observation schema.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import ctypes
import platform
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent
MODEL_ROOT = ROOT / "shared_assets" / "modelica_d2_buildings_aixlib_v1"
MODEL_SOURCE = MODEL_ROOT / "D2BuildingsAixLib.mo"
MODEL_NAME = "D2BuildingsAixLib.TwoRoomThermoHygrometric"
RUN_ROOT = ROOT / "generated" / "d2_modelica_buildings_aixlib_v1" / "runtime_runs"

# A project-local runtime is preferred.  ``omc`` on PATH is only probed; this
# module never installs, downloads, or mutates a global runtime.
LOCAL_OMC_CANDIDATES = (
    ROOT / "shared_runtime" / "modelica" / "openmodelica" / "bin" / "omc",
    ROOT / "shared_runtime" / "openmodelica" / "bin" / "omc",
)

OBSERVATION_ROLES = {
    "room_a_temperature_c": "roomATemperature",
    "room_b_temperature_c": "roomBTemperature",
    "room_a_relative_humidity_pct": "roomARelativeHumidity",
    "room_b_relative_humidity_pct": "roomBRelativeHumidity",
    "heater_heat_flow_w": "heaterHeatFlow",
}
ACTION = {
    "name": "radiator_valve",
    "unit": "1",
    "legal_range": [0.0, 1.0],
    "modelica_parameter": "radiatorValve",
    "semantics": "dimensionless radiator valve command; set online before each FMI co-simulation step",
}
REQUIRED_LIBRARIES = ("Buildings", "AixLib")
MSL_REPO = MODEL_ROOT / "Modelica"
MSL_ROOT = MSL_REPO / "Modelica"
FMU_ROOT = ROOT / "generated" / "d2_modelica_buildings_aixlib_v1" / "fmu" / "D2BuildingsAixLib.fmutmp"
FMU_MODEL_IDENTIFIER = "D2BuildingsAixLib"
FMU_GUID = "{141c19d1-c08f-4a77-a760-7f2d0a2276c7}"
FMU_VARIABLES = {
    "radiatorValve": 40,
    "heaterHeatFlow": 39,
    "roomARelativeHumidity": 59,
    "roomATemperature": 60,
    "roomBRelativeHumidity": 79,
    "roomBTemperature": 80,
}


class ModelicaBackendError(RuntimeError):
    """Base error for unavailable, invalid, or non-conforming Modelica runs."""


class ModelicaRuntimeUnavailable(ModelicaBackendError):
    """Raised when a real compiler/library/model cannot be used."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_action(action: float) -> float:
    value = float(action)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"illegal Modelica radiator_valve action {action!r}; expected finite value in [0, 1]")
    return value


def _omc_from_environment() -> Path | None:
    for candidate in LOCAL_OMC_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    configured = os.environ.get("MODELICA_OMC")
    if configured:
        candidate = Path(configured)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("omc")
    return Path(found) if found else None


def _runtime_environment(omc: Path) -> dict[str, str]:
    """Return a relocatable process environment for the project-local OMC.

    The arm64 nightly package was built against MacPorts paths.  We keep the
    compiler and simulation runtime project-local, while supplying only the
    two small Homebrew runtime dependencies that are not shipped in the pkg.
    In particular, do not add the package's ``lib/omc`` to DYLD_LIBRARY_PATH:
    its bundled libcurl has optional HTTP/3 symbols absent on this host.
    """
    env = os.environ.copy()
    om_root = omc.parent.parent
    env["OPENMODELICAHOME"] = str(om_root)
    env["PATH"] = str(omc.parent) + os.pathsep + env.get("PATH", "")
    dep_paths = [p for p in ("/opt/homebrew/opt/gettext/lib", "/opt/homebrew/opt/libiconv/lib") if Path(p).is_dir()]
    if dep_paths:
        env["DYLD_LIBRARY_PATH"] = os.pathsep.join(dep_paths)
    return env


def _library_root(name: str) -> Path | None:
    configured = os.environ.get(f"MODELICA_{name.upper()}_ROOT")
    candidates = [Path(configured)] if configured else []
    # GitHub source checkouts are kept under a provenance directory and have
    # the Modelica package itself one level below it (e.g. Buildings/Buildings).
    # Accept both that layout and a directly unpacked package root.
    candidates.extend((MODEL_ROOT / name / name, MODEL_ROOT / name, ROOT / "shared_runtime" / "modelica" / name))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _model_source_has_library_imports() -> bool:
    if not MODEL_SOURCE.is_file():
        return False
    text = MODEL_SOURCE.read_text(encoding="utf-8")
    return all(f"import {name}" in text or f"within {name}" in text for name in REQUIRED_LIBRARIES)


def probe_runtime() -> dict[str, Any]:
    """Inspect the real local toolchain without creating a simulation result."""
    omc = _omc_from_environment()
    compiler: dict[str, Any] = {
        "available": omc is not None,
        "path": str(omc) if omc else None,
        "version": None,
        "version_probe_ok": False,
        "error": None,
    }
    if omc:
        try:
            result = subprocess.run(
                [str(omc), "--version"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                env=_runtime_environment(omc),
            )
            version = (result.stdout or result.stderr).strip()
            compiler["version"] = version or None
            compiler["version_probe_ok"] = result.returncode == 0 and bool(version)
            if result.returncode != 0:
                compiler["error"] = f"omc --version exited {result.returncode}"
        except (OSError, subprocess.SubprocessError) as exc:
            compiler["error"] = f"version probe failed: {exc}"

    libraries = {}
    for name in REQUIRED_LIBRARIES:
        root = _library_root(name)
        libraries[name] = {
            "available": root is not None,
            "path": str(root) if root else None,
            "version": None,
            "pinned": False,
            "blocker": "pinned project-local library root is absent" if root is None else "version manifest absent",
        }
        if root:
            # A runtime is not considered pinned merely because a directory is
            # named Buildings/AixLib.  A checked-in VERSION/METADATA file is
            # required before the gate can certify it.
            for metadata in (root / "VERSION", root / "version.txt", root / "package.order"):
                if metadata.is_file() and metadata.name != "package.order":
                    libraries[name]["version"] = metadata.read_text(encoding="utf-8").strip() or None
                    libraries[name]["pinned"] = bool(libraries[name]["version"])
                    libraries[name]["blocker"] = None if libraries[name]["pinned"] else "empty version manifest"
                    break

    model = {
        "available": MODEL_SOURCE.is_file(),
        "path": str(MODEL_SOURCE.relative_to(ROOT)) if MODEL_SOURCE.is_file() else str(MODEL_SOURCE.relative_to(ROOT)),
        "sha256": sha256(MODEL_SOURCE) if MODEL_SOURCE.is_file() else None,
        "model_name": MODEL_NAME,
        "imports_required_libraries": _model_source_has_library_imports(),
    }
    blockers = []
    if not compiler["available"]:
        blockers.append("OPENMODELICA_COMPILER_MISSING")
    elif not compiler["version_probe_ok"]:
        blockers.append("OPENMODELICA_VERSION_PROBE_FAILED")
    for name, record in libraries.items():
        if not record["available"]:
            blockers.append(f"{name.upper()}_LIBRARY_MISSING")
        elif not record["pinned"]:
            blockers.append(f"{name.upper()}_LIBRARY_NOT_PINNED")
    if not model["available"]:
        blockers.append("MODELICA_SOURCE_MISSING")
    elif not model["imports_required_libraries"]:
        blockers.append("MODELICA_SOURCE_LIBRARY_IMPORTS_MISSING")
    return {
        "schema_version": "d2-modelica-buildings-aixlib-runtime-probe-v1",
        "backend": "Modelica",
        "compiler": compiler,
        "libraries": libraries,
        "model": model,
        "required_libraries": list(REQUIRED_LIBRARIES),
        "blockers": blockers,
        "available": not blockers,
        "surrogate_model_used": False,
    }


def _find_fmu_binary(root: Path = FMU_ROOT) -> Path | None:
    """Locate the compiled FMI binary without silently substituting a model."""
    if not root.is_dir():
        return None
    suffixes = {"Darwin": (".dylib",), "Linux": (".so",), "Windows": (".dll",)}
    for binary_root in sorted((root / "binaries").glob("*")):
        for suffix in suffixes.get(platform.system(), (".so", ".dylib", ".dll")):
            candidate = binary_root / f"{FMU_MODEL_IDENTIFIER}{suffix}"
            if candidate.is_file():
                return candidate
    return None


_FMI2Logger = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p)
_FMI2Allocator = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t)
_FMI2Deallocator = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class _FMI2Callbacks(ctypes.Structure):
    _fields_ = [
        ("logger", _FMI2Logger),
        ("allocateMemory", _FMI2Allocator),
        ("freeMemory", _FMI2Deallocator),
        ("stepFinished", ctypes.c_void_p),
        ("componentEnvironment", ctypes.c_void_p),
    ]


class _FMI2Session:
    """Small FMI 2.0 co-simulation binding using the generated FMU ABI.

    Keeping this binding local avoids making fmpy/pyfmi a hidden dependency.
    The FMU is still the compiled OpenModelica model; this class only invokes
    the standard FMI lifecycle and ``fmi2DoStep`` entry points.
    """

    _Status = ctypes.c_int
    _Real = ctypes.c_double
    _ValueReference = ctypes.c_uint32
    _Component = ctypes.c_void_p
    def __init__(self, root: Path = FMU_ROOT) -> None:
        binary = _find_fmu_binary(root)
        description = root / "modelDescription.xml"
        if binary is None or not description.is_file():
            raise ModelicaRuntimeUnavailable(
                "compiled FMI 2.0 co-simulation FMU is unavailable; run compile_d2_modelica_fmu.py"
            )
        self.root = root
        self.binary = binary
        self.description = description
        self._lib = ctypes.CDLL(str(binary))
        libc = ctypes.CDLL(None)
        libc.malloc.restype = ctypes.c_void_p
        libc.malloc.argtypes = [ctypes.c_size_t]
        libc.free.argtypes = [ctypes.c_void_p]
        self._libc = libc
        self._logger = _FMI2Logger(lambda *_args: None)
        self._allocator = _FMI2Allocator(lambda n, size: libc.malloc(n * size))
        self._deallocator = _FMI2Deallocator(lambda ptr: libc.free(ptr))
        self._callbacks = _FMI2Callbacks(self._logger, self._allocator, self._deallocator, None, None)
        self._component: ctypes.c_void_p | None = None
        self.time = 0.0
        self._configure_functions()

    def _configure_functions(self) -> None:
        P, I, D = ctypes.c_void_p, ctypes.c_int, ctypes.c_double
        VR = ctypes.POINTER(self._ValueReference)
        RP = ctypes.POINTER(self._Real)
        self._instantiate = self._function("fmi2Instantiate", P, [ctypes.c_char_p, I, ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_FMI2Callbacks), I, I])
        self._setup = self._function("fmi2SetupExperiment", I, [P, I, D, D, I, D])
        self._enter_init = self._function("fmi2EnterInitializationMode", I, [P])
        self._exit_init = self._function("fmi2ExitInitializationMode", I, [P])
        self._set_time = self._function("fmi2SetTime", I, [P, D])
        self._set_real = self._function("fmi2SetReal", I, [P, VR, ctypes.c_size_t, RP])
        self._get_real = self._function("fmi2GetReal", I, [P, VR, ctypes.c_size_t, RP])
        self._do_step = self._function("fmi2DoStep", I, [P, D, D, I])
        self._terminate = self._function("fmi2Terminate", I, [P])
        self._free_instance = self._function("fmi2FreeInstance", None, [P])

    def _function(self, name: str, result: Any, args: list[Any]) -> Any:
        function = getattr(self._lib, name)
        function.restype = result
        function.argtypes = args
        return function

    @staticmethod
    def _check(status: int, operation: str) -> None:
        # FMI warning (1) is recoverable; discard/error/fatal are not.
        if int(status) > 1:
            raise ModelicaBackendError(f"{operation} returned FMI status {int(status)}")

    def start(self, initial_action: float = 0.0) -> None:
        # FMI resourceLocation is a directory URI.  The trailing slash is
        # required by the generated OpenModelica FMU when resolving resources.
        resource = ((self.root / "resources").resolve().as_uri() + "/").encode("utf-8")
        self._component = self._instantiate(b"d2_modelica_agent", 1, FMU_GUID.encode("ascii"), resource, ctypes.byref(self._callbacks), 0, 0)
        if not self._component:
            raise ModelicaBackendError("fmi2Instantiate returned NULL")
        try:
            self._check(self._setup(self._component, 0, 0.0, 0.0, 1, 3600.0), "fmi2SetupExperiment")
            self._check(self._enter_init(self._component), "fmi2EnterInitializationMode")
            self.set_action(initial_action)
            self._check(self._exit_init(self._component), "fmi2ExitInitializationMode")
            self.time = 0.0
        except Exception:
            self.close()
            raise

    def set_action(self, action: float) -> None:
        if self._component is None:
            raise ModelicaBackendError("FMI session is not initialized")
        vr = (self._ValueReference * 1)(FMU_VARIABLES["radiatorValve"])
        values = (self._Real * 1)(float(action))
        self._check(self._set_real(self._component, vr, 1, values), "fmi2SetReal(radiatorValve)")

    def get_observation(self) -> dict[str, float]:
        if self._component is None:
            raise ModelicaBackendError("FMI session is not initialized")
        roles = (
            ("heater_heat_flow_w", "heaterHeatFlow"),
            ("room_a_relative_humidity_pct", "roomARelativeHumidity"),
            ("room_a_temperature_c", "roomATemperature"),
            ("room_b_relative_humidity_pct", "roomBRelativeHumidity"),
            ("room_b_temperature_c", "roomBTemperature"),
        )
        references = (self._ValueReference * len(roles))(*(FMU_VARIABLES[name] for _, name in roles))
        values = (self._Real * len(roles))()
        self._check(self._get_real(self._component, references, len(roles), values), "fmi2GetReal")
        observation = {role: float(value) for (role, _), value in zip(roles, values)}
        if not all(math.isfinite(value) for value in observation.values()):
            raise ModelicaBackendError("FMU returned a non-finite observation")
        return observation

    def step(self, action: float, dt_seconds: float) -> None:
        if self._component is None:
            raise ModelicaBackendError("FMI session is not initialized")
        self.set_action(action)
        current = self.time
        self._check(self._do_step(self._component, current, dt_seconds, 1), "fmi2DoStep")
        self.time = current + dt_seconds

    def close(self) -> None:
        if self._component is not None:
            try:
                self._terminate(self._component)
            finally:
                self._free_instance(self._component)
                self._component = None


class ModelicaBuildingsAixLibAdapter:
    """Interactive D2 adapter backed by a compiled FMI 2.0 co-simulation FMU."""

    def __init__(self, run_root: Path = RUN_ROOT) -> None:
        self.run_root = Path(run_root)
        self._reset_seed: int | None = None
        self._last_trace_digest: str | None = None
        self._session: _FMI2Session | None = None
        self._current_action = 0.0

    def capabilities(self) -> dict[str, Any]:
        """Return a backend-only capability contract, never a certification."""
        evidence = probe_runtime()
        return {
            "backend": "Modelica",
            "backend_family": "Buildings/AixLib",
            "physical_process_id": "modelica:d2_two_room_thermo_hygrometric",
            "provides": tuple(OBSERVATION_ROLES),
            "action": ACTION,
            "runtime_available": evidence["available"],
            "interactive_step": _find_fmu_binary() is not None,
            "status": "REAL_RUNTIME_PROBED" if evidence["available"] else "EVIDENCE_PENDING",
            "blockers": tuple(evidence["blockers"]),
            "surrogate_model_used": False,
        }

    def reset(self, seed: int = 0) -> dict[str, Any]:
        if int(seed) != seed:
            raise ValueError("Modelica reset seed must be an integer")
        self._reset_seed = int(seed)
        self._last_trace_digest = None
        if self._session is not None:
            self._session.close()
            self._session = None
        self._require_runtime()
        self._session = _FMI2Session()
        self._session.start(initial_action=0.0)
        self._current_action = 0.0
        # Keep reset consistent with the other D2 adapters: it returns the
        # actual initial observation, and unavailable runtimes raise above.
        return self.observe()

    def _require_runtime(self) -> tuple[Path, dict[str, Any]]:
        evidence = probe_runtime()
        if not evidence["available"]:
            raise ModelicaRuntimeUnavailable(
                "Modelica Buildings/AixLib runtime is unavailable; refusing surrogate execution: "
                + ", ".join(evidence["blockers"])
            )
        omc = Path(evidence["compiler"]["path"])
        return omc, evidence

    def legal_actions(self) -> dict[str, Any]:
        return {"name": ACTION["name"], "unit": ACTION["unit"], "legal_range": list(ACTION["legal_range"])}

    def observe(self) -> dict[str, float]:
        if self._session is None:
            raise ModelicaRuntimeUnavailable("interactive FMI session is unavailable; call reset() after compiling the FMU")
        return self._session.get_observation()

    def close(self) -> None:
        """Release the native FMU instance and its loaded model state."""
        if self._session is not None:
            self._session.close()
            self._session = None

    def step(self, action: float, dt_seconds: float = 60.0) -> dict[str, Any]:
        action = validate_action(action)
        dt_seconds = float(dt_seconds)
        if not math.isfinite(dt_seconds) or dt_seconds <= 0.0:
            raise ValueError("dt_seconds must be finite and positive")
        if self._session is None:
            raise ModelicaRuntimeUnavailable("interactive FMI session is unavailable; call reset() first")
        self._session.step(action, dt_seconds)
        self._current_action = action
        observation = self.observe()
        return {
            "time_seconds": self._session.time,
            "observation": observation,
            "action": {"radiator_valve": action},
            "done": self._session.time >= 3600.0,
            "terminal": self._session.time >= 3600.0,
        }

    def run(self, action: float, label: str, replicate: int = 1) -> dict[str, Any]:
        action = validate_action(action)
        if not label or any(char in label for char in "/\\"):
            raise ValueError("label must be a non-empty path-safe token")
        if int(replicate) != replicate or int(replicate) < 1:
            raise ValueError("replicate must be a positive integer")
        if self._reset_seed is None:
            raise ModelicaBackendError("call reset(seed=...) before every Modelica run")
        omc, evidence = self._require_runtime()
        if self._session is None:
            raise ModelicaBackendError("call reset(seed=...) before every Modelica run")
        trace = [{"step": 0, "time_s": 0.0, "action_radiator_valve": self._current_action, "observation": self.observe()}]
        for _ in range(60):
            transition = self.step(action, 60.0)
            trace.append({"step": len(trace), "time_s": transition["time_seconds"], "action_radiator_valve": action, "observation": transition["observation"]})
        digest = canonical_digest(trace)
        self._last_trace_digest = digest
        return {
            "label": label,
            "action": action,
            "replicate": int(replicate),
            "trace": trace,
            "trace_digest": digest,
            "provenance": {
                "backend": "Modelica",
                "compiler_path": str(omc),
                "compiler_version": evidence["compiler"]["version"],
                "model_name": MODEL_NAME,
                "model_source_path": str(MODEL_SOURCE.relative_to(ROOT)),
                "model_source_sha256": sha256(MODEL_SOURCE),
                "library_roots": {name: evidence["libraries"][name]["path"] for name in REQUIRED_LIBRARIES},
                "library_versions": {name: evidence["libraries"][name]["version"] for name in REQUIRED_LIBRARIES},
                "reset_semantics": "new FMI co-simulation instance per reset; state preserved across doStep calls",
                "fmi_standard": "FMI 2.0 CoSimulation",
                "fmi_model_identifier": FMU_MODEL_IDENTIFIER,
                "fmu_binary": str(_find_fmu_binary()),
                "action": ACTION,
                "observables": OBSERVATION_ROLES,
                "surrogate_model_used": False,
            },
        }

__all__ = [
    "ACTION",
    "MODEL_NAME",
    "MODEL_SOURCE",
    "FMU_ROOT",
    "ModelicaBackendError",
    "ModelicaBuildingsAixLibAdapter",
    "ModelicaRuntimeUnavailable",
    "canonical_digest",
    "probe_runtime",
    "sha256",
    "validate_action",
]
