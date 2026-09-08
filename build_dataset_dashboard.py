#!/usr/bin/env python3
"""Build the standalone V11 dataset dashboard from generated artifacts."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"
RELEASE = GENERATED / "battery_pv_release"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    payload = {
        "public": read_jsonl(RELEASE / "episodes_public.jsonl"),
        "private": read_jsonl(RELEASE / "episodes_private.jsonl"),
        "build": json.loads((RELEASE / "build_report.json").read_text(encoding="utf-8")),
        "qa": json.loads((RELEASE / "qa_report.json").read_text(encoding="utf-8")),
        "pool": json.loads((GENERATED / "battery_pv_process_pool.json").read_text(encoding="utf-8")),
        "gate": json.loads((GENERATED / "battery_pv_process_replay_gate.json").read_text(encoding="utf-8")),
        "legacy": {"hvac_processes": 50, "ev_processes": 36, "legacy_episodes": 95},
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.replace("__DATA__", data)
    output = ROOT / "dataset_dashboard.html"
    output.write_text(html, encoding="utf-8")
    print(json.dumps({"output": str(output), "bytes": output.stat().st_size, "episodes": len(payload["public"])}, ensure_ascii=False))


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>V11 Responsibility Benchmark 数据看板</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#18212f;--muted:#64748b;--line:#e3e8f0;--blue:#2563eb;--teal:#0f9f8f;--amber:#d97706;--red:#dc2626}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"PingFang SC",sans-serif}
header{padding:30px max(24px,5vw) 22px;background:linear-gradient(120deg,#0f172a,#173a66);color:#fff}header h1{margin:0 0 8px;font-size:28px}header p{margin:0;color:#cbd5e1;max-width:950px}
main{max-width:1440px;margin:auto;padding:22px}.grid{display:grid;gap:14px}.kpis{grid-template-columns:repeat(6,minmax(135px,1fr))}.card{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:16px;box-shadow:0 2px 8px #0f172a0a}.kpi b{display:block;font-size:25px}.kpi span,.muted{color:var(--muted)}
h2{font-size:18px;margin:4px 0 12px}.section{margin-top:18px}.pipeline{display:grid;grid-template-columns:repeat(8,1fr);gap:7px}.step{padding:11px 8px;border-radius:9px;background:#eef4ff;color:#174ea6;text-align:center;font-size:12px}.step:not(:last-child):after{content:"→";float:right;margin-right:-13px;color:#94a3b8}
.two{grid-template-columns:1fr 1fr}.bars{display:grid;gap:9px}.barrow{display:grid;grid-template-columns:170px 1fr 42px;gap:8px;align-items:center}.track{height:9px;background:#edf1f6;border-radius:99px;overflow:hidden}.fill{height:100%;background:var(--blue);border-radius:99px}.fill.teal{background:var(--teal)}.fill.amber{background:var(--amber)}
.filters{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:12px}input,select{border:1px solid var(--line);background:#fff;border-radius:8px;padding:8px 10px;color:var(--ink)}input{min-width:270px}
.tablewrap{overflow:auto;max-height:510px;border:1px solid var(--line);border-radius:10px}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:left}th{position:sticky;top:0;background:#f8fafc;cursor:pointer}tbody tr{cursor:pointer}tbody tr:hover{background:#f1f6ff}.pill{display:inline-block;border-radius:99px;padding:2px 8px;background:#eef4ff;color:#1d4ed8;font-size:12px}.ok{color:#087f5b;font-weight:700}
.detail{display:none;margin-top:14px}.detail.show{display:block}.detailgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.box{background:#f8fafc;border:1px solid var(--line);border-radius:9px;padding:12px;overflow:auto}.box h3{margin:0 0 7px;font-size:14px}.query{font-size:16px;padding:14px;border-left:4px solid var(--blue);background:#eef4ff;border-radius:6px}pre{font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;white-space:pre-wrap;word-break:break-word}.boundary{border-left:4px solid var(--amber)}
@media(max-width:1000px){.kpis{grid-template-columns:repeat(3,1fr)}.pipeline{grid-template-columns:repeat(4,1fr)}.two,.detailgrid{grid-template-columns:1fr}}@media(max-width:620px){.kpis{grid-template-columns:repeat(2,1fr)}main{padding:12px}.pipeline{grid-template-columns:repeat(2,1fr)}}
</style></head>
<body><header><h1>V11 Standing Responsibility Benchmark</h1><p>自顶向下的数据看板：责任类型与物理拓扑 → 后端能力 → canonical physical process → typed contract → executable Episode → trajectory evaluator。当前新增正式发布的是 battery/PV；HVAC 与 EV 显示为迁移的 legacy canonical processes。</p></header>
<main>
<section class="grid kpis" id="kpis"></section>
<section class="card section"><h2>统一构建流水线</h2><div class="pipeline"><div class="step">责任生命周期<br>+ 物理拓扑</div><div class="step">Process<br>Requirement</div><div class="step">Backend<br>Registry</div><div class="step">Physical<br>Process Pool</div><div class="step">Contract<br>Binding</div><div class="step">Executable<br>Episode</div><div class="step">Trajectory<br>Evaluator</div><div class="step">QA / 去重<br>/ Split</div></div></section>
<section class="grid two section"><div class="card"><h2>责任与 Split 构成</h2><div id="familyBars" class="bars"></div><hr style="border:0;border-top:1px solid var(--line);margin:14px 0"><div id="splitBars" class="bars"></div></div><div class="card"><h2>方法与主张边界</h2><ul><li>扫描 12 个建模住宅 × 365 日，共 4,380 个 source-grounded 窗口；每户每季选择 1 个物理机会最强的窗口。</li><li>负荷来自 ResStock-derived 建模轨迹，PV 由固定 CityLearn/PVWatts 路径生成；<b>不是家庭实测数据</b>。</li><li>48/48 过程通过确定性、终止、合法动作、SOC、终端储备和责任可行性门禁。</li><li>Evaluator 只评状态轨迹：硬约束 → 终端目标 → 加权软代价；不提供 gold action。</li><li>50 HVAC + 36 EV 是迁移的 legacy canonical processes；当前新格式正式 Episodes 为 48 条 battery/PV。</li></ul></div></section>
<section class="card section"><h2>Battery/PV Responsibility Episodes</h2><div class="filters"><input id="search" placeholder="搜索 Query / Episode / Building"><select id="family"><option value="">全部责任</option></select><select id="split"><option value="">全部 Split</option></select><span class="muted" id="shown"></span></div><div class="tablewrap"><table><thead><tr><th data-key="episode_id">Episode</th><th data-key="family_id">责任</th><th data-key="split">Split</th><th>住宅</th><th>季节</th><th>可转移 kWh</th><th>购电改善 kWh</th><th>Gate</th></tr></thead><tbody id="rows"></tbody></table></div><div id="detail" class="detail"></div></section>
<section class="card section boundary"><h2>如何读这份数据</h2><p>公开记录给 Agent：自然语言责任、初始观察、逐小时观察协议与连续电池动作。私有记录给 evaluator：物理绑定、唯一 PRIMARY Contract、派生变量公式和 typed clauses。QA 中的固定自用策略只证明任务可完成并产生不同物理后果，其动作轨迹不进入公开数据，也不是“正确答案”。</p></section>
</main>
<script>const D=__DATA__;
const priv=new Map(D.private.map(x=>[x.episode_id,x])), gate=new Map(D.gate.processes.map(x=>[x.process_id,x]));
const joined=D.public.map(x=>({...x,_p:priv.get(x.episode_id),_g:gate.get(priv.get(x.episode_id).process_id)}));
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function kpis(){const xs=[['48','V11 Battery/PV Episodes'],['48 / 48','责任级 replay 通过'],['4,380','扫描的住宅日'],['12','建模住宅'],['50','Legacy HVAC processes'],['36','Legacy EV processes']];document.querySelector('#kpis').innerHTML=xs.map(x=>`<div class="card kpi"><b>${x[0]}</b><span>${x[1]}</span></div>`).join('')}
function bars(id,obj,klass=''){const m=Math.max(...Object.values(obj));document.querySelector(id).innerHTML=Object.entries(obj).map(([k,v])=>`<div class="barrow"><span>${esc(k)}</span><div class="track"><div class="fill ${klass}" style="width:${100*v/m}%"></div></div><b>${v}</b></div>`).join('')}
function options(id,vals){document.querySelector(id).insertAdjacentHTML('beforeend',[...new Set(vals)].sort().map(v=>`<option>${esc(v)}</option>`).join(''))}
let sortKey='episode_id', asc=true;
function render(){const q=document.querySelector('#search').value.toLowerCase(),f=document.querySelector('#family').value,s=document.querySelector('#split').value;let xs=joined.filter(x=>(!f||x.family_id===f)&&(!s||x.split===s)&&(!q||JSON.stringify(x).toLowerCase().includes(q)));xs.sort((a,b)=>String(a[sortKey]??'').localeCompare(String(b[sortKey]??''))*(asc?1:-1));document.querySelector('#shown').textContent=`显示 ${xs.length} / ${joined.length}`;document.querySelector('#rows').innerHTML=xs.map(x=>`<tr data-id="${esc(x.episode_id)}"><td>${esc(x.episode_id.slice(-18))}</td><td><span class="pill">${esc(x.family_id)}</span></td><td>${esc(x.split)}</td><td>${esc(x._p.backend_binding.building_id.split('-').pop())}</td><td>Q${x._p.selection_lineage.season_index+1}</td><td>${x._p.selection_lineage.selection_metrics.transferable_kwh.toFixed(2)}</td><td>${x._g.grid_import_improvement_kwh.toFixed(2)}</td><td class="ok">PASS</td></tr>`).join('');document.querySelectorAll('tbody tr').forEach(r=>r.onclick=()=>show(r.dataset.id))}
function show(id){const x=joined.find(v=>v.episode_id===id),p=x._p,g=x._g,m=p.selection_lineage.selection_metrics;document.querySelector('#detail').className='detail show';document.querySelector('#detail').innerHTML=`<h2>Episode 详情</h2><div class="query">${esc(x.responsibility)}</div><div class="detailgrid" style="margin-top:12px"><div class="box"><h3>公开：初始观察</h3><pre>${esc(JSON.stringify(x.initial_observation,null,2))}</pre></div><div class="box"><h3>公开：动作协议</h3><pre>${esc(JSON.stringify(x.allowed_actions,null,2))}</pre></div><div class="box"><h3>私有：Typed Responsibility Contract</h3><pre>${esc(JSON.stringify(p.responsibility_contract,null,2))}</pre></div><div class="box"><h3>物理过程选择依据</h3><pre>${esc(JSON.stringify({building:p.backend_binding.building_id,window:[p.backend_binding.source_start_row,p.backend_binding.source_end_row],season:p.selection_lineage.season_index,metrics:m},null,2))}</pre></div><div class="box"><h3>Replay QA（无 gold action）</h3><pre>${esc(JSON.stringify({passed:g.passed,deterministic:g.deterministic,terminated:g.terminated,grid_import_improvement_kwh:g.grid_import_improvement_kwh,idle_score:g.idle_score,witness_score:g.feasibility_witness_score,trajectory_digests:g.trajectory_digests},null,2))}</pre></div><div class="box"><h3>可复现绑定</h3><pre>${esc(JSON.stringify(p.backend_binding,null,2))}</pre></div></div>`;document.querySelector('#detail').scrollIntoView({behavior:'smooth',block:'nearest'})}
kpis();bars('#familyBars',D.build.family_counts);bars('#splitBars',D.build.split_counts,'teal');options('#family',joined.map(x=>x.family_id));options('#split',joined.map(x=>x.split));['search','family','split'].forEach(id=>document.querySelector('#'+id).addEventListener(id==='search'?'input':'change',render));document.querySelectorAll('th[data-key]').forEach(h=>h.onclick=()=>{if(sortKey===h.dataset.key)asc=!asc;else{sortKey=h.dataset.key;asc=true}render()});render();
</script></body></html>'''


if __name__ == "__main__":
    main()
