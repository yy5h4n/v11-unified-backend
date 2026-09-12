# EV竞争情景证据核对

## 结论

后端有已记录的双车共享容量效应，但默认seed 0完整交互回放没有双车重叠。能力级探针与任务级情景覆盖不是同一证据。

## 已检查的证据

- `generated/autonomous_wait_delivery_native_v5/d3_ev2gym_electric_competition.json`：96步，默认seed 0，一辆车，未观察双端口同时连接。
- `generated/autonomous_wait_dedup_v3/d3_ev2gym_electric_competition.json`、`generated/closed_loop_long_runtimefix_20260910_v4/d3_ev2gym_electric_competition.json`和`generated/native_session_path_v1/d3_ev2gym_electric_competition.json`的96步记录也未观察双车重叠。
- `generated/fixed_peer_mechanisms_v1/d3_ev2gym_electric_competition.json`：同一23400s前缀中两车连接；仅端口0充电时11kW，总容量15kW，余量约4kW；两端口充电时各11kW，总22kW，余量约-7kW。固定端口功率没有被自动削减，证明共享约束效应，不是自动物理分配。
- `tools/probe_fixed_peer_mechanisms.py`明确采用seed 3及26步前缀，与默认seed 0交互记录不同。

## 对构造流程的要求

每个任务必须显式绑定scenario seed/config及本情景机制证据。不可将seed 3的机制探针结果移植为seed 0任务已经测到竞争的证明。可用seed 3作为已知开发情景继续做全时域服务可行性，但不能因为单步过载就认定整个需求存在必要权衡或一定可解。

本轮只读证据，不改后端默认seed、不伪造车辆、不改旧认证。后续应在任务构造层选择/记录情景并检查完整责任的可行性与反例；query仍与情景分离。
