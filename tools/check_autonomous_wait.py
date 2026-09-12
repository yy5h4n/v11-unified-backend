"""Two real native intervals per scripted decision; zero external API calls."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.backend_acceptance_runner import interpreter_for, _action
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS, route_metadata
from unified_compiler.llm_backend_session import run_session
from unified_compiler.inference_budget import InferenceBudget
from unified_compiler.llm_conversation import canonical_json, validate_canonical_conversation
from tools.audit_real_sessions import verify_batches


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--route', required=True, choices=PUBLIC_ROUTE_IDS)
    p.add_argument('--output-dir', type=Path, default=ROOT/'generated/autonomous_wait_native_v1')
    p.add_argument('--worker', action='store_true')
    p.add_argument('--full-horizon', action='store_true', help='One scripted decision holds its control to the declared terminal boundary')
    args = p.parse_args()
    if not args.worker:
        return subprocess.call([interpreter_for(args.route), str(Path(__file__).resolve()), *sys.argv[1:], '--worker'])
    destination = args.output_dir/(args.route+'.json')
    if destination.exists():
        raise SystemExit('choose a new output directory; evidence is not overwritten')
    metadata = route_metadata(args.route)
    duration = metadata['horizon_seconds'] if args.full_horizon else 2*metadata['cadence_seconds']
    class Scripted:
        model = 'SCRIPTED_WAIT_CONFORMANCE_NOT_LLM'
        retries = 0
        def complete(self, messages):
            return {'content': '<answer>'+canonical_json({'action': action,
                'wait': {'mode': 'for', 'duration_seconds': duration}})+'</answer>',
                'usage': None, 'provider_kind': 'scripted'}
    # Inspect schema after reset in run_session, not a second native episode.
    native = make_agent_backend(args.route)
    original_reset = native.reset
    action = None
    def reset(seed=0):
        nonlocal action
        receipt = original_reset(seed=seed)
        action = _action(args.route, native.legal_actions(), 0)
        return receipt
    native.reset = reset
    report = run_session(args.route, 'Scripted wait conformance, not a user task score.', Scripted(),
        InferenceBudget(max_calls=1), example_action=None, backend_factory=lambda _: native)
    report['external_api_calls'] = 0
    batch_verified = verify_batches(report, [c for c in report['calls'] if c['attempted']],
        report['transitions'], validate_canonical_conversation(report['final_messages']))
    scope_passed = ((report['status'] == 'native_terminal_reached' and report.get('declared_horizon_reached') is True)
                    if args.full_horizon else (report['status'] == 'budget_exhausted' and len(report['transitions']) == 2))
    report['wait_check_scope'] = 'full native horizon, scripted single decision' if args.full_horizon else 'two native intervals, scripted single decision'
    report['wait_conformance_passed'] = (scope_passed
        and report['attempted_calls'] == 1
        and len(report['decisions']) == 1 and report.get('backend_closed') is True and batch_verified)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with destination.open('x') as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: report.get(k) for k in ('route_id', 'status', 'wait_conformance_passed', 'error')}))
    return 0 if report['wait_conformance_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
