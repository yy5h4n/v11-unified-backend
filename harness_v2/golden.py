"""Construct one complete Harness V2 golden bundle for end-to-end conformance tests."""

from __future__ import annotations

import base64
import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any

import rfc8785

from .golden_backend import apply_immediate_command, resulting_temperature_c
from .trust_evidence import seal_manifest


START = "2026-01-01T17:00:00+00:00"
END = "2026-01-01T19:00:00+00:00"


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(label: str) -> str:
    return _sha_bytes(label.encode())


def canonical_payload(value: Any) -> dict[str, Any]:
    raw = rfc8785.dumps(value)
    return {
        "media_type": "application/json;profile=rfc8785",
        "canonical_bytes_base64": base64.b64encode(raw).decode(),
        "sha256": _sha_bytes(raw),
    }


def serialization(value: Any) -> dict[str, Any]:
    raw = rfc8785.dumps(value)
    return {
        "algorithm": "RFC8785_JCS_UTF8",
        "canonical_utf8_base64": base64.b64encode(raw).decode(),
        "digest": _sha_bytes(raw),
    }


def state(label: str) -> dict[str, str]:
    return {name: digest(f"{label}:{name}") for name in ("backend", "rules", "subscriptions", "wake_queue", "accounting")}


def _control_digests() -> dict[str, str]:
    view = agent_view("")
    config = episode_config()
    initial = {key: value for key, value in view.items() if key != "query"}
    horizon = {"clocks": config["clocks"], "horizon": view["horizon"]}
    return {
        "initial_public_state": _sha_bytes(rfc8785.dumps(initial)),
        "inventory": _sha_bytes(rfc8785.dumps(view["inventory"])),
        "costs": _sha_bytes(rfc8785.dumps(view["costs_budgets"])),
        "budgets": _sha_bytes(rfc8785.dumps(config["budgets"])),
        "horizon": _sha_bytes(rfc8785.dumps(horizon)),
    }


def episode_config() -> dict[str, Any]:
    return {
        "protocol_version": "interaction_state_machine_v2",
        "episode_id": "episode.golden",
        "track": "explicit_profile_control",
        "clocks": {"physics_tick_seconds": 60, "episode_timezone": "UTC", "episode_start": START, "episode_termination": END},
        "budgets": {"agent_callbacks": 4, "inspect_calls": 4, "mutation_transactions": 2, "clarification_questions": 0, "minimum_callback_interval_seconds": 60},
        "callback_policy": {
            "allowed_reasons": ["episode_reset", "subscribed_public_event", "requested_wake_time", "subscribed_workflow_result", "user_reply", "episode_termination"],
            "coalesce_same_timestamp": True,
            "delivery_order": ["episode_termination", "subscribed_workflow_result", "subscribed_public_event", "requested_wake_time", "user_reply"],
            "workflow_requires_subscription": True,
            "at_most_once_per_event_id": True,
        },
        "clarification_policy": {
            "enabled": False,
            "max_slots_per_question": 1,
            "allowed_slots": ["beneficiary", "spatial_scope", "activation_condition", "suspension_condition", "deadline", "release_condition", "priority", "authorized_action", "numeric_preference"],
            "duplicate_slot_question": "reject_and_consume_attempt",
            "invalid_question": "reject_and_consume_attempt",
            "answer_source": "pre_existing_grounded_record_or_unknown",
            "answer_delivery": "single_coalesced_callback_after_question_turn",
        },
        "termination_policy": {
            "callback_budget_exhaustion": "suppress_further_nontermination_callbacks",
            "installed_rules_after_callback_exhaustion": "continue_until_termination",
            "pending_events_at_horizon": "termination_wins_and_pending_results_are_trace_only",
            "notification_is_read_only": True,
            "trace_seal_is_one_way": True,
        },
    }


def agent_view(query: str) -> dict[str, Any]:
    return {
        "query": {"text": query, "language": "en"},
        "track": "explicit_profile_control",
        "profile": {"timezone": "UTC", "preferences": [{"name": "thermal.preference", "value": 22, "provenance": "user_stated", "evidence_id": "evidence.thermal"}]},
        "rooms": [{"room_id": "kitchen", "device_ids": ["hvac.kitchen"], "availability": "available"}],
        "virtual_clock": {"now": START, "timezone": "UTC"},
        "horizon": {"start": START, "termination_disclosure": "exact", "termination": END},
        "inventory": {"complete": True, "devices": [{"device_id": "hvac.kitchen", "device_type": "hvac", "capabilities": ["thermal.control"], "availability": "available", "state": {"mode": "off"}}]},
        "capability_catalog": [{"capability_id": "thermal.control", "operations": [{"name": "set", "parameters": [{"name": "target_c", "type": "number", "required": True, "unit": "C", "minimum": 16, "maximum": 30}]}]}],
        "observation_catalog": [{"field": "rooms.kitchen.temperature_c", "value_type": "number", "unit": "C"}],
        "observations": [{"name": "rooms.kitchen.temperature_c", "value": 18, "unit": "C", "quality": "fresh", "observed_at": START}],
        "events": [],
        "event_catalog": {"event_types": ["period.enter"], "filters": [{"event_type": "period.enter", "fields": [{"name": "period", "value_type": "string", "required": True, "unit": None}]}]},
        "named_periods": [{"name": "evening", "start": START, "end": END}],
        "costs_budgets": {
            "currency": "benchmark_unit",
            "budgets": {"energy": 100},
            "periods": [{"name": "episode", "start": START, "end": END}],
            "action_costs": [{"capability": "thermal.control", "operation": "set", "cost": 1, "unit": "benchmark_unit"}],
        },
        "tool_schema": {"allowed_actions": ["inspect", "act_now", "create_rule", "cancel_rule", "subscribe", "wake_at"], "interaction_budgets": {"max_callbacks": 4, "max_inspect_calls": 4, "max_mutation_transactions": 2, "max_questions": 0, "minimum_callback_interval_seconds": 60}},
        "safety_limits": [{"name": "temperature.max", "value": 30, "unit": "C", "provenance": "system_configuration"}],
    }


def bootstrap_audit() -> dict[str, Any]:
    return {"evidence_records": [{"evidence_id": "evidence.thermal", "source_hash": digest("source"), "visibility_justification": "resident explicitly stated this preference", "source_class": "user_stated"}]}


def _transaction(run_id: str, before: dict[str, str], target_c: float) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    public_token = f"r{digest(run_id)[:16]}"
    command = {"command_id": f"command.{public_token}", "device_id": "hvac.kitchen", "capability": "thermal.control", "operation": "set", "parameters": {"target_c": target_c}}
    request = {"transaction_id": f"transaction.{public_token}", "pre_turn_backend_state_digest": before["backend"], "act_now": {"commands": [command]}, "create_rules": [], "cancel_rules": [], "subscriptions": [], "wake_requests": []}
    after = dict(before)
    after["backend"] = apply_immediate_command(before["backend"], command)
    after["accounting"] = digest(f"{run_id}:accounting:committed")
    zero_ledger = {"callbacks_delivered": 1, "inspect_calls_used": 0, "mutation_transactions_attempted": 0, "clarification_questions_attempted": 0, "protocol_errors": 0}
    post_ledger = {**zero_ledger, "mutation_transactions_attempted": 1}
    before["accounting"] = _sha_bytes(rfc8785.dumps(zero_ledger))
    after["accounting"] = _sha_bytes(rfc8785.dumps(post_ledger))
    exchange = {
        "request": request,
        "pre_state": {"backend_state_digest": before["backend"], "accounting_ledger": zero_ledger},
        "strict_outcome": {"transaction_id": request["transaction_id"], "status": "committed", "pre_backend_state_digest": before["backend"], "post_backend_state_digest": after["backend"], "ledger_delta": {"mutation_transactions_attempted": 1, "protocol_errors": 0}, "mutation_outcomes": [{"mutation_kind": "command", "mutation_id": command["command_id"], "status": "committed", "error_code": None}], "transaction_error_code": None},
        "post_state": {"backend_state_digest": after["backend"], "accounting_ledger": post_ledger},
        "verifier_record": {"verifier_id": "trusted.transaction.v1", "verifier_version": "1", "verifier_hash": digest("transaction-verifier"), "verified": True, "checks": [{"check_id": "request_outcome_state_chain", "passed": True}]},
    }
    return request, exchange, after


def sealed_session(run_id: str, role: str, query: str, target_c: float | None) -> dict[str, Any]:
    config = episode_config()
    before = state(f"{run_id}:before")
    public_run_id = f"r{digest(run_id)[:16]}"
    reset = {"callback_id": f"callback.{public_run_id}", "reason": "episode_reset", "timestamp": START}
    if target_c is None:
        choice = {"kind": "yield_without_mutation"}
        outcome: dict[str, Any] = {"kind": "yielded"}
        after = before
        outcome_kind = "yield"
    else:
        request, outcome, after = _transaction(run_id, before, target_c)
        choice = {"kind": "commit_transaction", "transaction": request}
        outcome_kind = "tool_outcome"
    turn = {
        "turn_id": f"turn.{public_run_id}", "track": "explicit_profile_control",
        "callback": canonical_payload(reset), "pre_state": before, "inspects": [],
        "terminal_choice": canonical_payload(choice), "terminal_outcome": outcome, "post_state": after,
    }
    record_specs = [
        ("bootstrap_delivered", "harness", agent_view(query)),
        ("callback_delivered", "harness", reset),
        ("terminal_choice", "agent", choice),
        (outcome_kind, "harness", outcome),
        ("termination_notice", "harness", {"reason": "episode_termination", "timestamp": END}),
    ]
    records = []
    for i, (kind, actor, payload) in enumerate(record_specs):
        pre = before if i <= 3 else after
        post = after if i >= 3 else before
        records.append({"sequence": i, "timestamp": START if i < 4 else END, "kind": kind, "actor": actor, "payload": canonical_payload(payload), "pre_state": pre, "post_state": post})
    return {
        "session_id": f"session.{run_id}", "agent_build_digest": digest(f"build:{role}"), "policy_config_digest": digest(f"config:{role}"),
        "episode_configuration": config, "episode_configuration_digest": _sha_bytes(rfc8785.dumps(config)),
        "bootstrap": canonical_payload(agent_view(query)), "bootstrap_audit": bootstrap_audit(),
        "turns": [turn], "records": records, "sealed": True,
        "transcript_digest": _sha_bytes(rfc8785.dumps(records)),
    }


def manifest() -> dict[str, Any]:
    body = {
        "manifest_id": "manifest.thermal.v2", "manifest_version": "2", "family_id": "thermal", "implementation_hash": digest("evaluator"),
        "loss_components": [{
            "component_id": "temperature_error",
            "inputs": [{"json_pointer": "/public_state/temperature_c", "value_type": "number", "unit": "C", "allowed_qualities": ["fresh"]}],
            "loss_operator": {"name": "absolute_error", "parameters": {"target": "22"}},
            "active_selector": {"mode": "all_intervals", "binding": None},
            "output_unit": "C", "integration": "left_endpoint", "interval": "episode_start_inclusive_termination_exclusive_except_terminal_value",
            "missing_rule": {"missing_input": "fail_episode", "stale_input": "treat_as_missing", "max_staleness_seconds": 300, "maximum_missing_fraction": 0},
            "normalizer": {"method": "identity", "source": "preregistered_constant", "source_version": "1", "value": "1", "zero_value_rule": "manifest_invalid"},
            "aggregation": "mean_over_active_mask", "weight": "1",
        }],
        "safety_thresholds": [{"threshold_id": "temperature_max", "input_path": "/public_state/temperature_c", "comparator": "lte", "value": "30", "unit": "C"}],
        "opportunity_gap_threshold": "1", "main_gain_threshold": "0.5", "policy_separation_threshold": "0.1",
        "oracle": {"implementation_id": "oracle.thermal", "implementation_version": "1", "implementation_hash": digest("oracle"), "solver": "enumeration", "solver_version": "1", "optimality_tolerance": "0", "numeric_tolerance": "0", "information_parity": "same_online_public_information_and_no_future_exogenous_information", "api_parity": "same_action_rule_subscription_and_wake_api", "budget_parity": "same_callback_interaction_and_action_budgets"},
        "numeric_semantics": {"decimal_library": "decimal", "library_version": "3.12", "precision_digits": 28, "rounding": "ROUND_HALF_EVEN"},
        "canonical_serialization": {"algorithm": "RFC8785_JCS_UTF8", "nonfinite_rule": "reject_before_serialization", "decimal_rule": "canonical_decimal_no_exponent_no_trailing_zero_no_negative_zero", "digest": "sha256"},
    }
    return seal_manifest(body)


def trace(run_id: str, arm_id: str, query_digest: str, target_c: float | None, exchanges: list[dict[str, Any]]) -> dict[str, Any]:
    temperature = resulting_temperature_c(target_c)
    public_token = f"r{digest(run_id)[:16]}"
    applied = [] if target_c is None else [{"command_id": f"command.{public_token}", "source": "agent_immediate", "origin": {"kind": "agent_immediate", "transaction_id": f"transaction.{public_token}"}, "device_id": "hvac.kitchen", "capability": "thermal.control", "operation": "set", "parameters": {"target_c": target_c}, "requested_at": START, "applied_at": START, "status": "committed", "error_code": None, "action_cost": 1, "cost_unit": "benchmark_unit"}]
    frame = {
        "frame_index": 0, "timestamp": START, "duration_to_next_seconds": 7200,
        "exogenous_state": {},
        "public_state": {"temperature_c": {"value": temperature, "unit": "C", "quality": "fresh", "observed_at": START}},
        "private_evaluator_primitives": {}, "device_workflow_state": {},
        "applied_commands": applied, "rule_events": [], "protocol_events": [], "safety_events": [],
    }
    body = {
        "trace_schema_version": "sealed_trace_v2", "episode_id": "episode.golden", "scenario_group_id": "group.golden", "arm_id": arm_id,
        "query_digest": query_digest, "manifest_id": "manifest.thermal.v2", "manifest_version": "2",
        "start_inclusive": START, "termination_exclusive": END, "seed_digest": digest("seed"), "exogenous_realization_digest": digest("exogenous"),
        "frames": [frame], "transaction_exchanges": exchanges, "sealed": True,
    }
    sealed = serialization(body)
    return {**body, "trace_digest": sealed["digest"], "trace_serialization": sealed}


AXES = ("beneficiary", "spatial_scope", "activation_condition", "suspension_condition", "persistence", "deadline", "release_condition", "priority", "authorized_action")


def contract(arm_id: str) -> dict[str, Any]:
    values = {axis: ("kitchen" if axis == "spatial_scope" and arm_id == "A" else "whole_home" if axis == "spatial_scope" else axis) for axis in AXES}
    axes = {axis: {"canonical_utf8_base64": serialization(value)["canonical_utf8_base64"], "digest": serialization(value)["digest"]} for axis, value in values.items()}
    body = {"contract_version": "2", "responsibility_id": f"thermal.{arm_id}", "axes": axes}
    return {**body, "contract_digest": _sha_bytes(rfc8785.dumps(body)), "serialization": serialization(body)}


def scenario_group(query_a: str, query_b: str, paraphrase_a: str, paraphrase_b: str) -> dict[str, Any]:
    qa, qb, qpa, qpb = digest(query_a), digest(query_b), digest(paraphrase_a), digest(paraphrase_b)
    ma, mb = manifest(), deepcopy(manifest())
    axis_a, axis_b = contract("A")["axes"]["spatial_scope"], contract("B")["axes"]["spatial_scope"]
    return {
        "group_id": "group.golden", "track": "explicit_profile_control", "split": "validation", "one_factor_pair": True,
        "contrast_axis": "spatial_scope", "contrast_witness": {"arm_a_axis_value_digest": axis_a["digest"], "arm_b_axis_value_digest": axis_b["digest"], "values_differ": True},
        "initial_public_state_digest": _control_digests()["initial_public_state"], "inventory_digest": _control_digests()["inventory"], "simulator_seed_digest": digest("seed"), "exogenous_realization_digest": digest("exogenous"),
        "costs_digest": _control_digests()["costs"], "budgets_digest": _control_digests()["budgets"], "horizon_policy_digest": _control_digests()["horizon"],
        "arms": [{"arm_id": "A", "canonical_contract": contract("A"), "evaluator_manifest": ma}, {"arm_id": "B", "canonical_contract": contract("B"), "evaluator_manifest": mb}],
        "query_registry": {qa: {"arm_id": "A", "role": "source_near"}, qb: {"arm_id": "B", "role": "source_near"}, qpa: {"arm_id":"A","role":"faithful_paraphrase"}, qpb: {"arm_id":"B","role":"faithful_paraphrase"}},
        "query_shuffle_protocol": "rerun_policy_closed_loop_with_same_seed_and_exogenous_realization_never_reuse_backend_or_agent_trace",
        "group_aggregation": "mean_bidirectional_cross_application_regret",
    }


def controls() -> dict[str, Any]:
    frozen = _control_digests()
    return {**frozen, "simulator_seed": digest("seed"), "exogenous_realization": digest("exogenous"), "track": "explicit_profile_control"}


def receipt(run_id: str, role: str, arm_id: str, query: str, target_c: float | None) -> dict[str, Any]:
    qd = digest(query)
    policy_identity = "agent" if role == "diagnostic" else role
    session = sealed_session(run_id, policy_identity, query, target_c)
    exchanges = [turn["terminal_outcome"] for turn in session["turns"] if turn["terminal_choice"] and decode_payload_kind(turn["terminal_choice"]) == "commit_transaction"]
    primary = trace(run_id, arm_id, qd, target_c, exchanges)
    loss = str(abs(22 - primary["frames"][0]["public_state"]["temperature_c"]["value"])).rstrip("0").rstrip(".")
    replay = deepcopy(primary)
    replay_receipt = {"verifier_id": "trusted.replay.v1", "verifier_version": "1", "verifier_hash": _sha_bytes((Path(__file__).with_name("conformance.py")).read_bytes()), "backend_build_digest": _sha_bytes((Path(__file__).with_name("golden_backend.py")).read_bytes()), "adapter_build_digest": _sha_bytes(Path(__file__).read_bytes()), "initial_public_state_digest": controls()["initial_public_state"], "session_transcript_digest": session["transcript_digest"], "primary_trace_digest": primary["trace_digest"], "replay_trace_digest": replay["trace_digest"], "deterministic": True, "replay_trace": replay}
    return {"run_id": run_id, "role": role, "policy_build_digest": digest(f"build:{policy_identity}"), "policy_config_digest": digest(f"config:{policy_identity}"), "controls": controls(), "sealed_session": session, "sealed_trace": primary, "replay_receipt": replay_receipt, "loss": loss, "status": "eligible"}


def decode_payload_kind(payload: dict[str, Any]) -> str:
    import json
    return json.loads(base64.b64decode(payload["canonical_bytes_base64"]))["kind"]


def golden_bundle() -> dict[str, Any]:
    query_a = "Keep the kitchen comfortable this evening."
    query_b = "Keep the whole home comfortable this evening."
    paraphrase_a = "Could you make sure the kitchen stays comfortable tonight?"
    paraphrase_b = "Please make sure every room stays comfortable tonight."
    agent = receipt("main.agent", "agent", "A", query_a, 20)
    noop = receipt("main.noop", "noop", "A", query_a, None)
    oracle = receipt("main.oracle", "public_information_oracle", "A", query_a, 23)
    score_body = {"protocol_status": "valid", "safety_status": "safe", "oracle_status": "ok", "agent_loss": "3", "noop_loss": "5", "oracle_loss": "1", "denominator": "4", "gain_status": "eligible", "raw_gain": "0.5", "raw_normalized_regret": "0.5", "display_clipped_gain": "0.5", "main_gain_pass": True, "component_metrics": {"temperature_error": "3"}}
    score = {**score_body, "serialization": serialization(score_body)}
    specs = [
        ("A", "source_near", query_a, "own_arm"), ("B", "source_near", query_b, "own_arm"),
        ("A", "faithful_paraphrase", paraphrase_a, "own_arm"), ("B", "faithful_paraphrase", paraphrase_b, "own_arm"),
        ("A", "deletion", "", "deleted"), ("B", "deletion", "", "deleted"),
        ("A", "cross_arm_shuffle", query_b, "arm_B_query_on_A"), ("B", "cross_arm_shuffle", query_a, "arm_A_query_on_B"),
    ]
    diagnostic_targets = [22, 22, 22, 22, None, None, 18, 18]
    diagnostics = []
    for i, (arm, role, query, assignment) in enumerate(specs):
        run_id = f"diagnostic.{i}"
        diagnostics.append({"arm_id": arm, "query_role": role, "query_digest": digest(query), "assignment": assignment, "run": receipt(run_id, "diagnostic", arm, query, diagnostic_targets[i])})
    return {
        "protocol_version": "evaluation_bundle_v2", "evaluator_manifest": manifest(), "scenario_group": scenario_group(query_a, query_b, paraphrase_a, paraphrase_b),
        "main_evaluation": {"agent": agent, "noop": noop, "oracle": oracle, "score": score, "parity_verified": True},
        "diagnostics": {"runs": diagnostics, "one_factor_verified": True, "closed_loop_reruns_verified": True, "policy_separation": {"normalization": "noop_minus_oracle", "threshold": "0.1", "arm_margins": {"A": "0.625", "B": "0.625"}, "aggregate_min_margin": "0.625", "pass": True}},
        "semantic_verifier_digest": digest("semantic-validator-v2"),
    }


# The executable Round-8 fixture supersedes the legacy single-frame builder.
# Re-exporting preserves the public test/import surface while keeping the old
# fixture readable for migration archaeology.
from .golden_scenario import (  # noqa: E402,F401
    END as END,
    START as START,
    agent_view as agent_view,
    canonical_payload as canonical_payload,
    contract as contract,
    default_registry as default_registry,
    default_runtime as default_runtime,
    digest as digest,
    golden_bundle as golden_bundle,
    manifests as manifests,
    seal_trace as seal_trace,
    serialization as serialization,
)
