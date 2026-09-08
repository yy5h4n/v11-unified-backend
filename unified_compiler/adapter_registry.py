from __future__ import annotations

from typing import Iterable, Sequence

from .types import BackendCapability, CapabilityStatus, ProcessAdapter


class DuplicateAdapterError(ValueError):
    pass


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ProcessAdapter] = {}
        self._capabilities: dict[str, BackendCapability] = {}

    def register(self, adapter: ProcessAdapter) -> None:
        backend = adapter.backend
        if backend in self._adapters:
            raise DuplicateAdapterError(f"adapter already registered for backend {backend!r}")
        self._adapters[backend] = adapter
        for cap in adapter.capabilities():
            self._capabilities[cap.capability_id] = cap

    def register_static_capabilities(self, capabilities: Iterable[BackendCapability]) -> None:
        for cap in capabilities:
            self._capabilities.setdefault(cap.capability_id, cap)

    @property
    def adapters(self) -> Sequence[ProcessAdapter]:
        return tuple(self._adapters.values())

    def adapter_for(self, backend: str) -> ProcessAdapter | None:
        return self._adapters.get(backend)

    def all_capability_keys(self) -> frozenset[str]:
        keys: set[str] = set()
        for cap in self._capabilities.values():
            keys.update(cap.provides)
        return frozenset(keys)

    def find_supporting_adapters(self, required_keys: frozenset[str]) -> list[ProcessAdapter]:
        supported: list[ProcessAdapter] = []
        for backend in sorted(self._adapters):
            adapter = self._adapters[backend]
            provided: set[str] = set()
            for cap in adapter.capabilities():
                provided.update(cap.provides)
            if required_keys <= provided:
                supported.append(adapter)
        return supported

    def capability_status(self, capability_id: str) -> CapabilityStatus | None:
        cap = self._capabilities.get(capability_id)
        return cap.status if cap else None
