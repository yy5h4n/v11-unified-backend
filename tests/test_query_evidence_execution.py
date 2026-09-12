import copy
import json
from pathlib import Path

import pytest

from query_construction.evidence_execution import verify_execution_file


ROOT = Path(__file__).resolve().parents[1]
THERMAL = ROOT / "generated/query_construction_v2/core_transfer_thermal_native_v1.json"
GARAGE = ROOT / "generated/query_construction_v2/validation_garage_daily_check_v1.json"


def test_current_code_recomputes_core_policy_contrast():
    result = verify_execution_file(THERMAL)
    assert result["core_contrast_passed"] is True
    assert result["policy_outcomes"] == {
        "idle": False,
        "one_shot": False,
        "fixed_0_3": False,
        "feedback": True,
    }


def test_current_code_recomputes_calibration_contrast():
    result = verify_execution_file(GARAGE)
    assert result["calibration_contrast_passed"] is True
    assert result["policy_outcomes"] == {"idle": False, "scheduled_close": True}


def test_changed_thermal_summary_cannot_certify_evidence(tmp_path):
    value = json.loads(THERMAL.read_text())
    value["runs"][0]["evaluation"]["task_success"] = True
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="saved score"):
        verify_execution_file(path)


def test_missing_native_sample_cannot_certify_evidence(tmp_path):
    value = json.loads(GARAGE.read_text())
    value["runs"][1]["samples"].pop(5)
    path = tmp_path / "truncated.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="mis-timed samples"):
        verify_execution_file(path)


def test_action_labels_alone_cannot_fake_feedback_contrast(tmp_path):
    value = json.loads(THERMAL.read_text())
    idle = next(run for run in value["runs"] if run["policy"] == "idle")
    feedback = next(run for run in value["runs"] if run["policy"] == "feedback")
    feedback["actions"] = copy.deepcopy(idle["actions"])
    path = tmp_path / "fake-feedback.json"
    path.write_text(json.dumps(value))
    # The saved trajectory is authoritative for task outcome, but action/sample
    # alignment must make a forged action label observable to the verifier.
    with pytest.raises(ValueError, match="declared diagnostic action"):
        verify_execution_file(path)
