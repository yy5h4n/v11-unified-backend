from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Callable

import pytest

from unified_compiler import (
    CapabilityStatus,
    PhysicalTopology,
    ProcessRequirement,
    ResponsibilityLifecycle,
)
from unified_compiler.adapters.citylearn_battery import (
    DEFAULT_PROCESS_POOL,
    DEFAULT_REPLAY_GATE,
    PROVIDES,
    BatteryPoolError,
    CityLearnBatteryPVAdapter,
)


def _requirement(capabilities: frozenset[str]) -> ProcessRequirement:
    return ProcessRequirement(
        requirement_id="battery-pv",
        responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
        physical_topology=PhysicalTopology.STORAGE_DYNAMICS,
        required_capabilities=capabilities,
        state_variables=(),
        action_types=(),
    )


def _artifact_copies(tmp_path: Path) -> tuple[Path, Path]:
    pool_path = tmp_path / "battery_pv_process_pool.json"
    gate_path = tmp_path / "battery_pv_process_replay_gate.json"
    shutil.copyfile(DEFAULT_PROCESS_POOL, pool_path)
    shutil.copyfile(DEFAULT_REPLAY_GATE, gate_path)
    return pool_path, gate_path


def _mutated_adapter(
    tmp_path: Path,
    mutate_pool: Callable[[dict[str, Any]], None] | None = None,
    mutate_gate: Callable[[dict[str, Any]], None] | None = None,
) -> CityLearnBatteryPVAdapter:
    pool_path, gate_path = _artifact_copies(tmp_path)
    if mutate_pool is not None:
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        mutate_pool(pool)
        pool_path.write_text(json.dumps(pool), encoding="utf-8")
    if mutate_gate is not None:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        mutate_gate(gate)
        gate_path.write_text(json.dumps(gate), encoding="utf-8")
    return CityLearnBatteryPVAdapter(pool_path, gate_path)


def test_loads_48_verified_processes_once() -> None:
    adapter = CityLearnBatteryPVAdapter()
    requirement = _requirement(PROVIDES)

    first = adapter.scan([requirement])
    second = adapter.scan([requirement])

    assert len(first) == 48
    assert second == first
    assert adapter.load_count == 1
    assert adapter.scan_calls == 2


def test_capability_is_executable_with_exact_provides() -> None:
    capability = CityLearnBatteryPVAdapter().capabilities()[0]

    assert capability.status is CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED
    assert capability.backend == "citylearn_battery"
    assert capability.provides == tuple(sorted(PROVIDES))
    assert capability.metadata == {"process_count": 48}


def test_scan_returns_all_for_matching_requirement_and_none_for_mismatch() -> None:
    adapter = CityLearnBatteryPVAdapter()

    assert len(adapter.scan([_requirement(PROVIDES)])) == 48
    assert adapter.scan([_requirement(frozenset({"thermal.hvac_action"}))]) == ()


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield from _walk_strings(key)
            yield from _walk_strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _walk_strings(nested)


def test_manifests_have_no_absolute_paths_or_gold_actions() -> None:
    processes = CityLearnBatteryPVAdapter().scan([_requirement(PROVIDES)])

    assert len(processes) == 48
    for process in processes:
        manifest = process.manifest
        for value in _walk_strings(manifest):
            assert not Path(value).is_absolute(), value
        assert manifest["gold_actions_released"] is False
        assert "gold_actions" not in manifest
        assert "reference_actions" not in manifest
        assert "solver_result" not in manifest


def test_fails_closed_when_process_pool_is_missing(tmp_path: Path) -> None:
    _, gate_path = _artifact_copies(tmp_path)
    adapter = CityLearnBatteryPVAdapter(tmp_path / "missing.json", gate_path)

    with pytest.raises(BatteryPoolError, match="missing battery/PV artifact"):
        adapter.capabilities()


def test_fails_closed_when_pool_and_gate_digests_disagree(tmp_path: Path) -> None:
    adapter = _mutated_adapter(
        tmp_path,
        mutate_gate=lambda gate: gate.update({"process_pool_digest": "0" * 64}),
    )

    with pytest.raises(BatteryPoolError, match="does not match process pool"):
        adapter.capabilities()


def test_fails_closed_when_replay_gate_failed(tmp_path: Path) -> None:
    def fail_first_process(gate: dict[str, Any]) -> None:
        gate["processes"][0]["passed"] = False

    adapter = _mutated_adapter(tmp_path, mutate_gate=fail_first_process)

    with pytest.raises(BatteryPoolError, match="failed battery/PV replay gate"):
        adapter.capabilities()


def test_fails_closed_on_malformed_process(tmp_path: Path) -> None:
    def malformed_first_process(pool: dict[str, Any]) -> None:
        pool["processes"][0]["source_start_row"] = "not-an-integer"

    adapter = _mutated_adapter(tmp_path, mutate_pool=malformed_first_process)

    with pytest.raises(BatteryPoolError, match="malformed battery/PV process"):
        adapter.capabilities()


def test_fails_closed_on_duplicate_process(tmp_path: Path) -> None:
    def duplicate_first_process(pool: dict[str, Any]) -> None:
        pool["processes"].append(pool["processes"][0].copy())
        pool["selected_process_count"] += 1

    adapter = _mutated_adapter(tmp_path, mutate_pool=duplicate_first_process)

    with pytest.raises(BatteryPoolError, match="count or identity mismatch"):
        adapter.capabilities()
