import json
import pytest
from tools import check_backend_delivery as delivery


@pytest.mark.parametrize('damage', [None, 'missing', 'wrong_route', 'interaction', 'fixed_step', 'source', 'prefix', 'old_prefix', 'invariant'])
def test_delivery_rejects_missing_or_failed_evidence(tmp_path, monkeypatch, damage):
    route = 'd0_test'
    monkeypatch.setattr(delivery, 'ROOT', tmp_path)
    monkeypatch.setattr(delivery, 'PUBLIC_ROUTE_IDS', (route,))
    monkeypatch.setattr(delivery, 'SESSIONS', {route: 'test'})
    path = tmp_path / 'generated/test'
    path.mkdir(parents=True)
    if damage != 'missing':
        (path / (route + '.json')).write_text(json.dumps({
            'route_id': 'other' if damage == 'wrong_route' else route,
            'transitions': [{}], 'attempted_calls': 1,
        }))
    monkeypatch.setattr(delivery, 'verify', lambda _: {
        'interaction_verified': damage != 'interaction', 'autonomous_wait': damage != 'fixed_step'})
    monkeypatch.setattr(delivery, 'source_check', lambda _: {
        'status': 'failed' if damage == 'source' else 'passed_scoped_source_check'})
    monkeypatch.setattr(delivery, 'prefix_check', lambda _: {
        'status': 'mismatch' if damage == 'prefix' else 'passed',
        'source': 'old_prefix.json' if damage == 'old_prefix' else 'generated/native_future_isolation_v1/test.json'})
    monkeypatch.setattr(delivery, 'checks_for', lambda *_: [{'passed': damage != 'invariant'}])
    result = delivery.build()
    assert result['passed'] is (damage is None)
    assert result['benchmark_ready'] is False
    assert result['task_success_rate'] is None
    assert result['routes'][0]['task_success'] is None


def test_inventory_cannot_silently_drop_a_route(monkeypatch):
    monkeypatch.setattr(delivery, 'PUBLIC_ROUTE_IDS', ('a', 'b'))
    monkeypatch.setattr(delivery, 'SESSIONS', {'a': 'test'})
    monkeypatch.setattr(delivery, 'check_route', lambda route: {'route_id': route, 'passed': True})
    assert delivery.build()['passed'] is False


@pytest.mark.parametrize('damage', [None, 'clock', 'batch', 'missing_check', 'new_check', 'two_calls', 'paid', 'wrong_kind'])
def test_native_gate_checks_trajectory_not_only_pass_label(tmp_path, monkeypatch, damage):
    report = {'route_id': 'test', 'model': 'SCRIPTED_WAIT_CONFORMANCE_NOT_LLM',
              'external_api_calls': 0, 'attempted_calls': 1, 'decisions': [{}],
              'wait_conformance_passed': True}
    if damage == 'two_calls': report['attempted_calls'] = 2
    if damage == 'paid': report['external_api_calls'] = 1
    if damage == 'wrong_kind': report['model'] = 'a-real-model'
    (tmp_path / 'test.json').write_text(json.dumps(report))
    checks = dict.fromkeys(('terminal_status', 'closed', 'unchanged_protocol',
        'decision_batches_and_microtrajectory', 'clock', 'full_horizon'), True)
    checks.update(real_provider_record=False, usage_available=False)
    if damage == 'clock': checks['clock'] = False
    if damage == 'batch': checks['decision_batches_and_microtrajectory'] = False
    if damage == 'missing_check': del checks['clock']
    if damage == 'new_check': checks['unknown_check'] = True
    monkeypatch.setattr(delivery, 'verify', lambda _: {
        'checks': checks, 'autonomous_wait': True, 'native_steps': 12})
    assert delivery.check_scripted_native('test', tmp_path)['passed'] is (damage is None)
