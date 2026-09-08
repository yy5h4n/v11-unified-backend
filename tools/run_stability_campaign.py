"""Execute the required mixed queue and active native work stability phases."""
from __future__ import annotations
import argparse, json, os, sys, time, resource
try:
    import psutil
except ImportError:
    psutil = None
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from unified_compiler.executor import BoundedBackendExecutor
from run_episode_campaign import _episode


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--active-seconds", type=float, default=600.0); parser.add_argument("--queue-tasks", type=int, default=30)
    args = parser.parse_args(); args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic(); completed = failed = 0; peak = 0; queue_results = []; events_path = args.output.with_suffix(".events.jsonl")
    events_path.write_text("")
    def event(payload):
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1024 if sys.platform != "darwin" else 1)
        proc = psutil.Process() if psutil else None
        try:
            children = [c.pid for c in proc.children(recursive=True)] if proc else []
        except (PermissionError, psutil.Error if psutil else OSError):
            children = None
        payload.update({"monotonic": time.monotonic(), "rss_bytes": int(rss), "pid": os.getpid(), "fd_count": proc.num_fds() if proc and hasattr(proc, "num_fds") else None, "thread_count": proc.num_threads() if proc else None, "children": children})
        with events_path.open("a") as stream: stream.write(json.dumps(payload, default=str, sort_keys=True) + "\n")
    routes = ["d0_exogenous_context", "d3_modelica_shared_heat", "d3_citylearn_multi_system", "d1_discrete_device_fault"]
    with BoundedBackendExecutor(max_workers=2, max_pending=8, root=args.output.parent / "stability_sessions", timeout_seconds=120) as executor:
        futures = []; submitted = 0
        while submitted < args.queue_tasks:
            while submitted < args.queue_tasks and len(futures) < executor.max_pending:
                futures.append(executor.submit(_episode, routes[submitted % len(routes)], submitted % 3, max_steps=2)); submitted += 1
                peak = max(peak, len(executor._running))
            future = futures.pop(0)
            result = executor.result(future, timeout_seconds=120)
            completed += result.error is None; failed += result.error is not None
            actual_seed = len(queue_results)%3; item={"job_id": len(queue_results), "route": routes[len(queue_results) % len(routes)], "seed": actual_seed, "result_seed": result.value.get("seed") if isinstance(result.value, dict) else None, "error": result.error, "timed_out": result.timed_out, "wall_seconds": result.wall_seconds, "session_dir": result.session_dir}; queue_results.append(item); event({"phase":"queue","job":item})
        while futures:
            result = executor.result(futures.pop(0), timeout_seconds=120)
            completed += result.error is None; failed += result.error is not None
            actual_seed = len(queue_results)%3; item={"job_id": len(queue_results), "route": routes[len(queue_results) % len(routes)], "seed": actual_seed, "result_seed": result.value.get("seed") if isinstance(result.value, dict) else None, "error": result.error, "timed_out": result.timed_out, "wall_seconds": result.wall_seconds, "session_dir": result.session_dir}; queue_results.append(item); event({"phase":"queue","job":item})
        active_started = time.monotonic()
        while time.monotonic() - active_started < args.active_seconds:
            route = routes[completed % len(routes)]; job_started=time.monotonic()
            try:
                result = _episode(route, completed % 3, max_steps=2); error=None
            except Exception as exc:
                result=None; error=f"{type(exc).__name__}: {exc}"; failed += 1
            completed += 1
            actual_seed=(completed-1)%3; event({"phase":"active","route":route,"seed":actual_seed,"result_seed":result.get("seed") if isinstance(result,dict) else None,"error":error,"wall_seconds":time.monotonic()-job_started,"result":result})
    elapsed = time.monotonic() - started
    active_measured = max(0.0, time.monotonic() - active_started)
    record = {"schema": "v11.backend.stability.measurement.v3", "queue_tasks": args.queue_tasks, "queue_completed": len(queue_results), "active_soak_seconds": active_measured, "active_native_work": True, "completed_episodes": completed, "failed_episodes": failed, "max_concurrent_episodes": peak, "queue_results": queue_results, "event_stream": str(events_path), "resource_sampling": {"rss_max_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1024 if sys.platform != "darwin" else 1), "units": "bytes"}, "status": "passed" if failed == 0 and len(queue_results) == args.queue_tasks and active_measured >= args.active_seconds else "BLOCKED"}
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return 0 if record["status"] == "passed" else 1


if __name__ == "__main__": raise SystemExit(main())
