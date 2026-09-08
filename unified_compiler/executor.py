"""Bounded process-isolated execution for backend episodes."""
from __future__ import annotations
import multiprocessing as mp
import os
import signal
import shutil, tempfile, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

@dataclass(frozen=True)
class ExecutionResult:
    value: Any = None
    error: str | None = None
    timed_out: bool = False
    wall_seconds: float = 0.0
    session_dir: str = ""
    cleanup_failed: bool = False

def _worker(conn, fn, args, kwargs, session_dir):
    # Give each worker a private process group so native descendants can be
    # reaped together on timeout/close.
    if os.name == "posix":
        os.setsid()
    os.environ["V11_SESSION_DIR"] = session_dir
    try: conn.send((True, fn(*args, **kwargs)))
    except BaseException as exc: conn.send((False, f"{type(exc).__name__}: {exc}"))
    finally: conn.close()

class _ProcessFuture:
    def __init__(self, process, conn, session, fn=None, args=(), kwargs=None):
        self.process, self.conn, self.session = process, conn, session
        self.started = process is not None
        self._fn, self._args, self._kwargs = fn, args, (kwargs or {})

class BoundedBackendExecutor:
    """Bounded queue; timed out workers are terminated and reaped before cleanup."""
    def __init__(self, max_workers: int = 2, root: str | Path | None = None, timeout_seconds: float = 60.0, max_pending: int | None = None):
        if isinstance(max_workers, bool) or max_workers < 1 or timeout_seconds <= 0: raise ValueError("invalid executor limits")
        self.max_workers=int(max_workers); self.max_pending=int(max_pending or max_workers*2)
        self.root=Path(root or tempfile.gettempdir()) / "v11_backend_sessions"; self.root.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds=float(timeout_seconds); self._active=[]; self._running=set()
        self._ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context()
    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> _ProcessFuture:
        if len(self._active) >= self.max_pending: raise RuntimeError("executor admission queue is full")
        session=Path(tempfile.mkdtemp(prefix="episode_", dir=self.root)); parent, child=self._ctx.Pipe(False)
        proc = None
        if len(self._running) < self.max_workers:
            proc = self._start_process(fn, args, kwargs, session, child)
        try:
            if proc is None:
                child.close()
        except BaseException:
            try: child.close()
            except OSError: pass
            shutil.rmtree(session, ignore_errors=True)
            raise
        future=_ProcessFuture(proc, parent, session, fn, args, kwargs); self._active.append(future)
        if proc is not None: self._running.add(future)
        return future

    def _start_process(self, fn, args, kwargs, session, child):
        try:
            # Native adapters launch solver subprocesses; daemon workers are
            # prohibited from creating children under multiprocessing.
            proc=self._ctx.Process(target=_worker, args=(child, fn, args, kwargs, str(session)), daemon=False)
            proc.start(); child.close(); return proc
        except BaseException:
            try: child.close()
            except OSError: pass
            shutil.rmtree(session, ignore_errors=True)
            raise

    def _refresh_slots(self):
        for future in list(self._running):
            if not future.process.is_alive():
                self._running.discard(future)

    def _start_queued(self, future, deadline):
        self._refresh_slots()
        if future.started: return
        while len(self._running) >= self.max_workers:
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            self._refresh_slots()
        parent, child = self._ctx.Pipe(False)
        # The original parent endpoint is retained by the future; replace it
        # with the endpoint paired to this queued worker.
        try:
            future.conn.close()
        except OSError: pass
        future.conn = parent
        future.process = self._start_process(future._fn, future._args, future._kwargs, future.session, child)
        future.started = True
        self._running.add(future)
        return True
    def result(self, future: _ProcessFuture, timeout_seconds: float | None = None) -> ExecutionResult:
        if future not in self._active: raise ValueError("unknown or already collected future")
        started=time.monotonic(); timeout=float(timeout_seconds or self.timeout_seconds)
        deadline = started + timeout
        if not future.started and not self._start_queued(future, deadline):
            try: future.conn.close()
            except OSError: pass
            if future in self._active: self._active.remove(future)
            shutil.rmtree(future.session, ignore_errors=True)
            return ExecutionResult(error="worker deadline exceeded while queued", timed_out=True, wall_seconds=time.monotonic()-started, session_dir=str(future.session))
        try:
            remaining = max(0.0, deadline - time.monotonic())
            if not future.conn.poll(remaining):
                cleanup_error = self._stop_and_reap(future.process)
                return ExecutionResult(error="worker deadline exceeded" + (f"; cleanup failed: {cleanup_error}" if cleanup_error else ""), timed_out=True, wall_seconds=time.monotonic()-started, session_dir=str(future.session), cleanup_failed=bool(cleanup_error))
            try:
                ok,value=future.conn.recv()
            except (EOFError, OSError) as exc:
                cleanup_error = self._stop_and_reap(future.process)
                return ExecutionResult(error=f"worker exited without result: {exc}" + (f"; cleanup failed: {cleanup_error}" if cleanup_error else ""), wall_seconds=time.monotonic()-started, session_dir=str(future.session), cleanup_failed=bool(cleanup_error))
            future.process.join(timeout=2.0)
            cleanup_error = self._stop_and_reap(future.process)
            error = None if ok else str(value)
            if cleanup_error: error = (error + "; " if error else "") + f"cleanup failed: {cleanup_error}"
            return ExecutionResult(value=value if ok and not cleanup_error else None, error=error, wall_seconds=time.monotonic()-started, session_dir=str(future.session), cleanup_failed=bool(cleanup_error))
        finally:
            try: future.conn.close()
            except OSError: pass
            if future in self._active: self._active.remove(future)
            self._running.discard(future)
            if not 'cleanup_error' in locals() or not cleanup_error:
                shutil.rmtree(future.session, ignore_errors=True)

    @staticmethod
    def _stop_and_reap(process):
        if process is None: return None
        failures = []
        if os.name == "posix":
            # Kill the owned group even when the multiprocessing leader has
            # already exited; descendants can outlive that leader.
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            except PermissionError as exc: failures.append(f"SIGTERM: {exc}")
            process.join(timeout=2.0)
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            except PermissionError as exc: failures.append(f"SIGKILL: {exc}")
            process.join(timeout=2.0)
            try:
                os.killpg(process.pid, 0)
                failures.append("process group still exists after reap")
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                failures.append(f"verify: {exc}")
        else:
            if process.is_alive(): process.terminate(); process.join(timeout=2.0)
            if process.is_alive(): process.kill(); process.join(timeout=2.0)
        return "; ".join(failures) or None
    def close(self) -> None:
        for future in list(self._active):
            cleanup_error = self._stop_and_reap(future.process) if future.started else None
            try: future.conn.close()
            except OSError: pass
            if cleanup_error:
                (future.session / "CLEANUP_FAILED").write_text(cleanup_error + "\n")
            else:
                shutil.rmtree(future.session, ignore_errors=True)
            self._active.remove(future)
    def __enter__(self): return self
    def __exit__(self, *_): self.close()

__all__=["BoundedBackendExecutor","ExecutionResult"]
