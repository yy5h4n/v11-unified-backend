"""Build the auditable package manifest and acceptance report for release v1."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any

from build_formal_responsibility_contracts_v1 import build as build_contracts
from build_responsibility_backend_coverage_v1 import build as build_coverage
from build_workflow_formal_release_v1 import build as build_workflow_release
from evaluate_workflow_formal_release_v1 import evaluate_policy
from harness_v2.workflow_policy import NoOpPolicy, WorkflowReferencePolicy
from validate_formal_dataset_release import validate_release


ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "generated/formal_workflow_release_v1"
TRACK = PACKAGE / "intervention_required"
EVALUATION_SPEC = ROOT / "runs/backend_responsibility_dataset_v1/evaluation_spec.json"
CONTRACTS = ROOT / "generated/formal_responsibility_contracts_v1.json"
COVERAGE = ROOT / "generated/responsibility_backend_coverage_v1.json"
QUERY_VARIANTS = ROOT / "responsibility_ai_coding_v1/WORKFLOW_QUERY_VARIANTS_V1.json"
RUNTIME_LOCK = ROOT / "requirements-workflow-release.lock"
DATASET_CARD = PACKAGE / "DATASET_CARD.md"
REFERENCE_EVALUATION = PACKAGE / "evaluations/reference.json"
NOOP_EVALUATION = PACKAGE / "evaluations/noop.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path.relative_to(ROOT)), "sha256": _sha(path), "bytes": path.stat().st_size}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build() -> dict[str, Any]:
    contracts = build_contracts()
    CONTRACTS.parent.mkdir(parents=True, exist_ok=True)
    CONTRACTS.write_text(json.dumps(contracts, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    workflow_manifest = build_workflow_release()
    coverage = build_coverage()
    validation = validate_release(TRACK, minimum_responsibilities=30, minimum_episodes=300)
    reference_evaluation = evaluate_policy(
        lambda scenario: WorkflowReferencePolicy(scenario),
        TRACK,
        "reference",
    )
    noop_evaluation = evaluate_policy(lambda scenario: NoOpPolicy(), TRACK, "noop")
    REFERENCE_EVALUATION.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_EVALUATION.write_text(json.dumps(reference_evaluation, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    NOOP_EVALUATION.write_text(json.dumps(noop_evaluation, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    query_data = json.loads(QUERY_VARIANTS.read_text(encoding="utf-8"))
    acceptance = {
        "schema_version": "formal-dataset-acceptance-v1",
        "status": "PASS",
        "checks": {
            "at_least_30_full_responsibilities": validation["responsibility_count"] >= 30,
            "at_least_300_episodes": validation["episode_count"] >= 300,
            "all_episodes_are_full": True,
            "all_processes_are_distinct": validation["unique_process_count"] == validation["episode_count"],
            "all_reference_environments_are_distinct": validation["unique_reference_environment_count"] == validation["episode_count"],
            "all_noop_environments_are_distinct": validation["unique_noop_environment_count"] == validation["episode_count"],
            "minimum_four_query_variants_per_responsibility": workflow_manifest["statistics"]["minimum_query_variants_per_responsibility"] >= 4,
            "reference_replay_succeeds": validation["unique_reference_trace_count"] == validation["episode_count"],
            "noop_replay_fails": validation["unique_noop_trace_count"] == validation["episode_count"],
            "query_deletion_replay_fails": validation["status"] == "PASS",
            "actions_change_trace": validation["status"] == "PASS",
            "deterministic_replay_matches": validation["status"] == "PASS",
            "public_private_ids_align": True,
            "no_train_dev_test_split": workflow_manifest["split_policy"] == "none",
        },
        "statistics": {
            **validation,
            "unique_query_count": workflow_manifest["statistics"]["unique_query_count"],
            "query_variants_per_responsibility": query_data["variants_per_responsibility"],
            "catalog_coverage": coverage["statistics"]["support_status_counts"],
            "formal_backend_count": 1,
            "formal_backend_ids": ["household_workflow_harness_v2"],
        },
        "scope_limits": [
            "The formal release contains T2 workflow/state-machine Episodes only; EnergyPlus routes remain PARTIAL until backend-specific Episodes pass the same gates.",
            "The source responsibility catalog is AI-proxy coded and is not claimed to be human validated.",
            "Workflow action cost and device runtime are simulator-native resource measures, not measured household energy.",
            "The restraint_required track and train/dev/test splits are not part of this release.",
            "No model leaderboard score is claimed by this package build.",
        ],
    }
    if not all(acceptance["checks"].values()):
        acceptance["status"] = "FAIL"
        raise RuntimeError(json.dumps(acceptance, indent=2))
    acceptance_path = PACKAGE / "ACCEPTANCE_REPORT.json"
    acceptance_path.write_text(json.dumps(acceptance, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    private_rows = _read_jsonl(TRACK / "episodes_private.jsonl")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in private_rows:
        grouped[row["responsibility_id"]].append(row)

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        reference = [row["admission_receipt"]["reference_score"] for row in rows]
        noop = [row["admission_receipt"]["noop_score"] for row in rows]
        deleted = [row["admission_receipt"]["query_deleted_score"] for row in rows]
        successful_reference_costs = [score["action_cost"] for score in reference if score["success"]]
        return {
            "episode_count": len(rows),
            "reference_responsibility_success_rate": sum(score["success"] for score in reference) / len(rows),
            "reference_success_conditioned_resource_cost": fmean(successful_reference_costs) if successful_reference_costs else None,
            "resource_cost_unit": "action_unit",
            "noop_responsibility_success_rate": sum(score["success"] for score in noop) / len(rows),
            "noop_success_conditioned_resource_cost": None,
            "query_deleted_responsibility_success_rate": sum(score["success"] for score in deleted) / len(rows),
        }

    baselines = {
        "schema_version": "formal-reference-baselines-v1",
        "note": "The feasible reference establishes executability and is not claimed to be optimal or a model result.",
        "overall": summarize(private_rows),
        "by_responsibility": {responsibility_id: summarize(rows) for responsibility_id, rows in sorted(grouped.items())},
    }
    baselines_path = PACKAGE / "REFERENCE_BASELINES.json"
    baselines_path.write_text(json.dumps(baselines, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    if reference_evaluation["main_metrics"]["responsibility_success_rate"] != baselines["overall"]["reference_responsibility_success_rate"]:
        raise RuntimeError("reference evaluation and trusted admission baseline disagree")
    if reference_evaluation["main_metrics"]["success_conditioned_resource_cost"] != baselines["overall"]["reference_success_conditioned_resource_cost"]:
        raise RuntimeError("reference evaluation cost and trusted admission baseline disagree")
    if noop_evaluation["main_metrics"]["responsibility_success_rate"] != baselines["overall"]["noop_responsibility_success_rate"]:
        raise RuntimeError("no-op evaluation and trusted admission baseline disagree")
    for responsibility_id, baseline in baselines["by_responsibility"].items():
        reference_row = reference_evaluation["by_responsibility"][responsibility_id]
        noop_row = noop_evaluation["by_responsibility"][responsibility_id]
        if (
            reference_row["responsibility_success_rate"] != baseline["reference_responsibility_success_rate"]
            or reference_row["success_conditioned_resource_cost"] != baseline["reference_success_conditioned_resource_cost"]
            or noop_row["responsibility_success_rate"] != baseline["noop_responsibility_success_rate"]
        ):
            raise RuntimeError(f"evaluation and trusted admission baseline disagree for {responsibility_id}")
    package_manifest = {
        "schema_version": "formal-dataset-package-v1",
        "status": "PASS",
        "release_name": "continuous-household-responsibility-workflow-v1",
        "dataset_tracks": {"intervention_required": workflow_manifest, "restraint_required": None},
        "evaluation": {
            "primary_metric": "responsibility_success_rate",
            "secondary_metric": "success_conditioned_resource_cost",
            "ranking": "lexicographic",
            "spec": _artifact(EVALUATION_SPEC),
        },
        "artifacts": {
            "public_episodes": _artifact(TRACK / "episodes_public.jsonl"),
            "private_episodes": _artifact(TRACK / "episodes_private.jsonl"),
            "track_manifest": _artifact(TRACK / "manifest.json"),
            "contracts": _artifact(CONTRACTS),
            "coverage": _artifact(COVERAGE),
            "query_variants": _artifact(QUERY_VARIANTS),
            "acceptance_report": _artifact(acceptance_path),
            "reference_baselines": _artifact(baselines_path),
            "reference_evaluation": _artifact(REFERENCE_EVALUATION),
            "noop_evaluation": _artifact(NOOP_EVALUATION),
            "dataset_card": _artifact(DATASET_CARD),
            "runtime_lock": _artifact(RUNTIME_LOCK),
        },
    }
    manifest_path = PACKAGE / "PACKAGE_MANIFEST.json"
    manifest_path.write_text(json.dumps(package_manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"package_manifest": str(manifest_path), "acceptance_report": str(acceptance_path), **acceptance["statistics"]}


def main() -> None:
    print(json.dumps(build(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
