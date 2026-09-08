from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .adapter_registry import AdapterRegistry
from .process_pool import ProcessPool
from .types import (
    BindingRole,
    PhysicalProcess,
    ProcessRequirement,
    ResponsibilityContract,
)


@dataclass(frozen=True)
class CompileReport:
    processes: tuple[PhysicalProcess, ...]
    unsupported_requirements: tuple[ProcessRequirement, ...]
    unsatisfied_requirements: tuple[ProcessRequirement, ...]
    rejected_processes: tuple[PhysicalProcess, ...]
    scans_per_adapter: dict[str, int]
    process_matches: dict[str, tuple[str, ...]]
    deduplicated_process_ids: tuple[str, ...] = ()


class UnifiedCompiler:
    def __init__(self, registry: AdapterRegistry, pool: ProcessPool | None = None) -> None:
        self._registry = registry
        self._pool = pool if pool is not None else ProcessPool()

    @property
    def pool(self) -> ProcessPool:
        return self._pool

    def compile(self, requirements: Sequence[ProcessRequirement]) -> CompileReport:
        batches: dict[str, list[ProcessRequirement]] = {}
        batched: list[ProcessRequirement] = []
        unsupported: list[ProcessRequirement] = []
        for req in requirements:
            supporters = self._registry.find_supporting_adapters(req.required_capabilities)
            if not supporters:
                unsupported.append(req)
                continue
            batches.setdefault(supporters[0].backend, []).append(req)
            batched.append(req)

        stored: list[PhysicalProcess] = []
        deduped: list[str] = []
        rejected: list[PhysicalProcess] = []
        scans: dict[str, int] = {}
        matches: dict[str, tuple[str, ...]] = {}
        satisfied: set[str] = set()
        for backend, batch in sorted(batches.items()):
            adapter = self._registry.adapter_for(backend)
            assert adapter is not None
            scans[backend] = scans.get(backend, 0) + 1
            for process in adapter.scan(batch):
                req_ids = self._match_process(process, backend, batch)
                if req_ids is None:
                    rejected.append(process)
                    continue
                if process.process_id in self._pool:
                    self._pool.add(process)
                    deduped.append(process.process_id)
                else:
                    self._pool.add(process)
                    stored.append(process)
                prior = matches.get(process.process_id, ())
                merged = list(prior)
                for rid in req_ids:
                    if rid not in merged:
                        merged.append(rid)
                matches[process.process_id] = tuple(merged)
                satisfied.update(req_ids)

        unsatisfied = [r for r in batched if r.requirement_id not in satisfied]
        return CompileReport(
            processes=tuple(stored),
            unsupported_requirements=tuple(unsupported),
            unsatisfied_requirements=tuple(unsatisfied),
            rejected_processes=tuple(rejected),
            scans_per_adapter=scans,
            process_matches=matches,
            deduplicated_process_ids=tuple(deduped),
        )

    @staticmethod
    def _match_process(
        process: PhysicalProcess,
        expected_backend: str,
        batch: Sequence[ProcessRequirement],
    ) -> tuple[str, ...] | None:
        if process.backend != expected_backend:
            return None
        req_ids = tuple(
            r.requirement_id
            for r in batch
            if r.required_capabilities <= process.provided_capabilities
        )
        if not req_ids:
            return None
        return req_ids

    def bind_contract(self, contract: ResponsibilityContract) -> ResponsibilityContract:
        return self._pool.bind(contract)

    def make_primary_contract(
        self,
        contract_id: str,
        process_id: str,
        responsibility: str,
        clauses=(),
    ) -> ResponsibilityContract:
        contract = ResponsibilityContract(
            contract_id=contract_id,
            process_id=process_id,
            responsibility=responsibility,
            role=BindingRole.PRIMARY,
            clauses=tuple(clauses),
        )
        return self.bind_contract(contract)

    def make_semantic_control_contract(
        self,
        contract_id: str,
        process_id: str,
        responsibility: str,
        clauses=(),
    ) -> ResponsibilityContract:
        contract = ResponsibilityContract(
            contract_id=contract_id,
            process_id=process_id,
            responsibility=responsibility,
            role=BindingRole.SEMANTIC_CONTROL,
            clauses=tuple(clauses),
        )
        return self.bind_contract(contract)
