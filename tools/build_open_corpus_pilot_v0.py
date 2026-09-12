"""Build and execute a small source-grounded workflow Episode pilot.

The source text is frozen in ``open_corpus_pilot_v0/families.json``.  This
builder does not invent requirements: it validates each backend binding,
expands only the declared environment variants, and requires a reference
policy to pass while a no-op policy fails.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness_v2.core import EpisodeSpec, Harness
from harness_v2.workflow_backend import WorkflowBackend
from harness_v2.workflow_evaluator import evaluate_workflow
from harness_v2.workflow_policy import NoOpPolicy, TRIGGERS, WorkflowReferencePolicy
from harness_v2.workflow_scenario_registry import match_current_backend
from unified_compiler.adapters.d1_discrete_device_fault import (
    DiscreteDeviceFaultBackend,
    DiscreteFaultSchedule,
    DiscreteFaultWindow,
)

DEFAULT_SPEC = ROOT / "open_corpus_pilot_v0" / "families.json"
DEFAULT_OUTPUT = ROOT / "generated" / "open_corpus_pilot_v0"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _fault_window(spec: dict[str, Any]) -> DiscreteFaultWindow:
    return DiscreteFaultWindow(
        device_id=spec["device_id"],
        start_step=spec["start_step"],
        end_step=spec["end_step"],
        mode=spec["mode"],
        slowdown_factor=spec.get("slowdown_factor", 1.0),
    )


def _backend_factory(variant: dict[str, Any]) -> Callable[[], WorkflowBackend]:
    route = variant["backend_route"]
    if route == "workflow":
        return WorkflowBackend
    if route == "d1_discrete_device_fault":
        schedule = DiscreteFaultSchedule([_fault_window(variant["fault"])])
        return lambda: DiscreteDeviceFaultBackend(schedule)
    raise ValueError(f"unsupported pilot backend route: {route!r}")


def _event_steps(run: Any, trigger: str | None) -> list[int]:
    if trigger is None:
        return []
    result: list[int] = []
    for record in run.public_trace:
        if record.get("type") != "observation":
            continue
        for event in record.get("value", {}).get("events", []):
            if event.get("type") == trigger and isinstance(event.get("step"), int):
                result.append(event["step"])
    return result


def _validate_family(family: dict[str, Any]) -> dict[str, Any]:
    required = {
        "family_id", "source_record", "query", "transformation", "backend",
        "scenario_type", "horizon_seconds", "public_profile", "variants",
    }
    missing = required - set(family)
    if missing:
        raise ValueError(f"{family.get('family_id', '<unknown>')}: missing {sorted(missing)}")
    if family["backend"] != "harness_v2_workflow":
        raise ValueError(f"{family['family_id']}: pilot only admits harness_v2_workflow")
    if not family["query"].strip() or not family["source_record"]["original_text"].strip():
        raise ValueError(f"{family['family_id']}: query and source text must be non-empty")
    if family["transformation"].get("added_obligations"):
        raise ValueError(f"{family['family_id']}: source-near pilot forbids added obligations")
    match = match_current_backend(family["scenario_type"])
    if match["status"] != "FULL":
        raise ValueError(f"{family['family_id']}: backend match is {match['status']}: {match}")
    return match


def build(spec_path: Path, output_dir: Path) -> dict[str, Any]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    families = spec.get("families", [])
    if not families:
        raise ValueError("pilot specification has no families")
    ids = [family["family_id"] for family in families]
    if len(ids) != len(set(ids)):
        raise ValueError("family ids must be unique")

    public_rows: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    family_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for family in families:
        match = _validate_family(family)
        source = deepcopy(family["source_record"])
        source["text_sha256"] = sha256(source["original_text"].encode("utf-8")).hexdigest()
        family_rows.append({
            "family_id": family["family_id"],
            "query": family["query"],
            "source": source,
            "transformation": family["transformation"],
            "backend_match": match,
            "scenario_type": family["scenario_type"],
        })

        for variant in family["variants"]:
            episode_id = f"{family['family_id']}__{variant['variant_id']}"
            bootstrap = {
                "query": family["query"],
                "scenario_type": family["scenario_type"],
                "horizon_seconds": family["horizon_seconds"],
                "public_profile": deepcopy(family["public_profile"]),
            }
            episode = EpisodeSpec(
                episode_id=episode_id,
                public_bootstrap=bootstrap,
                seed=variant["seed"],
                max_decisions=256,
            )
            make_backend = _backend_factory(variant)
            oracle = Harness(make_backend()).run_one(
                episode, WorkflowReferencePolicy(family["scenario_type"])
            )
            oracle_replay = Harness(make_backend()).run_one(
                episode, WorkflowReferencePolicy(family["scenario_type"])
            )
            noop = Harness(make_backend()).run_one(episode, NoOpPolicy())
            oracle_eval = evaluate_workflow(family["scenario_type"], oracle)
            noop_eval = evaluate_workflow(family["scenario_type"], noop)
            replay_ok = oracle.trace_digest == oracle_replay.trace_digest
            passed = bool(
                oracle.status == "completed"
                and oracle_eval["success"]
                and noop.status == "completed"
                and not noop_eval["success"]
                and replay_ok
            )
            trigger = TRIGGERS.get(family["scenario_type"])
            public_rows.append({
                "schema": "open_corpus_workflow_episode_v0",
                "episode_id": episode_id,
                "family_id": family["family_id"],
                "query": family["query"],
                "scenario_type": family["scenario_type"],
                "backend_route": variant["backend_route"],
                "seed": variant["seed"],
                "horizon_seconds": family["horizon_seconds"],
                "public_profile": deepcopy(family["public_profile"]),
                "source_record_id": f"rules_nl_en.csv:{source['csv_record_number']}",
                "transformation_status": family["transformation"]["status"],
            })
            private_rows.append({
                "episode_id": episode_id,
                "variant": deepcopy(variant),
                "trigger_event": trigger,
                "trigger_steps": _event_steps(oracle, trigger),
                "oracle": {
                    "run_status": oracle.status,
                    "success": oracle_eval["success"],
                    "trace_digest": oracle.trace_digest,
                    "evaluation": oracle_eval,
                },
                "noop": {
                    "run_status": noop.status,
                    "success": noop_eval["success"],
                    "trace_digest": noop.trace_digest,
                    "evaluation": noop_eval,
                },
                "checks": {
                    "backend_match_full": True,
                    "oracle_passes": bool(oracle_eval["success"]),
                    "noop_fails": not bool(noop_eval["success"]),
                    "deterministic_replay": replay_ok,
                    "passed": passed,
                },
            })
            if not passed:
                failures.append({"episode_id": episode_id, "oracle": oracle_eval, "noop": noop_eval, "replay_ok": replay_ok})

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "episodes_public.jsonl", public_rows)
    _write_jsonl(output_dir / "episodes_private_validation.jsonl", private_rows)
    _write_json(output_dir / "families_with_provenance.json", {
        "schema": "open_corpus_workflow_family_release_v0",
        "dataset_id": spec["dataset_id"],
        "source": spec["source"],
        "families": family_rows,
    })
    summary = {
        "schema": "open_corpus_workflow_pilot_summary_v0",
        "dataset_id": spec["dataset_id"],
        "release_status": spec["release_status"],
        "semantic_status": spec["semantic_status"],
        "family_count": len(family_rows),
        "episode_count": len(public_rows),
        "passed_episode_count": sum(row["checks"]["passed"] for row in private_rows),
        "failed_episode_count": len(failures),
        "all_backend_matches_full": True,
        "all_oracles_pass": all(row["checks"]["oracle_passes"] for row in private_rows),
        "all_noops_fail": all(row["checks"]["noop_fails"] for row in private_rows),
        "all_replays_deterministic": all(row["checks"]["deterministic_replay"] for row in private_rows),
        "source_license_status": spec["source"]["license_status"],
        "formal_human_revalidation_claimed": False,
        "spec_sha256": _digest(spec),
        "failures": failures,
    }
    _write_json(output_dir / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = build(args.spec, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["failed_episode_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
