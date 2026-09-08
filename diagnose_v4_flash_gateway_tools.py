#!/usr/bin/env python3
"""Safe staged diagnostics for the V4 Flash OpenAI-compatible gateway.

The diagnostic never executes a returned tool and never prints credentials,
response bodies, model prose, URL userinfo, query strings, or fragments.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from evaluate_harness_v2_v4_flash import DEFAULT_BASE, DEFAULT_MODEL
import probe_v4_flash_native_tools as production_probe
import evaluate_workflow_v4_flash_v7 as v7


DIAGNOSTIC_KIND = "v7_gateway_native_tools_staged_diagnostic"
DIAGNOSTIC_SCHEMA_VERSION = "v1"
RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
STAGES = ("text", "minimal", "production")


def safe_base_url(base_url: str) -> str:
    """Drop URL userinfo, query, and fragment while retaining endpoint identity."""
    parsed = urlsplit(base_url.rstrip("/"))
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    return urlunsplit((parsed.scheme, hostname + port, parsed.path, "", ""))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def text_request(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word OK."}],
        "temperature": 0,
        "max_tokens": 8,
    }


def minimal_tool_request(model: str) -> dict[str, Any]:
    tool_name = "diagnostic_ping"
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Call diagnostic_ping once."}],
        "tools": [{
            "type": "function",
            "function": {
                "name": tool_name,
                "description": "Read-only gateway capability diagnostic. No tool is executed.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
        "temperature": 0,
        "max_tokens": 32,
    }


def production_tool_request(model: str) -> dict[str, Any]:
    return production_probe.build_probe_request(model)


def build_stage_request(stage: str, model: str) -> dict[str, Any]:
    if stage == "text":
        return text_request(model)
    if stage == "minimal":
        return minimal_tool_request(model)
    if stage == "production":
        return production_tool_request(model)
    raise ValueError(f"unknown stage: {stage}")


def _tool_metrics(request_payload: dict[str, Any]) -> tuple[int, int]:
    tools = request_payload.get("tools")
    if not isinstance(tools, list):
        return 0, 0
    return len(_canonical_bytes(tools)), len(tools)


def _transport_reason(exc: BaseException) -> str:
    """Classify an exception without copying provider- or credential-bearing text."""
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(reason, socket.gaierror):
        return "dns_error"
    if isinstance(reason, ssl.SSLError):
        return "tls_error"
    if isinstance(reason, ConnectionResetError) or getattr(reason, "errno", None) == errno.ECONNRESET:
        return "connection_reset"
    if isinstance(reason, ConnectionRefusedError) or getattr(reason, "errno", None) == errno.ECONNREFUSED:
        return "connection_refused"
    if getattr(reason, "errno", None) in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
        return "network_unreachable"
    return "url_error"


def _expected_native_call(stage: str, payload: Any) -> bool | None:
    if stage == "text":
        return None
    try:
        message = payload["choices"][0]["message"]
        if stage == "minimal":
            calls = message.get("tool_calls")
            return bool(
                isinstance(calls, list)
                and len(calls) == 1
                and isinstance(calls[0], dict)
                and calls[0].get("type") == "function"
                and isinstance(calls[0].get("function"), dict)
                and calls[0]["function"].get("name") == "diagnostic_ping"
            )
        call_id, tool_name, action = v7.parse_native_tool_response({"message": message})
        return bool(call_id) and tool_name == v7.TOOL_NAMES["wait_for"] and action == {
            "kind": "wait", "mode": "for", "duration_seconds": 60,
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _text_response_valid(payload: Any) -> bool:
    try:
        message = payload["choices"][0]["message"]
        return isinstance(message, dict) and isinstance(message.get("content"), str)
    except (KeyError, IndexError, TypeError):
        return False


def _retry_delay(attempt: int) -> float:
    return min(8.0, 0.5 * (2 ** attempt))


@dataclass(frozen=True)
class StageSpec:
    name: str
    request_payload: dict[str, Any]


def _base_stage_record(spec: StageSpec) -> dict[str, Any]:
    body = _canonical_bytes(spec.request_payload)
    tools_bytes, tools_count = _tool_metrics(spec.request_payload)
    return {
        "stage": spec.name,
        "request_bytes": len(body),
        "tools_bytes": tools_bytes,
        "tools_count": tools_count,
        "http_status": None,
        "latency_ms": None,
        "verdict": "not_run",
        "reason": None,
        "expected_native_call_received": None,
    }


def run_stage(base_url: str, spec: StageSpec, *, api_key: str, timeout: int = 60,
              retries: int = 2, opener: Callable[..., Any] | None = None,
              sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Run one diagnostic stage and return content-free evidence."""
    if retries < 0:
        raise ValueError("retries must be non-negative")
    opener = opener or urlopen
    record = _base_stage_record(spec)
    body = _canonical_bytes(spec.request_payload)
    endpoint = base_url.rstrip("/") + "/chat/completions"
    started = time.perf_counter()

    for attempt in range(retries + 1):
        try:
            request = Request(endpoint, data=body, method="POST", headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
            })
            with opener(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                raw_payload = response.read()
            record["http_status"] = status
            if status != 200:
                if status in RETRYABLE_HTTP_STATUS and attempt < retries:
                    sleeper(_retry_delay(attempt))
                    continue
                record.update({"verdict": "http_error", "reason": f"http_{status}"})
                break
            try:
                payload = json.loads(raw_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                record.update({"verdict": "payload_error", "reason": "invalid_json_response"})
                break

            expected = _expected_native_call(spec.name, payload)
            record["expected_native_call_received"] = expected
            if spec.name == "text":
                if _text_response_valid(payload):
                    record.update({"verdict": "passed", "reason": None})
                else:
                    record.update({"verdict": "payload_error", "reason": "missing_text_message"})
            elif expected:
                record.update({"verdict": "passed", "reason": None})
            else:
                record.update({"verdict": "native_call_error", "reason": "expected_native_call_missing_or_malformed"})
            break
        except HTTPError as exc:
            record["http_status"] = int(exc.code)
            if exc.code in RETRYABLE_HTTP_STATUS and attempt < retries:
                sleeper(_retry_delay(attempt))
                continue
            record.update({"verdict": "http_error", "reason": f"http_{exc.code}"})
            break
        except (URLError, TimeoutError, socket.timeout, ConnectionError, ssl.SSLError) as exc:
            reason = _transport_reason(exc)
            if attempt < retries:
                sleeper(_retry_delay(attempt))
                continue
            record.update({"verdict": "transport_error", "reason": reason})
            break
    record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return record


def _skipped_stage(stage: str, model: str, reason: str) -> dict[str, Any]:
    record = _base_stage_record(StageSpec(stage, build_stage_request(stage, model)))
    record.update({"verdict": "skipped", "reason": reason})
    return record


def static_compatibility_flags() -> list[dict[str, str]]:
    """Features present in production but absent/different in the minimal tool stage."""
    return [
        {"feature": "strict_true", "location": "production_tool_definitions", "status": "unverified"},
        {"feature": "parallel_tool_calls_false", "location": "production_request", "status": "unverified"},
        {"feature": "tool_choice_required_string", "location": "production_request", "status": "unverified"},
        {"feature": "nested_anyOf_and_nullable_types", "location": "production_tool_schemas", "status": "unverified"},
        {"feature": "large_schema_payload", "location": "production_tools", "status": "measured_not_concluded"},
    ]


def diagnose(base_url: str, model: str, *, api_key: str, stage: str = "all",
             timeout: int = 60, retries: int = 2,
             opener: Callable[..., Any] | None = None,
             sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    if stage not in {"all", *STAGES}:
        raise ValueError("invalid stage")
    selected = STAGES if stage == "all" else (stage,)
    results: list[dict[str, Any]] = []
    for name in selected:
        if stage == "all" and name != "text" and results[0]["verdict"] != "passed":
            results.append(_skipped_stage(name, model, "baseline_text_stage_failed"))
            continue
        spec = StageSpec(name, build_stage_request(name, model))
        results.append(run_stage(base_url, spec, api_key=api_key, timeout=timeout,
                                 retries=retries, opener=opener, sleeper=sleeper))
    return {
        "diagnostic_kind": DIAGNOSTIC_KIND,
        "diagnostic_schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "base_url": safe_base_url(base_url),
        "model": model,
        "stages": results,
        "static_compatibility_flags": static_compatibility_flags(),
    }


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--stage", choices=("all", *STAGES), default="all")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=2,
                        help="retries after the first request (default: 2; total attempts: 3)")
    ns = parser.parse_args(args)
    key = os.environ.get("AIGC_API_KEY")
    if key:
        report = diagnose(ns.base_url, ns.model, api_key=key, stage=ns.stage,
                          timeout=ns.timeout, retries=ns.retries)
    else:
        report = {
            "diagnostic_kind": DIAGNOSTIC_KIND,
            "diagnostic_schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "base_url": safe_base_url(ns.base_url),
            "model": ns.model,
            "stages": [_skipped_stage(name, ns.model, "AIGC_API_KEY_missing")
                       for name in (STAGES if ns.stage == "all" else (ns.stage,))],
            "static_compatibility_flags": static_compatibility_flags(),
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if all(row["verdict"] == "passed" for row in report["stages"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
