#!/usr/bin/env python3
"""Execute and persist the fail-closed D3 CityLearn coupling evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from d3_citylearn_coupling_adapter import (
    DEFAULT_BUILDING,
    DEFAULT_HORIZON,
    DEFAULT_START,
    D3CouplingError,
    probe_coupling,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "generated" / "d3_citylearn_coupling_evidence.json"


def build_evidence(building: str, start: int, horizon: int) -> dict:
    report = probe_coupling(building, start, horizon)
    # The evidence file is intentionally self-contained: each trajectory has
    # source/runtime hashes inherited from the exact native run.
    report["evidence_policy"] = {
        "membership": "native CityLearn replay only",
        "coupling": "one CityLearnEnv instance per trajectory; HVAC and battery actions are co-applied",
        "scope": "backend verification only; no responsibility binding, benchmark episode, or evaluator",
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building", default=DEFAULT_BUILDING)
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if evidence is stale")
    args = parser.parse_args()
    try:
        report = build_evidence(args.building, args.start, args.horizon)
    except D3CouplingError as exc:
        raise SystemExit(f"D3 FAIL-CLOSED: {exc}") from exc
    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"stale D3 evidence: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
