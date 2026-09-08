"""Complete multi-room, multi-frame Harness V2 conformance scenario."""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import rfc8785

from . import golden_backend, golden_policy
from .trace_evaluator import evaluate_trace
from .trust_evidence import build_policy_execution_receipt, build_reset_receipt, seal_manifest, serialization
from .trust_registry import TrustRegistry, TrustedRuntime, package_digest, sha256_value


START, RETRY, END = golden_backend.START, golden_backend.RETRY, golden_backend.END
SCHEMA_DIR = Path(__file__).resolve().parent
AXES = ("beneficiary", "spatial_scope", "activation_condition", "suspension_condition", "persistence", "deadline", "release_condition", "priority", "authorized_action")
RUNTIME_PACKAGE_MEMBERS = (
    "golden_scenario.py", "golden_backend.py", "golden_policy.py",
    "reference_scheduler.py", "trace_evaluator.py", "conformance.py",
    "semantic_validator.py", "trust_registry.py", "trust_evidence.py",
    "shared_types_v2.json", "rule_dsl_v0.json",
    "interaction_state_machine_v2.json", "public_private_schema_v2.json",
    "session_protocol_v2.json", "evaluator_and_group_protocol_v2.json",
    "evaluation_bundle_v2.json",
)


def canonical_decimal(value: Decimal | str | float) -> str:
    rendered = format(Decimal(str(value)), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def canonical_payload(value: Any) -> dict[str, Any]:
    raw = rfc8785.dumps(value)
    return {"media_type": "application/json;profile=rfc8785", "canonical_bytes_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()}


def decode_payload(payload: dict[str, Any]) -> Any:
    return json.loads(base64.b64decode(payload["canonical_bytes_base64"]))


def _package_paths(*names: str) -> list[Path]:
    return [SCHEMA_DIR / name for name in names]


def evaluator_package_digest() -> str:
    return package_digest(_package_paths("trace_evaluator.py", "evaluator_and_group_protocol_v2.json", "shared_types_v2.json"), package_id="harness-v2-evaluator", version="2")


def policy_package_digest() -> str:
    return package_digest(_package_paths("golden_policy.py", "rule_dsl_v0.json", "interaction_state_machine_v2.json"), package_id="harness-v2-golden-policy", version="2")


def backend_package_digest() -> str:
    return package_digest(_package_paths("golden_backend.py", "reference_scheduler.py", "rule_dsl_v0.json"), package_id="harness-v2-golden-backend", version="2")


def adapter_package_digest() -> str:
    return package_digest(_package_paths("golden_scenario.py", "public_private_schema_v2.json", "session_protocol_v2.json", "evaluation_bundle_v2.json"), package_id="harness-v2-golden-adapter", version="2")


def runtime_package_digest() -> str:
    return package_digest(_package_paths(*RUNTIME_PACKAGE_MEMBERS), package_id="harness-v2-golden-runtime", version="2")


def query_surface(query: str) -> dict[str, Any]:
    folded = query.casefold()
    if not query:
        room_refs, period_refs = [], []
    elif "whole home" in folded or "every room" in folded:
        room_refs, period_refs = ["kitchen", "living"], ["evening"]
    else:
        room_refs, period_refs = ["kitchen"], ["evening"]
    return {"text": query, "language": "en", "context": {"room_refs": room_refs, "named_period_refs": period_refs}}


def agent_view(query: str) -> dict[str, Any]:
    environment = golden_backend.project_reset(golden_backend.native_reset_snapshot())
    return {
        "query": query_surface(query),
        "track": "explicit_profile_control",
        "profile": {"timezone": "UTC", "preferences": [{"name": "thermal.preference", "value": 22, "provenance": "user_stated", "evidence_id": "evidence.thermal"}]},
        **environment,
        "horizon": {"start": START, "termination_disclosure": "exact", "termination": END},
        "capability_catalog": [{"capability_id": "thermal.control", "operations": [{"name": "set", "parameters": [{"name": "target_c", "type": "number", "required": True, "unit": "C", "minimum": 16, "maximum": 30}]}]}],
        "observation_catalog": [{"field": f"rooms.{room}.temperature_c", "value_type": "number", "unit": "C"} for room in ("kitchen", "living")],
        "event_catalog": {"event_types": ["period.enter"], "filters": [{"event_type": "period.enter", "fields": [{"name": "period", "value_type": "string", "required": True, "unit": None}]}]},
        "named_periods": [{"name": "evening", "start": START, "end": END}],
        "costs_budgets": {"currency": "benchmark_unit", "budgets": {"energy": 100}, "periods": [{"name": "episode", "start": START, "end": END}], "action_costs": [{"capability": "thermal.control", "operation": "set", "cost": 1, "unit": "benchmark_unit"}]},
        "tool_schema": {"allowed_actions": ["inspect", "act_now", "create_rule", "cancel_rule", "subscribe", "wake_at"], "interaction_budgets": {"max_callbacks": 4, "max_inspect_calls": 4, "max_mutation_transactions": 3, "max_questions": 0, "minimum_callback_interval_seconds": 60}},
        "safety_limits": [{"name": "temperature.max", "value": 30, "unit": "C", "provenance": "system_configuration"}],
    }


def bootstrap_audit() -> dict[str, Any]:
    return {"evidence_records": [{"evidence_id": "evidence.thermal", "source_hash": digest("source"), "visibility_justification": "resident explicitly stated this preference", "source_class": "user_stated"}]}


def episode_config() -> dict[str, Any]:
    return {
        "protocol_version": "interaction_state_machine_v2", "episode_id": "episode.golden.multiroom", "track": "explicit_profile_control",
        "clocks": {"physics_tick_seconds": 60, "episode_timezone": "UTC", "episode_start": START, "episode_termination": END},
        "budgets": {"agent_callbacks": 4, "inspect_calls": 4, "mutation_transactions": 3, "clarification_questions": 0, "minimum_callback_interval_seconds": 60},
        "callback_policy": {"allowed_reasons": ["episode_reset", "subscribed_public_event", "requested_wake_time", "subscribed_workflow_result", "user_reply", "episode_termination"], "coalesce_same_timestamp": True, "delivery_order": ["episode_termination", "subscribed_workflow_result", "subscribed_public_event", "requested_wake_time", "user_reply"], "workflow_requires_subscription": True, "at_most_once_per_event_id": True},
        "clarification_policy": {"enabled": False, "max_slots_per_question": 1, "allowed_slots": ["beneficiary", "spatial_scope", "activation_condition", "suspension_condition", "deadline", "release_condition", "priority", "authorized_action", "numeric_preference"], "duplicate_slot_question": "reject_and_consume_attempt", "invalid_question": "reject_and_consume_attempt", "answer_source": "pre_existing_grounded_record_or_unknown", "answer_delivery": "single_coalesced_callback_after_question_turn"},
        "termination_policy": {"callback_budget_exhaustion": "suppress_further_nontermination_callbacks", "installed_rules_after_callback_exhaustion": "continue_until_termination", "pending_events_at_horizon": "termination_wins_and_pending_results_are_trace_only", "notification_is_read_only": True, "trace_seal_is_one_way": True},
    }


def _state(backend: str, rules: str, ledger: dict[str, int], wake_queue: str | None = None) -> dict[str, str]:
    return {"backend": backend, "rules": rules, "subscriptions": digest("subscriptions.empty"), "wake_queue": wake_queue or digest("wake.empty"), "accounting": sha256_value(ledger)}


def _exchange(choice: dict[str, Any], pre: dict[str, str], post: dict[str, str], pre_ledger: dict[str, int], post_ledger: dict[str, int], committed: bool) -> dict[str, Any]:
    request = choice["transaction"]
    mutations = [("create_rule", item["rule"]["rule_id"]) for item in request["create_rules"]] + [("wake_request", item["wake_id"]) for item in request["wake_requests"]]
    if committed:
        outcome = {"transaction_id": request["transaction_id"], "status": "committed", "pre_backend_state_digest": pre["backend"], "post_backend_state_digest": post["backend"], "ledger_delta": {"mutation_transactions_attempted": 1, "protocol_errors": 0}, "mutation_outcomes": [{"mutation_kind": kind, "mutation_id": item_id, "status": "committed", "error_code": None} for kind, item_id in mutations], "transaction_error_code": None}
    else:
        outcome = {"transaction_id": request["transaction_id"], "status": "rejected", "unchanged_backend_state_digest": pre["backend"], "ledger_delta": {"mutation_transactions_attempted": 1, "protocol_errors": 1}, "mutation_outcomes": [{"mutation_kind": kind, "mutation_id": item_id, "status": "not_applied", "error_code": "TRANSACTION_ROLLBACK"} for kind, item_id in mutations], "transaction_error_code": "WAKE_NOT_FUTURE"}
    return {"request": request, "pre_state": {"backend_state_digest": pre["backend"], "accounting_ledger": pre_ledger}, "strict_outcome": outcome, "post_state": {"backend_state_digest": post["backend"], "accounting_ledger": post_ledger}, "verifier_record": {"verifier_id": "trusted.transaction.v2", "verifier_version": "2", "verifier_hash": runtime_package_digest(), "verified": True, "checks": [{"check_id": "atomic_rollback_or_commit", "passed": True}]}}


def sealed_session(query: str, policy_id: str, runtime: TrustedRuntime) -> tuple[dict[str, Any], dict[str, Any]]:
    view = agent_view(query)
    policy_config = runtime.policy_configs[policy_id]
    backend = sha256_value(golden_backend.native_reset_snapshot())
    reset_callback = {"callback_id": "callback.reset", "reason": "episode_reset", "timestamp": START, "backend_state_digest": backend, "wake_ids": []}
    first_choice = runtime.execute_policy(view, [reset_callback], policy_id, policy_config)[0]
    callbacks = [reset_callback]
    if first_choice["kind"] == "commit_transaction":
        retry_wakes = [item["wake_id"] for item in first_choice["transaction"]["wake_requests"] if item["at"] == RETRY]
        if retry_wakes:
            callbacks.append({"callback_id": "callback.retry", "reason": "requested_wake_time", "timestamp": RETRY, "backend_state_digest": backend, "wake_ids": retry_wakes})
    choices = runtime.execute_policy(view, callbacks, policy_id, policy_config)
    base_ledger = {"callbacks_delivered": len(callbacks), "inspect_calls_used": 0, "mutation_transactions_attempted": 0, "clarification_questions_attempted": 0, "protocol_errors": 0}
    empty_rules = digest("rules.empty")
    empty_wake = digest("wake.empty")
    current_rules = empty_rules
    current_wake = empty_wake
    current_ledger = base_ledger
    current_state = _state(backend, current_rules, current_ledger, current_wake)
    turns = []
    records = []

    def add(timestamp: str, kind: str, actor: str, payload: dict[str, Any], post: dict[str, str] | None = None) -> None:
        nonlocal current_state
        next_state = current_state if post is None else post
        records.append({"sequence": len(records), "timestamp": timestamp, "kind": kind, "actor": actor, "payload": canonical_payload(payload), "pre_state": current_state, "post_state": next_state})
        current_state = next_state

    add(START, "bootstrap_delivered", "harness", view)
    for callback, choice in zip(callbacks, choices):
        timestamp = callback["timestamp"]
        if callback["reason"] == "requested_wake_time":
            callback_state = _state(backend, current_rules, current_ledger, empty_wake)
            current_wake = empty_wake
        else:
            callback_state = current_state
        add(timestamp, "callback_delivered", "harness", callback, callback_state)
        pre = current_state
        add(timestamp, "terminal_choice", "agent", choice)
        if choice["kind"] == "yield_without_mutation":
            post, outcome = pre, {"kind": "yielded"}
        else:
            request = choice["transaction"]
            invalid_wake = any(datetime.fromisoformat(item["at"]) <= datetime.fromisoformat(timestamp) for item in request["wake_requests"])
            next_ledger = {**current_ledger, "mutation_transactions_attempted": current_ledger["mutation_transactions_attempted"] + 1, "protocol_errors": current_ledger["protocol_errors"] + int(invalid_wake)}
            if invalid_wake:
                post = _state(backend, current_rules, next_ledger, current_wake)
            else:
                created = [item["rule"] for item in request["create_rules"]]
                current_rules = sha256_value(created) if created else current_rules
                current_wake = sha256_value(request["wake_requests"]) if request["wake_requests"] else current_wake
                post = _state(backend, current_rules, next_ledger, current_wake)
            outcome = _exchange(choice, pre, post, current_ledger, next_ledger, not invalid_wake)
            current_ledger = next_ledger
        turn = {"turn_id": f"turn.{len(turns)}", "track": "explicit_profile_control", "callback": canonical_payload(callback), "pre_state": pre, "inspects": [], "terminal_choice": canonical_payload(choice), "terminal_outcome": outcome, "post_state": post}
        turns.append(turn)
        kind = "yield" if choice["kind"] == "yield_without_mutation" else ("protocol_error" if outcome["strict_outcome"]["status"] == "rejected" else "tool_outcome")
        add(timestamp, kind, "harness", outcome, post)
    add(END, "termination_notice", "harness", {"reason": "episode_termination", "timestamp": END})
    config = episode_config()
    session = {"session_id": f"session.{digest(query + policy_id)[:16]}", "agent_build_digest": runtime.policy_package_digests[policy_id], "policy_config_digest": sha256_value(policy_config), "episode_configuration": config, "episode_configuration_digest": sha256_value(config), "bootstrap": canonical_payload(view), "bootstrap_audit": bootstrap_audit(), "turns": turns, "records": records, "sealed": True, "transcript_digest": sha256_value(records)}
    policy_receipt = build_policy_execution_receipt(runtime, policy_id=policy_id, policy_config_digest=session["policy_config_digest"], agent_view=view, callbacks=callbacks, terminal_choices=choices)
    return session, policy_receipt


def _component(component_id: str, path: str, weight: str, unit: str = "C", operator: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"component_id": component_id, "inputs": [{"json_pointer": path, "value_type": "number", "unit": unit, "allowed_qualities": ["fresh"]}], "loss_operator": operator or {"name": "absolute_error", "parameters": {"target": "22"}}, "active_selector": {"mode": "all_intervals", "binding": None}, "output_unit": unit, "integration": "left_endpoint", "interval": "episode_start_inclusive_termination_exclusive_except_terminal_value", "missing_rule": {"missing_input": "fail_episode", "stale_input": "treat_as_missing", "max_staleness_seconds": 300, "maximum_missing_fraction": 0}, "normalizer": {"method": "identity", "source": "preregistered_constant", "source_version": "1", "value": "1", "zero_value_rule": "manifest_invalid"}, "aggregation": "mean_over_active_mask", "weight": weight}


def manifests() -> dict[str, dict[str, Any]]:
    implementation = evaluator_package_digest()
    common = {"manifest_version": "2", "family_id": "thermal", "implementation_hash": implementation, "safety_thresholds": [{"threshold_id": f"temperature_max_{room}", "input_path": f"/public_state/{room}_temperature_c", "comparator": "lte", "value": "30", "unit": "C"} for room in ("kitchen", "living")], "opportunity_gap_threshold": "1", "main_gain_threshold": "0.5", "policy_separation_threshold": "0.1", "oracle": {"implementation_id": "policy.oracle", "implementation_version": "2", "implementation_hash": policy_package_digest(), "solver": "enumeration", "solver_version": "2", "optimality_tolerance": "0", "numeric_tolerance": "0", "information_parity": "same_online_public_information_and_no_future_exogenous_information", "api_parity": "same_action_rule_subscription_and_wake_api", "budget_parity": "same_callback_interaction_and_action_budgets"}, "numeric_semantics": {"decimal_library": "decimal", "library_version": "3.12", "precision_digits": 28, "rounding": "ROUND_HALF_EVEN"}, "canonical_serialization": {"algorithm": "RFC8785_JCS_UTF8", "nonfinite_rule": "reject_before_serialization", "decimal_rule": "canonical_decimal_no_exponent_no_trailing_zero_no_negative_zero", "digest": "sha256"}}
    arm_a = seal_manifest({"manifest_id": "manifest.thermal.scope.A", **common, "loss_components": [_component("kitchen_error", "/public_state/kitchen_temperature_c", "1"), _component("unauthorized_action", "/private_evaluator_primitives/unauthorized_action_count", "1", "count", {"name": "identity", "parameters": {}})]})
    arm_b = seal_manifest({"manifest_id": "manifest.thermal.scope.B", **common, "loss_components": [_component("kitchen_error", "/public_state/kitchen_temperature_c", "0.5"), _component("living_error", "/public_state/living_temperature_c", "0.5")]})
    return {"A": arm_a, "B": arm_b}


def default_runtime() -> TrustedRuntime:
    expected = manifests()
    policy_digest = policy_package_digest()
    policy_ids = ("policy.agent", "policy.noop", "policy.oracle")
    return TrustedRuntime(runtime_id="runtime.golden.multiroom.v2", version="2", package_digest=runtime_package_digest(), backend_package_digest=backend_package_digest(), adapter_package_digest=adapter_package_digest(), evaluator_package_digest=evaluator_package_digest(), policy_package_digests={name: policy_digest for name in policy_ids}, policy_configs={name: {"policy_id": name, "fixture_version": "2"} for name in policy_ids}, evaluator_manifest_digests={manifest["manifest_id"]: manifest["manifest_digest"] for manifest in expected.values()}, reconstruct_reset=lambda seed_digest, exogenous_digest: golden_backend.native_reset_snapshot(), project_reset=golden_backend.project_reset, execute_policy=golden_policy.execute, replay=lambda native, session, arm_id, query_digest, manifest: seal_trace(golden_backend.replay_body(native, session, arm_id, query_digest, manifest)))


def default_registry() -> TrustRegistry:
    return TrustRegistry([default_runtime()])


def seal_trace(body: dict[str, Any]) -> dict[str, Any]:
    sealed = serialization(body)
    return {**body, "trace_digest": sealed["digest"], "trace_serialization": sealed}


def controls(view: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    initial = {key: value for key, value in view.items() if key != "query"}
    return {"initial_public_state": sha256_value(initial), "inventory": sha256_value(view["inventory"]), "simulator_seed": digest("seed"), "exogenous_realization": digest("exogenous"), "costs": sha256_value(view["costs_budgets"]), "budgets": sha256_value(config["budgets"]), "horizon": sha256_value({"clocks": config["clocks"], "horizon": view["horizon"]}), "track": "explicit_profile_control"}


def receipt(run_id: str, role: str, arm_id: str, query: str, runtime: TrustedRuntime, manifest: dict[str, Any]) -> dict[str, Any]:
    policy_id = {"agent": "policy.agent", "diagnostic": "policy.agent", "noop": "policy.noop", "public_information_oracle": "policy.oracle"}[role]
    session, policy_receipt = sealed_session(query, policy_id, runtime)
    view = decode_payload(session["bootstrap"])
    native = golden_backend.native_reset_snapshot()
    reset_receipt = build_reset_receipt(runtime, native, seed_digest=digest("seed"), exogenous_realization_digest=digest("exogenous"))
    query_digest = sha256_value(view["query"])
    trace = runtime.replay(native, session, arm_id, query_digest, manifest)
    replay = runtime.replay(native, session, arm_id, query_digest, manifest)
    evaluation = evaluate_trace(manifest, trace)
    replay_receipt = {"verifier_id": runtime.runtime_id, "verifier_version": runtime.version, "verifier_hash": runtime.package_digest, "backend_build_digest": runtime.backend_package_digest, "adapter_build_digest": runtime.adapter_package_digest, "initial_public_state_digest": controls(view, session["episode_configuration"])["initial_public_state"], "session_transcript_digest": session["transcript_digest"], "primary_trace_digest": trace["trace_digest"], "replay_trace_digest": replay["trace_digest"], "deterministic": True, "replay_trace": replay}
    return {"run_id": run_id, "role": role, "policy_build_digest": runtime.policy_package_digests[policy_id], "policy_config_digest": session["policy_config_digest"], "controls": controls(view, session["episode_configuration"]), "sealed_session": session, "reset_receipt": reset_receipt, "policy_execution_receipt": policy_receipt, "sealed_trace": trace, "replay_receipt": replay_receipt, "loss": canonical_decimal(evaluation.loss), "status": "eligible"}


def contract(arm_id: str) -> dict[str, Any]:
    values = {axis: ("kitchen" if axis == "spatial_scope" and arm_id == "A" else "whole_home" if axis == "spatial_scope" else axis) for axis in AXES}
    axes = {axis: {"canonical_utf8_base64": serialization(value)["canonical_utf8_base64"], "digest": serialization(value)["digest"]} for axis, value in values.items()}
    body = {"contract_version": "2", "responsibility_id": f"thermal.{arm_id}", "axes": axes}
    sealed = serialization(body)
    return {**body, "contract_digest": sealed["digest"], "serialization": sealed}


def golden_bundle() -> dict[str, Any]:
    query_a = "Keep the kitchen comfortable this evening."
    query_b = "Keep the whole home comfortable this evening."
    paraphrase_a = "Could you make sure the kitchen stays comfortable tonight?"
    paraphrase_b = "Please make sure every room stays comfortable tonight."
    runtime = default_runtime()
    manifest_by_arm = manifests()
    main = {name: receipt(f"main.{name}", role, "A", query_a, runtime, manifest_by_arm["A"]) for name, role in (("agent", "agent"), ("noop", "noop"), ("oracle", "public_information_oracle"))}
    agent_loss, noop_loss, oracle_loss = (main[name]["loss"] for name in ("agent", "noop", "oracle"))
    denominator_value = Decimal(noop_loss) - Decimal(oracle_loss)
    denominator = canonical_decimal(denominator_value)
    gain = (Decimal(noop_loss) - Decimal(agent_loss)) / denominator_value
    score_body = {"protocol_status": "valid", "safety_status": "safe", "oracle_status": "ok", "agent_loss": agent_loss, "noop_loss": noop_loss, "oracle_loss": oracle_loss, "denominator": denominator, "gain_status": "eligible", "raw_gain": canonical_decimal(gain), "raw_normalized_regret": "0", "display_clipped_gain": canonical_decimal(gain), "main_gain_pass": True, "component_metrics": {key: canonical_decimal(value) for key, value in evaluate_trace(manifest_by_arm["A"], main["agent"]["sealed_trace"]).component_metrics.items()}}
    score = {**score_body, "serialization": serialization(score_body)}
    specs = [("A", "source_near", query_a, "own_arm"), ("B", "source_near", query_b, "own_arm"), ("A", "faithful_paraphrase", paraphrase_a, "own_arm"), ("B", "faithful_paraphrase", paraphrase_b, "own_arm"), ("A", "deletion", "", "deleted"), ("B", "deletion", "", "deleted"), ("A", "cross_arm_shuffle", query_b, "arm_B_query_on_A"), ("B", "cross_arm_shuffle", query_a, "arm_A_query_on_B")]
    diagnostics = [{"arm_id": arm, "query_role": role, "query_digest": sha256_value(query_surface(query)), "assignment": assignment, "run": receipt(f"diagnostic.{index}", "diagnostic", arm, query, runtime, manifest_by_arm[arm])} for index, (arm, role, query, assignment) in enumerate(specs)]
    qa, qb, qpa, qpb = (sha256_value(query_surface(query)) for query in (query_a, query_b, paraphrase_a, paraphrase_b))
    controls_a = main["agent"]["controls"]
    scenario = {"group_id": "group.golden.multiroom", "track": "explicit_profile_control", "split": "validation", "one_factor_pair": True, "contrast_axis": "spatial_scope", "contrast_witness": {"arm_a_axis_value_digest": contract("A")["axes"]["spatial_scope"]["digest"], "arm_b_axis_value_digest": contract("B")["axes"]["spatial_scope"]["digest"], "values_differ": True}, "initial_public_state_digest": controls_a["initial_public_state"], "inventory_digest": controls_a["inventory"], "simulator_seed_digest": controls_a["simulator_seed"], "exogenous_realization_digest": controls_a["exogenous_realization"], "costs_digest": controls_a["costs"], "budgets_digest": controls_a["budgets"], "horizon_policy_digest": controls_a["horizon"], "arms": [{"arm_id": arm, "canonical_contract": contract(arm), "evaluator_manifest": manifest_by_arm[arm]} for arm in ("A", "B")], "query_registry": {qa: {"arm_id": "A", "role": "source_near"}, qb: {"arm_id": "B", "role": "source_near"}, qpa: {"arm_id": "A", "role": "faithful_paraphrase"}, qpb: {"arm_id": "B", "role": "faithful_paraphrase"}}, "query_shuffle_protocol": "rerun_policy_closed_loop_with_same_seed_and_exogenous_realization_never_reuse_backend_or_agent_trace", "group_aggregation": "mean_bidirectional_cross_application_regret"}
    # Filled by conformance recomputation; values are derived below for readability.
    diag_losses = {(item["arm_id"], item["query_role"]): Decimal(item["run"]["loss"]) for item in diagnostics}
    denom = Decimal(noop_loss) - Decimal(oracle_loss)
    margins = {}
    for arm in ("A", "B"):
        own = min((Decimal(noop_loss) - diag_losses[(arm, role)]) / denom for role in ("source_near", "faithful_paraphrase"))
        bad = max((Decimal(noop_loss) - diag_losses[(arm, role)]) / denom for role in ("deletion", "cross_arm_shuffle"))
        margins[arm] = canonical_decimal(own - bad)
    return {"protocol_version": "evaluation_bundle_v2", "runtime_id": runtime.runtime_id, "evaluator_manifest": manifest_by_arm["A"], "scenario_group": scenario, "main_evaluation": {**main, "score": score, "parity_verified": True}, "diagnostics": {"runs": diagnostics, "one_factor_verified": True, "closed_loop_reruns_verified": True, "policy_separation": {"normalization": "noop_minus_oracle", "threshold": "0.1", "arm_margins": margins, "aggregate_min_margin": canonical_decimal(min(Decimal(v) for v in margins.values())), "pass": True}}, "semantic_verifier_digest": runtime.package_digest}
