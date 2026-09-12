"""Explicit synthetic diagnostics for semantic reviewer, not benchmark queries."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.sources import SourceRecord
from query_construction.review import review_messages, parse_review
from tools.run_backend_casebook_llm_pilot import ChatClient


CASES = [
    ('control', 'Whenever the room is empty for 5 minutes, turn off the light.', False),
    ('duration', 'Whenever the room is empty for 15 minutes, turn off the light.', True),
    ('condition', 'Whenever 5 minutes have passed, turn off the light.', True),
    ('outcome', 'Whenever the room is empty for 5 minutes, turn on the light.', True),
    ('scope', 'The next time the room is empty for 5 minutes, turn off the light once and stop monitoring.', True),
]


def main():
    p = argparse.ArgumentParser(); p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    text = CASES[0][1]
    source = SourceRecord('synthetic-review-diagnostic', 'synthetic_test', 'tools/check_query_review_mutations.py',
                          text, 'not_a_human_participant', 'synthetic_recurring_rule', 'project_test_fixture')
    client = ChatClient('https://aigc.sankuai.com/v1/openai/native', 'deepseek-v4-flash-meituan',
                        os.environ['AIGC_API_KEY'], timeout=45, retries=0, max_tokens=1600)
    with args.output.open('x') as stream:
        def emit(row):
            stream.write(json.dumps(row, ensure_ascii=False)+'\n'); stream.flush(); os.fsync(stream.fileno())
        for name, query, should_flag in CASES:
            proposal = {'query': query, 'atoms': [{'kind': 'respond', 'responsibility': query,
                         'evidence': [{'source_id': source.id, 'start': 0, 'end': len(text), 'quote': text}]}]}
            packet = review_messages(source, proposal)
            # Expected labels are retained locally, never sent to reviewer.
            emit({'type': 'request', 'case': name, 'messages': packet, 'expected_flag': should_flag})
            try:
                receipt = client.complete(packet)
            except Exception as exc:
                emit({'type': 'transport_error', 'case': name, 'error_type': type(exc).__name__, 'usage': None}); break
            emit({'type': 'receipt', 'case': name, **receipt})
            try:
                if receipt['finish_reason'] != 'stop': raise ValueError('incomplete review')
                review = parse_review(source.id, receipt['content'])
                flagged = not review['model_review_clear']
                emit({'type': 'result', 'case': name, 'flagged': flagged,
                      'matches_expectation': flagged == should_flag, **review})
                print(name, 'flagged=', flagged, 'expected=', should_flag, flush=True)
            except (ValueError, TypeError) as exc:
                emit({'type': 'review_error', 'case': name, 'reason': str(exc)})


if __name__ == '__main__': main()
