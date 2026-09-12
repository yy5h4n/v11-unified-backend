"""Source checks must not become model actions or advance native time."""
import pytest
import json

from tools.probe_observation_sources import matches, reference
from tools.backend_acceptance_runner import _action
from unified_compiler.agent_interface import AgentActionError, make_agent_backend


def test_source_subset_requires_every_expected_field():
    assert matches({'a': {'b': 1}, 'extra': 2}, {'a': {'b': 1}})
    assert not matches({'a': {}}, {'a': {'b': 1}})
    assert not matches({'a': {'b': 2}}, {'a': {'b': 1}})


def test_long_probe_alternates_two_legal_variants(monkeypatch):
    from tools import probe_observation_sources as module
    actions = []
    class Backend:
        route = None
        closed = False
        def reset(self, seed):
            return {'time_seconds': 0, 'observation': {'x': 1}}
        def legal_actions(self):
            return {}
        def step(self, action):
            actions.append(action)
            return {'time_seconds': len(actions), 'observation': {'x': 1}}
        def close(self):
            self.closed = True
    backend = Backend()
    monkeypatch.setattr(module, 'make_agent_backend', lambda route: backend)
    monkeypatch.setattr(module, 'reference', lambda route, native: ({'x': 1}, 'test'))
    monkeypatch.setattr(module, '_action', lambda route, legal, variant: variant)
    result = module.probe('test', steps=4)
    assert result['status'] == 'passed_scoped_source_check'
    assert actions == [0, 1, 0, 1]
    assert backend.closed


def test_audit_rechecks_values_instead_of_trusting_pass_label(tmp_path, monkeypatch):
    from tools import audit_semantic_boundary as audit
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    folder = tmp_path/'generated/observation_sources_v1'
    folder.mkdir(parents=True)
    report = {'route_id': 'test', 'status': 'passed_scoped_source_check',
              'evidence': [{'passed': True, 'native_values': {'x': 1},
                            'public_observation': {'x': 2}}] * 3}
    (folder/'test.json').write_text(json.dumps(report))
    assert audit.source_check('test')['status'] == 'failed'
    assert audit.source_check('missing')['status'] == 'pending'


@pytest.mark.parametrize('route', ['wntr_residential_water', 'd3_wntr_water_competition'])
def test_native_inspection_is_read_only_and_not_a_public_action(route):
    backend = make_agent_backend(route)
    try:
        initial = backend.reset(seed=0)
        native = backend.route.backend if route == 'wntr_residential_water' else backend.route
        request = native._session_request if route == 'wntr_residential_water' else native._request
        first = request({'command': 'inspect_native'})
        expected, _ = reference(route, backend.route)
        assert matches(initial['observation'], expected)
        assert request({'command': 'inspect_native'}) == first
        with pytest.raises(AgentActionError):
            backend.step({'command': 'inspect_native'})
        assert request({'command': 'inspect_native'}) == first
        result = backend.step(_action(route, backend.legal_actions(), 0))
        second = request({'command': 'inspect_native'})
        assert second['native_time_seconds'] > first['native_time_seconds']
        expected, _ = reference(route, backend.route)
        assert matches(result['observation'], expected)
        assert request({'command': 'inspect_native'}) == second
    finally:
        backend.close()
