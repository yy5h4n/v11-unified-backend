#!/usr/bin/env python3
"""Fast, fail-closed preflight for the durable local V11 checkout.

This command does not generate Episodes.  It checks that the checkout is not
under a disposable directory, that the pinned local runtime roots exist, that
the public route registry imports, and that the checked-in acceptance package
is internally consistent.  Run it before a campaign; run the full acceptance
campaign separately when fresh native evidence is required.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
V10_ROOT = PROJECT_ROOT / "v10_diversity_aware_compiler"
V5_ROOT = PROJECT_ROOT / "v5_scenario_compiler"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(label: str, command: list[str], *, env: dict[str, str] | None = None) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{label}: {type(exc).__name__}: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, f"{label}: exit={result.returncode}: {detail[-1] if detail else 'no output'}"
    return True, f"{label}: ok"


def _check_file(label: str, path: Path, *, executable: bool = False) -> tuple[bool, str]:
    if not path.is_file():
        return False, f"{label}: missing {path}"
    if executable and not os.access(path, os.X_OK):
        return False, f"{label}: not executable {path}"
    return True, f"{label}: {path}"


def _check_dir(label: str, path: Path) -> tuple[bool, str]:
    if not path.is_dir():
        return False, f"{label}: missing {path}"
    return True, f"{label}: {path}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="require all native runtime roots and caches")
    args = parser.parse_args()
    failures: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []

    if any(part in {"tmp", "private"} for part in ROOT.parts) or str(ROOT).startswith("/private/tmp/"):
        failures.append(f"checkout is under a disposable path: {ROOT}")
    else:
        checks.append(f"durable checkout: {ROOT}")

    if not sys.version_info >= (3, 11):
        failures.append(f"Python >=3.11 required, got {platform.python_version()}")
    else:
        checks.append(f"Python {platform.python_version()} ({sys.executable})")

    # Compile and import the dependency-light public surface first.
    ok, detail = _run("compile", [sys.executable, "-m", "compileall", "-q", "unified_compiler", "tools", "tests"])
    (checks if ok else failures).append(detail)
    try:
        from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
        from unified_compiler.agent_interface import make_agent_backend  # noqa: F401
        if len(PUBLIC_ROUTE_IDS) != 15:
            failures.append(f"public route count is {len(PUBLIC_ROUTE_IDS)}, expected 15")
        else:
            checks.append("public route registry: 15 routes")
    except Exception as exc:  # pragma: no cover - diagnostic path
        failures.append(f"public backend import failed: {type(exc).__name__}: {exc}")

    required_files = {
        "EnergyPlus": ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64/energyplus",
        "FDS": ROOT / "shared_runtime/fds/bin/fds",
        "OpenModelica": ROOT / "shared_runtime/modelica/openmodelica/bin/omc",
        "CityLearn asset manifest": ROOT / "shared_assets/citylearn_v2.5.0/asset_manifest.json",
        "V5 runtime bootstrap": V5_ROOT / "runtime_bootstrap.py",
        "V5 source schema": V5_ROOT / "source_cache/schema.json",
        "EV2Gym checkout": V10_ROOT / ".runtime/ev2gym/ev2gym/models/ev2gym_env.py",
        "EV2Gym interpreter": V10_ROOT / ".runtime/venv/bin/python",
    }
    for label, path in required_files.items():
        ok, detail = _check_file(label, path, executable=label in {"EnergyPlus", "FDS", "OpenModelica"})
        if ok:
            checks.append(detail)
        elif args.strict:
            failures.append(detail)
        else:
            warnings.append(detail)

    required_dirs = {
        "WNTR site-packages": ROOT / "shared_runtime/wntr-site-packages/wntr",
        "SustainGym site-packages": ROOT / "shared_runtime/sustaingym-site-packages/sustaingym",
        "CityLearn support site-packages": ROOT / "shared_runtime/site-packages",
        "CityLearn cache": Path.home() / "Library/Caches/citylearn/v2.5.0",
        "UV archive cache": Path.home() / ".cache/uv/archive-v0",
    }
    for label, path in required_dirs.items():
        ok, detail = _check_dir(label, path)
        if ok:
            checks.append(detail)
        elif args.strict:
            failures.append(detail)
        else:
            warnings.append(detail)

    # Verify the provenance package itself, including source hashes and the
    # 15-route acceptance matrix.  This is intentionally read-only.
    acceptance_dir = ROOT / "acceptance/backend_acceptance_local_final"
    ok, detail = _run(
        "acceptance evidence",
        [sys.executable, "tools/backend_acceptance_runner.py", "--check", "--output-dir", str(acceptance_dir)],
    )
    (checks if ok else failures).append(detail)

    report = {"schema": "v11.local_preflight.v1", "root": str(ROOT), "strict": args.strict, "checks": checks, "warnings": warnings, "failures": failures}
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
