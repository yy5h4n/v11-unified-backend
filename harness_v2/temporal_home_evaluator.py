"""Temporal-home evaluator facade for future benchmark runs.

Reuses the fail-closed :func:`evaluate_workflow` semantics on the sealed run
artifact, then renames the legacy cost summary: the result carries
``device_command_count`` (derived from the terminal private backend state) and
``count_unit`` instead of ``action_cost`` / ``cost_unit``.
"""

from __future__ import annotations

from typing import Any, Mapping

from .workflow_evaluator import evaluate_workflow


def _terminal_private_state(run: Any) -> Mapping[str, Any]:
    terminal: Mapping[str, Any] = {}
    for row in getattr(run, "private_trace", ()) or ():
        if isinstance(row, Mapping) and row.get("type") == "backend_state" and isinstance(row.get("value"), Mapping):
            terminal = row["value"]
    return terminal


def evaluate_temporal_home(
    scenario: str,
    run: Any,
    *,
    contract: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = evaluate_workflow(scenario, run, contract=contract, profile=profile)
    result.pop("action_cost", None)
    result.pop("cost_unit", None)
    terminal = _terminal_private_state(run)
    result["device_command_count"] = terminal.get("device_command_count")
    result["count_unit"] = terminal.get("count_unit")
    return result


evaluate = evaluate_temporal_home

__all__ = ["evaluate", "evaluate_temporal_home"]
