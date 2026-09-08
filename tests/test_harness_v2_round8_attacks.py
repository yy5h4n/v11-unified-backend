from copy import deepcopy

import pytest

from harness_v2.conformance import ConformanceError, decode_canonical_payload, validate_conformance
from harness_v2.golden_scenario import (
    RUNTIME_PACKAGE_MEMBERS,
    canonical_payload,
    controls,
    default_runtime,
    digest,
    golden_bundle,
    seal_trace,
)
from harness_v2.golden_backend import native_reset_snapshot
from harness_v2.trust_evidence import (
    build_policy_execution_receipt,
    manifest_body,
    seal_manifest,
    serialization,
)
from harness_v2.trust_registry import sha256_value


def test_multiroom_golden_operationalizes_scope_and_language_contrast():
    bundle = golden_bundle()
    manifests = {item["arm_id"]: item["evaluator_manifest"] for item in bundle["scenario_group"]["arms"]}
    assert {binding["json_pointer"] for component in manifests["A"]["loss_components"] for binding in component["inputs"]} != {binding["json_pointer"] for component in manifests["B"]["loss_components"] for binding in component["inputs"]}
    diagnostics = {(item["arm_id"], item["query_role"]): item["run"] for item in bundle["diagnostics"]["runs"]}
    assert diagnostics[("A", "source_near")]["loss"] == diagnostics[("A", "faithful_paraphrase")]["loss"]
    assert diagnostics[("B", "source_near")]["loss"] == diagnostics[("B", "faithful_paraphrase")]["loss"]
    assert float(diagnostics[("A", "cross_arm_shuffle")]["loss"]) > float(diagnostics[("A", "source_near")]["loss"])
    assert float(diagnostics[("B", "cross_arm_shuffle")]["loss"]) > float(diagnostics[("B", "source_near")]["loss"])
    a_devices = {command["device_id"] for frame in diagnostics[("A", "source_near")]["sealed_trace"]["frames"] for command in frame["applied_commands"] if command["source"] == "rule_firing"}
    b_devices = {command["device_id"] for frame in diagnostics[("B", "source_near")]["sealed_trace"]["frames"] for command in frame["applied_commands"] if command["source"] == "rule_firing"}
    assert a_devices == {"hvac.kitchen"}
    assert b_devices == {"hvac.kitchen", "hvac.living"}


def test_golden_contains_multiframe_rollback_install_fire_release_lineage():
    trace = golden_bundle()["main_evaluation"]["agent"]["sealed_trace"]
    assert len(trace["frames"]) == 4
    assert [exchange["strict_outcome"]["status"] for exchange in trace["transaction_exchanges"]] == ["committed", "rejected"]
    event_types = [event["type"] for frame in trace["frames"] for event in frame["rule_events"]]
    assert {"installed", "firing_committed", "expired", "released"} <= set(event_types)
    sources = {command["source"] for frame in trace["frames"] for command in frame["applied_commands"]}
    assert sources == {"rule_firing", "rule_release"}
    assert all("origin" in command for frame in trace["frames"] for command in frame["applied_commands"])


def test_retry_callback_is_caused_by_the_committed_wake_and_consumes_it():
    session = golden_bundle()["main_evaluation"]["agent"]["sealed_session"]
    install = decode_canonical_payload(session["turns"][0]["terminal_choice"])["transaction"]
    retry = decode_canonical_payload(session["turns"][1]["callback"])
    assert retry["reason"] == "requested_wake_time"
    assert retry["wake_ids"] == [install["wake_requests"][0]["wake_id"]]
    assert retry["timestamp"] == install["wake_requests"][0]["at"]
    retry_record = next(record for record in session["records"] if record["payload"] == session["turns"][1]["callback"])
    assert retry_record["pre_state"]["wake_queue"] != retry_record["post_state"]["wake_queue"]


def test_resealed_wake_callback_without_matching_committed_wake_is_rejected():
    bundle = golden_bundle()
    runtime = default_runtime()
    run = bundle["main_evaluation"]["agent"]
    session = run["sealed_session"]
    original = session["turns"][1]["callback"]
    callback = decode_canonical_payload(original)
    callback["wake_ids"] = ["wake.forged"]
    replacement = canonical_payload(callback)
    session["turns"][1]["callback"] = replacement
    record = next(item for item in session["records"] if item["payload"] == original)
    record["payload"] = replacement
    session["transcript_digest"] = sha256_value(session["records"])
    callbacks = [decode_canonical_payload(turn["callback"]) for turn in session["turns"]]
    choices = [decode_canonical_payload(turn["terminal_choice"]) for turn in session["turns"]]
    run["policy_execution_receipt"] = build_policy_execution_receipt(runtime, policy_id="policy.agent", policy_config_digest=run["policy_config_digest"], agent_view=decode_canonical_payload(session["bootstrap"]), callbacks=callbacks, terminal_choices=choices)
    with pytest.raises(ConformanceError, match="committed wake request"):
        validate_conformance(bundle)


def test_fully_resealed_reset_temperature_attack_is_rejected_by_trusted_reset():
    bundle = golden_bundle()
    runtime = default_runtime()
    receipts = [bundle["main_evaluation"][name] for name in ("agent", "noop", "oracle")]
    receipts += [item["run"] for item in bundle["diagnostics"]["runs"]]
    new_initial_digest = None
    for run in receipts:
        session = run["sealed_session"]
        view = decode_canonical_payload(session["bootstrap"])
        for observation in view["observations"]:
            observation["value"] = 29
        session["bootstrap"] = canonical_payload(view)
        session["records"][0]["payload"] = session["bootstrap"]
        session["transcript_digest"] = sha256_value(session["records"])
        native = native_reset_snapshot()
        native["rooms"]["kitchen"]["temperature_c"] = 29
        native["rooms"]["living"]["temperature_c"] = 29
        forged_reset = deepcopy(run["reset_receipt"])
        forged_reset["native_snapshot"] = serialization(native)
        forged_reset["public_projection"] = serialization(runtime.project_reset(native))
        forged_reset["receipt_digest"] = sha256_value({key: value for key, value in forged_reset.items() if key != "receipt_digest"})
        run["reset_receipt"] = forged_reset
        run["controls"] = controls(view, session["episode_configuration"])
        new_initial_digest = run["controls"]["initial_public_state"]
        proof = run["replay_receipt"]
        proof["initial_public_state_digest"] = new_initial_digest
        proof["session_transcript_digest"] = session["transcript_digest"]
        callbacks = [decode_canonical_payload(turn["callback"]) for turn in session["turns"]]
        choices = [decode_canonical_payload(turn["terminal_choice"]) for turn in session["turns"]]
        run["policy_execution_receipt"] = build_policy_execution_receipt(runtime, policy_id=run["policy_execution_receipt"]["policy_id"], policy_config_digest=run["policy_config_digest"], agent_view=view, callbacks=callbacks, terminal_choices=choices)
    bundle["scenario_group"]["initial_public_state_digest"] = new_initial_digest
    with pytest.raises(ConformanceError, match="trusted deterministic reset"):
        validate_conformance(bundle)


def test_resealed_evaluator_target_rewrite_is_rejected_by_registry():
    bundle = golden_bundle()
    rewritten = {}
    for arm in bundle["scenario_group"]["arms"]:
        body = manifest_body(arm["evaluator_manifest"])
        for component in body["loss_components"]:
            if "target" in component["loss_operator"]["parameters"]:
                component["loss_operator"]["parameters"]["target"] = "21"
        rewritten[arm["arm_id"]] = seal_manifest(body)
        arm["evaluator_manifest"] = rewritten[arm["arm_id"]]
    bundle["evaluator_manifest"] = rewritten["A"]
    with pytest.raises(ConformanceError, match="trusted registry"):
        validate_conformance(bundle)


def test_rule_lineage_forgery_resealed_in_both_traces_is_rejected():
    bundle = golden_bundle()
    run = bundle["main_evaluation"]["agent"]
    for trace in (run["sealed_trace"], run["replay_receipt"]["replay_trace"]):
        command = next(command for frame in trace["frames"] for command in frame["applied_commands"] if command["source"] == "rule_firing")
        command["origin"]["rule_id"] = "rule.forged"
        resealed = seal_trace({key: value for key, value in trace.items() if key not in {"trace_digest", "trace_serialization"}})
        trace.clear()
        trace.update(resealed)
    run["replay_receipt"]["primary_trace_digest"] = run["sealed_trace"]["trace_digest"]
    run["replay_receipt"]["replay_trace_digest"] = run["replay_receipt"]["replay_trace"]["trace_digest"]
    with pytest.raises(ConformanceError):
        validate_conformance(bundle)


def test_runtime_package_digest_is_not_self_asserted():
    bundle = golden_bundle()
    bundle["main_evaluation"]["agent"]["reset_receipt"]["runtime_package_digest"] = "b" * 64
    with pytest.raises(ConformanceError, match="runtime_package_digest"):
        validate_conformance(bundle)


def test_runtime_package_pin_covers_the_trust_chain_implementation():
    assert {
        "conformance.py",
        "semantic_validator.py",
        "trust_registry.py",
        "trust_evidence.py",
        "golden_backend.py",
        "golden_policy.py",
        "trace_evaluator.py",
    } <= set(RUNTIME_PACKAGE_MEMBERS)
