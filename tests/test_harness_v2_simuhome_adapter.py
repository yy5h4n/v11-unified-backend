import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_v2.core import EpisodeSpec, Harness
from harness_v2.simuhome_adapter import SimuHomeHarnessAdapter


ROOT = Path(__file__).resolve().parents[6]
BENCHMARK = ROOT / "external" / "SimuHome" / "data" / "benchmark"


def candidate():
    for path in sorted(BENCHMARK.glob("*.json")):
        config = json.loads(path.read_text())["initial_home_config"]
        rooms = sorted(room for room, value in config["rooms"].items() if any(device.get("device_type") in {"air_conditioner", "heat_pump"} for device in value.get("devices", [])))
        if len(rooms) >= 2:
            return config, rooms[:2]
    raise AssertionError("SimuHome benchmark has no multi-room thermal candidate")


def heat_pump_golden_candidate():
    path = BENCHMARK / "qt1_feasible_seed_34.json"
    config = json.loads(path.read_text())["initial_home_config"]
    room = "kitchen"
    assert any(
        device.get("device_id") == "kitchen_heat_pump_1"
        and device.get("device_type") == "heat_pump"
        for device in config["rooms"][room].get("devices", [])
    )
    return config, room


def episode():
    return EpisodeSpec(
        episode_id="episode.simuhome.harness.smoke",
        public_bootstrap={
            "query": "Keep both available rooms comfortable this evening.",
            "profile": {"thermal_preference_c": 22},
            "horizon_steps": 3,
            "action_schema": {"kinds": ["act", "install_rule", "cancel_rule", "ask", "wait"]},
        },
        seed=11,
        max_decisions=4,
    )


class InstallBothRooms:
    def __init__(self):
        self.installed = False

    def decide(self, view):
        if self.installed:
            return {"kind": "wait", "mode": "for", "duration_seconds": 900}
        self.installed = True
        devices = [value["device_id"] for value in view["observation"]["devices"].values()]
        command = lambda device_id, target: {"device_id": device_id, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": target}}
        return {"kind": "install_rule", "rule": {"rule_id": "rule.both", "fire_at_step": 1, "release_at_step": 2, "commands": [command(device_id, 28) for device_id in devices], "release_commands": [command(device_id, 18) for device_id in devices]}}


def test_real_simuhome_adapter_runs_rule_fire_and_release_deterministically():
    config, rooms = candidate()
    first = Harness(SimuHomeHarnessAdapter(config, rooms)).run_one(episode(), InstallBothRooms())
    second = Harness(SimuHomeHarnessAdapter(config, rooms)).run_one(episode(), InstallBothRooms())
    assert first.status == "completed"
    assert first.trace_digest == second.trace_digest
    action_records = [item for item in first.public_trace if item["type"] == "action"]
    assert action_records[0]["accepted"] is True
    commands = [command for item in first.private_trace if item["type"] == "backend_state" for command in item["value"]["applied_commands"]]
    assert {command["source"] for command in commands} == {"rule_firing", "rule_release"}
    device_ids = {value["device_id"] for value in first.public_trace[0]["value"]["devices"].values()}
    assert {command["device_id"] for command in commands} == device_ids
    assert len(commands) == 2 * len(device_ids)


def test_real_simuhome_adapter_rejects_invalid_second_command_atomically():
    config, rooms = candidate()
    adapter = SimuHomeHarnessAdapter(config, rooms)
    initial = adapter.reset(episode())
    devices = [value["device_id"] for value in initial.public_observation["devices"].values()]
    valid = {"device_id": devices[0], "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 22}}
    invalid = {"device_id": "unknown.device", "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 22}}
    before = adapter.state_digest()
    outcome = adapter.execute_atomic({"kind": "act", "commands": [valid, invalid]})
    assert outcome.accepted is False
    assert outcome.error_code == "UNKNOWN_DEVICE"
    assert outcome.private_feedback["transaction_status"] == "rejected"
    assert outcome.private_feedback["applied_commands"] == []
    assert adapter.state_digest() == before


def test_real_simuhome_adapter_rolls_back_underlying_partial_failure(monkeypatch):
    config, rooms = candidate()
    adapter = SimuHomeHarnessAdapter(config, rooms)
    initial = adapter.reset(episode())
    devices = [value["device_id"] for value in initial.public_observation["devices"].values()]
    commands = [
        {"device_id": device_id, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 22}}
        for device_id in devices
    ]
    before = adapter.state_digest()
    legacy = adapter._require_legacy()
    original = legacy._apply_for_room
    calls = 0

    def fail_second(room, action, observation):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected second-command failure")
        return original(room, action, observation)

    monkeypatch.setattr(legacy, "_apply_for_room", fail_second)
    outcome = adapter.execute_atomic({"kind": "act", "commands": commands})
    assert outcome.accepted is False
    assert outcome.error_code == "BACKEND_REJECTED:RuntimeError"
    assert adapter.state_digest() == before


class AdvanceFailurePolicy:
    def __init__(self, adapter, failure):
        self.adapter = adapter
        self.failure = failure
        self.decisions = 0

    def _fail_second_command(self):
        legacy = self.adapter._require_legacy()
        original = legacy._apply_for_room
        calls = 0

        def fail_second(room, action, observation):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected rule batch failure")
            return original(room, action, observation)

        legacy._apply_for_room = fail_second

    def decide(self, view):
        devices = [value["device_id"] for value in view["observation"]["devices"].values()]
        command = lambda device_id, target: {"device_id": device_id, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": target}}
        if self.decisions == 0:
            if self.failure == "fire_second_command":
                self._fail_second_command()
            elif self.failure == "fast_forward":
                self.adapter._require_legacy().home._fast_forward_to = lambda *args, **kwargs: SimpleNamespace(success=False, error_message="injected", error_detail="fast-forward failure")
            self.decisions += 1
            return {"kind": "install_rule", "rule": {"rule_id": "rule.failure", "fire_at_step": 1, "release_at_step": 2, "commands": [command(device_id, 28) for device_id in devices], "release_commands": [command(device_id, 18) for device_id in devices]}}
        if self.failure == "release_second_command" and self.decisions == 1:
            self._fail_second_command()
        self.decisions += 1
        return {"kind": "wait", "mode": "for", "duration_seconds": 900}


@pytest.mark.parametrize("failure", ["fire_second_command", "release_second_command", "fast_forward"])
def test_real_simuhome_adapter_advance_failure_is_atomic_and_sealed(failure):
    config, rooms = candidate()
    adapter = SimuHomeHarnessAdapter(config, rooms)
    artifact = Harness(adapter).run_one(episode(), AdvanceFailurePolicy(adapter, failure))
    assert artifact.status == "backend_advance_failed"
    errors = [item for item in artifact.private_trace if item["type"] == "advance_error"]
    assert len(errors) == 1
    assert errors[0]["pre_state_digest"] == errors[0]["post_state_digest"]
    expected = {
        "fire_second_command": ("rule_firing", "RULE_FIRE_FAILED"),
        "release_second_command": ("rule_release", "RULE_RELEASE_FAILED"),
        "fast_forward": ("fast_forward", "SIMULATOR_ADVANCE_FAILED"),
    }[failure]
    assert (errors[0]["phase"], errors[0]["error_code"]) == expected
    assert artifact.public_trace[-1]["type"] == "backend_error"


def test_real_simuhome_state_digest_covers_latent_aggregator_and_scheduler_state():
    config, rooms = candidate()
    adapter = SimuHomeHarnessAdapter(config, rooms)
    adapter.reset(episode())
    home = adapter._require_legacy().home
    before = adapter.state_digest()
    temperature = home.aggregators_by_room[rooms[0]]["temperature"]
    temperature._first_sync_done = not temperature._first_sync_done
    assert adapter.state_digest() != before

    adapter.reset(episode())
    before = adapter.state_digest()
    adapter._require_legacy().home.task_seq_counter += 1
    assert adapter.state_digest() != before


@pytest.mark.parametrize("duplicate_phase", ["fire", "release"])
def test_real_simuhome_rule_rejects_duplicates_within_each_transaction(duplicate_phase):
    config, rooms = candidate()
    adapter = SimuHomeHarnessAdapter(config, rooms)
    initial = adapter.reset(episode())
    device_id = next(iter(initial.public_observation["devices"].values()))["device_id"]
    command = {"device_id": device_id, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 22}}
    fire = [command, command] if duplicate_phase == "fire" else [command]
    release = [command, command] if duplicate_phase == "release" else [command]
    outcome = adapter.execute_atomic({"kind": "install_rule", "rule": {"rule_id": "rule.duplicate", "fire_at_step": 1, "release_at_step": 2, "commands": fire, "release_commands": release}})
    assert outcome.accepted is False
    assert outcome.error_code == "DUPLICATE_DEVICE_COMMAND"


def test_real_simuhome_rule_allows_same_device_across_fire_and_release_transactions():
    config, rooms = candidate()
    adapter = SimuHomeHarnessAdapter(config, rooms)
    initial = adapter.reset(episode())
    device_id = next(iter(initial.public_observation["devices"].values()))["device_id"]
    command = {"device_id": device_id, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 22}}
    outcome = adapter.execute_atomic({"kind": "install_rule", "rule": {"rule_id": "rule.valid", "fire_at_step": 1, "release_at_step": 2, "commands": [command], "release_commands": [command]}})
    assert outcome.accepted is True


def test_immediate_command_has_committed_transaction_receipt_and_public_device_state():
    config, rooms = candidate()
    adapter = SimuHomeHarnessAdapter(config, rooms)
    initial = adapter.reset(episode())
    device_id = initial.public_observation["devices"][rooms[0]]["device_id"]
    command = {"device_id": device_id, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 22}}
    outcome = adapter.execute_atomic({"kind": "act", "commands": [command]})
    assert outcome.accepted is True
    assert outcome.private_feedback["transaction_status"] == "committed"
    assert outcome.private_feedback["applied_commands"][0]["origin"] == {
        "kind": "agent_immediate",
        "transaction_id": outcome.private_feedback["transaction_id"],
    }
    assert outcome.private_feedback["applied_commands"][0]["status"] == "committed"
    advanced = adapter.advance()
    assert advanced.public_observation["devices"][rooms[0]]["mode"] == "heat"
    assert advanced.public_observation["devices"][rooms[0]]["target_c"] == 22.0


def test_active_rule_is_public_but_internal_installation_origin_is_not():
    config, rooms = candidate()
    adapter = SimuHomeHarnessAdapter(config, rooms)
    initial = adapter.reset(episode())
    device_id = initial.public_observation["devices"][rooms[0]]["device_id"]
    command = {"device_id": device_id, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 22}}
    action = {"kind": "install_rule", "rule": {"rule_id": "rule.visible", "fire_at_step": 1, "release_at_step": 2, "commands": [command], "release_commands": [command]}}
    assert adapter.execute_atomic(action).accepted
    view = adapter.advance()
    assert view.public_observation["active_rules"][0]["rule_id"] == "rule.visible"
    assert "_installation_transaction_id" not in view.public_observation["active_rules"][0]
    applied = view.private_state["applied_commands"][0]
    assert applied["origin"]["installation_transaction_id"].startswith("tx.")


def test_identical_control_write_is_coalesced_after_first_commit():
    config, rooms = candidate()
    adapter = SimuHomeHarnessAdapter(config, rooms)
    initial = adapter.reset(episode())
    room = rooms[0]
    device_id = initial.public_observation["devices"][room]["device_id"]
    command = {"device_id": device_id, "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 22}}
    first = adapter.execute_atomic({"kind": "act", "commands": [command]})
    second = adapter.execute_atomic({"kind": "act", "commands": [command]})
    assert first.private_feedback["applied_commands"][0]["status"] in {"committed", "coalesced"}
    assert second.private_feedback["applied_commands"][0]["status"] == "coalesced"
    assert second.public_feedback["applied_command_count"] == 1


def test_heat_pump_energy_proxy_integrates_only_when_thermally_actuating():
    config, room = heat_pump_golden_candidate()
    adapter = SimuHomeHarnessAdapter(config, [room])
    initial = adapter.reset(episode())
    device = initial.public_observation["devices"][room]
    command = {"device_id": device["device_id"], "capability": "thermal.control", "operation": "set", "parameters": {"mode": "heat", "target_c": 28}}
    assert adapter.execute_atomic({"kind": "act", "commands": [command]}).accepted
    advanced = adapter.advance().public_observation["devices"][room]
    assert advanced["energy_semantics"] == "simulator_duty_gated_rated_power_proxy"
    assert advanced["rated_active_power_w"] > 0
    assert advanced["last_interval_energy_proxy_wh"] == pytest.approx(advanced["rated_active_power_w"] * 15 / 60)
    assert advanced["cumulative_energy_proxy_wh"] == advanced["last_interval_energy_proxy_wh"]
