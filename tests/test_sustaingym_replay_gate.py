from __future__ import annotations

import json
import importlib
from pathlib import Path

import pytest

from build_sustaingym_replay_gate import (
    DEFAULT_REPLAY_GATE,
    _max_temperature_delta,
    build_gate,
)
from unified_compiler.adapters.sustaingym_building import (
    DEFAULT_PROBE_RESULT,
    SustainGymAdapterError,
    SustainGymBuildingAdapter,
)
import unified_compiler.adapters.sustaingym_building as sustaingym_adapter


ROOT = Path(__file__).resolve().parents[1]


def test_generated_gate_is_current_and_fail_closed() -> None:
    gate = json.loads(DEFAULT_REPLAY_GATE.read_text(encoding="utf-8"))
    assert gate["schema_version"] == "sustaingym-replay-gate-v1"
    assert gate["probe_result_sha256"]
    assert gate["gold_actions_released"] is False
    assert gate["temperature_bounds_c"] == [0.0, 50.0]
    assert gate["all_temperatures_finite"] is True
    assert gate["all_temperatures_physically_plausible"] is True
    assert gate["verified"] is (
        gate["probe_evidence_valid"]
        and gate["backend_importable"]
        and gate["all_replays_completed"]
        and gate["all_replays_deterministic"]
        and gate["action_sensitive"]
    )
    if not gate["verified"]:
        assert gate["status"] == "DATA_PROBED_PENDING_REPLAY"
        assert gate["failure_reasons"]


def test_build_gate_matches_generated_artifact() -> None:
    gate = build_gate()
    if gate["verified"]:
        expected = json.dumps(gate, indent=2, sort_keys=True) + "\n"
        assert DEFAULT_REPLAY_GATE.read_text(encoding="utf-8") == expected
    else:
        # A developer machine without the pinned optional backend must still
        # exercise the fail-closed path without invalidating a verified
        # artifact produced in the backend venv.
        assert gate["status"] == "DATA_PROBED_PENDING_REPLAY"


def test_verified_verdict_requires_all_checks() -> None:
    gate = build_gate()
    assert gate["verified"] is all(
        gate[key] is True
        for key in (
            "probe_evidence_valid",
            "backend_importable",
            "all_replays_completed",
            "all_replays_deterministic",
            "action_sensitive",
        )
    )
    if gate["verified"]:
        assert len(gate["replays"]) == 4
        assert gate["all_replays_deterministic"] is True


def test_stale_gate_is_rejected_by_adapter(tmp_path: Path) -> None:
    stale = json.loads(DEFAULT_REPLAY_GATE.read_text(encoding="utf-8"))
    stale["adapter_sha256"] = "0" * 64
    stale_path = tmp_path / "stale.json"
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(SustainGymAdapterError, match="stale for this adapter"):
        SustainGymBuildingAdapter(replay_gate_path=stale_path).capabilities()


def test_probe_reference_is_the_pinned_backend_survey_result() -> None:
    assert DEFAULT_PROBE_RESULT == ROOT.parents[2] / "backend-survey" / "results" / "sustaingym.json"


def test_missing_probe_fails_closed_without_emitting_a_certificate(tmp_path: Path) -> None:
    gate = build_gate(probe_result_path=tmp_path / "missing.json")
    assert gate["verified"] is False
    assert gate["probe_evidence_valid"] is False
    assert gate["failure_reasons"]


def test_temperature_delta_uses_initial_observation_shape() -> None:
    def trajectory(values: list[float]) -> dict:
        return {
            "initial": {"zone_temperatures_c": [values[0]]},
            "transitions": [
                {
                    "transition": {
                        "observation": {"zone_temperatures_c": [value]},
                    }
                }
                for value in values[1:]
            ],
        }

    assert _max_temperature_delta(trajectory([25.0, 24.0]), trajectory([25.0, 23.0])) == 1.0


def test_project_local_runtime_is_used_only_after_normal_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module
    calls: list[str] = []

    def import_with_normal_failure(name: str):
        calls.append(name)
        if len(calls) == 1:
            raise ImportError("simulated missing installed package")
        return real_import(name)

    monkeypatch.setattr(sustaingym_adapter.importlib, "import_module", import_with_normal_failure)
    building_env, parameter_generator = SustainGymBuildingAdapter()._import_backend()
    assert building_env.__module__.startswith("sustaingym.envs.building")
    assert callable(parameter_generator)
    assert calls == ["sustaingym.envs.building", "sustaingym.envs.building"]


def test_fallback_distribution_wrong_version_fails_closed(tmp_path: Path) -> None:
    dist_info = tmp_path / "sustaingym-9.9.9.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: sustaingym\nVersion: 9.9.9\n",
        encoding="utf-8",
    )
    with pytest.raises(SustainGymAdapterError, match="does not match pinned"):
        sustaingym_adapter.SustainGymBuildingAdapter._verify_backend_version(tmp_path)


def test_missing_project_local_runtime_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sustaingym_adapter, "SUSTAINGYM_FALLBACK_SITE_PACKAGES", tmp_path / "absent")

    def always_missing(_name: str):
        raise ImportError("simulated missing package")

    monkeypatch.setattr(sustaingym_adapter.importlib, "import_module", always_missing)
    with pytest.raises(SustainGymAdapterError, match="fallback runtime is missing"):
        SustainGymBuildingAdapter()._import_backend()
