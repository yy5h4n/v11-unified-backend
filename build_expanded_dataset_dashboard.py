#!/usr/bin/env python3
"""Build a self-contained HTML dashboard for the expanded dataset release."""
from __future__ import annotations

import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RELEASE = ROOT / "generated" / "expanded_responsibility_release_v1"
PUBLIC = RELEASE / "episodes_public.jsonl"
REPORT = RELEASE / "build_report.json"
OUTPUT = RELEASE / "dataset_dashboard.html"


def main() -> Path:
    episodes = [json.loads(line) for line in PUBLIC.read_text(encoding="utf-8").splitlines() if line]
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    payload = json.dumps(episodes, ensure_ascii=False).replace("</", "<\\/")
    report_payload = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.replace("__EPISODES__", payload).replace("__REPORT__", report_payload)
    OUTPUT.write_text(page, encoding="utf-8")
    return OUTPUT


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="description" content="Responsibility Episode 数据集本地看板">
  <title>Responsibility Episode Dataset · v1</title>
  <style>
    :root {
      --ink:#17202a; --muted:#667085; --paper:#f4f1ea; --card:#fffdf8;
      --line:#ddd7ca; --blue:#195c78; --teal:#27877d; --amber:#d99b42;
      --red:#a74c44; --shadow:0 18px 46px rgba(47,42,31,.09);
    }
    *{box-sizing:border-box} body{margin:0;color:var(--ink);background:var(--paper);font:14px/1.55 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    body:before{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(circle at 10% 0%,rgba(39,135,125,.10),transparent 28rem),radial-gradient(circle at 95% 10%,rgba(217,155,66,.12),transparent 24rem)}
    main{width:min(1180px,calc(100% - 32px));margin:auto;padding:34px 0 64px;position:relative}
    header{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:24px}
    .eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--teal);font-weight:800}
    h1{font-family:Georgia,"Times New Roman",serif;font-weight:500;font-size:clamp(32px,5vw,58px);line-height:1.02;margin:8px 0 12px;max-width:760px}
    .lede{font-size:16px;color:var(--muted);max-width:720px;margin:0}
    .badge{white-space:nowrap;background:#fff3d9;color:#795117;border:1px solid #ead3a4;padding:8px 12px;border-radius:999px;font-size:12px;font-weight:750}
    .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:26px 0}
    .stat,.panel{background:rgba(255,253,248,.94);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}
    .stat{padding:18px}.stat strong{display:block;font:500 34px/1 Georgia,serif;margin-bottom:7px}.stat span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}
    .grid{display:grid;grid-template-columns:1.15fr .85fr;gap:16px;margin-bottom:16px}.panel{padding:22px}
    .panel h2{font:500 23px/1.2 Georgia,serif;margin:0 0 6px}.sub{margin:0 0 18px;color:var(--muted);font-size:13px}
    .stratum{padding:14px 0;border-top:1px solid var(--line)}.stratum:first-of-type{border-top:0}.stratum-head{display:flex;justify-content:space-between;gap:16px;margin-bottom:8px}.stratum b{font-size:14px}.stratum small{color:var(--muted)}
    .track{height:8px;border-radius:8px;background:#ece7dc;overflow:hidden}.fill{height:100%;border-radius:8px}.fill.energy{width:90.91%;background:var(--blue)}.fill.simu{width:9.09%;background:var(--amber)}
    .funnel{display:grid;grid-template-columns:repeat(4,1fr);align-items:center;gap:8px;margin-top:22px}.stage{text-align:center;position:relative}.stage:not(:last-child):after{content:"→";position:absolute;right:-9px;top:8px;color:#aaa08f}.stage strong{font:500 27px/1 Georgia,serif;display:block}.stage span{font-size:11px;color:var(--muted)}
    .responsibilities{display:grid;gap:10px}.responsibility{border:1px solid var(--line);border-radius:14px;padding:14px;background:#fff}.resp-top{display:flex;justify-content:space-between;gap:14px}.resp-count{font:500 22px/1 Georgia,serif;color:var(--blue)}.responsibility code{font-size:11px;color:var(--muted)}
    .browser{margin-top:16px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 12px}.toolbar input,.toolbar select{height:40px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:0 12px;color:var(--ink)}.toolbar input{flex:1;min-width:220px}.toolbar select{min-width:240px}.count{margin-left:auto;align-self:center;color:var(--muted);font-variant-numeric:tabular-nums}
    .table-wrap{overflow:auto;border:1px solid var(--line);border-radius:14px;background:#fff}table{width:100%;border-collapse:collapse;min-width:820px}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid #eee9df}th{position:sticky;top:0;background:#faf7f0;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.07em}tbody tr{cursor:pointer}tbody tr:hover{background:#f4faf8}td code{font-size:11px}.query{font-weight:650}.backend-pill{display:inline-flex;padding:4px 8px;border-radius:999px;font-size:11px;background:#e8f1f5;color:var(--blue)}.backend-pill.simu{background:#fff1d8;color:#8a5a13}
    dialog{width:min(780px,calc(100% - 28px));max-height:82vh;border:1px solid var(--line);border-radius:18px;padding:0;box-shadow:0 28px 90px rgba(0,0,0,.25)}dialog::backdrop{background:rgba(20,25,28,.58)}.dialog-head{display:flex;justify-content:space-between;align-items:center;padding:18px 20px;border-bottom:1px solid var(--line)}.dialog-head h3{margin:0;font:500 20px Georgia,serif}.close{border:0;background:#eee8dc;border-radius:9px;width:34px;height:34px;font-size:19px;cursor:pointer}pre{margin:0;padding:20px;overflow:auto;background:#18212a;color:#e7efe9;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
    .notes{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}.note{border-left:3px solid var(--teal);padding:4px 0 4px 13px;color:var(--muted)}.note b{display:block;color:var(--ink);margin-bottom:3px}.actions{display:flex;gap:10px;margin-top:20px}.actions a{color:#fff;background:var(--blue);padding:9px 13px;border-radius:10px;text-decoration:none;font-weight:700}.actions a.secondary{background:#e6e0d5;color:var(--ink)}
    footer{margin-top:24px;color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:14px}
    @media(max-width:780px){header{display:block}.badge{display:inline-block;margin-top:16px}.stats{grid-template-columns:1fr 1fr}.grid,.notes{grid-template-columns:1fr}.funnel{grid-template-columns:1fr 1fr}.stage:nth-child(2):after{display:none}.count{width:100%;margin-left:0}footer{display:block}}
  </style>
</head>
<body>
<main>
  <header>
    <div><div class="eyebrow">Responsibility Episode Dataset · v1</div><h1>从自然责任到可重放的物理过程</h1><p class="lede">当前内部预览集包含三种责任、两个物理后端。每个 Episode 只绑定一个主要责任，并保留独立合同、重放证书与来源血缘。</p></div>
    <span class="badge">Internal preview · provisional</span>
  </header>

  <section class="stats" aria-label="数据摘要">
    <div class="stat"><strong id="episodes-total">40</strong><span>Episodes</span></div>
    <div class="stat"><strong id="responsibilities-total">3</strong><span>责任</span></div>
    <div class="stat"><strong>2</strong><span>后端分层</span></div>
    <div class="stat"><strong>18/18</strong><span>聚焦测试</span></div>
  </section>

  <section class="grid">
    <article class="panel">
      <h2>后端 Fidelity 分层</h2><p class="sub">两种后端保留各自的物理可信度，不计算跨层汇总性能。</p>
      <div class="stratum"><div class="stratum-head"><div><b>EnergyPlus</b><br><small>建筑热模拟 · generic comfort</small></div><strong>30</strong></div><div class="track"><div class="fill energy" style="width:75%"></div></div></div>
      <div class="stratum"><div class="stratum-head"><div><b>SimuHome</b><br><small>合成房间状态聚合器 · kitchen + multi-room evening</small></div><strong>10</strong></div><div class="track"><div class="fill simu" style="width:25%"></div></div></div>
    </article>
    <article class="panel">
      <h2>SimuHome 构造漏斗</h2><p class="sub">先扫描真实源配置，再认证并按责任相关物理轨迹去重。</p>
      <div class="funnel"><div class="stage"><strong>600</strong><span>源配置</span></div><div class="stage"><strong>27</strong><span>初始多房间机会</span></div><div class="stage"><strong>10</strong><span>因果可控候选</span></div><div class="stage"><strong>7</strong><span>过程去重后</span></div></div>
    </article>
  </section>

  <section class="grid">
    <article class="panel"><h2>责任构成</h2><p class="sub">同一责任下的 Episodes 来自不同的责任相关物理过程，而非配置乘法。</p><div class="responsibilities" id="responsibility-cards"></div></article>
    <article class="panel"><h2>发布约束</h2><p class="sub">数据可用于内部 benchmark 检查，暂不等价于公开发布。</p><div class="notes">
      <div class="note"><b>无 Gold 泄漏</b>公开字段版不包含 witness 动作轨迹。</div>
      <div class="note"><b>无跨层总分</b>EnergyPlus 与 SimuHome 分开报告。</div>
      <div class="note"><b>无数据切分</b>所有记录均为 split=none。</div>
      <div class="note"><b>授权待确认</b>SimuHome 未发现许可证文件。</div>
    </div><div class="actions"><a href="episodes_public.jsonl">打开 Public JSONL</a><a class="secondary" href="build_report.json">查看 Build Report</a></div></article>
  </section>

  <section class="panel browser">
    <h2>Episode 浏览器</h2><p class="sub">筛选责任或搜索 Episode ID；点击任意一行查看完整公开记录。</p>
    <div class="toolbar"><select id="filter" aria-label="按责任筛选"><option value="all">全部责任</option></select><input id="search" type="search" placeholder="搜索 Episode ID、Query 或 Backend" aria-label="搜索 Episode"><span class="count" id="visible-count"></span></div>
    <div class="table-wrap"><table><thead><tr><th>Episode</th><th>自然 Query</th><th>Backend</th><th>初始状态</th><th>Horizon</th><th>Status</th></tr></thead><tbody id="episode-rows"></tbody></table></div>
  </section>

  <footer><span>Responsibility Episode Dataset · generated from the current combined release</span><span>40 Episodes · 3 responsibilities · internal preview</span></footer>
</main>

<dialog id="detail"><div class="dialog-head"><h3 id="detail-title">Episode detail</h3><button class="close" id="close" aria-label="关闭">×</button></div><pre id="detail-json"></pre></dialog>
<script id="episode-data" type="application/json">__EPISODES__</script>
<script id="report-data" type="application/json">__REPORT__</script>
<script>
  const episodes=JSON.parse(document.getElementById('episode-data').textContent);
  const report=JSON.parse(document.getElementById('report-data').textContent);
  const names={
    'rd_37104b57370a':'室内温度保持舒适',
    'rd_split_016151c7c038':'傍晚保持厨房温暖',
    'rd_split_b8457e559b4d':'傍晚保持多个房间舒适温暖'
  };
  const backend=e=>e.episode_id.startsWith('eplus_')?'EnergyPlus':'SimuHome';
  const initial=e=>{
    const o=e.initial_observation||{};
    const t=o.zone_temperature_c ?? o.temperature_c;
    if(t===undefined){
      const rooms=Object.entries(o);
      const temps=rooms.map(([,v])=>v.temperature_c).filter(Number.isFinite);
      if(temps.length) return `${rooms.length} rooms · ${Math.min(...temps).toFixed(2)}–${Math.max(...temps).toFixed(2)}°C · ${rooms[0][1].virtual_time.slice(11,16)}`;
      return '—';
    }
    const extra=o.outdoor_temperature_c!==undefined?` · 室外 ${o.outdoor_temperature_c.toFixed(1)}°C`:o.virtual_time?` · ${o.virtual_time.slice(11,16)}`:'';
    return `${Number(t).toFixed(2)}°C${extra}`;
  };
  document.getElementById('episodes-total').textContent=report.episode_count;
  document.getElementById('responsibilities-total').textContent=report.responsibility_count;
  const grouped=Object.entries(report.responsibility_episode_counts);
  const cards=document.getElementById('responsibility-cards');
  grouped.forEach(([id,count])=>{
    cards.insertAdjacentHTML('beforeend',`<div class="responsibility"><div class="resp-top"><div><div class="query">${names[id]||id}</div><code>${id}</code></div><span class="resp-count">${count}</span></div></div>`);
    const option=document.createElement('option');option.value=id;option.textContent=`${names[id]||id} (${count})`;document.getElementById('filter').appendChild(option);
  });
  const tbody=document.getElementById('episode-rows'), filter=document.getElementById('filter'), search=document.getElementById('search');
  function render(){
    const q=search.value.trim().toLowerCase();
    const rows=episodes.filter(e=>(filter.value==='all'||e.responsibility_id===filter.value)&&(!q||`${e.episode_id} ${e.natural_query} ${backend(e)}`.toLowerCase().includes(q)));
    tbody.innerHTML='';
    rows.forEach(e=>{
      const tr=document.createElement('tr');
      const b=backend(e);
      tr.innerHTML=`<td><code>${e.episode_id}</code></td><td class="query">${e.natural_query}</td><td><span class="backend-pill ${b==='SimuHome'?'simu':''}">${b}</span></td><td>${initial(e)}</td><td>${e.horizon_steps} × ${e.observation_interval_minutes} min</td><td>${e.statuses.physical_status}</td>`;
      tr.addEventListener('click',()=>show(e));tbody.appendChild(tr);
    });
    document.getElementById('visible-count').textContent=`显示 ${rows.length} / ${episodes.length}`;
  }
  function show(e){document.getElementById('detail-title').textContent=e.episode_id;document.getElementById('detail-json').textContent=JSON.stringify(e,null,2);document.getElementById('detail').showModal()}
  filter.addEventListener('change',render);search.addEventListener('input',render);document.getElementById('close').addEventListener('click',()=>document.getElementById('detail').close());render();
</script>
</body>
</html>'''


if __name__ == "__main__":
    print(main())
