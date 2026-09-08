import json

from harness_v2.core import Harness
from harness_v2.episode_validator import EpisodeValidationSuite
from harness_v2.simple_golden import GoldenHomeBackend, NoOpPolicy, RulePolicy, golden_episode


def test_core_runs_one_episode_and_keeps_advance_internal():
    class InspectingPolicy(RulePolicy):
        def __init__(self):
            super().__init__()
            self.allowed = []

        def decide(self, public_view):
            self.allowed.append(set(public_view["allowed_actions"]))
            return super().decide(public_view)

    policy = InspectingPolicy()
    artifact = Harness(GoldenHomeBackend()).run_one(golden_episode(), policy)
    assert artifact.status == "completed"
    actions = [item["action"] for item in artifact.public_trace if item["type"] == "action"]
    assert [item["kind"] for item in actions] == ["install_rule", "wait", "wait"]
    assert policy.allowed and all("advance" not in allowed for allowed in policy.allowed)
    private = [item for item in artifact.private_trace if item["type"] == "backend_state"]
    sources = [command["source"] for item in private for command in item["value"]["applied_commands"]]
    assert sources == ["rule_firing", "rule_release"]


def test_private_backend_state_is_never_in_public_trace():
    artifact = Harness(GoldenHomeBackend()).run_one(golden_episode(), RulePolicy())
    public_json = json.dumps(artifact.public_trace)
    assert '"energy"' not in public_json
    assert '"applied_commands"' not in public_json


def test_minimal_run_is_deterministic():
    first = Harness(GoldenHomeBackend()).run_one(golden_episode(), RulePolicy())
    second = Harness(GoldenHomeBackend()).run_one(golden_episode(), RulePolicy())
    assert first.trace_digest == second.trace_digest
    assert first.public_trace == second.public_trace
    assert first.private_trace == second.private_trace


def test_agent_cannot_request_advance():
    class BadPolicy:
        def decide(self, public_view):
            return {"kind": "advance"}

    artifact = Harness(GoldenHomeBackend()).run_one(golden_episode(), BadPolicy())
    assert artifact.status == "protocol_invalid"
    assert len([item for item in artifact.public_trace if item["type"] == "observation"]) == 1


def test_wait_for_stops_at_first_public_event_before_requested_deadline():
    class InstallThenLongWait(RulePolicy):
        def decide(self, public_view):
            if self._installed:
                return {"kind": "wait", "mode": "for", "duration_seconds": 2700}
            return super().decide(public_view)

    artifact = Harness(GoldenHomeBackend()).run_one(golden_episode(), InstallThenLongWait())
    observations = [item["value"] for item in artifact.public_trace if item["type"] == "observation"]
    assert observations[1]["step"] == 1
    assert observations[1]["events"] == [{"type": "rule_fired", "rule_id": "rule.evening"}]


def test_wait_until_event_stops_on_matching_event():
    class WaitForRelease(RulePolicy):
        def decide(self, public_view):
            if self._installed:
                return {"kind": "wait", "mode": "until_event", "event_filter": {"type": "rule_released"}, "timeout_seconds": 2700}
            return super().decide(public_view)

    artifact = Harness(GoldenHomeBackend()).run_one(golden_episode(), WaitForRelease())
    observations = [item["value"] for item in artifact.public_trace if item["type"] == "observation"]
    assert observations[2]["step"] == 2
    assert observations[2]["events"] == [{"type": "rule_released", "rule_id": "rule.evening"}]


def test_track_specific_action_filter_fails_closed():
    class AskingPolicy:
        def decide(self, public_view):
            assert public_view["allowed_actions"] == ["act", "wait"]
            return {"kind": "ask", "question": "What temperature do you prefer?"}

    episode = golden_episode()
    episode = type(episode)(
        episode.episode_id,
        {**episode.public_bootstrap, "allowed_action_kinds": ["act", "wait"]},
        episode.seed,
        episode.max_decisions,
    )
    artifact = Harness(GoldenHomeBackend()).run_one(episode, AskingPolicy())
    assert artifact.status == "protocol_invalid"
    assert len([item for item in artifact.public_trace if item["type"] == "observation"]) == 1


def test_backend_response_depends_on_command_parameters_not_command_presence():
    class ColdRulePolicy(RulePolicy):
        @staticmethod
        def _command(room, target):
            return RulePolicy._command(room, 16 if target == 22 else target)

    warm = Harness(GoldenHomeBackend()).run_one(golden_episode(), RulePolicy())
    cold = Harness(GoldenHomeBackend()).run_one(golden_episode(), ColdRulePolicy())
    warm_step = [item["value"] for item in warm.public_trace if item["type"] == "observation"][1]
    cold_step = [item["value"] for item in cold.public_trace if item["type"] == "observation"][1]
    assert warm_step["rooms"]["kitchen"]["temperature_c"] > cold_step["rooms"]["kitchen"]["temperature_c"]


def test_invalid_second_command_rejects_whole_batch_without_side_effects():
    class InvalidBatchThenWait:
        def __init__(self):
            self.sent = False

        def decide(self, public_view):
            if self.sent:
                return {"kind": "wait", "mode": "for", "duration_seconds": 900}
            self.sent = True
            valid = RulePolicy._command("kitchen", 22)
            invalid = {**RulePolicy._command("living", 22), "device_id": "hvac.unknown"}
            return {"kind": "act", "commands": [valid, invalid]}

    artifact = Harness(GoldenHomeBackend()).run_one(golden_episode(), InvalidBatchThenWait())
    rejected = next(item for item in artifact.public_trace if item["type"] == "action")
    result = next(item for item in artifact.private_trace if item["type"] == "action_result")
    first_advance = [item["value"] for item in artifact.public_trace if item["type"] == "observation"][1]
    assert rejected["accepted"] is False
    assert result["pre_state_digest"] == result["post_state_digest"]
    assert first_advance["devices"]["hvac.kitchen"]["target_c"] is None
    assert first_advance["rooms"]["kitchen"]["temperature_c"] == 17.5


def test_whole_home_rule_fires_and_releases_both_rooms():
    artifact = Harness(GoldenHomeBackend()).run_one(golden_episode("Keep the whole home comfortable this evening."), RulePolicy())
    commands = [command for item in artifact.private_trace if item["type"] == "backend_state" for command in item["value"]["applied_commands"]]
    assert {(command["source"], command["device_id"]) for command in commands} == {
        ("rule_firing", "hvac.kitchen"),
        ("rule_firing", "hvac.living"),
        ("rule_release", "hvac.kitchen"),
        ("rule_release", "hvac.living"),
    }


def test_episode_diagnostics_live_outside_harness_core():
    harness = Harness(GoldenHomeBackend())

    def score(run):
        observations = [item["value"] for item in run.public_trace if item["type"] == "observation"]
        return abs(22 - observations[-1]["rooms"]["kitchen"]["temperature_c"])

    evidence = EpisodeValidationSuite(harness.run_one, score).collect(
        golden_episode(),
        policies={"agent": RulePolicy, "noop": NoOpPolicy, "oracle": RulePolicy},
        query_variants={"deleted": {"text": "", "language": "en", "context": {}}},
    )
    assert set(evidence.runs) == {"agent", "noop", "oracle", "query:deleted"}
    assert evidence.scores["agent"] < evidence.scores["noop"]
    assert evidence.scores["query:deleted"] == evidence.scores["noop"]
    assert evidence.runs["query:deleted"].episode_id == evidence.runs["agent"].episode_id
