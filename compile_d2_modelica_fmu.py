#!/usr/bin/env python3
"""Compile the D2 Buildings/AixLib model to a real FMI 2.0 co-simulation FMU."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from d2_modelica_buildings_aixlib_adapter import (
    MODEL_ROOT, MODEL_SOURCE, MODEL_NAME, MSL_REPO, MSL_ROOT,
    _library_root, _omc_from_environment, _runtime_environment, _find_fmu_binary,
)

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "generated" / "d2_modelica_buildings_aixlib_v1" / "fmu"


def main() -> int:
    omc = _omc_from_environment()
    if omc is None:
        print("OpenModelica compiler is unavailable", file=sys.stderr)
        return 2
    OUTPUT.mkdir(parents=True, exist_ok=True)
    mos = OUTPUT / "compile_fmu.mos"
    mos.write_text(
        "\n".join(
            [
                f'loadFile("{MSL_REPO / "ModelicaServices" / "package.mo"}");',
                f'loadFile("{MSL_REPO / "Complex.mo"}");',
                f'loadFile("{MSL_ROOT / "package.mo"}");',
                f'loadFile("{_library_root("Buildings") / "package.mo"}");',
                f'loadFile("{_library_root("AixLib") / "package.mo"}");',
                f'loadFile("{MODEL_ROOT / "D2BuildingsAixLib" / "package.mo"}");',
                f'loadFile("{MODEL_SOURCE}");',
                f'translateModelFMU({MODEL_NAME}, version="2.0", fmuType="cs", fileNamePrefix="D2BuildingsAixLib");',
                "getErrorString();",
            ]
        ) + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(omc), str(mos)], cwd=str(OUTPUT), capture_output=True, text=True,
        timeout=900, check=False, env=_runtime_environment(omc),
    )
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    # Some OpenModelica distributions return success after generating the FMU
    # sources even when their CMake path is stale.  The generated FMU sources
    # include the portable Autoconf fallback; use it when CMake did not leave
    # a native binary behind.
    fmu_root = OUTPUT / "D2BuildingsAixLib.fmutmp"
    if _find_fmu_binary(fmu_root) is None:
        sources = fmu_root / "sources"
        cppflags = f"-I{ROOT / 'shared_runtime' / 'modelica' / 'openmodelica' / 'include' / 'omc' / 'c' / 'fmi'}"
        configured = subprocess.run(
            ["./configure", "--quiet", f"CPPFLAGS={cppflags}"],
            cwd=str(sources), capture_output=True, text=True, timeout=300, check=False,
        )
        built = subprocess.run(
            ["make", "-j2"], cwd=str(sources), capture_output=True, text=True,
            timeout=900, check=False,
        ) if configured.returncode == 0 else configured
        sys.stdout.write((built.stdout or "")[-4000:])
        sys.stderr.write((built.stderr or "")[-4000:])
        if _find_fmu_binary(fmu_root) is None:
            return built.returncode or result.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
