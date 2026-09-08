"""Verified adapter over the mined CityLearn battery/PV process pool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Sequence

from ..types import BackendCapability, CapabilityStatus, PhysicalProcess, ProcessRequirement


V11_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROCESS_POOL = V11_ROOT / "generated" / "battery_pv_process_pool.json"
DEFAULT_REPLAY_GATE = V11_ROOT / "generated" / "battery_pv_process_replay_gate.json"
PROVIDES = frozenset(
    ("storage.soc", "storage.charge_discharge_action", "pv.generation_profile")
)


class BatteryPoolError(RuntimeError):
    """Raised when the verified process pool is absent or inconsistent."""


class CityLearnBatteryPVAdapter:
    backend: ClassVar[str] = "citylearn_battery"

    def __init__(
        self,
        process_pool_path: Path | str | None = None,
        replay_gate_path: Path | str | None = None,
    ) -> None:
        self.process_pool_path = Path(process_pool_path or DEFAULT_PROCESS_POOL)
        self.replay_gate_path = Path(replay_gate_path or DEFAULT_REPLAY_GATE)
        self.scan_calls = 0
        self.load_count = 0
        self._processes: tuple[PhysicalProcess, ...] | None = None

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise BatteryPoolError(f"missing battery/PV artifact: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BatteryPoolError(f"invalid battery/PV artifact {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise BatteryPoolError(f"battery/PV artifact is not an object: {path}")
        return value

    @staticmethod
    def _relative(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(V11_ROOT.parent))
        except ValueError:
            return str(path)

    def _ensure_loaded(self) -> None:
        if self._processes is not None:
            return
        pool = self._load_json(self.process_pool_path)
        gate = self._load_json(self.replay_gate_path)
        self.load_count += 1
        if gate.get("process_pool_digest") != pool.get("process_pool_digest"):
            raise BatteryPoolError("battery/PV replay gate does not match process pool")
        if gate.get("all_passed") is not True or gate.get("failed_count") != 0:
            raise BatteryPoolError("battery/PV process pool has not passed replay gate")
        gate_by_id = {
            item.get("process_id"): item
            for item in gate.get("processes", [])
            if isinstance(item, dict)
        }
        processes: list[PhysicalProcess] = []
        for item in pool.get("processes", []):
            process_id = item.get("process_id")
            gate_item = gate_by_id.get(process_id)
            if not isinstance(process_id, str) or not isinstance(gate_item, dict):
                raise BatteryPoolError("process missing from battery/PV replay gate")
            if gate_item.get("passed") is not True:
                raise BatteryPoolError(f"process failed battery/PV replay gate: {process_id}")
            start = item.get("source_start_row")
            end = item.get("source_end_row")
            source_hash = item.get("source_trace_sha256")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or end < start
                or not isinstance(source_hash, str)
                or len(source_hash) != 64
            ):
                raise BatteryPoolError(f"malformed battery/PV process: {process_id}")
            manifest = {
                "building_id": item["building_id"],
                "window": {"source_start_row": start, "source_end_row": end},
                "season_index": item["season_index"],
                "battery": item["battery"],
                "pv_nominal_power_kw": item["pv_nominal_power_kw"],
                "selection_metrics": item["metrics"],
                "process_pool_ref": self._relative(self.process_pool_path),
                "replay_gate_ref": self._relative(self.replay_gate_path),
                "trajectory_digests": gate_item["trajectory_digests"],
                "gold_actions_released": False,
            }
            processes.append(
                PhysicalProcess(
                    process_id=process_id,
                    domain="electrical_storage",
                    backend=self.backend,
                    backend_version="2.5.0",
                    source_id=f"{item['building_id']}:{start}-{end}",
                    source_hash=source_hash,
                    horizon_steps=end - start + 1,
                    observation_interval_seconds=3600.0,
                    provided_capabilities=PROVIDES,
                    state_variables=("battery_soc", "net_electricity", "solar_generation"),
                    action_types=("charge_discharge_rate",),
                    manifest=manifest,
                )
            )
        expected_count = pool.get("selected_process_count")
        if len(processes) != expected_count or len({p.process_id for p in processes}) != len(processes):
            raise BatteryPoolError("battery/PV process count or identity mismatch")
        self._processes = tuple(processes)

    def capabilities(self) -> Sequence[BackendCapability]:
        self._ensure_loaded()
        return (
            BackendCapability(
                capability_id="citylearn_battery.battery_pv",
                backend=self.backend,
                provides=tuple(sorted(PROVIDES)),
                status=CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED,
                evidence=(
                    self._relative(self.process_pool_path),
                    self._relative(self.replay_gate_path),
                ),
                metadata={"process_count": len(self._processes or ())},
            ),
        )

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        self.scan_calls += 1
        self._ensure_loaded()
        if not any(req.required_capabilities <= PROVIDES for req in requirements):
            return ()
        return self._processes or ()
