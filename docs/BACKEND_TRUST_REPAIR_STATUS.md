# 可信后端修复记录（2026-09-09）

## 本轮范围

后续的原生机制对照、15路由消息回放及剩余条件见 [长期责任与评估接入前置验证](MECHANISM_AND_INTERACTION_STATUS.md)。

验证 LLM 和动态环境之间的执行边界。这里的“通过”是后端协议验收，**不是 LLM 成功率，也不是任务可行性或论文 claim 已被证明**。15 个正式后端全部保留，BOPTEST 仍是候选。

## 已修复

- 15 路由统一接入动作预检；格式错误在原生模拟器入口前拒绝，允许同一运行中纠正。数字动作不默默接受字符串或布尔值。
- workflow 的设备失效是执行结果：失败有回执，时间、外生事件、故障恢复继续发生。未知原生异常仍停止该运行。
- 未来规则安装与实际触发分开检查；不能用当前设备失效阻止合法的未来规则，也不能在触发时绕过故障。
- workflow 关闭或异常后的 reset 清理生命周期状态；原生 reset 异常不再隐式重试。
- 终态可以继续读取最后的原生观测副本；终态后不允许继续动作。
- D0 构造参数单位转换修正，默认公开时长与原生 12 步对齐为 720 秒。
- SustainGym 构造参数与真实类对齐；原生 288 步 × 300 秒的时长改为 86400 秒，不再错误声明一小时结束。
- 共享供热的 FMI 实验边界跟随公开时长，61 分钟完整运行通过。
- pytest 不再递归收集模拟器自带 Python/GUI 测试；CityLearn 数据扫描的“不得导入模拟器”检查改为独立进程，避免测试顺序污染。

## 原生验收证据

基本交互验收覆盖 **15/15**：重置、动作、错误输入不污染运行、观测副本、多 seed、关闭。FDS 首轮 180 秒超时，单独以 600 秒预算完成，实际约 302 秒；它依然是真实 FDS prefix replay，不是在线续跑。

完整时域验收也已 **15/15 通过**（seed 0，脚本动作）：逐步时钟、实际终止时刻、最后观测可读、终止后动作被拒绝。SustainGym 首轮发现时长不匹配，修正后 288 步复验通过，保留了原失败记录。汇总器重新核对原始轨迹中的步长和终止位置，结果见 `generated/backend_trust_summary_v1.json`。这是一次完整轨迹协议验收，不是所有 seed、所有动作或任务成功率保证。

- 首轮：`generated/backend_trust_repair_v1/`。
- 前六路由加强预检复验：`generated/backend_trust_preflight_v1/`。
- FDS 长预算复验：`generated/backend_trust_fds_v1/`。
- 完整时域逐步轨迹：`generated/backend_trust_horizon_v1/`。
- 完整时域发现问题后的复验：`generated/backend_trust_horizon_fix_v1/`。

这些记录应按各自测试范围阅读；基本交互的 `full_horizon=false` / `mechanism_gate=false` 是刻意保留的边界，不能改成成功来凑总验收。

## 尚不能宣称完成的部分

全仓库回归不是全绿：一次全面运行（先排除两个引用已移除脚本而无法收集的旧测试模块）为 330 passed、10 failed、32 errors。多数错误引用已从精简后端仓库移除的历史数据发布文件；另有旧机制目录要求的验证记录已过期。没有用跳过断言或补造结果把它们变绿。

其中当前后端相关的 WNTR 旧轨迹缺失已通过真实 probe 重建；CityLearn 测试顺序问题已修。历史 query/data release 的恢复属于之前明确暂缓的工作，不作为这轮后端物理能力证据。

修复后复验：D2/D3 WNTR 21 项通过；CityLearn 只读数据扫描 19 项通过。

最后回归：动作预检、workflow、LLM 增量交互、异常边界与 D2 协议共 57 项通过；汇总器防止错误时钟/提前终止/缺失证据被标成通过的 5 项测试通过。D1 EV 严格输入预检后的真实三 seed 复验也通过。`compileall` 和 `git diff --check` 通过。

FDS 烟雾可见度任务是否有效、不同场景的服务可行性、CityLearn multi-system 未证明的跨通道竞争，依然需要各自原生因果验证。接口验收不会替代这些判断。

## 复现

```sh
/opt/anaconda3/bin/python tools/backend_acceptance_runner.py --all --output-dir generated/backend_trust_recheck --timeout 600 --loops 1
/opt/anaconda3/bin/python tools/backend_acceptance_runner.py --all --full-horizon-only --output-dir generated/backend_horizon_recheck --timeout 900 --loops 0
/opt/anaconda3/bin/python -m pytest -q tests/test_trusted_action_preflight.py tests/test_trusted_workflow_boundary.py tests/test_llm_conversation.py
```

验收主命令仍会在缺少机制验证或完整认证时返回非零；应读每个路由的 `status` 和 `checks`，不能把基本交互通过解释为 `READY_FOR_ASTRA`。
