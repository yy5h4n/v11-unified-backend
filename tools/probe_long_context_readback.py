"""Bounded recorded-history state retrieval, NOT live control or task scoring."""
import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.llm_conversation import canonical_json, validate_canonical_conversation
from unified_compiler.native_action_conversation import decode_native_action
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
from tools.run_backend_casebook_llm_pilot import ChatClient
from unified_compiler.inference_budget import InferenceBudget


def leaves(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, path + (key,))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from leaves(child, path + (i,))
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        if not any(any(term in str(p).lower() for term in ('clock', 'time', 'step', 'epoch', 'hour', 'month')) for p in path):
            yield path, value


def prepare(report):
    # Stop before the final action: no post-terminal command is requested.
    messages = deepcopy(report['messages'][:-2])
    states = validate_canonical_conversation(messages)
    maps = [dict(leaves(s)) for s in states]
    candidates = []
    for path, value in maps[-1].items():
        if not all(path in m for m in maps):
            continue
        changes = [i for i in range(1, len(maps)) if maps[i][path] != maps[i-1][path]]
        candidates.append((changes[-1] if changes else 0, path, value))
    candidates.sort(key=lambda row: (row[0], str(row[1])))
    if not candidates:
        raise ValueError('no numeric state fields available')
    selected = [candidates[0]]
    for item in reversed(candidates):
        if item[1] not in [s[1] for s in selected]:
            selected.append(item)
        if len(selected) == min(3, len(candidates)):
            break
    paths = [list(row[1]) for row in selected]
    # Replace the controller system for this isolated readback diagnostic.
    # Retain only the public delta instructions and recorded public history.
    original_system = json.loads(messages[0]['content'])
    messages[0]['content'] = canonical_json({
        'role': 'recorded_public_state_readback_diagnostic',
        'observation_delta_protocol': original_system['observation_delta_protocol'],
        'instruction': 'This is recorded history, not a live control task. Reconstruct the latest observation. Reply exactly <answer>{"action":[VALUES]}</answer>, numeric values in the requested path order. The action field is only a diagnostic response container and will NOT be sent to a backend.'})
    messages.append({'role': 'user', 'content': canonical_json({'read_current_numeric_paths': paths,
        'instruction': 'Return current values, including fields unchanged in the latest delta. Do not return the paths or earlier values.'})})
    return messages, selected


def run(client, output_dir, max_bytes, input_dir=None):
    budget = InferenceBudget(max_message_bytes=max_bytes, max_calls=15)
    input_dir = input_dir or ROOT / 'generated/public_receipt_audit_v2'
    output_dir.mkdir(parents=True, exist_ok=True)
    for route in PUBLIC_ROUTE_IDS:
        target = output_dir / (route + '.json')
        if target.exists():
            raise RuntimeError('refusing to overwrite or repeat an existing run: ' + route)
    for route in PUBLIC_ROUTE_IDS:
        source = input_dir / (route + '.json')
        report = json.loads(source.read_text())
        messages, selected = prepare(report)
        size = len(canonical_json(messages).encode())
        result = {'route_id': route, 'mode': 'recorded-history readback, not live control',
            'source': str(source.relative_to(ROOT)), 'history_steps': report['steps'] - 1,
            'request_messages_bytes': size, 'max_messages_bytes': max_bytes,
            'fields': [{'path': list(p), 'expected': v, 'last_changed_state_index': age} for age, p, v in selected],
            'attempted_calls': 0, 'passed': False}
        if size > max_bytes:
            result['status'] = 'local_budget_exceeded_no_truncation'
        elif client is None:
            result['status'] = 'offline_only'
        else:
            result['request_messages'] = messages
            result['attempted_calls'] = 1
            try:
                response = budget.complete(client, messages)
                result['response'] = response
                values = decode_native_action(response['content'])
                result['returned_values'] = values
                result['passed'] = isinstance(values, list) and len(values) == len(selected) and all(
                    isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a)
                    and math.isclose(a, row[2], rel_tol=1e-6, abs_tol=1e-6)
                    for a, row in zip(values, selected))
                result['status'] = 'passed' if result['passed'] else 'readback_mismatch'
            except Exception as exc:
                result['status'] = 'call_or_format_error'
                result['error'] = f'{type(exc).__name__}: {exc}'
        (output_dir / (route + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(route, result['status'], size, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-bytes', type=int, default=300000)
    parser.add_argument('--input-dir', type=Path, default=ROOT / 'generated/public_receipt_audit_v2')
    args = parser.parse_args()
    client = ChatClient('https://aigc.sankuai.com/v1/openai/native', 'deepseek-v4-flash-meituan', os.environ['AIGC_API_KEY'], timeout=60, retries=0) if args.live else None
    run(client, args.output_dir, args.max_bytes, args.input_dir)
