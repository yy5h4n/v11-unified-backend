from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from unified_compiler.adapters.ev2gym_fault import (
    ChargerFaultSchedule,
    ChargerFaultWindow,
    EV2GymFaultActionError,
    EV2GymFaultAdapter,
    EV2GymFaultError,
    EV2GymFaultTrajectory,
    PROFILE_SCHEDULES,
)


class FakeEVClaimEpisode:
    """Small native-surface double; no EV2Gym semantics are invented in adapter."""

    replay_id = "sha256:fake"

    def __init__(self) -> None:
        self.soc = 0.2
        self.step_index = 0
        self.started = False
        self.previous_kw = 0.0

    @property
    def done(self):
        return self.step_index >= 8

    def reset(self):
        self.soc, self.step_index, self.previous_kw, self.started = 0.2, 0, 0.0, True
        return self.observe()

    def observe(self):
        if not self.started or self.done:
            raise RuntimeError("not active")
        return {
            "time": self.step_index,
            "vehicle_connected": True,
            "vehicle_soc": round(self.soc, 6),
            "charger_max_power_kw": 3.68,
        }

    def legal_actions(self):
        return {"SET_CHARGE_POWER": {"kw": {"minimum": 0.0, "maximum": 3.68}}, "WAIT": {}}

    def step(self, action):
        kw = float(action["kw"])
        before = self.soc
        self.soc = min(1.0, self.soc + kw / 3.68 * 0.02)
        self.previous_kw = kw
        row = {
            "source_step": self.step_index,
            "observation": self.observe(),
            "action": {"charge_power_kw": kw},
            "effect": {
                "vehicle_soc_before": before,
                "vehicle_soc_after": self.soc,
                "charged_energy_kwh": (self.soc - before) * 50.0,
            },
            "backend_terminated": self.step_index == 7,
        }
        self.step_index += 1
        return row

    def private_state(self):
        return {"backend_version": "fake", "step_index": self.step_index}


def test_schedule_is_canonical_and_deterministic():
    schedule = ChargerFaultSchedule((ChargerFaultWindow(2, 5, "outage"),))
    assert schedule.health_at(1).mode == "healthy"
    assert schedule.health_at(2).availability == 0.0
    assert schedule.health_at(5).mode == "healthy"
    assert schedule.schedule_id == ChargerFaultSchedule((ChargerFaultWindow(2, 5, "outage"),)).schedule_id
    with pytest.raises(EV2GymFaultError, match="must not overlap"):
        ChargerFaultSchedule((ChargerFaultWindow(1, 4, "outage"), ChargerFaultWindow(3, 5, "derated", 0.5)))


@pytest.mark.parametrize("name", ("derated", "outage", "intermittent"))
def test_fault_modes_transform_native_action_and_public_state(name):
    trajectory = EV2GymFaultTrajectory(FakeEVClaimEpisode(), PROFILE_SCHEDULES[name], "fake")
    trajectory.reset()
    rows = [trajectory.step({"type": "SET_CHARGE_POWER", "kw": 3.68}) for _ in range(8)]
    assert rows[0]["d1_fault"]["effective_charge_power_kw"] == 3.68
    if name == "derated":
        assert rows[2]["d1_fault"]["effective_charge_power_kw"] == 1.84
        assert rows[2]["observation"]["charger_max_power_kw"] == 1.84
    elif name == "outage":
        assert rows[2]["d1_fault"]["effective_charge_power_kw"] == 0.0
        assert rows[2]["effect"]["delivered_charging_kwh"] == 0.0
    else:
        assert rows[2]["d1_fault"]["effective_charge_power_kw"] == 0.0
        assert rows[3]["d1_fault"]["effective_charge_power_kw"] == 3.68


def test_wait_tracks_requested_mode_across_fault_recovery():
    trajectory = EV2GymFaultTrajectory(
        FakeEVClaimEpisode(), ChargerFaultSchedule((ChargerFaultWindow(1, 2, "outage"),)), "fake"
    )
    trajectory.reset()
    trajectory.step({"type": "SET_CHARGE_POWER", "kw": 3.68})
    failed = trajectory.step({"type": "WAIT"})
    recovered = trajectory.step({"type": "WAIT"})
    assert failed["d1_fault"]["effective_charge_power_kw"] == 0.0
    assert recovered["d1_fault"]["effective_charge_power_kw"] == 3.68


def test_invalid_action_fails_closed():
    trajectory = EV2GymFaultTrajectory(FakeEVClaimEpisode(), PROFILE_SCHEDULES["outage"], "fake")
    trajectory.reset()
    with pytest.raises(EV2GymFaultActionError):
        trajectory.step({"type": "SET_CHARGE_POWER", "kw": 99.0})


def test_missing_gate_is_pending_and_scan_is_empty(tmp_path: Path):
    adapter = EV2GymFaultAdapter(replay_gate_path=tmp_path / "missing.json")
    capability = adapter.capabilities()[0]
    assert capability.status.value == "DATA_PROBED_PENDING_REPLAY"
    assert adapter.scan(()) == ()


def test_native_gate_has_counterfactual_evidence():
    gate_path = Path(__file__).parents[1] / "generated" / "ev2gym_fault_replay_gate_v1.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    assert gate["schema_version"] == "ev2gym-d1-fault-replay-gate-v1"
    assert gate["scope"] == "backend trajectory replay evidence only"
    if gate["verified"]:
        assert all(gate["deterministic_replay"].values())
        assert set(gate["healthy_vs_fault_counterfactual"]) == {"derated", "outage", "intermittent"}
        assert all(item["effective_action_differs"] for item in gate["healthy_vs_fault_counterfactual"].values())
        assert all("transitions" not in item and "initial" not in item for item in [gate["healthy"], *gate["profiles"].values()])
        assert gate["native_verification"]["artifact_refs"]


def test_probe_check_stale_fails_without_writing(tmp_path: Path, monkeypatch):
    import probe_ev2gym_faults as probe

    output = tmp_path / "gate.json"
    output.write_text('{"old": true}\n', encoding="utf-8")
    before = output.read_bytes()
    monkeypatch.setattr(probe, "build_gate", lambda: {"new": True})
    monkeypatch.setattr(sys, "argv", ["probe_ev2gym_faults.py", "--check", "--output", str(output)])
    with pytest.raises(SystemExit) as excinfo:
        probe.main()
    assert excinfo.value.code == 1
    assert output.read_bytes() == before


def test_probe_check_valid_does_not_write(tmp_path: Path, monkeypatch):
    import probe_ev2gym_faults as probe

    output = tmp_path / "gate.json"
    expected = {"new": True}
    output.write_text(json.dumps(expected) + "\n", encoding="utf-8")
    before = output.read_bytes()
    monkeypatch.setattr(probe, "build_gate", lambda: expected)
    monkeypatch.setattr(sys, "argv", ["probe_ev2gym_faults.py", "--check", "--output", str(output)])
    probe.main()
    assert output.read_bytes() == before
