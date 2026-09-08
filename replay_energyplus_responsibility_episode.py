#!/usr/bin/env python3
"""Programmatic policy bridge and evaluator for released residential Episodes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from probe_energyplus_residential_runtime import run_observation_policy

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "generated/energyplus_responsibility_release_v1/episodes_private.jsonl"


def _episodes() -> dict[str, dict[str, Any]]:
    rows = [json.loads(line) for line in PRIVATE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["episode_id"]: row for row in rows}


def evaluate_episode(
    episode_id: str,
    policy: Callable[[dict[str, float], int], float],
    run_name: str = "candidate_policy",
) -> dict[str, Any]:
    """Run a legal observation-conditioned policy and score the frozen Contract."""
    episode = _episodes().get(episode_id)
    if episode is None:
        raise KeyError(f"unknown Episode: {episode_id}")
    if episode["responsibility_id"] != "rd_37104b57370a":
        raise RuntimeError("this v1 bridge only supports the frozen generic-comfort responsibility")
    day = int(episode["source_window"]["exact_key"]["day"])
    replay = run_observation_policy(day, policy, run_name)
    if len(replay["steps"]) != 96:
        raise RuntimeError("incomplete replay")
    contract = episode["contract"]
    target = float(contract["profile"]["target_c"])
    tolerance = float(contract["profile"]["tolerance_c"])
    errors = [abs(float(step["effect"]["zone_temperature_c"]) - target) for step in replay["steps"]]
    return {
        "episode_id": episode_id,
        "responsibility_id": episode["responsibility_id"],
        "trajectory_complete": True,
        "hard_contract_satisfied": all(error <= tolerance for error in errors),
        "hard_violation_fraction": sum(error > tolerance for error in errors) / len(errors),
        "mean_abs_error_c": sum(errors) / len(errors),
        "terminal_verdict": "continues_beyond_window",
        "trajectory_digest": replay["trace_digest"],
        "actions_acknowledged": len(replay["steps"]),
    }


__all__ = ["evaluate_episode"]
