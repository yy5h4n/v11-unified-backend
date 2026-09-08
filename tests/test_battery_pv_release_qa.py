from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable

import pytest

import qa_battery_pv_release as qa


HERE = Path(__file__).resolve().parents[1]
SOURCE_RELEASE = HERE / "generated" / "battery_pv_release"
SOURCE_POOL = HERE / "generated" / "battery_pv_process_pool.json"
SOURCE_GATE = HERE / "generated" / "battery_pv_process_replay_gate.json"


@pytest.fixture
def release_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    release = tmp_path / "battery_pv_release"
    release.mkdir()
    for name in ("episodes_public.jsonl", "episodes_private.jsonl", "build_report.json"):
        shutil.copy2(SOURCE_RELEASE / name, release / name)
    pool = tmp_path / "battery_pv_process_pool.json"
    gate = tmp_path / "battery_pv_process_replay_gate.json"
    shutil.copy2(SOURCE_POOL, pool)
    shutil.copy2(SOURCE_GATE, gate)
    return release, pool, gate


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in records))


def test_release_passes_all_checks(release_fixture: tuple[Path, Path, Path]) -> None:
    release, pool, gate = release_fixture
    summary = qa.run_checks(release, pool, gate)
    assert summary["ok"] is True
    assert summary["failure_count"] == 0
    assert all(summary["checks"].values())
    assert summary["episode_count"] == 48
    assert summary["process_count"] == 48


Mutation = Callable[[Path, Path, Path], None]


def mutate_public_field(name: str, value: Any) -> Mutation:
    def mutate(release: Path, _pool: Path, _gate: Path) -> None:
        records = read_jsonl(release / "episodes_public.jsonl")
        records[0][name] = value
        write_jsonl(release / "episodes_public.jsonl", records)

    return mutate


@pytest.mark.parametrize(
    ("mutation", "failed_check"),
    [
        (mutate_public_field("horizon_steps", 23), "horizon_steps"),
        (mutate_public_field("allowed_actions", {"battery_rate": {"type": "discrete", "minimum": -1.0, "maximum": 1.0}}), "action_bounds"),
        (mutate_public_field("initial_observation", {"battery_soc": 0.25}), "public_observation_fields"),
    ],
)
def test_public_schema_and_action_guards(
    release_fixture: tuple[Path, Path, Path],
    mutation: Mutation,
    failed_check: str,
) -> None:
    release, pool, gate = release_fixture
    mutation(release, pool, gate)
    summary = qa.run_checks(release, pool, gate)
    assert summary["ok"] is False
    assert summary["checks"][failed_check] is False


def test_public_hidden_key_and_private_secret_are_rejected(
    release_fixture: tuple[Path, Path, Path],
) -> None:
    release, pool, gate = release_fixture
    public = read_jsonl(release / "episodes_public.jsonl")
    public[0]["contract_blob"] = "must remain private"
    write_jsonl(release / "episodes_public.jsonl", public)
    private = read_jsonl(release / "episodes_private.jsonl")
    private[0]["api_token"] = "not-a-real-token"
    write_jsonl(release / "episodes_private.jsonl", private)

    summary = qa.run_checks(release, pool, gate)
    assert summary["ok"] is False
    assert summary["checks"]["public_hidden_keys"] is False
    assert summary["checks"]["no_paths_or_secrets"] is False


def test_pairing_split_season_and_gate_guards(
    release_fixture: tuple[Path, Path, Path],
) -> None:
    release, pool, gate = release_fixture
    private = read_jsonl(release / "episodes_private.jsonl")
    private[1]["episode_id"] = "not-the-public-id"
    private[1]["split"] = "test"
    private[1]["selection_lineage"]["season_index"] = private[0]["selection_lineage"]["season_index"]
    write_jsonl(release / "episodes_private.jsonl", private)
    replay_gate = json.loads(gate.read_text())
    replay_gate["processes"][0]["passed"] = False
    replay_gate["all_passed"] = False
    replay_gate["failed_count"] = 1
    gate.write_text(json.dumps(replay_gate))

    summary = qa.run_checks(release, pool, gate)
    assert summary["ok"] is False
    assert summary["checks"]["episode_id_pairing"] is False
    assert summary["checks"]["one_process_per_building_season"] is False
    assert summary["checks"]["replay_gate_passed"] is False


def test_malformed_input_returns_json_failure_summary(
    release_fixture: tuple[Path, Path, Path],
) -> None:
    release, pool, gate = release_fixture
    (release / "episodes_public.jsonl").write_text("not-json\n")
    summary = qa.run_checks(release, pool, gate)
    assert summary["ok"] is False
    assert summary["failures"][0]["check"] == "input_files"


def test_cli_writes_atomic_json_report_and_nonzero_on_failure(
    release_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    release, pool, gate = release_fixture
    output = tmp_path / "qa-report.json"
    command = [
        sys.executable,
        str(HERE / "qa_battery_pv_release.py"),
        "--release-dir",
        str(release),
        "--process-pool",
        str(pool),
        "--replay-gate",
        str(gate),
        "--output",
        str(output),
    ]
    passed = subprocess.run(command, capture_output=True, text=True)
    assert passed.returncode == 0
    assert json.loads(output.read_text())["ok"] is True

    report = json.loads((release / "build_report.json").read_text())
    report["episode_count"] = 0
    (release / "build_report.json").write_text(json.dumps(report))
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode != 0
    written = json.loads(output.read_text())
    assert written["ok"] is False
    assert written["checks"]["report_counts"] is False
