from __future__ import annotations

import json
from pathlib import Path

from tools.build_open_corpus_pilot_v0 import build


ROOT = Path(__file__).resolve().parents[1]


def test_open_corpus_pilot_builds_and_executes(tmp_path: Path):
    summary = build(ROOT / "open_corpus_pilot_v0" / "families.json", tmp_path)
    assert summary["family_count"] == 7
    assert summary["episode_count"] == 17
    assert summary["failed_episode_count"] == 0
    assert summary["all_backend_matches_full"] is True
    assert summary["all_oracles_pass"] is True
    assert summary["all_noops_fail"] is True
    assert summary["all_replays_deterministic"] is True
    assert summary["formal_human_revalidation_claimed"] is False

    public = [json.loads(line) for line in (tmp_path / "episodes_public.jsonl").read_text().splitlines()]
    private = [json.loads(line) for line in (tmp_path / "episodes_private_validation.jsonl").read_text().splitlines()]
    assert len({row["episode_id"] for row in public}) == 17
    assert len(private) == 17
    assert all("fault" not in row for row in public)
    assert all(row["checks"]["passed"] for row in private)
