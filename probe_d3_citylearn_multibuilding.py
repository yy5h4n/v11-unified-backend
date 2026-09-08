#!/usr/bin/env python3
"""Generate/check real CityLearn multi-building shared-meter evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path

from d3_citylearn_multibuilding_adapter import (
    DEFAULT_BUILDINGS,
    DEFAULT_HORIZON,
    DEFAULT_SHARED_METER_CAPACITY_KWH,
    DEFAULT_START,
    D3MultiBuildingError,
    probe_multibuilding,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "generated" / "d3_citylearn_multibuilding_evidence.json"


def sha256(path: Path) -> str:
    if not path.is_file():
        raise D3MultiBuildingError(f"missing provenance file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build_evidence(building_ids: tuple[str, ...], start: int, horizon: int, capacity: float) -> dict:
    report = probe_multibuilding(
        building_ids=building_ids,
        start=start,
        horizon=horizon,
        shared_meter_capacity_kwh=capacity,
    )
    report["evidence_policy"] = {
        "membership": "native CityLearn 2.5.0 multi-building replay only",
        "native_scope": "one persistent central-agent CityLearnEnv with two native building transitions",
        "capacity_scope": "benchmark/shared-meter threshold layered after native transition; no native transformer clipping",
        "scope": "backend verification only; no responsibility binding, benchmark episode, or evaluator",
    }
    # These are computed after the trajectory so the evidence pins the exact
    # adapter/probe sources without creating a self-referential output hash.
    adapter_path = ROOT / "d3_citylearn_multibuilding_adapter.py"
    probe_path = Path(__file__).resolve()
    baseline_provenance = report["baseline"].get("provenance", {})
    if not isinstance(baseline_provenance, dict):
        raise D3MultiBuildingError("missing baseline runtime provenance")
    required = ("citylearn_version", "runtime_sha256", "runtime_file_count", "citylearn_tag_commit")
    if any(key not in baseline_provenance for key in required):
        raise D3MultiBuildingError("incomplete CityLearn runtime provenance")
    if baseline_provenance["citylearn_version"] != "2.5.0":
        raise D3MultiBuildingError("public runtime provenance is not CityLearn 2.5.0")
    if not isinstance(baseline_provenance["runtime_sha256"], str) or len(baseline_provenance["runtime_sha256"]) != 64:
        raise D3MultiBuildingError("invalid CityLearn runtime digest")
    report["provenance"] = {
        "adapter_path": adapter_path.name,
        "adapter_sha256": sha256(adapter_path),
        "probe_path": probe_path.name,
        "probe_sha256": sha256(probe_path),
        "public_runtime": {
            "python": platform.python_version(),
            "runtime_package": "citylearn",
            "citylearn_version": baseline_provenance["citylearn_version"],
            "citylearn_tag_commit": baseline_provenance["citylearn_tag_commit"],
            "runtime_sha256": baseline_provenance["runtime_sha256"],
            "runtime_file_count": baseline_provenance["runtime_file_count"],
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building", dest="buildings", action="append", default=None)
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--capacity", type=float, default=DEFAULT_SHARED_METER_CAPACITY_KWH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if evidence is stale")
    args = parser.parse_args()
    building_ids = tuple(args.buildings or DEFAULT_BUILDINGS)
    try:
        report = build_evidence(building_ids, args.start, args.horizon, args.capacity)
    except D3MultiBuildingError as exc:
        raise SystemExit(f"D3 FAIL-CLOSED: {exc}") from exc
    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"stale D3 multi-building evidence: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
