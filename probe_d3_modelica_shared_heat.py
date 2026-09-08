#!/usr/bin/env python3
"""Run the D3 Modelica shared-heat native conformance probe."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from d3_modelica_shared_heat_adapter import probe_d3_modelica


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=3)
    parser.add_argument("--dt-seconds", type=float, default=60.0)
    parser.add_argument("--output", type=str)
    parser.add_argument("--check", action="store_true", help="re-probe and reject a stale existing gate report")
    args = parser.parse_args()
    report = probe_d3_modelica(steps=args.steps, dt_seconds=args.dt_seconds)
    report_path = Path(__file__).resolve().parent / "generated" / "d3_modelica_shared_heat_v1" / "gate_report.json"
    if args.check:
        if not report_path.is_file():
            raise SystemExit(f"missing D3 Modelica gate report: {report_path}")
        try:
            expected = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid D3 Modelica gate report: {report_path}") from exc
        # Compare the complete parsed report, not only the boolean gates.  This
        # makes model, adapter, compiler, FMU, runtime, and causal trajectory
        # evidence stale-safe while leaving the report untouched.
        if expected != report:
            raise SystemExit("stale D3 Modelica shared-heat gate report")
        return 0
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
