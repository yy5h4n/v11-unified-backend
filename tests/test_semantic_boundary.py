import pytest
import json
from tools.audit_semantic_boundary import checks_for


def test_old_battery_zero_observation_is_detected():
    checks = checks_for('d1_citylearn_battery_fault', {
        'observation': {'battery_soc': 0.}, 'info': {'effect': {'battery_soc': .3}}})
    assert not checks[0]['passed']


def test_missing_measurement_is_not_a_pass():
    with pytest.raises(KeyError):
        checks_for('d1_citylearn_battery_fault', {'observation': {'battery_soc': 0.}})


def test_missing_route_specific_check_is_explicitly_empty():
    assert checks_for('fds_smoke_fire', {'observation': {}}) == []


def test_future_audit_does_not_trust_summary_flags(tmp_path, monkeypatch):
    from tools import audit_semantic_boundary as audit
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    folder = tmp_path/'generated/native_future_isolation_v1'
    folder.mkdir(parents=True)
    (folder/'d0_exogenous_context.json').write_text(json.dumps({
        'route_id': 'd0_exogenous_context', 'status': 'passed_scoped_native_test',
        'same_actions': True, 'equal_public_prefixes_0_through_7': [True]*8,
        'physical_outcome_differing_steps': [5], 'runs': []}))
    assert audit.prefix_check('d0_exogenous_context')['status'] == 'mismatch'
