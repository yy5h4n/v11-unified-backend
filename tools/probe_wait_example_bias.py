"""Three one-shot public-prompt variants; not an episode or capability score."""
import json
import os
from copy import deepcopy
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.decision_wait import WAIT_PROTOCOL, decode_decision
from unified_compiler.llm_conversation import canonical_json, canonical_assistant_action, parse_assistant_action
from unified_compiler.inference_budget import InferenceBudget
from tools.run_backend_casebook_llm_pilot import ChatClient


def main():
    source = ROOT/'generated/autonomous_wait_llm_pilot_v1/d0_exogenous_context.jsonl'
    destination = ROOT/'generated/wait_example_bias_v1.jsonl'
    if destination.exists():
        raise SystemExit('existing evidence, no overwrite')
    with source.open() as stream:
        for line in stream:
            event = json.loads(line)
            if event['type'] == 'request_intent':
                baseline = event['messages']
                break
        else:
            raise ValueError('no public request found')
    client = ChatClient('https://aigc.sankuai.com/v1/openai/native', 'deepseek-v4-flash-meituan',
                        os.environ['AIGC_API_KEY'], timeout=60, retries=0)
    budget = InferenceBudget(max_calls=3, max_total_message_bytes=1000000)
    with destination.open('x') as stream:
        for factor in (1, 3, 6):
            messages = deepcopy(baseline)
            system = json.loads(messages[0]['content'])
            system['decision_wait_protocol'] = deepcopy(WAIT_PROTOCOL)
            example = parse_assistant_action({'role': 'assistant', 'content': system['example_is_format_only_not_a_recommended_policy']})
            example['wait']['duration_seconds'] = factor*60
            system['example_is_format_only_not_a_recommended_policy'] = canonical_assistant_action(example)['content']
            messages[0]['content'] = canonical_json(system)
            stream.write(json.dumps({'type': 'request_intent', 'factor': factor, 'messages': messages})+'\n')
            stream.flush(); os.fsync(stream.fileno())
            response = budget.complete(client, messages)
            result = {'type': 'response', 'example_seconds': factor*60, 'response': response,
                      'scope': 'same initial state/query; one sample per example, no simulator execution'}
            try:
                result['decision'] = decode_decision(response['content'], cadence=60, now=0, horizon=720)[0]
            except Exception as exc:
                result['format_error'] = str(exc)
            stream.write(json.dumps(result)+'\n'); stream.flush(); os.fsync(stream.fileno())
            print(json.dumps({k: v for k, v in result.items() if k != 'response'}), flush=True)


if __name__ == '__main__':
    main()
