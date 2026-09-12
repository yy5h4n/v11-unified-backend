from copy import deepcopy
import pytest

from harness_v2.core import EpisodeSpec
from unified_compiler.agent_interface import make_agent_backend, AgentActionError, AgentInterfaceError
from unified_compiler.adapters.d1_discrete_device_fault import DiscreteFaultSchedule, DiscreteFaultWindow

OPEN={'kind':'act','commands':[{'device_id':'garage_door.main','capability':'garage.door','operation':'open','parameters':{}}]}
WAIT={'kind':'act','commands':[]}


def backend(mode='jammed'):
    return make_agent_backend('d1_discrete_device_fault',
        schedule=DiscreteFaultSchedule((DiscreteFaultWindow('garage_door.main',0,3,mode),)),
        episode_spec=EpisodeSpec('trusted-boundary',{'horizon_seconds':600},17))


@pytest.mark.parametrize('mode',['jammed','stuck','offline'])
def test_device_failure_advances_time_and_recovers_without_reset(mode):
    r=backend(mode)
    try:
        r.reset(seed=17)
        for i in range(3):
            receipt=r.step(deepcopy(OPEN))
            assert receipt['time_seconds']==(i+1)*60
            assert receipt['delta_t_seconds']==60
            assert not receipt['info']['accepted']
            assert receipt['info']['execution_status']=='device_rejected'
            assert receipt['info']['error_code']=='FAULT_DEVICE_'+mode.upper()
            assert not receipt['done']
        recovered=r.step(deepcopy(OPEN))
        assert recovered['info']['accepted']
        assert recovered['observation']['devices']['garage_door.main']['state']=='open'
    finally:r.close()


@pytest.mark.parametrize('bad',[
    None, {'kind':'act','commands':[None]},
    {'kind':'act','commands':[{'device_id':'missing','capability':'garage.door','operation':'open','parameters':{}}]},
    {'kind':'wait','mode':'until','timestamp':'not-a-time'},
    {'kind':'wait','mode':'for','duration_seconds':float('nan')},
    {'kind':'install_rule','rule':{}},
    {'kind':'cancel_rule','rule_id':'missing'},
])
def test_protocol_error_does_not_change_state_time_or_poison_episode(bad):
    r=backend()
    try:
        r.reset();before=r.observe()
        with pytest.raises(AgentActionError):r.step(bad)
        assert r.observe()==before
        assert r.step(deepcopy(WAIT))['time_seconds']==60
    finally:r.close()


def test_rejected_batch_does_not_partially_execute_other_device():
    r=backend()
    try:
        initial=r.reset()['observation'];action=deepcopy(OPEN)
        action['commands'].insert(0,{'device_id':'front_door_lock.main','capability':'lock.control','operation':'unlock','parameters':{}})
        result=r.step(action)
        assert not result['info']['accepted']
        assert result['observation']['devices']['front_door_lock.main']['state']==initial['devices']['front_door_lock.main']['state']
    finally:r.close()


def test_unknown_native_failure_still_stops_episode(monkeypatch):
    r=backend()
    try:
        r.reset()
        def crash(action):raise RuntimeError('native failure')
        monkeypatch.setattr(r.route,'advance',crash)
        with pytest.raises(AgentInterfaceError):r.step(deepcopy(WAIT))
        with pytest.raises(AgentInterfaceError):r.step(deepcopy(WAIT))
    finally:r.close()


def test_future_rule_can_be_installed_during_fault_and_fires_after_recovery():
    r=backend()
    try:
        r.reset()
        installed=r.step({'kind':'install_rule','rule':{'rule_id':'after_recovery','fire_at_step':4,'commands':deepcopy(OPEN['commands'])}})
        assert installed['info']['accepted']
        for _ in range(3):
            last=r.step(deepcopy(WAIT))
        assert last['observation']['devices']['garage_door.main']['state']=='open'
    finally:r.close()


def test_future_rule_checks_fault_at_firing_time():
    r=backend()
    try:
        r.reset()
        r.step({'kind':'install_rule','rule':{'rule_id':'during_fault','fire_at_step':2,'commands':deepcopy(OPEN['commands'])}})
        receipt=r.step(deepcopy(WAIT))
        assert receipt['observation']['devices']['garage_door.main']['state']=='closed'
    finally:r.close()


def test_reset_recovers_after_native_failure(monkeypatch):
    r=backend()
    try:
        r.reset()
        native_advance=r.route.advance
        def crash(action):raise RuntimeError('native failure')
        monkeypatch.setattr(r.route,'advance',crash)
        with pytest.raises(AgentInterfaceError):r.step(deepcopy(WAIT))
        monkeypatch.setattr(r.route,'advance',native_advance)
        r.reset()
        assert r.step(deepcopy(WAIT))['time_seconds']==60
    finally:r.close()
