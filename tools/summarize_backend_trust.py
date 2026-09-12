"""Summarize observed conformance, without claiming task/mechanism readiness."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS, route_metadata


def build() -> dict:
    records = []
    for route_id in PUBLIC_ROUTE_IDS:
        directory = "backend_trust_horizon_fix_v1" if route_id == "d1_sustaingym_fault" else "backend_trust_horizon_v1"
        path = ROOT / "generated" / directory / f"{route_id}.json"
        row = {"route_id": route_id, "evidence": str(path.relative_to(ROOT)), "passed": False}
        try:
            data = json.loads(path.read_text())
            trace = data["trace"]
            metadata = route_metadata(route_id)
            cadence, horizon = metadata["cadence_seconds"], metadata["horizon_seconds"]
            if data["status"] != "passed" or not trace:
                raise ValueError("native run did not pass")
            for index, receipt in enumerate(trace):
                if not math.isclose(receipt["time_seconds"], (index + 1) * cadence):
                    raise ValueError("trace clock disagrees with cadence")
                if bool(receipt["done"]) != (index == len(trace) - 1):
                    raise ValueError("trace terminal boundary is inconsistent")
            if not math.isclose(trace[-1]["time_seconds"], horizon):
                raise ValueError("trace terminal time disagrees with registry")
            required = ("full_horizon", "terminal_observation", "terminal_step_rejected", "cadence")
            if not all(data["checks"].get(key) is True for key in required):
                raise ValueError("missing native conformance checks")
            row.update(passed=True, steps=len(trace), elapsed_seconds=horizon)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row["error"] = str(exc)
        records.append(row)
    return {
        "scope": "observed native full-horizon conformance; seed 0; scripted actions, not LLM or task evaluation",
        "formal_route_count": len(PUBLIC_ROUTE_IDS),
        "passed": sum(row["passed"] for row in records),
        "routes": records,
        "mechanism_or_benchmark_ready": False,
        "limits": ["does not certify all seeds or arbitrary actions", "does not certify task feasibility or scientific claims", "historical data-release tests remain unresolved"],
    }


if __name__ == "__main__":
    report = build()
    target = ROOT / "generated/backend_trust_summary_v1.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] == report["formal_route_count"] else 1)
