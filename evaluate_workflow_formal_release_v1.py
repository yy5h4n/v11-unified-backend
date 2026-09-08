"""Replay a policy on the formal workflow release and compute the main metrics."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any, Callable

from harness_v2.core import EpisodeSpec, Harness
from harness_v2.trust_evidence import decode_serialization
from harness_v2.workflow_backend import WorkflowBackend
from harness_v2.workflow_evaluator import evaluate_workflow
from harness_v2.workflow_policy import NoOpPolicy, WorkflowReferencePolicy


ROOT = Path(__file__).resolve().parent
DEFAULT_RELEASE = ROOT / "generated/formal_workflow_release_v1/intervention_required"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row["run_status"] == "completed"]
    successful = [row for row in eligible if row["success"]]
    return {
        "eligible_episode_count": len(eligible),
        "responsibility_success_rate": len(successful) / len(eligible) if eligible else None,
        "success_conditioned_resource_cost": fmean(row["action_cost"] for row in successful) if successful else None,
        "resource_cost_unit": "action_unit",
        "protocol_invalid_count": sum(row["run_status"] == "protocol_invalid" for row in rows),
    }


def evaluate_policy(
    policy_factory: Callable[[str], Any],
    release_dir: Path = DEFAULT_RELEASE,
    policy_name: str = "custom",
) -> dict[str, Any]:
    public = _read_jsonl(release_dir / "episodes_public.jsonl")
    private = _read_jsonl(release_dir / "episodes_private.jsonl")
    if [row["episode_id"] for row in public] != [row["episode_id"] for row in private]:
        raise RuntimeError("public/private Episode IDs are misaligned")
    scored = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for public_row, private_row in zip(public, private):
        reset = private_row["reset_receipt"]["payload"]
        trusted_spec = decode_serialization(
            private_row["trusted_reset_receipt"]["episode_spec"],
            "workflow EpisodeSpec",
        )
        spec = EpisodeSpec(
            episode_id=public_row["episode_id"],
            public_bootstrap=reset["public_bootstrap"],
            seed=reset["seed"],
            max_decisions=trusted_spec["max_decisions"],
        )
        scenario = private_row["contract"]["scenario_type"]
        process = private_row["process_receipt"]["payload"]
        backend = WorkflowBackend(
            private_scenario_type=scenario,
            private_config=process["backend_effective_config"],
            private_horizon_seconds=process["horizon_seconds"],
        )
        run = Harness(backend).run_one(spec, policy_factory(scenario))
        score = evaluate_workflow(
            scenario,
            run,
            contract=private_row["contract"],
            profile=public_row["public_profile"],
        )
        row = {"episode_id": spec.episode_id, "responsibility_id": private_row["responsibility_id"], **score}
        scored.append(row)
        grouped[private_row["responsibility_id"]].append(row)
    return {
        "schema_version": "workflow-policy-evaluation-v1",
        "policy_name": policy_name,
        "main_metrics": _summarize(scored),
        "by_responsibility": {responsibility_id: _summarize(rows) for responsibility_id, rows in sorted(grouped.items())},
        "episode_results": scored,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--policy", choices=["reference", "noop"], required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    factory = (lambda scenario: WorkflowReferencePolicy(scenario)) if args.policy == "reference" else (lambda scenario: NoOpPolicy())
    result = evaluate_policy(factory, args.release_dir, args.policy)
    payload = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
