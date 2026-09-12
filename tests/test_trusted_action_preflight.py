"""Pure boundary regression tests; no native solver is mocked as evidence."""
import pytest
from unified_compiler.agent_interface import make_agent_backend, AgentActionError, AgentReceiptAdapter, AgentInterfaceError
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS


@pytest.mark.parametrize("route_id", PUBLIC_ROUTE_IDS)
def test_every_formal_route_has_explicit_preflight(route_id):
    route = make_agent_backend(route_id)
    try:
        assert route.action_validator is not None or route_id == "d1_discrete_device_fault"
    finally:
        route.close()


def test_d0_horizon_and_recoverable_invalid_actions():
    route = make_agent_backend("d0_exogenous_context", horizon_seconds=720)
    try:
        route.reset()
        before = route.observe()
        for malformed in (None, {}, "invalid", {"kind": "act", "command": {"target": [], "operation": "on"}}):
            with pytest.raises(AgentActionError):
                route.step(malformed)
            assert route.observe() == before
        action = {"kind": "act", "command": {"target": "interior_lights", "operation": "on"}}
        assert route.step(action)["time_seconds"] == 60
        for _ in range(10):
            assert not route.step(action)["done"]
        terminal = route.step(action)
        assert terminal["done"]
        assert route.observe() == terminal["observation"]
    finally:
        route.close()


@pytest.mark.parametrize("seconds", [True, 0, -60, 61, float("nan"), "120"])
def test_d0_rejects_invalid_horizons(seconds):
    with pytest.raises(AgentActionError):
        make_agent_backend("d0_exogenous_context", horizon_seconds=seconds)


def test_workflow_reset_after_close():
    route = make_agent_backend("d1_discrete_device_fault")
    try:
        route.reset()
        route.close()
        initial = route.reset()
        assert route.observe() == initial["observation"]
        assert route.step({"kind": "act", "commands": []})["time_seconds"] == 60
    finally:
        route.close()


def test_modelica_custom_horizon_reaches_native_terminal():
    route = make_agent_backend("d3_modelica_shared_heat", horizon_seconds=3660)
    try:
        route.reset()
        for i in range(61):
            receipt = route.step({"space_heating_request": 0.5, "dhw_request": 0.5})
            assert receipt["time_seconds"] == (i + 1) * 60
            assert receipt["done"] == (i == 60)
        assert route.observe() == receipt["observation"]
    finally:
        route.close()


def test_native_reset_typeerror_is_not_retried():
    class Broken:
        calls = 0
        def reset(self, seed=0):
            self.calls += 1
            raise TypeError("native seed table failed after allocation")
    native = Broken()
    with pytest.raises(AgentInterfaceError):
        AgentReceiptAdapter(native).reset()
    assert native.calls == 1


def test_sustaingym_declared_horizon_matches_pinned_native_episode():
    from unified_compiler.adapters.sustaingym_building import PINNED_EPISODE_LEN, PINNED_TIME_RESOLUTION_SECONDS
    from unified_compiler.route_registry import route_metadata
    assert route_metadata("d1_sustaingym_fault")["horizon_seconds"] == PINNED_EPISODE_LEN * PINNED_TIME_RESOLUTION_SECONDS


def test_sustaingym_native_mask_rejected_before_step(monkeypatch):
    import unified_compiler.agent_interface as interface
    from types import SimpleNamespace
    calls = []
    def native_validate(action):
        calls.append("native_mask")
        raise ValueError("zero-HVAC zone requires zero")
    episode = SimpleNamespace(
        _validate_action=lambda action: calls.append("wrapper_bounds"),
        base=SimpleNamespace(_validate_action=native_validate),
    )
    facade = AgentReceiptAdapter(SimpleNamespace(_episode=episode))
    facade._started = True
    monkeypatch.setattr(interface, "_make_agent_backend", lambda *a, **kw: facade)
    route = interface.make_agent_backend("d1_sustaingym_fault")
    with pytest.raises(AgentActionError):
        route.step([-0.1])
    assert calls == ["wrapper_bounds", "native_mask"]
    assert not route._poisoned


@pytest.mark.parametrize("action", [
    {"type": "SET_CHARGE_POWER", "kw": "1.0"},
    {"type": "SET_CHARGE_POWER", "kw": True},
    {"type": "SET_CHARGE_POWER", "kw": 1, "extra": 3},
    {"type": "WAIT", "kw": 1},
])
def test_ev_preflight_rejects_coercion_and_extra_fields(monkeypatch, action):
    import unified_compiler.agent_interface as interface
    class Probe:
        def _requested_power(self, action):
            raise AssertionError("must reject before delegated validation")
    facade = AgentReceiptAdapter(Probe())
    facade._started = True
    monkeypatch.setattr(interface, "_make_agent_backend", lambda *a, **kw: facade)
    route = interface.make_agent_backend("d1_ev2gym_fault")
    with pytest.raises(AgentActionError):
        route.step(action)
    assert not route._poisoned
