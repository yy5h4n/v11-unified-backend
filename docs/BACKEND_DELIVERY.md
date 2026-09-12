# 当前底座的使用与验收入口

本阶段本机LLM交互底座已完成逐项核对，见[交付核对表](BACKEND_COMPLETION_AUDIT.md)。当前严格入口：`/opt/anaconda3/bin/python tools/local_preflight.py --strict --profile autonomous-interaction`。旧发布认证以`--profile legacy-release`单列且保持原有失败行为，两者不混用。

## 使用什么

生产交互入口：`tools/run_trusted_llm_session.py`。它调用`unified_compiler.llm_backend_session.run_session`，默认启用自主等待；EV路线自动进入专用解释器。调用示例与授权条件见[运行器说明](TRUSTED_SESSION_RUNNER.md)。本机原生依赖不随代码分发，路径要求见`tools/local_preflight.py`和`TEST_COMMANDS.json`。

模型获得完整初始观测、原生动作schema、时间/单位/公开字段说明和调用预算。随后收到无损增量、执行回执以及等待期间各原生步的公开变化。回复格式：`<answer>{"action": 原生动作, "wait": 等待对象}</answer>`。等待支持时长、模拟截止时间、公开字段等值事件；不是任意自然语言事件表达式。连续控制保持显式设定，离散命令只执行一次。模拟器内部步长不等于模型调用频率。

运行中不修模型答案、不隐式重试、不裁剪动作、不截断重建历史、不自动重置后续跑。输出截断、格式错误、非法动作、传输失败、原生异常和预算耗尽分别记录。完整JSONL保留原生回执，不应直接作为模型输入；公共反馈已经过独立处理。

## 三层检查不能混用

1. **当前保存记录的离线检查**：`/opt/anaconda3/bin/python tools/check_backend_delivery.py`（加`--json`查看详情）。重新核对15条完整真实模型交互、抽样原生来源、可用的轨迹不变量，以及D0/D1有限未来隔离对照。明确区分13条自然责任请求与2条明确策略诊断，不计算任务成功率。
2. **当前代码实际执行**：`/opt/anaconda3/bin/python tools/check_autonomous_wait.py --route ROUTE --full-horizon --output-dir generated/NEW_DIRECTORY`。零外部API，用脚本一次长等待跑完整原生时域，验证所有微步与终态。不能称作真实LLM能力测试。输出目录必须选新的，不能覆盖旧记录。
3. **旧发布包严格验收**：`tools/local_preflight.py --strict`仍因改造前发布包与当前实现不一致而失败。未更改旧记录、放宽原检查或冒称新离线检查等同完整发布认证。当前本机可用性证据与可迁移发布包验收是不同范围；最终交付不能遗漏此差异。

当前全后端原生长等待复验输出目录为`generated/autonomous_wait_delivery_native_v5/`；只有最终报告存在且`wait_conformance_passed=true`才算对应路线完成，运行中的目录不能视为通过。

本次已实际完成15/15，批处理正常退出；联合检查随后退出0。所有路线均是一次脚本决策推进原定完整时域，零外部API调用。它补充真实模型交互证据，不能替代或混作模型任务成绩。

联合检查命令：`/opt/anaconda3/bin/python tools/check_backend_delivery.py --native-dir generated/autonomous_wait_delivery_native_v5`。它进一步重新解码脚本决策、重建所有微步、核对时钟/终態/关闭，并要求每路线一次脚本决策、零外部API。即使保存报告自称通过，轨迹不一致仍失败；缺少任何正式路线也失败。脚本身份与缺失provider usage只在明确的脚本原生检查中按证据类型处理，真实模型验收仍要求真实模型记录和usage。

## 能力边界

完整15路线表及原生机制对照见[机制与交互](MECHANISM_AND_INTERACTION_STATUS.md)，字段口径见`unified_compiler/observation_contracts.py`。

- FDS使用真实原生前缀重放，不支持在线续跑；已验证温度影响，未验证烟雾可见度任务。
- CityLearn单楼多系统不计作已验证跨通道竞争。
- 双车变压器和CityLearn多楼电表提供共享约束，不宣称原生限流分配；供热和通风另有物理分配竞争证据。
- WNTR流量代理不是已经校准的家庭需求满足率。
- 观测来源是限定字段/状态的核对；未来隔离是D0/D1各一个有限前缀对照，不是全状态无泄漏证明。
- BOPTEST仍是候选，不计入15条正式路由。

## 当前结果的正确解释

真实记录已覆盖15条完整交互，但SustainGym明确策略诊断中模型选择了错误分支；终态成功不能掩盖它。费用记录中的tokens、HTTP体字节、推理延迟分别报告；缺失usage不是零账单，也未按未经核实的服务单价估算金额。详情见[最新覆盖](AUTONOMOUS_WAIT_COVERAGE_STATUS.md)。

本阶段不验收query来源、长期责任evaluator、任务可行性或正式模型成功率。这些是用户明确暂缓的数据/评估层工作，不由底座记录替代。底座交付后按用户额度条件停止，未开始数据构造。
