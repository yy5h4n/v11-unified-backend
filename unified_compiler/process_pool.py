from __future__ import annotations

from .types import BindingRole, PhysicalProcess, ResponsibilityContract


class ProcessConflictError(ValueError):
    pass


class PrimaryBindingConflict(ValueError):
    pass


class UnknownProcessError(KeyError):
    pass


class ProcessPool:
    def __init__(self) -> None:
        self._processes: dict[str, PhysicalProcess] = {}
        self._primary_binding: dict[str, str] = {}
        self._semantic_bindings: dict[str, list[str]] = {}
        self._contracts: dict[str, ResponsibilityContract] = {}
        self.add_count = 0
        self.dedup_count = 0

    def add(self, process: PhysicalProcess) -> PhysicalProcess:
        existing = self._processes.get(process.process_id)
        if existing is not None:
            if existing != process:
                raise ProcessConflictError(
                    f"process_id {process.process_id!r} already stored with different content"
                )
            self.dedup_count += 1
            return existing
        self._processes[process.process_id] = process
        self.add_count += 1
        return process

    def get(self, process_id: str) -> PhysicalProcess:
        try:
            return self._processes[process_id]
        except KeyError:
            raise UnknownProcessError(process_id) from None

    def __contains__(self, process_id: str) -> bool:
        return process_id in self._processes

    def __len__(self) -> int:
        return len(self._processes)

    def processes(self) -> tuple[PhysicalProcess, ...]:
        return tuple(self._processes.values())

    def bind(self, contract: ResponsibilityContract) -> ResponsibilityContract:
        if contract.process_id not in self._processes:
            raise UnknownProcessError(contract.process_id)
        if contract.role is BindingRole.PRIMARY:
            holder = self._primary_binding.get(contract.process_id)
            if holder is not None and holder != contract.contract_id:
                raise PrimaryBindingConflict(
                    f"process {contract.process_id!r} already has primary contract "
                    f"{holder!r}; cannot bind {contract.contract_id!r} as primary"
                )
            self._primary_binding[contract.process_id] = contract.contract_id
        else:
            self._semantic_bindings.setdefault(contract.process_id, [])
            if contract.contract_id not in self._semantic_bindings[contract.process_id]:
                self._semantic_bindings[contract.process_id].append(contract.contract_id)
        self._contracts[contract.contract_id] = contract
        return contract

    def primary_contract_id(self, process_id: str) -> str | None:
        return self._primary_binding.get(process_id)

    def semantic_control_contract_ids(self, process_id: str) -> tuple[str, ...]:
        return tuple(self._semantic_bindings.get(process_id, ()))

    def contracts(self) -> tuple[ResponsibilityContract, ...]:
        return tuple(self._contracts.values())
