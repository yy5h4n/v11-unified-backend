"""Cross-object Harness V2 invariants that JSON Schema cannot express."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable


PRIVATE_TOKENS = {
    "canonical_contract", "active_mask", "active_room_time_mask", "evaluator",
    "oracle", "required_rooms", "target_scope", "witness", "noop", "gold",
    "simulator_seed", "selection_stratum", "benchmark_scenario_parameter",
}


class ConformanceError(ValueError):
    pass


def _fail(message: str) -> None:
    raise ConformanceError(message)


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        _fail(f"timestamp lacks offset: {value}")
    return parsed


def _unique(items: Iterable[Any], key, label: str) -> None:
    seen: set[Any] = set()
    for item in items:
        value = key(item)
        if value in seen:
            _fail(f"duplicate {label}: {value}")
        seen.add(value)


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield f"{path}.<key>", str(key)
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def validate_no_private_canary(public_dto: Any) -> None:
    for path, text in _walk(public_dto):
        folded = text.casefold().replace("-", "_").replace(" ", "_")
        if any(token in folded for token in PRIVATE_TOKENS):
            _fail(f"private canary in public DTO at {path}")


def validate_episode_configuration(config: dict[str, Any]) -> None:
    clocks = config["clocks"]
    if _instant(clocks["episode_start"]) >= _instant(clocks["episode_termination"]):
        _fail("episode_start must precede episode_termination")
    explicit = config["track"] == "explicit_profile_control"
    if explicit and (config["budgets"]["clarification_questions"] != 0 or config["clarification_policy"]["enabled"]):
        _fail("explicit track cannot enable clarification")


def validate_agent_view(view: dict[str, Any], audit: dict[str, Any]) -> None:
    validate_no_private_canary(view)
    inventory = view["inventory"]["devices"]
    capabilities = view["capability_catalog"]
    rooms = view["rooms"]
    _unique(inventory, lambda x: x["device_id"], "device_id")
    _unique(capabilities, lambda x: x["capability_id"], "capability_id")
    _unique(rooms, lambda x: x["room_id"], "room_id")
    device_ids = {item["device_id"] for item in inventory}
    capability_ids = {item["capability_id"] for item in capabilities}
    operation_index: set[tuple[str, str]] = set()
    for capability in capabilities:
        _unique(capability["operations"], lambda x: x["name"], f"operation in {capability['capability_id']}")
        for operation in capability["operations"]:
            _unique(operation["parameters"], lambda x: x["name"], f"parameter in {capability['capability_id']}.{operation['name']}")
            operation_index.add((capability["capability_id"], operation["name"]))
    for item in inventory:
        unknown = set(item["capabilities"]) - capability_ids
        if unknown:
            _fail(f"device references unknown capabilities: {sorted(unknown)}")
    assigned: list[str] = []
    for room in rooms:
        unknown = set(room["device_ids"]) - device_ids
        if unknown:
            _fail(f"room references unknown devices: {sorted(unknown)}")
        assigned.extend(room["device_ids"])
    _unique(assigned, lambda x: x, "room device assignment")
    observation_catalog = {item["field"]: item for item in view["observation_catalog"]}
    _unique(view["observation_catalog"], lambda x: x["field"], "observation field")
    _unique(view["observations"], lambda x: x["name"], "observation value")
    for observation in view["observations"]:
        declared = observation_catalog.get(observation["name"])
        if declared is None or declared["unit"] != observation["unit"]:
            _fail(f"observation catalog mismatch: {observation['name']}")
    event_types = set(view["event_catalog"]["event_types"])
    _unique(view["event_catalog"]["filters"], lambda x: x["event_type"], "event filter declaration")
    filter_index = {item["event_type"]: item["fields"] for item in view["event_catalog"]["filters"]}
    if set(filter_index) - event_types:
        _fail("event filter declared for unknown event type")
    _unique(view["events"], lambda x: x["event_id"], "event_id")
    for event in view["events"]:
        if event["type"] not in event_types:
            _fail(f"event has unknown type: {event['type']}")
        declared_fields = {field["name"]: field for field in filter_index.get(event["type"], [])}
        required_fields = {name for name, field in declared_fields.items() if field["required"]}
        if not required_fields <= set(event["payload"]) or set(event["payload"]) - set(declared_fields):
            _fail(f"event payload does not match catalog: {event['event_id']}")
    periods: dict[str, list[tuple[datetime, datetime]]] = {}
    for period in view["named_periods"]:
        start, end = _instant(period["start"]), _instant(period["end"])
        if start >= end:
            _fail(f"named period is empty or reversed: {period['name']}")
        periods.setdefault(period["name"], []).append((start, end))
    for name, occurrences in periods.items():
        ordered = sorted(occurrences)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            _fail(f"named period occurrences overlap: {name}")
    query_context = view["query"].get("context", {"room_refs": [], "named_period_refs": []})
    room_ids = {room["room_id"] for room in rooms}
    unknown_room_refs = set(query_context["room_refs"]) - room_ids
    if unknown_room_refs:
        _fail(f"query context references unknown rooms: {sorted(unknown_room_refs)}")
    unknown_period_refs = set(query_context["named_period_refs"]) - set(periods)
    if unknown_period_refs:
        _fail(f"query context references unknown named periods: {sorted(unknown_period_refs)}")
    for cost in view["costs_budgets"]["action_costs"]:
        if (cost["capability"], cost["operation"]) not in operation_index:
            _fail("action cost references unknown capability operation")
    evidence = {record["evidence_id"]: record for record in audit["evidence_records"]}
    _unique(audit["evidence_records"], lambda x: x["evidence_id"], "evidence_id")
    for pref in view["profile"]["preferences"]:
        record = evidence.get(pref["evidence_id"])
        if record is None or record["source_class"] != pref["provenance"]:
            _fail(f"preference evidence mismatch: {pref['name']}")
    allowed = set(view["tool_schema"]["allowed_actions"])
    if view["track"] == "explicit_profile_control" and "ask_user" in allowed:
        _fail("explicit track exposes ask_user")
    if view["track"] == "interactive_clarification" and "ask_user" not in allowed:
        _fail("interactive track omits ask_user")


def validate_callback(callback: dict[str, Any]) -> None:
    reasons = set(callback["reasons"])
    pairs = {
        "subscribed_public_event": bool(callback["events"]),
        "subscribed_workflow_result": bool(callback["workflow_results"]),
        "user_reply": callback["user_reply"] is not None,
        "episode_termination": bool(callback["termination"]),
    }
    for reason, present in pairs.items():
        if (reason in reasons) != present:
            _fail(f"callback reason/payload mismatch: {reason}")


def validate_turn_exchange(turn: dict[str, Any]) -> None:
    if turn["track"] == "explicit_profile_control" and "question" in turn["terminal_outcome"]:
        _fail("explicit track used clarification")
    if turn["terminal_outcome"].get("kind") == "yielded" and turn["pre_state"] != turn["post_state"]:
        _fail("yield mutated state")
    for inspect in turn["inspects"]:
        if inspect["pre_state"] != inspect["post_state"]:
            _fail("inspect mutated state")


def validate_sealed_session(session: dict[str, Any]) -> None:
    records = session["records"]
    if [r["sequence"] for r in records] != list(range(len(records))):
        _fail("transcript sequence is not contiguous")
    if records[0]["kind"] != "bootstrap_delivered" or records[-1]["kind"] != "termination_notice":
        _fail("transcript must start with bootstrap and end with termination")
    timestamps = [_instant(record["timestamp"]) for record in records]
    if timestamps != sorted(timestamps):
        _fail("transcript timestamps are not monotonic")
    for turn in session["turns"]:
        validate_turn_exchange(turn)


def validate_evaluator_manifest(manifest: dict[str, Any]) -> None:
    components = manifest["loss_components"]
    thresholds = manifest["safety_thresholds"]
    _unique(components, lambda x: x["component_id"], "loss component_id")
    _unique(thresholds, lambda x: x["threshold_id"], "safety threshold_id")
    if not any(Decimal(component["weight"]) > 0 for component in components):
        _fail("at least one loss component must have positive weight")
    for component in components:
        if component["integration"] == "event_sum" and component["active_selector"]["mode"] != "event_selector":
            _fail("event_sum requires event_selector")
        if component["active_selector"]["mode"] != "all_intervals" and not component["active_selector"]["binding"]:
            _fail("active selector requires a binding")


def validate_scenario_group(group: dict[str, Any]) -> None:
    arm_a, arm_b = group["arms"]
    if arm_a["arm_id"] != "A" or arm_b["arm_id"] != "B":
        _fail("arms must be ordered A then B")
    axes_a = arm_a["canonical_contract"]["axes"]
    axes_b = arm_b["canonical_contract"]["axes"]
    differing = {key for key in axes_a if axes_a[key] != axes_b[key]}
    if differing != {group["contrast_axis"]}:
        _fail(f"contract arms differ on {sorted(differing)}, expected only contrast_axis")
    for field in ("family_id", "manifest_version", "implementation_hash"):
        if arm_a["evaluator_manifest"][field] != arm_b["evaluator_manifest"][field]:
            _fail(f"counterfactual evaluator drift: {field}")


def validate_score(manifest: dict[str, Any], main: dict[str, Any]) -> None:
    score = main["score"]
    if main["agent"]["status"] != "eligible" and score["gain_status"] == "eligible":
        _fail("non-eligible agent receipt cannot produce an eligible score")
    if main["noop"]["status"] != "eligible" and score["gain_status"] == "eligible":
        _fail("non-eligible noop receipt cannot produce an eligible score")
    if main["oracle"]["status"] != "eligible" and score["gain_status"] == "eligible":
        _fail("non-eligible oracle receipt cannot produce an eligible score")
    if main["agent"]["status"] == "protocol_invalid" and score["protocol_status"] != "invalid":
        _fail("protocol-invalid agent receipt cannot produce a valid score")
    if main["agent"]["status"] == "unsafe" and score["safety_status"] != "unsafe":
        _fail("unsafe agent receipt cannot produce a safe score")
    if main["oracle"]["status"] in {"oracle_failed", "nonfinite", "policy_failed"} and score["oracle_status"] == "ok":
        _fail("failed oracle receipt cannot produce oracle_status=ok")
    receipt_loss = {name: main[name]["loss"] for name in ("agent", "noop", "oracle")}
    for name, field in (("agent", "agent_loss"), ("noop", "noop_loss"), ("oracle", "oracle_loss")):
        if score[field] != receipt_loss[name]:
            _fail(f"score {field} is not bound to run receipt")
    if score["protocol_status"] != "valid" or score["safety_status"] != "safe" or score["oracle_status"] != "ok":
        if any(score[key] is not None for key in ("raw_gain", "raw_normalized_regret", "display_clipped_gain")):
            _fail("excluded score carries headline gain")
        return
    agent = Decimal(score["agent_loss"])
    noop = Decimal(score["noop_loss"])
    oracle = Decimal(score["oracle_loss"])
    denominator = noop - oracle
    if Decimal(score["denominator"]) != denominator:
        _fail("score denominator mismatch")
    opportunity = Decimal(manifest["opportunity_gap_threshold"])
    if denominator <= 0:
        expected = "nonpositive_denominator_excluded"
    elif denominator < opportunity:
        expected = "below_threshold_excluded"
    elif denominator == opportunity:
        expected = "boundary_equal_threshold_excluded"
    else:
        expected = "eligible"
    if score["gain_status"] != expected:
        _fail(f"gain branch mismatch: expected {expected}")
    if expected != "eligible":
        if any(score[key] is not None for key in ("raw_gain", "raw_normalized_regret", "display_clipped_gain")):
            _fail("boundary score carries headline gain")
        return
    gain = (noop - agent) / denominator
    regret = (agent - oracle) / denominator
    if Decimal(score["raw_gain"]) != gain or Decimal(score["raw_normalized_regret"]) != regret:
        _fail("normalized score formula mismatch")
    clipped = min(Decimal(1), max(Decimal(0), gain))
    if Decimal(score["display_clipped_gain"]) != clipped:
        _fail("display clipped gain mismatch")
    expected_pass = gain >= Decimal(manifest["main_gain_threshold"])
    if score["main_gain_pass"] is not expected_pass:
        _fail("main gain threshold mismatch")


def validate_evaluation_bundle(bundle: dict[str, Any]) -> None:
    validate_evaluator_manifest(bundle["evaluator_manifest"])
    validate_scenario_group(bundle["scenario_group"])
    main = bundle["main_evaluation"]
    controls = [main[name]["controls"] for name in ("agent", "noop", "oracle")]
    if not controls[0] == controls[1] == controls[2]:
        _fail("agent/noop/oracle controls differ")
    validate_score(bundle["evaluator_manifest"], main)
    for name in ("agent", "noop", "oracle"):
        validate_sealed_session(main[name]["sealed_session"])
    diagnostics = bundle["diagnostics"]["runs"]
    _unique(diagnostics, lambda x: x["run"]["run_id"], "diagnostic run_id")
    roles = {(run["arm_id"], run["query_role"], run["assignment"]) for run in diagnostics}
    required = {
        ("A", "source_near", "own_arm"), ("B", "source_near", "own_arm"),
        ("A", "faithful_paraphrase", "own_arm"), ("B", "faithful_paraphrase", "own_arm"),
        ("A", "deletion", "deleted"), ("B", "deletion", "deleted"),
        ("A", "cross_arm_shuffle", "arm_B_query_on_A"),
        ("B", "cross_arm_shuffle", "arm_A_query_on_B"),
    }
    if not required <= roles:
        _fail("diagnostic closed-loop matrix is incomplete")
    frozen = controls[0]
    for diagnostic in diagnostics:
        if diagnostic["run"]["controls"] != frozen:
            _fail("diagnostic run changed frozen controls")
