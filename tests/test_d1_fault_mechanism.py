"""D1 actuator-fault route tests.

The unit tests use a one-zone deterministic process to isolate the D1 causal
contract.  One integration test below exercises the real pinned SustainGym
adapter when its local runtime is available.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from unified_compiler import (
    PhysicalProcess,
    PhysicalTopology,
    ProcessRequirement,
    ResponsibilityLifecycle,
)
from unified_compiler.adapters.d1_fault_mechanism import (
    ActuatorFaultSchedule,
    ActuatorFaultWindow,
    D1ActionError,
    D1FaultAdapter,
    D1FaultEpisode,
    D1FaultError,
    SensorFaultSchedule,
    SensorFaultWindow,
    d1_profile,
)
from unified_compiler.adapters.sustaingym_building import SustainGymBuildingAdapter


class FakeThermalBase:
    """Minimal base with the same reset/observe/step surface as SustainGym."""

    backend = "fake_sustaingym"

    def __init__(self, horizon: int = 8) -> None:
        self.horizon = horizon
        self.temperature = 25.0
        self.index = 0
        self._active = False

    def capabilities(self):
        return ()

    def scan(self, requirements):
        return (
            PhysicalProcess(
                process_id="fake-hvac",
                domain="hvac",
                backend=self.backend,
                backend_version="test",
                source_id="fake://fixed-window",
                source_hash="a" * 64,
                horizon_steps=self.horizon,
                observation_interval_seconds=300.0,
                provided_capabilities=frozenset(
                    {
                        "thermal.zone_temperature",
                        "thermal.hvac_cooling_action",
                        "weather.exogenous",
                        "occupancy.exogenous",
                    }
                ),
                state_variables=("zone_temperature",),
                action_types=("hvac_cooling_power",),
                manifest={"source_window": "fixed"},
            ),
        )

    def reset(self, *, seed=200, t_initial=None):
        self.temperature = float(t_initial[0]) if t_initial is not None else 25.0
        self.index = 0
        self._active = True
        return self.observe()

    def replay_id(self):
        if not self._active:
            raise RuntimeError("not reset")
        return f"fake-replay-seed-200"

    def observe(self):
        if not self._active:
            raise RuntimeError("not reset")
        return {
            "step_index": self.index,
            "zone_temperatures_c": [self.temperature],
            "weather": {"outdoor_temperature_c": 30.0},
        }

    def legal_actions(self):
        if not self._active:
            raise RuntimeError("not reset")
        return {
            "type": "hvac_power_per_zone",
            "shape": [1],
            "cooling": {"minimum": -0.05, "maximum": 0.0},
        }

    def step(self, action):
        if not self._active:
            raise RuntimeError("not reset")
        # Cooling is causal: -0.05 reduces the one-step heat gain by 0.5 C.
        self.temperature += 1.0 + float(action[0]) * 10.0
        old = self.index
        self.index += 1
        done = self.index >= self.horizon
        return {"step_index": old, "observation": self.observe(), "done": done}

    @property
    def done(self):
        return self.index >= self.horizon

    def private_state(self):
        return {"base": "fake", "index": self.index}


def _schedule():
    return ActuatorFaultSchedule((ActuatorFaultWindow(2, 5, "failed"),))


def _trace(schedule, actions=None):
    base = FakeThermalBase()
    episode = D1FaultEpisode(base, schedule, "fake-d1")
    episode.reset(seed=200)
    actions = actions or [[-0.05]] * base.horizon
    trace = [episode.step(action) for action in actions]
    return episode, trace


def test_schedule_is_canonical_half_open_and_rejects_overlap():
    schedule = _schedule()
    assert schedule.state_at(1).mode == "healthy"
    assert schedule.state_at(2).mode == "failed"
    assert schedule.state_at(4).gain == 0.0
    assert schedule.state_at(5).mode == "healthy"
    assert schedule.as_dict()["schedule_id"] == schedule.schedule_id
    with pytest.raises(D1FaultError, match="must not overlap"):
        ActuatorFaultSchedule(
            (ActuatorFaultWindow(1, 4, "failed"), ActuatorFaultWindow(3, 5, "failed"))
        )
    with pytest.raises(D1FaultError, match="mode"):
        ActuatorFaultWindow(1, 2, "intermittent")


@pytest.mark.parametrize("mode", ["degraded", "failed", "stuck", "intermittent_dropout"])
def test_verified_actuator_fault_modes_have_canonical_profiles(mode):
    actuator, sensors = d1_profile(mode)
    assert actuator.windows[0].mode == mode
    assert not sensors.windows


@pytest.mark.parametrize("mode", ["bias", "drift"])
def test_verified_sensor_fault_modes_are_public_observation_only(mode):
    actuator, sensors = d1_profile(mode)
    assert not actuator.windows
    assert sensors.windows[0].mode == mode


def test_fault_schedules_reject_cross_family_modes():
    with pytest.raises(D1FaultError, match="unsupported actuator"):
        ActuatorFaultWindow(1, 5, "bias")
    with pytest.raises(D1FaultError, match="unsupported sensor"):
        SensorFaultWindow(1, 5, "failed")


def test_same_reset_and_schedule_replay_exactly():
    schedule = _schedule()
    _, trace_a = _trace(schedule)
    _, trace_b = _trace(schedule)
    assert trace_a == trace_b
    assert trace_a[2]["d1"]["fault"]["mode"] == "failed"
    assert trace_a[2]["d1"]["effective_action"] == [0.0]


def test_fault_changes_future_state_and_feasible_effect():
    schedule = _schedule()
    _, healthy = _trace(ActuatorFaultSchedule())
    _, faulty = _trace(schedule)
    assert [r["d1"]["requested_action"] for r in healthy] == [
        r["d1"]["requested_action"] for r in faulty
    ]
    assert faulty[2]["d1"]["effective_action"] == [0.0]
    assert healthy[2]["d1"]["effective_action"] == [-0.05]
    assert faulty[4]["observation"]["zone_temperatures_c"] != healthy[4]["observation"]["zone_temperatures_c"]
    # After the exogenous outage clears, the same requested action is again effective.
    assert faulty[5]["d1"]["effective_action"] == [-0.05]
    # With the same reset and the same fault schedule, agent choices still
    # matter once the actuator recovers (the core action->future-state gate).
    _, fault_all_off = _trace(schedule, [[0.0]] * 8)
    assert fault_all_off[7]["observation"]["zone_temperatures_c"] != faulty[7]["observation"]["zone_temperatures_c"]


@pytest.mark.parametrize("mode", ["bias", "drift"])
def test_sensor_fault_changes_public_observation_not_latent_state(mode):
    _, sensors = d1_profile(mode)
    faulty = D1FaultEpisode(FakeThermalBase(), ActuatorFaultSchedule(), "sensor-fault", sensors)
    healthy = D1FaultEpisode(FakeThermalBase(), ActuatorFaultSchedule(), "healthy")
    faulty.reset(seed=200)
    healthy.reset(seed=200)
    faulty_trace = [faulty.step([-0.05]) for _ in range(8)]
    healthy_trace = [healthy.step([-0.05]) for _ in range(8)]
    assert faulty_trace[0]["observation"]["zone_temperatures_c"] == healthy_trace[0]["observation"]["zone_temperatures_c"]
    assert faulty_trace[1]["observation"]["zone_temperatures_c"] != healthy_trace[1]["observation"]["zone_temperatures_c"]
    assert faulty.base.private_state() == healthy.base.private_state()
    if mode == "bias":
        assert faulty_trace[1]["observation"]["zone_temperatures_c"][0] - healthy_trace[1]["observation"]["zone_temperatures_c"][0] == 1.0
    else:
        deltas = [f["observation"]["zone_temperatures_c"][0] - h["observation"]["zone_temperatures_c"][0] for f, h in zip(faulty_trace[1:5], healthy_trace[1:5])]
        assert deltas == sorted(deltas) and len(set(deltas)) == 4


def test_episode_and_action_surface_fail_closed():
    episode = D1FaultEpisode(FakeThermalBase(), _schedule(), "fake-d1")
    with pytest.raises(D1FaultError, match="reset"):
        episode.observe()
    episode.reset()
    with pytest.raises(D1ActionError):
        episode.step([0.1])
    with pytest.raises(D1ActionError):
        episode.step([float("nan")])
    with pytest.raises(D1ActionError):
        episode.step([-0.05, -0.05])


def _write_gate(path: Path, adapter: D1FaultAdapter, **overrides):
    source = Path(__import__("unified_compiler.adapters.d1_fault_mechanism", fromlist=["__file__"]).__file__)
    gate = {
        "schema_version": "d1-fault-replay-gate-v1",
        "backend_commit": "eac2a4d5ce4ccf44b13c290edac72532c68e30f5",
        "schedule_id": adapter.schedule.schedule_id,
        "adapter_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "verified": True,
        "backend_importable": True,
        "same_reset_same_window_replay": True,
        "action_sensitive": True,
        "fault_changes_future_state": True,
        "fault_changes_feasible_strategy": True,
        "all_replays_completed": True,
    }
    gate.update(overrides)
    path.write_text(json.dumps(gate), encoding="utf-8")


def test_adapter_gate_fail_closed_then_exposes_process(tmp_path: Path):
    schedule = _schedule()
    missing = tmp_path / "missing.json"
    adapter = D1FaultAdapter(FakeThermalBase(), schedule, missing)
    assert adapter.capabilities()[0].status.value == "DATA_PROBED_PENDING_REPLAY"
    assert adapter.scan(()) == ()
    with pytest.raises(D1FaultError, match="not verified"):
        adapter.open_episode()

    _write_gate(missing, adapter)
    requirement = ProcessRequirement(
        requirement_id="d1-test",
        responsibility_lifecycle=ResponsibilityLifecycle.RECOVER_AFTER_EVENT,
        physical_topology=PhysicalTopology.THERMAL_DYNAMICS,
        required_capabilities=frozenset({"actuator.failure_schedule"}),
        state_variables=(),
        action_types=(),
    )
    process = adapter.scan((requirement,))
    assert len(process) == 1
    assert process[0].backend == "sustaingym_building_d1"
    assert process[0].manifest["agent_cannot_modify_schedule"] is True
    assert adapter.capabilities()[0].status.value == "EXECUTABLE_REPLAY_VERIFIED"

    bad = tmp_path / "bad.json"
    _write_gate(bad, adapter, schedule_id="wrong")
    with pytest.raises(D1FaultError, match="schedule"):
        D1FaultAdapter(FakeThermalBase(), schedule, bad).capabilities()


def test_pinned_sustaingym_short_counterfactual():
    """The D1 wrapper forwards to the real pinned BuildingEnv when available."""
    try:
        faulty = D1FaultAdapter()
        episode = faulty.open_episode()
        initial = episode.reset(seed=200)
        healthy = D1FaultEpisode(
            SustainGymBuildingAdapter(), ActuatorFaultSchedule(), "healthy"
        )
        healthy.reset(seed=200)
    except Exception as exc:
        # The repository's project-local runtime is optional on clean machines.
        pytest.skip(f"pinned SustainGym runtime unavailable: {exc}")
    action = [-0.05] * len(initial["zone_temperatures_c"])
    faulty_trace = [episode.step(action) for _ in range(8)]
    healthy_trace = [healthy.step(action) for _ in range(8)]
    assert faulty_trace[2]["d1"]["fault"]["mode"] == "failed"
    assert faulty_trace[2]["d1"]["effective_action"] != healthy_trace[2]["d1"]["effective_action"]
    assert faulty_trace[4]["observation"]["zone_temperatures_c"] != healthy_trace[4]["observation"]["zone_temperatures_c"]
