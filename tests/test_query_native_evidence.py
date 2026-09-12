import copy
import json
from pathlib import Path
import pytest
from query_construction.native_evidence import summarize_native


def evidence():
    root = Path(__file__).resolve().parents[1] / 'generated/query_construction_v1'
    native = json.loads((root / 'crowdre_arrival_native_v2/result.json').read_text())
    diagnostic = json.loads((root / 'crowdre_arrival_policies_v5.json').read_text())
    return native, diagnostic


def test_real_saved_run_is_calibration_not_claim_admission():
    native, diagnostic = evidence()
    result = summarize_native(native, native['public_contract'], native['user_responsibility'], diagnostic)
    assert result['evaluation']['task_success'] is True
    assert result['classification'] == 'calibration_only_constant_baseline_passes'
    assert not result['admitted']


def test_no_baseline_is_not_claim_success():
    native, _ = evidence()
    assert summarize_native(native, native['public_contract'], native['user_responsibility'])['classification'] == 'claim_validation_pending'


def test_rejects_wrong_query_and_fabricated_score():
    native, diagnostic = evidence()
    with pytest.raises(ValueError, match='another query'):
        summarize_native(native, native['public_contract'], 'different', diagnostic)
    native['contract_evaluation']['task_success'] = False
    with pytest.raises(ValueError, match='saved native score'):
        summarize_native(native, native['public_contract'], native['user_responsibility'], diagnostic)


def test_rejects_wrong_diagnostic_initial_state():
    native, diagnostic = evidence()
    diagnostic = copy.deepcopy(diagnostic)
    diagnostic['runs'][0]['samples'][0]['observation']['devices']['interior_lights'] = 'on'
    with pytest.raises(ValueError, match='initial state mismatch'):
        summarize_native(native, native['public_contract'], native['user_responsibility'], diagnostic)
