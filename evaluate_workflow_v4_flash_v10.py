#!/usr/bin/env python3
"""V10 compact-observation runner with v8's append-only delta history.

V10 keeps every V8/V9 semantic that matters at the API boundary: the
gateway-compatible ``<answer>JSON_OBJECT</answer>`` text envelope (never native
tools), V9's Episode-level parallelism with ``--workers 30`` default, V8's
``api_output_tokens`` primary token metric, and the no-thinking retention rule.

Two changes distinguish v10.  First, the v10 system message extends the v6
system document with an ``observation_delta_protocol`` key that teaches the
evaluated agent exactly how ``observation_delta`` works (baseline, sequential
apply, the four ops, missing-vs-null), so the delta history is explained to
the model rather than silently assumed.  Second, what the environment says
after the bootstrap turn.  The history is v8's simple append-only list, never
rebuilt: system, one initial user message carrying the original query, public
preferences and the full ``initial_observation`` obs0, then repeatedly a
canonical assistant ``<answer>JSON action</answer>`` followed by exactly one
structured user ``environment_observation`` turn holding the exact
``action_result`` plus a deterministic, lossless ``observation_delta``
relative to the immediately preceding observation.  No full observation snapshot is ever appended after
obs0, and no prior message is dynamically rebuilt or replaced.  The policy
keeps the latest full observation privately in process memory only to compute
the next delta; it is never sent or persisted again.

Model prose/thinking is never replayed and never persisted; the report keeps
the compact sent conversation, from which obs0 plus the delta chain
reconstructs every later observation exactly.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import evaluate_workflow_v4_flash_v5 as v5
import evaluate_workflow_v4_flash_v8 as v8
from evaluate_harness_v2_v4_flash import APIError, ChatClient, DEFAULT_BASE, DEFAULT_MODEL
from harness_v2.core import Harness
from harness_v2.temporal_home_evaluator import evaluate_temporal_home


ROOT = Path(__file__).resolve().parent
RELEASE_DIR = v8.RELEASE_DIR
DEFAULT_OUTPUT = ROOT / "runs/workflow_v4_flash_non_notification_15_v10/report.json"
SAMPLE_INDICES = v8.SAMPLE_INDICES
SAMPLE_EPISODE_IDS = v8.SAMPLE_EPISODE_IDS
SCHEMA_VERSION = "temporal-home-v4-flash-notification-only-filtered-smoke15-v10-append-only-delta-history"
DEFAULT_WORKERS = 30

MIGRATION_MANIFEST = {
    "frozen_sample": "v5 exact 15 Episode indices and IDs",
    "action_transport": "v6 exactly-one <answer>JSON_OBJECT</answer> text envelope",
    "conversation": "v8 append-only history, never rebuilt: system, user(obs0), then assistant action and user environment turn per step",
    "environment_turns": "exact action_result plus observation_delta relative to the immediately preceding observation",
    "full_snapshots": "initial_observation only in the initial user message; never appended, sent, or persisted again",
    "latest_full_observation": "process memory only, used to compute the next delta",
    "native_tools": "not sent and not required",
    "previous_action_fields": "removed; represented once in chronological history",
    "system_prompt_extension": "v10 system message is the v6 system document plus an observation_delta_protocol key carrying the exact observation-delta instructions, so the evaluated agent is taught the delta semantics",
    "thinking": "not retained and not replayed",
    "raw_model_content": "sha256 and UTF-8 byte length only",
    "token_metric": "api_output_tokens (exactly the provider completion_tokens/output_tokens)",
    "checkpoint": "arbitrary completed subset, strict v10 delta-chain validation, atomic replace, completed-report refusal",
}

DELTA_OPS = {
    "none": "prior and next snapshots are canonically identical",
    "replace": "scalar/type/list-shape change; carries exact before and after values",
    "object": "recursive dict change with added values, removed key list, and changed sub-deltas",
    "array": "same-length list change with per-index sub-deltas",
}

OBSERVATION_DELTA_PROTOCOL_KEY = "observation_delta_protocol"

OBSERVATION_DELTA_INSTRUCTIONS = (
    "The initial user message contains initial_observation: that is the full "
    "baseline observation for the whole Episode.",
    "Every later environment_observation user message contains the exact "
    "action_result plus an observation_delta relative to the immediately "
    "preceding reconstructed observation, never a full snapshot.",
    "Apply the observation deltas sequentially, starting from "
    "initial_observation, to reconstruct the current full observation before "
    "deciding.",
    "Delta ops: op=none means no change; op=replace carries before and after, "
    "use the after value; op=object removes the keys listed in removed, adds "
    "the values in added, and recursively applies changed; op=array "
    "recursively applies the changed entries keyed by decimal index.",
    "Never treat omitted unchanged fields as absent: a key missing from a "
    "delta is unchanged, and a missing key differs from an explicit null, "
    "which is a value.",
)


def build_v10_system_content(
    query: str,
    device_interfaces: list[dict[str, Any]],
    selected_event_types: tuple[str, ...],
) -> str:
    """V6 system document extended with the v10 observation-delta protocol.

    Deterministic for identical inputs: the v6 document is extended with the
    fixed ``observation_delta_protocol`` key and re-serialized with v6's exact
    canonical settings, so the episode-static system prefix stays
    byte-identical across calls within an Episode.
    """

    document = json.loads(v8.v6.build_system_content(query, device_interfaces, selected_event_types))
    document[OBSERVATION_DELTA_PROTOCOL_KEY] = {
        "instructions": list(OBSERVATION_DELTA_INSTRUCTIONS),
    }
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _canonically_equal(left: Any, right: Any) -> bool:
    """JSON-semantic equality: 1 vs true and 0.0 vs 0-style traps included."""

    return canonical_json(left) == canonical_json(right)


def diff_observation(old: Any, new: Any) -> dict[str, Any] | None:
    """Canonical generic recursive diff with added/removed/changed semantics.

    Returns ``None`` when the values are canonically identical.  Dicts recurse
    over every key (known or unknown future keys alike); same-length lists
    recurse per index; everything else — scalars, type changes, list length
    changes, None-vs-value distinctions — becomes an exact ``replace`` node
    carrying both ``before`` and ``after`` so apply can verify strictly.
    Missing keys and explicit ``null`` are never conflated: absence is encoded
    by ``removed``/``added``, while ``None`` values flow through as values.
    """

    if _canonically_equal(old, new):
        return None
    if isinstance(old, dict) and isinstance(new, dict):
        added = {key: deepcopy(new[key]) for key in sorted(set(new) - set(old))}
        removed = sorted(set(old) - set(new))
        changed: dict[str, Any] = {}
        for key in sorted(set(old) & set(new)):
            sub = diff_observation(old[key], new[key])
            if sub is not None:
                changed[key] = sub
        node: dict[str, Any] = {"op": "object"}
        if added:
            node["added"] = added
        if removed:
            node["removed"] = removed
        if changed:
            node["changed"] = changed
        return node
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        changed = {}
        for index, (old_item, new_item) in enumerate(zip(old, new)):
            sub = diff_observation(old_item, new_item)
            if sub is not None:
                changed[str(index)] = sub
        return {"op": "array", "changed": changed} if changed else None
    return {"op": "replace", "before": deepcopy(old), "after": deepcopy(new)}


def observation_delta(old: Any, new: Any) -> dict[str, Any]:
    """Total delta: identical snapshots get the explicit ``none`` op."""

    return diff_observation(old, new) or {"op": "none"}


def apply_observation_delta(before: Any, delta: Any) -> Any:
    """Strictly apply a v10 delta to ``before`` and reconstruct the next snapshot.

    Every expectation is checked: replace nodes must match their recorded
    ``before``; object removals must exist, additions must not, and recursion
    keys must exist; array indices must be in range.  Any mismatch raises so a
    tampered delta can never silently pass validation.
    """

    if not isinstance(delta, dict) or "op" not in delta:
        raise RuntimeError("observation delta must be an op-tagged object")
    op = delta["op"]
    if op == "none":
        if set(delta) != {"op"}:
            raise RuntimeError("none delta must be empty")
        return deepcopy(before)
    if op == "replace":
        if set(delta) != {"op", "before", "after"}:
            raise RuntimeError("replace delta fields invalid")
        if not _canonically_equal(before, delta["before"]):
            raise RuntimeError("replace delta before-value mismatch")
        return deepcopy(delta["after"])
    if op == "object":
        if not set(delta) <= {"op", "added", "removed", "changed"} or not set(delta) - {"op"}:
            raise RuntimeError("object delta fields invalid")
        if not isinstance(before, dict):
            raise RuntimeError("object delta requires a dict snapshot")
        removed = delta.get("removed", [])
        added = delta.get("added", {})
        changed = delta.get("changed", {})
        if not isinstance(removed, list) or not isinstance(added, dict) or not isinstance(changed, dict):
            raise RuntimeError("object delta collections invalid")
        if len(set(removed)) != len(removed):
            raise RuntimeError("object delta removes a key twice")
        overlap = set(removed) & (set(added) | set(changed))
        if overlap:
            raise RuntimeError("object delta adds or changes a removed key")
        result = deepcopy(before)
        for key in removed:
            if key not in result:
                raise RuntimeError("object delta removes a missing key")
            del result[key]
        for key, value in added.items():
            if key in result:
                raise RuntimeError("object delta adds an existing key")
            result[key] = deepcopy(value)
        for key, sub in changed.items():
            if key not in result:
                raise RuntimeError("object delta changes a missing key")
            result[key] = apply_observation_delta(result[key], sub)
        return result
    if op == "array":
        if set(delta) != {"op", "changed"} or not isinstance(delta["changed"], dict):
            raise RuntimeError("array delta fields invalid")
        if not isinstance(before, list):
            raise RuntimeError("array delta requires a list snapshot")
        result = deepcopy(before)
        for index, sub in delta["changed"].items():
            if not isinstance(index, str) or not index.isdigit():
                raise RuntimeError("array delta index invalid")
            position = int(index)
            if position >= len(result):
                raise RuntimeError("array delta index out of range")
            result[position] = apply_observation_delta(result[position], sub)
        return result
    raise RuntimeError("unknown observation delta op")


def build_environment_delta_message(feedback: Any, delta: dict[str, Any]) -> dict[str, str]:
    """Structured user-role environment turn appended after every action.

    Replaces v8's full ``current_observation`` snapshot with the exact
    ``action_result`` plus the ``observation_delta`` from the immediately
    preceding observation; the full snapshot is never re-sent.
    """

    payload = {
        "message_type": "environment_observation",
        "action_result": feedback,
        "observation_delta": delta,
    }
    return {"role": "user", "content": canonical_json(payload)}


class CompactDeltaObservationPolicy:
    """V8 append-only envelope policy whose environment turns carry deltas."""

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
        self._last_observation: dict[str, Any] | None = None

    def _initialize(self, view: dict[str, Any]) -> None:
        selected, matched = v8.v4.select_event_types_for_query(view["query"])
        interfaces = v8.v4.device_interfaces_from_observation(view["observation"])
        system = build_v10_system_content(view["query"], interfaces, selected)
        self.system_content = system
        self.system_prompt_sha256 = v8.v6.system_prompt_sha256(system)
        self.selected_event_types = selected
        self.selection_matched_keywords = matched
        self.history = [{"role": "system", "content": system}, v8.build_initial_user_message(view)]
        self._last_observation = deepcopy(view["observation"])
        if view.get("last_feedback") is not None:
            raise RuntimeError("initial decision unexpectedly has prior feedback")

    def _verify_static_prefix(self, view: dict[str, Any]) -> None:
        selected, _ = v8.v4.select_event_types_for_query(view["query"])
        candidate = build_v10_system_content(
            view["query"], v8.v4.device_interfaces_from_observation(view["observation"]), selected
        )
        if candidate != self.system_content:
            raise RuntimeError("Episode system prefix must be byte-identical across calls")

    def _append_environment_turn(self, feedback: Any, observation: dict[str, Any]) -> None:
        if self._last_observation is None:
            raise RuntimeError("cannot compute an observation delta before initialization")
        self.history.append(
            build_environment_delta_message(feedback, observation_delta(self._last_observation, observation))
        )
        self._last_observation = deepcopy(observation)

    def build_request(self) -> list[dict[str, str]]:
        """Return the accumulated append-only history; never rebuilt."""

        if not self.history:
            raise RuntimeError("cannot build a request before initialization")
        return deepcopy(self.history)

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        if self.system_content is None:
            self._initialize(view)
        else:
            self._verify_static_prefix(view)
            if not self.pending_action or not self.history or self.history[-1].get("role") != "assistant":
                raise RuntimeError("missing prior canonical assistant action")
            self._append_environment_turn(view.get("last_feedback"), view["observation"])
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
            usage = v8.v6.usage_token_record(response.get("usage"))
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
            byte_length, digest = v8.response_fingerprint(content)
            record["raw_response_byte_length"] = byte_length
            record["raw_response_sha256"] = digest
            try:
                action = v8.v6.parse_answer_envelope(content)
            except (TypeError, ValueError) as exc:
                error = f"{type(exc).__name__}:{exc}"
                record["protocol_error"] = error
                self.errors[error] += 1
                return {"kind": "invalid_model_output"}
            record["canonical_action"] = deepcopy(action)
            self.history.append(v8.canonical_assistant_action(action))
            self.pending_action = True
            self.parsed_json_actions += 1
            return action
        finally:
            self.output_records.append(record)

    def finalize(self, public_trace: tuple[dict[str, Any], ...]) -> None:
        """Append the final action result plus its observation delta."""

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
        self._append_environment_turn(feedback, deepcopy(observations[-1]))
        self.pending_action = False


def prompt_template_sha256() -> str:
    payload = {
        "v6_prompt_template_sha256": v8.v6.prompt_template_sha256(),
        "conversation_protocol": MIGRATION_MANIFEST,
        "initial_message_fields": ["message_type", "original_query", "public_preferences", "initial_observation"],
        "environment_message_fields": ["message_type", "action_result", "observation_delta"],
        "delta_codec": DELTA_OPS,
        "system_extension_key": OBSERVATION_DELTA_PROTOCOL_KEY,
        "system_observation_delta_instructions": list(OBSERVATION_DELTA_INSTRUCTIONS),
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def expected_episode_prompt(index: int) -> tuple[str, tuple[str, ...]]:
    """Expected v10 system hash and event selection for one frozen Episode."""

    public, _, spec, backend = v8.v4.load_temporal_episode(index, RELEASE_DIR)
    observation = backend.reset(spec).public_observation
    selected, _ = v8.v4.select_event_types_for_query(public["query"])
    content = build_v10_system_content(
        public["query"], v8.v4.device_interfaces_from_observation(observation), selected
    )
    return v8.v6.system_prompt_sha256(content), selected


def parse_environment_payload(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict) or message.get("role") != "user" or set(message) != {"role", "content"}:
        raise RuntimeError("checkpoint canonical environment message invalid")
    try:
        payload = json.loads(message["content"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("checkpoint canonical environment message invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("checkpoint canonical environment message invalid")
    return payload


def validate_canonical_conversation(
    conversation: Any, *, expected_query: str | None = None, expected_episode_id: str | None = None
) -> list[dict[str, Any]]:
    """Validate v8's append-only shape with v10 delta-only environment turns.

    Besides message-level serialization checks, the delta chain is replayed
    from the initial observation so every environment turn must be exactly
    applicable to the immediately preceding observation.
    """

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
    if initial["message_type"] != "initial_request" or conversation[1] != v8.build_initial_user_message({
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
    prior = initial["initial_observation"]
    for offset in range(0, len(suffix), 2):
        assistant, environment = suffix[offset:offset + 2]
        if not isinstance(assistant, dict) or assistant.get("role") != "assistant" or set(assistant) != {"role", "content"}:
            raise RuntimeError("checkpoint canonical assistant action invalid")
        try:
            action = v8.v6.parse_answer_envelope(assistant["content"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("checkpoint canonical assistant action invalid") from exc
        if assistant != v8.canonical_assistant_action(action):
            raise RuntimeError("checkpoint canonical assistant action serialization invalid")
        payload = parse_environment_payload(environment)
        if "current_observation" in payload:
            raise RuntimeError("checkpoint environment turn must not carry a full observation snapshot")
        if set(payload) != {"message_type", "action_result", "observation_delta"} or payload["message_type"] != "environment_observation":
            raise RuntimeError("checkpoint canonical environment fields invalid")
        if not isinstance(payload["observation_delta"], dict):
            raise RuntimeError("checkpoint canonical environment fields invalid")
        if environment != build_environment_delta_message(payload["action_result"], payload["observation_delta"]):
            raise RuntimeError("checkpoint canonical environment serialization invalid")
        try:
            prior = apply_observation_delta(prior, payload["observation_delta"])
        except RuntimeError as exc:
            raise RuntimeError(f"checkpoint observation delta chain does not apply: {exc}") from exc
        actions.append(action)
    return actions


def _validate_token_row(row: dict[str, Any]) -> None:
    v8._validate_token_row(row)


def evaluate_one(client: Any, index: int, release_dir: Path = RELEASE_DIR) -> dict[str, Any]:
    public, private, spec, backend = v8.v4.load_temporal_episode(index, release_dir)
    policy = CompactDeltaObservationPolicy(client)
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
    result["max_consecutive_identical_rejected_action"] = v8.v4.max_consecutive_identical_rejected_action(records)
    return result


def metrics_with_device_total(results: list[dict[str, Any]]) -> dict[str, Any]:
    return v8.metrics_with_device_total(results)


def _validate_episode_row(row: dict[str, Any], *, position: int, public_rows: list[dict[str, Any]]) -> None:
    episode_id = SAMPLE_EPISODE_IDS[position]
    index = SAMPLE_INDICES[position]
    if row.get("episode_id") != episode_id:
        raise RuntimeError("checkpoint Episode identity mismatch")
    if row.get("model") != DEFAULT_MODEL or row.get("query") != public_rows[position]["query"]:
        raise RuntimeError("checkpoint model/query does not match frozen Episode")
    prompt_hash, selected = expected_episode_prompt(index)
    if row.get("system_prompt_sha256") != prompt_hash or row.get("selected_event_types") != list(selected):
        raise RuntimeError("checkpoint Episode prompt/event selection mismatch")
    _validate_token_row(row)
    conversation = row.get("canonical_conversation")
    actions = validate_canonical_conversation(
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
        "prompt_implementation": "evaluate_workflow_v4_flash_v10",
        "prompt_template_sha256": prompt_template_sha256(),
        "observable_event_catalog_sha256": v5.V4_EVENT_CATALOG_SHA256,
        "system_prefix_policy": "episode_static_byte_identical_across_calls",
        "conversation_policy": "v8 append-only history, never rebuilt: system, user(initial full observation), then one canonical assistant action plus one environment_observation delta turn per step",
        "response_envelope_protocol": {
            "format": "<answer>JSON_OBJECT</answer>", "required": True,
            "strict_json": ["duplicate_keys_rejected", "nonfinite_constants_rejected"],
            "outside_reasoning": "ignored_not_replayed_not_persisted",
        },
        "native_tools": "not_used",
        "observation_protocol": {
            "initial_observation": "full, in the initial user message only; never repeated afterwards",
            "environment_turns": "exact action_result plus observation_delta relative to the immediately preceding observation",
            "full_snapshot_policy": "no current_observation or full snapshot is ever appended, sent, or persisted after the initial user message",
            "delta_codec": DELTA_OPS,
            "delta_apply": "apply_observation_delta; the initial observation plus the delta chain reconstructs every later observation exactly",
            "missing_vs_null": "absence is added/removed; null is a value; never conflated",
            "latest_full_observation": "policy process memory only, used to compute the next delta; not sent and not persisted",
            "system_instructions": "the v10 system message is the v6 system document plus an observation_delta_protocol key carrying the exact observation-delta instructions taught to the evaluated agent",
        },
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
    config["checkpoint"] = "arbitrary_completed_subset_strictly_validated_with_v10_delta_chain_and_canonically_ordered"
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_finished": execution_finished,
        "experiment_config": config,
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
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def load_checkpoint(path: Path, *, requested_workers: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("existing output is not a v10 append-only delta-history checkpoint")
    if checkpoint.get("execution_finished") is True:
        raise RuntimeError("refusing to overwrite an already completed v10 report")
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
    return evaluate_one(client, index, RELEASE_DIR)


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
        raise ValueError("v10 smoke freezes the default base URL and model")
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
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="v10-episode") as executor:
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
