import copy

from tools.run_evidence_query_diagnostics import _evaluate


def samples(state="closed"):
    return [
        {"time_seconds": index * 60, "observation": {"devices": {"garage_door.main": {"state": state}}}}
        for index in range(11)
    ]


def test_daily_check_requires_closed_at_disclosed_deadline():
    good = samples()
    bad = samples()
    bad[5]["observation"]["devices"]["garage_door.main"]["state"] = "open"
    assert _evaluate(good)["task_success"] is True
    assert _evaluate(bad)["task_success"] is False


def test_incomplete_or_wrong_clock_cannot_pass():
    assert _evaluate(samples()[:-1])["task_success"] is None
    wrong = copy.deepcopy(samples())
    wrong[4]["time_seconds"] = 999
    assert _evaluate(wrong)["task_success"] is None


def test_later_reopening_is_outside_one_time_calibration_contract():
    value = samples()
    value[7]["observation"]["devices"]["garage_door.main"]["state"] = "open"
    assert _evaluate(value)["task_success"] is True
