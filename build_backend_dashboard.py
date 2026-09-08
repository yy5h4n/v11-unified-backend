#!/usr/bin/env python3
"""Build a standalone dashboard for backend mapping and the first HVAC release."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
G = ROOT / "generated"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> None:
    release = G / "supported_hvac_release_v1"
    payload = {
        "inventory": json.loads((G / "backend_capability_inventory_v1.json").read_text()),
        "mapping": json.loads((G / "responsibility_backend_mapping_v1.json").read_text()),
        "survey": json.loads((G / "expanded_backend_survey_v1.json").read_text()),
        "gate": json.loads((G / "supported_hvac_replay_gate_v1.json").read_text()),
        "build": json.loads((release / "build_report.json").read_text()),
        "public": jsonl(release / "episodes_public.jsonl"),
        "private": jsonl(release / "episodes_private.jsonl"),
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    output = ROOT / "backend_dashboard.html"
    output.write_text(TEMPLATE.replace("__DATA__", data), encoding="utf-8")
    print(json.dumps({"output": str(output), "responsibilities": 129, "episodes": 48}, ensure_ascii=False))


TEMPLATE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>V11 后端构造看板</title><style>
:root{--bg:#f4f7fb;--card:#fff;--ink:#172033;--muted:#64748b;--line:#dfe6ef;--blue:#2563eb;--green:#07845e;--amber:#bd6800;--red:#b42318}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"PingFang SC",sans-serif}header{background:linear-gradient(120deg,#111827,#204d78);color:#fff;padding:28px max(22px,5vw)}header h1{margin:0 0 7px}header p{margin:0;color:#d5deea}main{max-width:1450px;margin:auto;padding:20px}.grid{display:grid;gap:12px}.kpis{grid-template-columns:repeat(6,1fr)}.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:15px;box-shadow:0 2px 9px #1020400a}.kpi b{display:block;font-size:25px}.kpi span,.muted{color:var(--muted)}h2{font-size:18px;margin:0 0 11px}.two{grid-template-columns:1fr 1fr}.pipeline{display:grid;grid-template-columns:repeat(6,1fr);gap:7px}.step{padding:10px;background:#edf4ff;color:#174b9b;border-radius:8px;text-align:center}.bar{display:grid;grid-template-columns:190px 1fr 45px;gap:8px;align-items:center;margin:8px 0}.track{height:9px;background:#edf1f5;border-radius:9px;overflow:hidden}.fill{height:100%;background:var(--blue)}.table{overflow:auto;max-height:540px;border:1px solid var(--line);border-radius:9px}table{border-collapse:collapse;width:100%;white-space:nowrap}th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left}th{position:sticky;top:0;background:#f8fafc}input,select{padding:8px 10px;border:1px solid var(--line);border-radius:8px;margin:0 7px 10px 0}.pill{padding:2px 8px;border-radius:99px;font-size:12px}.SUPPORTED_REPLAYABLE{background:#dcfce7;color:#08734f}.PARTIALLY_SUPPORTED{background:#fff2cc;color:#9a5a00}.UNSUPPORTED_BACKEND_GAP{background:#fee4e2;color:#a31b13}.ok{color:var(--green);font-weight:700}.warn{border-left:4px solid var(--amber)}pre{white-space:pre-wrap;word-break:break-word;background:#f8fafc;padding:10px;border-radius:8px}@media(max-width:1000px){.kpis{grid-template-columns:repeat(3,1fr)}.two{grid-template-columns:1fr}.pipeline{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.kpis{grid-template-columns:repeat(2,1fr)}}
</style></head><body><header><h1>V11 Responsibility → Backend 构造看板</h1><p>先冻结责任，再声明物理需求，再核验后端，最后挖掘和回放 Episode；后端不反向改写责任。</p></header><main>
<section class="grid kpis" id="kpis"></section>
<section class="card" style="margin-top:13px"><h2>当前流水线状态</h2><div class="pipeline"><div class="step">129 条冻结责任<br>DONE</div><div class="step">物理需求映射<br>DONE</div><div class="step">后端能力审计<br>DONE</div><div class="step">50 HVAC 候选回放<br>DONE</div><div class="step">48 Episode 编译<br>DONE</div><div class="step">扩展后端缺口<br>NEXT</div></div></section>
<section class="grid two" style="margin-top:13px"><div class="card"><h2>129 条责任的后端状态</h2><div id="status"></div></div><div class="card"><h2>可执行物理过程池</h2><div id="backends"></div></div></section>
<section class="grid two" style="margin-top:13px"><div class="card"><h2>外部后端扩展潜力（未验证）</h2><div class="bar"><span>Direct adapter candidates</span><div class="track"><div class="fill" style="width:35%"></div></div><b>25</b></div><div class="bar"><span>Composite / thin extension</span><div class="track"><div class="fill" style="width:46%"></div></div><b>33</b></div><div class="bar"><span>Event / logic only</span><div class="track"><div class="fill" style="width:100%"></div></div><b>71</b></div></div><div class="card"><h2>候选接入顺序</h2><ol><li>EnergyPlus/OpenStudio；BOPTEST 用于 HVAC/建筑控制测试。</li><li>BEHAVIOR-1K/OmniGibson：家务物理过程。</li><li>GreenLight/AquaCrop：植物水分过程。</li><li>FDS、EPANET、GridLAB-D：高风险安全与全屋能源。</li></ol><p class="muted">候选必须经过安装、数据探测、因果回放和 evaluator gate，不能视为当前支持。</p></div></section>
<section class="card warn" style="margin-top:13px"><h2>关键边界</h2><div>134 是 indexed process inventory：其中 86 个 HVAC/EV 是 legacy executable pending migration，只有 48 个 battery/PV 已完成本版本 replay verification。battery/PV 过程不是 48 个新目录责任；“家庭能源成本”目前缺 whole-home tariff、cost evaluator 和授权负载范围。HVAC 的 ±2°C 是 Episode construction witness 参数，不是从责任语义声称的普遍用户阈值；50 个候选中有 2 个未通过该门，因此首批只发布 48 条。</div></section>
<section class="card" style="margin-top:13px"><h2>逐责任映射</h2><input id="q" placeholder="搜索责任 / Query / 缺失能力"><select id="s"><option value="">全部状态</option></select><span id="shown" class="muted"></span><div class="table"><table><thead><tr><th>ID</th><th>Query</th><th>Family</th><th>状态</th><th>候选后端</th><th>缺失能力</th></tr></thead><tbody id="rows"></tbody></table></div></section>
<section class="card" style="margin-top:13px"><h2>首批 HVAC Episode</h2><div id="episodes"></div></section>
</main><script>const D=__DATA__;const C=D.mapping.summary.status_counts,B=D.build;function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}document.querySelector('#kpis').innerHTML=[['129','冻结责任'],['134','可执行物理过程'],['2','完整后端匹配责任'],['12','部分支持责任'],['48','首批 HVAC Episodes'],['2','可行性排除窗口']].map(x=>`<div class="card kpi"><b>${x[0]}</b><span>${x[1]}</span></div>`).join('');function bars(id,obj){const m=Math.max(...Object.values(obj));document.querySelector(id).innerHTML=Object.entries(obj).map(([k,v])=>`<div class="bar"><span>${esc(k)}</span><div class="track"><div class="fill" style="width:${100*v/m}%"></div></div><b>${v}</b></div>`).join('')}bars('#status',C);bars('#backends',Object.fromEntries(D.inventory.backends.filter(x=>x.process_count).map(x=>[x.backend_id,x.process_count])));const maps=D.mapping.mappings;document.querySelector('#s').insertAdjacentHTML('beforeend',Object.keys(C).map(x=>`<option>${x}</option>`).join(''));function render(){const q=document.querySelector('#q').value.toLowerCase(),s=document.querySelector('#s').value;const xs=maps.filter(x=>(!s||x.support_status===s)&&(!q||JSON.stringify(x).toLowerCase().includes(q)));document.querySelector('#shown').textContent=`显示 ${xs.length} / 129`;document.querySelector('#rows').innerHTML=xs.map(x=>`<tr><td>${esc(x.responsibility_id)}</td><td>${esc(x.natural_query)}</td><td>${esc(x.family)}</td><td><span class="pill ${x.support_status}">${x.support_status}</span></td><td>${esc(x.candidate_backends.join(', '))}</td><td>${esc(x.missing_capabilities.join(', '))}</td></tr>`).join('')}document.querySelector('#q').oninput=render;document.querySelector('#s').onchange=render;render();const qs=Object.fromEntries(D.public.map(x=>[x.responsibility_id,x.query]));document.querySelector('#episodes').innerHTML=`<p><b>${B.episode_count}</b> 条 / <b>${B.process_count}</b> 个唯一过程 / train ${B.split_counts.train}, dev ${B.split_counts.dev}, test ${B.split_counts.test}</p>`+Object.entries(B.responsibility_episode_counts).map(([id,n])=>`<div class="bar"><span>${esc(qs[id])}</span><div class="track"><div class="fill" style="width:${100*n/B.episode_count}%"></div></div><b>${n}</b></div>`).join('')+`<pre>${esc(JSON.stringify({replay_candidates:D.gate.candidate_process_count,replay_passed:D.gate.passed_count,excluded:D.gate.excluded_count,deterministic:D.gate.all_replays_deterministic,action_sensitive:D.gate.all_processes_action_sensitive,gold_actions_released:D.gate.gold_actions_released},null,2))}</pre>`;</script></body></html>'''


if __name__ == "__main__":
    main()
