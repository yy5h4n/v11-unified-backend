#!/usr/bin/env python3
"""Run one DeepSeek V4 Flash policy on the reviewed formal workflow release."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from statistics import fmean
from typing import Any

from evaluate_harness_v2_v4_flash import ChatClient, DEFAULT_BASE, DEFAULT_MODEL, _parse_strict_json
from evaluate_workflow_formal_release_v1 import DEFAULT_RELEASE, _read_jsonl
from harness_v2.core import EpisodeSpec, Harness
from harness_v2.trust_evidence import decode_serialization
from harness_v2.workflow_backend import WorkflowBackend
from harness_v2.workflow_evaluator import evaluate_workflow
from validate_formal_dataset_release import validate_release


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "runs/workflow_v4_flash_prompt_only_20_v1/report.json"
SYSTEM_PROMPT = (
    "You are responsible for carrying out the user's standing household request. "
    "Observe events and device state, act only when justified, and keep monitoring until the episode ends. "
    "Your response is parsed directly as JSON. Think silently. Your entire response MUST start with { and end with }. "
    "Return exactly one JSON action matching action_protocol. Never output reasoning, a preamble, Markdown, code fences, "
    "or trailing text. Before sending, remove everything outside the single JSON object."
)
PROTOCOL_RULES = [
    "The first response character must be { and the last must be }.",
    "Return exactly one plain JSON object; no reasoning, markdown, code fence, preamble, or trailing text.",
    "Use only published device interfaces and exact parameter schemas.",
    "wait means allowing real simulated time to pass; choose its duration yourself.",
    "An empty act command list is legal but changes nothing.",
]
ACTION_FORMS = {
    "act": {"kind": "act", "commands": [{
        "device_id": "<published device_id>",
        "capability": "<published capability>",
        "operation": "<published operation>",
        "parameters": {},
    }]},
    "wait_for": {"kind": "wait", "mode": "for", "duration_seconds": 60},
    "wait_until": {"kind": "wait", "mode": "until", "timestamp": "<future RFC3339 timestamp>"},
    "wait_until_event": {
        "kind": "wait", "mode": "until_event",
        "event_filter": {"type": "<public event type>"}, "timeout_seconds": 3600,
    },
    "ask": {"kind": "ask", "question": "<question>"},
    "cancel_rule": {"kind": "cancel_rule", "rule_id": "<active rule id>"},
    "install_rule": {"kind": "install_rule", "rule": {
        "rule_id": "<new id>", "fire_at_step": 2, "release_at_step": None,
        "commands": [], "release_commands": [],
    }},
}
REQUEST_BODY_FIELDS = ["model", "messages", "temperature", "max_tokens"]


def prompt_template_sha256() -> str:
    value = {"system": SYSTEM_PROMPT, "protocol_rules": PROTOCOL_RULES, "action_forms": ACTION_FORMS}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def action_protocol(view: dict[str, Any]) -> dict[str, Any]:
    """Describe only public Harness actions and backend-published interfaces."""

    return {
        "allowed_actions": view["allowed_actions"],
        "forms": deepcopy(ACTION_FORMS),
        "device_interfaces": [
            {"device_id": item["device_id"], "interfaces": item["interfaces"]}
            for item in view["observation"]["inventory"]["devices"]
        ],
        "rules": list(PROTOCOL_RULES),
    }


def build_messages(view: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "query": view["query"],
        "user_preferences": view["public_profile"],
        "current_observation": view["observation"],
        "previous_action_result": view["last_feedback"],
        "action_protocol": action_protocol(view),
    }
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]


class WorkflowLLMPolicy:
    def __init__(self, client: Any):
        self.client = client
        self.calls = 0
        self.api_successes = 0
        self.parsed_json_actions = 0
        self.tokens = 0
        self.latency_ms = 0.0
        self.errors: Counter[str] = Counter()
        self.output_records: list[dict[str, Any]] = []

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        record = {"call_index": self.calls - 1, "raw_content": None, "raw_content_sha256": None, "error": None}
        try:
            response = self.client.complete(build_messages(view))
            self.api_successes += 1
            self.tokens += int((response.get("usage") or {}).get("total_tokens", 0))
            self.latency_ms += float(response.get("latency_ms", 0.0))
            content = response.get("content")
            if isinstance(content, str):
                record["raw_content"] = content
                record["raw_content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
            action = _parse_strict_json(content)
            self.parsed_json_actions += 1
            return action
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            record["error"] = error
            self.errors[error] += 1
            return {"kind": "invalid_model_output"}
        finally:
            self.output_records.append(record)


def load_episode(index: int, release_dir: Path = DEFAULT_RELEASE) -> tuple[dict[str, Any], dict[str, Any], EpisodeSpec, WorkflowBackend]:
    public_rows = _read_jsonl(release_dir / "episodes_public.jsonl")
    private_rows = _read_jsonl(release_dir / "episodes_private.jsonl")
    if index < 0 or index >= len(public_rows) or len(public_rows) != len(private_rows):
        raise ValueError("episode index is outside the aligned formal release")
    public, private = public_rows[index], private_rows[index]
    if public["episode_id"] != private["episode_id"]:
        raise RuntimeError("public/private Episode IDs are misaligned")
    trusted_spec = decode_serialization(private["trusted_reset_receipt"]["episode_spec"], "workflow EpisodeSpec")
    reset = private["reset_receipt"]["payload"]
    spec = EpisodeSpec(public["episode_id"], deepcopy(reset["public_bootstrap"]), reset["seed"], trusted_spec["max_decisions"])
    process = private["process_receipt"]["payload"]
    backend = WorkflowBackend(
        private_scenario_type=private["contract"]["scenario_type"],
        private_config=deepcopy(process["backend_effective_config"]),
        private_horizon_seconds=process["horizon_seconds"],
    )
    return public, private, spec, backend


def evaluate_one(client: Any, index: int = 0, release_dir: Path = DEFAULT_RELEASE) -> dict[str, Any]:
    public, private, spec, backend = load_episode(index, release_dir)
    policy = WorkflowLLMPolicy(client)
    run = Harness(backend).run_one(spec, policy)
    score = evaluate_workflow(
        private["contract"]["scenario_type"], run,
        contract=private["contract"], profile=public["public_profile"],
    )
    action_records = [row for row in run.public_trace if row["type"] in {"action", "protocol_error", "backend_error"}]
    executed_actions = [row for row in action_records if row["type"] == "action"]
    rejected_actions = sum(row["accepted"] is not True for row in executed_actions)
    return {
        "episode_id": spec.episode_id,
        "query": public["query"],
        "model": getattr(client, "model", DEFAULT_MODEL),
        "run_status": run.status,
        "responsibility_success": score["success"],
        "action_cost": score["action_cost"],
        "calls": policy.calls,
        "api_successes": policy.api_successes,
        "parsed_json_actions": policy.parsed_json_actions,
        "all_actions_accepted": bool(executed_actions) and rejected_actions == 0,
        "rejected_action_count": rejected_actions,
        "tokens": policy.tokens,
        "latency_ms": policy.latency_ms,
        "errors": dict(policy.errors),
        "trace_digest": run.trace_digest,
        "public_action_records": action_records,
        "model_output_records": policy.output_records,
    }


def smoke_accepted(result: dict[str, Any]) -> bool:
    return bool(
        result["run_status"] == "completed"
        and result["api_successes"] == result["calls"]
        and result["parsed_json_actions"] == result["calls"]
        and not result["errors"]
        and result["all_actions_accepted"]
        and not any(row["type"] in {"protocol_error", "backend_error"} for row in result["public_action_records"])
    )


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    calls = sum(row["calls"] for row in results)
    successful = [row for row in results if row["responsibility_success"]]
    return {
        "episode_count": len(results),
        "responsibility_success_rate": sum(row["responsibility_success"] for row in results) / len(results) if results else None,
        "success_conditioned_action_cost": fmean(row["action_cost"] for row in successful) if successful else None,
        "completed_run_rate": sum(row["run_status"] == "completed" for row in results) / len(results) if results else None,
        "smoke_acceptance_rate": sum(smoke_accepted(row) for row in results) / len(results) if results else None,
        "decision_call_api_success_rate_after_transport_retries": sum(row["api_successes"] for row in results) / calls if calls else None,
        "plain_json_action_rate": sum(row["parsed_json_actions"] for row in results) / calls if calls else None,
        "rejected_action_count": sum(row["rejected_action_count"] for row in results),
        "total_api_calls": calls,
        "total_tokens": sum(row["tokens"] for row in results),
        "mean_latency_ms_per_call": sum(row["latency_ms"] for row in results) / calls if calls else None,
    }


def build_report(results: list[dict[str, Any]], indices: list[int], *, execution_finished: bool) -> dict[str, Any]:
    return {
        "schema_version": "workflow-v4-flash-prompt-only-v1",
        "execution_finished": execution_finished,
        "experiment_config": {
            "model": results[0]["model"] if results else DEFAULT_MODEL,
            "sample_indices": indices,
            "sampling_semantics": "release-order first classes, one variant-0 Episode per class; not a random or full-30 sample",
            "prompt_template_sha256": prompt_template_sha256(),
            "request_body_fields": REQUEST_BODY_FIELDS,
            "structured_output_enabled": False,
            "transport_retries": 2,
            "model_protocol_failure_retries": 0,
        },
        "metrics_all": aggregate_results(results),
        "metrics_held_out_after_prompt_tuning": aggregate_results(results[1:]),
        "episodes": results,
    }


def main(args: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--stride", type=int, default=10)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ns = parser.parse_args(args)
    validate_release(DEFAULT_RELEASE, minimum_responsibilities=30, minimum_episodes=300)
    if ns.count <= 0 or ns.stride <= 0:
        raise ValueError("count and stride must be positive")
    indices = [ns.index + offset * ns.stride for offset in range(ns.count)]
    if indices[-1] >= 300:
        raise ValueError("sample index exceeds the formal release")
    client = ChatClient(ns.base_url, ns.model)
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for index in indices:
        results.append(evaluate_one(client, index))
        checkpoint = build_report(results, indices[:len(results)], execution_finished=False)
        ns.output.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    report = build_report(results, indices, execution_finished=True)
    ns.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
