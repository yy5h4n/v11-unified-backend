import json
from pathlib import Path

from build_harness_v2_full_episode_batch import build


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_fresh_full_batch_passes_dataset_side_diagnostics(tmp_path):
    report = build(tmp_path)
    assert report["status"] == "PASS"
    assert report["candidate_count"] == report["episode_count"] == 11
    assert report["responsibility_episode_counts"] == {
        "rd_split_016151c7c038": 5,
        "rd_split_b8457e559b4d": 6,
    }
    gate = json.loads((tmp_path / "validation_gate.json").read_text())
    assert gate["all_passed"] is True
    assert all(all(row["gates"].values()) for row in gate["episodes"])


def test_public_batch_exposes_preferences_but_no_private_evaluator_or_gold(tmp_path):
    build(tmp_path)
    rows = load_jsonl(tmp_path / "episodes_public.jsonl")
    assert len(rows) == 11
    for row in rows:
        assert row["gold_actions_released"] is False
        assert row["agent_view"]["user_preferences"]["thermal_target_c"] == 22.0
        assert "query_id" not in row["agent_view"]
        assert "responsibility_id" not in row["agent_view"]
        assert "evaluator" not in row["agent_view"]
        assert row["agent_view"]["allowed_action_kinds"] == ["act", "wait"]
        assert row["agent_view"]["capability_manifest"]["backend_id"] == "simuhome_room_thermal"
        serialized = json.dumps(row).casefold()
        assert "oracle" not in serialized
        assert "evaluator" not in serialized
        assert "gold_actions\":" not in serialized


def test_every_private_run_has_a_matching_deterministic_replay(tmp_path):
    build(tmp_path)
    for row in load_jsonl(tmp_path / "episodes_private.jsonl"):
        assert {
            name: run["trace_digest"] for name, run in row["runs"].items()
        } == row["replicate_trace_digests"]
