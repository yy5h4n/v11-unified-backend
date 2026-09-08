"""Tests for the legacy v10 adapters, against the real frozen v10 artifacts.

Everything here is read-only: scans index the existing v10 JSONL artifacts in
place and must never create files inside v11.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence

import pytest

from unified_compiler import (
    PhysicalProcess,
    PhysicalTopology,
    ProcessPool,
    ProcessRequirement,
    ResponsibilityLifecycle,
)
from unified_compiler.adapters import (
    CityLearnV10Adapter,
    EV2GymV10Adapter,
    LegacyArtifactError,
    UnknownEpisodeError,
    V10ArtifactIndex,
    V10RuntimeBridge,
)
from unified_compiler.adapters.legacy_v10 import (
    DEFAULT_ARTIFACT_DIR,
    PRIVATE_FILENAME,
    PUBLIC_FILENAME,
    _read_jsonl,
)
from unified_compiler.adapters.legacy_v10_runtime import V10_RUNTIME_MODULE_NAME

V11_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not DEFAULT_ARTIFACT_DIR.is_dir(), reason="frozen v10 artifacts not present"
)


def _requirement(requirement_id: str, capabilities: frozenset[str]) -> ProcessRequirement:
    return ProcessRequirement(
        requirement_id=requirement_id,
        responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
        physical_topology=PhysicalTopology.THERMAL_DYNAMICS,
        required_capabilities=capabilities,
        state_variables=(),
        action_types=(),
    )


def _all(processes: Sequence[PhysicalProcess]) -> dict[str, PhysicalProcess]:
    return {p.process_id: p for p in processes}


def _v11_files() -> set[str]:
    ignored = {"__pycache__", ".pytest_cache"}
    return {
        str(path.relative_to(V11_ROOT))
        for path in V11_ROOT.rglob("*")
        if path.is_file() and not ignored & set(path.parts)
    }


@pytest.fixture(scope="module")
def citylearn() -> CityLearnV10Adapter:
    return CityLearnV10Adapter()


@pytest.fixture(scope="module")
def ev2gym() -> EV2GymV10Adapter:
    return EV2GymV10Adapter()


@pytest.fixture(scope="module")
def hvac_processes(citylearn: CityLearnV10Adapter) -> tuple[PhysicalProcess, ...]:
    return tuple(
        citylearn.scan([_requirement("hvac", frozenset({"thermal.zone_temperature", "thermal.hvac_action"}))])
    )


@pytest.fixture(scope="module")
def ev_processes(ev2gym: EV2GymV10Adapter) -> tuple[PhysicalProcess, ...]:
    return tuple(
        ev2gym.scan([_requirement("ev", frozenset({"ev.soc", "ev.charge_action"}))])
    )


def test_inventory_counts_match_frozen_artifacts(
    hvac_processes: tuple[PhysicalProcess, ...],
    ev_processes: tuple[PhysicalProcess, ...],
) -> None:
    assert len(hvac_processes) == 50
    assert len(ev_processes) == 36
    assert len(hvac_processes) + len(ev_processes) == 86

    private = _read_jsonl(DEFAULT_ARTIFACT_DIR / PRIVATE_FILENAME)
    public = _read_jsonl(DEFAULT_ARTIFACT_DIR / PUBLIC_FILENAME)
    assert len(private) == 95
    assert len(public) == 95
    assert {p.process_id for p in hvac_processes + ev_processes} == {
        record["physical_process_id"] for record in private
    }


def test_all_95_episodes_map_onto_processes(
    hvac_processes: tuple[PhysicalProcess, ...],
    ev_processes: tuple[PhysicalProcess, ...],
) -> None:
    mapped: list[str] = []
    for process in hvac_processes + ev_processes:
        mapped.extend(process.manifest["legacy_episode_ids"])
    assert len(mapped) == 95
    assert len(set(mapped)) == 95
    public = _read_jsonl(DEFAULT_ARTIFACT_DIR / PUBLIC_FILENAME)
    assert set(mapped) == {record["episode_id"] for record in public}


def test_backend_names_match_process_backend(
    citylearn: CityLearnV10Adapter,
    ev2gym: EV2GymV10Adapter,
    hvac_processes: tuple[PhysicalProcess, ...],
    ev_processes: tuple[PhysicalProcess, ...],
) -> None:
    assert citylearn.backend == "CityLearn"
    assert ev2gym.backend == "EV2Gym"
    assert {p.backend for p in hvac_processes} == {citylearn.backend}
    assert {p.backend for p in ev_processes} == {ev2gym.backend}


def test_capabilities_and_provenance_fields(
    hvac_processes: tuple[PhysicalProcess, ...],
    ev_processes: tuple[PhysicalProcess, ...],
) -> None:
    for process in hvac_processes:
        assert process.provided_capabilities == frozenset(
            {"thermal.zone_temperature", "thermal.hvac_action"}
        )
    for process in ev_processes:
        assert process.provided_capabilities == frozenset({"ev.soc", "ev.charge_action"})
    for process in hvac_processes + ev_processes:
        assert process.source_id
        assert process.source_hash.startswith("sha256:")
        assert len(process.source_hash) == len("sha256:") + 64
        assert process.horizon_steps > 0
        assert process.observation_interval_seconds > 0


def test_scan_loads_artifacts_once_per_instance() -> None:
    adapter = CityLearnV10Adapter()
    req = _requirement("hvac", frozenset({"thermal.zone_temperature"}))
    first = adapter.scan([req])
    second = adapter.scan([req])
    adapter.scan([req])
    assert adapter.scan_calls == 3
    assert adapter.index.load_count == 1
    assert [p.process_id for p in first] == [p.process_id for p in second]

    shared_index = V10ArtifactIndex()
    CityLearnV10Adapter(index=shared_index).scan([req])
    EV2GymV10Adapter(index=shared_index).scan(
        [_requirement("ev", frozenset({"ev.soc"}))]
    )
    assert shared_index.load_count == 1


def test_scan_matches_only_fulfilled_requirements(citylearn: CityLearnV10Adapter) -> None:
    req_match = _requirement("hvac", frozenset({"thermal.hvac_action"}))
    req_no_match = _requirement("storage", frozenset({"storage.soc"}))
    assert len(citylearn.scan([req_match])) == 50
    assert citylearn.scan([req_no_match]) == ()
    assert len(citylearn.scan([req_match, req_no_match])) == 50
    assert citylearn.scan([]) == ()


def test_pool_deduplicates_across_adapters(
    hvac_processes: tuple[PhysicalProcess, ...],
    ev_processes: tuple[PhysicalProcess, ...],
) -> None:
    pool = ProcessPool()
    for process in hvac_processes + ev_processes:
        pool.add(process)
    for process in hvac_processes + ev_processes:
        pool.add(process)
    assert len(pool) == 86
    assert pool.add_count == 86
    assert pool.dedup_count == 86


def test_primary_ids_and_source_hashes_stable() -> None:
    def snapshot() -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for adapter, caps in (
            (CityLearnV10Adapter(), {"thermal.zone_temperature", "thermal.hvac_action"}),
            (EV2GymV10Adapter(), {"ev.soc", "ev.charge_action"}),
        ):
            for process in adapter.scan([_requirement("r", frozenset(caps))]):
                rows.append((process.process_id, process.source_hash))
        return sorted(rows)

    first, second = snapshot(), snapshot()
    assert first == second
    assert len({pid for pid, _ in first}) == 86
    assert len({h for _, h in first}) == 86


def test_manifest_excludes_hidden_contract_and_gold_actions(
    hvac_processes: tuple[PhysicalProcess, ...],
    ev_processes: tuple[PhysicalProcess, ...],
) -> None:
    forbidden = (
        "responsibility_contract",
        "obligations",
        "objectives",
        "priority",
        "gold",
        "gold_action",
        "allowed_solver",
        "selection_lineage",
        "activation",
    )
    private = _read_jsonl(DEFAULT_ARTIFACT_DIR / PRIVATE_FILENAME)
    goldish_keys = {"solver_result", "reference_actions", "gold_actions"}
    for process in hvac_processes + ev_processes:
        manifest = process.manifest
        json.dumps(manifest)
        assert set(manifest) == {
            "migration",
            "artifact_ref",
            "binding_ref",
            "legacy_episode_ids",
            "provenance",
            "lifecycle",
        }
        blob = json.dumps(manifest).lower()
        for term in forbidden:
            assert term not in blob, f"{process.process_id} leaks {term!r}"
        assert manifest["binding_ref"].startswith(f"{PRIVATE_FILENAME}#physical_process_id=")
        assert not Path(manifest["artifact_ref"]).is_absolute()

    for record in private:
        assert "responsibility_contract" in record  # hidden source payload we must NOT copy
        assert not goldish_keys & set(record)


def test_scans_create_no_files_inside_v11() -> None:
    before = _v11_files()
    for adapter, caps in (
        (CityLearnV10Adapter(), {"thermal.zone_temperature", "thermal.hvac_action"}),
        (EV2GymV10Adapter(), {"ev.soc", "ev.charge_action"}),
    ):
        adapter.capabilities()
        adapter.scan([_requirement("r", frozenset(caps))])
    assert _v11_files() == before
    assert not (V11_ROOT / ".runtime").exists()


def test_scan_fails_closed_on_missing_artifacts(tmp_path: Path) -> None:
    adapter = CityLearnV10Adapter(artifact_dir=tmp_path / "nope")
    with pytest.raises(LegacyArtifactError):
        adapter.scan([_requirement("hvac", frozenset({"thermal.zone_temperature"}))])


def test_scan_fails_closed_on_inconsistent_artifacts(tmp_path: Path) -> None:
    public_records = _read_jsonl(DEFAULT_ARTIFACT_DIR / PUBLIC_FILENAME)
    private_records = _read_jsonl(DEFAULT_ARTIFACT_DIR / PRIVATE_FILENAME)
    dropped = private_records[0]["episode_id"]
    public_records = [r for r in public_records if r["episode_id"] != dropped]
    (tmp_path / PUBLIC_FILENAME).write_text(
        "\n".join(json.dumps(r) for r in public_records) + "\n"
    )
    (tmp_path / PRIVATE_FILENAME).write_text(
        "\n".join(json.dumps(r) for r in private_records) + "\n"
    )
    adapter = CityLearnV10Adapter(artifact_dir=tmp_path)
    with pytest.raises(LegacyArtifactError):
        adapter.scan([_requirement("hvac", frozenset({"thermal.zone_temperature"}))])


def test_bridge_resolves_pairs_without_runtime() -> None:
    bridge = V10RuntimeBridge()
    private = _read_jsonl(DEFAULT_ARTIFACT_DIR / PRIVATE_FILENAME)
    process_id = private[0]["physical_process_id"]
    episode_ids = bridge.episode_ids_for_process(process_id)
    assert episode_ids
    public, hidden = bridge.resolve_pair(episode_ids[0])
    assert public["episode_id"] == episode_ids[0]
    assert hidden["episode_id"] == episode_ids[0]
    assert hidden["physical_process_id"] == process_id
    pairs = bridge.resolve_pairs(process_id=process_id)
    assert len(pairs) == len(episode_ids)
    with pytest.raises(UnknownEpisodeError):
        bridge.resolve_pair("no-such-episode")
    assert V10_RUNTIME_MODULE_NAME not in sys.modules


def test_bridge_not_instantiated_by_scan(
    hvac_processes: tuple[PhysicalProcess, ...],
    ev_processes: tuple[PhysicalProcess, ...],
) -> None:
    assert hvac_processes and ev_processes
    assert V10_RUNTIME_MODULE_NAME not in sys.modules
