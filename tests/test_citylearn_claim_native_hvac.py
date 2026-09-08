"""Native v11 CityLearn HVAC episode route: focused claim-facade tests.

The route-selection, validation, and fail-closed tests run against the real
frozen v10 artifacts and the real source probe without importing CityLearn
(the verified episode runtime is replaced by an in-test fake). One integration
test runs the real pinned CityLearn 2.5.0 runtime end to end and is skipped
when that pinned runtime is unavailable.

Everything is read-only: no files are written inside v11.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

from unified_compiler import (
    CapabilityStatus,
    PhysicalTopology,
    ProcessRequirement,
    ResponsibilityLifecycle,
)
from unified_compiler.adapters import legacy_v10_runtime
from unified_compiler.adapters import citylearn_claim as claim_module
from unified_compiler.adapters.citylearn_claim import (
    ROUTE_BATTERY_PV,
    ROUTE_HVAC_NATIVE,
    ClaimActionError,
    ClaimEpisodeError,
    CityLearnBatteryPVEpisode,
    CityLearnClaimAdapter,
    CityLearnNativeHvacEpisode,
)
from unified_compiler.adapters.legacy_v10 import DEFAULT_ARTIFACT_DIR, V10ArtifactIndex

pytestmark = pytest.mark.skipif(
    not DEFAULT_ARTIFACT_DIR.is_dir(), reason="frozen v10 artifacts not present"
)


def _hvac_requirement() -> ProcessRequirement:
    return ProcessRequirement(
        requirement_id="hvac-thermal",
        responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
        physical_topology=PhysicalTopology.THERMAL_DYNAMICS,
        required_capabilities=frozenset(
            {"thermal.zone_temperature", "thermal.hvac_action"}
        ),
        state_variables=(),
        action_types=(),
    )


@pytest.fixture(scope="module")
def adapter() -> CityLearnClaimAdapter:
    return CityLearnClaimAdapter()


@pytest.fixture(scope="module")
def hvac_process(adapter: CityLearnClaimAdapter):
    processes = [
        process
        for process in adapter.scan([_hvac_requirement()])
        if process.manifest.get("subsystem") == "hvac"
    ]
    assert processes, "expected at least one probed HVAC process"
    return processes[0]


# -- fakes ---------------------------------------------------------------


class _FakeRuntime:
    """Mirrors the verified CityLearnEpisodeRuntime surface, no CityLearn."""

    def __init__(self, public: Mapping[str, Any], private: Mapping[str, Any]) -> None:
        assert public["episode_id"] == private["episode_id"]
        binding = private["backend_binding"]
        self._hidden_start = int(binding["hidden_initialization_start_row"])
        self._start = int(binding["source_start_row"])
        self._end = int(binding["source_end_row"])
        self._time_step = self._start - self._hidden_start
        self.done = False
        self.step_actions: list[Any] = []

    @property
    def source_row(self) -> int:
        return self._hidden_start + self._time_step

    def observe(self) -> dict[str, float]:
        return {
            "virtual_hour": 13,
            "occupant_count": 2.0,
            "indoor_temperature_c": 18.337,
            "resident_setpoint_c": 18.333,
            "outdoor_temperature_c": 15.7,
        }

    def step(self, action: Any) -> dict[str, Any]:
        row = self.source_row
        self.step_actions.append(action)
        self._time_step += 1
        self.done = row >= self._end
        return {
            "source_row": row,
            "backend_action": 0.0,
            "effect": {"indoor_temperature_c": 18.0, "hvac_energy_kwh": 0.0},
            "backend_terminated": False,
            "observation": self.observe(),
            "requested_mode": action,
            "effective_mode": action,
            "episode_done": self.done,
        }


class _FakeRuntimeModule:
    CityLearnEpisodeRuntime = _FakeRuntime


@pytest.fixture()
def forbidding_v10_runtime_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee the frozen v10 runtime chain is never touched on this path."""

    def _forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("V10RuntimeBridge / v10 runtime used on the HVAC path")

    monkeypatch.setattr(
        legacy_v10_runtime.V10RuntimeBridge, "runtime_for_episode", _forbidden
    )
    monkeypatch.setattr(legacy_v10_runtime.V10RuntimeBridge, "replay", _forbidden)
    monkeypatch.setattr(
        legacy_v10_runtime.V10RuntimeBridge, "resolve_pair", _forbidden
    )
    monkeypatch.setattr(legacy_v10_runtime, "_load_v10_runtime", _forbidden)


@pytest.fixture()
def fake_hvac_runtime(monkeypatch: pytest.MonkeyPatch) -> list[_FakeRuntime]:
    created: list[_FakeRuntime] = []

    def _load() -> _FakeRuntimeModule:
        return _FakeRuntimeModule

    original_init = _FakeRuntime.__init__

    def _tracking_init(self: _FakeRuntime, public: Any, private: Any) -> None:
        original_init(self, public, private)
        created.append(self)

    monkeypatch.setattr(_FakeRuntime, "__init__", _tracking_init)
    monkeypatch.setattr(claim_module, "_load_hvac_runtime", _load)
    return created


# -- route selection -----------------------------------------------------


def test_open_episode_selects_native_hvac_route(
    adapter: CityLearnClaimAdapter, hvac_process
) -> None:
    assert not hasattr(adapter, "_bridge"), "adapter must not carry a V10RuntimeBridge"
    episode = adapter.open_episode(hvac_process.process_id)
    assert isinstance(episode, CityLearnNativeHvacEpisode)
    assert not isinstance(episode, CityLearnBatteryPVEpisode)
    state = episode.private_state()
    assert state["route"] == ROUTE_HVAC_NATIVE
    assert state["execution"] == "native_verified_citylearn_runtime"
    assert state["episode_id"] == hvac_process.manifest["legacy_episode_ids"][0]


def test_battery_route_selection_is_unchanged(
    adapter: CityLearnClaimAdapter,
) -> None:
    adapter._ensure_processes()
    battery_processes = [
        process
        for process in adapter._processes or ()
        if adapter._routes.get(process.process_id) == ROUTE_BATTERY_PV
    ]
    assert battery_processes, "expected battery/PV processes from the verified pool"
    episode = adapter.open_episode(battery_processes[0].process_id)
    assert isinstance(episode, CityLearnBatteryPVEpisode)
    capabilities = {cap.capability_id: cap.status for cap in adapter.capabilities()}
    assert capabilities["citylearn_claim.battery_pv"] is (
        CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED
    )
    assert capabilities["citylearn_claim.hvac"] is (
        CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED
    )


# -- reset / step behavior (fake runtime, no CityLearn import) ------------


def test_native_hvac_reset_step_done_without_v10_runtime_bridge(
    adapter: CityLearnClaimAdapter,
    hvac_process,
    fake_hvac_runtime: list[_FakeRuntime],
    forbidding_v10_runtime_bridge: None,
) -> None:
    episode = adapter.open_episode(hvac_process.process_id)
    assert isinstance(episode, CityLearnNativeHvacEpisode)
    window = hvac_process.manifest["window"]
    start = int(window["source_start_row"])
    end = int(window["source_end_row"])

    observation = episode.reset()
    assert observation["source_step"] == start
    assert {"virtual_hour", "occupant_count", "indoor_temperature_c"} <= set(observation)
    assert episode.legal_actions() == {
        "ECO_OFF": {},
        "MAINTAIN_COMFORT": {},
        "WAIT": {"semantics": "continue the previously selected mode"},
    }
    assert episode.legal_actions() is not episode._allowed  # defensive copy

    expected_modes: list[str] = []
    while True:
        occupied = episode.observe()["occupant_count"] > 0
        mode = "MAINTAIN_COMFORT" if occupied else "ECO_OFF"
        result = episode.step(mode)
        expected_modes.append(mode)
        assert result["step_index"] == len(expected_modes) - 1
        assert start <= result["source_step"] <= end
        assert result["observation"]["requested_mode"] == mode
        if result["done"]:
            break

    assert len(expected_modes) == hvac_process.horizon_steps
    assert result["source_step"] == end
    assert episode.private_state()["steps_taken"] == hvac_process.horizon_steps
    runtime = fake_hvac_runtime[0]
    assert runtime.step_actions == expected_modes
    with pytest.raises(ClaimEpisodeError, match="already done"):
        episode.step("ECO_OFF")


def test_native_hvac_invalid_actions_fail_closed(
    adapter: CityLearnClaimAdapter,
    hvac_process,
    fake_hvac_runtime: list[_FakeRuntime],
    forbidding_v10_runtime_bridge: None,
) -> None:
    episode = adapter.open_episode(hvac_process.process_id)
    with pytest.raises(ClaimEpisodeError, match="reset"):
        episode.observe()
    with pytest.raises(ClaimEpisodeError, match="reset"):
        episode.step("ECO_OFF")
    with pytest.raises(ClaimEpisodeError, match="reset"):
        episode.legal_actions()

    episode.reset()
    invalid_actions: list[Any] = [
        "SET_CHARGE_POWER",
        "maintain_comfort",
        {"type": "ECO_OFF"},
        0.5,
        None,
    ]
    for action in invalid_actions:
        with pytest.raises(ClaimActionError):
            episode.step(action)
    assert episode.private_state()["steps_taken"] == 0
    assert fake_hvac_runtime[0].step_actions == []


# -- fail closed: missing assets / runtime --------------------------------


def test_missing_hvac_runtime_file_fails_closed(
    adapter: CityLearnClaimAdapter,
    hvac_process,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        claim_module, "HVAC_RUNTIME_SOURCE", tmp_path / "absent" / "episode_runtime.py"
    )
    episode = adapter.open_episode(hvac_process.process_id)
    assert isinstance(episode, CityLearnNativeHvacEpisode)
    with pytest.raises(ClaimEpisodeError, match="missing verified CityLearn"):
        episode.reset()
    assert episode.private_state()["steps_taken"] == 0


def test_unavailable_pinned_runtime_fails_closed(
    adapter: CityLearnClaimAdapter,
    hvac_process,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> None:
        raise ClaimEpisodeError("pinned CityLearn runtime unavailable")

    monkeypatch.setattr(claim_module, "_load_hvac_runtime", _raise)
    episode = adapter.open_episode(hvac_process.process_id)
    with pytest.raises(ClaimEpisodeError, match="pinned CityLearn runtime"):
        episode.reset()


def test_missing_source_assets_fail_closed(
    adapter: CityLearnClaimAdapter,
    hvac_process,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = adapter.open_episode(hvac_process.process_id)
    assert isinstance(episode, CityLearnNativeHvacEpisode)
    building_id = str(hvac_process.manifest["building_id"])
    index = adapter._shared.source_index

    with pytest.raises(ClaimEpisodeError, match="absent from the pinned v5 source schema"):
        episode._ensure_source_assets("resstock-amy2018-2021-release-1-not-a-building")

    monkeypatch.setattr(index, "csv_columns", lambda name: None)
    with pytest.raises(ClaimEpisodeError, match="energy_simulation csv missing"):
        episode._ensure_source_assets(building_id)

    monkeypatch.setattr(index, "csv_columns", lambda name: frozenset({"x"}))
    monkeypatch.setattr(index, "asset_exists", lambda name: False)
    with pytest.raises(ClaimEpisodeError, match="thermal dynamics model missing"):
        episode._ensure_source_assets(building_id)


@pytest.mark.parametrize(
    ("asset_label", "asset_selector"),
    [
        ("source schema", lambda index, entry: claim_module.SCHEMA_FILENAME),
        ("source CSV", lambda index, entry: entry["energy_simulation"]),
        (
            "thermal dynamics model",
            lambda index, entry: entry["dynamics"]["attributes"]["filename"],
        ),
    ],
)
def test_native_hvac_rejects_changed_pinned_data_assets(
    adapter: CityLearnClaimAdapter,
    hvac_process,
    monkeypatch: pytest.MonkeyPatch,
    asset_label: str,
    asset_selector,
) -> None:
    """A same-name replacement must not pass the native evidence gate."""
    episode = adapter.open_episode(hvac_process.process_id)
    assert isinstance(episode, CityLearnNativeHvacEpisode)
    building_id = str(hvac_process.manifest["building_id"])
    index = adapter._shared.source_index
    entry = index.building_entry(building_id)
    assert entry is not None
    asset_name = asset_selector(index, entry)
    asset_path = (index.source_cache_dir / asset_name).resolve()
    original_digest = claim_module._sha256_file

    def _tampered_digest(path: Path) -> str:
        if path.resolve() == asset_path:
            return "0" * 64
        return original_digest(path)

    monkeypatch.setattr(claim_module, "_sha256_file", _tampered_digest)
    with pytest.raises(ClaimEpisodeError, match=f"pinned {asset_label} digest mismatch"):
        episode._ensure_source_assets(building_id)


def test_native_hvac_rejects_binding_digest_not_in_source_manifest(
    adapter: CityLearnClaimAdapter,
    hvac_process,
) -> None:
    episode = adapter.open_episode(hvac_process.process_id)
    assert isinstance(episode, CityLearnNativeHvacEpisode)
    building_id = str(hvac_process.manifest["building_id"])
    _, private = episode._resolve_pair()
    binding = dict(private["backend_binding"])
    binding["source_trace_sha256"] = "0" * 64
    with pytest.raises(ClaimEpisodeError, match="source CSV provenance digest mismatch"):
        episode._ensure_source_assets(building_id, binding=binding)


@pytest.mark.parametrize(
    "runtime_attr",
    ["HVAC_RUNTIME_BOOTSTRAP_SOURCE", "HVAC_RUNTIME_PROBE_SOURCE", "HVAC_RUNTIME_SOURCE"],
)
def test_native_hvac_rejects_changed_runtime_asset(
    adapter: CityLearnClaimAdapter,
    hvac_process,
    monkeypatch: pytest.MonkeyPatch,
    runtime_attr: str,
) -> None:
    episode = adapter.open_episode(hvac_process.process_id)
    assert isinstance(episode, CityLearnNativeHvacEpisode)
    building_id = str(hvac_process.manifest["building_id"])
    runtime_path = getattr(claim_module, runtime_attr).resolve()
    original_digest = claim_module._sha256_file

    def _tampered_digest(path: Path) -> str:
        if path.resolve() == runtime_path:
            return "0" * 64
        return original_digest(path)

    monkeypatch.setattr(claim_module, "_sha256_file", _tampered_digest)
    with pytest.raises(ClaimEpisodeError, match="pinned runtime .* digest mismatch"):
        episode._ensure_source_assets(building_id)


def test_binding_window_mismatch_fails_closed(
    adapter: CityLearnClaimAdapter, hvac_process
) -> None:
    episode = CityLearnNativeHvacEpisode(
        adapter._shared.source_index,
        episode_id=str(hvac_process.manifest["legacy_episode_ids"][0]),
        process_id=hvac_process.process_id,
        expected_building_id="resstock-amy2018-2021-release-1-000000",
        expected_window=hvac_process.manifest["window"],
    )
    with pytest.raises(ClaimEpisodeError, match="does not match the scanned process window"):
        episode.reset()


def test_unknown_episode_id_fails_closed(adapter: CityLearnClaimAdapter) -> None:
    index = adapter._shared.source_index.v10_index
    assert isinstance(index, V10ArtifactIndex)
    episode = CityLearnNativeHvacEpisode(
        adapter._shared.source_index,
        episode_id="no_such_episode__000000000000000000",
        process_id="no_such_process",
        expected_building_id="resstock-amy2018-2021-release-1-102040",
        expected_window={"source_start_row": 3150, "source_end_row": 3173},
    )
    with pytest.raises(ClaimEpisodeError, match="no frozen v10 episode record"):
        episode.reset()


# -- integration: real pinned CityLearn runtime ---------------------------
#
# Executed in a subprocess so the real pinned CityLearn runtime is imported
# only there and never pollutes this process's sys.modules: sibling suites
# assert that scanning/importing the adapters never loads a simulator runtime.


_SUBPROCESS_EPISODE_SCRIPT = """
import json
import sys

V11_ROOT = {v11_root!r}
sys.path.insert(0, V11_ROOT)

import unified_compiler.adapters.citylearn_claim as claim_module
from unified_compiler import (
    PhysicalTopology,
    ProcessRequirement,
    ResponsibilityLifecycle,
)

try:
    adapter = claim_module.CityLearnClaimAdapter()
    requirement = ProcessRequirement(
        requirement_id="hvac-thermal",
        responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
        physical_topology=PhysicalTopology.THERMAL_DYNAMICS,
        required_capabilities=frozenset(
            {{"thermal.zone_temperature", "thermal.hvac_action"}}
        ),
        state_variables=(),
        action_types=(),
    )
    process = [
        p for p in adapter.scan([requirement])
        if p.manifest.get("subsystem") == "hvac"
    ][0]
    episode = adapter.open_episode(process.process_id)
    observation = episode.reset()
    steps = 0
    while True:
        occupied = episode.observe()["occupant_count"] > 0
        result = episode.step("MAINTAIN_COMFORT" if occupied else "ECO_OFF")
        steps += 1
        if result["done"]:
            break
    print(json.dumps({{
        "available": True,
        "horizon_steps": process.horizon_steps,
        "steps_completed": steps,
        "source_start": int(process.manifest["window"]["source_start_row"]),
        "source_end": int(process.manifest["window"]["source_end_row"]),
        "first_observation": observation,
        "final_source_step": result["source_step"],
        "final_effect": result["observation"]["effect"],
        "v10_runtime_module_loaded": "v10_runtime_for_v11_replay" in sys.modules,
        "citylearn_version": __import__("citylearn").__version__,
    }}))
except SystemExit:
    raise
except Exception as exc:
    print(json.dumps({{"available": False, "reason": str(exc)}}))
"""


def _run_pinned_runtime_episode() -> dict[str, Any]:
    script = _SUBPROCESS_EPISODE_SCRIPT.format(v11_root=str(Path(__file__).resolve().parents[1]))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_native_hvac_full_episode_on_pinned_runtime() -> None:
    report = _run_pinned_runtime_episode()
    if not report.get("available"):
        pytest.skip(f"pinned CityLearn 2.5.0 runtime unavailable: {report.get('reason')}")
    assert report["horizon_steps"] == report["steps_completed"]
    assert report["first_observation"]["source_step"] == report["source_start"]
    assert report["final_source_step"] == report["source_end"]
    assert report["final_effect"]["indoor_temperature_c"] > 0
    assert report["citylearn_version"].startswith("2.5.0")
    # The frozen v10 runtime chain was never loaded on the native HVAC path.
    assert report["v10_runtime_module_loaded"] is False
