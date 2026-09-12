"""Bounded two-step provider/native protocol echo test; not control evaluation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_backend_casebook_llm_pilot import ChatClient
from tools.backend_acceptance_runner import _action
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.llm_conversation import canonical_json
from unified_compiler.native_action_conversation import build_native_conversation, decode_native_action, append_native_transition
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
from unified_compiler.inference_budget import InferenceBudget


def probe(route_id, client):
    budget = InferenceBudget(max_calls=2)
    started = time.monotonic()
    result = {"route_id": route_id, "mode": "two-step instructed-action protocol echo; not autonomous control/task evaluation",
              "passed": False, "calls": [], "transitions": [], "model": client.model}
    backend = make_agent_backend(route_id)
    try:
        initial = backend.reset(seed=0)
        legal = backend.legal_actions()
        planned = [_action(route_id, legal, i) for i in range(2)]
        query = ("This is a wire-protocol echo test, NOT a household task. Submit exactly the first listed native action now; "
                 "after the first environment feedback, submit exactly the second. Wrap each in the instructed action envelope. "
                 "Do not evaluate goals or invent a policy. Native actions in order: " + canonical_json(planned))
        conversation = build_native_conversation(route_id, query=query, initial_observation=initial["observation"],
                                                 legal_actions=legal, example_action=planned[0])
        result["initial_observation"] = initial["observation"]
        for index in range(2):
            messages = conversation.messages()
            call = {"decision": index, "request_messages": messages, "attempted": False}
            result["calls"].append(call)
            before_attempts = budget.attempted_calls
            try:
                response = budget.complete(client, messages)
            finally:
                call["attempted"] = budget.attempted_calls > before_attempts
            call.update(response)
            action = decode_native_action(response["content"])
            call["parsed_action"] = action
            call["matches_instructed_action"] = canonical_json(action) == canonical_json(planned[index])
            receipt = backend.step(action)
            result["transitions"].append(receipt)
            append_native_transition(conversation, action, receipt)
        reconstructed = conversation.validate()
        result["state_reconstruction_verified"] = reconstructed[1:] == [r["observation"] for r in result["transitions"]]
        result["passed"] = result["state_reconstruction_verified"] and all(c["matches_instructed_action"] for c in result["calls"])
        result["final_messages"] = conversation.messages()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        backend.close()
    result["wall_seconds"] = time.monotonic() - started
    result["attempted_calls"] = budget.attempted_calls
    result["usage_available_calls"] = sum(bool(c.get("usage")) for c in result["calls"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", required=True, choices=PUBLIC_ROUTE_IDS)
    parser.add_argument("--model", default="deepseek-v4-flash-meituan")
    parser.add_argument("--base-url", default="https://aigc.sankuai.com/v1/openai/native")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated/llm_native_protocol_v1")
    args = parser.parse_args()
    if not os.environ.get("AIGC_API_KEY"):
        raise SystemExit("AIGC_API_KEY is required")
    client = ChatClient(args.base_url, args.model, os.environ["AIGC_API_KEY"], timeout=60, retries=0)
    report = probe(args.route, client)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / (args.route + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k:v for k,v in report.items() if k in {"route_id", "passed", "attempted_calls", "error", "wall_seconds"}}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)
