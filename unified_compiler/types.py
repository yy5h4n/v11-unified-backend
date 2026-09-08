from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


class ResponsibilityLifecycle(str, Enum):
    MAINTAIN = "MAINTAIN"
    ACHIEVE_BY = "ACHIEVE_BY"
    PREPARE_FOR = "PREPARE_FOR"
    RECOVER_AFTER_EVENT = "RECOVER_AFTER_EVENT"
    OPTIMIZE_UNDER = "OPTIMIZE_UNDER"
    GUARD = "GUARD"


class PhysicalTopology(str, Enum):
    THERMAL_DYNAMICS = "THERMAL_DYNAMICS"
    STORAGE_DYNAMICS = "STORAGE_DYNAMICS"
    CONTAMINANT_DYNAMICS = "CONTAMINANT_DYNAMICS"
    NONINTERRUPTIBLE_CYCLE = "NONINTERRUPTIBLE_CYCLE"
    SPATIAL_MOBILITY = "SPATIAL_MOBILITY"


class CapabilityStatus(str, Enum):
    EXECUTABLE_REPLAY_VERIFIED = "EXECUTABLE_REPLAY_VERIFIED"
    API_VERIFIED_NOT_DATA_PROBED = "API_VERIFIED_NOT_DATA_PROBED"
    LEGACY_EXECUTABLE_PENDING_MIGRATION = "LEGACY_EXECUTABLE_PENDING_MIGRATION"
    DATA_PROBED_PENDING_REPLAY = "DATA_PROBED_PENDING_REPLAY"
    CAPABILITY_UNVERIFIED = "CAPABILITY_UNVERIFIED"


class BindingRole(str, Enum):
    PRIMARY = "PRIMARY"
    SEMANTIC_CONTROL = "SEMANTIC_CONTROL"


class ClauseKind(str, Enum):
    HARD_INVARIANT = "HARD_INVARIANT"
    TERMINAL_GOAL = "TERMINAL_GOAL"
    CUMULATIVE_SOFT_COST = "CUMULATIVE_SOFT_COST"


@dataclass(frozen=True)
class BackendCapability:
    capability_id: str
    backend: str
    provides: tuple[str, ...]
    status: CapabilityStatus
    evidence: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessRequirement:
    requirement_id: str
    responsibility_lifecycle: ResponsibilityLifecycle
    physical_topology: PhysicalTopology
    required_capabilities: frozenset[str]
    state_variables: tuple[str, ...]
    action_types: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PhysicalProcess:
    process_id: str
    domain: str
    backend: str
    backend_version: str
    source_id: str
    source_hash: str
    horizon_steps: int
    observation_interval_seconds: float
    provided_capabilities: frozenset[str]
    state_variables: tuple[str, ...]
    action_types: tuple[str, ...]
    manifest: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrajectoryClause:
    clause_id: str
    kind: ClauseKind
    variable: str
    op: str
    params: Mapping[str, Any] = field(default_factory=dict)
    weight: float = 1.0


@dataclass(frozen=True)
class ResponsibilityContract:
    contract_id: str
    process_id: str
    responsibility: str
    role: BindingRole
    clauses: tuple[TrajectoryClause, ...] = ()


@dataclass(frozen=True)
class Episode:
    episode_id: str
    contract_id: str
    process_id: str
    horizon: int
    exogenous_events: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ProcessAdapter(Protocol):
    @property
    def backend(self) -> str: ...

    def capabilities(self) -> Sequence[BackendCapability]: ...

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]: ...
