"""Fresh native full-horizon integration via a SCRIPTED client, not an LLM."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.llm_backend_session import run_session
from unified_compiler.inference_budget import InferenceBudget
from unified_compiler.native_action_conversation import encode_native_action
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--route', choices=PUBLIC_ROUTE_IDS, required=True)
    p.add_argument('--output-dir', type=Path, default=ROOT/'generated/native_session_path_v1')
    args = p.parse_args()
    source_dir = ('citylearn_reset_state_v2' if args.route in {'d3_citylearn_multi_system', 'd3_citylearn_multibuilding_competition'} else
                  'battery_soc_repair_v1' if args.route == 'd1_citylearn_battery_fault' else
                  'backend_trust_horizon_fix_v1' if args.route == 'd1_sustaingym_fault' else 'backend_trust_horizon_v1')
    source = ROOT/'generated'/source_dir/(args.route+'.json')
    reference = json.loads(source.read_text())
    actions = [r['action'] for r in reference['trace']]

    class ScriptedClient:
        model = 'SCRIPTED_NATIVE_INTEGRATION_NOT_LLM'
        retries = 0
        def __init__(self):
            self.index = 0
        def complete(self, messages):
            action = actions[self.index]
            self.index += 1
            return {'content': encode_native_action(action), 'provider_kind': 'scripted', 'usage': None}

    report = run_session(args.route, 'Native full-horizon infrastructure check; scripted actions, no responsibility scoring.',
        ScriptedClient(), InferenceBudget(max_calls=len(actions), max_message_bytes=2000000,
            max_total_message_bytes=2000000*len(actions)), example_action=actions[0])
    report['mode'] = 'fresh native execution through production session path, SCRIPTED provider, no model capability claim'
    report['action_source'] = str(source.relative_to(ROOT))
    report['external_api_calls'] = 0
    report['scripted_client_calls'] = report['attempted_calls']
    report['conformance_passed'] = (report['status'] == 'native_terminal_reached'
        and report['declared_horizon_reached'] and report['state_reconstruction_verified']
        and report['backend_closed'] and len(report['transitions']) == len(actions))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir/(args.route+'.json')).open('x') as f:
        json.dump(report, f, indent=2, allow_nan=False)
    print(json.dumps({k: report.get(k) for k in ('route_id', 'status', 'conformance_passed', 'scripted_client_calls', 'wall_seconds', 'error')}))
    return 0 if report['conformance_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
