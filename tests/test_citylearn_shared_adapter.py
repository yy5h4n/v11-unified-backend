"""Tests for the shared CityLearn data-probe adapter, against the real frozen data.

Everything here is read-only: probing indexes the existing v10 JSONL bindings,
the v5 schema.json, and source-cache CSV headers/assets in place, and never
creates files inside v11 nor instantiates any simulator backend.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

import pytest

from unified_compiler import (
    AdapterRegistry,
    CapabilityStatus,
    PhysicalProcess,
    PhysicalTopology,
    ProcessPool,
    ProcessRequirement,
    ResponsibilityLifecycle,
    UnifiedCompiler,
)
from unified_compiler.adapters import (
    CityLearnSharedAdapter,
    CityLearnSourceIndex,
    LegacyArtifactError,
)
from unified_compiler.adapters.citylearn_shared import (
    DEFAULT_V5_SOURCE_CACHE,
    SCHEMA_FILENAME,
    SUBSYSTEM_BATTERY_PV,
    SUBSYSTEM_DHW_STORAGE,
    SUBSYSTEM_HVAC,
    SUBSYSTEM_THERMAL_STORAGE,
)
from unified_compiler.adapters.legacy_v10 import DEFAULT_ARTIFACT_DIR
from unified_compiler.adapters.legacy_v10_runtime import V10_RUNTIME_MODULE_NAME

pytestmark = pytest.mark.skipif(
    not DEFAULT_ARTIFACT_DIR.is_dir() or not DEFAULT_V5_SOURCE_CACHE.is_dir(),
    reason="frozen v10 artifacts or v5 source cache not present",
)

HVAC_CAPS = frozenset({"thermal.zone_temperature", "thermal.hvac_action"})
BATTERY_PV_CAPS = frozenset(
    {"storage.soc", "storage.charge_discharge_action", "pv.generation_profile"}
)
DHW_CAPS = frozenset({"storage.soc", "storage.charge_discharge_action", "dhw.demand_profile"})
THERMAL_STORAGE_CAPS = frozenset(
    {"storage.soc", "storage.charge_discharge_action", "thermal.cooling_load"}
)

N_BUNDLES = 50
N_BUILDINGS = 12
N_HVAC_EPISODES = 54


def _requirement(requirement_id: str, capabilities: frozenset[str]) -> ProcessRequirement:
    return ProcessRequirement(
        requirement_id=requirement_id,
        responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
        physical_topology=PhysicalTopology.STORAGE_DYNAMICS,
        required_capabilities=capabilities,
        state_variables=(),
        action_types=(),
    )


def _subsystem(processes: Sequence[PhysicalProcess], subsystem: str) -> tuple[PhysicalProcess, ...]:
    return tuple(p for p in processes if p.manifest["subsystem"] == subsystem)


@pytest.fixture(scope="module")
def source_index() -> CityLearnSourceIndex:
    return CityLearnSourceIndex()


@pytest.fixture(scope="module")
def adapter(source_index: CityLearnSourceIndex) -> CityLearnSharedAdapter:
    return CityLearnSharedAdapter(source_index=source_index)


@pytest.fixture(scope="module")
def processes(adapter: CityLearnSharedAdapter) -> tuple[PhysicalProcess, ...]:
    return adapter.processes()


def test_bundles_deduplicate_windows_and_load_once(source_index: CityLearnSourceIndex) -> None:
    bundles = source_index.bundles()
    assert len(bundles) == N_BUNDLES
    assert len({b.bundle_id for b in bundles}) == N_BUNDLES
    assert len({b.building_id for b in bundles}) == N_BUILDINGS
    assert sum(len(b.legacy_episode_ids) for b in bundles) == N_HVAC_EPISODES
    assert all(
        b.bundle_id
        == f"citylearn-resstock://{b.building_id}/rows:{b.source_start_row}-{b.source_end_row}"
        for b in bundles
    )
    assert all(b.horizon_steps > 0 for b in bundles)
    assert source_index.bundles() is bundles

    adapter = CityLearnSharedAdapter(source_index=source_index)
    adapter.capabilities()
    adapter.processes()
    adapter.scan([_requirement("hvac", HVAC_CAPS)])
    adapter.probe_report()
    assert adapter.source_index.v10_index.load_count == 1
    assert source_index.schema_load_count == 1
    assert source_index.header_loads == N_BUILDINGS
    assert source_index.row_count_loads == N_BUILDINGS


def test_hvac_windows_reuse_shared_bundles_without_envs(
    source_index: CityLearnSourceIndex,
    adapter: CityLearnSharedAdapter,
    processes: tuple[PhysicalProcess, ...],
) -> None:
    hvac = _subsystem(processes, SUBSYSTEM_HVAC)
    assert len(hvac) == N_BUNDLES
    bundle_ids = {b.bundle_id for b in source_index.bundles()}
    assert {p.manifest["source_bundle_id"] for p in hvac} == bundle_ids
    assert len({p.process_id for p in hvac}) == N_BUNDLES
    for process in hvac:
        assert process.domain == "hvac"
        assert process.provided_capabilities == HVAC_CAPS
        assert "dynamics_model" in process.manifest["assets"]
    # scanning never touches a simulator runtime
    assert V10_RUNTIME_MODULE_NAME not in sys.modules
    assert "citylearn" not in sys.modules


def test_battery_pv_source_candidates_share_all_hvac_bundles(
    source_index: CityLearnSourceIndex,
    adapter: CityLearnSharedAdapter,
    processes: tuple[PhysicalProcess, ...],
) -> None:
    assert _subsystem(processes, SUBSYSTEM_BATTERY_PV) == ()
    bundle_ids = {b.bundle_id for b in source_index.bundles()}
    report = adapter.probe_report()
    shared = {
        bundle_id
        for bundle_id, verdicts in report["per_bundle"].items()
        if SUBSYSTEM_BATTERY_PV in verdicts["source_candidates"]
        and SUBSYSTEM_HVAC in verdicts["source_candidates"]
    }
    assert shared == bundle_ids
    assert len(shared) == N_BUNDLES


def test_dhw_and_thermal_storage_fail_closed(
    adapter: CityLearnSharedAdapter,
    processes: tuple[PhysicalProcess, ...],
) -> None:
    assert _subsystem(processes, SUBSYSTEM_DHW_STORAGE) == ()
    assert _subsystem(processes, SUBSYSTEM_THERMAL_STORAGE) == ()
    assert adapter.scan([_requirement("dhw", DHW_CAPS)]) == ()
    assert adapter.scan([_requirement("thermal", THERMAL_STORAGE_CAPS)]) == ()
    provided: set[str] = set()
    for cap in adapter.capabilities():
        provided.update(cap.provides)
    assert "dhw.demand_profile" not in provided
    assert "thermal.cooling_load" not in provided
    report = adapter.probe_report()
    assert report["subsystem_source_candidate_counts"][SUBSYSTEM_BATTERY_PV] == N_BUNDLES
    assert report["subsystem_process_ready_counts"][SUBSYSTEM_BATTERY_PV] == 0
    assert report["subsystem_candidate_counts"] == dict(
        report["subsystem_process_ready_counts"]
    )
    assert report["subsystem_candidate_counts"][SUBSYSTEM_DHW_STORAGE] == 0
    assert report["subsystem_candidate_counts"][SUBSYSTEM_THERMAL_STORAGE] == 0
    assert report["subsystem_candidate_counts"][SUBSYSTEM_HVAC] == N_BUNDLES
    assert report["subsystem_candidate_counts"][SUBSYSTEM_BATTERY_PV] == 0


def test_dhw_demand_column_alone_never_implies_storage(
    source_index: CityLearnSourceIndex,
    adapter: CityLearnSharedAdapter,
    processes: tuple[PhysicalProcess, ...],
) -> None:
    columns_seen = set()
    for entry in source_index.schema()["buildings"].values():
        csv_name = entry.get("energy_simulation")
        if isinstance(csv_name, str):
            columns = source_index.csv_columns(csv_name)
            if columns:
                columns_seen.update(columns)
    assert "dhw_demand" in columns_seen
    assert _subsystem(processes, SUBSYSTEM_DHW_STORAGE) == ()
    for verdicts in adapter.probe_report()["per_bundle"].values():
        assert verdicts["excluded"][SUBSYSTEM_DHW_STORAGE] == "out_of_probe_scope"
        assert verdicts["excluded"][SUBSYSTEM_THERMAL_STORAGE] == "out_of_probe_scope"


def test_capability_status_is_data_probed_pending_replay(
    adapter: CityLearnSharedAdapter,
) -> None:
    caps = adapter.capabilities()
    assert len(caps) == 1
    ids = {c.capability_id for c in caps}
    assert ids == {"citylearn_shared.hvac"}
    for cap in caps:
        assert cap.status is CapabilityStatus.DATA_PROBED_PENDING_REPLAY
        assert cap.backend == adapter.backend == "citylearn"
        for evidence in cap.evidence:
            assert not Path(evidence.split("#")[0]).is_absolute()


def test_process_ids_and_hashes_stable_across_instances() -> None:
    def snapshot() -> list[tuple[str, str, str]]:
        adapter = CityLearnSharedAdapter()
        return sorted(
            (p.process_id, p.source_hash, p.source_id) for p in adapter.processes()
        )

    first, second = snapshot(), snapshot()
    assert first == second
    assert len(first) == N_BUNDLES
    assert len({pid for pid, _, _ in first}) == N_BUNDLES
    assert len({h for _, h, _ in first}) == N_BUNDLES
    for pid, source_hash, source_id in first:
        assert source_hash.startswith("sha256:") and len(source_hash) == len("sha256:") + 64
        assert source_id.startswith("citylearn-resstock://")
        assert not Path(pid).is_absolute() and not Path(source_id).is_absolute()


def _walk_strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in node.items():
            out.extend(_walk_strings(key))
            out.extend(_walk_strings(value))
        return out
    if isinstance(node, (list, tuple)):
        out = []
        for value in node:
            out.extend(_walk_strings(value))
        return out
    return []


def test_manifests_have_no_absolute_paths_contract_or_traces(
    processes: tuple[PhysicalProcess, ...],
) -> None:
    forbidden = (
        "responsibility_contract",
        "obligations",
        "priority",
        "gold",
        "solver",
        "selection_lineage",
        "secret",
        "token",
        "password",
        "credential",
    )
    for process in processes:
        manifest = process.manifest
        json.dumps(manifest)
        assert set(manifest) == {
            "probe",
            "source_bundle_id",
            "subsystem",
            "building_id",
            "window",
            "assets",
            "legacy_process_ids",
            "legacy_episode_ids",
            "provenance",
        }
        blob = json.dumps(manifest).lower()
        for term in forbidden:
            assert term not in blob, f"{process.process_id} leaks {term!r}"
        for string in _walk_strings(manifest):
            assert not string.startswith("/"), f"absolute path leaked: {string!r}"
            assert string != str(DEFAULT_V5_SOURCE_CACHE).lower()


def test_scans_create_no_files_inside_v11() -> None:
    v11_root = Path(__file__).resolve().parents[1]
    ignored = {"__pycache__", ".pytest_cache"}

    def snapshot() -> set[str]:
        return {
            str(path.relative_to(v11_root))
            for path in v11_root.rglob("*")
            if path.is_file() and not ignored & set(path.parts)
        }

    before = snapshot()
    adapter = CityLearnSharedAdapter()
    adapter.capabilities()
    adapter.scan([_requirement("hvac", HVAC_CAPS)])
    adapter.scan([_requirement("battery", BATTERY_PV_CAPS)])
    adapter.probe_report()
    assert snapshot() == before


def test_fail_closed_when_schema_missing(tmp_path: Path) -> None:
    adapter = CityLearnSharedAdapter(source_cache_dir=tmp_path / "nope")
    with pytest.raises(LegacyArtifactError):
        adapter.capabilities()


def _write_synthetic_cache(tmp_path: Path, building: dict[str, Any], csv_header: str) -> Path:
    cache = tmp_path / "source_cache"
    cache.mkdir()
    schema = {
        "seconds_per_time_step": 3600,
        "actions": {
            "cooling_or_heating_device": {"active": True},
            "electrical_storage": {"active": True},
            "dhw_storage": {"active": True},
        },
        "buildings": {"b1": building},
    }
    (cache / SCHEMA_FILENAME).write_text(json.dumps(schema))
    (cache / "b1.csv").write_text(csv_header + "\n" + "0," * 20 + "\n" * 200)
    (cache / "b1.pth").write_bytes(b"fake-weights")
    (cache / "weather.epw").write_text("LOCATION,fake\n" + "0,0\n" * 100)
    return cache


def _write_synthetic_backend_prerequisites(tmp_path: Path) -> tuple[Path, Path]:
    citylearn_cache = tmp_path / "citylearn_cache"
    misc = citylearn_cache / "misc"
    misc.mkdir(parents=True)
    (misc / "battery_choices.yaml").write_text("battery_1:\n  capacity: 13.5\n")
    (misc / "lbl-tracking_the_sun-res-pv.csv").write_text("system,degradation\nres-pv,0.005\n")
    site_packages = tmp_path / "site-packages"
    (site_packages / "PySAM").mkdir(parents=True)
    (site_packages / "PySAM" / "__init__.py").write_text("")
    return citylearn_cache, site_packages


def _synthetic_adapter(cache: Path, tmp_path: Path) -> CityLearnSharedAdapter:
    binding = {
        "backend": "CityLearn",
        "building_id": "b1",
        "source_start_row": 0,
        "source_end_row": 23,
        "source_trace_sha256": "a" * 64,
        "thermal_model_sha256": "b" * 64,
        "version": "2.5.0",
    }
    private = {
        "episode_id": "ep1",
        "physical_process_id": "hvac_v10_fake",
        "backend_binding": binding,
    }
    public = {"episode_id": "ep1", "horizon_steps": 24, "observation_interval_minutes": 60.0}
    artifact_dir = tmp_path / "v10"
    artifact_dir.mkdir(exist_ok=True)
    (artifact_dir / "episodes_private.jsonl").write_text(json.dumps(private) + "\n")
    (artifact_dir / "episodes_public.jsonl").write_text(json.dumps(public) + "\n")
    citylearn_cache, site_packages = _write_synthetic_backend_prerequisites(tmp_path)
    return CityLearnSharedAdapter(
        artifact_dir=artifact_dir,
        source_cache_dir=cache,
        citylearn_cache_dir=citylearn_cache,
        backend_site_packages_dir=site_packages,
    )


def _full_building() -> dict[str, Any]:
    return {
        "include": True,
        "energy_simulation": "b1.csv",
        "dynamics": {"type": "citylearn.dynamics.LSTMDynamics", "attributes": {"filename": "b1.pth"}},
        "electrical_storage": {"type": "citylearn.energy_model.Battery", "autosize": True},
        "pv": {
            "type": "citylearn.energy_model.PV",
            "autosize": True,
            "autosize_attributes": {"epw_filepath": "weather.epw"},
        },
        "dhw_storage": None,
        "cooling_storage": None,
        "heating_storage": None,
        "inactive_actions": [],
    }


def test_synthetic_positive_probe(tmp_path: Path) -> None:
    cache = _write_synthetic_cache(
        tmp_path,
        _full_building(),
        "cooling_demand,heating_demand,indoor_dry_bulb_temperature,solar_generation,non_shiftable_load,dhw_demand",
    )
    adapter = _synthetic_adapter(cache, tmp_path)
    processes = adapter.processes()
    assert {p.manifest["subsystem"] for p in processes} == {
        SUBSYSTEM_HVAC,
        SUBSYSTEM_BATTERY_PV,
    }
    assert len({p.manifest["source_bundle_id"] for p in processes}) == 1
    report = adapter.probe_report()
    assert report["subsystem_candidate_counts"] == {
        SUBSYSTEM_HVAC: 1,
        SUBSYSTEM_BATTERY_PV: 1,
        SUBSYSTEM_DHW_STORAGE: 0,
        SUBSYSTEM_THERMAL_STORAGE: 0,
    }


_FULL_CSV_HEADER = (
    "cooling_demand,heating_demand,indoor_dry_bulb_temperature,"
    "solar_generation,non_shiftable_load,dhw_demand"
)


@pytest.mark.parametrize(
    "prerequisite,expected_reason_fragment",
    [
        ("battery_choices", "battery_choices.yaml"),
        ("tracking_the_sun", "lbl-tracking_the_sun-res-pv.csv"),
        ("epw_file", "epw file missing"),
        ("pysam", "PySAM"),
    ],
)
def test_battery_pv_fails_closed_without_prerequisite(
    tmp_path: Path, prerequisite: str, expected_reason_fragment: str
) -> None:
    cache = _write_synthetic_cache(tmp_path, _full_building(), _FULL_CSV_HEADER)
    adapter = _synthetic_adapter(cache, tmp_path)
    if prerequisite == "battery_choices":
        (tmp_path / "citylearn_cache" / "misc" / "battery_choices.yaml").unlink()
    elif prerequisite == "tracking_the_sun":
        (tmp_path / "citylearn_cache" / "misc" / "lbl-tracking_the_sun-res-pv.csv").unlink()
    elif prerequisite == "epw_file":
        (cache / "weather.epw").unlink()
    elif prerequisite == "pysam":
        shutil.rmtree(tmp_path / "site-packages" / "PySAM")
    processes = adapter.processes()
    assert {p.manifest["subsystem"] for p in processes} == {SUBSYSTEM_HVAC}
    bundle_verdicts = adapter.probe_report()["per_bundle"].popitem()[1]
    assert SUBSYSTEM_BATTERY_PV in bundle_verdicts["source_candidates"]
    assert SUBSYSTEM_BATTERY_PV not in bundle_verdicts["ready"]
    reason = bundle_verdicts["excluded"][SUBSYSTEM_BATTERY_PV]
    assert expected_reason_fragment in reason


def test_missing_pv_column_yields_zero_battery_processes(tmp_path: Path) -> None:
    cache = _write_synthetic_cache(
        tmp_path,
        _full_building(),
        "cooling_demand,heating_demand,indoor_dry_bulb_temperature",
    )
    adapter = _synthetic_adapter(cache, tmp_path)
    processes = adapter.processes()
    assert {p.manifest["subsystem"] for p in processes} == {SUBSYSTEM_HVAC}
    reason = adapter.probe_report()["per_bundle"].popitem()[1]["excluded"][SUBSYSTEM_BATTERY_PV]
    assert "solar_generation" in reason


def test_null_storage_component_yields_zero_battery_processes(tmp_path: Path) -> None:
    building = _full_building()
    building["electrical_storage"] = None
    cache = _write_synthetic_cache(
        tmp_path,
        building,
        "cooling_demand,heating_demand,indoor_dry_bulb_temperature,solar_generation,non_shiftable_load",
    )
    adapter = _synthetic_adapter(cache, tmp_path)
    assert {p.manifest["subsystem"] for p in adapter.processes()} == {SUBSYSTEM_HVAC}


def test_configured_dhw_tank_still_emits_zero_out_of_scope(tmp_path: Path) -> None:
    building = _full_building()
    building["dhw_storage"] = {"type": "citylearn.energy_model.StorageTank", "autosize": True}
    cache = _write_synthetic_cache(
        tmp_path,
        building,
        "cooling_demand,heating_demand,indoor_dry_bulb_temperature,solar_generation,non_shiftable_load,dhw_demand",
    )
    adapter = _synthetic_adapter(cache, tmp_path)
    assert adapter.scan([_requirement("dhw", DHW_CAPS)]) == ()
    assert _subsystem(adapter.processes(), SUBSYSTEM_DHW_STORAGE) == ()


def test_compiler_integration_with_shared_adapter(
    source_index: CityLearnSourceIndex,
) -> None:
    registry = AdapterRegistry()
    registry.register(CityLearnSharedAdapter(source_index=source_index))
    compiler = UnifiedCompiler(registry, pool=ProcessPool())
    report = compiler.compile(
        [
            _requirement("hvac_req", HVAC_CAPS),
            _requirement("battery_req", BATTERY_PV_CAPS),
            _requirement("dhw_req", DHW_CAPS),
            _requirement("thermal_req", THERMAL_STORAGE_CAPS),
        ]
    )
    assert {r.requirement_id for r in report.unsupported_requirements} == {
        "battery_req",
        "dhw_req",
        "thermal_req",
    }
    assert report.unsatisfied_requirements == ()
    assert len(report.processes) == N_BUNDLES
    assert len(compiler.pool) == N_BUNDLES
    assert report.scans_per_adapter == {"citylearn": 1}
