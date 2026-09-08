#!/usr/bin/env python3
"""Paired V4 Flash rerun on the frozen 20 workflow Episodes with a grammar-first prompt.

Prompt/payload-order is the ONLY change versus evaluate_workflow_v4_flash.py:
the exact top-level kind grammar comes first, action/action_protocol/response
wrappers are explicitly forbidden, and every action kind ships one complete
legal JSON example. The formal release, Harness, WorkflowBackend, contract,
evaluator, strict JSON parser, endpoint, model, temperature, max_tokens and
transport retries are all reused unchanged. No JSON repair, no wrapper
unpacking, no protocol retries, no structured output, no hidden information.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

from evaluate_harness_v2_v4_flash import ChatClient, DEFAULT_BASE, DEFAULT_MODEL, _parse_strict_json
import evaluate_workflow_v4_flash as _v1
from evaluate_workflow_v4_flash import (
    REQUEST_BODY_FIELDS,
    load_episode,
    smoke_accepted,
)
from evaluate_workflow_formal_release_v1 import DEFAULT_RELEASE, _read_jsonl
from harness_v2.core import Harness
from harness_v2.workflow_evaluator import evaluate_workflow
from validate_formal_dataset_release import validate_release


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "runs/workflow_v4_flash_complete_prompt_20_v2/report.json"
BASELINE_REPORT_PATH = ROOT / "runs/workflow_v4_flash_prompt_only_20_v1/report.json"
BASELINE_REPORT_SHA256 = "74a10ddb0865fc0146ee2e2c99a6be15a9e36932dbf3ad33a6b81692a38e017a"
FIXED_INDICES = [offset * 10 for offset in range(20)]

SYSTEM_PROMPT = (
    "You are responsible for carrying out the user's standing household request. "
    "Observe events and device state, act only when justified, and keep monitoring until the episode ends. "
    "Your response is parsed directly as JSON. Think silently. Your entire response MUST start with { and end with }. "
    "The response describes exactly ONE next action: a single JSON object whose top-level \"kind\" is one of exactly five values: "
    "\"act\", \"wait\", \"ask\", \"cancel_rule\", \"install_rule\". "
    "Top-level keys named \"action\", \"action_protocol\", or \"response\" (or any other wrapper) are forbidden: "
    "the object you output IS the action itself. Never output reasoning, a preamble, Markdown, code fences, or trailing text. "
    "Before sending, remove everything outside the single JSON object. "
    "Output exactly one next action per call. After any state-changing action you are re-queried with a fresh observation; "
    "re-observe before deciding the next step."
)
GRAMMAR = [
    '{"kind":"act","commands":[{"device_id":"<published device_id>","capability":"<published capability>","operation":"<published operation>","parameters":{...}}]}',
    '{"kind":"wait","mode":"for","duration_seconds":<number in (0, 604800]>}',
    '{"kind":"wait","mode":"until","timestamp":"<future RFC3339 timestamp>"}',
    '{"kind":"wait","mode":"until_event","event_filter":{"<event field>":"<scalar value>"},"timeout_seconds":<number in (0, 604800]>}',
    '{"kind":"ask","question":"<string>"}',
    '{"kind":"cancel_rule","rule_id":"<id from observation.active_rule_ids>"}',
    '{"kind":"install_rule","rule":{"rule_id":"<new unique id>","fire_at_step":<absolute step GREATER than current observation step>,"release_at_step":<absolute step GREATER than fire_at_step, or null>,"commands":[<command>, ...],"release_commands":[<command>, ...]}}',
]
ACTION_EXAMPLES = {
    "act": {"kind": "act", "commands": [{
        "device_id": "notification.service",
        "capability": "notification.send",
        "operation": "send",
        "parameters": {"message": "The laundry cycle has finished.", "channel": "app", "recipients": ["resident.primary"]},
    }]},
    "wait_for": {"kind": "wait", "mode": "for", "duration_seconds": 300},
    "wait_until": {"kind": "wait", "mode": "until", "timestamp": "2026-01-01T19:00:00Z"},
    "wait_until_event": {
        "kind": "wait", "mode": "until_event",
        "event_filter": {"type": "laundry_cycle_finished"}, "timeout_seconds": 3600,
    },
    "ask": {"kind": "ask", "question": "Is the washer still running?"},
    "cancel_rule": {"kind": "cancel_rule", "rule_id": "rule.notify-washer-done"},
    "install_rule": {"kind": "install_rule", "rule": {
        "rule_id": "rule.notify-washer-done", "fire_at_step": 1, "release_at_step": None,
        "commands": [{
            "device_id": "notification.service",
            "capability": "notification.send",
            "operation": "send",
            "parameters": {"message": "Washer cycle finished.", "channel": "app", "recipients": ["resident.primary"]},
        }],
        "release_commands": [],
    }},
}
PROTOCOL_RULES = [
    "The first response character must be { and the last must be }.",
    "Return exactly one plain JSON object: one of the five top-level kinds in grammar; no reasoning, markdown, code fence, preamble, or trailing text.",
    'Never wrap the action: top-level keys "action", "action_protocol", or "response" are forbidden; the response object itself IS the action.',
    "Each object field set must match its grammar branch exactly; use only published device interfaces and exact parameter schemas from device_interfaces below.",
    "install_rule.rule.fire_at_step is an absolute step that must be strictly greater than the current observation step: each step advances observation.tick_seconds seconds, so compute fire_at_step = current observation step + ceil(delay_seconds / tick_seconds); never reuse a fixed constant.",
    "The install_rule example is generated from the current observation on every call; do not reuse an earlier example's step value.",
    "install_rule.rule.commands must be a non-empty list; release_at_step is optional and release_commands must be non-empty exactly when release_at_step is set.",
    "wait means allowing real simulated time to pass; choose its duration yourself; until_event stops early once an event matching event_filter appears; until requires a future RFC3339 timestamp.",
    "An empty act command list is legal but changes nothing.",
    "Output exactly one next action per call. After any change is accepted you are re-queried with a fresh observation; re-observe before acting again.",
]


def prompt_template_sha256() -> str:
    value = {"system": SYSTEM_PROMPT, "grammar": GRAMMAR, "examples": ACTION_EXAMPLES, "protocol_rules": PROTOCOL_RULES}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def action_protocol(view: dict[str, Any]) -> dict[str, Any]:
    """Grammar and legal examples first; large inventory last. Public material only."""

    examples = deepcopy(ACTION_EXAMPLES)
    observation = view["observation"]
    current_step = int(observation["step"])
    current_time = datetime.fromisoformat(observation["time"].replace("Z", "+00:00"))
    examples["wait_until"]["timestamp"] = (current_time + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    active_rule_ids = set(observation.get("active_rule_ids", []))
    if active_rule_ids:
        examples["cancel_rule"]["rule_id"] = sorted(active_rule_ids)[0]
    else:
        del examples["cancel_rule"]
    rule_id = f"rule.prompt-example-{current_step}"
    suffix = 0
    while rule_id in active_rule_ids:
        suffix += 1
        rule_id = f"rule.prompt-example-{current_step}-{suffix}"
    examples["install_rule"]["rule"]["rule_id"] = rule_id
    examples["install_rule"]["rule"]["fire_at_step"] = current_step + 1
    return {
        "grammar": list(GRAMMAR),
        "examples": examples,
        "rules": list(PROTOCOL_RULES),
        "allowed_actions": view["allowed_actions"],
        "device_interfaces": [
            {"device_id": item["device_id"], "interfaces": item["interfaces"]}
            for item in view["observation"]["inventory"]["devices"]
        ],
    }


def build_messages(view: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "action_protocol": action_protocol(view),
        "query": view["query"],
        "user_preferences": view["public_profile"],
        "current_observation": view["observation"],
        "previous_action_result": view["last_feedback"],
    }
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
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
        "harness_action_records": len(executed_actions),
        "accepted_harness_actions": sum(row["accepted"] is True for row in executed_actions),
        "protocol_error_count": sum(row["type"] == "protocol_error" for row in action_records),
        "backend_error_count": sum(row["type"] == "backend_error" for row in action_records),
        "all_actions_accepted": bool(executed_actions) and rejected_actions == 0,
        "rejected_action_count": rejected_actions,
        "tokens": policy.tokens,
        "latency_ms": policy.latency_ms,
        "errors": dict(policy.errors),
        "trace_digest": run.trace_digest,
        "public_action_records": action_records,
        "model_output_records": policy.output_records,
    }


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = _v1.aggregate_results(results)
    calls = sum(row["calls"] for row in results)
    harness_actions = sum(row["harness_action_records"] for row in results)
    accepted = sum(row["accepted_harness_actions"] for row in results)
    metrics["protocol_valid_action_rate"] = harness_actions / calls if calls else None
    metrics["backend_accepted_action_rate"] = accepted / harness_actions if harness_actions else None
    metrics["harness_action_total"] = harness_actions
    metrics["accepted_harness_action_total"] = accepted
    metrics["protocol_error_count"] = sum(row["protocol_error_count"] for row in results)
    metrics["backend_error_count"] = sum(row["backend_error_count"] for row in results)
    return metrics


def paired_outcomes(baseline_episodes: list[dict[str, Any]], current_episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Episode-level gained/lost/tied versus v1, paired by identical Episode ID."""

    baseline_ids = [row["episode_id"] for row in baseline_episodes]
    current_ids = [row["episode_id"] for row in current_episodes]
    if len(baseline_ids) != len(set(baseline_ids)) or len(current_ids) != len(set(current_ids)):
        raise RuntimeError("paired Episode IDs must be unique")
    if current_ids != baseline_ids[: len(current_ids)]:
        raise RuntimeError("current Episodes must exactly match a prefix of the frozen baseline")
    current_by_id = {row["episode_id"]: row for row in current_episodes}
    dimensions = {
        "responsibility_success": lambda row: bool(row["responsibility_success"]),
        "run_completed": lambda row: row["run_status"] == "completed",
    }
    paired: dict[str, Any] = {}
    for name, pick in dimensions.items():
        gained: list[str] = []
        lost: list[str] = []
        tied: list[str] = []
        matched = 0
        for base in baseline_episodes:
            current = current_by_id.get(base["episode_id"])
            if current is None:
                break
            matched += 1
            before, after = pick(base), pick(current)
            if not before and after:
                gained.append(base["episode_id"])
            elif before and not after:
                lost.append(base["episode_id"])
            else:
                tied.append(base["episode_id"])
        paired[name] = {
            "gained": gained,
            "lost": lost,
            "tied": tied,
            "gained_count": len(gained),
            "lost_count": len(lost),
            "tied_count": len(tied),
            "paired_episode_count": matched,
        }
    return paired


def assert_v1_baseline_frozen(path: Path = BASELINE_REPORT_PATH, release_dir: Path = DEFAULT_RELEASE) -> dict[str, Any]:
    """Verify the frozen v1 report: SHA256, the fixed 20 indices, and matching Episode IDs."""

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != BASELINE_REPORT_SHA256:
        raise RuntimeError(f"v1 baseline report SHA256 mismatch: {digest}")
    report = json.loads(path.read_text(encoding="utf-8"))
    indices = report["experiment_config"]["sample_indices"]
    if indices != FIXED_INDICES:
        raise RuntimeError(f"v1 baseline sample indices must be exactly {FIXED_INDICES}")
    episodes = report["episodes"]
    if len(episodes) != len(FIXED_INDICES):
        raise RuntimeError("v1 baseline must contain exactly 20 paired Episodes")
    public_rows = _read_jsonl(release_dir / "episodes_public.jsonl")
    expected_ids = [public_rows[index]["episode_id"] for index in FIXED_INDICES]
    actual_ids = [row["episode_id"] for row in episodes]
    if actual_ids != expected_ids:
        raise RuntimeError("v1 baseline Episode IDs do not match the fixed release indices")
    return report


def build_report(results: list[dict[str, Any]], indices: list[int], *, execution_finished: bool, baseline: dict[str, Any]) -> dict[str, Any]:
    baseline_ids = [row["episode_id"] for row in baseline["episodes"]]
    result_ids = [row["episode_id"] for row in results]
    expected_ids = baseline_ids if execution_finished else baseline_ids[: len(results)]
    if result_ids != expected_ids:
        raise RuntimeError("v2 result Episode IDs do not exactly match the frozen paired sample")
    return {
        "schema_version": "workflow-v4-flash-complete-prompt-v2",
        "execution_finished": execution_finished,
        "experiment_config": {
            "model": results[0]["model"] if results else DEFAULT_MODEL,
            "base_url": DEFAULT_BASE,
            "sample_indices": indices,
            "sampling_semantics": "release-order first classes, one variant-0 Episode per class; not a random or full-30 sample",
            "prompt_template_sha256": prompt_template_sha256(),
            "request_body_fields": REQUEST_BODY_FIELDS,
            "structured_output_enabled": False,
            "transport_retries": 2,
            "model_protocol_failure_retries": 0,
            "changed_vs_v1": ["system_prompt", "payload_order_and_content"],
            "baseline_report_path": str(BASELINE_REPORT_PATH.relative_to(ROOT)),
            "baseline_report_sha256": BASELINE_REPORT_SHA256,
        },
        "metrics_all": aggregate_results(results),
        "paired_vs_v1": paired_outcomes(baseline["episodes"], results),
        "episodes": results,
    }


def main(args: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--stride", type=int, default=10)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ns = parser.parse_args(args)
    if ns.base_url != DEFAULT_BASE or ns.model != DEFAULT_MODEL:
        raise ValueError("paired v2 rerun freezes the default base URL and model")
    validate_release(DEFAULT_RELEASE, minimum_responsibilities=30, minimum_episodes=300)
    if ns.count <= 0 or ns.stride <= 0:
        raise ValueError("count and stride must be positive")
    indices = [ns.index + offset * ns.stride for offset in range(ns.count)]
    if indices != FIXED_INDICES:
        raise ValueError("the paired v2 rerun requires exactly the fixed indices 0,10,...,190")
    baseline = assert_v1_baseline_frozen()
    if baseline["experiment_config"].get("model") != DEFAULT_MODEL:
        raise RuntimeError("v1 baseline model does not match the frozen default model")
    client = ChatClient(ns.base_url, ns.model)
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for index in indices:
        results.append(evaluate_one(client, index))
        checkpoint = build_report(results, indices[:len(results)], execution_finished=False, baseline=baseline)
        ns.output.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    report = build_report(results, indices, execution_finished=True, baseline=baseline)
    ns.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
