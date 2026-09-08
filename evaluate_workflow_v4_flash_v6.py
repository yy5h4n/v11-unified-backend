#!/usr/bin/env python3
"""V6 answer-envelope smoke rerun on the same frozen 15 notification-only-filtered Episodes.

Compatibility-safe orchestration layer on top of the untouched v4 prompt
machinery and the v5 frozen release/sample.  Differences from v5:

- The model MUST wrap its single next action in exactly one
  ``<answer>JSON_OBJECT</answer>`` envelope.  Reasoning outside the envelope
  is ignored.  Responses with a missing, multiple, nested, empty, or
  malformed envelope are rejected, and the envelope payload is strict-parsed
  (duplicate JSON keys and nonfinite constants are rejected).  The envelope
  protocol is taught in the v6 system prompt, grammar, and examples.
- Token usage is recorded separately as ``prompt_tokens``,
  ``completion_tokens``, and ``total_tokens`` per call, per Episode, and in
  the aggregate; ``completion_tokens`` is the output-token metric.  Missing
  provider fields stay zero; the provider total is never synthesized and is
  retained exactly even when it disagrees with prompt+completion.  Presence
  of the provider total, and (only when all three provider fields are
  present) whether it matches prompt+completion, are recorded per call and
  counted in the aggregate.

Historical v1-v5 files are untouched.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import evaluate_workflow_v4_flash_v4 as v4
import evaluate_workflow_v4_flash_v5 as v5
from evaluate_harness_v2_v4_flash import ChatClient, DEFAULT_BASE, DEFAULT_MODEL, _parse_strict_json
from harness_v2.core import Harness
from harness_v2.temporal_home_evaluator import evaluate_temporal_home


ROOT = Path(__file__).resolve().parent
RELEASE_DIR = v5.RELEASE_DIR
DEFAULT_OUTPUT = ROOT / "runs/workflow_v4_flash_non_notification_15_v6/report.json"
SAMPLE_INDICES = v5.SAMPLE_INDICES
SAMPLE_EPISODE_IDS = v5.SAMPLE_EPISODE_IDS
SCHEMA_VERSION = "temporal-home-v4-flash-notification-only-filtered-smoke15-v6-answer-envelope"

ANSWER_OPEN = "<answer>"
ANSWER_CLOSE = "</answer>"
ANSWER_ENVELOPE_RE = re.compile(re.escape(ANSWER_OPEN) + r"(.*?)" + re.escape(ANSWER_CLOSE), re.DOTALL)

V6_SYSTEM_PROMPT = (
    "You are responsible for carrying out the user's standing household request. "
    "Observe events and device state, act only when justified, and keep monitoring until the episode ends. "
    "Your response is parsed directly. Think silently. "
    'Your response MUST contain exactly one <answer> envelope: the literal marker <answer>, then one plain JSON object, '
    'then the literal marker </answer>. Inside the envelope the JSON object\'s top-level "kind" is one of exactly four '
    'values: "act", "wait", "cancel_rule", "install_rule". '
    "Any reasoning may appear only OUTSIDE the envelope and is ignored; inside the envelope there must be nothing but "
    "the single JSON object. "
    "Never omit the envelope, never emit two or more <answer> markers, never nest <answer> inside <answer>, and never "
    "leave the envelope empty. "
    "Before sending, remove everything outside the single <answer>...</answer> envelope. "
    'The JSON object inside the envelope IS the action itself: top-level keys named "action", "action_protocol", or '
    '"response" (or any other wrapper) are forbidden. '
    "Output exactly one next action per call. After any state-changing action you are re-queried with a fresh "
    "observation; re-observe before deciding the next step."
)
V6_GRAMMAR = [ANSWER_OPEN + line + ANSWER_CLOSE for line in v4.GRAMMAR]


def _envelope_example(action: dict[str, Any]) -> str:
    return ANSWER_OPEN + json.dumps(action, ensure_ascii=False, sort_keys=True) + ANSWER_CLOSE


V6_ACTION_EXAMPLES = {
    name: _envelope_example(action) for name, action in v4.ACTION_EXAMPLES.items()
}
V6_PROTOCOL_RULES = [
    "The response must contain exactly one <answer>...</answer> envelope; reasoning is allowed only outside the envelope and is ignored.",
    "Inside the envelope return exactly one plain JSON object: one of the four top-level kinds in grammar; no reasoning, markdown, code fence, preamble, or trailing text inside the envelope.",
    "Never emit a missing, second, nested, empty, or unclosed <answer> envelope; exactly one opening marker and one closing marker, in that order.",
    'Never wrap the action inside the envelope: top-level keys "action", "action_protocol", or "response" are forbidden; the envelope object itself IS the action.',
    "Each object field set must match its grammar branch exactly; use only published device interfaces and exact parameter schemas from device_interfaces in this system message.",
    "install_rule.rule.fire_at_step is an absolute step that must be strictly greater than the current observation step: each step advances observation.tick_seconds seconds, so compute fire_at_step = current observation step + ceil(delay_seconds / tick_seconds); never reuse a fixed constant.",
    "All action_examples are illustrative only; every timestamp and step value must be computed from the current observation in the user payload, never copied from an example or from an earlier call.",
    "install_rule.rule.commands must be a non-empty list; release_at_step is optional and release_commands must be non-empty exactly when release_at_step is set.",
    "wait means allowing real simulated time to pass; choose its duration yourself; until_event stops early once an event matching event_filter appears; until requires a future RFC3339 timestamp computed from the current observation time.",
    "The observable_event_interface lists the event types available in this Episode: query-relevant types plus conservative device-completion and exogenous-trigger fallback groups plus the rule lifecycle events. Use only its filterable scalar fields in wait.until_event; it does not reveal which events will occur or when.",
    "After a rejected action, do not repeat the identical action. Re-observe and choose a corrected action or a short wait.",
    "An empty act command list is legal but changes nothing.",
    "Output exactly one next action per call. After any change is accepted you are re-queried with a fresh observation; re-observe before acting again.",
]
EVENT_INTERFACE_SCOPE_NOTE = v4.EVENT_INTERFACE_SCOPE_NOTE


def parse_answer_envelope(content: Any) -> dict[str, Any]:
    """Extract and strict-parse the single <answer>JSON_OBJECT</answer> envelope.

    Reasoning outside the envelope is ignored.  Rejections: non-text content,
    missing envelope, multiple envelopes, malformed envelope (unclosed or
    reversed markers), empty envelope, and (via strict JSON parsing) duplicate
    keys, nonfinite constants, and non-object payloads.
    """

    if not isinstance(content, str):
        raise ValueError("response_not_text")
    opens = content.count(ANSWER_OPEN)
    closes = content.count(ANSWER_CLOSE)
    if opens == 0 and closes == 0:
        raise ValueError("missing_answer_envelope")
    if opens > 1 or closes > 1:
        raise ValueError("multiple_answer_envelopes")
    match = ANSWER_ENVELOPE_RE.search(content)
    if match is None:
        raise ValueError("malformed_answer_envelope")
    inner = match.group(1).strip()
    if not inner:
        raise ValueError("empty_answer_envelope")
    return _parse_strict_json(inner)


def build_system_content(
    query: str,
    device_interfaces: list[dict[str, Any]],
    selected_event_types: tuple[str, ...],
) -> str:
    """Episode-static v6 system message with the answer-envelope protocol."""

    selected = set(selected_event_types)
    document = {
        "role_directive": V6_SYSTEM_PROMPT,
        "response_envelope": {
            "format": "<answer>{single plain JSON action object}</answer>",
            "rules": [
                "exactly one envelope per response",
                "reasoning outside the envelope is ignored",
                "missing, multiple, nested, empty, or malformed envelopes are rejected",
                "duplicate JSON keys and nonfinite constants are rejected",
            ],
        },
        "action_grammar": list(V6_GRAMMAR),
        "action_examples": deepcopy(V6_ACTION_EXAMPLES),
        "protocol_rules": list(V6_PROTOCOL_RULES),
        "device_interfaces": device_interfaces,
        "observable_event_interface": {
            "scope_note": EVENT_INTERFACE_SCOPE_NOTE,
            "selected_event_types": list(selected_event_types),
            "event_catalog": [
                row for row in v4.OBSERVABLE_EVENT_CATALOG if row["event_type"] in selected
            ],
        },
    }
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1)


def system_prompt_sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def prompt_template_sha256() -> str:
    value = {
        "system_directive": V6_SYSTEM_PROMPT,
        "response_envelope_rules": V6_PROTOCOL_RULES[:3],
        "grammar": V6_GRAMMAR,
        "examples": V6_ACTION_EXAMPLES,
        "protocol_rules": V6_PROTOCOL_RULES,
        "observable_event_catalog": v4.OBSERVABLE_EVENT_CATALOG,
        "fallback_event_groups": {name: sorted(group) for name, group in v4.FALLBACK_EVENT_GROUPS.items()},
        "rule_event_types": sorted(v4.RULE_EVENT_TYPES),
        "query_keyword_tokens": v4.QUERY_KEYWORD_TOKENS,
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class EnvelopeWorkflowLLMPolicy(v4.WorkflowLLMPolicy):
    """V4 policy with the answer-envelope protocol and split token accounting."""

    def __init__(self, client: Any):
        super().__init__(client)
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.provider_total_tokens_present_calls = 0
        self.provider_total_tokens_mismatch_calls = 0

    def _episode_system_content(self, view: dict[str, Any]) -> str:
        query = view["query"]
        selected, matched = v4.select_event_types_for_query(query)
        device_interfaces = v4.device_interfaces_from_observation(view["observation"])
        content = build_system_content(query, device_interfaces, selected)
        if self.system_content is None:
            self.system_content = content
            self.system_prompt_sha256 = system_prompt_sha256(content)
            self.selected_event_types = selected
            self.selection_matched_keywords = matched
        elif content != self.system_content:
            raise RuntimeError("Episode system prefix must be byte-identical across calls")
        return self.system_content

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        record: dict[str, Any] = {
            "call_index": self.calls - 1,
            "raw_content": None,
            "raw_content_sha256": None,
            "usage": None,
            "error": None,
        }
        system_content = self._episode_system_content(view)
        try:
            response = self.client.complete(v4.build_messages(view, self.previous_action, system_content))
            self.api_successes += 1
            usage = usage_token_record(response.get("usage"))
            self.prompt_tokens += usage["prompt_tokens"]
            self.completion_tokens += usage["completion_tokens"]
            self.total_tokens += usage["total_tokens"]
            if usage["provider_total_tokens_present"]:
                self.provider_total_tokens_present_calls += 1
                if usage["provider_total_tokens_matches_sum"] is False:
                    self.provider_total_tokens_mismatch_calls += 1
            record["usage"] = usage
            self.latency_ms += float(response.get("latency_ms", 0.0))
            content = response.get("content")
            if isinstance(content, str):
                record["raw_content"] = content
                record["raw_content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
            action = parse_answer_envelope(content)
            self.parsed_json_actions += 1
            self.previous_action = deepcopy(action)
            return action
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            record["error"] = error
            self.errors[error] += 1
            return {"kind": "invalid_model_output"}
        finally:
            self.output_records.append(record)


def usage_token_record(usage: Any) -> dict[str, Any]:
    """Split a provider usage payload without ever synthesizing a total.

    Missing ``prompt_tokens``/``completion_tokens``/``total_tokens`` each stay
    zero.  The provider total is retained exactly, even when it disagrees with
    prompt+completion; whether it was present, and (only when all three
    provider fields are present) whether it matches the sum, are recorded
    separately instead of being corrected.
    """

    usage = usage or {}
    prompt_present = "prompt_tokens" in usage
    completion_present = "completion_tokens" in usage
    total_present = "total_tokens" in usage
    prompt = int(usage["prompt_tokens"] or 0) if prompt_present else 0
    completion = int(usage["completion_tokens"] or 0) if completion_present else 0
    total = int(usage["total_tokens"] or 0) if total_present else 0
    all_present = prompt_present and completion_present and total_present
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "provider_prompt_tokens_present": prompt_present,
        "provider_completion_tokens_present": completion_present,
        "provider_total_tokens_present": total_present,
        "provider_total_tokens_matches_sum": (total == prompt + completion) if all_present else None,
    }


def expected_episode_prompt(index: int) -> tuple[str, tuple[str, ...]]:
    public, _, spec, backend = v4.load_temporal_episode(index, RELEASE_DIR)
    observation = backend.reset(spec).public_observation
    selected, _ = v4.select_event_types_for_query(public["query"])
    content = build_system_content(
        public["query"], v4.device_interfaces_from_observation(observation), selected
    )
    return system_prompt_sha256(content), selected


def evaluate_one(client: Any, index: int, release_dir: Path = RELEASE_DIR) -> dict[str, Any]:
    public, private, spec, backend = v4.load_temporal_episode(index, release_dir)
    policy = EnvelopeWorkflowLLMPolicy(client)
    run = Harness(backend).run_one(spec, policy)
    score = evaluate_temporal_home(
        private["contract"]["scenario_type"], run,
        contract=private["contract"], profile=public["public_profile"],
    )
    action_records = [row for row in run.public_trace if row["type"] in {"action", "protocol_error", "backend_error"}]
    executed_actions = [row for row in action_records if row["type"] == "action"]
    rejected_actions = sum(row["accepted"] is not True for row in executed_actions)
    result = {
        "episode_id": spec.episode_id,
        "query": public["query"],
        "model": getattr(client, "model", DEFAULT_MODEL),
        "run_status": run.status,
        "responsibility_success": score["success"],
        "device_command_count": score["device_command_count"],
        "count_unit": score["count_unit"],
        "calls": policy.calls,
        "api_successes": policy.api_successes,
        "parsed_json_actions": policy.parsed_json_actions,
        "harness_action_records": len(executed_actions),
        "accepted_harness_actions": sum(row["accepted"] is True for row in executed_actions),
        "protocol_error_count": sum(row["type"] == "protocol_error" for row in action_records),
        "backend_error_count": sum(row["type"] == "backend_error" for row in action_records),
        "all_actions_accepted": bool(executed_actions) and rejected_actions == 0,
        "rejected_action_count": rejected_actions,
        "system_prompt_sha256": policy.system_prompt_sha256,
        "selected_event_types": list(policy.selected_event_types),
        "selection_matched_keywords": list(policy.selection_matched_keywords),
        "prompt_tokens": policy.prompt_tokens,
        "completion_tokens": policy.completion_tokens,
        "total_tokens": policy.total_tokens,
        "provider_total_tokens_present_calls": policy.provider_total_tokens_present_calls,
        "provider_total_tokens_mismatch_calls": policy.provider_total_tokens_mismatch_calls,
        "latency_ms": policy.latency_ms,
        "errors": dict(policy.errors),
        "trace_digest": run.trace_digest,
        "public_action_records": action_records,
        "model_output_records": policy.output_records,
    }
    result["max_consecutive_identical_rejected_action"] = v4.max_consecutive_identical_rejected_action(action_records)
    return result


def metrics_with_device_total(results: list[dict[str, Any]]) -> dict[str, Any]:
    # v4.aggregate_results reads a legacy per-row "tokens" key; feed it private
    # temporary copies so the generic "tokens" field never reaches v6 reports.
    compat_rows = []
    for row in results:
        compat = dict(row)
        compat["tokens"] = row["total_tokens"]
        compat_rows.append(compat)
    metrics = v4.aggregate_results(compat_rows)
    metrics["total_device_command_count"] = sum(row["device_command_count"] for row in results)
    metrics["prompt_tokens"] = sum(row["prompt_tokens"] for row in results)
    metrics["completion_tokens"] = sum(row["completion_tokens"] for row in results)
    metrics["total_tokens"] = sum(row["total_tokens"] for row in results)
    metrics["provider_total_tokens_present_calls"] = sum(row["provider_total_tokens_present_calls"] for row in results)
    metrics["provider_total_tokens_mismatch_calls"] = sum(row["provider_total_tokens_mismatch_calls"] for row in results)
    metrics["output_token_metric"] = "completion_tokens"
    metrics["output_tokens"] = metrics["completion_tokens"]
    return metrics


def validate_result_prefix(results: list[dict[str, Any]]) -> None:
    if [row.get("episode_id") for row in results] != SAMPLE_EPISODE_IDS[: len(results)]:
        raise RuntimeError("checkpoint results are not a prefix of the frozen sample")
    public_rows, _ = v5.assert_release_and_sample_frozen()
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


def build_report(results: list[dict[str, Any]], *, execution_finished: bool) -> dict[str, Any]:
    validate_result_prefix(results)
    if execution_finished and len(results) != len(SAMPLE_INDICES):
        raise RuntimeError("finished report requires all 15 Episodes")
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_finished": execution_finished,
        "experiment_config": {
            "model": results[0]["model"] if results else DEFAULT_MODEL,
            "base_url": DEFAULT_BASE,
            "release_dir": str(RELEASE_DIR.relative_to(ROOT)),
            "release_public_sha256": v5.PUBLIC_RELEASE_SHA256,
            "release_private_sha256": v5.PRIVATE_RELEASE_SHA256,
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
            "prompt_implementation": "evaluate_workflow_v4_flash_v6",
            "prompt_template_sha256": prompt_template_sha256(),
            "observable_event_catalog_sha256": v5.V4_EVENT_CATALOG_SHA256,
            "system_prefix_policy": "episode_static_byte_identical_across_calls",
            "response_envelope_protocol": {
                "format": "<answer>JSON_OBJECT</answer>",
                "required": True,
                "rejected_cases": ["missing", "multiple", "nested", "empty", "malformed"],
                "strict_json": ["duplicate_keys_rejected", "nonfinite_constants_rejected"],
                "outside_reasoning": "ignored",
            },
            "output_token_metric": "completion_tokens",
            "token_accounting": {
                "provider_total_synthesized": False,
                "missing_provider_fields": "stay_zero",
                "provider_total_mismatch": "retained_exactly_and_counted",
                "presence_counters": ["provider_total_tokens_present_calls"],
                "mismatch_counters": ["provider_total_tokens_mismatch_calls"],
                "legacy_generic_tokens_field": "not_emitted",
            },
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
    if checkpoint.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("existing output is not a v6 smoke checkpoint")
    if checkpoint.get("execution_finished") is True:
        raise RuntimeError("refusing to overwrite an already completed v6 report")
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
        raise ValueError("v6 smoke freezes the default base URL and model")
    v5.assert_release_and_sample_frozen()
    results = load_checkpoint(ns.output)
    client = ChatClient(ns.base_url, ns.model)
    for index in SAMPLE_INDICES[len(results):]:
        results.append(evaluate_one(client, index, RELEASE_DIR))
        atomic_write_report(ns.output, build_report(results, execution_finished=False))
    report = build_report(results, execution_finished=True)
    atomic_write_report(ns.output, report)
    return report


if __name__ == "__main__":
    main()
