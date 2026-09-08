import hashlib
from copy import deepcopy

import pytest
import rfc8785

from harness_v2.conformance import ConformanceError, decode_canonical_payload, validate_conformance, validate_schema
from harness_v2.golden import canonical_payload, golden_bundle


def reseal_session(session):
    session["transcript_digest"] = hashlib.sha256(rfc8785.dumps(session["records"])).hexdigest()


def reseal_trace(trace):
    body = {key: value for key, value in trace.items() if key not in {"trace_digest", "trace_serialization"}}
    sealed = __import__("harness_v2.golden", fromlist=["serialization"]).serialization(body)
    trace["trace_digest"] = sealed["digest"]
    trace["trace_serialization"] = sealed


def mutate_and_reject(mutator):
    bundle = golden_bundle()
    mutator(bundle)
    with pytest.raises(ConformanceError):
        validate_conformance(bundle)


def test_complete_golden_bundle_is_schema_and_conformance_valid():
    bundle = golden_bundle()
    validate_schema(bundle, "evaluation_bundle_v2.json")
    validate_conformance(bundle)


def test_rejects_explicit_track_clarification_exchange():
    def mutate(bundle):
        session = bundle["main_evaluation"]["agent"]["sealed_session"]
        turn = session["turns"][0]
        question = {"question_id": "q1", "slots": ["numeric_preference"], "question_text": "Which temperature?"}
        turn["terminal_choice"] = canonical_payload({"kind": "ask_user", "question": question})
        turn["terminal_outcome"] = {"question": canonical_payload(question), "outcome": {"status": "answered", "answer": "22 C", "error_code": None}, "pre_state": turn["pre_state"], "post_state": turn["post_state"]}
        session["records"][2]["payload"] = turn["terminal_choice"]
        session["records"][3]["kind"] = "clarification_reply"
        session["records"][3]["payload"] = canonical_payload({"answer": "22 C"})
        reseal_session(session)
    mutate_and_reject(mutate)


def test_rejects_forged_canonical_payload_digest():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"]["sealed_session"]["bootstrap"].update({"sha256": "a" * 64}))


def test_rejects_callback_payload_leakage_casefolded():
    def mutate(bundle):
        session = bundle["main_evaluation"]["agent"]["sealed_session"]
        session["records"][1]["payload"] = canonical_payload({"Oracle": "gold"})
        reseal_session(session)
    mutate_and_reject(mutate)


def test_rejects_termination_not_after_start():
    def mutate(bundle):
        session = bundle["main_evaluation"]["agent"]["sealed_session"]
        config = session["episode_configuration"]
        config["clocks"]["episode_termination"] = config["clocks"]["episode_start"]
        session["episode_configuration_digest"] = hashlib.sha256(rfc8785.dumps(config)).hexdigest()
    mutate_and_reject(mutate)


def test_formal_rule_schema_rejects_whitespace_ids():
    rule = {
        "rule_id": "bad id", "trigger": {"type": "at_timestamp", "at": "2026-01-01T18:00:00+00:00"},
        "condition": {"op": "true"},
        "commands": [{"command_id": "also bad", "device_id": "hvac.kitchen", "capability": "thermal.control", "operation": "set", "parameters": {"target_c": 22}}],
        "lifecycle": {"type": "once"}, "on_release_commands": [], "on_release_policy": {"execute_on":"every_normal_retirement_and_committed_manual_cancel","exactly_once":True,"failure":"record_failure_retire_no_retry"}, "cooldown_seconds": 0, "priority": 1,
    }
    valid = deepcopy(rule)
    valid["rule_id"] = "rule.good"
    valid["commands"][0]["command_id"] = "command.good"
    validate_schema(valid, "rule_dsl_v0.json")
    with pytest.raises(ConformanceError):
        validate_schema(rule, "rule_dsl_v0.json")


def test_formal_manifest_rejects_missing_operator_target():
    def mutate(bundle):
        bundle["evaluator_manifest"]["loss_components"][0]["loss_operator"]["parameters"] = {}
    mutate_and_reject(mutate)


def test_rejects_protocol_invalid_receipt_with_eligible_score():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"].update({"status": "protocol_invalid"}))


def test_rejects_forged_normalized_gain():
    mutate_and_reject(lambda b: b["main_evaluation"]["score"].update({"raw_gain": "0.9"}))


def test_rejects_reused_closed_loop_trace():
    def mutate(bundle):
        runs = bundle["diagnostics"]["runs"]
        runs[1]["run"]["sealed_trace"]["trace_digest"] = runs[0]["run"]["sealed_trace"]["trace_digest"]
    mutate_and_reject(mutate)


def test_rejects_session_trace_query_swap():
    def mutate(bundle):
        run = bundle["diagnostics"]["runs"][0]["run"]
        run["sealed_session"]["bootstrap"] = canonical_payload({**deepcopy(__import__("harness_v2.golden", fromlist=["agent_view"]).agent_view("wrong query"))})
    mutate_and_reject(mutate)


def test_rejects_query_deletion_with_residual_scope_context():
    def mutate(bundle):
        diagnostic = next(item for item in bundle["diagnostics"]["runs"] if item["arm_id"] == "A" and item["query_role"] == "deletion")
        run = diagnostic["run"]
        session = run["sealed_session"]
        view = decode_canonical_payload(session["bootstrap"])
        view["query"]["context"] = {"room_refs": ["kitchen"], "named_period_refs": ["evening"]}
        session["bootstrap"] = canonical_payload(view)
        session["records"][0]["payload"] = session["bootstrap"]
        reseal_session(session)
        query_digest = hashlib.sha256(rfc8785.dumps(view["query"])).hexdigest()
        diagnostic["query_digest"] = query_digest
        run["sealed_trace"]["query_digest"] = query_digest
        reseal_trace(run["sealed_trace"])
    mutate_and_reject(mutate)


def test_rejects_text_only_shuffle_that_keeps_old_scope_context():
    def mutate(bundle):
        diagnostic = next(item for item in bundle["diagnostics"]["runs"] if item["arm_id"] == "A" and item["query_role"] == "cross_arm_shuffle")
        run = diagnostic["run"]
        session = run["sealed_session"]
        view = decode_canonical_payload(session["bootstrap"])
        view["query"]["text"] = "Keep the kitchen comfortable this evening."
        session["bootstrap"] = canonical_payload(view)
        session["records"][0]["payload"] = session["bootstrap"]
        reseal_session(session)
        query_digest = hashlib.sha256(rfc8785.dumps(view["query"])).hexdigest()
        diagnostic["query_digest"] = query_digest
        run["sealed_trace"]["query_digest"] = query_digest
        reseal_trace(run["sealed_trace"])
    mutate_and_reject(mutate)


def test_rejects_missing_paraphrase_diagnostics():
    def mutate(bundle):
        bundle["diagnostics"]["runs"] = [run for run in bundle["diagnostics"]["runs"] if run["query_role"] != "faithful_paraphrase"]
    mutate_and_reject(mutate)


def test_rejects_identical_counterfactual_contract_arms():
    def mutate(bundle):
        group = bundle["scenario_group"]
        group["arms"][1]["canonical_contract"] = deepcopy(group["arms"][0]["canonical_contract"])
    mutate_and_reject(mutate)


def test_rejects_forged_transcript_digest():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"]["sealed_session"].update({"transcript_digest": "b" * 64}))


def test_rejects_forged_trace_serialization_digest():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"]["sealed_trace"]["trace_serialization"].update({"digest": "b" * 64}))


def test_rejects_forged_trace_digest_even_when_serialization_record_is_intact():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"]["sealed_trace"].update({"trace_digest": "b" * 64}))


def test_rejects_forged_score_serialization_bytes():
    mutate_and_reject(lambda b: b["main_evaluation"]["score"]["serialization"].update({"canonical_utf8_base64": "e30="}))


def test_rejects_forged_contract_serialization_digest():
    mutate_and_reject(lambda b: b["scenario_group"]["arms"][0]["canonical_contract"]["serialization"].update({"digest": "b" * 64}))


def test_rejects_forged_axis_serialization_digest():
    mutate_and_reject(lambda b: b["scenario_group"]["arms"][0]["canonical_contract"]["axes"]["spatial_scope"].update({"digest": "b" * 64}))


def test_rejects_forged_contrast_witness():
    mutate_and_reject(lambda b: b["scenario_group"]["contrast_witness"].update({"arm_a_axis_value_digest": "b" * 64}))


def test_rejects_forged_policy_separation_summary():
    mutate_and_reject(lambda b: b["diagnostics"]["policy_separation"].update({"aggregate_min_margin": "0.9"}))


def test_rejects_policy_separation_below_manifest_threshold():
    mutate_and_reject(lambda b: b["evaluator_manifest"].update({"policy_separation_threshold": "0.6"}))


def test_rejects_receipt_policy_identity_swap():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"].update({"policy_config_digest": "b" * 64}))


def test_rejects_trace_group_swap():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"]["sealed_trace"].update({"scenario_group_id": "group.other"}))


def test_rejects_frozen_control_group_drift():
    mutate_and_reject(lambda b: b["diagnostics"]["runs"][0]["run"]["controls"].update({"inventory": "b" * 64}))


def test_rejects_arm_evaluator_manifest_drift_from_top_level():
    mutate_and_reject(lambda b: b["scenario_group"]["arms"][0]["evaluator_manifest"].update({"manifest_id": "manifest.other"}))


def test_rejects_protocol_invalid_language_diagnostic():
    mutate_and_reject(lambda b: b["diagnostics"]["runs"][0]["run"].update({"status": "protocol_invalid"}))


def test_rejects_language_diagnostic_policy_config_drift():
    def mutate(bundle):
        run = bundle["diagnostics"]["runs"][0]["run"]
        run["policy_config_digest"] = "b" * 64
        run["sealed_session"]["policy_config_digest"] = "b" * 64
    mutate_and_reject(mutate)


def test_rejects_policy_failed_main_with_eligible_score():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"].update({"status": "policy_failed"}))


def test_rejects_diagnostic_trace_arm_swap_even_when_trace_is_resealed():
    def mutate(bundle):
        trace = bundle["diagnostics"]["runs"][0]["run"]["sealed_trace"]
        trace["arm_id"] = "B"
        body = {key: value for key, value in trace.items() if key not in {"trace_digest", "trace_serialization"}}
        sealed = __import__("harness_v2.golden", fromlist=["serialization"]).serialization(body)
        trace["trace_digest"] = sealed["digest"]
        trace["trace_serialization"] = sealed
    mutate_and_reject(mutate)


def test_rejects_bootstrap_initial_state_digest_forgery():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"]["controls"].update({"initial_public_state": "b" * 64}))


def test_rejects_session_transaction_missing_from_trace_after_reseal():
    def mutate(bundle):
        trace = bundle["main_evaluation"]["agent"]["sealed_trace"]
        trace["transaction_exchanges"] = []
        reseal_trace(trace)
    mutate_and_reject(mutate)


def test_rejects_applied_command_not_equal_to_session_request_after_reseal():
    def mutate(bundle):
        trace = bundle["main_evaluation"]["agent"]["sealed_trace"]
        trace["frames"][2]["applied_commands"][0]["parameters"]["target_c"] = 21
        reseal_trace(trace)
    mutate_and_reject(mutate)


def test_rejects_untrusted_replay_verifier_hash():
    mutate_and_reject(lambda b: b["main_evaluation"]["agent"]["replay_receipt"].update({"verifier_hash": "b" * 64}))


def test_rejects_jointly_forged_primary_and_replay_trace_against_trusted_recomputation():
    def mutate(bundle):
        run = bundle["diagnostics"]["runs"][0]["run"]
        for trace in (run["sealed_trace"], run["replay_receipt"]["replay_trace"]):
            trace["frames"][0]["public_state"]["kitchen_temperature_c"]["value"] = 22
            reseal_trace(trace)
        run["replay_receipt"]["primary_trace_digest"] = run["sealed_trace"]["trace_digest"]
        run["replay_receipt"]["replay_trace_digest"] = run["replay_receipt"]["replay_trace"]["trace_digest"]
        run["loss"] = "0"
    mutate_and_reject(mutate)
