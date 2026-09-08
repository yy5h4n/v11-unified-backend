#!/usr/bin/env python3
"""Fail-closed runtime acceptance runner for the 15 public V11 routes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V10_PYTHON = ROOT.parent / "v10_diversity_aware_compiler/.runtime/venv/bin/python"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS, inventory, route_metadata


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _source_bindings(route_id: str) -> dict[str, str]:
    """Bind each result to the adapter/registry code used to produce it."""
    source_names = {
        "d0_exogenous_context": "unified_compiler/adapters/d0_exogenous_context.py",
        "d1_sustaingym_fault": "unified_compiler/adapters/d1_fault_mechanism.py",
        "d1_citylearn_battery_fault": "unified_compiler/adapters/citylearn_battery_fault.py",
        "d1_ev2gym_fault": "unified_compiler/adapters/ev2gym_fault.py",
        "d1_discrete_device_fault": "unified_compiler/agent_interface.py",
        "energyplus_iaq": "d2_humidity_air_quality_adapter.py",
        "wntr_residential_water": "unified_compiler/adapters/d2_wntr.py",
        "fds_smoke_fire": "d2_fds_adapter.py",
        "modelica_buildings_aixlib": "d2_modelica_buildings_aixlib_adapter.py",
        "d3_citylearn_multi_system": "d3_citylearn_coupling_adapter.py",
        "d3_citylearn_multibuilding_competition": "d3_citylearn_multibuilding_adapter.py",
        "d3_wntr_water_competition": "d3_wntr_water_competition_adapter.py",
        "d3_modelica_shared_heat": "d3_modelica_shared_heat_adapter.py",
        "d3_ev2gym_electric_competition": "d3_ev2gym_electric_competition_adapter.py",
        "d3_energyplus_shared_ventilation": "d3_energyplus_shared_ventilation_adapter.py",
    }
    paths = [ROOT / source_names[route_id], ROOT / "unified_compiler/agent_interface.py", ROOT / "unified_compiler/route_registry.py", ROOT / "tools/backend_acceptance_runner.py"]
    if route_id in {"energyplus_iaq", "d3_energyplus_shared_ventilation"}:
        paths.append(ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64/energyplus")
    if route_id in {"fds_smoke_fire"}:
        paths.append(ROOT / "shared_runtime/fds/bin/fds")
    result = {}
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def interpreter_for(route_id: str) -> str:
    if route_id in {"d1_ev2gym_fault", "d3_ev2gym_electric_competition"} and V10_PYTHON.is_file():
        return str(V10_PYTHON)
    ana = Path("/opt/anaconda3/bin/python")
    return str(ana if ana.is_file() else Path(sys.executable))


def _action(route_id: str, legal: dict, variant: int):
    # Route-specific native payloads are intentionally explicit.  No action is
    # invented when the schema cannot identify one; that route fails closed.
    if route_id == "d0_exogenous_context":
        return {"kind": "act", "command": {"target": "interior_lights", "operation": "off" if variant == 0 else "on"}}
    if route_id == "d1_discrete_device_fault":
        return {"kind": "act", "commands": []} if variant == 0 else {"kind": "wait", "mode": "for", "duration_seconds": 60}
    if route_id in {"energyplus_iaq", "modelica_buildings_aixlib"}:
        return 0.0 if variant == 0 else 1.0
    if route_id == "wntr_residential_water":
        return float(variant)
    if route_id == "d3_wntr_water_competition":
        return {"shower_valve_open": float(variant), "laundry_valve_open": 0.0}
    if route_id == "fds_smoke_fire":
        return 0.0 if variant == 0 else 1.0
    if route_id == "d3_modelica_shared_heat":
        return {"space_heating_request": float(variant), "dhw_request": 1.0}
    if route_id == "d3_ev2gym_electric_competition":
        return {"charger_0_rate": float(variant), "charger_1_rate": 0.0}
    if route_id == "d3_energyplus_shared_ventilation":
        return {"zone_a_airflow_request": float(variant), "zone_b_airflow_request": 0.0}
    if route_id == "d3_citylearn_multi_system":
        return {"battery_rate": float(variant), "hvac_rate": 0.0}
    if route_id == "d3_citylearn_multibuilding_competition":
        from d3_citylearn_multibuilding_adapter import DEFAULT_BUILDINGS
        return {DEFAULT_BUILDINGS[0]: {"battery_rate": float(variant), "hvac_rate": 0.0}, DEFAULT_BUILDINGS[1]: {"battery_rate": 0.0, "hvac_rate": 0.0}}
    if route_id in {"d1_citylearn_battery_fault"}:
        return float(variant)
    if route_id in {"d1_sustaingym_fault"}:
        return [(-0.05 if variant == 0 else 0.0)] * 27
    if route_id in {"d1_ev2gym_fault"}:
        return {"type": "SET_CHARGE_POWER", "kw": 0.0 if variant == 0 else 1.0}
    raise ValueError(f"no native action probe defined for {route_id}; legal_actions={legal}")


def run_one(route_id: str, *, loops: int = 10) -> dict:
    started = time.monotonic()
    record = {"route_id": route_id, "metadata": route_metadata(route_id), "status": "failed", "checks": {}, "errors": [], "source_bindings": _source_bindings(route_id), "seed": 0, "horizon_seconds": route_metadata(route_id).get("horizon_seconds")}
    backend = None
    try:
        from unified_compiler.agent_interface import make_agent_backend
        backend = make_agent_backend(route_id)
        first = backend.reset(seed=0)
        obs0 = deepcopy(first["observation"])
        legal = backend.legal_actions()
        a0, a1 = _action(route_id, legal, 0), _action(route_id, legal, 1)
        t1 = backend.step(a0)
        t2 = backend.step(a1)
        if t1["delta_t_seconds"] <= 0 or t2["delta_t_seconds"] <= 0:
            raise AssertionError("non-positive delta_t_seconds")
        if _digest(t1["observation"]) == _digest(t2["observation"]):
            raise AssertionError("different actions did not change native observation")
        # Invalid dt and non-finite action must be rejected before native step.
        before = _digest(backend.observe())
        for bad in (float("nan"), float("inf"), 0.0, -1.0):
            try:
                backend.step(a0, dt_seconds=bad)
            except Exception:
                pass
            else:
                raise AssertionError(f"invalid dt accepted: {bad!r}")
        after = _digest(backend.observe())
        if before != after:
            raise AssertionError("invalid operation changed observation")
        for seed in (0, 1, 2):
            backend.reset(seed=seed)
            for variant in (0, 1):
                backend.step(_action(route_id, backend.legal_actions(), variant))
        for _ in range(loops):
            # close() is terminal for one public session; each repeated
            # lifecycle probe therefore owns a fresh native backend.
            cycle = make_agent_backend(route_id)
            try:
                cycle.reset(seed=0)
                cycle.step(_action(route_id, cycle.legal_actions(), 0))
            finally:
                cycle.close()
        backend.reset(seed=0)
        copied = backend.observe()
        copied["__mutation_probe__"] = True
        if "__mutation_probe__" in backend.observe():
            raise AssertionError("observation is not a deep copy")
        backend.close(); backend.close()
        try:
            backend.observe()
        except Exception:
            pass
        else:
            raise AssertionError("observe accepted after close")
        record["status"] = "passed"
        record["checks"] = {"reset_observe_legal_actions": True, "two_actions": True, "invalid_preflight": True, "seed_matrix": 3, "reset_close_loops": loops, "close_idempotent": True, "deep_copy": True, "full_horizon": False, "same_prefix_causality": False, "mechanism_gate": False}
    except (ImportError, ModuleNotFoundError, FileNotFoundError) as exc:
        record["status"] = "pending"
        record["errors"].append({"category": "dependency", "error": repr(exc)})
    except Exception as exc:
        record["errors"].append({"category": "runtime", "error": repr(exc)})
    finally:
        if backend is not None:
            try: backend.close()
            except Exception: pass
    record["wall_seconds"] = round(time.monotonic() - started, 6)
    return record


def _child(route_id: str, output: Path, loops: int) -> int:
    result = run_one(route_id, loops=loops)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--single-route", action="store_true")
    parser.add_argument("--interpreter")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated/backend_acceptance_v1")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--loops", type=int, default=10)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        manifest = args.output_dir / "manifest.json"
        acceptance = args.output_dir / "acceptance.json"
        required = {"manifest.json", "acceptance.json", "route_matrix.json", "runtime_manifest.json", "stability.json"}
        if not manifest.is_file() or not acceptance.is_file() or not required <= {p.name for p in args.output_dir.glob("*.json")}:
            return 2
        try:
            data = json.loads(manifest.read_text()); summary = json.loads(acceptance.read_text())
            if data.get("schema") != "v11.backend.acceptance.manifest.v1": return 1
            if summary.get("schema") != "v11.backend.acceptance.v1" or summary.get("overall") != "READY_FOR_ASTRA": return 1
            if tuple(r.get("route_id") for r in summary.get("route_inventory", [])) != tuple(PUBLIC_ROUTE_IDS): return 1
            if len(summary.get("routes", [])) != len(PUBLIC_ROUTE_IDS): return 1
            route_ids = [r.get("route_id") for r in summary["routes"]]
            if route_ids != list(PUBLIC_ROUTE_IDS) or len(set(route_ids)) != len(PUBLIC_ROUTE_IDS): return 1
            if any(r.get("status") != "passed" for r in summary["routes"]): return 1
            if any(not r.get("checks", {}).get("full_horizon") or not r.get("checks", {}).get("episode_ready", True) for r in summary["routes"]): return 1
            if summary.get("counts", {}).get("passed") != len(PUBLIC_ROUTE_IDS): return 1
            matrix = json.loads((args.output_dir / "route_matrix.json").read_text())
            matrix_routes = matrix.get("routes", [])
            if [r.get("route_id") for r in matrix_routes] != list(PUBLIC_ROUTE_IDS): return 1
            # A summary of booleans is not evidence.  Every route must have
            # its own immutable result and execution log, with source hashes
            # that still match the current work copy.
            for route_id, route in zip(PUBLIC_ROUTE_IDS, summary["routes"]):
                result_path = args.output_dir / f"{route_id}.json"
                log_path = args.output_dir / f"{route_id}.log"
                if not result_path.is_file() or not log_path.is_file() or not log_path.read_text(errors="replace").strip(): return 1
                if route != json.loads(result_path.read_text()): return 1
                if route.get("metadata", {}).get("route_id") != route_id: return 1
                if not isinstance(route.get("source_bindings"), dict) or not route["source_bindings"]: return 1
                for rel, expected in route["source_bindings"].items():
                    source = ROOT / rel
                    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected: return 1
                if float(route.get("wall_seconds", 0)) <= 0 or float(route.get("runner_wall_seconds", 0)) <= 0: return 1
                if int(route.get("seed", -1)) != 0 or route.get("horizon_seconds") != route_metadata(route_id).get("horizon_seconds"): return 1
                campaign = route.get("campaign")
                if not isinstance(campaign, dict): return 1
                seeds = campaign.get("seed_runs", [])
                if not isinstance(seeds, list) or {item.get("seed") for item in seeds if isinstance(item, dict)} != {0, 1, 2}: return 1
                authoritative = route_metadata(route_id)
                if float(campaign.get("metadata", {}).get("horizon_seconds", -1)) != float(authoritative.get("horizon_seconds", -2)) or float(campaign.get("metadata", {}).get("cadence_seconds", -1)) != float(authoritative.get("cadence_seconds", -2)): return 1
                for item in seeds:
                    if not isinstance(item, dict) or not item.get("full_horizon") or not item.get("transition_digests") or not item.get("termination_reason"): return 1
                    trace=item["transition_digests"]; cadence=float(item.get("cadence_seconds", 0)); horizon=float(item.get("declared_horizon_seconds", 0))
                    if cadence != float(authoritative.get("cadence_seconds", -2)) or horizon != float(authoritative.get("horizon_seconds", -2)): return 1
                    if int(item.get("steps", -1)) != len(trace) or cadence <= 0 or horizon <= 0: return 1
                    times=[float(x.get("time_seconds", -1)) for x in trace]
                    if abs(times[0]-cadence)>1e-6 or any(abs((b-a)-cadence)>1e-6 for a,b in zip(times,times[1:])): return 1
                    for transition in trace:
                        if _digest(transition.get("action")) != transition.get("action_digest") or _digest(transition.get("observation")) != transition.get("observation_digest"): return 1
                    if abs(times[-1]-horizon)>1e-6 or not item.get("full_horizon") or item.get("termination_reason") != "declared_horizon": return 1
                if int(campaign.get("reset_close_cycles", 0)) < 10: return 1
                if any(not c.get("resource_before", {}).get("rss_bytes") or not c.get("resource_after", {}).get("rss_bytes") or not c.get("cleanup", {}).get("route_closed") or c.get("cleanup", {}).get("owned_children_remaining") is None or c.get("cleanup", {}).get("cleanup_failed") for c in campaign.get("cycles", [])): return 1
                concurrency = campaign.get("concurrency", {})
                if int(concurrency.get("episodes", 0)) < 2 or not concurrency.get("serial_match") or not concurrency.get("distinct_action_controls") or len(concurrency.get("pairs", [])) < 2: return 1
                for pair in concurrency.get("pairs", []):
                    if not pair.get("concurrent") or not pair.get("serial"): return 1
                    ct, st = pair["concurrent"].get("transition_digests", []), pair["serial"].get("transition_digests", [])
                    if not ct or ct != st: return 1
                    times = [float(x.get("time_seconds", -1)) for x in ct]
                    if any(b <= a for a, b in zip(times, times[1:])): return 1
                causal = campaign.get("causal", {})
                if not causal.get("future_leakage_check") or not causal.get("branches"): return 1
                if route_id in {"d3_citylearn_multi_system", "d3_ev2gym_electric_competition"}:
                    cross = causal.get("cross_channel", {})
                    if not cross.get("probes") or len(cross["probes"]) != 2: return 1
                    for probe in cross["probes"]:
                        runs = probe.get("runs", [])
                        if len(runs) != 2 or not probe.get("same_prefix"): return 1
                        if runs[0].get("prefix_observation") != runs[1].get("prefix_observation"): return 1
                        if any(not isinstance(run.get("action"), dict) or run.get("effect") is None or run.get("prefix_observation") is None for run in runs): return 1
                        fixed, varied = probe.get("fixed_channel"), probe.get("varied_channel")
                        if not fixed or not varied or fixed == varied: return 1
                        if runs[0]["action"].get(fixed) != runs[1]["action"].get(fixed) or runs[0]["action"].get(varied) == runs[1]["action"].get(varied): return 1
                    if route_id == "d3_citylearn_multi_system":
                        if cross.get("native_cross_effect_observed") is not False or cross.get("gate") is not False or cross.get("coupling_supported") is not False: return 1
                        if route_metadata(route_id).get("d3_coupling_supported") is not False: return 1
                    elif not cross.get("gate"): return 1
            if matrix_routes != summary["routes"]: return 1
            stability = json.loads((args.output_dir / "stability.json").read_text())
            measurements = stability.get("measurements", {})
            if stability.get("status") != "passed" or not measurements or float(measurements.get("wall_seconds_sum", 0)) <= 0: return 1
            if int(measurements.get("queue_tasks", 0)) < 30 or float(measurements.get("active_soak_seconds", 0)) < 600: return 1
            if not stability.get("event_stream") or not Path(stability["event_stream"]).is_file(): return 1
            events = [json.loads(line) for line in Path(stability["event_stream"]).read_text().splitlines() if line.strip()]
            if len(events) < int(measurements.get("queue_tasks", 0)) or any(e.get("error") for e in events): return 1
            if any(e.get("phase") == "active" and e.get("seed") != (e.get("result", {}) or {}).get("seed") for e in events): return 1
            if not stability.get("resource_sampling", {}).get("rss_max_bytes", 0): return 1
            if any("rss_bytes" not in e or "fd_count" not in e or "thread_count" not in e for e in events): return 1
            if int(measurements.get("max_concurrent_episodes", 0)) < 2: return 1
            if not data.get("sha256") or set(data["sha256"]) != {p.name for p in args.output_dir.iterdir() if p.name != "manifest.json"}: return 1
            changed = ROOT / "CHANGED_FILES.json"
            if not changed.is_file(): return 1
            for entry in json.loads(changed.read_text()).get("entries", []):
                path = ROOT / entry["path"]
                if entry.get("new_sha256") and (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["new_sha256"]): return 1
        except (OSError, ValueError, TypeError, KeyError):
            return 1
        for rel, expected in data.get("sha256", {}).items():
            path = args.output_dir / rel
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return 1
        return 0
    if args.single_route:
        if not args.route: return 2
        args.output_dir.mkdir(parents=True, exist_ok=True)
        return _child(args.route, args.output_dir / f"{args.route}.json", args.loops)
    routes = [args.route] if args.route else list(PUBLIC_ROUTE_IDS)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for route_id in routes:
        target = args.output_dir / f"{route_id}.json"
        interpreter = interpreter_for(route_id)
        cmd = [interpreter, __file__, "--single-route", "--route", route_id, "--output-dir", str(args.output_dir), "--loops", str(args.loops)]
        started = time.monotonic()
        child = None
        try:
            child = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=(os.name == "posix"))
            # communicate() drains both pipes while enforcing the deadline;
            # waiting first can deadlock a verbose native child on a full
            # stdout/stderr pipe.
            stdout, stderr = child.communicate(timeout=args.timeout)
            log = args.output_dir / f"{route_id}.log"
            log.write_text(stdout + "\n--- stderr ---\n" + stderr)
            result = json.loads(target.read_text()) if target.is_file() else {"route_id": route_id, "status": "failed", "errors": [{"category": "runner", "error": "child produced no result"}]}
            result.update({"exit_code": child.returncode, "runner_wall_seconds": round(time.monotonic() - started, 6), "interpreter": interpreter, "log": log.name})
        except subprocess.TimeoutExpired as exc:
            # Native adapters may launch their own simulator children.  Kill
            # the whole session before recording a timeout so no worker can
            # outlive the route and retain its session directory.
            if child is not None:
                try:
                    if os.name == "posix":
                        os.killpg(child.pid, signal.SIGTERM)
                    else:
                        child.terminate()
                    child.communicate(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        if os.name == "posix": os.killpg(child.pid, signal.SIGKILL)
                        else: child.kill()
                    except OSError: pass
                    try: child.communicate(timeout=5)
                    except subprocess.TimeoutExpired: pass
            result = {"route_id": route_id, "metadata": route_metadata(route_id), "status": "failed", "checks": {}, "errors": [{"category": "timeout", "error": str(exc)}], "exit_code": None, "interpreter": interpreter, "log": f"{route_id}.log", "source_bindings": _source_bindings(route_id), "seed": 0, "horizon_seconds": route_metadata(route_id).get("horizon_seconds"), "wall_seconds": round(time.monotonic() - started, 6), "runner_wall_seconds": round(time.monotonic() - started, 6)}
            try:
                stdout, stderr = child.communicate(timeout=1) if child is not None else ("", "")
            except Exception:
                stdout, stderr = "", ""
            (args.output_dir / f"{route_id}.log").write_text(stdout + "\n--- stderr ---\n" + stderr)
        target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        results.append(result)
    complete = len(results) == 15 and all(r.get("status") == "passed" for r in results) and all(r.get("checks", {}).get("full_horizon") and r.get("checks", {}).get("mechanism_gate") for r in results)
    summary = {"schema": "v11.backend.acceptance.v1", "route_inventory": inventory(), "routes": results, "counts": {s: sum(r.get("status") == s for r in results) for s in ("passed", "failed", "pending")}, "overall": "READY_FOR_ASTRA" if complete else "BLOCKED", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (args.output_dir / "acceptance.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (args.output_dir / "route_matrix.json").write_text(json.dumps({"schema": "v11.backend.route_matrix.v1", "routes": results}, indent=2, sort_keys=True) + "\n")
    measured = {"route_count": len(results), "passed_count": summary["counts"]["passed"], "failed_count": summary["counts"]["failed"], "pending_count": summary["counts"]["pending"], "wall_seconds_sum": round(sum(float(r.get("runner_wall_seconds", r.get("wall_seconds", 0.0))) for r in results), 6), "wall_seconds_max": round(max((float(r.get("runner_wall_seconds", r.get("wall_seconds", 0.0))) for r in results), default=0.0), 6), "process_isolation": True}
    (args.output_dir / "stability.json").write_text(json.dumps({"schema": "v11.backend.stability.v1", "method": {"seed_count": 3, "reset_close_loops": args.loops, "concurrent_episodes": 2, "queue_tasks": 30, "long_soak_seconds": 600}, "status": "passed" if complete else "BLOCKED", "measurements": measured, "reason": "full-horizon/mechanism/resource campaign incomplete; no synthetic pass is emitted", "observed_acceptance_counts": summary["counts"]}, indent=2, sort_keys=True) + "\n")
    (args.output_dir / "runtime_manifest.json").write_text(json.dumps({"schema": "v11.backend.runtime_manifest.v1", "python": sys.executable, "python_version": sys.version, "platform": sys.platform, "machine": os.uname().machine, "route_count": len(results), "command": f"PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python tools/backend_acceptance_runner.py --all --output-dir generated/backend_acceptance_v1 --timeout {args.timeout:g} --loops {args.loops}"}, indent=2, sort_keys=True) + "\n")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.output_dir.iterdir()) if p.name != "manifest.json" and p.is_file()}
    (args.output_dir / "manifest.json").write_text(json.dumps({"schema": "v11.backend.acceptance.manifest.v1", "sha256": hashes}, indent=2, sort_keys=True) + "\n")
    return 0 if summary["overall"] == "READY_FOR_ASTRA" else 1


if __name__ == "__main__":
    raise SystemExit(main())
