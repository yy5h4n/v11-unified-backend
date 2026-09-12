"""Outcome-specific counterfactual checks; never infer coupling from total state."""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any


def at(document: Any, path: list) -> Any:
    for key in path:
        document = document[key]
    return document


def compare_fixed_peer(low: dict, high: dict, *, fixed_path: list,
                       varied_path: list, delivery_path: list,
                       margin_path: list | None = None, tolerance: float = 1e-6) -> dict:
    """Paths address action payloads or complete receipts, respectively."""
    same_prefix = low["prefix_observation"] == high["prefix_observation"]
    same_time = low["receipt"]["time_seconds"] == high["receipt"]["time_seconds"]
    fixed_equal = at(low["action"], fixed_path) == at(high["action"], fixed_path)
    varied_changed = at(low["action"], varied_path) != at(high["action"], varied_path)
    normalized = deepcopy(low["action"])
    parent = normalized
    for key in varied_path[:-1]:
        parent = parent[key]
    parent[varied_path[-1]] = at(high["action"], varied_path)
    only_one_changed = normalized == high["action"]
    def delta(path):
        a, b = at(low["receipt"], path), at(high["receipt"], path)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, b)):
            raise ValueError("mechanism outcome must be finite numeric native/public evidence")
        return {"low": a, "high": b, "high_minus_low": b - a}
    delivery = delta(delivery_path)
    margin = delta(margin_path) if margin_path else None
    controlled = same_prefix and same_time and fixed_equal and varied_changed and only_one_changed
    return {
        "controlled_comparison": controlled,
        "same_prefix": same_prefix, "same_time": same_time,
        "fixed_request_unchanged": fixed_equal, "other_request_changed": varied_changed,
        "only_intervention_changed": only_one_changed,
        "fixed_delivery": delivery, "shared_margin": margin,
        "fixed_delivery_changed": controlled and abs(delivery["high_minus_low"]) > tolerance,
        "shared_margin_changed": controlled and margin is not None and abs(margin["high_minus_low"]) > tolerance,
    }
