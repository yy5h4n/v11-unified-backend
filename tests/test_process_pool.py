import pytest

from unified_compiler import (
    BindingRole,
    PhysicalProcess,
    PrimaryBindingConflict,
    ProcessConflictError,
    ProcessPool,
    ResponsibilityContract,
    UnknownProcessError,
)


def make_process(pid: str = "p1", domain: str = "dom", backend: str = "fake") -> PhysicalProcess:
    return PhysicalProcess(
        process_id=pid,
        domain=domain,
        backend=backend,
        backend_version="0.0.1-fake",
        source_id=f"fake://{backend}/{pid}",
        source_hash=f"sha256:fake-{pid}",
        horizon_steps=24,
        observation_interval_seconds=3600.0,
        provided_capabilities=frozenset({"storage.soc", "storage.charge_discharge_action"}),
        state_variables=("soc",),
        action_types=("charge",),
        manifest={"handle": pid},
    )


def make_contract(cid: str, pid: str, role: BindingRole) -> ResponsibilityContract:
    return ResponsibilityContract(
        contract_id=cid, process_id=pid, responsibility=f"resp-{cid}", role=role
    )


def test_process_stored_once_and_deduplicated():
    pool = ProcessPool()
    p = make_process()
    assert pool.add(p) is p
    assert pool.add(make_process()) is p
    assert len(pool) == 1
    assert pool.add_count == 1
    assert pool.dedup_count == 1


def test_conflicting_process_content_rejected():
    pool = ProcessPool()
    pool.add(make_process())
    with pytest.raises(ProcessConflictError):
        pool.add(make_process(domain="other"))


def test_one_primary_binding_per_process():
    pool = ProcessPool()
    pool.add(make_process())
    pool.bind(make_contract("c1", "p1", BindingRole.PRIMARY))
    with pytest.raises(PrimaryBindingConflict):
        pool.bind(make_contract("c2", "p1", BindingRole.PRIMARY))
    assert pool.primary_contract_id("p1") == "c1"


def test_semantic_controls_multiple_and_labeled():
    pool = ProcessPool()
    pool.add(make_process())
    pool.bind(make_contract("c1", "p1", BindingRole.PRIMARY))
    pool.bind(make_contract("s1", "p1", BindingRole.SEMANTIC_CONTROL))
    pool.bind(make_contract("s2", "p1", BindingRole.SEMANTIC_CONTROL))
    assert pool.primary_contract_id("p1") == "c1"
    assert pool.semantic_control_contract_ids("p1") == ("s1", "s2")
    semantic = [c for c in pool.contracts() if c.role is BindingRole.SEMANTIC_CONTROL]
    assert {c.contract_id for c in semantic} == {"s1", "s2"}


def test_bind_unknown_process_fails_closed():
    pool = ProcessPool()
    with pytest.raises(UnknownProcessError):
        pool.bind(make_contract("c1", "missing", BindingRole.PRIMARY))
