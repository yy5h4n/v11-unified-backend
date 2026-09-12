"""Read initial public observation and action schema from the actual backend."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.backend_acceptance_runner import interpreter_for
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.observation_contracts import validate_public_observation
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS, route_metadata


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--route', choices=PUBLIC_ROUTE_IDS, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--worker', action='store_true')
    args = p.parse_args()
    if not args.worker:
        return subprocess.call([interpreter_for(args.route), str(Path(__file__).resolve()),
                                *sys.argv[1:], '--worker'])
    # Reserve output before native initialization; preserve any failed attempt.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        backend = None
        result = {'route_id': args.route, 'seed': args.seed, 'task_success': None,
                  'scope': 'initial public interface only; no trajectory feasibility'}
        try:
            backend = make_agent_backend(args.route)
            initial = backend.reset(seed=args.seed)
            validate_public_observation(args.route, initial['observation'])
            result.update(status='snapshot_ok', initial=initial,
                          legal_actions=backend.legal_actions(), clock=route_metadata(args.route))
        except Exception as exc:
            result.update(status='snapshot_error', error_type=type(exc).__name__, error=str(exc))
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception as exc:
                    result.update(status='close_error', close_error_type=type(exc).__name__)
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({'route': args.route, 'status': result['status']}), flush=True)
        return 0 if result['status'] == 'snapshot_ok' else 1


if __name__ == '__main__': raise SystemExit(main())
