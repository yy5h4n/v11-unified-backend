import json

import pytest

from tools import summarize_backend_trust as summary


@pytest.mark.parametrize("damage", [None, "clock", "terminal", "checks", "missing"])
def test_summary_rechecks_trace_instead_of_trusting_pass_label(tmp_path, monkeypatch, damage):
    monkeypatch.setattr(summary, "ROOT", tmp_path)
    monkeypatch.setattr(summary, "PUBLIC_ROUTE_IDS", ("test_route",))
    monkeypatch.setattr(summary, "route_metadata", lambda _: {"cadence_seconds": 60, "horizon_seconds": 120})
    directory = tmp_path / "generated/backend_trust_horizon_v1"
    directory.mkdir(parents=True)
    report = {
        "status": "passed",
        "checks": {key: True for key in ("full_horizon", "terminal_observation", "terminal_step_rejected", "cadence")},
        "trace": [{"time_seconds": 60, "done": False}, {"time_seconds": 120, "done": True}],
    }
    if damage == "clock":
        report["trace"][0]["time_seconds"] = 61
    elif damage == "terminal":
        report["trace"][0]["done"] = True
    elif damage == "checks":
        report["checks"]["terminal_observation"] = False
    if damage != "missing":
        (directory / "test_route.json").write_text(json.dumps(report))
    result = summary.build()
    assert result["passed"] == (1 if damage is None else 0)
    assert result["formal_route_count"] == 1
    assert result["mechanism_or_benchmark_ready"] is False
