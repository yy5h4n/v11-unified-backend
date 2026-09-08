from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
import time

import pytest

import evaluate_workflow_v4_flash_v8 as v8
import evaluate_workflow_v4_flash_v9 as v9


def fake_result(index: int) -> dict:
    position = v9.SAMPLE_INDICES.index(index)
    return {"episode_id": v9.SAMPLE_EPISODE_IDS[position], "index": index}


def lightweight_report(results, *, execution_finished, requested_workers):
    return {
        "schema_version": v9.SCHEMA_VERSION,
        "execution_finished": execution_finished,
        "workers": requested_workers,
        "episodes": deepcopy(results),
    }


def patch_main(monkeypatch, *, evaluator, initial=None):
    monkeypatch.setattr(v9.v5, "assert_release_and_sample_frozen", lambda: ([], []))
    monkeypatch.setattr(v9, "load_checkpoint", lambda path, requested_workers: deepcopy(initial or []))
    monkeypatch.setattr(v9, "evaluate_index", evaluator)
    monkeypatch.setattr(v9, "build_report", lightweight_report)


def test_workers_must_be_positive():
    assert v9.positive_int("30") == 30
    with pytest.raises(Exception):
        v9.positive_int("0")


def test_workers_30_for_fixed_15_has_effective_15():
    assert len(v9.SAMPLE_INDICES) == 15
    assert v9.effective_worker_count(30, 15) == 15
    assert v9.effective_worker_count(30, 0) == 0


def test_actual_episode_parallelism(monkeypatch, tmp_path: Path):
    active = 0
    peak = 0
    lock = threading.Lock()
    barrier = threading.Barrier(len(v9.SAMPLE_INDICES))

    def evaluator(index, base_url, model):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=3)
        with lock:
            active -= 1
        return fake_result(index)

    patch_main(monkeypatch, evaluator=evaluator)
    monkeypatch.setattr(v9, "atomic_write_report", lambda path, report: None)
    v9.main(["--workers", "30", "--output", str(tmp_path / "r.json")])
    assert peak == 15


def test_each_episode_constructs_a_distinct_client_and_calls_v8_once(monkeypatch):
    clients = []
    calls = []

    class Client:
        def __init__(self, base_url, model):
            self.serial = len(clients)
            clients.append(self)

    monkeypatch.setattr(v9, "ChatClient", Client)
    monkeypatch.setattr(v9.v8, "evaluate_one", lambda client, index, release: calls.append((client, index)) or {})
    v9.evaluate_index(v9.SAMPLE_INDICES[0], v9.DEFAULT_BASE, v9.DEFAULT_MODEL)
    v9.evaluate_index(v9.SAMPLE_INDICES[1], v9.DEFAULT_BASE, v9.DEFAULT_MODEL)
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert [client for client, _ in calls] == clients


def test_calls_within_one_episode_are_not_parallelized(monkeypatch):
    active = 0
    peak = 0

    class Client:
        def __init__(self, base_url, model):
            pass

        def complete(self):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.001)
            active -= 1

    def sequential_episode(client, index, release):
        for _ in range(3):
            client.complete()
        return fake_result(index)

    monkeypatch.setattr(v9, "ChatClient", Client)
    monkeypatch.setattr(v9.v8, "evaluate_one", sequential_episode)
    v9.evaluate_index(v9.SAMPLE_INDICES[0], v9.DEFAULT_BASE, v9.DEFAULT_MODEL)
    assert peak == 1


def test_out_of_order_futures_produce_frozen_final_order(monkeypatch, tmp_path: Path):
    def evaluator(index, base_url, model):
        position = v9.SAMPLE_INDICES.index(index)
        time.sleep((len(v9.SAMPLE_INDICES) - position) * 0.001)
        return fake_result(index)

    writes = []
    patch_main(monkeypatch, evaluator=evaluator)
    monkeypatch.setattr(v9, "atomic_write_report", lambda path, report: writes.append(deepcopy(report)))
    report = v9.main(["--workers", "15", "--output", str(tmp_path / "r.json")])
    assert [row["episode_id"] for row in report["episodes"]] == list(v9.SAMPLE_EPISODE_IDS)
    assert writes[-1]["execution_finished"] is True


def test_arbitrary_subset_checkpoint_resumes_only_missing(monkeypatch, tmp_path: Path):
    positions = [1, 5, 13]
    initial = [fake_result(v9.SAMPLE_INDICES[position]) for position in positions]
    executed = []

    def evaluator(index, base_url, model):
        executed.append(index)
        return fake_result(index)

    patch_main(monkeypatch, evaluator=evaluator, initial=initial)
    monkeypatch.setattr(v9, "atomic_write_report", lambda path, report: None)
    report = v9.main(["--output", str(tmp_path / "r.json")])
    assert set(executed) == set(v9.SAMPLE_INDICES) - {v9.SAMPLE_INDICES[p] for p in positions}
    assert [row["episode_id"] for row in report["episodes"]] == list(v9.SAMPLE_EPISODE_IDS)


def test_partial_api_failure_keeps_other_completed_results_and_is_unfinished(monkeypatch, tmp_path: Path):
    failed_index = v9.SAMPLE_INDICES[3]
    writes = []

    def evaluator(index, base_url, model):
        if index == failed_index:
            raise v9.APIError("connection_reset")
        return fake_result(index)

    patch_main(monkeypatch, evaluator=evaluator)
    monkeypatch.setattr(v9, "atomic_write_report", lambda path, report: writes.append(deepcopy(report)))
    with pytest.raises(v9.APIError, match="connection_reset"):
        v9.main(["--workers", "15", "--output", str(tmp_path / "r.json")])
    final_checkpoint = writes[-1]
    assert final_checkpoint["execution_finished"] is False
    assert len(final_checkpoint["episodes"]) == 14
    assert v9.SAMPLE_EPISODE_IDS[3] not in {row["episode_id"] for row in final_checkpoint["episodes"]}


def test_subset_validator_rejects_duplicate_unknown_and_noncanonical_order(monkeypatch):
    monkeypatch.setattr(v9.v5, "assert_release_and_sample_frozen", lambda: ([], []))
    monkeypatch.setattr(v9, "_validate_episode_row", lambda row, position, public_rows: None)
    first = {"episode_id": v9.SAMPLE_EPISODE_IDS[0]}
    second = {"episode_id": v9.SAMPLE_EPISODE_IDS[1]}
    with pytest.raises(RuntimeError, match="duplicate"):
        v9.validate_result_subset([first, first])
    with pytest.raises(RuntimeError, match="unknown"):
        v9.validate_result_subset([{"episode_id": "unknown"}])
    with pytest.raises(RuntimeError, match="order"):
        v9.validate_result_subset([second, first])


def test_load_checkpoint_refuses_completed_report(tmp_path: Path):
    path = tmp_path / "done.json"
    path.write_text(json.dumps({"schema_version": v9.SCHEMA_VERSION, "execution_finished": True}))
    with pytest.raises(RuntimeError, match="already completed"):
        v9.load_checkpoint(path, requested_workers=30)


def test_load_checkpoint_rejects_metadata_tampering(monkeypatch, tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    payload = lightweight_report([], execution_finished=False, requested_workers=30)
    payload["workers"] = 29
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(v9, "build_report", lightweight_report)
    with pytest.raises(RuntimeError, match="metadata"):
        v9.load_checkpoint(path, requested_workers=30)


def test_v9_delegates_episode_semantics_to_v8():
    assert v9.v8.evaluate_one is v8.evaluate_one
    assert v9.SAMPLE_INDICES is v8.SAMPLE_INDICES
    assert "v9" in str(v9.DEFAULT_OUTPUT)


def test_v9_report_declares_api_output_tokens_as_primary(monkeypatch):
    monkeypatch.setattr(v9, "validate_result_subset", lambda results: None)
    monkeypatch.setattr(v9.v8, "metrics_with_device_total", lambda results: {
        "api_output_tokens": 7,
        "completion_tokens": 7,
        "output_tokens": 7,
        "primary_token_metric": "api_output_tokens",
        "token_diagnostics": {"prompt_tokens": 100, "provider_total_tokens": 107},
    })
    report = v9.build_report([], execution_finished=False, requested_workers=30)
    config = report["experiment_config"]
    assert config["primary_token_metric"] == "api_output_tokens"
    assert config["token_accounting"]["prompt_and_provider_total_location"] == "metrics_all.token_diagnostics"
    assert "output_token_metric" not in config
    assert "total_tokens" not in report["metrics_all"]
