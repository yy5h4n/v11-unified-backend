#!/usr/bin/env python3
"""V4 Flash diversity smoke test on 15 notification-only-filtered responsibilities.

This compatibility-safe orchestration layer reuses the v4 prompt, policy,
backend, and evaluator. It changes only the frozen release, selected Episodes,
report schema, and checkpoint behavior. Historical v1-v4 files are untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import evaluate_workflow_v4_flash_v4 as v4
from evaluate_harness_v2_v4_flash import ChatClient, DEFAULT_BASE, DEFAULT_MODEL
from evaluate_workflow_formal_release_v1 import _read_jsonl


ROOT = Path(__file__).resolve().parent
RELEASE_DIR = ROOT / "generated/formal_workflow_release_non_notification_v1/intervention_required"
DEFAULT_OUTPUT = ROOT / "runs/workflow_v4_flash_non_notification_15_v5/report.json"
PUBLIC_RELEASE_SHA256 = "e655538d94d9f45d6a6dc72423edc4748160f7ccfa06e34645d01bb283fc6e97"
PRIVATE_RELEASE_SHA256 = "f362e7e9c2baded90d89603f6c69888940b13bdd5e9efd49e7ac9b32bbd069ff"
V4_PROMPT_TEMPLATE_SHA256 = "5296248fd2603d022aa9d7d09aab3d3389fb837c9c342cb457d28b192a712a8d"
V4_EVENT_CATALOG_SHA256 = "01ea65b09c3fefede657982cd2b63416b68266bb4b886ba088f1fd8863b6e51a"
V4_RUNNER_SHA256 = "712c1295a9fc06d6201e8e3a5f5d3634bd9f6f8fe55f4c0e33d01c7ce5e4e868"
SAMPLE_INDICES = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 140, 150, 190, 200]
SAMPLE_EPISODE_IDS = [
    "wf_1431a0192aa480b30b4e", "wf_f67e284eb1f93cffed52",
    "wf_350f3528bd071fdcb4a3", "wf_cdcc6f72fe04275be622",
    "wf_26940956e5248fc34813", "wf_935d2fd3000078d9667a",
    "wf_c9485ce9dea05f97dd62", "wf_07754910f0ed588f14f3",
    "wf_4218281e3c511b005e57", "wf_99c914ec4da407ac9d06",
    "wf_8601b37fd7c217cf0fb0", "wf_66ee71160144aaab1477",
    "wf_bee089bef795fe304b1e", "wf_ffbb41319b594c967643",
    "wf_3c14562a7348ffb58c4f",
]


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_release_and_sample_frozen(release_dir: Path = RELEASE_DIR) -> tuple[list[dict], list[dict]]:
    public_path = release_dir / "episodes_public.jsonl"
    private_path = release_dir / "episodes_private.jsonl"
    if sha256_path(public_path) != PUBLIC_RELEASE_SHA256:
        raise RuntimeError("filtered public release SHA256 mismatch")
    if sha256_path(private_path) != PRIVATE_RELEASE_SHA256:
        raise RuntimeError("filtered private release SHA256 mismatch")
    if v4.prompt_template_sha256() != V4_PROMPT_TEMPLATE_SHA256:
        raise RuntimeError("v4 prompt template SHA256 mismatch")
    if v4.event_catalog_sha256() != V4_EVENT_CATALOG_SHA256:
        raise RuntimeError("v4 event catalog SHA256 mismatch")
    if sha256_path(Path(v4.__file__).resolve()) != V4_RUNNER_SHA256:
        raise RuntimeError("v4 runner source SHA256 mismatch")

    public_rows = _read_jsonl(public_path)
    private_rows = _read_jsonl(private_path)
    if len(public_rows) != 210 or len(private_rows) != 210:
        raise RuntimeError("filtered release must contain exactly 210 paired Episodes")
    public_ids = [row["episode_id"] for row in public_rows]
    private_ids = [row["episode_id"] for row in private_rows]
    if public_ids != private_ids or len(public_ids) != len(set(public_ids)):
        raise RuntimeError("filtered public/private Episode pairing is invalid")
    responsibility_counts = Counter(row["responsibility_id"] for row in private_rows)
    if len(responsibility_counts) != 21 or set(responsibility_counts.values()) != {10}:
        raise RuntimeError("filtered release must contain 21 responsibilities with 10 Episodes each")
    if any(row["contract"].get("required_actions") == ["notification.send"] for row in private_rows):
        raise RuntimeError("notification-only Contract leaked into filtered release")

    selected_public = [public_rows[index] for index in SAMPLE_INDICES]
    selected_private = [private_rows[index] for index in SAMPLE_INDICES]
    selected_ids = [row["episode_id"] for row in selected_public]
    if selected_ids != SAMPLE_EPISODE_IDS:
        raise RuntimeError("frozen smoke Episode IDs mismatch")
    selected_responsibilities = [row["responsibility_id"] for row in selected_private]
    if len(set(selected_responsibilities)) != 15:
        raise RuntimeError("smoke sample must contain 15 distinct responsibilities")
    return selected_public, selected_private


def expected_episode_prompt(index: int) -> tuple[str, tuple[str, ...]]:
    public, _, spec, backend = v4.load_temporal_episode(index, RELEASE_DIR)
    observation = backend.reset(spec).public_observation
    selected, _ = v4.select_event_types_for_query(public["query"])
    content = v4.build_system_content(
        public["query"], v4.device_interfaces_from_observation(observation), selected
    )
    return v4.system_prompt_sha256(content), selected


def validate_result_prefix(results: list[dict[str, Any]]) -> None:
    if [row.get("episode_id") for row in results] != SAMPLE_EPISODE_IDS[: len(results)]:
        raise RuntimeError("checkpoint results are not a prefix of the frozen sample")
    public_rows, _ = assert_release_and_sample_frozen()
    for position, row in enumerate(results):
        if row.get("model") != DEFAULT_MODEL:
            raise RuntimeError("checkpoint model does not match the frozen model")
        if row.get("query") != public_rows[position]["query"]:
            raise RuntimeError("checkpoint query does not match the frozen Episode")
        prompt_hash, selected = expected_episode_prompt(SAMPLE_INDICES[position])
        if row.get("system_prompt_sha256") != prompt_hash:
            raise RuntimeError("checkpoint Episode system prompt SHA256 mismatch")
        if row.get("selected_event_types") != list(selected):
            raise RuntimeError("checkpoint Episode event selection mismatch")


def metrics_with_device_total(results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = v4.aggregate_results(results)
    metrics["total_device_command_count"] = sum(row["device_command_count"] for row in results)
    return metrics


def build_report(results: list[dict[str, Any]], *, execution_finished: bool) -> dict[str, Any]:
    validate_result_prefix(results)
    if execution_finished and len(results) != len(SAMPLE_INDICES):
        raise RuntimeError("finished report requires all 15 Episodes")
    return {
        "schema_version": "temporal-home-v4-flash-notification-only-filtered-smoke15-v5",
        "execution_finished": execution_finished,
        "experiment_config": {
            "model": results[0]["model"] if results else DEFAULT_MODEL,
            "base_url": DEFAULT_BASE,
            "release_dir": str(RELEASE_DIR.relative_to(ROOT)),
            "release_public_sha256": PUBLIC_RELEASE_SHA256,
            "release_private_sha256": PRIVATE_RELEASE_SHA256,
            "planned_indices": SAMPLE_INDICES,
            "completed_indices": SAMPLE_INDICES[: len(results)],
            "planned_episode_ids": SAMPLE_EPISODE_IDS,
            "sampling_semantics": (
                "manually frozen mechanism-diversity smoke subset; the first release-order Episode from each of "
                "15 distinct responsibilities; not a random sample or estimator of all 21 responsibilities"
            ),
            "scope_semantics": (
                "notification-only responsibilities excluded; mixed responsibilities with independently "
                "evaluated physical/device outcomes remain eligible"
            ),
            "prompt_implementation": "evaluate_workflow_v4_flash_v4",
            "prompt_implementation_sha256": V4_RUNNER_SHA256,
            "prompt_template_sha256": V4_PROMPT_TEMPLATE_SHA256,
            "observable_event_catalog_sha256": V4_EVENT_CATALOG_SHA256,
            "system_prefix_policy": "episode_static_byte_identical_across_calls",
            "baseline_comparison": "omitted_new_release_and_sample_not_pairable_to_v2_v3",
        },
        "metrics_all": metrics_with_device_total(results),
        "rejected_action_loop_diagnostics": {
            row["episode_id"]: row["max_consecutive_identical_rejected_action"] for row in results
        },
        "episodes": results,
    }


def atomic_write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_checkpoint(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("schema_version") != "temporal-home-v4-flash-notification-only-filtered-smoke15-v5":
        raise RuntimeError("existing output is not a v5 smoke checkpoint")
    if checkpoint.get("execution_finished") is True:
        raise RuntimeError("refusing to overwrite an already completed v5 report")
    results = checkpoint.get("episodes")
    if not isinstance(results, list):
        raise RuntimeError("existing checkpoint has invalid episodes")
    expected = build_report(results, execution_finished=False)
    if checkpoint != expected:
        raise RuntimeError("existing checkpoint metadata or metrics mismatch")
    return results


def main(args: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ns = parser.parse_args(args)
    if ns.base_url != DEFAULT_BASE or ns.model != DEFAULT_MODEL:
        raise ValueError("v5 smoke freezes the default base URL and model")
    assert_release_and_sample_frozen()
    results = load_checkpoint(ns.output)
    client = ChatClient(ns.base_url, ns.model)
    for index in SAMPLE_INDICES[len(results):]:
        results.append(v4.evaluate_one(client, index, RELEASE_DIR))
        atomic_write_report(ns.output, build_report(results, execution_finished=False))
    report = build_report(results, execution_finished=True)
    atomic_write_report(ns.output, report)
    return report


if __name__ == "__main__":
    main()
