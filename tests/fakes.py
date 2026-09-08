from __future__ import annotations

from typing import Sequence

from unified_compiler import (
    BackendCapability,
    CapabilityStatus,
    PhysicalProcess,
    ProcessRequirement,
)


def make_process(
    pid: str,
    backend: str,
    provides: frozenset[str] = frozenset({"storage.soc", "storage.charge_discharge_action"}),
) -> PhysicalProcess:
    return PhysicalProcess(
        process_id=pid,
        domain="dom",
        backend=backend,
        backend_version="0.0.1-fake",
        source_id=f"fake://{backend}/{pid}",
        source_hash=f"sha256:fake-{pid}",
        horizon_steps=24,
        observation_interval_seconds=3600.0,
        provided_capabilities=provides,
        state_variables=("soc",),
        action_types=("charge",),
        manifest={"handle": pid},
    )


class FakeAdapter:
    def __init__(
        self,
        backend: str,
        provides: tuple[str, ...],
        processes: tuple[PhysicalProcess, ...] = (),
        status: CapabilityStatus = CapabilityStatus.API_VERIFIED_NOT_DATA_PROBED,
    ) -> None:
        self._backend = backend
        self._capability = BackendCapability(
            capability_id=f"fake.{backend}",
            backend=backend,
            provides=provides,
            status=status,
            evidence=("fake://test",),
        )
        self._processes = processes
        self.scan_calls: list[tuple[str, ...]] = []

    @property
    def backend(self) -> str:
        return self._backend

    def capabilities(self) -> Sequence[BackendCapability]:
        return (self._capability,)

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        self.scan_calls.append(tuple(r.requirement_id for r in requirements))
        return self._processes
