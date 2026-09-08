"""Fail-closed thermal responsibility metrics for Harness V2 sealed traces.

This module deliberately does not reuse the legacy loss evaluator.  It evaluates a
continuous responsibility over the half-open interval ``[start, release)`` and
keeps unavailable metrics distinct from successful ones.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

from .semantic_validator import ConformanceError


@dataclass(frozen=True)
class AvailableMetric:
    availability: str
    value: Any = None
    reason: str | None = None

    @classmethod
    def available(cls, value: Any) -> "AvailableMetric":
        return cls("available", value, None)

    @classmethod
    def unavailable(cls, reason: str) -> "AvailableMetric":
        return cls("unavailable", None, reason)


@dataclass(frozen=True)
class ThermalArmMetrics:
    band_satisfaction: Decimal
    band_maintenance_pass: bool
    discrete_units: int
    active_room_seconds: Decimal
    room_band_satisfaction: dict[str, Decimal]
    worst_room_soft_mae_c: AvailableMetric
    mean_room_soft_mae_c: AvailableMetric
    lifecycle: AvailableMetric
    energy: AvailableMetric
    formal_safety: AvailableMetric
    realized_collateral: AvailableMetric


@dataclass(frozen=True)
class ThermalComparison:
    protocol_version: str
    manifest_version: str
    agent: ThermalArmMetrics
    noop: ThermalArmMetrics
    reference: ThermalArmMetrics | None
    delta_satisfaction: Decimal
    formal_nrg: AvailableMetric

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ConformanceError(f"{label} must be numeric")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ConformanceError(f"{label} must be finite")
    return result


def _instant(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ConformanceError(f"{label} must be an ISO timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConformanceError(f"{label} must be an ISO timestamp") from exc


def _pointer(document: dict[str, Any], pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ConformanceError("evaluator binding must be an absolute JSON pointer")
    node: Any = document
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            raise ConformanceError(f"evaluator binding does not resolve: {pointer}")
        node = node[key]
    return node


def _datum(frame: dict[str, Any], pointer: str) -> dict[str, Any]:
    node = _pointer(frame, pointer)
    required = {"value", "unit", "quality", "observed_at"}
    if not isinstance(node, dict) or not required <= set(node):
        raise ConformanceError(f"binding is not a trace datum: {pointer}")
    return node


def _assert_no_private_target(manifest: dict[str, Any]) -> None:
    allowed = manifest.get("public_preferences")
    if allowed is not None and not isinstance(allowed, dict):
        raise ConformanceError("public_preferences must be an object")

    def walk(node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = path + (str(key),)
                if (key in {"target_c", "soft_target_c"} or str(key).endswith("target_c")) and child[:1] != ("public_preferences",):
                    raise ConformanceError("soft temperature target must come from public_preferences")
                walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + (str(index),))

    walk(manifest, ())


def _config(manifest: dict[str, Any]) -> dict[str, Any]:
    required = {"manifest_version", "thermal_evaluator_v3", "nrg"}
    if not required <= set(manifest):
        raise ConformanceError("thermal evaluator manifest is incomplete")
    cfg = manifest["thermal_evaluator_v3"]
    if not isinstance(cfg, dict) or cfg.get("protocol_version") != "thermal_evaluator_v3":
        raise ConformanceError("unsupported thermal evaluator protocol version")
    room_bindings = cfg.get("room_bindings")
    if not isinstance(room_bindings, dict) or not room_bindings:
        raise ConformanceError("room_bindings must name at least one room")
    if any(not isinstance(room, str) or not room or not isinstance(binding, str) for room, binding in room_bindings.items()):
        raise ConformanceError("room_bindings are malformed")
    active = cfg.get("active_window")
    if not isinstance(active, dict) or set(active) != {"start_inclusive", "release_exclusive"}:
        raise ConformanceError("active_window must explicitly define start and release")
    start = _instant(active["start_inclusive"], "active start")
    release = _instant(active["release_exclusive"], "active release")
    if release <= start:
        raise ConformanceError("active window has zero or negative duration")
    band = cfg.get("acceptable_band_c")
    if not isinstance(band, dict) or set(band) != {"lower", "upper"}:
        raise ConformanceError("acceptable_band_c must define lower and upper")
    lower, upper = _decimal(band["lower"], "band lower"), _decimal(band["upper"], "band upper")
    if lower > upper:
        raise ConformanceError("temperature band is inverted")
    max_stale = _decimal(cfg.get("max_staleness_seconds"), "max_staleness_seconds")
    if max_stale < 0:
        raise ConformanceError("max_staleness_seconds must be nonnegative")
    nrg = manifest["nrg"]
    if not isinstance(nrg, dict) or set(nrg) != {"definition_version", "gamma", "min_discrete_units"}:
        raise ConformanceError("versioned NRG gamma and min_discrete_units must be explicit")
    if not isinstance(nrg["definition_version"], str) or not nrg["definition_version"]:
        raise ConformanceError("NRG definition_version is required")
    gamma = _decimal(nrg["gamma"], "NRG gamma")
    units = nrg["min_discrete_units"]
    if gamma < 0 or isinstance(units, bool) or not isinstance(units, int) or units < 1:
        raise ConformanceError("invalid NRG eligibility configuration")
    _assert_no_private_target(manifest)
    return cfg


def _frame_intervals(trace: dict[str, Any]) -> Iterable[tuple[dict[str, Any], datetime, datetime]]:
    frames = trace.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ConformanceError("trace must contain frames")
    prior_end: datetime | None = None
    for expected_index, frame in enumerate(frames):
        if not isinstance(frame, dict) or frame.get("frame_index") != expected_index:
            raise ConformanceError("trace frame indices must be contiguous")
        start = _instant(frame.get("timestamp"), "frame timestamp")
        duration = _decimal(frame.get("duration_to_next_seconds"), "frame duration")
        if duration <= 0:
            raise ConformanceError("frame duration must be positive")
        end = start + timedelta(seconds=float(duration))
        if prior_end is not None and start != prior_end:
            raise ConformanceError("trace frames must form a contiguous partition")
        prior_end = end
        yield frame, start, end


def _valid_temperature(
    frame: dict[str, Any], pointer: str, unit: str, max_staleness: Decimal, frame_start: datetime
) -> Decimal:
    datum = _datum(frame, pointer)
    if datum["unit"] != unit:
        raise ConformanceError(f"temperature unit mismatch for {pointer}")
    if datum["quality"] != "fresh" or datum["value"] is None:
        raise ConformanceError(f"missing or stale temperature for {pointer}")
    observed = _instant(datum["observed_at"], "temperature observed_at")
    age = Decimal(str((frame_start - observed).total_seconds()))
    if age < 0 or age > max_staleness:
        raise ConformanceError(f"stale temperature for {pointer}")
    return _decimal(datum["value"], "temperature")


def _optional_metric(trace: dict[str, Any], spec: Any, name: str) -> AvailableMetric:
    if spec is None or not isinstance(spec, dict) or not spec.get("binding"):
        return AvailableMetric.unavailable(f"{name}_binding_absent")
    aggregation = spec.get("aggregation")
    if aggregation not in {"all", "mean", "sum", "terminal"}:
        raise ConformanceError(f"invalid {name} aggregation")
    values: list[Decimal | bool] = []
    for frame, _, _ in _frame_intervals(trace):
        datum = _datum(frame, spec["binding"])
        if datum["quality"] != "fresh" or datum["value"] is None:
            raise ConformanceError(f"{name} binding is not fresh")
        value = datum["value"]
        if aggregation == "all":
            if not isinstance(value, bool):
                raise ConformanceError(f"{name} all aggregation requires booleans")
            values.append(value)
        else:
            values.append(_decimal(value, name))
    if aggregation == "all":
        return AvailableMetric.available(all(values))
    if aggregation == "terminal":
        return AvailableMetric.available(values[-1])
    if aggregation == "sum":
        return AvailableMetric.available(sum(values, Decimal(0)))
    return AvailableMetric.available(sum(values, Decimal(0)) / Decimal(len(values)))


def _collateral(trace: dict[str, Any], spec: Any) -> AvailableMetric:
    if spec is None or not isinstance(spec, dict) or "device_scope_map" not in spec:
        return AvailableMetric.unavailable("realized_collateral_binding_absent")
    scope = spec["device_scope_map"]
    if not isinstance(scope, dict):
        raise ConformanceError("device_scope_map must be an object")
    applied = 0
    collateral = 0
    for frame, _, _ in _frame_intervals(trace):
        commands = frame.get("applied_commands")
        if not isinstance(commands, list):
            raise ConformanceError("applied_commands must be an array")
        for command in commands:
            if not isinstance(command, dict):
                raise ConformanceError("applied command record is malformed")
            status = command.get("status")
            if status not in {"committed", "coalesced", "rejected", "failed"}:
                raise ConformanceError("unknown applied command status")
            if status not in {"committed", "coalesced"}:
                continue
            device = command.get("device_id")
            if not isinstance(device, str) or device not in scope:
                raise ConformanceError("committed command lacks device scope mapping")
            classification = scope[device]
            if classification not in {"in_scope", "out_of_scope"}:
                raise ConformanceError("invalid device scope classification")
            applied += 1
            collateral += int(classification == "out_of_scope")
    value = {
        "committed_or_coalesced_commands": applied,
        "out_of_scope_commands": collateral,
        "fraction": Decimal(collateral) / Decimal(applied) if applied else Decimal(0),
    }
    return AvailableMetric.available(value)


def evaluate_thermal_arm(manifest: dict[str, Any], trace: dict[str, Any]) -> ThermalArmMetrics:
    cfg = _config(manifest)
    active = cfg["active_window"]
    active_start = _instant(active["start_inclusive"], "active start")
    release = _instant(active["release_exclusive"], "active release")
    lower = _decimal(cfg["acceptable_band_c"]["lower"], "band lower")
    upper = _decimal(cfg["acceptable_band_c"]["upper"], "band upper")
    unit = cfg.get("temperature_unit")
    if not isinstance(unit, str) or not unit:
        raise ConformanceError("temperature_unit must be explicit")
    max_stale = _decimal(cfg["max_staleness_seconds"], "max staleness")
    room_good: dict[str, Decimal] = {room: Decimal(0) for room in cfg["room_bindings"]}
    room_total: dict[str, Decimal] = {room: Decimal(0) for room in cfg["room_bindings"]}
    room_error: dict[str, Decimal] = {room: Decimal(0) for room in cfg["room_bindings"]}
    discrete_units = 0
    target: Decimal | None = None
    public = manifest.get("public_preferences")
    if isinstance(public, dict) and "target_c" in public:
        if public.get("visibility") != "public":
            raise ConformanceError("soft target must be explicitly public")
        target = _decimal(public["target_c"], "public target_c")
    intervals = list(_frame_intervals(trace))
    if intervals[0][1] > active_start or intervals[-1][2] < release:
        raise ConformanceError("trace does not cover the complete active window")
    for frame, frame_start, frame_end in intervals:
        overlap_start, overlap_end = max(frame_start, active_start), min(frame_end, release)
        if overlap_end <= overlap_start:
            continue
        seconds = Decimal(str((overlap_end - overlap_start).total_seconds()))
        for room, binding in cfg["room_bindings"].items():
            value = _valid_temperature(frame, binding, unit, max_stale, frame_start)
            room_total[room] += seconds
            discrete_units += 1
            if lower <= value <= upper:
                room_good[room] += seconds
            if target is not None:
                room_error[room] += abs(value - target) * seconds
    if not discrete_units or any(value == 0 for value in room_total.values()):
        raise ConformanceError("trace does not cover every room in the active window")
    total = sum(room_total.values(), Decimal(0))
    good = sum(room_good.values(), Decimal(0))
    room_sat = {room: room_good[room] / room_total[room] for room in room_total}
    if target is None:
        worst_mae = mean_mae = AvailableMetric.unavailable("public_soft_target_absent")
    else:
        maes = [room_error[room] / room_total[room] for room in room_total]
        worst_mae = AvailableMetric.available(max(maes))
        mean_mae = AvailableMetric.available(sum(maes, Decimal(0)) / Decimal(len(maes)))
    optional = manifest.get("optional_metrics")
    if optional is not None and not isinstance(optional, dict):
        raise ConformanceError("optional_metrics must be an object")
    optional = optional or {}
    return ThermalArmMetrics(
        band_satisfaction=good / total,
        band_maintenance_pass=good == total,
        discrete_units=discrete_units,
        active_room_seconds=total,
        room_band_satisfaction=room_sat,
        worst_room_soft_mae_c=worst_mae,
        mean_room_soft_mae_c=mean_mae,
        lifecycle=_optional_metric(trace, optional.get("lifecycle"), "lifecycle"),
        energy=_optional_metric(trace, optional.get("energy"), "energy"),
        formal_safety=_optional_metric(trace, optional.get("formal_safety"), "formal_safety"),
        realized_collateral=_collateral(trace, optional.get("realized_collateral")),
    )


def evaluate_thermal_v3(
    manifest: dict[str, Any],
    agent_trace: dict[str, Any],
    noop_trace: dict[str, Any],
    reference_trace: dict[str, Any] | None = None,
) -> ThermalComparison:
    """Evaluate agent/no-op/reference arms under one preregistered manifest."""

    _config(manifest)
    agent = evaluate_thermal_arm(manifest, agent_trace)
    noop = evaluate_thermal_arm(manifest, noop_trace)
    reference = evaluate_thermal_arm(manifest, reference_trace) if reference_trace is not None else None
    delta = agent.band_satisfaction - noop.band_satisfaction
    if reference is None:
        nrg = AvailableMetric.unavailable("reference_trace_absent")
    else:
        gamma = _decimal(manifest["nrg"]["gamma"], "NRG gamma")
        minimum = manifest["nrg"]["min_discrete_units"]
        counts = {agent.discrete_units, noop.discrete_units, reference.discrete_units}
        if len(counts) != 1:
            raise ConformanceError("NRG arms have different discrete obligation units")
        gap = reference.band_satisfaction - noop.band_satisfaction
        if agent.discrete_units < minimum:
            nrg = AvailableMetric.unavailable("insufficient_discrete_units")
        elif gap <= gamma:
            nrg = AvailableMetric.unavailable("reference_gap_not_strictly_above_gamma")
        else:
            nrg = AvailableMetric.available(delta / gap)
    return ThermalComparison(
        protocol_version="thermal_evaluator_v3",
        manifest_version=str(manifest["manifest_version"]),
        agent=agent,
        noop=noop,
        reference=reference,
        delta_satisfaction=delta,
        formal_nrg=nrg,
    )
