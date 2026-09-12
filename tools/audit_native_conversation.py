"""Replay real complete traces through the all-route LLM wire codec; no LLM score."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
from unified_compiler.native_action_conversation import (
    build_native_conversation, encode_native_action, decode_native_action, append_native_transition,
)
from unified_compiler.llm_conversation import canonical_json


def audit(route_id, source_override=None):
    folder = "backend_trust_horizon_fix_v1" if route_id == "d1_sustaingym_fault" else "backend_trust_horizon_v1"
    source = Path(source_override).resolve() if source_override else ROOT / "generated" / folder / (route_id + ".json")
    trace = json.loads(source.read_text())["trace"]
    backend = make_agent_backend(route_id)
    try:
        initial = backend.reset(seed=0)
        legal = backend.legal_actions()
        conversation = build_native_conversation(route_id, query="Interface-only diagnostic; no responsibility evaluation.",
            initial_observation=initial["observation"], legal_actions=legal, example_action=trace[0]["action"])
        expected = [initial["observation"]]
        for receipt in trace:
            parsed = decode_native_action(encode_native_action(receipt["action"]))
            if canonical_json(parsed) != canonical_json(receipt["action"]):
                raise ValueError("action shape/value changed by codec")
            append_native_transition(conversation, parsed, receipt)
            expected.append(receipt["observation"])
        reconstructed = conversation.validate()
        if canonical_json(reconstructed) != canonical_json(expected):
            raise ValueError("public observation reconstruction failed")
        messages = conversation.messages()
        for message in messages[3::2]:
            payload = json.loads(message["content"])
            if set(payload) != {"message_type", "action_result", "observation_delta"}:
                raise ValueError("non-delta environment message")
        return {"route_id": route_id, "passed": True, "steps": len(trace), "source": str(source.relative_to(ROOT)),
                "mode": "native initial schema plus recorded native trajectory replay, not an LLM run",
                "messages": messages, "initial_schema": legal,
                "serialized_transcript_bytes": len(canonical_json(messages).encode("utf-8"))}
    finally:
        backend.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", choices=PUBLIC_ROUTE_IDS, required=True)
    parser.add_argument("--source", type=Path, help="Explicit replacement native trace after a backend repair")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated/native_conversation_audit_v1")
    args = parser.parse_args()
    try:
        result = audit(args.route, args.source)
    except Exception as exc:
        result = {"route_id": args.route, "passed": False, "error": f"{type(exc).__name__}: {exc}"}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / (args.route + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k:v for k,v in result.items() if k not in {"messages", "initial_schema"}}, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
