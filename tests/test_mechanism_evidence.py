from copy import deepcopy
import pytest
from unified_compiler.mechanism_evidence import compare_fixed_peer


def pair():
    low = {"prefix_observation": {"x": 1}, "action": {"a": 1, "b": 0, "c": 0},
           "receipt": {"time_seconds": 60, "observation": {"a_delivery": 10., "total": 10., "headroom": 5.}}}
    high = deepcopy(low)
    high["action"]["b"] = 1
    high["receipt"]["observation"].update(total=20., headroom=-5.)
    return low, high


def check(low, high):
    return compare_fixed_peer(low, high, fixed_path=["a"], varied_path=["b"],
                              delivery_path=["observation", "a_delivery"], margin_path=["observation", "headroom"])


def test_own_effect_and_total_change_do_not_prove_physical_coupling():
    result = check(*pair())
    assert result["controlled_comparison"]
    assert not result["fixed_delivery_changed"]
    assert result["shared_margin_changed"]


def test_delivery_change_is_separate_evidence():
    low, high = pair()
    high["receipt"]["observation"]["a_delivery"] = 5.
    assert check(low, high)["fixed_delivery_changed"]


@pytest.mark.parametrize("damage", ["prefix", "time", "fixed", "third"])
def test_uncontrolled_comparison_never_passes(damage):
    low, high = pair()
    high["receipt"]["observation"]["a_delivery"] = 5.
    if damage == "prefix": high["prefix_observation"]["x"] = 2
    if damage == "time": high["receipt"]["time_seconds"] = 120
    if damage == "fixed": high["action"]["a"] = 0
    if damage == "third": high["action"]["c"] = 1
    result = check(low, high)
    assert not result["fixed_delivery_changed"]
    assert not result["shared_margin_changed"]


def test_nonfinite_evidence_is_rejected():
    low, high = pair()
    high["receipt"]["observation"]["a_delivery"] = float("nan")
    with pytest.raises(ValueError): check(low, high)
