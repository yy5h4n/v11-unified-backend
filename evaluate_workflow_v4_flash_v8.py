#!/usr/bin/env python3
"""V8 text-envelope runner with complete action/environment history.

V8 deliberately uses the gateway-compatible V6 ``<answer>JSON</answer>``
transport while retaining every canonical action and subsequent public
environment observation in the next request.  It never sends native ``tools``
or native tool calls.  Model prose/thinking is neither replayed nor persisted.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import evaluate_workflow_v4_flash_v4 as v4
import evaluate_workflow_v4_flash_v5 as v5
import evaluate_workflow_v4_flash_v6 as v6
from evaluate_harness_v2_v4_flash import APIError, ChatClient, DEFAULT_BASE, DEFAULT_MODEL
from harness_v2.core import Harness
from harness_v2.temporal_home_evaluator import evaluate_temporal_home


ROOT = Path(__file__).resolve().parent
RELEASE_DIR = v5.RELEASE_DIR
DEFAULT_OUTPUT = ROOT / "runs/workflow_v4_flash_non_notification_15_v8/report.json"
SAMPLE_INDICES = v5.SAMPLE_INDICES
SAMPLE_EPISODE_IDS = v5.SAMPLE_EPISODE_IDS
SCHEMA_VERSION = "temporal-home-v4-flash-notification-only-filtered-smoke15-v8-json-history"

MIGRATION_MANIFEST = {
    "frozen_sample": "v5 exact 15 Episode indices and IDs",
    "action_transport": "v6 exactly-one <answer>JSON_OBJECT</answer> text envelope",
    "conversation": "initial request followed by complete canonical action/environment-observation history",
    "native_tools": "not sent and not required",
    "previous_action_fields": "removed; represented once in chronological history",
    "thinking": "not retained and not replayed",
    "raw_model_content": "sha256 and UTF-8 byte length only",
    "token_metric": "api_output_tokens (exactly the provider completion_tokens/output_tokens)",
    "checkpoint": "prefix validation, exact rebuild, atomic replace, completed-report refusal",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def build_initial_user_message(view: dict[str, Any]) -> dict[str, str]:
    payload = {
        "message_type": "initial_request",
        "original_query": view["query"],
        "public_preferences": view["public_profile"],
        "initial_observation": view["observation"],
    }
    return {"role": "user", "content": canonical_json(payload)}


def canonical_assistant_action(action: dict[str, Any]) -> dict[str, str]:
    return {"role": "assistant", "content": v6.ANSWER_OPEN + canonical_json(action) + v6.ANSWER_CLOSE}


def build_environment_message(feedback: Any, observation: dict[str, Any]) -> dict[str, str]:
    """Represent a tool/environment result without native tool-call syntax.

    OpenAI-compatible APIs require role=tool messages to reference a preceding
    native tool_call_id.  V8 intentionally has no such dependency, so the
    environment speaks through a structured user-role message instead.
    """
    payload = {
        "message_type": "environment_observation",
        "action_result": feedback,
        "current_observation": observation,
    }
    return {"role": "user", "content": canonical_json(payload)}


def response_fingerprint(content: Any) -> tuple[int, str | None]:
    if not isinstance(content, str):
        return 0, None
    encoded = content.encode("utf-8")
    return len(encoded), hashlib.sha256(encoded).hexdigest()


class JsonEnvelopeHistoryPolicy:
    """Closed-loop policy with canonical, replayable, privacy-safe history."""

    def __init__(self, client: Any):
        self.client = client
        self.calls = self.api_successes = self.parsed_json_actions = 0
        self.prompt_tokens = self.completion_tokens = self.output_tokens = self.provider_total_tokens = 0
        self.provider_total_tokens_present_calls = self.provider_total_tokens_mismatch_calls = 0
        self.latency_ms = 0.0
        self.errors: Counter[str] = Counter()
        self.output_records: list[dict[str, Any]] = []
        self.history: list[dict[str, str]] = []
        self.pending_action = False
        self.system_content: str | None = None
        self.system_prompt_sha256: str | None = None
        self.selected_event_types: tuple[str, ...] = ()
        self.selection_matched_keywords: tuple[str, ...] = ()

    def _initialize(self, view: dict[str, Any]) -> None:
        selected, matched = v4.select_event_types_for_query(view["query"])
        interfaces = v4.device_interfaces_from_observation(view["observation"])
        system = v6.build_system_content(view["query"], interfaces, selected)
        self.system_content = system
        self.system_prompt_sha256 = v6.system_prompt_sha256(system)
        self.selected_event_types = selected
        self.selection_matched_keywords = matched
        self.history = [{"role": "system", "content": system}, build_initial_user_message(view)]

    def _verify_static_prefix(self, view: dict[str, Any]) -> None:
        selected, _ = v4.select_event_types_for_query(view["query"])
        candidate = v6.build_system_content(
            view["query"], v4.device_interfaces_from_observation(view["observation"]), selected
        )
        if candidate != self.system_content:
            raise RuntimeError("Episode system prefix must be byte-identical across calls")

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        if self.system_content is None:
            self._initialize(view)
            if view.get("last_feedback") is not None:
                raise RuntimeError("initial decision unexpectedly has prior feedback")
        else:
            self._verify_static_prefix(view)
            if not self.pending_action or not self.history or self.history[-1].get("role") != "assistant":
                raise RuntimeError("missing prior canonical assistant action")
            self.history.append(build_environment_message(view.get("last_feedback"), view["observation"]))
            self.pending_action = False

        self.calls += 1
        record: dict[str, Any] = {
            "call_index": self.calls - 1,
            "canonical_action": None,
            "raw_response_sha256": None,
            "raw_response_byte_length": 0,
            "usage": None,
            "protocol_error": None,
        }
        try:
            response = self.client.complete(deepcopy(self.history))
            self.api_successes += 1
            usage = v6.usage_token_record(response.get("usage"))
            # Persist an unambiguous V8 usage view. V6's compatibility helper
            # calls the provider aggregate ``total_tokens``; exposing that
            # generic name made prompt+completion look like model output.
            record["usage"] = {
                "api_output_tokens": usage["completion_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "output_tokens": usage["completion_tokens"],
                "diagnostic_prompt_tokens": usage["prompt_tokens"],
                "diagnostic_provider_total_tokens": usage["total_tokens"],
                "provider_prompt_tokens_present": usage["provider_prompt_tokens_present"],
                "provider_completion_tokens_present": usage["provider_completion_tokens_present"],
                "provider_total_tokens_present": usage["provider_total_tokens_present"],
                "provider_total_tokens_matches_sum": usage["provider_total_tokens_matches_sum"],
            }
            self.prompt_tokens += usage["prompt_tokens"]
            self.completion_tokens += usage["completion_tokens"]
            self.output_tokens += usage["completion_tokens"]
            self.provider_total_tokens += usage["total_tokens"]
            if usage["provider_total_tokens_present"]:
                self.provider_total_tokens_present_calls += 1
                if usage["provider_total_tokens_matches_sum"] is False:
                    self.provider_total_tokens_mismatch_calls += 1
            self.latency_ms += float(response.get("latency_ms", 0.0))
            content = response.get("content")
            byte_length, digest = response_fingerprint(content)
            record["raw_response_byte_length"] = byte_length
            record["raw_response_sha256"] = digest
            try:
                action = v6.parse_answer_envelope(content)
            except (TypeError, ValueError) as exc:
                # Only a successfully received model response with an invalid
                # envelope/JSON object is a model protocol error.  APIError is
                # deliberately not caught here: exhausted transport retries
                # must abort the Episode instead of fabricating an
                # invalid_model_output action and a protocol-invalid score.
                error = f"{type(exc).__name__}:{exc}"
                record["protocol_error"] = error
                self.errors[error] += 1
                return {"kind": "invalid_model_output"}
            record["canonical_action"] = deepcopy(action)
            self.history.append(canonical_assistant_action(action))
            self.pending_action = True
            self.parsed_json_actions += 1
            return action
        finally:
            self.output_records.append(record)

    def finalize(self, public_trace: tuple[dict[str, Any], ...]) -> None:
        """Attach public feedback and the newest observation to the final action."""
        if not self.pending_action:
            return
        observations = [row["value"] for row in public_trace if row.get("type") == "observation"]
        if not observations:
            raise RuntimeError("cannot finalize conversation without an observation")
        actions = [row for row in public_trace if row.get("type") == "action"]
        if actions:
            feedback = deepcopy(actions[-1].get("feedback"))
        else:
            errors = [row for row in public_trace if row.get("type") in {"protocol_error", "backend_error"}]
            feedback = {
                "status": "rejected",
                "error_code": "PROTOCOL_ERROR",
                "message": errors[-1].get("message") if errors else "action not executed",
            }
        self.history.append(build_environment_message(feedback, observations[-1]))
        self.pending_action = False


def prompt_template_sha256() -> str:
    payload = {
        "v6_prompt_template_sha256": v6.prompt_template_sha256(),
        "conversation_protocol": MIGRATION_MANIFEST,
        "initial_message_fields": ["message_type", "original_query", "public_preferences", "initial_observation"],
        "environment_message_fields": ["message_type", "action_result", "current_observation"],
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def expected_episode_prompt(index: int) -> tuple[str, tuple[str, ...]]:
    public, _, spec, backend = v4.load_temporal_episode(index, RELEASE_DIR)
    observation = backend.reset(spec).public_observation
    selected, _ = v4.select_event_types_for_query(public["query"])
    content = v6.build_system_content(
        public["query"], v4.device_interfaces_from_observation(observation), selected
    )
    return v6.system_prompt_sha256(content), selected


def validate_canonical_conversation(
    conversation: Any, *, expected_query: str | None = None, expected_episode_id: str | None = None
) -> list[dict[str, Any]]:
    if not isinstance(conversation, list) or len(conversation) < 2:
        raise RuntimeError("checkpoint canonical conversation is incomplete")
    if conversation[0].get("role") != "system" or conversation[1].get("role") != "user":
        raise RuntimeError("checkpoint canonical conversation prefix invalid")
    try:
        initial = json.loads(conversation[1]["content"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("checkpoint canonical initial request invalid") from exc
    if set(initial) != {"message_type", "original_query", "public_preferences", "initial_observation"}:
        raise RuntimeError("checkpoint canonical initial request fields invalid")
    if initial["message_type"] != "initial_request" or conversation[1] != build_initial_user_message({
        "query": initial["original_query"], "public_profile": initial["public_preferences"],
        "observation": initial["initial_observation"],
    }):
        raise RuntimeError("checkpoint canonical initial request serialization invalid")
    if expected_query is not None and initial["original_query"] != expected_query:
        raise RuntimeError("checkpoint canonical initial query mismatch")
    if not isinstance(initial["initial_observation"], dict) or (
        expected_episode_id is not None and initial["initial_observation"].get("episode_id") != expected_episode_id
    ):
        raise RuntimeError("checkpoint canonical initial observation mismatch")
    suffix = conversation[2:]
    if len(suffix) % 2:
        raise RuntimeError("checkpoint canonical conversation has unpaired action")
    actions: list[dict[str, Any]] = []
    for offset in range(0, len(suffix), 2):
        assistant, environment = suffix[offset:offset + 2]
        if not isinstance(assistant, dict) or assistant.get("role") != "assistant" or set(assistant) != {"role", "content"}:
            raise RuntimeError("checkpoint canonical assistant action invalid")
        try:
            action = v6.parse_answer_envelope(assistant["content"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("checkpoint canonical assistant action invalid") from exc
        if assistant != canonical_assistant_action(action):
            raise RuntimeError("checkpoint canonical assistant action serialization invalid")
        if not isinstance(environment, dict) or environment.get("role") != "user":
            raise RuntimeError("checkpoint canonical environment message invalid")
        try:
            payload = json.loads(environment["content"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("checkpoint canonical environment message invalid") from exc
        if set(payload) != {"message_type", "action_result", "current_observation"} or payload["message_type"] != "environment_observation" or not isinstance(payload["current_observation"], dict):
            raise RuntimeError("checkpoint canonical environment fields invalid")
        if environment != build_environment_message(payload["action_result"], payload["current_observation"]):
            raise RuntimeError("checkpoint canonical environment serialization invalid")
        actions.append(action)
    return actions


def _validate_token_row(row: dict[str, Any]) -> None:
    names = (
        "prompt_tokens", "completion_tokens", "output_tokens", "provider_total_tokens",
        "provider_total_tokens_present_calls", "provider_total_tokens_mismatch_calls",
    )
    if any(isinstance(row.get(name), bool) or not isinstance(row.get(name), int) or row[name] < 0 for name in names):
        raise RuntimeError("checkpoint token fields missing or invalid")
    if row["output_tokens"] != row["completion_tokens"]:
        raise RuntimeError("checkpoint output/completion token mismatch")
    if not 0 <= row["provider_total_tokens_mismatch_calls"] <= row["provider_total_tokens_present_calls"] <= row.get("calls", -1):
        raise RuntimeError("checkpoint provider token counters invalid")


def evaluate_one(client: Any, index: int, release_dir: Path = RELEASE_DIR) -> dict[str, Any]:
    public, private, spec, backend = v4.load_temporal_episode(index, release_dir)
    policy = JsonEnvelopeHistoryPolicy(client)
    run = Harness(backend).run_one(spec, policy)
    policy.finalize(run.public_trace)
    score = evaluate_temporal_home(
        private["contract"]["scenario_type"], run,
        contract=private["contract"], profile=public["public_profile"],
    )
    records = [row for row in run.public_trace if row["type"] in {"action", "protocol_error", "backend_error"}]
    executed = [row for row in records if row["type"] == "action"]
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
        "harness_action_records": len(executed),
        "accepted_harness_actions": sum(row["accepted"] is True for row in executed),
        "protocol_error_count": sum(row["type"] == "protocol_error" for row in records),
        "backend_error_count": sum(row["type"] == "backend_error" for row in records),
        "all_actions_accepted": bool(executed) and all(row["accepted"] is True for row in executed),
        "rejected_action_count": sum(row["accepted"] is not True for row in executed),
        "system_prompt_sha256": policy.system_prompt_sha256,
        "selected_event_types": list(policy.selected_event_types),
        "selection_matched_keywords": list(policy.selection_matched_keywords),
        "prompt_tokens": policy.prompt_tokens,
        "completion_tokens": policy.completion_tokens,
        "output_tokens": policy.output_tokens,
        "provider_total_tokens": policy.provider_total_tokens,
        "provider_total_tokens_present_calls": policy.provider_total_tokens_present_calls,
        "provider_total_tokens_mismatch_calls": policy.provider_total_tokens_mismatch_calls,
        "latency_ms": policy.latency_ms,
        "errors": dict(policy.errors),
        "trace_digest": run.trace_digest,
        "public_action_records": records,
        "model_output_records": policy.output_records,
        "canonical_conversation": deepcopy(policy.history),
    }
    result["max_consecutive_identical_rejected_action"] = v4.max_consecutive_identical_rejected_action(records)
    return result


def metrics_with_device_total(results: list[dict[str, Any]]) -> dict[str, Any]:
    compat = []
    for row in results:
        item = dict(row)
        # v4.aggregate_results requires a compatibility input named ``tokens``.
        # Feed it output tokens, then remove its ambiguous output key below.
        item["tokens"] = row["output_tokens"]
        compat.append(item)
    metrics = v4.aggregate_results(compat)
    metrics["total_device_command_count"] = sum(row["device_command_count"] for row in results)
    api_output_tokens = sum(row["completion_tokens"] for row in results)
    metrics.pop("total_tokens", None)
    metrics["api_output_tokens"] = api_output_tokens
    metrics["completion_tokens"] = api_output_tokens
    metrics["output_tokens"] = api_output_tokens
    metrics["primary_token_metric"] = "api_output_tokens"
    metrics["token_diagnostics"] = {
        "prompt_tokens": sum(row["prompt_tokens"] for row in results),
        "provider_total_tokens": sum(row["provider_total_tokens"] for row in results),
        "provider_total_tokens_present_calls": sum(row["provider_total_tokens_present_calls"] for row in results),
        "provider_total_tokens_mismatch_calls": sum(row["provider_total_tokens_mismatch_calls"] for row in results),
        "provider_total_synthesized": False,
    }
    return metrics


def validate_result_prefix(results: list[dict[str, Any]]) -> None:
    if [row.get("episode_id") for row in results] != SAMPLE_EPISODE_IDS[:len(results)]:
        raise RuntimeError("checkpoint results are not a prefix of the frozen sample")
    public_rows, _ = v5.assert_release_and_sample_frozen()
    for position, row in enumerate(results):
        if row.get("model") != DEFAULT_MODEL or row.get("query") != public_rows[position]["query"]:
            raise RuntimeError("checkpoint model/query does not match frozen Episode")
        prompt_hash, selected = expected_episode_prompt(SAMPLE_INDICES[position])
        if row.get("system_prompt_sha256") != prompt_hash or row.get("selected_event_types") != list(selected):
            raise RuntimeError("checkpoint Episode prompt/event selection mismatch")
        _validate_token_row(row)
        actions = validate_canonical_conversation(
            row.get("canonical_conversation"), expected_query=row["query"], expected_episode_id=row["episode_id"]
        )
        if hashlib.sha256(row["canonical_conversation"][0]["content"].encode()).hexdigest() != row["system_prompt_sha256"]:
            raise RuntimeError("checkpoint canonical system message does not match prompt hash")
        model_actions = [record.get("canonical_action") for record in row.get("model_output_records", []) if record.get("canonical_action") is not None]
        if actions != model_actions:
            raise RuntimeError("checkpoint model records do not match canonical conversation")
        public_actions = [record["action"] for record in row.get("public_action_records", []) if record.get("type") == "action"]
        # A strict-JSON action can still violate the Harness action grammar. In
        # that case it is the final canonical model action, followed by a
        # protocol-error observation, but is correctly absent from executed
        # public action records.
        unexecuted = len(actions) - len(public_actions)
        if public_actions != actions[:len(public_actions)] or unexecuted not in {0, 1} or (
            unexecuted == 1 and row.get("run_status") != "protocol_invalid"
        ):
            raise RuntimeError("checkpoint public actions do not match canonical conversation")
        for record in row.get("model_output_records", []):
            if "raw_content" in record or "content" in record:
                raise RuntimeError("checkpoint leaks raw model content")


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
            "completed_indices": SAMPLE_INDICES[:len(results)],
            "planned_episode_ids": SAMPLE_EPISODE_IDS,
            "sampling_semantics": "frozen v5 mechanism-diversity smoke subset: one release-order Episode from each of 15 distinct responsibilities; not an estimator",
            "scope_semantics": "notification-only responsibilities excluded; mixed responsibilities with independently evaluated physical/device outcomes remain eligible",
            "prompt_implementation": "evaluate_workflow_v4_flash_v8",
            "prompt_template_sha256": prompt_template_sha256(),
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
            "migration_manifest": deepcopy(MIGRATION_MANIFEST),
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
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_checkpoint(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("existing output is not a v8 JSON-history checkpoint")
    if checkpoint.get("execution_finished") is True:
        raise RuntimeError("refusing to overwrite an already completed v8 report")
    results = checkpoint.get("episodes")
    if not isinstance(results, list):
        raise RuntimeError("existing checkpoint has invalid episodes")
    if checkpoint != build_report(results, execution_finished=False):
        raise RuntimeError("existing checkpoint metadata or metrics mismatch")
    return results


def main(args: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ns = parser.parse_args(args)
    if ns.base_url != DEFAULT_BASE or ns.model != DEFAULT_MODEL:
        raise ValueError("v8 smoke freezes the default base URL and model")
    v5.assert_release_and_sample_frozen()
    results = load_checkpoint(ns.output)
    client = ChatClient(ns.base_url, ns.model)
    try:
        for index in SAMPLE_INDICES[len(results):]:
            # Append only after the entire Episode has completed.  A transport
            # failure therefore cannot create a zero-token pseudo-result.
            result = evaluate_one(client, index, RELEASE_DIR)
            results.append(result)
            atomic_write_report(ns.output, build_report(results, execution_finished=False))
    except APIError:
        # Persist exactly the already completed prefix (including an empty
        # prefix on a first-Episode failure) and keep it explicitly resumable.
        atomic_write_report(ns.output, build_report(results, execution_finished=False))
        raise
    report = build_report(results, execution_finished=True)
    atomic_write_report(ns.output, report)
    return report


if __name__ == "__main__":
    main()
