#!/usr/bin/env python3
"""V9 Episode-parallel runner preserving V8's within-Episode semantics.

Only independent Episodes execute concurrently.  Every Episode owns its own
backend, policy and ChatClient, while the main thread alone aggregates results
and atomically writes checkpoints.  Checkpoints may contain any completed
subset of the frozen sample and always store that subset in frozen-sample
order, regardless of future completion order.
"""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import evaluate_workflow_v4_flash_v5 as v5
import evaluate_workflow_v4_flash_v8 as v8
from evaluate_harness_v2_v4_flash import APIError, ChatClient, DEFAULT_BASE, DEFAULT_MODEL


ROOT = Path(__file__).resolve().parent
RELEASE_DIR = v8.RELEASE_DIR
DEFAULT_OUTPUT = ROOT / "runs/workflow_v4_flash_non_notification_15_v9/report.json"
SAMPLE_INDICES = v8.SAMPLE_INDICES
SAMPLE_EPISODE_IDS = v8.SAMPLE_EPISODE_IDS
SCHEMA_VERSION = "temporal-home-v4-flash-notification-only-filtered-smoke15-v9-episode-parallel"
DEFAULT_WORKERS = 30


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def effective_worker_count(workers: int, remaining_count: int) -> int:
    if workers <= 0:
        raise ValueError("workers must be positive")
    return min(workers, remaining_count) if remaining_count else 0


def ordered_results(results_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a completed subset in the immutable sample order."""
    unknown = set(results_by_id) - set(SAMPLE_EPISODE_IDS)
    if unknown:
        raise RuntimeError(f"unknown checkpoint Episode IDs: {sorted(unknown)}")
    return [results_by_id[episode_id] for episode_id in SAMPLE_EPISODE_IDS if episode_id in results_by_id]


def _validate_episode_row(row: dict[str, Any], *, position: int, public_rows: list[dict[str, Any]]) -> None:
    episode_id = SAMPLE_EPISODE_IDS[position]
    index = SAMPLE_INDICES[position]
    if row.get("episode_id") != episode_id:
        raise RuntimeError("checkpoint Episode identity mismatch")
    if row.get("model") != DEFAULT_MODEL or row.get("query") != public_rows[position]["query"]:
        raise RuntimeError("checkpoint model/query does not match frozen Episode")
    prompt_hash, selected = v8.expected_episode_prompt(index)
    if row.get("system_prompt_sha256") != prompt_hash or row.get("selected_event_types") != list(selected):
        raise RuntimeError("checkpoint Episode prompt/event selection mismatch")
    v8._validate_token_row(row)
    conversation = row.get("canonical_conversation")
    actions = v8.validate_canonical_conversation(
        conversation, expected_query=row["query"], expected_episode_id=episode_id
    )
    if hashlib.sha256(conversation[0]["content"].encode()).hexdigest() != row["system_prompt_sha256"]:
        raise RuntimeError("checkpoint canonical system message does not match prompt hash")
    model_records = row.get("model_output_records")
    if not isinstance(model_records, list):
        raise RuntimeError("checkpoint model records missing")
    model_actions = [record.get("canonical_action") for record in model_records if record.get("canonical_action") is not None]
    if actions != model_actions:
        raise RuntimeError("checkpoint model records do not match canonical conversation")
    public_records = row.get("public_action_records")
    if not isinstance(public_records, list):
        raise RuntimeError("checkpoint public action records missing")
    public_actions = [record["action"] for record in public_records if record.get("type") == "action"]
    unexecuted = len(actions) - len(public_actions)
    if public_actions != actions[:len(public_actions)] or unexecuted not in {0, 1} or (
        unexecuted == 1 and row.get("run_status") != "protocol_invalid"
    ):
        raise RuntimeError("checkpoint public actions do not match canonical conversation")
    for record in model_records:
        if not isinstance(record, dict) or "raw_content" in record or "content" in record:
            raise RuntimeError("checkpoint leaks or corrupts raw model content")


def validate_result_subset(results: list[dict[str, Any]]) -> None:
    """Strictly validate an arbitrary completed subset of the frozen sample."""
    if not isinstance(results, list):
        raise RuntimeError("checkpoint episodes must be a list")
    ids = [row.get("episode_id") if isinstance(row, dict) else None for row in results]
    if len(ids) != len(set(ids)):
        raise RuntimeError("checkpoint contains duplicate Episode IDs")
    position_by_id = {episode_id: position for position, episode_id in enumerate(SAMPLE_EPISODE_IDS)}
    if any(episode_id not in position_by_id for episode_id in ids):
        raise RuntimeError("checkpoint contains unknown Episode ID")
    positions = [position_by_id[episode_id] for episode_id in ids]
    if positions != sorted(positions):
        raise RuntimeError("checkpoint Episodes are not in frozen-sample order")
    public_rows, _ = v5.assert_release_and_sample_frozen()
    for row, position in zip(results, positions):
        _validate_episode_row(row, position=position, public_rows=public_rows)


def build_report(
    results: list[dict[str, Any]], *, execution_finished: bool, requested_workers: int
) -> dict[str, Any]:
    validate_result_subset(results)
    if execution_finished and len(results) != len(SAMPLE_INDICES):
        raise RuntimeError("finished report requires all frozen Episodes")
    completed_ids = [row["episode_id"] for row in results]
    completed_indices = [
        SAMPLE_INDICES[SAMPLE_EPISODE_IDS.index(episode_id)] for episode_id in completed_ids
    ]
    config = {
        "model": results[0]["model"] if results else DEFAULT_MODEL,
        "base_url": DEFAULT_BASE,
        "release_dir": str(RELEASE_DIR.relative_to(ROOT)),
        "release_public_sha256": v5.PUBLIC_RELEASE_SHA256,
        "release_private_sha256": v5.PRIVATE_RELEASE_SHA256,
        "planned_indices": SAMPLE_INDICES,
        "completed_indices": completed_indices,
        "planned_episode_ids": SAMPLE_EPISODE_IDS,
        "completed_episode_ids": completed_ids,
        "sampling_semantics": "frozen v5 mechanism-diversity smoke subset: one release-order Episode from each of 15 distinct responsibilities; not an estimator",
        "scope_semantics": "notification-only responsibilities excluded; mixed responsibilities with independently evaluated physical/device outcomes remain eligible",
        "prompt_implementation": "evaluate_workflow_v4_flash_v9",
        "prompt_template_sha256": v8.prompt_template_sha256(),
        "observable_event_catalog_sha256": v5.V4_EVENT_CATALOG_SHA256,
        "system_prefix_policy": "episode_static_byte_identical_across_calls",
        "conversation_policy": "full canonical assistant action and environment observation history; no previous_action duplicate fields",
        "response_envelope_protocol": {
            "format": "<answer>JSON_OBJECT</answer>", "required": True,
            "strict_json": ["duplicate_keys_rejected", "nonfinite_constants_rejected"],
            "outside_reasoning": "ignored_not_replayed_not_persisted",
        },
        "native_tools": "not_used",
        "raw_model_content_retention": "sha256_and_byte_length_only",
        "primary_token_metric": "api_output_tokens",
        "token_accounting": {
            "api_output_tokens_definition": "exact provider completion_tokens/output_tokens only",
            "prompt_and_provider_total_location": "metrics_all.token_diagnostics",
            "provider_total_synthesized": False,
            "missing_provider_fields": "stay_zero",
            "provider_total_mismatch": "retained_exactly_and_counted",
            "generic_tokens_field": "not_emitted",
        },
        "migration_manifest": deepcopy(v8.MIGRATION_MANIFEST),
    }
    config["concurrency"] = {
        "unit": "Episode",
        "requested_workers": requested_workers,
        "maximum_effective_workers": min(requested_workers, len(SAMPLE_INDICES)),
        "within_episode": "strictly_serial",
        "client_scope": "one_ChatClient_per_Episode",
        "checkpoint_writer": "main_thread_only_atomic_replace",
        "result_order": "frozen_SAMPLE_INDICES_order",
    }
    config["checkpoint"] = "arbitrary_completed_subset_strictly_validated_and_canonically_ordered"
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_finished": execution_finished,
        "experiment_config": config,
        "metrics_all": v8.metrics_with_device_total(results),
        "rejected_action_loop_diagnostics": {
            row["episode_id"]: row["max_consecutive_identical_rejected_action"] for row in results
        },
        "episodes": results,
    }


def atomic_write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def load_checkpoint(path: Path, *, requested_workers: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("existing output is not a v9 Episode-parallel checkpoint")
    if checkpoint.get("execution_finished") is True:
        raise RuntimeError("refusing to overwrite an already completed v9 report")
    results = checkpoint.get("episodes")
    if not isinstance(results, list):
        raise RuntimeError("existing checkpoint has invalid episodes")
    expected = build_report(results, execution_finished=False, requested_workers=requested_workers)
    if checkpoint != expected:
        raise RuntimeError("existing checkpoint metadata, metrics, or concurrency configuration mismatch")
    return results


def evaluate_index(index: int, base_url: str, model: str) -> dict[str, Any]:
    """Execute one Episode with exclusively owned mutable state."""
    client = ChatClient(base_url, model)
    return v8.evaluate_one(client, index, RELEASE_DIR)


def _submit_remaining(
    executor: ThreadPoolExecutor, remaining: list[int], base_url: str, model: str
) -> dict[Future[dict[str, Any]], int]:
    return {executor.submit(evaluate_index, index, base_url, model): index for index in remaining}


def main(args: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=positive_int, default=DEFAULT_WORKERS)
    ns = parser.parse_args(args)
    if ns.base_url != DEFAULT_BASE or ns.model != DEFAULT_MODEL:
        raise ValueError("v9 smoke freezes the default base URL and model")
    v5.assert_release_and_sample_frozen()
    completed = load_checkpoint(ns.output, requested_workers=ns.workers)
    results_by_id = {row["episode_id"]: row for row in completed}
    remaining = [
        index for index, episode_id in zip(SAMPLE_INDICES, SAMPLE_EPISODE_IDS)
        if episode_id not in results_by_id
    ]
    worker_count = effective_worker_count(ns.workers, len(remaining))
    failures: list[Exception] = []
    if worker_count:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="v9-episode") as executor:
            future_to_index = _submit_remaining(executor, remaining, ns.base_url, ns.model)
            for future in as_completed(future_to_index):
                try:
                    result = future.result()
                    episode_id = result.get("episode_id")
                    expected_id = SAMPLE_EPISODE_IDS[SAMPLE_INDICES.index(future_to_index[future])]
                    if episode_id != expected_id or episode_id in results_by_id:
                        raise RuntimeError("worker returned duplicate or mismatched Episode identity")
                    results_by_id[episode_id] = result
                    current = ordered_results(results_by_id)
                    atomic_write_report(
                        ns.output,
                        build_report(current, execution_finished=False, requested_workers=ns.workers),
                    )
                except Exception as exc:  # preserve other successful futures before re-raising
                    failures.append(exc)
    results = ordered_results(results_by_id)
    if failures:
        atomic_write_report(
            ns.output, build_report(results, execution_finished=False, requested_workers=ns.workers)
        )
        raise failures[0]
    report = build_report(results, execution_finished=True, requested_workers=ns.workers)
    atomic_write_report(ns.output, report)
    return report


if __name__ == "__main__":
    main()
