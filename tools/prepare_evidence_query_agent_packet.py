#!/usr/bin/env python3
"""Freeze the exact model-visible initial packet for one accepted V2 item."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from query_construction.evidence_batch import load_and_validate
from query_construction.native_evidence import canonical
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.native_action_conversation import build_native_conversation


def build_packet(batch_path: Path, item_id: str, diagnostic_path: Path) -> dict:
    batch, _ = load_and_validate(batch_path)
    matches = [item for item in batch["items"] if item["id"] == item_id]
    if len(matches) != 1 or not matches[0]["decision"].startswith("accepted"):
        raise ValueError("one accepted batch item is required")
    item = matches[0]
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    if diagnostic.get("item_id") != item_id or diagnostic.get("route_id") != item["scenario"]["route_id"]:
        raise ValueError("diagnostic does not belong to accepted item")
    public = diagnostic.get("public_contract")
    if not isinstance(public, dict):
        raise ValueError("agent packet currently requires a standard public contract")
    seed = diagnostic.get("seed")
    first = diagnostic["runs"][0]["samples"][0]
    backend = make_agent_backend(public["route_id"])
    try:
        initial = backend.reset(seed=seed)
        if canonical(initial) != canonical(first):
            raise ValueError("current native initial state differs from diagnostic")
        legal = backend.legal_actions()
    finally:
        backend.close()
    query_payload = json.dumps({
        "user_responsibility": item["query"],
        "public_evaluation_conditions": public,
    }, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    limits = {
        "max_model_calls": 30,
        "max_message_bytes": 1000000,
        "max_total_message_bytes": 20000000,
        "on_exhaustion": "stop without another action; not a task failure or successful completion",
        "history_truncation": False,
    }
    conversation = build_native_conversation(
        public["route_id"], query=query_payload,
        initial_observation=initial["observation"], legal_actions=legal,
        example_action=0.0, initial_time_seconds=initial["time_seconds"],
        runtime_limits=limits, autonomous_wait=True,
    )
    return {
        "schema": "evidence-query-agent-packet.v1",
        "item_id": item_id,
        "batch_id": batch["batch_id"],
        "route_id": public["route_id"],
        "seed": seed,
        "query": item["query"],
        "query_payload": query_payload,
        "public_contract": public,
        "snapshot": {
            "status": "snapshot_ok", "route_id": public["route_id"],
            "seed": seed, "initial": initial, "legal_actions": legal,
        },
        "model_messages_at_first_call": conversation.messages(),
        "runtime_limits": limits,
        "example_action_scope": "format only; scalar 0.0 is not a recommended policy",
        "provider_calls": 0,
        "execution_status": "not_authorized_not_run",
        "claim_boundary": "Prepared model-visible input only; no model performance evidence.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite packet: {args.output}")
    result = build_packet(args.batch, args.item_id, args.diagnostic)
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.write_text(payload, encoding="utf-8")
    print(json.dumps({
        "output": str(args.output), "sha256": sha256(payload.encode()).hexdigest(),
        "provider_calls": 0, "messages": len(result["model_messages_at_first_call"]),
    }))


if __name__ == "__main__":
    main()
