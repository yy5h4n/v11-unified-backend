from __future__ import annotations

import json
from pathlib import Path

import pytest

from unified_compiler import PhysicalTopology, ProcessRequirement, ResponsibilityLifecycle
from unified_compiler.adapters.citylearn_battery_fault import (
    DEFAULT_BUILDING,
    DEFAULT_HORIZON,
    DEFAULT_START,
    FAULT_MODES,
    PROVIDES,
    BatteryFaultSchedule,
    BatteryFaultWindow,
    CityLearnBatteryFaultAdapter,
    CityLearnBatteryFaultError,
    battery_fault_profile,
    run_battery_fault,
)
import probe_citylearn_battery_fault as battery_fault_probe


def requirement() -> ProcessRequirement:
    return ProcessRequirement(
        requirement_id="citylearn-battery-fault",
        responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
        physical_topology=PhysicalTopology.STORAGE_DYNAMICS,
        required_capabilities=PROVIDES,
        state_variables=(),
        action_types=(),
    )


def test_schedule_is_canonical_exogenous_and_half_open() -> None:
    schedule = BatteryFaultSchedule((BatteryFaultWindow(2, 6, "unavailable"),))
    assert schedule.state_at(1).active is False
    assert schedule.state_at(2).mode == "unavailable"
    assert schedule.state_at(6).active is False
    assert schedule.as_dict()["agent_can_modify"] is False


@pytest.mark.parametrize("mode", FAULT_MODES)
def test_real_citylearn_replay_diverges_from_healthy(mode: str) -> None:
    actions = tuple(1.0 if i % 2 == 0 else -0.75 for i in range(DEFAULT_HORIZON))
    faulty = run_battery_fault(DEFAULT_BUILDING, DEFAULT_START, DEFAULT_HORIZON, battery_fault_profile(mode), actions)
    repeat = run_battery_fault(DEFAULT_BUILDING, DEFAULT_START, DEFAULT_HORIZON, battery_fault_profile(mode), actions)
    healthy = run_battery_fault(DEFAULT_BUILDING, DEFAULT_START, DEFAULT_HORIZON, BatteryFaultSchedule(), actions)
    assert faulty == repeat
    assert faulty["deterministic"] is True
    assert faulty["terminated"] is True
    assert any(a["effect"]["battery_soc"] != b["effect"]["battery_soc"] for a, b in zip(faulty["records"], healthy["records"]))
    assert any(a["effect"]["net_electricity_kwh"] != b["effect"]["net_electricity_kwh"] for a, b in zip(faulty["records"], healthy["records"]))


def test_adapter_is_verified_only_by_matching_profile_gate() -> None:
    for mode in FAULT_MODES:
        adapter = CityLearnBatteryFaultAdapter(profile=mode)
        capability = adapter.capabilities()[0]
        assert capability.status.value == "EXECUTABLE_REPLAY_VERIFIED"
        processes = adapter.scan([requirement()])
        assert len(processes) == 1
        assert processes[0].manifest["gold_actions_released"] is False
        assert processes[0].backend_version == "2.5.0"


def test_missing_gate_fails_closed(tmp_path: Path) -> None:
    adapter = CityLearnBatteryFaultAdapter(profile="unavailable", replay_gate_path=tmp_path / "missing.json")
    assert adapter.scan([requirement()]) == ()
    assert adapter.capabilities()[0].status.value == "DATA_PROBED_PENDING_REPLAY"


def test_overlapping_windows_rejected() -> None:
    with pytest.raises(CityLearnBatteryFaultError):
        BatteryFaultSchedule((
            BatteryFaultWindow(0, 3, "stuck"),
            BatteryFaultWindow(2, 4, "unavailable"),
        ))


def test_probe_check_accepts_current_four_profile_evidence() -> None:
    destination = battery_fault_probe.DEFAULT_PROFILE_GATE_DIR
    names = list(battery_fault_probe.FAULT_MODES)
    expected = {
        name: json.loads((destination / f"{name}.json").read_text(encoding="utf-8"))
        for name in names
    }
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    battery_fault_probe._artifact_check(
        destination, names, expected, manifest, check_manifest=True
    )


def test_probe_check_rejects_stale_without_writing(tmp_path: Path) -> None:
    destination = tmp_path / "evidence"
    destination.mkdir()
    names = list(battery_fault_probe.FAULT_MODES)
    expected = {}
    for name in names:
        source = battery_fault_probe.DEFAULT_PROFILE_GATE_DIR / f"{name}.json"
        report = json.loads(source.read_text(encoding="utf-8"))
        expected[name] = report
        (destination / f"{name}.json").write_text(json.dumps(report), encoding="utf-8")
    manifest = json.loads(
        (battery_fault_probe.DEFAULT_PROFILE_GATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    (destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stale = json.loads((destination / "stuck.json").read_text(encoding="utf-8"))
    stale["verified"] = False
    (destination / "stuck.json").write_text(json.dumps(stale), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in destination.iterdir()}
    with pytest.raises(SystemExit, match="stale"):
        battery_fault_probe._artifact_check(
            destination, names, expected, manifest, check_manifest=True
        )
    assert before == {path.name: path.read_bytes() for path in destination.iterdir()}
