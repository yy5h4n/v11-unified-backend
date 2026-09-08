"""Executable bounded-executor lifecycle probe used by acceptance evidence."""
from __future__ import annotations

import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unified_compiler.executor import BoundedBackendExecutor


def work(value: int, delay: float = 0.05) -> int:
    time.sleep(delay)
    return value


def main() -> None:
    with BoundedBackendExecutor(max_workers=1, max_pending=3, timeout_seconds=1.0) as executor:
        futures = [executor.submit(work, index) for index in range(3)]
        assert futures[0].process is not None
        assert futures[1].process is None and futures[2].process is None
        assert [executor.result(future).value for future in futures] == [0, 1, 2]
    with BoundedBackendExecutor(max_workers=1, max_pending=1, timeout_seconds=0.05) as executor:
        future = executor.submit(work, 9, delay=1.0)
        result = executor.result(future)
        assert result.timed_out and result.error == "worker deadline exceeded"
    print("executor_limits_ok")


if __name__ == "__main__":
    main()
