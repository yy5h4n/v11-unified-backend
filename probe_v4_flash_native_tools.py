#!/usr/bin/env python3
"""Read-only production-schema native-tools probe for frozen V4 Flash.

No tool is executed and no file is written. The key is read only from
AIGC_API_KEY; stdout contains one content-free JSON evidence record.
"""

from __future__ import annotations

import argparse
import errno
from functools import lru_cache
import hashlib
import json
import os
import socket
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from evaluate_harness_v2_v4_flash import DEFAULT_BASE, DEFAULT_MODEL
import evaluate_workflow_v4_flash_v4 as v4
import evaluate_workflow_v4_flash_v5 as v5
import evaluate_workflow_v4_flash_v7 as v7


PROBE_KIND = "read_only_production_native_tool_schema"
PROBE_SCHEMA_VERSION = "v7-production-tools-probe-2"
RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})


@lru_cache(maxsize=1)
def _production_probe_tools_json() -> str:
    public, _, spec, backend = v4.load_temporal_episode(v5.SAMPLE_INDICES[0], v5.RELEASE_DIR)
    observation = backend.reset(spec).public_observation
    selected, _ = v4.select_event_types_for_query(public["query"])
    tools = v7.build_native_tools(v4.device_interfaces_from_observation(observation), selected)
    return json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def production_probe_tools() -> list[dict[str, Any]]:
    return json.loads(_production_probe_tools_json())


def build_probe_request(model: str = DEFAULT_MODEL) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "Read-only capability probe. Return exactly one required native tool call; no tool will execute."},
            {"role": "user", "content": "Call submit_wait_for with kind wait, mode for, and duration_seconds 60."},
        ],
        "tools": production_probe_tools(), "tool_choice": "required", "parallel_tool_calls": False,
        "temperature": 0, "max_tokens": 64,
    }


def probe_request_bytes(model: str = DEFAULT_MODEL) -> bytes:
    return json.dumps(build_probe_request(model), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def probe_request_sha256(model: str = DEFAULT_MODEL) -> str:
    return hashlib.sha256(probe_request_bytes(model)).hexdigest()


def probe_tools_sha256() -> str:
    raw = json.dumps(production_probe_tools(), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def parse_probe_response(payload: Any) -> dict[str, Any]:
    """Return a raw-content-free verdict after v7's production parser accepts it."""
    try:
        message = payload["choices"][0]["message"]
        call_id, tool_name, action = v7.parse_native_tool_response({"message": message})
        expected = {"kind": "wait", "mode": "for", "duration_seconds": 60}
        if tool_name != v7.TOOL_NAMES["wait_for"] or action != expected:
            raise ValueError("unexpected_probe_action")
        if not call_id:
            raise ValueError("missing_probe_call_id")
        raw = json.dumps(message, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
        return {
            "verified_native_tools": True,
            "verdict": "supported",
            "reason": None,
            # Retain the concrete native result needed by the execution gate,
            # but never retain provider prose/reasoning or the provider call ID.
            "returned_tool_name": tool_name,
            "returned_arguments": action,
            "response_message_sha256": hashlib.sha256(raw).hexdigest(),
            "response_message_byte_length": len(raw),
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"verified_native_tools": False, "verdict": "unsupported_or_malformed", "reason": str(exc)}


def _safe_evidence_base_url(base_url: str) -> str:
    """Retain endpoint identity while dropping userinfo, query, and fragment secrets."""
    parsed = urlsplit(base_url.rstrip("/"))
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    return urlunsplit((parsed.scheme, hostname + port, parsed.path, "", ""))


def _base_evidence(base_url: str, model: str) -> dict[str, Any]:
    return {
        "base_url": _safe_evidence_base_url(base_url), "model": model,
        "request_sha256": probe_request_sha256(model),
        "tools_sha256": probe_tools_sha256(), "probe_kind": PROBE_KIND,
        "probe_schema_version": PROBE_SCHEMA_VERSION,
    }


def _safe_transport_reason(exc: BaseException) -> str:
    """Classify transport failures without copying exception text or request data."""
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


def _retry_delay(attempt: int) -> float:
    return min(8.0, 0.5 * (2 ** attempt))


def probe(base_url: str, model: str, *, api_key: str, timeout: int = 60,
          retries: int = 2) -> dict[str, Any]:
    if retries < 0:
        raise ValueError("retries must be non-negative")
    body = probe_request_bytes(model)
    evidence = _base_evidence(base_url, model)
    started = time.perf_counter()
    for attempt in range(retries + 1):
        try:
            request = Request(base_url.rstrip("/") + "/chat/completions", data=body, method="POST", headers={
                "Content-Type": "application/json", "Authorization": "Bearer " + api_key,
            })
            with urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                raw_payload = response.read()
            if status != 200:
                if status in RETRYABLE_HTTP_STATUS and attempt < retries:
                    time.sleep(_retry_delay(attempt))
                    continue
                evidence.update({"verified_native_tools": False, "verdict": "http_error",
                                 "reason": f"http_{status}", "http_status": status})
                return evidence
            try:
                payload = json.loads(raw_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                evidence.update({"verified_native_tools": False, "verdict": "payload_error",
                                 "reason": "invalid_json_response", "http_status": status})
                return evidence
            verdict = parse_probe_response(payload)
            evidence.update(verdict)
            evidence.update({"http_status": status,
                             "latency_ms": round((time.perf_counter() - started) * 1000, 3)})
            return evidence
        except HTTPError as exc:
            if exc.code in RETRYABLE_HTTP_STATUS and attempt < retries:
                time.sleep(_retry_delay(attempt))
                continue
            evidence.update({"verified_native_tools": False, "verdict": "http_error",
                             "reason": f"http_{exc.code}", "http_status": exc.code})
            return evidence
        except (URLError, TimeoutError) as exc:
            reason = _safe_transport_reason(exc)
            if attempt < retries:
                time.sleep(_retry_delay(attempt))
                continue
            evidence.update({"verified_native_tools": False, "verdict": "transport_error",
                             "reason": reason, "http_status": None})
            return evidence
    raise AssertionError("unreachable retry loop")


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE); parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=2,
                        help="retries after the first request (default: 2; total attempts: 3)")
    ns = parser.parse_args(args)
    key = os.environ.get("AIGC_API_KEY")
    if key:
        verdict = probe(ns.base_url, ns.model, api_key=key, timeout=ns.timeout, retries=ns.retries)
    else:
        verdict = _base_evidence(ns.base_url, ns.model)
        verdict.update({"verified_native_tools": False, "verdict": "not_run", "reason": "AIGC_API_KEY_missing",
                        "http_status": None})
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if verdict["verified_native_tools"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
