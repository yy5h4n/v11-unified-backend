#!/usr/bin/env python3
"""Build an offline, user-verifiable acceptance package for one golden run."""

from __future__ import annotations

import html
import json
from copy import deepcopy
from pathlib import Path

from build_harness_v2_full_episode_batch import SIMUHOME_DATA
from evaluate_harness_v2_v4_flash import preflight_batch, score_run
from harness_v2.core import EpisodeSpec, Harness
from harness_v2.simuhome_adapter import SimuHomeHarnessAdapter


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "runs" / "harness_v2_golden_acceptance_v1"
SOURCE = OUT / "v4_flash_report.json"


class RecordedPolicy:
    def __init__(self, actions: list[dict]):
        self.actions = deepcopy(actions)
        self.index = 0

    def decide(self, _view: dict) -> dict:
        action = self.actions[self.index]
        self.index += 1
        return deepcopy(action)


def load_config(private: dict) -> dict:
    path = SIMUHOME_DATA / private["source"]["source_file"]
    return json.loads(path.read_text(encoding="utf-8"))["initial_home_config"]


def replay(public: dict, private: dict, actions: list[dict]):
    bootstrap = deepcopy(public["agent_view"])
    spec = EpisodeSpec(public["episode_id"], bootstrap, seed=0, max_decisions=int(bootstrap["horizon_steps"]) + 1)
    backend = SimuHomeHarnessAdapter(
        load_config(private),
        public["visible_room_ids"],
        sample_minutes=int(bootstrap["decision_interval_minutes"]),
    )
    return Harness(backend).run_one(spec, RecordedPolicy(actions))


def main() -> dict:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    source_episode = source["episodes"][0]
    public_rows, private_rows, _ = preflight_batch()
    public = next(row for row in public_rows if row["episode_id"] == source_episode["episode_id"])
    private = private_rows[public["episode_id"]]
    actions = [row["action"] for row in source_episode["public_action_records"] if row["type"] == "action"]
    first = replay(public, private, actions)
    second = replay(public, private, actions)
    score = score_run(first, private, int(public["agent_view"]["decision_interval_minutes"]))

    observations = [row["value"] for row in first.public_trace if row["type"] == "observation"]
    action_results = [row for row in first.private_trace if row["type"] == "action_result" and row["accepted"]]
    applied = [command for row in action_results for command in row["feedback"].get("applied_commands", [])]
    trajectory = []
    for observation in observations:
        device = observation["devices"]["kitchen"]
        trajectory.append({
            "step": observation["step"],
            "virtual_time": observation["virtual_time"],
            "temperature_c": observation["rooms"]["kitchen"]["temperature_c"],
            "control_mode": device["mode"],
            "target_c": device["target_c"],
            "thermal_actuation_active": device["thermal_actuation_active"],
            "effective_power_proxy_w": device["effective_power_proxy_w"],
            "interval_energy_proxy_wh": device["last_interval_energy_proxy_wh"],
            "cumulative_energy_proxy_wh": device["cumulative_energy_proxy_wh"],
            "energy_semantics": device["energy_semantics"],
        })

    committed = sum(item.get("status") == "committed" for item in applied)
    coalesced = sum(item.get("status") == "coalesced" for item in applied)
    replay_match = first.trace_digest == second.trace_digest == source_episode["trace_digest"]
    criteria = {
        "real_model_run_completed": source["smoke_accepted"] is True,
        "all_model_actions_valid": source_episode["calls"] == source_episode["valid_actions"],
        "one_effective_state_change": committed == 1,
        "redundant_writes_coalesced": coalesced == len(actions) - 1,
        "temperature_actuation_and_energy_observed": all(
            row["temperature_c"] is not None
            and row["thermal_actuation_active"] is not None
            and row["effective_power_proxy_w"] is not None
            and row["cumulative_energy_proxy_wh"] is not None
            for row in trajectory
        ),
        "energy_is_explicit_proxy_not_real_metering": all(
            row["energy_semantics"] == "simulator_duty_gated_rated_power_proxy" for row in trajectory
        ),
        "comfort_evaluated": score["band_satisfaction"] is not None,
        "collateral_evaluated": score["realized_collateral"]["availability"] == "available",
        "deterministic_offline_replay": replay_match,
        "nontrivial_temperature_evolution": len({row["temperature_c"] for row in trajectory}) > 1,
    }
    report = {
        "schema_version": "harness-v2-golden-acceptance-1",
        "verdict": "PASS" if all(criteria.values()) else "FAIL_CLOSED",
        "episode_id": public["episode_id"],
        "model": source["model"],
        "query": public["agent_view"]["query"]["text"],
        "responsibility": "Maintain the kitchen thermal condition during the evening window.",
        "initial_observation": public["initial_observation"],
        "model_action_summary": {
            "total_actions": len(actions),
            "first_action": actions[0],
            "committed_state_changes": committed,
            "coalesced_redundant_writes": coalesced,
            "interpretation": "The heat control remained enabled at 22 C, but thermostat duty never activated because kitchen temperature stayed above target; recorded proxy energy is therefore 0 Wh.",
        },
        "score": score,
        "trajectory": trajectory,
        "deterministic_replay_receipt": {
            "source_run_trace_digest": source_episode["trace_digest"],
            "offline_replay_a_trace_digest": first.trace_digest,
            "offline_replay_b_trace_digest": second.trace_digest,
            "all_match": replay_match,
            "action_count": len(actions),
        },
        "acceptance_criteria": criteria,
        "limitations": [
            "Energy is a SimuHome duty-gated rated-power proxy, not measured or calibrated real-world energy.",
            "This Episode is rejected because the kitchen temperature is exactly constant after heat duty stops; the source baseline equals the initial temperature and there is no weather/building-envelope coupling.",
            "This package covers one kitchen thermal Episode only; it does not validate dataset-scale coverage.",
            "Lifecycle and formal safety remain unscorable in this Episode and are not claimed as passed.",
        ],
        "source_report": str(SOURCE),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / "golden_acceptance_report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row[key]))}</td>" for key in (
            "step", "virtual_time", "temperature_c", "control_mode", "target_c",
            "thermal_actuation_active", "effective_power_proxy_w", "interval_energy_proxy_wh",
            "cumulative_energy_proxy_wh",
        )) + "</tr>"
        for row in trajectory
    )
    checks = "".join(f"<li class='{str(ok).lower()}'>{'PASS' if ok else 'FAIL'} — {html.escape(name)}</li>" for name, ok in criteria.items())
    page = f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>Harness V2 Golden Acceptance</title>
<style>body{{font:15px/1.5 system-ui;max-width:1180px;margin:32px auto;padding:0 20px;color:#222}}h1{{margin-bottom:4px}}.pass,.true{{color:#087830}}.fail,.false{{color:#b00020}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border:1px solid #ddd;padding:6px;text-align:right}}th:first-child,td:first-child{{text-align:center}}code{{background:#f4f4f4;padding:2px 5px}}.note{{background:#fff8df;padding:12px;border-left:4px solid #d49b00}}</style>
<h1>Harness V2 单条 Golden Episode 验收</h1><p class='{report['verdict'].lower()}'><b>{report['verdict']}</b> · {html.escape(report['episode_id'])}</p>
<h2>任务</h2><p><b>Query：</b>{html.escape(report['query'])}</p><p><b>责任：</b>傍晚时段维持厨房热环境。</p>
<h2>真实模型动作</h2><p>共 {len(actions)} 次：第 1 次产生有效状态变化，后 {coalesced} 次相同写入被合并。控制设为 heat/22°C，但整个轨迹温度都高于目标，实际热执行器未启动。</p>
<div class='note'><b>能耗解释：</b>累计 0 Wh 是明确观测结果，不是缺失值。这里使用 <code>simulator_duty_gated_rated_power_proxy</code>，不能称作真实测量能耗。</div>
<h2>评估</h2><ul><li>舒适区保持率：{score['band_satisfaction']:.3f}</li><li>相对 no-op 改善：{score['delta_satisfaction']:.6f}</li><li>NRG：{score['formal_nrg']['value']}</li><li>无关房间动作比例：{score['realized_collateral']['value']['fraction']:.3f}</li><li>能耗代理：{score['simulator_energy_proxy_wh']['value']} Wh</li></ul>
<h2>验收门</h2><ul>{checks}</ul>
<h2>确定性重放</h2><p>原运行与两次离线重放 digest 全部一致：<code>{first.trace_digest}</code></p>
<h2>厨房轨迹</h2><table><thead><tr><th>step</th><th>time</th><th>temp °C</th><th>mode</th><th>target °C</th><th>actuating</th><th>power proxy W</th><th>interval Wh</th><th>cumulative Wh</th></tr></thead><tbody>{rows}</tbody></table>
<h2>限制</h2><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in report['limitations'])}</ul></html>"""
    (OUT / "golden_acceptance.html").write_text(page, encoding="utf-8")
    (OUT / "REPRODUCE.md").write_text(
        "# Reproduce the offline golden acceptance\n\n"
        "```bash\n"
        "/Users/shanyingyu/DRPIE/home-design/external/SimuHome/.venv/bin/python "
        "/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/build_golden_acceptance.py\n"
        "```\n\nThis command does not call the model API; it replays the actions sealed in `v4_flash_report.json`.\n",
        encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, allow_nan=False))
