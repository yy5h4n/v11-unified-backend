from __future__ import annotations

import json

from compile_supported_hvac_episodes import RELEASE, build, validate


def test_supported_hvac_release_is_complete_unique_and_private() -> None:
    outputs = build()
    validate(outputs)
    public = [json.loads(x) for x in outputs[RELEASE / "episodes_public.jsonl"].splitlines()]
    private = [json.loads(x) for x in outputs[RELEASE / "episodes_private.jsonl"].splitlines()]
    assert len(public) == len(private) == 48
    assert len({x["process_id"] for x in private}) == 48
    assert {x["responsibility_id"] for x in private} == {"rd_37104b57370a", "rd_94d666a58c83"}
    assert all("backend_binding" not in x and "process_id" not in x for x in public)
    assert all(x["episode_release_status"] if "episode_release_status" in x else True for x in private)


def test_supported_hvac_outputs_are_current_and_deterministic() -> None:
    assert build() == build()
    for path, content in build().items():
        assert path.read_text(encoding="utf-8") == content


def test_replay_gate_excludes_only_infeasible_candidates() -> None:
    gate = json.loads((RELEASE.parent / "supported_hvac_replay_gate_v1.json").read_text())
    assert gate["candidate_process_count"] == 50
    assert gate["passed_count"] == 48
    assert gate["excluded_count"] == 2
    assert gate["all_replays_deterministic"]
    assert gate["all_processes_action_sensitive"]
    excluded = [row for row in gate["processes"] if not row["passed"]]
    assert all(row["exclusion_reasons"] == ["FIXED_WITNESS_CONTRACT_INFEASIBLE"] for row in excluded)
