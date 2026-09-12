# 完整闭环运行器（2026-09-10）

当前使用与分层验收见[底座入口](BACKEND_DELIVERY.md)，最新真实自主等待结果见[覆盖状态](AUTONOMOUS_WAIT_COVERAGE_STATUS.md)。下文的固定步长覆盖、连接阻塞和预算请求是历史记录，不是当前进度。当前模型自己决定等待时长，运行器在等待期间保留原生步，不强制每步调用。

**主协议已修复为自主等待，详见 [自主等待修复](AUTONOMOUS_WAIT_REPAIR.md)。以下13条完整运行属于旧固定决策步长证据，不证明自主等待已完成模型验收。当前CLI默认由模型选择等待，内部原生轨迹连续保留。**

## 最新真实恢复验收

当前最新合计：**13/15路由，378次真实调用，3,117,056 total_tokens**。FDS在 `generated/closed_loop_long_20260910_v3/fds_smoke_fire.json` 完成6步/8913 tokens，含真实原生前缀重放；同目录失败的双车初始化记录仍保留，正确运行在runtimefix_v4，不可将失败记录删除或混为模型错误。全部13条重新经过只读验收检查。当前交互相关回归55项通过。剩余两条长会话的完整运行预算已向用户确认，未获答复前不启动约200MB累计消息量级的调用。

再更新：双车共享变压器已完成96次真实模型调用、1,272,329 total_tokens，来源为 `generated/closed_loop_long_runtimefix_20260910_v4/`。此前使用基础Python启动时暴露了虚拟环境选择bug：不能用 `Path.resolve()` 后的解释器路径判断环境相同，venv可软链接同一基础二进制但有不同依赖。入口现在总是明确进入选定解释器一次，worker标志防止递归。失败记录保留在v3目录，0次模型调用，归为基建错误。

新增只读验收命令 `tools/audit_real_sessions.py <结果目录> ...`，检查逐步模型动作、实际动作、历史动作一致，重建公开状态，核对时钟/声明终态/关闭/usage；不计算责任成功。缺少路由则非零退出。该检查基于记录证据，不是服务提供者身份认证，也不是模拟器所有物理情景证明。解释器选择、记录验收和生产会话相关14项测试通过；测试使用本地合成fixture，不依赖生成目录或付费API。

后续覆盖更新：恢复目录与 `generated/closed_loop_coverage_20260910_v2/` 合计 **11/15路由、276次调用、1,835,814 total_tokens**。逐条复核模型文本解出的动作与原生回执动作相同，调用数与执行步数相同，最后回执done、声明时域到达、无损重建与关闭均通过，每次调用都有usage。此处仍不报告任务成功率。

|新增路由|真实调用数|total_tokens|
|---|---:|---:|
|家庭水系统|6|12261|
|共享水系统|6|13191|
|CityLearn多系统|24|163983|
|CityLearn多楼|24|217424|
|EV充电器故障|43|332349|
|Modelica双房间|60|444356|
|Modelica共享热泵|60|410041|
|离散设备故障|10|84575|

剩余真实完整验收：SustainGym故障、EnergyPlus IAQ、双车共享变压器、FDS。前三条更长，需要继续检验真实模型上下文和累计消耗；不得用缩短时域替代。FDS需要真实原生前缀重放，不能用旧脚本轨迹冒充新的模型动作执行。当前没有底座交付完成结论，也未进入数据构造。

`generated/closed_loop_recovery_20260910_v1/` 保存恢复后的三个完整原生闭环：D0为12次调用/31948 total_tokens；D1电池为8次/17938；D3共享通风为23次/107748。共43次/157634 tokens，均实际到达声明时域终点、完成无损状态重建并关闭后端。没有格式修复、重试、历史截断或替代动作。provider usage每次均返回；token总量不是货币账单，缓存计费另需服务价格说明。

这是三条路由的接口完整性证据，不是全部15条完成，也不是任务成功率。电池轨迹的SOC从0逐渐升至约0.363，没有始终维持在0.5附近；`task_success`仍为null，不能拿原生终态替代责任判分。此前连接错误报告继续保留，以下旧阻塞描述只对应恢复前运行。

主入口为 `tools/run_trusted_llm_session.py`，使用 `unified_compiler.llm_backend_session`，适用于全部15条原生路由的数字、数组和对象动作。EV 自动选择已固定的专用 Python。旧 casebook runner 不作为本运行器的验收结果。

例子（会调用现有第三方模型接口，运行前需要外部发送授权与 AIGC_API_KEY）：

```sh
/opt/anaconda3/bin/python tools/run_trusted_llm_session.py \
  --route d1_citylearn_battery_fault \
  --query 'Keep the battery charge near 50 percent throughout this episode.' \
  --example-action 0.0 \
  --max-calls 8 --max-message-bytes 300000 --max-total-message-bytes 1000000 \
  --output-dir generated/my_battery_closed_loop
```

示例动作只用于说明格式，不自动执行。模型自行选择每一步动作，实际到达原生终态才记为 `native_terminal_reached`，另记录是否达到公开声明时长。`task_success` 固定为 null：本阶段尚未接责任 evaluator。

每次请求前保存请求意图，模型返回后保存响应，再保存动作意图、实际原生回执，最后保存退出状态。JSONL 每次落盘并 fsync；正常结束另存完整 JSON 报告。已有证据不覆盖、不自动恢复运行，避免中断后将可能已经执行的动作重复提交。日志可供检查，但不宣称可自动断点续跑。不要把本地含原始回执的日志作为模型输入。

错误分为模型格式、模型动作、传输、后端/回执基础设施、预算、清理错误。没有隐式重试、自动格式修复、动作裁剪、替代动作或历史截断。实际模型调用期间模拟时钟暂停，每个接受的动作推进该后端规定的间隔。

同时修正原生异常归因：即使原生调用抛出 ValueError/TypeError，也归为运行时基础设施错误并隔离该 episode；只有执行前的纯输入校验才能判定为模型动作错误。不得根据异常类名字把已经进入模拟器的失败归咎于模型。

预算同时约束单次消息字节、累计消息字节和调用次数；失败尝试也计入调用与发送字节预算。字节数不是 token 估算，缺失 provider usage 不是零消费。

## 本轮真实尝试与限制

三条尝试（D0、D1 电池、D3 通风）共最多43次预算，实际只发起了3次连接尝试，全部在第一步模型返回前报传输错误，后端动作数均为0。证据为 `generated/closed_loop_pilot_v1/`。无成功模型响应、无返回 usage，不能宣称零账单或模型失败。

对同一已授权 API 域名做无密钥 DNS/TLS/HEAD 检查：DNS 可解析，Python TLS 报 unexpected EOF，curl 报 connection reset by peer。没有关闭证书验证、换域名或绕过权限。完整真实模型长运行仍待连接恢复后验证。

## 同期本地验证

`tools/probe_native_future_isolation.py` 在 D0 与四条 D1 的真实后端中，只改第5步才生效的外生安排，执行相同动作。五条均在初始到第4步保持完整公开消息相同，第5步后才出现物理结果差异，证据为 `generated/native_future_isolation_v1/`。这是单seed、一个起始时刻的原生非干扰实验，不是所有场景认证。

CityLearn 单楼/多楼 reset 在原生 warm-up 后也按最后完成位置提供受控状态；明确区分下时段输入与上时段输出。预热能耗不是本episode动作消耗，不能混入episode费用。新完整时域与消息证据分别为 `generated/citylearn_reset_state_v2/`、`generated/citylearn_reset_messages_v2/`，旧初始观测证据保留但不当作最新版。

当前仍不能标记底座交付完成：原生观测字段契约、更多非干扰覆盖、长会话运行、模型完整闭环与统一发布验收都有未完成项。

本轮回归：CityLearn/初始完成态/运行器与回执预算相关组合 39 项通过；运行器错误归因/公开回执/公共接口/workflow/D2 协议组合 37 项通过。这两组有重叠，不相加作为独立测试数量。五条未来隔离原生实验全部通过各自限定检查。网络故障仍是未解决的外部条件，未通过更换域名或降低 TLS 安全要求规避。
