#!/usr/bin/env python3
"""Compile the real Modelica D3 shared-heat plant to FMI 2.0 CoSimulation."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from d2_modelica_buildings_aixlib_adapter import (
    MODEL_ROOT, MSL_REPO, MSL_ROOT, _library_root, _omc_from_environment,
    _runtime_environment,
)

ROOT = Path(__file__).resolve().parent
D3_ROOT = ROOT / "shared_assets" / "modelica_d3_shared_heat_v1"
MODEL_PACKAGE = D3_ROOT / "D3SharedHeat"
MODEL_SOURCE = D3_ROOT / "D3SharedHeat.mo"
OUTPUT = ROOT / "generated" / "d3_modelica_shared_heat_v1" / "fmu"
MODEL_NAME = "D3SharedHeat.SharedHeatPumpTwoService"


def _find_binary(root: Path) -> Path | None:
    suffix = {"Darwin": ".dylib", "Linux": ".so", "Windows": ".dll"}.get(__import__("platform").system(), ".so")
    for directory in sorted((root / "binaries").glob("*")):
        candidate = directory / f"D3SharedHeat{suffix}"
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    omc = _omc_from_environment()
    if omc is None:
        print("OpenModelica compiler is unavailable", file=sys.stderr)
        return 2
    OUTPUT.mkdir(parents=True, exist_ok=True)
    mos = OUTPUT / "compile_fmu.mos"
    mos.write_text("\n".join([
        f'loadFile("{MSL_REPO / "ModelicaServices" / "package.mo"}");',
        f'loadFile("{MSL_REPO / "Complex.mo"}");',
        f'loadFile("{MSL_ROOT / "package.mo"}");',
        f'loadFile("{_library_root("Buildings") / "package.mo"}");',
        f'loadFile("{_library_root("AixLib") / "package.mo"}");',
        f'loadFile("{MODEL_PACKAGE / "package.mo"}");',
        f'loadFile("{MODEL_SOURCE}");',
        f'translateModelFMU({MODEL_NAME}, version="2.0", fmuType="cs", fileNamePrefix="D3SharedHeat");',
        'getErrorString();',
    ]) + "\n", encoding="utf-8")
    result = subprocess.run([str(omc), str(mos)], cwd=str(OUTPUT), capture_output=True,
                            text=True, timeout=900, check=False,
                            env=_runtime_environment(omc))
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    # The pinned macOS OpenModelica package may emit the FMU but reference a
    # stale Homebrew cmake path.  Its generated Autoconf project is portable;
    # use that project before declaring the real FMU unavailable.
    fmu_root = OUTPUT / "D3SharedHeat.fmutmp"
    if _find_binary(fmu_root) is None and (fmu_root / "sources" / "configure").is_file():
        sources = fmu_root / "sources"
        cppflags = f"-I{ROOT / 'shared_runtime' / 'modelica' / 'openmodelica' / 'include' / 'omc' / 'c' / 'fmi'}"
        configured = subprocess.run(["./configure", "--quiet", f"CPPFLAGS={cppflags}"], cwd=str(sources), capture_output=True, text=True, timeout=300, check=False)
        if configured.returncode == 0:
            built = subprocess.run(["make", "-j2"], cwd=str(sources), capture_output=True, text=True, timeout=900, check=False)
        else:
            built = configured
        sys.stdout.write((built.stdout or "")[-4000:])
        sys.stderr.write((built.stderr or "")[-4000:])
    return 0 if _find_binary(fmu_root) is not None else (result.returncode or 1)


if __name__ == "__main__":
    raise SystemExit(main())
