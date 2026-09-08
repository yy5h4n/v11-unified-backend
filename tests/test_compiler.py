import inspect

import pytest

from unified_compiler import (
    AdapterRegistry,
    PhysicalTopology,
    PrimaryBindingConflict,
    ProcessRequirement,
    ResponsibilityLifecycle,
    UnifiedCompiler,
)

from fakes import FakeAdapter, make_process


def make_req(rid: str, caps: frozenset[str]) -> ProcessRequirement:
    return ProcessRequirement(
        requirement_id=rid,
        responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
        physical_topology=PhysicalTopology.STORAGE_DYNAMICS,
        required_capabilities=caps,
        state_variables=("soc",),
        action_types=("charge",),
    )


def test_scan_called_once_per_adapter_for_requirement_batch():
    adapter = FakeAdapter(
        "fakeA",
        provides=("storage.soc", "storage.charge_discharge_action"),
        processes=(make_process("p1", "fakeA"),),
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    compiler = UnifiedCompiler(registry)

    reqs = [
        make_req("r1", frozenset({"storage.soc"})),
        make_req("r2", frozenset({"storage.soc", "storage.charge_discharge_action"})),
    ]
    report = compiler.compile(reqs)

    assert len(adapter.scan_calls) == 1
    assert adapter.scan_calls[0] == ("r1", "r2")
    assert report.scans_per_adapter == {"fakeA": 1}


def test_processes_deduplicated_across_compile_calls():
    adapter = FakeAdapter(
        "fakeA",
        provides=("storage.soc",),
        processes=(make_process("p1", "fakeA"),),
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    compiler = UnifiedCompiler(registry)
    req = make_req("r1", frozenset({"storage.soc"}))

    report1 = compiler.compile([req])
    report2 = compiler.compile([req])

    assert len(report1.processes) == 1
    assert report2.processes == ()
    assert report2.deduplicated_process_ids == ("p1",)
    assert len(compiler.pool) == 1


def test_unsupported_capability_fails_closed():
    adapter = FakeAdapter("fakeA", provides=("storage.soc",))
    registry = AdapterRegistry()
    registry.register(adapter)
    compiler = UnifiedCompiler(registry)

    req = make_req("r-unsupported", frozenset({"robot.motion_action"}))
    report = compiler.compile([req])

    assert adapter.scan_calls == []
    assert report.processes == ()
    assert [r.requirement_id for r in report.unsupported_requirements] == ["r-unsupported"]


def test_process_from_wrong_backend_rejected():
    adapter = FakeAdapter(
        "fakeA",
        provides=("storage.soc",),
        processes=(make_process("p1", "fakeB"),),
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    compiler = UnifiedCompiler(registry)

    report = compiler.compile([make_req("r1", frozenset({"storage.soc"}))])

    assert report.processes == ()
    assert [p.process_id for p in report.rejected_processes] == ["p1"]
    assert [r.requirement_id for r in report.unsatisfied_requirements] == ["r1"]
    assert len(compiler.pool) == 0


def test_process_missing_claimed_capabilities_rejected():
    adapter = FakeAdapter(
        "fakeA",
        provides=("storage.soc", "storage.charge_discharge_action"),
        processes=(make_process("p1", "fakeA", provides=frozenset({"storage.soc"})),),
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    compiler = UnifiedCompiler(registry)

    req = make_req("r2", frozenset({"storage.soc", "storage.charge_discharge_action"}))
    report = compiler.compile([req])

    assert report.processes == ()
    assert [p.process_id for p in report.rejected_processes] == ["p1"]
    assert [r.requirement_id for r in report.unsatisfied_requirements] == ["r2"]
    assert len(compiler.pool) == 0


def test_one_batched_scan_covers_multiple_requirements_and_records_matches():
    adapter = FakeAdapter(
        "fakeA",
        provides=("storage.soc", "storage.charge_discharge_action"),
        processes=(
            make_process("p1", "fakeA"),
            make_process("p2", "fakeA", provides=frozenset({"storage.soc"})),
        ),
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    compiler = UnifiedCompiler(registry)

    reqs = [
        make_req("r1", frozenset({"storage.soc"})),
        make_req("r2", frozenset({"storage.soc", "storage.charge_discharge_action"})),
    ]
    report = compiler.compile(reqs)

    assert len(adapter.scan_calls) == 1
    assert adapter.scan_calls[0] == ("r1", "r2")
    assert report.process_matches["p1"] == ("r1", "r2")
    assert report.process_matches["p2"] == ("r1",)
    assert report.unsatisfied_requirements == ()
    assert report.rejected_processes == ()


def test_main_binding_cannot_multiply_one_process_across_responsibilities():
    adapter = FakeAdapter(
        "fakeA", provides=("storage.soc",), processes=(make_process("p1", "fakeA"),)
    )
    registry = AdapterRegistry()
    registry.register(adapter)
    compiler = UnifiedCompiler(registry)
    compiler.compile([make_req("r1", frozenset({"storage.soc"}))])

    compiler.make_primary_contract("c-hvac", "p1", "keep house warm")
    with pytest.raises(PrimaryBindingConflict):
        compiler.make_primary_contract("c-dhw", "p1", "keep water hot")

    compiler.make_semantic_control_contract("s-cost", "p1", "cost shaping view")
    compiler.make_semantic_control_contract("s-comfort", "p1", "comfort view")
    assert compiler.pool.primary_contract_id("p1") == "c-hvac"
    assert set(compiler.pool.semantic_control_contract_ids("p1")) == {"s-cost", "s-comfort"}


def test_compile_takes_no_solver_or_agent_inputs():
    sig = inspect.signature(UnifiedCompiler.compile)
    param_names = set(sig.parameters)
    forbidden = {"solver", "agent", "policy", "actions", "gold_actions", "outcomes"}
    assert param_names.isdisjoint(forbidden)
    report_fields = set(__import__("unified_compiler").CompileReport.__dataclass_fields__)
    assert report_fields.isdisjoint(forbidden)
