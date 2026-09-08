"""Deterministic offline evaluator that recomputes Harness V2 loss from a sealed trace."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .semantic_validator import ConformanceError


@dataclass(frozen=True)
class EvaluationResult:
    loss: Decimal
    component_metrics: dict[str, Decimal]
    safety_pass: bool
    missing_ledger: dict[str, int]


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _datum(frame: dict[str, Any], pointer: str) -> dict[str, Any]:
    node: Any = frame
    for raw in pointer.strip("/").split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            raise ConformanceError(f"trace input does not resolve: {pointer}")
        node = node[key]
    if not isinstance(node, dict) or not {"value", "unit", "quality", "observed_at"} <= node.keys():
        raise ConformanceError(f"trace input is not a trace datum: {pointer}")
    return node


def _numeric(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ConformanceError("numeric loss operator received nonnumeric input")
    return Decimal(str(value))


def _pointwise(name: str, value: Decimal, parameters: dict[str, str]) -> Decimal:
    if name == "absolute_error":
        return abs(value - Decimal(parameters["target"]))
    if name == "squared_error":
        delta = value - Decimal(parameters["target"])
        return delta * delta
    if name == "upper_hinge":
        return max(Decimal(0), value - Decimal(parameters["upper"]))
    if name == "lower_hinge":
        return max(Decimal(0), Decimal(parameters["lower"]) - value)
    if name == "interval_violation":
        return max(Decimal(0), Decimal(parameters["lower"]) - value, value - Decimal(parameters["upper"]))
    if name == "indicator":
        return Decimal(0) if value == Decimal(parameters["expected"]) else Decimal(1)
    if name == "identity":
        return value
    raise ConformanceError(f"unsupported pointwise operator: {name}")


def _active(frame: dict[str, Any], selector: dict[str, Any]) -> bool:
    mode = selector["mode"]
    if mode == "all_intervals":
        return True
    if mode == "private_boolean_mask":
        return bool(_datum(frame, selector["binding"])["value"])
    if mode == "terminal":
        return True
    if mode == "event_selector":
        category, _, event_type = selector["binding"].partition(":")
        if category not in {"rule_events", "protocol_events", "safety_events"} or not event_type:
            raise ConformanceError("invalid event selector binding")
        return any(event.get("type") == event_type for event in frame[category])
    raise ConformanceError(f"unknown active selector: {mode}")


def _component(component: dict[str, Any], frames: list[dict[str, Any]]) -> tuple[Decimal, int]:
    values: list[tuple[Decimal, Decimal]] = []
    missing = 0
    for index, frame in enumerate(frames):
        if not _active(frame, component["active_selector"]):
            continue
        if component["integration"] == "terminal_value" and index != len(frames) - 1:
            continue
        binding = component["inputs"][0]
        datum = _datum(frame, binding["json_pointer"])
        quality = datum["quality"]
        age = (_instant(frame["timestamp"]) - _instant(datum["observed_at"])).total_seconds()
        invalid = quality not in binding["allowed_qualities"] or quality == "missing" or age > component["missing_rule"]["max_staleness_seconds"]
        if invalid:
            missing += 1
            rule = component["missing_rule"]["missing_input"]
            if rule == "fail_episode":
                raise ConformanceError(f"missing evaluator input: {component['component_id']}")
            if rule == "exclude_interval_and_report":
                continue
            value = _numeric(component["missing_rule"]["imputation_value"])
        else:
            if datum["unit"] != binding["unit"]:
                raise ConformanceError(f"unit mismatch for {binding['json_pointer']}")
            value = _numeric(datum["value"])
        operator = component["loss_operator"]
        if operator["name"] == "event_cost":
            point = Decimal(operator["parameters"]["cost"])
        else:
            point = _pointwise(operator["name"], value, operator["parameters"])
        duration = Decimal(str(frame["duration_to_next_seconds"]))
        values.append((point, duration))
    if not values:
        raise ConformanceError(f"no evaluable values for {component['component_id']}")
    aggregation = component["aggregation"]
    if aggregation in {"mean_over_active_mask", "weighted_sum_over_active_mask"}:
        weighted = sum(value * duration for value, duration in values)
        raw = weighted / sum(duration for _, duration in values) if aggregation == "mean_over_active_mask" else weighted
    elif aggregation == "max_over_active_mask":
        raw = max(value for value, _ in values)
    elif aggregation == "sum_over_events":
        raw = sum(value for value, _ in values)
    elif aggregation == "terminal":
        raw = values[-1][0]
    else:
        raise ConformanceError(f"unknown aggregation: {aggregation}")
    normalizer = component["normalizer"]
    normalized = raw if normalizer["method"] == "identity" else raw / Decimal(normalizer["value"])
    return normalized, missing


def evaluate_trace(manifest: dict[str, Any], trace: dict[str, Any]) -> EvaluationResult:
    frames = trace["frames"]
    if [frame["frame_index"] for frame in frames] != list(range(len(frames))):
        raise ConformanceError("trace frame indices are not contiguous")
    if any(_instant(left["timestamp"]) >= _instant(right["timestamp"]) for left, right in zip(frames, frames[1:])):
        raise ConformanceError("trace timestamps are not strictly increasing")
    end = _instant(trace["termination_exclusive"])
    for index, frame in enumerate(frames):
        expected_end = _instant(frames[index + 1]["timestamp"]) if index + 1 < len(frames) else end
        actual = Decimal(str((expected_end - _instant(frame["timestamp"])).total_seconds()))
        if Decimal(str(frame["duration_to_next_seconds"])) != actual:
            raise ConformanceError("frame duration does not match adjacent timestamps")
    components: dict[str, Decimal] = {}
    missing: dict[str, int] = {}
    total = Decimal(0)
    for component in manifest["loss_components"]:
        value, missing_count = _component(component, frames)
        components[component["component_id"]] = value
        missing[component["component_id"]] = missing_count
        total += value * Decimal(component["weight"])
    safe = True
    for threshold in manifest["safety_thresholds"]:
        target = Decimal(threshold["value"])
        for frame in frames:
            datum = _datum(frame, threshold["input_path"])
            if datum["unit"] != threshold["unit"]:
                raise ConformanceError("safety threshold unit mismatch")
            value = _numeric(datum["value"])
            comparator = threshold["comparator"]
            passed = {"lt": value < target, "lte": value <= target, "gt": value > target, "gte": value >= target, "eq": value == target}[comparator]
            safe = safe and passed
    return EvaluationResult(total, components, safe, missing)

