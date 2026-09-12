"""Run one real model/native episode. No benchmark score or implicit retries."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.backend_acceptance_runner import interpreter_for
from tools.run_backend_casebook_llm_pilot import ChatClient, summarize_model_usage
from unified_compiler.inference_budget import InferenceBudget
from unified_compiler.llm_backend_session import run_session
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--route', choices=PUBLIC_ROUTE_IDS, required=True)
    p.add_argument('--query', required=True)
    p.add_argument('--example-action', required=True, help='JSON syntax-only legal example, never automatically submitted')
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--max-calls', type=int, default=30)
    p.add_argument('--max-message-bytes', type=int, default=300000)
    p.add_argument('--max-total-message-bytes', type=int, default=1000000)
    p.add_argument('--worker', action='store_true')
    args = p.parse_args()
    # A venv executable can resolve to the same base binary while selecting
    # different site-packages. Always enter the selected interpreter once;
    # --worker prevents recursion. Never infer environment identity by inode.
    if not args.worker:
        return subprocess.call([interpreter_for(args.route), str(Path(__file__).resolve()), *sys.argv[1:], '--worker'])
    action = json.loads(args.example_action)
    budget = InferenceBudget(max_calls=args.max_calls, max_message_bytes=args.max_message_bytes,
                             max_total_message_bytes=args.max_total_message_bytes)
    client = ChatClient('https://aigc.sankuai.com/v1/openai/native', 'deepseek-v4-flash-meituan',
                        os.environ['AIGC_API_KEY'], timeout=60, retries=0)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / (args.route+'.json')
    journal = args.output_dir / (args.route+'.jsonl')
    if target.exists() or journal.exists():
        raise SystemExit('existing session evidence: choose a new output directory; no automatic resume')
    with journal.open('x') as stream:
        def emit(event):
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
            if event['type'] in {'decision_finished', 'session_finished'}:
                print(json.dumps({k: v for k, v in event.items() if k != 'receipt'}), flush=True)
        result = run_session(args.route, args.query, client, budget, example_action=action, emit=emit)
    result['model_usage'] = summarize_model_usage([c for c in result['calls'] if c['attempted']])
    result['usage_available_calls'] = sum(bool(c.get('usage')) for c in result['calls'] if c['attempted'])
    result['usage_interpretation'] = 'Missing provider usage is unavailable, not zero billed tokens. Message bytes attempted are measured separately; TLS failure may prevent actual HTTP transmission.'
    with target.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'status': result['status'], 'calls': result['attempted_calls'], 'usage': result['model_usage']}), flush=True)
    return 0 if result['status'] == 'native_terminal_reached' else 1


if __name__ == '__main__':
    raise SystemExit(main())
