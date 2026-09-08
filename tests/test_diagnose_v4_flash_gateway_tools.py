from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

import diagnose_v4_flash_gateway_tools as diagnostic


class FakeHTTPResponse:
    def __init__(self, payload: object, status: int = 200):
        self.status = status
        self.raw = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.raw


def text_response() -> FakeHTTPResponse:
    return FakeHTTPResponse({"choices": [{"message": {"role": "assistant", "content": "OK"}}]})


def native_response(name: str, arguments: dict, call_id: str = "call-safe") -> FakeHTTPResponse:
    return FakeHTTPResponse({"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments),
        }}],
    }}]})


def test_requests_increase_from_text_to_minimal_to_production() -> None:
    requests = [diagnostic.build_stage_request(stage, "model") for stage in diagnostic.STAGES]
    sizes = [len(diagnostic._canonical_bytes(request)) for request in requests]
    assert requests[0].get("tools") is None
    assert len(requests[1]["tools"]) == 1
    assert len(requests[2]["tools"]) > 1
    assert sizes[0] < sizes[1] < sizes[2]


def test_minimal_stage_avoids_production_only_compatibility_features() -> None:
    request = diagnostic.minimal_tool_request("model")
    assert request["tool_choice"] == {"type": "function", "function": {"name": "diagnostic_ping"}}
    assert "parallel_tool_calls" not in request
    assert "strict" not in request["tools"][0]["function"]


def test_all_stages_pass_with_fake_urlopen_and_no_response_content_in_report() -> None:
    responses = iter([
        text_response(),
        native_response("diagnostic_ping", {}),
        native_response("submit_wait_for", {"kind": "wait", "mode": "for", "duration_seconds": 60}),
    ])
    seen_authorizations = []

    def opener(request, timeout):
        seen_authorizations.append(request.get_header("Authorization"))
        return next(responses)

    report = diagnostic.diagnose(
        "https://url-user:url-secret@example.test/v1?token=url-token",
        "model", api_key="top-secret", opener=opener, sleeper=lambda _: None,
    )
    assert [row["verdict"] for row in report["stages"]] == ["passed", "passed", "passed"]
    assert [row["expected_native_call_received"] for row in report["stages"]] == [None, True, True]
    encoded = json.dumps(report)
    assert "top-secret" not in encoded and "url-secret" not in encoded and "url-token" not in encoded
    assert "OK" not in encoded and "call-safe" not in encoded
    assert report["base_url"] == "https://example.test/v1"
    assert seen_authorizations == ["Bearer top-secret"] * 3


def test_text_transport_failure_skips_later_stages_in_all_mode() -> None:
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise URLError(ConnectionResetError())

    report = diagnostic.diagnose(
        "https://example.test/v1", "model", api_key="secret", retries=0,
        opener=opener, sleeper=lambda _: None,
    )
    assert len(calls) == 1
    assert report["stages"][0]["verdict"] == "transport_error"
    assert report["stages"][0]["reason"] == "connection_reset"
    assert [row["verdict"] for row in report["stages"][1:]] == ["skipped", "skipped"]
    assert all(row["reason"] == "baseline_text_stage_failed" for row in report["stages"][1:])


@pytest.mark.parametrize("stage", diagnostic.STAGES)
def test_each_stage_can_run_independently(stage: str) -> None:
    if stage == "text":
        response = text_response()
    elif stage == "minimal":
        response = native_response("diagnostic_ping", {})
    else:
        response = native_response("submit_wait_for", {"kind": "wait", "mode": "for", "duration_seconds": 60})
    report = diagnostic.diagnose(
        "https://example.test/v1", "model", api_key="secret", stage=stage,
        opener=lambda request, timeout: response, sleeper=lambda _: None,
    )
    assert len(report["stages"]) == 1
    assert report["stages"][0]["stage"] == stage
    assert report["stages"][0]["verdict"] == "passed"


def test_retry_is_identical_and_safe() -> None:
    requests = []

    def opener(request, timeout):
        requests.append(request.data)
        if len(requests) == 1:
            raise URLError(ConnectionResetError())
        return native_response("diagnostic_ping", {})

    report = diagnostic.diagnose(
        "https://example.test/v1", "model", api_key="secret", stage="minimal",
        retries=2, opener=opener, sleeper=lambda _: None,
    )
    assert report["stages"][0]["verdict"] == "passed"
    assert len(requests) == 2 and requests[0] == requests[1]


def test_http_error_does_not_copy_response_body() -> None:
    body = io.BytesIO(b"provider-secret-body")

    def opener(request, timeout):
        raise HTTPError(request.full_url, 400, "unsafe provider prose", {}, body)

    report = diagnostic.diagnose(
        "https://example.test/v1", "model", api_key="secret", stage="text",
        opener=opener, sleeper=lambda _: None,
    )
    encoded = json.dumps(report)
    assert report["stages"][0]["reason"] == "http_400"
    assert "provider-secret-body" not in encoded and "unsafe provider prose" not in encoded


def test_invalid_json_and_missing_native_call_are_classified_without_body() -> None:
    invalid = diagnostic.diagnose(
        "https://example.test/v1", "model", api_key="secret", stage="text",
        opener=lambda request, timeout: FakeHTTPResponse(b"secret-not-json"), sleeper=lambda _: None,
    )
    assert invalid["stages"][0]["reason"] == "invalid_json_response"
    assert "secret-not-json" not in json.dumps(invalid)

    missing = diagnostic.diagnose(
        "https://example.test/v1", "model", api_key="secret", stage="minimal",
        opener=lambda request, timeout: text_response(), sleeper=lambda _: None,
    )
    assert missing["stages"][0]["verdict"] == "native_call_error"
    assert missing["stages"][0]["expected_native_call_received"] is False


def test_metrics_are_content_free_and_exact() -> None:
    request = diagnostic.production_tool_request("model")
    record = diagnostic._base_stage_record(diagnostic.StageSpec("production", request))
    assert record["request_bytes"] == len(diagnostic._canonical_bytes(request))
    assert record["tools_bytes"] == len(diagnostic._canonical_bytes(request["tools"]))
    assert record["tools_count"] == len(request["tools"])
    assert set(record) == {
        "stage", "request_bytes", "tools_bytes", "tools_count", "http_status",
        "latency_ms", "verdict", "reason", "expected_native_call_received",
    }


def test_static_flags_are_observations_not_claimed_causes() -> None:
    flags = diagnostic.static_compatibility_flags()
    assert {row["feature"] for row in flags} >= {
        "strict_true", "parallel_tool_calls_false", "tool_choice_required_string",
        "nested_anyOf_and_nullable_types", "large_schema_payload",
    }
    assert all(row["status"] in {"unverified", "measured_not_concluded"} for row in flags)


def test_main_without_key_never_touches_network(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("AIGC_API_KEY", raising=False)
    monkeypatch.setattr(diagnostic, "urlopen", lambda *args, **kwargs: pytest.fail("network forbidden"))
    assert diagnostic.main(["--stage", "minimal"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["stages"][0]["reason"] == "AIGC_API_KEY_missing"
