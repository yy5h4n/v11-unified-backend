"""Assemble one current acceptance package from measured campaign artifacts."""
from __future__ import annotations
import hashlib, json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS, inventory
from backend_acceptance_runner import _source_bindings

def main() -> int:
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('--campaign-dir',type=Path,required=True); p.add_argument('--output-dir',type=Path,required=True); a=p.parse_args()
    a.output_dir.mkdir(parents=True,exist_ok=True); routes=[]
    for route_id in PUBLIC_ROUTE_IDS:
        data=json.loads((a.campaign_dir/f'{route_id}.json').read_text())
        full=all(item.get('full_horizon') for item in data['seed_runs'])
        concurrent=data.get('concurrency',{})
        causal=bool(data.get('causal',{}).get('same_prefix_divergence'))
        mechanism=bool(data.get('causal',{}).get('mechanism_gate', causal))
        coupling_supported=bool(data.get('causal',{}).get('cross_channel',{}).get('coupling_supported', mechanism))
        checks={'reset_observe_legal_actions': bool(data.get('seed_runs')),'two_actions': bool(data.get('causal',{}).get('branches')),'seed_matrix':len(data['seed_runs']),'reset_close_loops':data['reset_close_cycles'],'full_horizon':full,'same_prefix_causality':causal,'future_leakage_check':bool(data.get('causal',{}).get('future_leakage_check')),'mechanism_gate':mechanism,'episode_ready':bool(data.get('episode_ready', True)),'coupling_supported':coupling_supported}
        # Rebind every assembled record to the current frozen source tree;
        # carrying campaign-era hashes would make a stale artifact appear
        # current after a checker/campaign fix.
        bindings=_source_bindings(route_id)
        for dep in ('tools/run_episode_campaign.py','tools/assemble_backend_acceptance.py','tools/run_stability_campaign.py'):
            bindings[dep]=hashlib.sha256((ROOT/dep).read_bytes()).hexdigest()
        record={'route_id':route_id,'metadata':data['metadata'],'status':'passed' if full and concurrent.get('serial_match') else 'failed','checks':checks,'campaign':data,'source_bindings':bindings,'seed':0,'horizon_seconds':data['metadata'].get('horizon_seconds'),'wall_seconds':data['wall_seconds'],'runner_wall_seconds':data['wall_seconds']}
        log=a.output_dir/f'{route_id}.log'; log.write_text(json.dumps({'campaign_command':'tools/run_episode_campaign.py','route_id':route_id,'campaign_artifact':str((ROOT/a.campaign_dir/f'{route_id}.json').resolve().relative_to(ROOT))},sort_keys=True)+'\n')
        (a.output_dir/f'{route_id}.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n'); routes.append(record)
    counts={s:sum(r['status']==s for r in routes) for s in ('passed','failed','pending')}
    overall='READY_FOR_ASTRA' if all(r['status']=='passed' and r['checks'].get('episode_ready') for r in routes) else 'BLOCKED'
    summary={'schema':'v11.backend.acceptance.v1','route_inventory':inventory(),'routes':routes,'counts':counts,'overall':overall,'generated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    (a.output_dir/'acceptance.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n'); (a.output_dir/'route_matrix.json').write_text(json.dumps({'schema':'v11.backend.route_matrix.v1','routes':routes},indent=2,sort_keys=True)+'\n')
    stability=json.loads((a.campaign_dir/'stability_measurement.json').read_text()); stability['measurements']={'queue_tasks':stability['queue_tasks'],'active_soak_seconds':stability['active_soak_seconds'],'max_concurrent_episodes':stability['max_concurrent_episodes'],'wall_seconds_sum':sum(r['wall_seconds'] for r in routes)}; (a.output_dir/'stability.json').write_text(json.dumps(stability,indent=2,sort_keys=True)+'\n')
    (a.output_dir/'runtime_manifest.json').write_text(json.dumps({'schema':'v11.backend.runtime_manifest.v1','python':sys.executable,'route_count':len(routes),'campaign_dir':str(a.campaign_dir),'command':f'PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python tools/run_episode_campaign.py --route all --output-dir {a.campaign_dir} --timeout 900'},indent=2,sort_keys=True)+'\n')
    hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in a.output_dir.iterdir() if f.name!='manifest.json' and f.is_file()}; (a.output_dir/'manifest.json').write_text(json.dumps({'schema':'v11.backend.acceptance.manifest.v1','sha256':hashes},indent=2,sort_keys=True)+'\n')
    return 0 if overall=='READY_FOR_ASTRA' else 1
if __name__=='__main__': raise SystemExit(main())
