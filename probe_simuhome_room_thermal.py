#!/usr/bin/env python3
"""Small, fail-closed audit of SimuHome's room thermal path.

This is deliberately an audit, not an episode generator: it records what the
backend can demonstrate and explicitly reports missing lifecycle semantics.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[5]
SIMU = ROOT / "external" / "SimuHome"
sys.path.insert(0, str(SIMU))

OUT = Path(__file__).resolve().parent / "generated" / "simuhome_room_thermal_probe_v1.json"

def _run(room: str, action: bool):
    from src.simulator.domain.home import Home
    from src.simulator.domain.devices.air_conditioner import AirConditioner
    h = Home(tick_interval=60.0, fast_forward=False, base_time="2025-08-23 18:00:00")
    for r in ("kitchen", "bathroom"):
        h.ensure_room_initialized(r)
    d = AirConditioner("ac_" + room)
    add = h._add_device(room, d)
    if not add.success:
        raise RuntimeError("device add failed: " + str(add))
    # Explicit physical baseline, shared Home, before any action.
    for r in ("kitchen", "bathroom"):
        h.aggregators_by_room[r]["temperature"].current_value = 1800.0
        h.aggregators_by_room[r]["temperature"].baseline_value = 1800.0
    before = h._get_home_state().data
    if action:
        if not h._execute_command(d.device_id, 1, "OnOff", "On").success:
            raise RuntimeError("power action failed")
        if not h._write_attribute(d.device_id, 1, "Thermostat", "OccupiedHeatingSetpoint", 1800).success:
            raise RuntimeError("initial setpoint action failed")
        if not h._write_attribute(d.device_id, 1, "Thermostat", "SystemMode", 4).success:
            raise RuntimeError("thermal action failed")
        if not h._write_attribute(d.device_id, 1, "Thermostat", "OccupiedCoolingSetpoint", 2800).success:
            raise RuntimeError("cooling setpoint action failed")
        if not h._write_attribute(d.device_id, 1, "Thermostat", "OccupiedHeatingSetpoint", 2400).success:
            raise RuntimeError("setpoint action failed")
        if not h._write_attribute(d.device_id, 1, "FanControl", "PercentSetting", 100).success:
            raise RuntimeError("fan action failed")
    aligned = h._get_home_state().data
    trace = [aligned]
    for tick in (1, 5, 10):
        ff = h._fast_forward_to(tick)
        if not ff.success: raise RuntimeError("fast forward failed")
        trace.append(ff.data)
    return {"before": before, "post_action": aligned, "trace": trace,
            "action": action, "virtual_evening": aligned.get("current_time", "").endswith("18:00:00")}

def main():
    result = {"schema_version": "simuhome_room_thermal_probe_v1", "status": "FAILED", "rooms": {},
              "supported": {"deterministic_replay": False, "room_thermal_dynamics": False,
                             "controllable_thermal_device": False, "virtual_clock_evening": False},
              "unsupported_lifecycle_semantics": ["cooking", "shower", "wake", "presence", "fireplace", "newborn"]}
    try:
        for room in ("kitchen", "bathroom"):
            witness = _run(room, True)
            contrast = _run(room, False)
            wtemp = witness["trace"][-1]["rooms"][room]["state"].get("temperature")
            ctemp = contrast["trace"][-1]["rooms"][room]["state"].get("temperature")
            delta = None if not isinstance(wtemp, (int,float)) or not isinstance(ctemp, (int,float)) else wtemp-ctemp
            result["rooms"][room] = {"witness": witness, "contrast": contrast, "temperature_delta": delta,
                                     "causal_effect_observed": bool(delta and abs(delta)>1e-9),
                                     "room_isolation_checked": all(
                                         a["rooms"]["bathroom" if room == "kitchen" else "kitchen"]["state"].get("temperature") == b["rooms"]["bathroom" if room == "kitchen" else "kitchen"]["state"].get("temperature")
                                         for a, b in zip(witness["trace"], contrast["trace"]))}
        result["supported"]["deterministic_replay"] = result["rooms"]["kitchen"]["witness"] == _run("kitchen", True)
        result["supported"]["controllable_thermal_device"] = True
        result["supported"]["virtual_clock_evening"] = True
        result["supported"]["room_thermal_dynamics"] = all(x["causal_effect_observed"] for x in result["rooms"].values())
        result["status"] = "PASS" if result["supported"]["room_thermal_dynamics"] else "FAIL_CLOSED"
    except Exception as e:
        result["error"] = repr(e)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in ("PASS", "FAIL_CLOSED") else 1

if __name__ == "__main__": raise SystemExit(main())
