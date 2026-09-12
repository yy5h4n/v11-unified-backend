# 长期责任与评估接入前置验证（2026-09-09）

范围：验证动态机制真实存在，以及模型动作与原生后端间的传输边界。这里不评判用户责任是否满足，不报告模型任务成功率。15 条正式路由全部保留，BOPTEST 仍单列候选。

## 原生机制证据

| 路由 | 本轮原生对照结果 | 边界 |
|---|---|---|
| d0_exogenous_context | 相同动作，有/无外生事件时家庭上下文及门状态不同 | 一组事件对照 |
| d1_discrete_device_fault | 相同关门动作，卡住时原生门状态不同 | 非全部设备故障覆盖 |
| d1_sustaingym_fault | 相同冷却动作，故障改变原生区域温度 | 非供热能力证明 |
| d1_citylearn_battery_fault | 相同充电请求，故障改变原生 SOC | 非任务可行性证明 |
| d1_ev2gym_fault | 相同功率请求，故障改变原生充电交付 | 非完整恢复策略证明 |
| energyplus_iaq | 持续通风输入改变连续 CO2 轨迹 | 一组输入对照 |
| wntr_residential_water | 阀门输入改变连续水箱状态 | 服务量尚未校准为家庭需求 |
| fds_smoke_fire | 门开关改变原生温度轨迹 | 未证明烟雾可见度任务有效；仍为 prefix replay |
| modelica_buildings_aixlib | 散热器输入改变连续室温轨迹 | 一组输入对照 |
| d3_modelica_shared_heat | 固定一路请求，另一需求增加使其实际供热减少 | 两方向各减少 600 W |
| d3_energyplus_shared_ventilation | 固定一路请求，另一需求增加使其实际风量减少 | 物理分配竞争 |
| d3_wntr_water_competition | 固定一路阀门，另一路开启后其实际流量减少 | 首步无差异，第二步出现水箱耦合；保留首轮空结果 |
| d3_ev2gym_electric_competition | 固定一路功率未减少，但共享变压器余量由 4 到 -7 kW | 共享约束，不是原生限流分配 |
| d3_citylearn_multibuilding_competition | 固定楼电池 SOC 不变，共享电表预算余量改变 | 对原生能耗施加的外部预算，不是原生功率裁剪 |
| d3_citylearn_multi_system | 尚未观测到固定通道的交付被另一通道影响 | 不计作已验证的资源竞争 |

D3 对照要求初始状态、时刻和固定通道输入一致，只改变另一通道；不能再用总能耗或整个状态发生变化证明竞争。旧 campaign 的 D3 判断已改为使用这类证据；普通动作敏感性也不再自动获得机制认证。机制观察不自动令 `episode_ready` 成立。

原始回执：`generated/fixed_peer_mechanisms_v1/`；WNTR 延迟复验：`generated/fixed_peer_mechanisms_delayed_v1/`；D0–D2：`generated/exogenous_dynamics_mechanisms_v1/`；SustainGym 修正健康对照后的复验：`generated/exogenous_dynamics_mechanisms_fix_v1/`。健康对照只在诊断中、第一步前移除故障，不是向 agent 开放修改故障的操作。

## 统一交互

新增 `unified_compiler/native_action_conversation.py`：统一 `<answer>{"action": NATIVE_ACTION}</answer>`，原生动作仍可为数字、数组或对象，不强行转换。提示包含真实动作 schema、格式示例、时间语义、等待/保持差异与请求量/实际量差异。严格拒绝额外字段、重复键、非有限数字及包裹说明文字；回执动作不匹配时不更新历史。

仍使用初始完整观测 + 后续无损增量 + 执行回执。15 条原生完整时域记录通过格式往返与逐步状态重建，见 `generated/native_conversation_audit_v1/`。这是原生记录的协议回放，不是 LLM 控制运行。旧 pilot 尚未全部迁移到这条桥接层。

上下文成本仍待验证：SustainGym 288 步结束时序列化会话约 1.17 MB。这不是 token 数，也不是累计 API 消耗；不能靠静默截断历史掩盖上下文增长。

真实模型两步协议测试脚本为 `tools/probe_llm_native_protocol.py`：明确指定动作的格式/传输测试，不测自主规划，禁用隐式重试，保留 usage 与实际回执。首次外部 API 发送被安全审批拦截；用户随后明确授权，授权后的结果如下，先前连接失败不计为模型失败。

### 授权后的真实模型协议测试

`deepseek-v4-flash-meituan` 经现有 `https://aigc.sankuai.com/v1/openai/native` 接口完成 15/15 路由、每路两步，总计 30 次调用，未重试。30 次输出均符合指定动作、成功进入原生后端，并通过回执后状态重建。EV 使用已固定的专用 Python 环境，各路独立进程运行。完整请求、响应、usage 和原生回执保存在 `generated/llm_native_protocol_authorized_v1/`。

- provider 返回的 prompt tokens：47,182；completion tokens：947；total tokens：48,129。30 次均有 usage。
- API 调用延迟累计 17.623 秒（各次相加，不是并行测试总耗时，也不包含原生模拟耗时）。
- 请求字节数累计 195,963；响应字节数未采集，不把旧汇总器的缺省零解释成实际零。
- 缓存字段口径不一致：`effectiveCachedTokens` 合计 768，而 `cache_read_tokens`/`cached_tokens` 为零；保留原始字段，不据此估算账单。
- FDS 两步运行约 43.77 秒，包含原生 prefix replay，不能归为模型思考耗时。

这是给定动作的两步协议测试，不是自主决策、故障恢复、完整长期运行或任务成功率。它不证明其他模型也能通过。已完成授权的 30 次调用，不自动扩展测试预算。

## 接入前还需要

- 两步真实模型传输测试已完成；更长运行仍需单独验证，原生运行错误、传输错误和模型格式错误分开。
- 明确长时域上下文预算及超限处理，验证公开观测不泄露未来私有安排。
- 对每个拟接入的长期责任定义观察量、持续/截止条件与不可满足状态；另行验证任务可行性和 evaluator。当前机制对照不替代它们。
- CityLearn 单楼竞争与 FDS 可见度仍不得作为已有能力；供水服务代理需校准后才能解释为家庭责任满足。
