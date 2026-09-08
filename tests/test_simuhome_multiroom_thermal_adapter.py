import json
from pathlib import Path
import pytest
from unified_compiler.simuhome_multiroom_thermal_adapter import ReplayError, SimuHomeMultiroomThermalAdapter, digest_json

ROOT = Path(__file__).resolve().parents[6]
BENCHMARK = ROOT / "external" / "SimuHome" / "data" / "benchmark"

def _candidate():
    fallback = None
    for path in sorted(BENCHMARK.glob("*.json")):
        config = json.loads(path.read_text())["initial_home_config"]
        rooms = [r for r, value in config["rooms"].items() if any(d.get("device_type") in {"air_conditioner", "heat_pump"} for d in value.get("devices", []))]
        if len(rooms) >= 2:
            candidate = (config, sorted(rooms)[:2])
            if any(d.get("device_type") == "heat_pump" for r in candidate[1] for d in config["rooms"][r].get("devices", [])):
                return candidate
            fallback = fallback or candidate
    if fallback:
        return fallback
    raise AssertionError("benchmark has no two-room thermal candidate")

def test_adapter_requires_two_explicit_rooms():
    with pytest.raises(ReplayError, match="at_least_two"):
        SimuHomeMultiroomThermalAdapter({"rooms": {"a": {}}}, room_ids=["a"])

def test_replay_is_synchronized_and_action_mapping_changes_temperature():
    config, rooms = _candidate()
    adapter = SimuHomeMultiroomThermalAdapter(config, room_ids=rooms)
    noop = adapter.replay(None, end_hour=23)
    policy = lambda _step, obs: {r: {"mode": "heat", "target_c": 28} for r in obs}
    heated = adapter.replay(policy, end_hour=23)
    for row in heated["trace"]:
        assert len({o["current_tick"] for o in row["observation"].values()}) == 1
        assert len({o["virtual_time"] for o in row["observation"].values()}) == 1
        assert set(row["action"]) == set(rooms)
    before = {r: noop["trace"][1]["observation"][r]["temperature_c"] for r in rooms}
    after = {r: heated["trace"][1]["observation"][r]["temperature_c"] for r in rooms}
    assert any(after[r] != before[r] for r in rooms)
    assert digest_json(heated) == digest_json(SimuHomeMultiroomThermalAdapter(config, room_ids=rooms).replay(policy, end_hour=23))

def test_heat_pump_cooling_fails_closed():
    config, rooms = _candidate()
    adapter = SimuHomeMultiroomThermalAdapter(config, room_ids=rooms)
    if not any(typ == "heat_pump" for _, typ in adapter.devices.values()): pytest.skip("candidate has no heat pump")
    with pytest.raises(ReplayError, match="heat_pump_cooling"):
        adapter.replay(lambda _step, _obs: {r: {"mode": "cool", "target_c": 18} for r in rooms}, end_hour=23)

def test_exact_boundary_is_exclusive():
    config, rooms = _candidate()
    config = json.loads(json.dumps(config))
    config["base_time"] = config["base_time"][:11] + "17:00:00"
    replay = SimuHomeMultiroomThermalAdapter(config, room_ids=rooms).replay(None, end_hour=23)
    assert len(replay["trace"]) == 24
    assert replay["trace"][-1]["observation"][rooms[0]]["virtual_time"].endswith("22:45:00")
