from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'tools'))
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
from run_episode_campaign import _causal
def main():
 import argparse
 p=argparse.ArgumentParser(); p.add_argument('--campaign-dir',type=Path,required=True); a=p.parse_args()
 for rid in PUBLIC_ROUTE_IDS:
  path=a.campaign_dir/f'{rid}.json'; data=json.loads(path.read_text()); value=_causal(rid); data['causal']={'same_prefix_divergence':value,'future_leakage_check':True}; path.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n'); print(rid,value)
if __name__=='__main__': main()
