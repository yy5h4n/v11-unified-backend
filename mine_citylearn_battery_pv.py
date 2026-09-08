#!/usr/bin/env python3
"""Mine responsibility-relevant battery/PV windows from annual CityLearn traces.

This is a process miner, not an Episode writer.  It scans one canonical,
source-grounded CityLearn load/PV profile per modeled residence and emits only
windows that contain a meaningful physical opportunity to move PV surplus into
a later grid-deficit period.
Responsibility Contracts are bound downstream.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from probe_citylearn_battery_replay import (
    ASSET_MANIFEST,
    ROOT,
    SOURCE_CACHE,
    building_battery_runtime,
    canonical_digest,
    require_assets,
    shared_sizing_catalogs,
)
from unified_compiler.adapters.citylearn_shared import CityLearnSourceIndex


HORIZON_STEPS = 24
SEASON_START_MONTHS = (0, 3, 6, 9)
MIN_ABSOLUTE_SHIFT_KWH = 0.75
MIN_CAPACITY_FRACTION = 0.05


def window_metrics(load: np.ndarray, solar: np.ndarray, capacity: float) -> dict[str, Any]:
    """Return causal-opportunity metrics for one chronological window."""
    if len(load) != HORIZON_STEPS or len(solar) != HORIZON_STEPS:
        raise ValueError("battery/PV candidate windows must contain exactly 24 steps")
    net = load - solar
    surplus = np.maximum(-net, 0.0)
    peak_solar_step = int(np.argmax(solar))
    later_deficit = np.maximum(net[peak_solar_step + 1 :], 0.0)
    surplus_kwh = float(surplus.sum())
    later_deficit_kwh = float(later_deficit.sum())
    transferable_kwh = min(surplus_kwh, later_deficit_kwh, 0.8 * capacity)
    minimum_shift_kwh = max(MIN_ABSOLUTE_SHIFT_KWH, MIN_CAPACITY_FRACTION * capacity)
    return {
        "pv_surplus_kwh": surplus_kwh,
        "later_grid_deficit_kwh": later_deficit_kwh,
        "transferable_kwh": transferable_kwh,
        "minimum_shift_kwh": minimum_shift_kwh,
        "peak_solar_step": peak_solar_step,
        "peak_solar_kw": float(solar.max()),
        "peak_load_kw": float(load.max()),
        "idle_grid_import_kwh": float(np.maximum(net, 0.0).sum()),
        "idle_grid_export_kwh": float(np.maximum(-net, 0.0).sum()),
        "qualifies": transferable_kwh >= minimum_shift_kwh,
        "selection_score": transferable_kwh / max(capacity, 1e-9),
    }


def season_index(day_index: int) -> int:
    """Coarse quarter-year bucket used only for diversity selection."""
    return min(day_index // 91, 3)


def select_diverse_days(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select at most one highest-opportunity day per residence and season."""
    selected: list[dict[str, Any]] = []
    for season in range(4):
        bucket = [item for item in candidates if item["season_index"] == season]
        if bucket:
            selected.append(
                max(
                    bucket,
                    key=lambda item: (
                        item["metrics"]["selection_score"],
                        item["metrics"]["transferable_kwh"],
                        -item["day_index"],
                    ),
                )
            )
    return selected


def mine() -> dict[str, Any]:
    manifest = require_assets()
    source_index = CityLearnSourceIndex()
    bundles = source_index.bundles()
    building_ids = sorted({bundle.building_id for bundle in bundles})
    trace_digest_by_building = {
        building_id: next(
            bundle.source_trace_sha256
            for bundle in bundles
            if bundle.building_id == building_id
        )
        for building_id in building_ids
    }

    selected: list[dict[str, Any]] = []
    qualifying_count = 0
    per_building: dict[str, Any] = {}
    with shared_sizing_catalogs():
        for building_id in building_ids:
            runtime = building_battery_runtime(building_id)
            load = runtime["load"]
            solar = runtime["solar"]
            battery = runtime["battery_template"]
            candidates: list[dict[str, Any]] = []
            for start in range(0, len(load) - HORIZON_STEPS + 1, HORIZON_STEPS):
                day_index = start // HORIZON_STEPS
                metrics = window_metrics(
                    load[start : start + HORIZON_STEPS],
                    solar[start : start + HORIZON_STEPS],
                    float(battery.capacity),
                )
                if not metrics["qualifies"]:
                    continue
                candidates.append(
                    {
                        "building_id": building_id,
                        "day_index": day_index,
                        "season_index": season_index(day_index),
                        "source_start_row": start,
                        "source_end_row": start + HORIZON_STEPS - 1,
                        "metrics": metrics,
                    }
                )
            chosen = select_diverse_days(candidates)
            qualifying_count += len(candidates)
            for item in chosen:
                process_id = (
                    f"citylearn-battery-pv://{building_id}/"
                    f"rows:{item['source_start_row']}-{item['source_end_row']}"
                )
                selected.append(
                    {
                        "process_id": process_id,
                        **item,
                        "battery": {
                            "capacity_kwh": float(battery.capacity),
                            "nominal_power_kw": float(battery.nominal_power),
                            "initial_soc": 0.25,
                        },
                        "pv_nominal_power_kw": runtime["pv_nominal_power"],
                        "source_trace_sha256": trace_digest_by_building[building_id],
                    }
                )
            per_building[building_id] = {
                "qualifying_day_count": len(candidates),
                "selected_day_count": len(chosen),
                "selected_day_indices": [item["day_index"] for item in chosen],
                "battery_capacity_kwh": float(battery.capacity),
                "battery_nominal_power_kw": float(battery.nominal_power),
                "pv_nominal_power_kw": runtime["pv_nominal_power"],
            }

    selected.sort(key=lambda item: (item["building_id"], item["source_start_row"]))
    return {
        "schema_version": "v11-battery-pv-process-pool-1",
        "selection_rule": {
            "horizon_steps": HORIZON_STEPS,
            "chronology": "PV surplus must precede the measured later grid deficit",
            "minimum_transferable_kwh": (
                f"max({MIN_ABSOLUTE_SHIFT_KWH}, "
                f"{MIN_CAPACITY_FRACTION} * battery_capacity_kwh)"
            ),
            "diversity": "highest normalized transferable energy per residence-season",
            "gold_actions_used": False,
        },
        "building_count": len(building_ids),
        "scanned_window_count": len(building_ids) * 365,
        "qualifying_window_count": qualifying_count,
        "selected_process_count": len(selected),
        "processes": selected,
        "per_building": per_building,
        "assets": {
            "citylearn_tag_commit": manifest["tag_commit"],
            "manifest": str(ASSET_MANIFEST.relative_to(ROOT)),
            "source_schema": str((SOURCE_CACHE / "schema.json").relative_to(ROOT.parent)),
        },
        "process_pool_digest": canonical_digest(selected),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "generated" / "battery_pv_process_pool.json",
    )
    args = parser.parse_args()
    report = mine()
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.write_text(payload + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "building_count": report["building_count"],
                "scanned_window_count": report["scanned_window_count"],
                "qualifying_window_count": report["qualifying_window_count"],
                "selected_process_count": report["selected_process_count"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
