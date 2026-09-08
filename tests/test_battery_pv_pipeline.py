from __future__ import annotations

import importlib
import numpy as np
import pytest
import sys
from types import ModuleType


def _pure_miner_helpers():
    """Load the miner's pure helpers without importing its CityLearn probe."""
    probe_name = "probe_citylearn_battery_replay"
    fake_probe = ModuleType(probe_name)
    fake_probe.ASSET_MANIFEST = None
    fake_probe.ROOT = None
    fake_probe.SOURCE_CACHE = None
    fake_probe.building_battery_runtime = None
    fake_probe.canonical_digest = None
    fake_probe.require_assets = None
    fake_probe.shared_sizing_catalogs = None
    original_probe = sys.modules.get(probe_name)
    sys.modules[probe_name] = fake_probe
    try:
        module = importlib.import_module("mine_citylearn_battery_pv")
    finally:
        if original_probe is None:
            sys.modules.pop(probe_name, None)
        else:
            sys.modules[probe_name] = original_probe
    return module


@pytest.fixture(autouse=True)
def _drop_battery_runtime_modules():
    yield
    for name in tuple(sys.modules):
        if name == "citylearn" or name.startswith("citylearn."):
            sys.modules.pop(name, None)


def test_window_metrics_requires_surplus_before_later_deficit() -> None:
    window_metrics = _pure_miner_helpers().window_metrics

    load = np.ones(24)
    solar = np.zeros(24)
    solar[20:] = 3.0
    metrics = window_metrics(load, solar, capacity=10.0)
    assert metrics["pv_surplus_kwh"] > 0.0
    assert metrics["later_grid_deficit_kwh"] == 0.0
    assert metrics["qualifies"] is False


def test_window_metrics_accepts_meaningful_shift_opportunity() -> None:
    window_metrics = _pure_miner_helpers().window_metrics

    load = np.ones(24)
    solar = np.zeros(24)
    solar[8:15] = 3.0
    metrics = window_metrics(load, solar, capacity=10.0)
    assert metrics["transferable_kwh"] >= metrics["minimum_shift_kwh"]
    assert metrics["qualifies"] is True


def test_diversity_selection_keeps_best_day_per_season() -> None:
    select_diverse_days = _pure_miner_helpers().select_diverse_days

    candidates = [
        {
            "season_index": season,
            "day_index": season * 91 + offset,
            "metrics": {
                "selection_score": score,
                "transferable_kwh": score * 10.0,
            },
        }
        for season in range(4)
        for offset, score in ((0, 0.1), (1, 0.2))
    ]
    selected = select_diverse_days(candidates)
    assert len(selected) == 4
    assert all(item["day_index"] % 91 == 1 for item in selected)


def test_battery_replay_isolated_from_invalid_hvac_window() -> None:
    from probe_citylearn_battery_replay import (
        DEFAULT_BUILDING,
        building_battery_runtime,
        run_policy,
        shared_sizing_catalogs,
    )

    building_battery_runtime.cache_clear()
    with shared_sizing_catalogs():
        idle = run_policy(DEFAULT_BUILDING, 7800, 24, 0.0)
        charge = run_policy(DEFAULT_BUILDING, 7800, 24, 1.0)
    assert idle["terminated"] and charge["terminated"]
    assert idle["metadata"] == charge["metadata"]
    assert idle["metadata"]["action_names"] == ["electrical_storage"]
    assert max(row["soc"] for row in charge["records"]) > max(
        row["soc"] for row in idle["records"]
    )
    assert any(
        a["net_electricity"] != b["net_electricity"]
        for a, b in zip(idle["records"], charge["records"])
    )
