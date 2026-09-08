import json
import shutil
from copy import deepcopy

import pytest

from evaluate_harness_v2_v4_flash import BATCH, build_messages, evaluate_one, preflight_batch, public_action_protocol, score_run, smoke_accepted, validate_public_action
from harness_v2.core import EpisodeSpec, RunArtifact
from harness_v2.simuhome_adapter import SimuHomeHarnessAdapter


class FakeClient:
    def __init__(self, response='{"kind":"wait"}', error=None):
        self.response = response
        self.error = error
        self.messages = []

    def complete(self, messages):
        self.messages.append(messages)
        if self.error:
            raise self.error
        return {"content": self.response, "usage": {"total_tokens": 3}, "latency_ms": 1.0}


@pytest.fixture(scope="module")
def pair():
    public, private, _ = preflight_batch()
    return public[0], private[public[0]["episode_id"]]


def test_wait_run_is_leakage_free_and_has_exact_cadence(pair):
    public, private = pair
    private = {**private, "private_sentinel": "NEVER_SEND_PRIVATE_SENTINEL"}
    client = FakeClient()
    result = evaluate_one(public, private, client)
    assert result["status"] == "completed"
    assert result["calls"] == public["agent_view"]["horizon_steps"]
    assert result["observation_count"] == result["calls"] + 1
    wire = json.dumps(client.messages)
    for forbidden in ("NEVER_SEND_PRIVATE_SENTINEL", public["responsibility_id"], public["query_surface"]["query_id"], "oracle_loss", "noop_loss", "validation", "future_trace"):
        assert forbidden not in wire
    payloads = [json.loads(messages[1]["content"]) for messages in client.messages]
    assert payloads[0]["current_observation"]["step"] == 0
    assert payloads[-1]["current_observation"]["step"] == result["calls"] - 1
    assert payloads[0]["previous_action_summary"] is None
    assert payloads[1]["previous_action_summary"]["status"] == "accepted"
    assert "mode" in payloads[0]["current_observation"]["devices"]["kitchen"]
    assert "active_rules" in payloads[0]["current_observation"]


def test_sparse_act_is_accepted_and_advances(pair):
    public, private = pair
    device = public["initial_observation"]["devices"]["kitchen"]["device_id"]
    response = json.dumps({"kind": "act", "commands": [{"device_id": device, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "auto", "target_c": 22.0}}]})
    result = evaluate_one(public, private, FakeClient(response))
    assert result["status"] == "completed"
    assert result["valid_actions"] == result["calls"]


def test_repeated_golden_heat_pump_command_is_coalesced_and_energy_proxy_is_scored(pair):
    public, private = pair
    device = public["initial_observation"]["devices"]["kitchen"]
    assert device["device_type"] == "heat_pump"
    response = json.dumps({"kind": "act", "commands": [{
        "device_id": device["device_id"],
        "capability": "thermal.control",
        "operation": "set",
        "parameters": {"mode": "auto", "target_c": 22.0},
    }]})
    result = evaluate_one(public, private, FakeClient(response))
    effects = result["score"]["command_effects"]
    energy = result["score"]["simulator_energy_proxy_wh"]
    assert result["status"] == "completed"
    assert effects["committed_state_changes"] == 1
    assert effects["coalesced_redundant_writes"] == result["calls"] - 1
    assert energy["availability"] == "available"
    # This golden starts above 22 C, so auto/22 resolves to off: zero is a
    # measured simulator proxy outcome, not a missing energy observation.
    assert energy["value"] == 0.0
    assert energy["semantics"] == "simulator_duty_gated_rated_power_proxy_wh"
    assert "energy" not in result["score"]["unscorable_dimensions"]


def test_public_protocol_is_strict_schema_with_two_legal_examples(pair):
    public, _ = pair
    view = {**public["agent_view"], "observation": public["initial_observation"], "last_feedback": None}
    protocol = public_action_protocol(view)
    assert protocol["authority"] == "backend_capability_manifest"
    act_schema, wait_schema = protocol["json_schema"]["oneOf"]
    assert wait_schema["additionalProperties"] is False
    assert act_schema["required"] == ["kind", "commands"]
    assert act_schema["additionalProperties"] is False
    validate_public_action({"kind": "wait"}, view)


def test_wire_prompt_explicitly_requires_schema_and_all_required_fields(pair):
    public, _ = pair
    view = {**public["agent_view"], "observation": public["initial_observation"], "last_feedback": None}
    messages = build_messages(view)
    assert "matching one branch" in messages[0]["content"]
    assert "every required field" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert "json_schema" in payload["action_protocol"]
    assert payload["action_protocol"]["authority"] == "backend_capability_manifest"


def test_public_protocol_is_manifest_driven_and_track_filtered(pair):
    public, _ = pair
    view = {**public["agent_view"], "observation": public["initial_observation"], "last_feedback": None}
    protocol = public_action_protocol(view)
    assert protocol["allowed_actions"] == ["act", "wait"]
    assert "ask" not in json.dumps(protocol)
    assert "thermal.control" in json.dumps(protocol)


def test_wait_preserves_prior_heat_while_later_off_changes_trajectory(pair):
    public, private = pair
    from evaluate_harness_v2_v4_flash import _load_config

    config = _load_config(private)
    bootstrap = {**public["agent_view"], "horizon_steps": 2}
    episode = EpisodeSpec("semantics_probe", bootstrap, seed=0, max_decisions=3)
    device = public["initial_observation"]["devices"]["kitchen"]["device_id"]
    heat = {"kind": "act", "commands": [{"device_id": device, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 24.0}}]}
    off = {"kind": "act", "commands": [{"device_id": device, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "off", "target_c": 24.0}}]}

    def two_steps(second_action):
        backend = SimuHomeHarnessAdapter(config, public["visible_room_ids"], sample_minutes=15)
        backend.reset(episode)
        assert backend.execute_atomic(heat).accepted
        backend.advance()
        assert backend.execute_atomic(second_action).accepted
        return backend.advance().public_observation["rooms"]["kitchen"]["temperature_c"]

    persisted = two_steps({"kind": "wait"})
    stopped = two_steps(off)
    assert persisted > stopped


@pytest.mark.parametrize("response", [
    "not json",
    '```json\n{"kind":"wait"}\n```',
    '{"kind":"wait","extra":1}',
    '{"kind":"wait","kind":"act"}',
    '{"kind":"act","commands":[],"x":NaN}',
    '{"kind":"act","commands":[],"x":Infinity}',
    '{"kind":"install_rule","rule":{}}',
    '{"kind":"act","commands":[{"device_id":"missing","capability":"thermal.control","operation":"set","parameters":{"mode":"auto","target_c":22}}]}',
    '{"kind":"act","commands":[{"device_id":"kitchen_air_conditioner_1","capability":"thermal.control","operation":"set","parameters":{"mode":"warp","target_c":22}}]}',
    '{"kind":"act","commands":[{"device_id":"kitchen_air_conditioner_1","capability":"thermal.control","operation":"set","parameters":{"mode":"auto","target_c":99}}]}',
])
def test_invalid_model_outputs_fail_closed_without_advance(pair, response):
    public, private = pair
    result = evaluate_one(public, private, FakeClient(response))
    assert result["status"] == "protocol_invalid"
    assert result["calls"] == 1
    assert result["valid_actions"] == 0
    assert result["observation_count"] == 1


def test_api_failure_fails_closed_without_fallback(pair):
    public, private = pair
    result = evaluate_one(public, private, FakeClient(error=RuntimeError("offline")))
    assert result["status"] == "protocol_invalid"
    assert result["calls"] == 1
    assert result["api_successes"] == 0
    assert result["observation_count"] == 1
    assert result["score"]["band_satisfaction"] is None
    assert result["score"]["score_valid"] is False
    assert smoke_accepted(result, public["agent_view"]["horizon_steps"]) is False
    json.dumps(result, allow_nan=False)


def test_invalid_public_model_output_is_preserved_with_hash_and_stage(pair):
    public, private = pair
    raw = '{"action":{"kind":"wait"}}'
    result = evaluate_one(public, private, FakeClient(raw))
    record = result["model_output_records"][0]
    assert record["raw_content"] == raw
    assert record["raw_content_sha256"] == __import__("hashlib").sha256(raw.encode()).hexdigest()
    assert record["stage"] == "json_parsed"
    assert record["error"] == "CapabilityProtocolViolation:action must be an object with a kind"


def test_api_failure_records_no_raw_content(pair):
    public, private = pair
    result = evaluate_one(public, private, FakeClient(error=RuntimeError("offline")))
    record = result["model_output_records"][0]
    assert record["stage"] == "request_started"
    assert record["raw_content"] is None
    assert record["raw_content_sha256"] is None
    assert record["error"] == "RuntimeError:offline"


def test_score_excludes_terminal_frame_and_uses_private_scope(pair):
    public, private = pair
    stored = deepcopy(private["runs"]["oracle"])
    terminal = [item for item in stored["public_trace"] if item["type"] == "observation"][-1]
    for room in private["target_room_ids"]:
        terminal["value"]["rooms"][room]["temperature_c"] = 100.0
    run = RunArtifact(stored["episode_id"], stored["status"], tuple(stored["public_trace"]), tuple(stored["private_trace"]), "digest")
    score = score_run(run, private, public["agent_view"]["decision_interval_minutes"])
    assert score["band_satisfaction"] == 1.0
    assert score["band_maintenance_pass"] is True


@pytest.mark.parametrize("mutation", ["report_status", "gate_status", "gate_ids", "episode_count", "extra_failed_gate_row"])
def test_preflight_rejects_unadmitted_or_inconsistent_batch(tmp_path, mutation):
    copied = tmp_path / "batch"
    shutil.copytree(BATCH, copied)
    report_path = copied / "build_report.json"
    gate_path = copied / "validation_gate.json"
    report = json.loads(report_path.read_text())
    gate = json.loads(gate_path.read_text())
    if mutation == "report_status":
        report["status"] = "FAIL_CLOSED"
    elif mutation == "gate_status":
        gate["all_passed"] = False
    elif mutation == "gate_ids":
        gate["episodes"][0]["episode_id"] = "forged"
    elif mutation == "extra_failed_gate_row":
        gate["episodes"].append({"episode_id": "unaccounted", "passed": False})
    else:
        report["episode_count"] += 1
    report_path.write_text(json.dumps(report))
    gate_path.write_text(json.dumps(gate))
    with pytest.raises(RuntimeError):
        preflight_batch(copied)
