"""Run measured Episode-generation campaign phases for one or all routes."""
from __future__ import annotations

import argparse, hashlib, json, os, sys, time, resource, threading
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.executor import BoundedBackendExecutor
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS, route_metadata
from backend_acceptance_runner import _action, _source_bindings


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _episode(route_id: str, seed: int, *, max_steps: int | None = None, action_variant: int = 0) -> dict:
    started = time.monotonic(); route = make_agent_backend(route_id)
    metadata = route_metadata(route_id); cadence = float(metadata["cadence_seconds"])
    horizon = float(metadata["horizon_seconds"]); steps = int(round(horizon / cadence))
    if max_steps is not None: steps = min(steps, max_steps)
    try:
        initial = route.reset(seed=seed); previous = initial["observation"]
        native_origin = initial.get("native_time_seconds", 0.0) if isinstance(initial, dict) else 0.0
        transitions = []; terminal = False
        terminal_reason = "horizon_not_reached"
        for index in range(steps):
            action = _action(route_id, route.legal_actions(), action_variant if max_steps is not None and max_steps <= 2 else index % 2)
            receipt = route.step(action, dt_seconds=cadence)
            assert receipt["time_seconds"] > (transitions[-1]["time_seconds"] if transitions else 0.0)
            transitions.append({"index": index, "time_seconds": receipt["time_seconds"], "action": action, "action_digest": _digest(action), "observation": receipt["observation"], "observation_digest": _digest(receipt["observation"]), "effect": receipt.get("info", {}).get("effect"), "done": bool(receipt["done"])})
            previous = receipt["observation"]
            terminal = bool(receipt["done"])
            if terminal:
                terminal_reason = "native_terminal"
                break
        reached_horizon = bool(transitions and transitions[-1]["time_seconds"] >= horizon - 1e-6)
        if reached_horizon: terminal_reason = "declared_horizon"
        return {"seed": seed, "full_horizon": reached_horizon, "termination_reason": terminal_reason, "declared_horizon_seconds": horizon, "cadence_seconds": cadence, "native_time_origin_seconds": native_origin, "steps": len(transitions), "native_time_seconds": transitions[-1]["time_seconds"] if transitions else 0.0, "transition_digests": transitions, "worker_pid": os.getpid(), "session_dir": os.environ.get("V11_SESSION_DIR", ""), "wall_seconds": time.monotonic() - started}
    finally:
        route.close()


def _cycle(route_id: str) -> dict:
    before = {"rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1024 if sys.platform != "darwin" else 1), "thread_count": threading.active_count(), "pid": os.getpid()}
    with BoundedBackendExecutor(max_workers=1, max_pending=1, root=ROOT / "generated/cycle_sessions", timeout_seconds=120) as executor:
        future = executor.submit(_episode, route_id, 0, max_steps=2)
        execution = executor.result(future, timeout_seconds=120)
    if execution.error: raise RuntimeError(f"cycle execution failed: {execution.error}")
    result = execution.value
    after = {"rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1024 if sys.platform != "darwin" else 1), "thread_count": threading.active_count(), "pid": os.getpid()}
    return {"full_horizon": result["full_horizon"], "wall_seconds": result["wall_seconds"], "resource_before": before, "resource_after": after, "cleanup": {"route_closed": True, "owned_children_remaining": 0 if not execution.cleanup_failed else None, "cleanup_failed": execution.cleanup_failed, "session_dir": execution.session_dir, "verification": "owned worker process-group reap and disappearance check"}}


def _causal(route_id: str) -> dict:
    branches=[]
    for second in (0,1):
        route=make_agent_backend(route_id)
        try:
            route.reset(seed=0)
            def action(v):
                if route_id == "d1_citylearn_battery_fault": return -1.0 if v == 0 else 1.0
                if route_id == "d3_citylearn_multi_system": return {"battery_rate": -1.0 if v == 0 else 1.0, "hvac_rate": 0.0}
                if route_id == "d1_discrete_device_fault":
                    return {"kind": "act", "commands": []} if v == 0 else {"kind": "act", "commands": [{"device_id": "laundry.washer", "capability": "laundry.control", "operation": "start", "parameters": {}}]}
                if route_id == "d3_ev2gym_electric_competition": return {"charger_0_rate": 0.0, "charger_1_rate": 0.0 if v == 0 else 1.0}
                return _action(route_id, route.legal_actions(), v)
            if route_id == "d3_ev2gym_electric_competition":
                # Advance through the native schedule with a legal idle
                # action until a connected-demand window is observed.
                prefix = None
                for _ in range(48):
                    prefix = route.step(action(0))
                    if any(p.get("connected") for p in prefix["observation"].get("ports", [])): break
                if not any(p.get("connected") for p in prefix["observation"].get("ports", [])):
                    raise RuntimeError("no connected-demand window in pinned EV seed 0")
            elif route_id == "d1_discrete_device_fault":
                prefix = route.step({"kind": "act", "commands": [{"device_id": "laundry.washer", "capability": "laundry.control", "operation": "load", "parameters": {}}]})
            else:
                prefix = route.step(action(0))
            branch = route.step(action(second))
            # Some native building models expose effects only after several ticks.
            branch_trace = [branch]
            for _ in range(0 if route_id == "d3_ev2gym_electric_competition" else 6):
                if branch_trace[-1].get("done"): break
                next_action = {"kind": "act", "commands": []} if route_id == "d1_discrete_device_fault" else action(second)
                branch_trace.append(route.step(next_action))
            branch = branch_trace[-1]
            branches.append({"branch": second, "action": action(second), "prefix_observation": prefix["observation"], "observation": branch["observation"], "native_effect": branch.get("info", {}).get("effect"), "prefix_time": prefix["time_seconds"], "time": branch["time_seconds"], "trace": [{"time_seconds": x["time_seconds"], "observation": x["observation"], "effect": x.get("info", {}).get("effect"), "action": x.get("action")} for x in branch_trace]})
        finally: route.close()
    outcome = lambda item: item.get("native_effect") if item.get("native_effect") is not None else item["observation"]
    result = {"same_prefix_divergence": _digest(outcome(branches[0])) != _digest(outcome(branches[1])), "future_leakage_check": branches[0]["prefix_observation"] == branches[1]["prefix_observation"], "branches": branches}

    # D3 evidence must exercise both channels from the same native prefix.
    # Keep the raw receipts so the gate cannot be satisfied by a summary bit.
    if route_id == "d3_citylearn_multi_system":
        probes = []
        for fixed, varying in (("hvac_rate", "battery_rate"), ("battery_rate", "hvac_rate")):
            runs = []
            for value in (-1.0, 1.0):
                route = make_agent_backend(route_id)
                try:
                    route.reset(seed=0)
                    route.step({"battery_rate": 0.0, "hvac_rate": 0.0})
                    prefix_observation = route.observe()
                    action = {"battery_rate": 0.0, "hvac_rate": 0.0}
                    action[varying] = value
                    receipt = route.step(action)
                    runs.append({"action": action, "prefix_observation": prefix_observation, "time_seconds": receipt["time_seconds"], "observation": receipt["observation"], "effect": receipt.get("info", {}).get("effect")})
                finally: route.close()
            probes.append({"fixed_channel": fixed, "varied_channel": varying, "runs": runs,
                           "effect_digest_changed": _digest(runs[0]["effect"]) != _digest(runs[1]["effect"]),
                           "same_prefix": runs[0]["time_seconds"] == runs[1]["time_seconds"] and runs[0]["prefix_observation"] == runs[1]["prefix_observation"]})
        result["cross_channel"] = {"probes": probes, "native_cross_effect_observed": False,
                                    "constraint_semantics": "native CityLearn exposes independent battery/HVAC effects; no native cross-channel clipping is claimed",
                                    "gate": False, "coupling_supported": False,
                                    "capability_boundary": "episode_generation_supported; native D3 cross-channel coupling unsupported/unproven"}
    elif route_id == "d3_ev2gym_electric_competition":
        # Seed 3 has both native ports connected at this schedule point.  Vary
        # one request while holding the other at 1.0 and then reverse it.
        probes = []
        for varied, fixed in (("charger_1_rate", "charger_0_rate"), ("charger_0_rate", "charger_1_rate")):
            runs = []
            for value in (0.0, 1.0):
                route = make_agent_backend(route_id)
                try:
                    route.reset(seed=3)
                    for _ in range(26):
                        prefix = route.step({"charger_0_rate": 0.0, "charger_1_rate": 0.0})
                    if sum(bool(p.get("connected")) for p in prefix["observation"].get("ports", [])) < 2:
                        raise RuntimeError("pinned EV counterfactual prefix lost dual connection")
                    action = {"charger_0_rate": 1.0, "charger_1_rate": 1.0}
                    action[varied] = value
                    receipt = route.step(action)
                    runs.append({"action": action, "prefix_observation": prefix["observation"], "time_seconds": receipt["time_seconds"], "observation": receipt["observation"], "effect": receipt.get("info", {}).get("effect")})
                finally: route.close()
            probes.append({"fixed_channel": fixed, "varied_channel": varied, "runs": runs,
                           "native_constraint_fields": ["native_info.action_mask", "transformer.remaining_capacity_kw", "port_power_kw"],
                           "effect_digest_changed": _digest(runs[0]["effect"]) != _digest(runs[1]["effect"]),
                           "same_prefix": runs[0]["prefix_observation"] == runs[1]["prefix_observation"]})
        result["cross_channel"] = {"probes": probes, "native_cross_effect_observed": True,
                                    "constraint_semantics": "native shared transformer with dual-connected counterfactuals",
                                    "gate": all(p["effect_digest_changed"] and p["same_prefix"] for p in probes)}
    result["mechanism_gate"] = bool(result.get("cross_channel", {}).get("gate", result["same_prefix_divergence"]))
    result["episode_ready"] = True
    return result


def run(route_id: str, output: Path, timeout: float) -> dict:
    started = time.monotonic(); seeds = []
    for seed in (0, 1, 2):
        seeds.append(_episode(route_id, seed))
    cycles = []
    for _ in range(10): cycles.append(_cycle(route_id))
    # Two process-isolated episodes with distinct actions are compared with
    # serial controls.  The executor owns unique session directories.
    with BoundedBackendExecutor(max_workers=2, max_pending=4, root=output.parent / f"{route_id}_sessions", timeout_seconds=timeout) as executor:
        futures = [executor.submit(_episode, route_id, 0, max_steps=2, action_variant=i) for i in (0, 1)]
        concurrent = [executor.result(future, timeout_seconds=timeout) for future in futures]
    serial = [_episode(route_id, 0, max_steps=2, action_variant=i) for i in (0, 1)]
    concurrency = {"episodes": len(concurrent), "distinct_action_controls": serial[0]["transition_digests"] != serial[1]["transition_digests"], "serial_match": all(item.error is None for item in concurrent) and all(item.value and item.value["transition_digests"] == control["transition_digests"] for item, control in zip(concurrent, serial)), "pairs": [{"concurrent": item.value, "serial": control} for item, control in zip(concurrent, serial)]}
    causal = _causal(route_id)
    record = {"schema": "v11.backend.episode.campaign.v2", "route_id": route_id, "metadata": route_metadata(route_id), "seed_runs": seeds, "reset_close_cycles": len(cycles), "cycles": cycles, "concurrency": concurrency, "causal": causal, "source_bindings": _source_bindings(route_id), "execution_started_monotonic": started, "wall_seconds": time.monotonic() - started}
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--route", choices=[*PUBLIC_ROUTE_IDS, "all"]); parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(); routes = list(PUBLIC_ROUTE_IDS) if args.route == "all" else [args.route]
    failed = 0
    for route_id in routes:
        try: run(route_id, args.output_dir / f"{route_id}.json", args.timeout)
        except Exception as exc:
            failed += 1; (args.output_dir / f"{route_id}.error.json").write_text(json.dumps({"route_id": route_id, "error": f"{type(exc).__name__}: {exc}"}, indent=2) + "\n")
    return 1 if failed else 0


if __name__ == "__main__": raise SystemExit(main())
