"""Build and verify trusted Harness V2 reset, policy, and manifest evidence."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import rfc8785

from .semantic_validator import ConformanceError
from .trust_registry import TrustedRuntime, sha256_value


ENVIRONMENT_VIEW_FIELDS = ("rooms", "virtual_clock", "inventory", "observations", "events")


def serialization(value: Any) -> dict[str, str]:
    raw = rfc8785.dumps(value)
    return {
        "algorithm": "RFC8785_JCS_UTF8",
        "canonical_utf8_base64": base64.b64encode(raw).decode(),
        "digest": hashlib.sha256(raw).hexdigest(),
    }


def decode_serialization(record: dict[str, str], label: str) -> Any:
    try:
        raw = base64.b64decode(record["canonical_utf8_base64"], validate=True)
        value = json.loads(raw)
    except Exception as exc:
        raise ConformanceError(f"invalid {label} serialization") from exc
    if rfc8785.dumps(value) != raw or hashlib.sha256(raw).hexdigest() != record["digest"]:
        raise ConformanceError(f"noncanonical or forged {label} serialization")
    return value


def public_environment_projection(agent_view: dict[str, Any]) -> dict[str, Any]:
    return {field: agent_view[field] for field in ENVIRONMENT_VIEW_FIELDS}


def build_reset_receipt(
    runtime: TrustedRuntime,
    native_snapshot: dict[str, Any],
    *,
    seed_digest: str,
    exogenous_realization_digest: str,
) -> dict[str, Any]:
    expected_native = runtime.reconstruct_reset(seed_digest, exogenous_realization_digest)
    if native_snapshot != expected_native:
        raise ValueError("native snapshot was not produced by the trusted deterministic reset")
    projection = runtime.project_reset(native_snapshot)
    body = {
        "runtime_id": runtime.runtime_id,
        "runtime_version": runtime.version,
        "registry_entry_digest": runtime.registry_entry_digest,
        "runtime_package_digest": runtime.package_digest,
        "backend_package_digest": runtime.backend_package_digest,
        "adapter_package_digest": runtime.adapter_package_digest,
        "seed_digest": seed_digest,
        "exogenous_realization_digest": exogenous_realization_digest,
        "native_snapshot": serialization(native_snapshot),
        "public_projection": serialization(projection),
    }
    return {**body, "receipt_digest": sha256_value(body)}


def verify_reset_receipt(
    receipt: dict[str, Any],
    agent_view: dict[str, Any],
    runtime: TrustedRuntime,
    *,
    expected_seed_digest: str,
    expected_exogenous_realization_digest: str,
) -> dict[str, Any]:
    bindings = {
        "runtime_id": runtime.runtime_id,
        "runtime_version": runtime.version,
        "registry_entry_digest": runtime.registry_entry_digest,
        "runtime_package_digest": runtime.package_digest,
        "backend_package_digest": runtime.backend_package_digest,
        "adapter_package_digest": runtime.adapter_package_digest,
        "seed_digest": expected_seed_digest,
        "exogenous_realization_digest": expected_exogenous_realization_digest,
    }
    for field, expected in bindings.items():
        if receipt[field] != expected:
            raise ConformanceError(f"trusted reset binding mismatch: {field}")
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if receipt["receipt_digest"] != sha256_value(body):
        raise ConformanceError("trusted reset receipt digest mismatch")
    native = decode_serialization(receipt["native_snapshot"], "native reset snapshot")
    expected_native = runtime.reconstruct_reset(expected_seed_digest, expected_exogenous_realization_digest)
    if native != expected_native:
        raise ConformanceError("trusted deterministic reset reconstruction mismatch")
    projected = decode_serialization(receipt["public_projection"], "public reset projection")
    if runtime.project_reset(native) != projected:
        raise ConformanceError("trusted adapter projection does not reproduce reset receipt")
    if projected != public_environment_projection(agent_view):
        raise ConformanceError("bootstrap public environment differs from trusted reset projection")
    return native


def manifest_body(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key not in {"manifest_digest", "serialization"}}


def seal_manifest(body: dict[str, Any]) -> dict[str, Any]:
    sealed = serialization(body)
    return {**body, "manifest_digest": sealed["digest"], "serialization": sealed}


def verify_manifest(manifest: dict[str, Any], runtime: TrustedRuntime) -> None:
    body = manifest_body(manifest)
    expected_serialization = serialization(body)
    if manifest["serialization"] != expected_serialization or manifest["manifest_digest"] != expected_serialization["digest"]:
        raise ConformanceError("evaluator manifest serialization or digest mismatch")
    expected = runtime.evaluator_manifest_digests.get(manifest["manifest_id"])
    if expected != manifest["manifest_digest"]:
        raise ConformanceError("evaluator manifest is absent from trusted registry")
    if manifest["implementation_hash"] != runtime.evaluator_package_digest:
        raise ConformanceError("evaluator implementation is not the trusted package")


def build_policy_execution_receipt(
    runtime: TrustedRuntime,
    *,
    policy_id: str,
    policy_config_digest: str,
    agent_view: dict[str, Any],
    callbacks: list[dict[str, Any]],
    terminal_choices: list[dict[str, Any]],
) -> dict[str, Any]:
    config = runtime.policy_configs.get(policy_id)
    if config is None or policy_config_digest != sha256_value(config):
        raise ValueError("policy config is absent from the trusted registry")
    body = {
        "runtime_id": runtime.runtime_id,
        "registry_entry_digest": runtime.registry_entry_digest,
        "policy_id": policy_id,
        "policy_package_digest": runtime.policy_package_digests[policy_id],
        "policy_config_digest": policy_config_digest,
        "agent_view_digest": sha256_value(agent_view),
        "callback_stream_digest": sha256_value(callbacks),
        "terminal_choices_digest": sha256_value(terminal_choices),
    }
    return {**body, "receipt_digest": sha256_value(body)}


def verify_policy_execution_receipt(
    receipt: dict[str, Any],
    runtime: TrustedRuntime,
    *,
    agent_view: dict[str, Any],
    callbacks: list[dict[str, Any]],
    terminal_choices: list[dict[str, Any]],
) -> None:
    policy_id = receipt["policy_id"]
    expected_package = runtime.policy_package_digests.get(policy_id)
    if expected_package is None or receipt["policy_package_digest"] != expected_package:
        raise ConformanceError("policy package is absent from trusted registry")
    config = runtime.policy_configs.get(policy_id)
    if config is None or receipt["policy_config_digest"] != sha256_value(config):
        raise ConformanceError("policy config is absent from the trusted registry")
    expected_choices = runtime.execute_policy(agent_view, callbacks, policy_id, config)
    if terminal_choices != expected_choices:
        raise ConformanceError("sealed session choices differ from trusted policy execution")
    expected = build_policy_execution_receipt(
        runtime,
        policy_id=policy_id,
        policy_config_digest=receipt["policy_config_digest"],
        agent_view=agent_view,
        callbacks=callbacks,
        terminal_choices=terminal_choices,
    )
    if receipt != expected:
        raise ConformanceError("policy execution receipt is not recomputable")
