#!/usr/bin/env python3
"""Generate all-route review data without changing stored browser annotations."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend_casebook_v0.claim_designs import DESIGNS, COMMON
from backend_casebook_v0.case_review import review_for
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS


def main():
    ids = [d['route'] for d in DESIGNS]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(PUBLIC_ROUTE_IDS) | {'boptest'}
    reviewed = [dict(d, review=review_for(d['route'])) for d in DESIGNS]
    payload = {'status': 'CLAIM_DESIGN_NOT_EXECUTION_RESULT', 'common': COMMON, 'cases': reviewed}
    (ROOT/'backend_casebook_v0/claim_designs.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
    script = 'window.CLAIM_DESIGNS = '+json.dumps(payload,ensure_ascii=False)+';\n'
    script += '''
for (const c of window.BACKEND_CASES) {
  const d = window.CLAIM_DESIGNS.cases.find(d => d.route === c.route || (d.route === 'boptest' && c.route.toLowerCase().includes('boptest')));
  if (!d) throw new Error('Missing claim design: '+c.route);
  c.claimDesign=d;
  c.caseReview=d.review;
  c.query=d.query; c.queryZh=d.zh; c.support=d.claim;
  c.status='design_pending';
  c.responsibility={activate:'场景公开的服务窗口或触发条件；不把隐藏事件写进query',maintain:d.outcome,release:'公开窗口结束；本次episode结束不等于长期责任永久解除',forbidden:'不能改外生日程、读取未来故障，也不能把动作成功当作结果成功'};
  c.timeline=[['场景绑定',d.bind,'动作由被测策略选择']];
  c.evaluator={hard:[d.outcome],soft:[d.cost,'LLM调用与tokens、运行时间；与履约分别报告'],failure:d.contrast};
  if(c.route==='d1_sustaingym_fault') c.backend.actions=['已验证制冷比例 [-0.05, 0]；不可用维度必须为0；公开schema为准'];
}
'''
    (ROOT/'backend_casebook_v0/claim_designs.js').write_text(script)
    print(f'Covered {len(PUBLIC_ROUTE_IDS)} formal routes plus BOPTEST candidate; no execution readiness inferred.')


if __name__ == '__main__': main()
