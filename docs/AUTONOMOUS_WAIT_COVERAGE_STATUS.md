# 自主等待真实覆盖：当前状态

当前代码补充复验：`generated/autonomous_wait_delivery_native_v5/`全部15路线的一次脚本长等待完整时域通过；批处理结束、无失败。`tools/check_backend_delivery.py --native-dir generated/autonomous_wait_delivery_native_v5`独立核对15条真实模型记录及15条脚本原生轨迹后退出0。生产交互相关143项回归通过。这些不新增模型任务成功率，也不宣称旧Episode发布包认证已更新。

## 最新验收（2026-09-10）

15/15正式路由已有真实模型自主等待协议下的完整时域记录，并通过独立`verify`检查。这里的通过仅指记录中的交互、时钟、动作、状态重建和终态，不是长期责任成功率或完整物理认证。其中13条使用自然责任请求，另2条是明确策略的长等待诊断；后者SustainGym的醒后条件选择错误照常保留。

多楼新记录：`generated/autonomous_wait_format_clarity_network_v4/d3_citylearn_multibuilding_competition.json`。同一query与动作示例，在全局格式提示明确禁止Markdown后，24次调用、24个原生步完成；输入262016 tokens，输出2184 tokens，合计264200；HTTP请求体893102字节、响应体21682字节。模型本次自行选择每小时决策，不是运行器强制频率。

原多楼Markdown格式失败未覆盖；本次受限网络中的首个连接失败记录另保留于`generated/autonomous_wait_format_clarity_v4/`，0个环境动作，归为transport。新结果不是同一提示的受控消融，不能把差异全部归因于提示修订。

独立覆盖核对的输入目录：`autonomous_wait_llm_pilot_v1`、`autonomous_wait_coverage_v2`、`autonomous_wait_dedup_v3`、`autonomous_wait_long_feedback_diagnostic_v2`、`autonomous_wait_format_clarity_network_v4`（均在generated下）。可将这些目录传给`tools/audit_real_sessions.py`重复检查。主运行器相关34项回归通过，`git diff --check`通过。总体底座交付还需汇总机制边界和验收入口；没有开始数据构造。

## 此前记录（以下数字和运行状态不代表最新覆盖）

**更新：FDS已完成，双车在同一6MB预算下用去重表示完成96步。自然责任完整终态覆盖为12条；两条长后端另完成明确策略的完整接口诊断，不能混作自然责任成绩；多楼格式失败继续保留。以下旧运行状态是历史。**

`generated/autonomous_wait_dedup_v3/`：双车96次调用、1658703 total_tokens、消息5889501字节，通过独立轨迹检查；事件诊断仍在120秒唤醒并于720秒终止。前次双车90次预算停止与此次96次完整运行的轨迹/策略未做完全相同的受控对照，只能报告同预算新运行完成，不归因全部差异为去重。

`generated/autonomous_wait_long_feedback_diagnostic_v2/`：不跑200MB逐步调用，显式诊断要求先等半段时域，读取指定当前字段后再决定后半段控制。

|后端|模型调用|原生步|输入tokens|输出tokens|醒后条件判断|
|---|---:|---:|---:|---:|---|
|EnergyPlus IAQ|2|287|30044|58|正确：CO2 557.225，选择0.25|
|SustainGym故障|2|288|195008|213|错误：首区13.861°C，应选全0，模型选全-0.05|

两条均通过时钟、动作与回执、全部微状态和终态的独立检查；SustainGym条件判断错误不是后端给错状态，也不能由运行器偷偷纠正。此单次诊断不证明错误由长上下文导致；接口通过与模型条件判断成功必须分列。诊断不是自然长期责任策略评测，不能当成自然任务成功。

## 消耗记录修复

新客户端记录实际HTTP响应体字节数和finish_reason。缺失字节/延迟记录的total返回null，并报告known_subtotal和available_calls，不再把未知当0。旧报告response_bytes_total=0可能只是当时未测，不能据此声称没有输出。provider明确返回length时，动作不执行，归为输出预算问题，而不是模型格式错误。相关26项测试通过。此前已运行进程仍保留旧测量记录，不伪造补填。

CLI控制台现在按decision_finished显示进度，完整native_receipt仍逐条写入并fsync日志，避免长等待时重复几百条相同决策编号刷屏。没有删轨迹。

只使用自主等待主协议的记录，不以旧固定步长实验替代。

结果来源：`autonomous_wait_llm_pilot_v1`、`autonomous_wait_coverage_v2`、`autonomous_wait_long_bounded_v1`（均在generated目录）。事件接口诊断单独存放，不混入自然责任结果。

当前10条已到达原定终态：D0、D1电池、D1离散设备、D1 EV、两条水系统、CityLearn单楼多系统、两条Modelica、共享通风。

其余状态：

- CityLearn多楼：第一条模型回复为Markdown JSON代码块，不符合answer封套。0个原生动作，明确模型格式错误，没有自动修答案或重跑到成功。
- 双车共享变压器：90次调用后达到6MB累计消息预算，原定96步，未完成且不补齐虚构记录。
- SustainGym、EnergyPlus IAQ：各4次有界入口检查后停止，未完成原定时域。
- FDS：仍在运行中。当前进程会话79722；必须轮询同一会话确认结束，不因暂时无输出重启。报告完成后以实际JSON为准。

## 无损反馈去重

此前每个wake的最终状态变化同时出现在外层delta和最后微步delta。现在最后微步以 `observation_source: wake_observation` 引用外层重建状态，不重复存储。早先各微步仍有自己的delta，中间状态/事件没有删除。提示明确：外层delta相对于上次wake，不能应用到某个中间状态。

独立审计同时支持旧表示和新引用表示；引用只能出现在最后一个微步，不能同时携带delta或额外未定义字段。离线对实际CityLearn单楼记录转换后，最终会话从73964字节降到53274字节，完整轨迹核对一致。没有修改原始运行文件；这不是实测新token账单。

本轮已启动运行的进程使用启动时旧表示，不能宣称它们已经享受新表示的成本。新表示的本地验证与旧表示下的真实结果明确分开。没有总体交付完成结论，没有开始数据构造。
