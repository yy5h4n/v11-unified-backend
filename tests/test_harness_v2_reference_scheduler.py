from datetime import datetime, timedelta, timezone

from harness_v2.reference_scheduler import Occurrence, RuleState, scheduler_step


T = datetime(2026, 1, 1, 18, tzinfo=timezone.utc)
H = T + timedelta(hours=1)


def command(target=22):
    return {"command_id": f"set{target}", "device_id": "hvac.kitchen", "capability": "thermal.control", "operation": "set", "parameters": {"target_c": target}}


def test_backend_failure_does_not_consume_fire_or_cooldown():
    rule = RuleState("r1", 1, 0, [command()], lifecycle="max_fires", max_fires=2, cooldown_seconds=300)
    result = scheduler_step(timestamp=T, horizon=H, rules={"r1": rule}, occurrences=[Occurrence("o1", "r1", T)], backend_apply=lambda _: False)
    assert rule.fire_count == 0
    assert rule.cooldown_until is None
    assert rule.active
    assert result.events[-1]["type"] == "firing_failed"


def test_expiry_precedes_same_timestamp_trigger_and_releases_once():
    rule = RuleState("r1", 1, 0, [command()], [command(18)])
    calls = []
    result = scheduler_step(timestamp=T, horizon=H, rules={"r1": rule}, occurrences=[Occurrence("z.trigger", "r1", T), Occurrence("a.expiry", "r1", T, "expiry")], backend_apply=lambda batch: calls.append(batch) or True)
    assert rule.fire_count == 0
    assert not rule.active and rule.released
    assert calls == [[command(18)]]
    assert any(event["type"] == "trigger_inactive" for event in result.events)


def test_priority_conflict_has_one_winner():
    high = RuleState("high", 10, 1, [command(22)])
    low = RuleState("low", 1, 0, [command(20)])
    calls = []
    result = scheduler_step(timestamp=T, horizon=H, rules={"high": high, "low": low}, occurrences=[Occurrence("o.high", "high", T), Occurrence("o.low", "low", T)], backend_apply=lambda batch: calls.append(batch) or True)
    assert calls == [[command(22)]]
    assert high.fire_count == 1 and low.fire_count == 0
    assert any(event["type"] == "firing_conflict_lost" and event["rule_id"] == "low" for event in result.events)


def test_cooldown_suppresses_without_increment():
    rule = RuleState("r1", 1, 0, [command()], cooldown_seconds=300, fire_count=1, cooldown_until=T + timedelta(seconds=60))
    result = scheduler_step(timestamp=T, horizon=H, rules={"r1": rule}, occurrences=[Occurrence("o1", "r1", T)], backend_apply=lambda _: True)
    assert rule.fire_count == 1
    assert result.applied_commands == []
    assert result.events == [{"type": "cooldown_suppressed", "rule_id": "r1", "occurrence_id": "o1"}]


def test_successful_once_firing_retires_then_release_conflicts_with_firing():
    rule = RuleState("r1", 1, 0, [command(22)], [command(18)], lifecycle="once")
    result = scheduler_step(timestamp=T, horizon=H, rules={"r1": rule}, occurrences=[Occurrence("o1", "r1", T)], backend_apply=lambda _: True)
    assert rule.fire_count == 1 and not rule.active and rule.released
    assert result.applied_commands == [command(22)]
    assert any(event["type"] == "release_conflict_failed" for event in result.events)


def test_horizon_wins_over_all_other_reasons_and_rules():
    rule = RuleState("r1", 1, 0, [command()])
    result = scheduler_step(timestamp=H, horizon=H, rules={"r1": rule}, occurrences=[Occurrence("o1", "r1", H)], backend_apply=lambda _: True, public_callback_reasons=["requested_wake_time"])
    assert result.terminated
    assert result.callback_reasons == ["episode_termination"]
    assert rule.fire_count == 0

