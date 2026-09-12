# Backend Casebook LLM Pilot v0

## 结论

这是一次 **case / backend 联调 pilot**，不是可以写进论文的正式 benchmark 结果。15 个 route 均完成了真实 LLM 调用和后端运行。

- 运行完成：15/15（100%）
- LLM 通过：6/15（40%）
- idle 通过：3/15（20%）
- **LLM 通过且 idle 失败：3/15（20%）**
- 排除不支持 strong-D3 claim 的 `d3_citylearn_multi_system` 后，LLM 通过 5/14（35.7%）；其中非平凡胜利仍为 3/14（21.4%）。

所以当前最可靠的数字是 **20% 的非平凡通过率**，而不是 40% 表面通过率。

## 实验设置

- Agent：`deepseek-v4-flash-meituan`
- 每个 case 运行同 seed 的 LLM agent 和 idle baseline
- 运行后端的完整 native horizon
- 大多数 route 最多 8 次 LLM 决策；D0、离散设备和 FDS 在每个决策点调用
- evaluator 检查完整运行轨迹，不是只检查最后一步

case query 和 contract 是当前为了验证后端而手工编写的，尚未完成“来自开源人类需求”的数据集准入，因此不能直接作为论文模型成绩。

## 逐 case 结果

| Route | LLM | Idle | 判断 | LLM tokens | Agent wall |
|---|---:|---:|---|---:|---:|
| `d0_exogenous_context` | 通过 | 失败 | **非平凡胜利** | 20,064 | 5.65 s |
| `d1_sustaingym_fault` | 失败 | 失败 | 策略过度制冷，且 query 的“下午”与场景冷启动不一致 | 45,755 | 5.18 s |
| `d1_citylearn_battery_fault` | 失败 | 失败 | 初始 SoC=0，时窗内无足够富余光伏，目前 contract 可能不可行 | 11,199 | 3.70 s |
| `d1_ev2gym_fault` | 通过 | 失败 | **非平凡胜利** | 14,417 | 4.64 s |
| `d1_discrete_device_fault` | 失败 | 失败 | 设备再次受阻后没有持续重试，是真实的长程策略失败 | 71,349 | 5.53 s |
| `energyplus_iaq` | 失败 | 失败 | 相比 idle 明显改善，但 CO2 峰值 2024 ppm，仍超过 1200 ppm | 9,161 | 3.19 s |
| `wntr_residential_water` | 失败 | 失败 | 阀门一直打开，漏水超限；水量量级也需校准 | 7,856 | 22.42 s |
| `fds_smoke_fire` | 通过 | 通过 | **无区分度**；短轨迹没有形成烟气挑战，且是 prefix replay | 2,553 | 33.15 s |
| `modelica_buildings_aixlib` | 通过 | 通过 | **无区分度**；不加热也保持 20°C | 5,500 | 3.09 s |
| `d3_citylearn_multi_system` | 通过 | 通过 | **无区分度**，且不支持 strong-D3 claim | 17,761 | 3.73 s |
| `d3_citylearn_multibuilding_competition` | 失败 | 失败 | 起始电池 SoC=0，无法用放电解决共享电表超限，case 可能不可行 | 24,777 | 5.50 s |
| `d3_wntr_water_competition` | 通过 | 失败 | **非平凡胜利**，但家庭水量的模拟量级需校准 | 8,356 | 4.27 s |
| `d3_modelica_shared_heat` | 失败 | 失败 | 两类服务都提供了，但房间过热到 32.3°C | 10,200 | 3.12 s |
| `d3_ev2gym_electric_competition` | 失败 | 失败 | 第二辆车 SoC 0.55 < 0.8；后端还有 action/observation mask 命名错误 | 15,297 | 6.48 s |
| `d3_energyplus_shared_ventilation` | 失败 | 失败 | 近失败：A=826 ppm，B=1231 ppm，只超目标 31 ppm | 13,391 | 4.81 s |

## 消耗的记录方式

消耗必须分三层记录，不能把异质量纲相加成一个原始总分。

1. **模型消耗**：LLM calls、prompt/completion/total tokens、provider 返回的 cache 字段、LLM latency。
2. **计算消耗**：backend steps、agent wall time、CPU time、进程 peak RSS。
3. **环境资源消耗**：保留 backend 原生单位，例如用电/EV 充电 `kWh`、费用 `EUR`、用水/漏水 `m³`、供热 `kWh_thermal`、通风量 `m³`、通风指令强度 `fraction_hours`、门打开时间 `seconds`。

本次总计：

- LLM calls：116
- prompt tokens：273,872
- completion tokens：3,764
- total tokens：277,636
- provider 同时返回 `effectiveCachedTokens=199,680`，却返回 `cache_read_tokens=0` / `cached_tokens=0`；字段定义不一致，因此原样保留，不声称“没有 cache”。
- agent wall time 合计：114.46 s
- 其中 LLM latency 合计：76.34 s
- agent CPU time 合计：38.90 s
- idle backend wall time 合计：45.53 s

## 怎么得到可比的消耗分

评测应使用“先成功，后消耗”的字典序：任务失败时，不能因为少用电、少用水就得高分。建议正式报告分开给出：

`(trajectory_success, hard_violations, normalized_excess_consumption, LLM_tokens, compute_seconds)`

对不同后端的原生消耗，只有在每个 case 已冻结同 seed 的“最佳可行策略”和“差参考策略”后，才做归一化：

`normalized_excess = clip((C_agent - C_best_feasible) / (C_bad_reference - C_best_feasible), 0, 1)`

本次只有 idle baseline，还没有经验证的 feasible witness，所以尚不应生成跨后端的“总消耗分”。

## 下一轮准入门

每个 case 进入正式 benchmark 前必须同时满足：

1. query 可追溯到开源真实人类需求；
2. query 是长期责任，不暴露 workflow；
3. 有一条冻结的 witness 轨迹证明 case 可行；
4. idle / 简单脚本不能轻易通过；
5. 外生变化、故障、连续演化或资源竞争真正出现在轨迹中；
6. evaluator 阈值、单位和家庭量级经过校准。
