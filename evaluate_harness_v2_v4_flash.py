#!/usr/bin/env python3
"""Leakage-controlled DeepSeek V4 Flash evaluation for Harness V2 Episodes."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from harness_v2.core import EpisodeSpec, Harness, RunArtifact
from harness_v2.capability_protocol import build_action_protocol, validate_action_against_manifest
from harness_v2.evaluator_v3 import evaluate_thermal_v3
from harness_v2.simuhome_adapter import SimuHomeHarnessAdapter
from build_harness_v2_full_episode_batch import evaluator_trace


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[4]
BATCH = ROOT / "generated" / "harness_v2_full_episode_batch_v2"
SIMUHOME_DATA = WORKSPACE / "external" / "SimuHome" / "data" / "benchmark"
DEFAULT_BASE = "https://aigc.sankuai.com/v1/openai/native"
DEFAULT_MODEL = "deepseek-v4-flash-meituan"
LOWER_C = 20.0
UPPER_C = 24.0
TARGET_MIN_C = 7.0
TARGET_MAX_C = 32.0


class APIError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ChatClient:
    def __init__(self, base_url: str = DEFAULT_BASE, model: str = DEFAULT_MODEL, api_key: str | None = None, retries: int = 2, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.environ.get("AIGC_API_KEY")
        self.retries = retries
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("AIGC_API_KEY is required")

    def complete(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 512,
        }).encode("utf-8")
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                request = Request(
                    self.base_url + "/chat/completions",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key},
                )
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return {
                    "content": payload.get("choices", [{}])[0].get("message", {}).get("content", ""),
                    "usage": payload.get("usage", {}) or {},
                    "latency_ms": (time.perf_counter() - started) * 1000,
                }
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.retries:
                    raise APIError(f"http_{exc.code}") from exc
            except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                if attempt >= self.retries:
                    raise APIError(type(exc).__name__.lower()) from exc
            time.sleep(min(8.0, 0.5 * (2**attempt)))
        raise APIError("retry_exhausted")


def public_action_protocol(public_view: dict[str, Any]) -> dict[str, Any]:
    return build_action_protocol(
        public_view["capability_manifest"],
        public_view["observation"],
        public_view["track"],
        public_view.get("interaction_budget"),
    )


def build_messages(public_view: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "query": deepcopy(public_view["query"]),
        "user_preferences": deepcopy(public_view["user_preferences"]),
        "decision_interval_minutes": public_view["decision_interval_minutes"],
        "horizon_steps": public_view["horizon_steps"],
        "current_observation": deepcopy(public_view["observation"]),
        "previous_action_summary": deepcopy(public_view["last_feedback"]),
        "action_protocol": public_action_protocol(public_view),
    }
    return [
        {"role": "system", "content": "Control the home to satisfy the user's request. Return exactly one JSON object matching one branch of action_protocol.json_schema. Include every required field exactly as named. Do not add fields, markdown, or explanation."},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]


def _parse_strict_json(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        raise ValueError("response_not_text")
    text = content.strip()
    if not text.startswith("{") or not text.endswith("}"):
        raise ValueError("response_not_plain_json_object")
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"nonfinite_json_constant:{value}")

    value = json.loads(text, object_pairs_hook=reject_duplicates, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("response_not_json_object")
    return value


def validate_public_action(action: dict[str, Any], public_view: dict[str, Any]) -> None:
    validate_action_against_manifest(
        action,
        public_view["capability_manifest"],
        public_view["observation"],
        public_view["track"],
        public_view.get("interaction_budget"),
        current_step=int(public_view["observation"]["step"]),
        horizon=int(public_view["horizon_steps"]),
    )


class LLMPolicy:
    def __init__(self, client: Any):
        self.client = client
        self.calls = 0
        self.api_successes = 0
        self.valid_actions = 0
        self.tokens = 0
        self.latency_ms = 0.0
        self.errors: Counter[str] = Counter()
        self.message_log: list[list[dict[str, str]]] = []
        self.model_output_records: list[dict[str, Any]] = []

    def decide(self, public_view: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        messages = build_messages(public_view)
        self.message_log.append(deepcopy(messages))
        output_record: dict[str, Any] = {
            "call_index": self.calls - 1,
            "stage": "request_started",
            "raw_content": None,
            "raw_content_sha256": None,
            "error": None,
        }
        try:
            response = self.client.complete(messages)
            self.api_successes += 1
            self.tokens += int((response.get("usage") or {}).get("total_tokens", 0))
            self.latency_ms += float(response.get("latency_ms", 0.0))
            raw_content = response.get("content")
            output_record["stage"] = "response_received"
            if isinstance(raw_content, str):
                output_record["raw_content"] = raw_content
                output_record["raw_content_sha256"] = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
            action = _parse_strict_json(raw_content)
            output_record["stage"] = "json_parsed"
            validate_public_action(action, public_view)
            output_record["stage"] = "action_valid"
            self.valid_actions += 1
            return action
        except Exception as exc:
            error = type(exc).__name__ + ":" + str(exc)
            output_record["error"] = error
            self.errors[error] += 1
            return {"kind": "invalid_model_output"}
        finally:
            self.model_output_records.append(output_record)


def preflight_batch(batch: Path = BATCH) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    report = json.loads((batch / "build_report.json").read_text(encoding="utf-8"))
    gate = json.loads((batch / "validation_gate.json").read_text(encoding="utf-8"))
    if report.get("status") != "PASS" or report.get("validation_failures") != [] or not gate.get("all_passed"):
        raise RuntimeError("batch is not admitted")
    public_path, private_path = batch / "episodes_public.jsonl", batch / "episodes_private.jsonl"
    if _sha256_file(public_path) != report["public_sha256"] or _sha256_file(private_path) != report["private_sha256"]:
        raise RuntimeError("batch hash mismatch")
    implementation_paths = {
        "builder_sha256": ROOT / "build_harness_v2_full_episode_batch.py",
        "harness_core_sha256": ROOT / "harness_v2" / "core.py",
        "validation_suite_sha256": ROOT / "harness_v2" / "episode_validator.py",
        "simuhome_harness_adapter_sha256": ROOT / "harness_v2" / "simuhome_adapter.py",
        "capability_protocol_sha256": ROOT / "harness_v2" / "capability_protocol.py",
        "evaluator_v3_sha256": ROOT / "harness_v2" / "evaluator_v3.py",
        "thermal_adapter_sha256": ROOT / "unified_compiler" / "simuhome_multiroom_thermal_adapter.py",
    }
    for name, path in implementation_paths.items():
        if _sha256_file(path) != report["implementation_hashes"][name]:
            raise RuntimeError(f"implementation hash mismatch: {name}")
    public_rows = _load_jsonl(public_path)
    private_rows = {row["episode_id"]: row for row in _load_jsonl(private_path)}
    if len(public_rows) != len(private_rows) or {row["episode_id"] for row in public_rows} != set(private_rows):
        raise RuntimeError("public/private pairing mismatch")
    gate_rows = gate.get("episodes")
    if not isinstance(gate_rows, list):
        raise RuntimeError("validation gate episodes must be a list")
    gate_episode_ids = [row.get("episode_id") for row in gate_rows]
    gate_ids = set(gate_episode_ids)
    public_ids = {row["episode_id"] for row in public_rows}
    responsibility_counts = {
        responsibility_id: sum(row["responsibility_id"] == responsibility_id for row in public_rows)
        for responsibility_id in sorted({row["responsibility_id"] for row in public_rows})
    }
    if (
        report.get("candidate_count") != len(public_rows)
        or report.get("episode_count") != len(public_rows)
        or report.get("responsibility_count") != len(responsibility_counts)
        or report.get("responsibility_episode_counts") != responsibility_counts
        or len(gate_rows) != len(public_rows)
        or gate.get("candidate_count") != len(public_rows)
        or gate.get("passed_count") != len(public_rows)
        or gate.get("failed_count") != 0
        or not all(row.get("passed") is True for row in gate_rows)
        or len(gate_episode_ids) != len(gate_ids)
        or gate_ids != public_ids
    ):
        raise RuntimeError("batch manifest/gate mismatch")
    for row in private_rows.values():
        source = SIMUHOME_DATA / row["source"]["source_file"]
        if _sha256_file(source) != row["source"]["source_file_sha256"]:
            raise RuntimeError(f"source hash mismatch: {source.name}")
    return public_rows, private_rows, report


def _load_config(private: dict[str, Any]) -> dict[str, Any]:
    source = SIMUHOME_DATA / private["source"]["source_file"]
    config = json.loads(source.read_text(encoding="utf-8"))["initial_home_config"]
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != private["source"]["source_config_sha256"]:
        raise RuntimeError("source config hash mismatch")
    return config


def _observations(run: RunArtifact) -> list[dict[str, Any]]:
    return [item["value"] for item in run.public_trace if item["type"] == "observation"]


def _stored_run(value: dict[str, Any]) -> RunArtifact:
    return RunArtifact(
        value["episode_id"],
        value["status"],
        tuple(value["public_trace"]),
        tuple(value["private_trace"]),
        value["trace_digest"],
    )


def score_run(run: RunArtifact, private: dict[str, Any], sample_minutes: int = 15) -> dict[str, Any]:
    if run.status != "completed":
        return {
            "score_valid": False,
            "failure_reason": "run_not_completed",
            "band_satisfaction": None,
            "band_maintenance_pass": False,
            "delta_satisfaction": None,
            "formal_nrg": {"availability": "unavailable", "value": None, "reason": "run_not_completed"},
            "worst_room_soft_mae_c": None,
        }
    comparison = evaluate_thermal_v3(
        private["evaluator_manifest"],
        evaluator_trace(run, sample_minutes, private["target_room_ids"]),
        evaluator_trace(_stored_run(private["runs"]["noop"]), sample_minutes, private["target_room_ids"]),
        evaluator_trace(_stored_run(private["runs"]["oracle"]), sample_minutes, private["target_room_ids"]),
    )
    formal = comparison.formal_nrg
    soft = comparison.agent.worst_room_soft_mae_c
    applied_statuses = Counter(
        command.get("status")
        for item in run.private_trace
        if item["type"] == "action_result" and item["accepted"]
        for command in item["feedback"].get("applied_commands", [])
    )
    energy = comparison.agent.energy
    return {
        "score_valid": True,
        "failure_reason": None,
        "band_satisfaction": float(comparison.agent.band_satisfaction),
        "band_maintenance_pass": comparison.agent.band_maintenance_pass,
        "delta_satisfaction": float(comparison.delta_satisfaction),
        "formal_nrg": {
            "availability": formal.availability,
            "value": None if formal.value is None else float(formal.value),
            "reason": formal.reason,
        },
        "worst_room_soft_mae_c": None if soft.value is None else float(soft.value),
        "simulator_energy_proxy_wh": {
            "availability": energy.availability,
            "value": None if energy.value is None else float(energy.value),
            "reason": energy.reason,
            "semantics": "simulator_duty_gated_rated_power_proxy_wh",
        },
        "command_effects": {
            "committed_state_changes": applied_statuses.get("committed", 0),
            "coalesced_redundant_writes": applied_statuses.get("coalesced", 0),
        },
        "unscorable_dimensions": [
            name
            for name in ("lifecycle", "energy", "formal_safety")
            if getattr(comparison.agent, name).availability == "unavailable"
        ],
        "realized_collateral": {
            "availability": comparison.agent.realized_collateral.availability,
            "value": None if comparison.agent.realized_collateral.value is None else {
                **comparison.agent.realized_collateral.value,
                "fraction": float(comparison.agent.realized_collateral.value["fraction"]),
            },
            "reason": comparison.agent.realized_collateral.reason,
        },
    }


def evaluate_one(public: dict[str, Any], private: dict[str, Any], client: Any) -> dict[str, Any]:
    config = _load_config(private)
    bootstrap = deepcopy(public["agent_view"])
    episode = EpisodeSpec(public["episode_id"], bootstrap, seed=0, max_decisions=int(bootstrap["horizon_steps"]) + 1)
    probe = SimuHomeHarnessAdapter(config, public["visible_room_ids"], sample_minutes=int(bootstrap["decision_interval_minutes"]))
    reset = probe.reset(episode)
    if reset.public_observation != public["initial_observation"]:
        raise RuntimeError("reset observation mismatch")
    policy = LLMPolicy(client)
    backend = SimuHomeHarnessAdapter(config, public["visible_room_ids"], sample_minutes=int(bootstrap["decision_interval_minutes"]))
    run = Harness(backend).run_one(episode, policy)
    score = score_run(run, private, int(bootstrap["decision_interval_minutes"]))
    return {
        "episode_id": public["episode_id"],
        "status": run.status,
        "trace_digest": run.trace_digest,
        "calls": policy.calls,
        "api_successes": policy.api_successes,
        "valid_actions": policy.valid_actions,
        "tokens": policy.tokens,
        "latency_ms": policy.latency_ms,
        "errors": dict(policy.errors),
        "observation_count": len(_observations(run)),
        "public_action_records": [deepcopy(item) for item in run.public_trace if item["type"] in {"action", "protocol_error"}],
        "model_output_records": deepcopy(policy.model_output_records),
        "score": score,
    }


def smoke_accepted(row: dict[str, Any], horizon: int) -> bool:
    return bool(
        row["status"] == "completed"
        and row["calls"] == horizon
        and row["api_successes"] == horizon
        and row["valid_actions"] == horizon
        and row["observation_count"] == horizon + 1
        and row["score"]["score_valid"]
    )


def main(args: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--output", default=str(ROOT / "runs" / "harness_v2_v4_flash_smoke_v2_20260901" / "report.json"))
    ns = parser.parse_args(args)
    public_rows, private_rows, report = preflight_batch()
    client = ChatClient(ns.base_url, ns.model)
    results = [evaluate_one(row, private_rows[row["episode_id"]], client) for row in public_rows[: ns.limit]]
    accepted = [smoke_accepted(row, int(public_rows[index]["agent_view"]["horizon_steps"])) for index, row in enumerate(results)]
    output = {
        "model": ns.model,
        "batch_public_sha256": report["public_sha256"],
        "episode_count": len(results),
        "episodes": results,
        "execution_finished": True,
        "smoke_accepted": bool(results) and all(accepted),
    }
    path = Path(ns.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    main()
