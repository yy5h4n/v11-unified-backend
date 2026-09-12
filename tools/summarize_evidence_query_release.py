#!/usr/bin/env python3
"""Validate several construction batches and emit denominator-safe release totals."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.evidence_batch import load_and_validate


def summarize(paths: list[Path]) -> dict:
    if not paths:
        raise ValueError("at least one batch is required")

    audits = []
    decisions = Counter()
    classifications = Counter()
    splits = Counter()
    failed_owners = Counter()
    costs = Counter()
    model_evaluated = 0
    model_passed = 0

    for path in paths:
        document, audit = load_and_validate(path)
        audits.append(audit)
        decisions.update(audit["decision_counts"])
        classifications.update(audit["classification_counts"])
        splits.update(audit["split_counts"])
        failed_owners.update(audit["failed_gate_owner_counts"])
        for key in ("provider_calls", "provider_tokens", "native_runs", "unknown_cost_events"):
            costs[key] += audit["costs"][key]
        for item in document["items"]:
            status = item["gates"]["model_execution"]["status"]
            if status in {"passed", "failed"}:
                model_evaluated += 1
                model_passed += status == "passed"

    attempted = sum(audit["attempted_items"] for audit in audits)
    accepted = decisions["accepted_core"] + decisions["accepted_calibration"]
    rejected = decisions["rejected"]
    pending = decisions["pending"]
    return {
        "schema": "evidence-query-release-summary.v1",
        "batch_ids": [audit["batch_id"] for audit in audits],
        "batches": audits,
        "combined_descriptive_counts": {
            "attempted_items": attempted,
            "accepted_items": accepted,
            "accepted_core_items": decisions["accepted_core"],
            "accepted_calibration_items": decisions["accepted_calibration"],
            "rejected_items": rejected,
            "pending_items": pending,
            "acceptance_rate": accepted / attempted,
            "core_acceptance_rate": decisions["accepted_core"] / attempted,
            "rejection_rate": rejected / attempted,
            "decision_counts": dict(decisions),
            "classification_counts": dict(classifications),
            "split_counts": dict(splits),
            "failed_gate_owner_counts": dict(failed_owners),
        },
        "model_execution": {
            "evaluated_items": model_evaluated,
            "passed_items": model_passed,
            "pass_rate": model_passed / model_evaluated if model_evaluated else None,
        },
        "recorded_cost_totals": dict(costs),
        "claim_boundary": (
            "Combined rates are descriptive bookkeeping across batches with different selection "
            "purposes. They are not estimates of population prevalence, route coverage, or model capability."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(summarize(args.batch), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
