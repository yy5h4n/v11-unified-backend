"""Single fail-closed entrypoint for Harness V2 artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .semantic_validator import (
    ConformanceError,
    validate_episode_configuration,
    validate_evaluation_bundle,
    validate_agent_view,
    validate_no_private_canary,
)
from .trace_evaluator import evaluate_trace
from .trust_evidence import verify_manifest, verify_policy_execution_receipt, verify_reset_receipt
from .trust_registry import TrustRegistry, TrustedRuntime


SCHEMA_DIR = Path(__file__).resolve().parent
PUBLIC_RECORD_KINDS = {
    "bootstrap_delivered", "callback_delivered", "inspect_response",
    "tool_outcome", "clarification_reply", "protocol_error", "termination_notice",
}


def _schemas() -> tuple[dict[str, dict[str, Any]], Registry]:
    documents = [json.loads(path.read_text()) for path in SCHEMA_DIR.glob("*.json")]
    by_name = {document["$id"].rsplit("/", 1)[-1]: document for document in documents}
    registry = Registry().with_resources(
        (document["$id"], Resource.from_contents(document)) for document in documents
    )
    return by_name, registry


def validate_schema(instance: Any, schema_name: str, definition: str | None = None) -> None:
    schemas, registry = _schemas()
    schema = schemas[schema_name]
    target = {"$schema": schema["$schema"], "$ref": schema["$id"]}
    if definition:
        target["$ref"] += f"#/$defs/{definition}"
    errors = sorted(
        Draft202012Validator(target, registry=registry, format_checker=FormatChecker()).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        locations = ", ".join(f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:5])
        raise ConformanceError(f"schema validation failed: {locations}")


def decode_canonical_payload(payload: dict[str, Any]) -> Any:
    try:
        raw = base64.b64decode(payload["canonical_bytes_base64"], validate=True)
        decoded = json.loads(raw)
    except Exception as exc:
        raise ConformanceError("invalid canonical payload encoding") from exc
    if rfc8785.dumps(decoded) != raw:
        raise ConformanceError("payload bytes are not RFC8785 canonical JSON")
    if hashlib.sha256(raw).hexdigest() != payload["sha256"]:
        raise ConformanceError("canonical payload digest mismatch")
    return decoded


def verify_canonical_serialization(value: Any, record: dict[str, Any], label: str) -> None:
    """Recompute a canonical serialization record from the normative value."""
    try:
        raw = base64.b64decode(record["canonical_utf8_base64"], validate=True)
    except Exception as exc:
        raise ConformanceError(f"invalid {label} canonical serialization encoding") from exc
    expected = rfc8785.dumps(value)
    if raw != expected:
        raise ConformanceError(f"{label} canonical serialization bytes mismatch")
    if hashlib.sha256(raw).hexdigest() != record["digest"]:
        raise ConformanceError(f"{label} canonical serialization digest mismatch")


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ConformanceError("nonfinite derived metric")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def _digest_value(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _public_control_digests(bootstrap: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    initial = {key: value for key, value in bootstrap.items() if key != "query"}
    horizon = {"clocks": config["clocks"], "horizon": bootstrap["horizon"]}
    return {
        "initial_public_state": _digest_value(initial),
        "inventory": _digest_value(bootstrap["inventory"]),
        "costs": _digest_value(bootstrap["costs_budgets"]),
        "budgets": _digest_value(config["budgets"]),
        "horizon": _digest_value(horizon),
    }


def _validate_transaction_trace_binding(session: dict[str, Any], trace: dict[str, Any]) -> None:
    exchanges: list[dict[str, Any]] = []
    expected_immediate: dict[str, dict[str, Any]] = {}
    installed_rules: dict[str, tuple[str, dict[str, Any]]] = {}
    rejected_rule_ids: set[str] = set()
    for turn in session["turns"]:
        choice = decode_canonical_payload(turn["terminal_choice"])
        if choice["kind"] != "commit_transaction":
            continue
        exchange = turn["terminal_outcome"]
        exchanges.append(exchange)
        if exchange["pre_state"]["backend_state_digest"] != turn["pre_state"]["backend"] or exchange["post_state"]["backend_state_digest"] != turn["post_state"]["backend"]:
            raise ConformanceError("transaction exchange backend state is not bound to turn state")
        if _digest_value(exchange["pre_state"]["accounting_ledger"]) != turn["pre_state"]["accounting"] or _digest_value(exchange["post_state"]["accounting_ledger"]) != turn["post_state"]["accounting"]:
            raise ConformanceError("transaction exchange accounting ledger is not bound to turn state")
        if exchange["strict_outcome"]["status"] == "committed":
            committed = {item["mutation_id"] for item in exchange["strict_outcome"]["mutation_outcomes"] if item["mutation_kind"] == "command" and item["status"] == "committed"}
            for command in exchange["request"]["act_now"]["commands"]:
                if command["command_id"] in committed:
                    expected_immediate[command["command_id"]] = command
            for creation in exchange["request"]["create_rules"]:
                rule = creation["rule"]
                installed_rules[rule["rule_id"]] = (exchange["request"]["transaction_id"], rule)
        else:
            if turn["pre_state"]["backend"] != turn["post_state"]["backend"] or turn["pre_state"]["rules"] != turn["post_state"]["rules"]:
                raise ConformanceError("rejected transaction changed backend or rule state")
            if any(item["status"] != "not_applied" for item in exchange["strict_outcome"]["mutation_outcomes"]):
                raise ConformanceError("rejected transaction contains an applied mutation")
            rejected_rule_ids.update(item["rule"]["rule_id"] for item in exchange["request"]["create_rules"])
    if trace["transaction_exchanges"] != exchanges:
        raise ConformanceError("physical trace transaction exchanges differ from sealed session")
    actual_immediate: dict[str, dict[str, Any]] = {}
    for frame in trace["frames"]:
        for command in frame["applied_commands"]:
            if command["source"] == "agent_immediate" and command["status"] == "committed":
                actual_immediate[command["command_id"]] = command
    if set(actual_immediate) != set(expected_immediate):
        raise ConformanceError("session immediate commands and physical trace commands differ")
    for command_id, request in expected_immediate.items():
        actual = actual_immediate[command_id]
        for field in ("device_id", "capability", "operation", "parameters"):
            if actual[field] != request[field]:
                raise ConformanceError(f"applied command differs from transaction request: {command_id}.{field}")
    events = [event for frame in trace["frames"] for event in frame["rule_events"]]
    installed_events = {event["rule_id"]: event for event in events if event["type"] == "installed"}
    if set(installed_events) != set(installed_rules) or (rejected_rule_ids - set(installed_rules)) & set(installed_events):
        raise ConformanceError("rule installation events do not match committed transactions")
    for rule_id, (transaction_id, rule) in installed_rules.items():
        event = installed_events[rule_id]
        if event["origin"] != {"kind": "installation_transaction", "id": transaction_id}:
            raise ConformanceError("rule installation lineage mismatch")
        expected_firing = {command["command_id"]: command for command in rule["commands"]}
        expected_release = {command["command_id"]: command for command in rule["on_release_commands"]}
        firing_records = [command for frame in trace["frames"] for command in frame["applied_commands"] if command["source"] == "rule_firing" and command["origin"]["rule_id"] == rule_id]
        release_records = [command for frame in trace["frames"] for command in frame["applied_commands"] if command["source"] == "rule_release" and command["origin"]["rule_id"] == rule_id]
        if {item["command_id"] for item in firing_records} != set(expected_firing) or {item["command_id"] for item in release_records} != set(expected_release):
            raise ConformanceError("rule firing or release commands do not match installed rule")
        for record in firing_records:
            if record["origin"]["installation_transaction_id"] != transaction_id:
                raise ConformanceError("rule firing lost installation transaction lineage")
            expected = expected_firing[record["command_id"]]
            if any(record[field] != expected[field] for field in ("device_id", "capability", "operation", "parameters")):
                raise ConformanceError("rule firing command differs from installed rule")
            matching = [event for event in events if event["type"] == "firing_committed" and event["rule_id"] == rule_id and event["trigger_occurrence_id"] == record["origin"]["trigger_occurrence_id"] and record["command_id"] in event["command_ids"]]
            if len(matching) != 1:
                raise ConformanceError("rule firing occurrence lineage is incomplete")
        for record in release_records:
            if record["origin"]["installation_transaction_id"] != transaction_id:
                raise ConformanceError("rule release lost installation transaction lineage")
            expected = expected_release[record["command_id"]]
            if any(record[field] != expected[field] for field in ("device_id", "capability", "operation", "parameters")):
                raise ConformanceError("rule release command differs from installed rule")
            retirement = record["origin"]["retirement_occurrence_id"]
            matching = [event for event in events if event["type"] in {"expired", "released"} and event["rule_id"] == rule_id and event["origin"] == {"kind": "retirement_occurrence", "id": retirement}]
            if {event["type"] for event in matching} != {"expired", "released"}:
                raise ConformanceError("rule release retirement lineage is incomplete")


def _validate_replay_receipt(receipt: dict[str, Any], runtime: TrustedRuntime, native: dict[str, Any], manifest: dict[str, Any]) -> None:
    proof = receipt["replay_receipt"]
    primary = receipt["sealed_trace"]
    replay = proof["replay_trace"]
    replay_body = {key: value for key, value in replay.items() if key not in {"trace_digest", "trace_serialization"}}
    verify_canonical_serialization(replay_body, replay["trace_serialization"], "replay trace")
    if replay["trace_digest"] != replay["trace_serialization"]["digest"]:
        raise ConformanceError("replay trace digest mismatch")
    if proof["initial_public_state_digest"] != receipt["controls"]["initial_public_state"] or proof["session_transcript_digest"] != receipt["sealed_session"]["transcript_digest"]:
        raise ConformanceError("replay receipt is not bound to initial state and session")
    if proof["primary_trace_digest"] != primary["trace_digest"] or proof["replay_trace_digest"] != replay["trace_digest"]:
        raise ConformanceError("replay receipt trace digest binding mismatch")
    if replay != primary:
        raise ConformanceError("deterministic replay differs from primary physical trace")
    if proof["verifier_id"] != runtime.runtime_id or proof["verifier_version"] != runtime.version:
        raise ConformanceError("untrusted replay verifier")
    if proof["verifier_hash"] != runtime.package_digest:
        raise ConformanceError("replay verifier implementation hash mismatch")
    if proof["backend_build_digest"] != runtime.backend_package_digest:
        raise ConformanceError("replay backend build digest mismatch")
    if proof["adapter_build_digest"] != runtime.adapter_package_digest:
        raise ConformanceError("replay adapter build digest mismatch")
    session = receipt["sealed_session"]
    try:
        expected = runtime.replay(native, session, primary["arm_id"], primary["query_digest"], manifest)
    except Exception as exc:
        raise ConformanceError("trusted deterministic replay failed") from exc
    if replay != expected:
        raise ConformanceError("trusted deterministic replay recomputation mismatch")


def _verify_contract_serialization(contract: dict[str, Any], arm_id: str) -> None:
    for axis, serialized_value in contract["axes"].items():
        try:
            raw = base64.b64decode(serialized_value["canonical_utf8_base64"], validate=True)
            decoded = json.loads(raw)
        except Exception as exc:
            raise ConformanceError(f"invalid arm {arm_id} axis serialization: {axis}") from exc
        verify_canonical_serialization(decoded, {
            "algorithm": "RFC8785_JCS_UTF8",
            "canonical_utf8_base64": serialized_value["canonical_utf8_base64"],
            "digest": serialized_value["digest"],
        }, f"arm {arm_id} axis {axis}")
    body = {key: value for key, value in contract.items() if key not in {"contract_digest", "serialization"}}
    verify_canonical_serialization(body, contract["serialization"], f"arm {arm_id} contract")
    if contract["contract_digest"] != contract["serialization"]["digest"]:
        raise ConformanceError(f"arm {arm_id} contract digest mismatch")


def _verify_policy_separation(bundle: dict[str, Any], evaluated_diagnostics: list[tuple[dict[str, Any], Decimal]]) -> None:
    manifest = bundle["evaluator_manifest"]
    main = bundle["main_evaluation"]
    denominator = Decimal(main["noop"]["loss"]) - Decimal(main["oracle"]["loss"])
    if denominator <= 0:
        raise ConformanceError("language separation requires a positive noop-oracle denominator")
    by_arm: dict[str, dict[str, Decimal]] = {"A": {}, "B": {}}
    for diagnostic, loss in evaluated_diagnostics:
        role = diagnostic["query_role"]
        if role in by_arm[diagnostic["arm_id"]]:
            raise ConformanceError("duplicate diagnostic role within arm")
        by_arm[diagnostic["arm_id"]][role] = loss
    oracle_loss = Decimal(main["oracle"]["loss"])
    arm_margins: dict[str, str] = {}
    for arm_id, losses in by_arm.items():
        required = {"source_near", "faithful_paraphrase", "deletion", "cross_arm_shuffle"}
        if set(losses) != required:
            raise ConformanceError(f"language diagnostic roles incomplete for arm {arm_id}")
        gains = {role: (Decimal(main["noop"]["loss"]) - loss) / denominator for role, loss in losses.items()}
        own_worst = min(gains["source_near"], gains["faithful_paraphrase"])
        adversarial_best = max(gains["deletion"], gains["cross_arm_shuffle"])
        margin = own_worst - adversarial_best
        if own_worst < Decimal(manifest["main_gain_threshold"]):
            raise ConformanceError(f"own-arm language strategy fails main gain threshold for arm {arm_id}")
        if margin < Decimal(manifest["policy_separation_threshold"]):
            raise ConformanceError(f"language policy separation below threshold for arm {arm_id}")
        arm_margins[arm_id] = _canonical_decimal(margin)
    aggregate = min(Decimal(value) for value in arm_margins.values())
    supplied = bundle["diagnostics"]["policy_separation"]
    expected = {
        "normalization": "noop_minus_oracle",
        "threshold": manifest["policy_separation_threshold"],
        "arm_margins": arm_margins,
        "aggregate_min_margin": _canonical_decimal(aggregate),
        "pass": True,
    }
    if supplied != expected:
        raise ConformanceError("policy separation evidence is not recomputed from diagnostic traces")


def validate_session_bytes(session: dict[str, Any]) -> None:
    validate_episode_configuration(session["episode_configuration"])
    config_bytes = rfc8785.dumps(session["episode_configuration"])
    if hashlib.sha256(config_bytes).hexdigest() != session["episode_configuration_digest"]:
        raise ConformanceError("episode configuration digest mismatch")
    bootstrap = decode_canonical_payload(session["bootstrap"])
    validate_schema(bootstrap, "public_private_schema_v2.json", "agent_view")
    validate_agent_view(bootstrap, session["bootstrap_audit"])
    validate_no_private_canary(bootstrap)
    for record in session["records"]:
        decoded = decode_canonical_payload(record["payload"])
        if record["kind"] in PUBLIC_RECORD_KINDS:
            validate_no_private_canary(decoded)
    if session["records"][0]["payload"] != session["bootstrap"]:
        raise ConformanceError("sealed session bootstrap differs from transcript bootstrap record")
    canonical_records = rfc8785.dumps(session["records"])
    if hashlib.sha256(canonical_records).hexdigest() != session["transcript_digest"]:
        raise ConformanceError("transcript digest mismatch")
    records = session["records"]
    for left, right in zip(records, records[1:]):
        if left["post_state"] != right["pre_state"]:
            raise ConformanceError("transcript state chain is discontinuous")
    kinds = [record["kind"] for record in records]
    turn_count = len(session["turns"])
    if kinds.count("bootstrap_delivered") != 1 or kinds.count("termination_notice") != 1:
        raise ConformanceError("transcript bootstrap/termination cardinality mismatch")
    if kinds.count("callback_delivered") != turn_count or kinds.count("terminal_choice") != turn_count:
        raise ConformanceError("turn/transcript cardinality mismatch")
    terminal_outcomes = sum(kinds.count(kind) for kind in ("tool_outcome", "clarification_reply", "yield", "protocol_error"))
    if terminal_outcomes != turn_count:
        raise ConformanceError("terminal outcome transcript cardinality mismatch")
    callback_records = [record for record in records if record["kind"] == "callback_delivered"]
    choice_records = [record for record in records if record["kind"] == "terminal_choice"]
    outcome_records = [record for record in records if record["kind"] in {"tool_outcome", "clarification_reply", "yield", "protocol_error"}]
    committed_wakes: dict[str, str] = {}
    consumed_wakes: set[str] = set()
    callback_ids: set[str] = set()
    inspect_count = 0
    mutation_count = 0
    clarification_count = 0
    for index, turn in enumerate(session["turns"]):
        if turn["track"] != session["episode_configuration"]["track"]:
            raise ConformanceError("turn track differs from episode configuration")
        callback = decode_canonical_payload(turn["callback"])
        validate_no_private_canary(callback)
        if set(callback) != {"callback_id", "reason", "timestamp", "backend_state_digest", "wake_ids"}:
            raise ConformanceError("callback envelope is incomplete or contains unknown fields")
        if callback["callback_id"] in callback_ids:
            raise ConformanceError("duplicate callback id")
        callback_ids.add(callback["callback_id"])
        if callback["reason"] not in session["episode_configuration"]["callback_policy"]["allowed_reasons"]:
            raise ConformanceError("callback reason is not allowed by episode configuration")
        callback_record = callback_records[index]
        if callback_record["payload"] != turn["callback"] or callback_record["post_state"] != turn["pre_state"]:
            raise ConformanceError("turn callback/state is not the ordered transcript callback result")
        if callback["backend_state_digest"] != turn["pre_state"]["backend"]:
            raise ConformanceError("callback backend digest differs from post-phase turn state")
        if index == 0:
            if callback["reason"] != "episode_reset" or callback["wake_ids"]:
                raise ConformanceError("first callback must be the reset without wake lineage")
        elif callback["reason"] == "episode_reset":
            raise ConformanceError("episode reset callback may occur only once")
        if callback["reason"] == "requested_wake_time":
            if not callback["wake_ids"] or len(callback["wake_ids"]) != len(set(callback["wake_ids"])):
                raise ConformanceError("wake callback must name unique committed wake ids")
            for wake_id in callback["wake_ids"]:
                if committed_wakes.get(wake_id) != callback["timestamp"] or wake_id in consumed_wakes:
                    raise ConformanceError("wake callback lacks an unconsumed committed wake request")
                consumed_wakes.add(wake_id)
            for field in ("backend", "rules", "subscriptions", "accounting"):
                if callback_record["pre_state"][field] != callback_record["post_state"][field]:
                    raise ConformanceError("wake delivery changed non-wake runtime state")
            if callback_record["pre_state"]["wake_queue"] == callback_record["post_state"]["wake_queue"]:
                raise ConformanceError("wake delivery did not consume the committed wake queue")
        elif callback["wake_ids"]:
            raise ConformanceError("non-wake callback carries wake lineage")
        choice = decode_canonical_payload(turn["terminal_choice"])
        choice_record = choice_records[index]
        if choice_record["payload"] != turn["terminal_choice"] or choice_record["pre_state"] != turn["pre_state"] or choice_record["post_state"] != turn["pre_state"]:
            raise ConformanceError("terminal choice is not the ordered read-only transcript record")
        validate_schema(choice, "interaction_state_machine_v2.json", "terminalChoice")
        inspect_count += len(turn["inspects"])
        outcome = turn["terminal_outcome"]
        kind = choice["kind"]
        if kind == "commit_transaction":
            mutation_count += 1
            if "request" not in outcome or outcome["request"] != choice["transaction"]:
                raise ConformanceError("transaction choice/outcome mismatch")
            if outcome["strict_outcome"]["status"] == "committed":
                for wake in choice["transaction"]["wake_requests"]:
                    if wake["wake_id"] in committed_wakes:
                        raise ConformanceError("committed wake id is reused")
                    committed_wakes[wake["wake_id"]] = wake["at"]
        elif kind == "ask_user":
            clarification_count += 1
            if turn["track"] != "interactive_clarification" or "question" not in outcome:
                raise ConformanceError("clarification is illegal for this track/outcome")
            if decode_canonical_payload(outcome["question"]) != choice["question"]:
                raise ConformanceError("clarification choice/outcome mismatch")
        elif kind == "yield_without_mutation":
            if outcome != {"kind": "yielded"} or turn["pre_state"] != turn["post_state"]:
                raise ConformanceError("yield choice/outcome mismatch or mutation")
        outcome_record = outcome_records[index]
        if decode_canonical_payload(outcome_record["payload"]) != outcome or outcome_record["pre_state"] != turn["pre_state"] or outcome_record["post_state"] != turn["post_state"]:
            raise ConformanceError("turn outcome/state is not the ordered transcript outcome")
    termination = datetime.fromisoformat(session["episode_configuration"]["clocks"]["episode_termination"])
    unconsumed_due = [wake_id for wake_id, at in committed_wakes.items() if datetime.fromisoformat(at) < termination and wake_id not in consumed_wakes]
    if unconsumed_due:
        raise ConformanceError("committed pre-termination wake lacks exactly-once callback delivery")
    budgets = session["episode_configuration"]["budgets"]
    if len(session["turns"]) > budgets["agent_callbacks"] or inspect_count > budgets["inspect_calls"] or mutation_count > budgets["mutation_transactions"] or clarification_count > budgets["clarification_questions"]:
        raise ConformanceError("session exceeds declared interaction budget")


def validate_conformance(bundle: dict[str, Any], trust_registry: TrustRegistry | None = None) -> None:
    """Validate a complete release unit; partial validators are never release gates."""
    validate_schema(bundle, "evaluation_bundle_v2.json")
    if trust_registry is None:
        from .golden_scenario import default_registry
        trust_registry = default_registry()
    try:
        runtime = trust_registry.require(bundle["runtime_id"])
    except ValueError as exc:
        raise ConformanceError(str(exc)) from exc
    if bundle["semantic_verifier_digest"] != runtime.package_digest:
        raise ConformanceError("semantic verifier is not the selected trusted runtime package")
    main = bundle["main_evaluation"]
    group = bundle["scenario_group"]
    arm_manifests = {arm["arm_id"]: arm["evaluator_manifest"] for arm in group["arms"]}
    verify_manifest(bundle["evaluator_manifest"], runtime)
    for manifest in arm_manifests.values():
        verify_manifest(manifest, runtime)
    receipts = [main[name] for name in ("agent", "noop", "oracle")]
    receipts.extend(item["run"] for item in bundle["diagnostics"]["runs"])
    seen_run_ids: set[str] = set()
    evaluated_diagnostics: list[tuple[dict[str, Any], Decimal]] = []
    for receipt in receipts:
        if receipt["run_id"] in seen_run_ids:
            raise ConformanceError("duplicate run receipt id")
        seen_run_ids.add(receipt["run_id"])
        validate_session_bytes(receipt["sealed_session"])
        session = receipt["sealed_session"]
        trace = receipt["sealed_trace"]
        if receipt["policy_build_digest"] != session["agent_build_digest"] or receipt["policy_config_digest"] != session["policy_config_digest"]:
            raise ConformanceError("run receipt policy identity is not bound to sealed session")
        trace_body = {key: value for key, value in trace.items() if key not in {"trace_digest", "trace_serialization"}}
        verify_canonical_serialization(trace_body, trace["trace_serialization"], "sealed trace")
        if trace["trace_digest"] != trace["trace_serialization"]["digest"]:
            raise ConformanceError("trace digest mismatch")
        bootstrap = decode_canonical_payload(session["bootstrap"])
        native = verify_reset_receipt(receipt["reset_receipt"], bootstrap, runtime, expected_seed_digest=receipt["controls"]["simulator_seed"], expected_exogenous_realization_digest=receipt["controls"]["exogenous_realization"])
        callbacks = [decode_canonical_payload(turn["callback"]) for turn in session["turns"]]
        terminal_choices = [decode_canonical_payload(turn["terminal_choice"]) for turn in session["turns"]]
        verify_policy_execution_receipt(receipt["policy_execution_receipt"], runtime, agent_view=bootstrap, callbacks=callbacks, terminal_choices=terminal_choices)
        if receipt["policy_build_digest"] != receipt["policy_execution_receipt"]["policy_package_digest"] or receipt["policy_config_digest"] != receipt["policy_execution_receipt"]["policy_config_digest"]:
            raise ConformanceError("run policy identity differs from trusted policy execution receipt")
        expected_controls = _public_control_digests(bootstrap, session["episode_configuration"])
        for name, expected_digest in expected_controls.items():
            if receipt["controls"][name] != expected_digest:
                raise ConformanceError(f"frozen control digest is not recomputed from public bootstrap: {name}")
        query_digest = _digest_value(bootstrap["query"])
        if query_digest != trace["query_digest"]:
            raise ConformanceError("session query is not bound to physical trace")
        if session["episode_configuration"]["episode_id"] != trace["episode_id"]:
            raise ConformanceError("session/trace episode mismatch")
        if session["episode_configuration"]["track"] != receipt["controls"]["track"]:
            raise ConformanceError("session track differs from frozen run controls")
        if trace["start_inclusive"] != session["episode_configuration"]["clocks"]["episode_start"] or trace["termination_exclusive"] != session["episode_configuration"]["clocks"]["episode_termination"]:
            raise ConformanceError("session clocks are not bound to physical trace")
        if trace["scenario_group_id"] != bundle["scenario_group"]["group_id"]:
            raise ConformanceError("trace scenario group mismatch")
        selected_manifest = arm_manifests[trace["arm_id"]]
        if trace["manifest_id"] != selected_manifest["manifest_id"] or trace["manifest_version"] != selected_manifest["manifest_version"]:
            raise ConformanceError("trace evaluator manifest mismatch")
        if receipt["controls"]["simulator_seed"] != trace["seed_digest"] or receipt["controls"]["exogenous_realization"] != trace["exogenous_realization_digest"]:
            raise ConformanceError("run controls are not bound to trace")
        _validate_transaction_trace_binding(session, trace)
        _validate_replay_receipt(receipt, runtime, native, selected_manifest)
        evaluated = evaluate_trace(arm_manifests[trace["arm_id"]], trace)
        if receipt["role"] == "diagnostic" and receipt["status"] != "eligible":
            raise ConformanceError("language diagnostic run is not eligible")
        if receipt["status"] == "eligible" and (receipt["loss"] is None or Decimal(receipt["loss"]) != evaluated.loss):
            raise ConformanceError("run receipt loss is not recomputed from trace")
        if receipt["status"] == "eligible" and not evaluated.safety_pass:
            raise ConformanceError("unsafe trace cannot have eligible receipt status")
        if receipt["role"] == "diagnostic":
            diagnostic = next(item for item in bundle["diagnostics"]["runs"] if item["run"] is receipt)
            evaluated_diagnostics.append((diagnostic, evaluated.loss))
    control_bindings = {
        "initial_public_state": "initial_public_state_digest",
        "inventory": "inventory_digest",
        "simulator_seed": "simulator_seed_digest",
        "exogenous_realization": "exogenous_realization_digest",
        "costs": "costs_digest",
        "budgets": "budgets_digest",
        "horizon": "horizon_policy_digest",
    }
    for receipt in receipts:
        for control_name, group_name in control_bindings.items():
            if receipt["controls"][control_name] != group[group_name]:
                raise ConformanceError(f"run control differs from scenario group: {control_name}")
    for arm in group["arms"]:
        _verify_contract_serialization(arm["canonical_contract"], arm["arm_id"])
        for field in ("manifest_version", "family_id", "implementation_hash"):
            if arm["evaluator_manifest"][field] != bundle["evaluator_manifest"][field]:
                raise ConformanceError(f"arm evaluator differs from top-level manifest: {field}")
    source_counts = {"A": 0, "B": 0}
    for assignment in group["query_registry"].values():
        if assignment["role"] == "source_near":
            source_counts[assignment["arm_id"]] += 1
    if source_counts != {"A": 1, "B": 1}:
        raise ConformanceError("query registry must contain exactly one source-near query per arm")
    axes_a = group["arms"][0]["canonical_contract"]["axes"]
    axes_b = group["arms"][1]["canonical_contract"]["axes"]
    contrast = group["contrast_axis"]
    witness = group["contrast_witness"]
    if witness["arm_a_axis_value_digest"] != axes_a[contrast]["digest"] or witness["arm_b_axis_value_digest"] != axes_b[contrast]["digest"]:
        raise ConformanceError("contrast witness is not bound to serialized axis values")
    if axes_a[contrast]["canonical_utf8_base64"] == axes_b[contrast]["canonical_utf8_base64"]:
        raise ConformanceError("contrast axis canonical values do not differ")
    for axis in set(axes_a) - {contrast}:
        if axes_a[axis] != axes_b[axis]:
            raise ConformanceError(f"noncontrast axis serialization differs: {axis}")
    registry = bundle["scenario_group"]["query_registry"]
    diagnostic_policy_identities = {
        (item["run"]["policy_build_digest"], item["run"]["policy_config_digest"])
        for item in bundle["diagnostics"]["runs"]
    }
    if len(diagnostic_policy_identities) != 1:
        raise ConformanceError("language diagnostics changed policy build or configuration")
    main_policy_identity = (main["agent"]["policy_build_digest"], main["agent"]["policy_config_digest"])
    if diagnostic_policy_identities != {main_policy_identity}:
        raise ConformanceError("language diagnostics did not run the main Agent policy")
    for diagnostic in bundle["diagnostics"]["runs"]:
        receipt = diagnostic["run"]
        if receipt["sealed_trace"]["arm_id"] != diagnostic["arm_id"]:
            raise ConformanceError("diagnostic arm is not bound to its physical trace")
        bootstrap = decode_canonical_payload(receipt["sealed_session"]["bootstrap"])
        query_surface = bootstrap["query"]
        text = query_surface["text"]
        computed = _digest_value(query_surface)
        if computed != diagnostic["query_digest"] or computed != receipt["sealed_trace"]["query_digest"]:
            raise ConformanceError("diagnostic query digest mismatch")
        if diagnostic["query_role"] == "deletion":
            empty_context = query_surface.get("context", {"room_refs": [], "named_period_refs": []})
            if text != "" or empty_context != {"room_refs": [], "named_period_refs": []} or diagnostic["assignment"] != "deleted":
                raise ConformanceError("query deletion run is not an actual deletion")
        else:
            assignment = registry.get(computed)
            if assignment is None:
                raise ConformanceError("diagnostic query is absent from registry")
            if diagnostic["query_role"] in {"source_near", "faithful_paraphrase"}:
                if assignment != {"arm_id": diagnostic["arm_id"], "role": diagnostic["query_role"]}:
                    raise ConformanceError("own-arm query assignment mismatch")
            elif diagnostic["query_role"] == "cross_arm_shuffle" and assignment["arm_id"] == diagnostic["arm_id"]:
                raise ConformanceError("query shuffle did not cross arms")
    main_arm_ids = {main[name]["sealed_trace"]["arm_id"] for name in ("agent", "noop", "oracle")}
    if len(main_arm_ids) != 1:
        raise ConformanceError("agent/noop/oracle traces do not share one arm")
    main_query_assignment = registry.get(main["agent"]["sealed_trace"]["query_digest"])
    if main_query_assignment != {"arm_id": next(iter(main_arm_ids)), "role": "source_near"}:
        raise ConformanceError("main evaluation query is not the source-near query for its trace arm")
    agent_eval = evaluate_trace(bundle["evaluator_manifest"], main["agent"]["sealed_trace"])
    supplied_metrics = {key: Decimal(value) for key, value in main["score"]["component_metrics"].items()}
    if supplied_metrics != agent_eval.component_metrics:
        raise ConformanceError("score component metrics are not recomputed from trace")
    score_body = {key: value for key, value in main["score"].items() if key != "serialization"}
    verify_canonical_serialization(score_body, main["score"]["serialization"], "score")
    _verify_policy_separation(bundle, evaluated_diagnostics)
    validate_evaluation_bundle(bundle)
