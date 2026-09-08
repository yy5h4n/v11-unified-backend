# V11 独立审计：首轮证据报告
日期：2026-09-06。审计范围：本地 V11 源码、冻结规范及选定的只读复现。没有修改项目代码或重生成发布证据。本报告不是全路由、全回归验收。

项目根目录：/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler
下文代码路径均相对此根目录。

## 1. Executive verdict

结论：有真实物理后端和实质性的工程基础，但尚不能作为已验收的统一 benchmark 生成系统。发现可复现的评分输入验证缺陷、终止状态处理缺陷和公共协议不一致。严重程度应按“能否污染实际评分链路”判断，不能仅按错误看起来是否吓人。

基础 evaluator 会将 NaN 和不等长轨迹判为 hard_feasible=True，这是评分组件的高优先级问题。不过在排除依赖目录后的 Python 调用搜索中，该函数只见于自身、导出和测试，未找到它被当前生产 runner 调用的证据。因此不能说已有成功率已经因该漏洞失真，也不能把漏洞外推给 [conformance_v1/evaluator.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/conformance_v1/evaluator.py) 或 harness 的其他 evaluator。

真实复核：CityLearn 单楼耦合、多楼耦合、单楼公共 Agent 探针均通过 --check；Modelica D3 真实 FMU 探针通过 --check，10 项相关测试通过。13 项轻量测试通过。build_catalog() 通过当前证据校验，但这不等于当前解释器可运行全部后端，更不等于 Episode 有效。

## 2. Claim matrix

状态说明：VERIFIED 只覆盖本次明确执行的探针范围；PARTIALLY_VERIFIED 表示只核验部分闭环行为；EVIDENCE_PENDING 表示本次缺少独立实际执行，不代表历史证据为假。D3 route 原生性不自动证明语义、初态、信息可解性和 evaluator 正确。

| 路由 | 后端 | 持久闭环 / replay / 机制的本轮证据 | 本轮状态 | 起点路径 |
|---|---|---|---|---|
| D0 外生上下文 | 自定义离散事件运行时 | 公共接口动作与观察测试通过；未穷举事件取消/冲突 | PARTIALLY_VERIFIED | [unified_compiler/adapters/d0_exogenous_context.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/adapters/d0_exogenous_context.py) |
| D1 HVAC | SustainGym + fault wrapper | catalog 校验通过；未重跑该真实路由 | EVIDENCE_PENDING | [unified_compiler/adapters/d1_fault_mechanism.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/adapters/d1_fault_mechanism.py) |
| D1 电池 | CityLearn + fault wrapper | catalog 校验通过；未逐 fault mode 重跑 | EVIDENCE_PENDING | [unified_compiler/adapters/citylearn_battery_fault.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/adapters/citylearn_battery_fault.py) |
| D1 EV | EV2Gym claim + fault wrapper | catalog 校验通过；未重跑 D1 | EVIDENCE_PENDING | [unified_compiler/adapters/ev2gym_fault.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/adapters/ev2gym_fault.py) |
| D1 离散设备 | Workflow 状态机 | 在线动作测试通过；发现 dt/终止缺陷 | PARTIALLY_VERIFIED | [unified_compiler/adapters/d1_discrete_device_fault.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/adapters/d1_discrete_device_fault.py)；[unified_compiler/agent_interface.py:276](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/agent_interface.py:276) |
| D2 IAQ | EnergyPlus | catalog 校验通过；原生重跑未执行 | EVIDENCE_PENDING | d2_humidity_air_quality_adapter.py |
| D2 水网 | WNTR | catalog 校验通过；原生重跑未执行 | EVIDENCE_PENDING | [unified_compiler/adapters/d2_wntr.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/adapters/d2_wntr.py) |
| D2 烟火 | FDS | catalog 校验通过；原生重跑未执行 | EVIDENCE_PENDING | d2_fds_adapter.py |
| D2 热过程 | Modelica Buildings/AixLib | 本次 D3 FMU 验证不能代替本 D2 路由验证 | EVIDENCE_PENDING | d2_modelica_buildings_aixlib_adapter.py |
| D3 单楼多系统 | CityLearn | coupling 与 Agent 两个真实探针 --check 通过 | VERIFIED（探针范围） | d3_citylearn_coupling_adapter.py；probe_d3_citylearn_coupling.py；probe_d3_agent_interface.py |
| D3 多楼竞争 | CityLearn + 外加共享电表约束 | 真实 probe --check 通过；不将外部约束称作 native clipping | VERIFIED（探针范围） | d3_citylearn_multibuilding_adapter.py；probe_d3_citylearn_multibuilding.py |
| D3 水竞争 | WNTR | catalog 校验通过；未重跑写文件的 probe | EVIDENCE_PENDING | d3_wntr_water_competition_adapter.py；probe_d3_wntr_water_competition.py |
| D3 共享热源 | Modelica FMI2 CoSimulation | probe --check 和 10 项真实 FMU 测试通过，含双向资源分配干预 | VERIFIED（探针范围） | d3_modelica_shared_heat_adapter.py；probe_d3_modelica_shared_heat.py |
| D3 电竞争 | EV2Gym | 专用环境 --seed 3 的真实探针通过；默认 CLI seed=23 失败；跨系统机制测试仍偏弱 | PARTIALLY_VERIFIED | d3_ev2gym_electric_competition_adapter.py；generated/d3_ev2gym_electric_competition_evidence.json |
| D3 通风竞争 | EnergyPlus EMS | catalog 校验通过；读代码发现原始时间与公共适配时间口径不同 | EVIDENCE_PENDING | d3_energyplus_shared_ventilation_adapter.py；probe_d3_energyplus_shared_ventilation.py |

D0 和 D1 离散设备使用状态机符合其离散机制用途；不能把它们宣传成连续物理引擎，也不能因它们是状态机就说整个项目是假后端。其余未实际重跑路线不授予 VERIFIED。

## 3. Critical findings

### F1：基础评分器接受 NaN，并将其判为成功（P1，评分组件）

位置：[unified_compiler/evaluator.py:13](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/evaluator.py:13)、54、74、114。
复现：temperature=[float('nan')]；同时配置 HARD_INVARIANT between 18–25 与 TERMINAL_GOAL >=22。evaluate_trajectory 返回 hard_feasible=True、hard_violation_count=0。
原因：NaN 的大小比较均为 false；between 落入 0.0 分支，max(0.0, NaN) 也掩盖错误。
影响：任何直接用此组件评分且未前置拒绝非有限值的调用方，都可能把坏数据当成功。Agent observation 层有有限值检查是有效防线，但不能替代 evaluator 对直接输入的验证。
最小修复：入口拒绝状态和 clause 参数中的 NaN/Inf；测试每个运算符。坏数据应报告 invalid，而非普通失败或成功。
边界：没有证明当前已发布成绩实际经过这一函数。

### F2：同一个 evaluator 不校验跨变量轨迹长度（P1，评分完整性）

位置：[unified_compiler/evaluator.py:18](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/evaluator.py:18)、114。
复现：x=[0]、y=[0,1]，分别约束 x<=1 与 y终值>=1。evaluate_trajectory 返回成功；访问同一对象的 horizon 却抛 ragged trajectory ValueError。
原因：长度校验只放在 horizon 属性中，评分入口从不调用它。Contract/Evaluator 也没有传入期望 horizon 的接口。
影响：丢帧、截断或不同步的变量可能被各自评分，缺失的危险时刻不被计入。
最小修复：强制统一长度，绑定预期时长、采样时间和合法终止原因；缺记录 fail closed。单个正常值被接受本身不是漏洞，未满足约定 horizon 却被接受才是漏洞。

### F3：D1 离散公共接口在终止后伪造时间推进，dt=NaN 也可进入（P1/P2，取决于 runner 是否挡住）

位置：[unified_compiler/agent_interface.py:276](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/agent_interface.py:276)–301；[harness_v2/workflow_backend.py:399](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/harness_v2/workflow_backend.py:399)。
复现：make_agent_backend('d1_discrete_device_fault') reset 后，step({'kind':'act','commands':[]},float('nan')) 被接受并推进。
随后 wait 直到600秒终止，再 act([])，回执时间变成660秒，done=True，但底层 state_digest 不变。
原因：无终止锁；时间不增长时直接补 tick。NaN 绕过 abs(dt-tick)>阈值。
影响：公共时间不再对应物理过程，终止后的 action 仍可能先进入 execute_atomic；依赖此时钟判定期限的上层有风险。严格在 done 时停止的 runner 不触发终止后路径。
最小修复：记录 terminal/truncated，后续 step 在执行动作前拒绝；统一有限正数 dt 验证；时钟来自实际 backend，不能自行补出转移。

### F4：统一接口尚未统一到可无差别调用（P2）

位置：[unified_compiler/d2_closed_loop.py:152](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/d2_closed_loop.py:152)；[unified_compiler/agent_interface.py:189](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/unified_compiler/agent_interface.py:189)、276、349附近。
D2 step(action,dt_seconds) 必须传 dt，其他路由可省略；错误类型和 receipt 的额外字段也不同。工厂部分路由静默丢弃 kwargs，配置拼写错误可能无声失效。
最小修复：明确统一签名、每路由 cadence 元数据、统一异常和拒绝未知参数；用同一契约测试遍历全部 factory route，而非只分别测原生 adapter。

### F5a：EV2Gym 默认复现入口与证据使用条件不一致（P2，证据可复现性）

位置：probe_d3_ev2gym_electric_competition.py:46、140。Python probe 默认 seed=3，CLI 默认 seed=23；生成的证据没有顶层 seed/horizon 参数记录。
专用 v10/.runtime/venv/bin/python 执行默认 --check 也失败，实际诊断为 seed did not produce two simultaneous native EVs，并返回 EVIDENCE_PENDING。因此不能仅用缺依赖解释此次失败。
最小修复：证据绑定 seed、horizon 和精确复现命令；函数/CLI 默认参数统一；缺少双车同时接入时保持 pending，不重选种子后冒充原命令成功。专用环境显式 --seed 3 后 --check exit 0，确认可复现历史证据。此问题不等于证明 native coupling 不存在。

### F5：说明与冻结规范在数据构造规则上存在实质分歧（发布前阻断）

位置：GPT6_PROJECT_AUDIT_PROMPT.md 的 D 节；DATASET_CONSTRUCTION_PIPELINE_V1.md:345–416。
Prompt 要求 membership 不由 solver 结果决定，witness 仅作 release gate；冻结规范明确允许 Pi_cert 构造 oracle 对 opportunity strata 的判定，且明确声明 zero experimental-baseline-driven membership，而非 zero oracle-driven membership。
两者不能直接当成同一个规范。可以用“原始候选 pool”与“最终 strata”区分，但必须写成版本化的确定规则，不能自行解释后宣布 conformance。

另一个适用边界：冻结规范保留 certified-no-op 与 boundary，Prompt 的非平凡性要求只应作用于 achievement 正例，不应删除等待正确的对照层。
Query v2.4 禁止在 canonical query 加设备/新 deadline；Prompt 允许 Episode surface query 填授权 slots。需显式区分 canonical query 与 Episode context/surface，保留 lineage，不能把后生成文本冒充冻结原 query。

## 4. False-positive risks

- 现有 evaluator 7 项测试全部通过，但 F1/F2 照样复现。test_evaluator_is_gold_action_free 主要检查结构，不是对评分抗攻击能力的保证。
- [tests/test_d2_closed_loop_protocol.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/tests/test_d2_closed_loop_protocol.py) 用 FakePhysicalRoute，只能证明 normalizer 的局部行为。不能据此说四种物理引擎在线可用。
- [tests/test_dynamic_mechanism_catalog.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/tests/test_dynamic_mechanism_catalog.py) 调 build_catalog 后断言 verified。它确实校验部分证据 hash，并非纯文件存在检查；但未重新执行物理过程。
- “73 passed”未在本轮完整复现，不作为审计结论。部分测试读 JSON，部分测试真调 runtime，必须逐项分开计数。
- probe_d3_energyplus_shared_ventilation.py:35–52、96–103 在 --check 判断前执行 build()，build 写 trace 且 prepare_working_model 写模型；WNTR probe 也在 build 中写 trace。--check 不等于只读。本轮未执行这两个会改项目产物的探针。
- AgentReceiptAdapter 回完整 observation；增量协议实现在 evaluate_workflow_v4_flash_v10.py 的 LLM history 层。这是可以成立的分层，但不能声称所有 backend.step 已返回 delta。
- HarnessD1AgentBackend 将 outcome.private_feedback 放进公共 info（agent_interface.py:299）。本轮见到的 workflow 内容主要是状态/应用结果，尚未证明具体未来信息泄漏；但这是应删除或白名单化的越界通道。不能仅凭字段名断言已经作弊。
- D3 EnergyPlus reset 已运行一个原生 tick，原始首 step 时间为1800s（probe:56–59）；公共 wrapper 从0开始按900累加，因为只读 observation 内时间字段。相对时钟可能合理，但必须显式定义 offset，否则 deadline 对齐有风险。
- Token 主指标应保持 provider output tokens；V8–V10 审计明确缺字段按0并记录 presence。统计时必须区分 missing 与真实0，否则跨 provider 均值会偏低。本轮未执行真实 API 请求。
- 本轮无证据表明存在蓄意恶意代码。“恶性 bug”指可能污染结论的缺陷，不能推断开发者动机。

## 5. Episode readiness 与完整生成方案

已有：schema/compiler/registry 基础、多个 adapter、独立 conformance 包、历史 workflow Episode/runner、冻结语义协议。尚未证明：新 D0–D3 的统一 released Episode 构建链已完成。旧 workflow 数据可运行不证明新 route 数据集已就绪。

建议按冻结规范版本组织以下可执行流水线：

1. 真实证据、人类双人标注、授权与责任 admission；保留未支持/有争议项。
2. 冻结 canonical responsibility、semantic query、Contract、公开 profile 字段；所有阈值有来源。禁止根据 backend 能力改目标。
3. 冻结机会谓词、public-history 分组、Pi_auth_pub/Pi_cert、seed、diversity/split 策略。先解决 F5 的规范分歧。
4. 采样 native 初态与隐变量，在将服务该 Episode 的同一持久实例 reset 后、任何 Agent 动作前抓 initial_observation。
5. 按冻结规则构造 candidate pool/strata；区分 positive、boundary、certified-no-op、unsupported。搜索失败不是无解证明。
6. 搜索 private witness；在同 backend 独立 reset/replay 验证可行性，另用共享公开历史、不同隐藏未来的情景组验证 public policy 信息可解性。全知动作序列不足以证明这一点。
7. 对 success_spec 跑定向负例：违反安全一次、错过期限、删除帧、NaN、提前终止、无效动作；至少一种破坏应由对应 clause 拒绝。
8. 按预授权 slots 绑定公开 context；最后 LLM 只作等义改写，经双向语义复核和人审，不增添目标或动作配方。
9. 输出公共 query/initial_observation/action schema；私有 success_spec/witness/hidden_scenario；provenance 绑定 backend、模型、版本、seed、输入和 reset 状态。文件存在与 hash 不是语义验收。
10. 按语义及物理 genealogy 切分、去重、配额 QA 后冻结发布，再运行独立 Agent。通知指标单列，不混回当前 primary success。

不存在唯一 correct_state：末态满足只是部分条件，过程安全与时间义务也必须满足；witness.final_observation 只能是一个例子。

分层 admission：
- D0：外生事件真在中途出现；Agent 无法改 schedule；公开信息足以应对；取消/冲突及等待对照有效。
- D1：相同指令在健康/故障下改变 evaluator 相关结果；区分动作无效与传感器误差；恢复若被声称必须验证。
- D2：真实持久动力学、足够可控性/时间分辨率；初态与整段轨迹受约束；no-op/对照改变评价。
- D3：至少两路可控、资源会绑定；固定其他条件干预A影响B，并验证反向或明确单向机制；至少一条可行公共策略。
以上各层均要检查确定性或明确随机性、信息不泄漏、语义一致和评价负例。维护型/no-op strata 不强行要求“无动作失败”。

## 6. Linux/codelab risks

CODELAB_PORTABILITY.md 已明确 Mac 运行物不能直接迁移：EnergyPlus Darwin arm64、FDS x86_64/Rosetta、OpenModelica Mach-O、WNTR CPython Darwin 扩展。CityLearn 和 EV2Gym 还依赖 v5/v10 sibling 资产及专用环境。
应按 OS/arch/Python ABI 重建 runtime manifest、固定依赖/模型 hash，在 Linux 重跑证据；本次 Mac 通过不授予 portable 状态。未进行资产体积盘点，不能声称完整迁移审计完成。

## 7. Recommended next actions

1. 修 F1/F2/F3，并加入独立攻击输入；明确实际 production evaluator，禁止新生成器误接基础 evaluator。
2. 解决 F5 的规范版本与命名；统一公共签名、终止、时间 offset、公开 receipt 白名单。
3. 让 probe --check 真正只读，原生临时输出使用隔离临时目录；每条 route 独立 runtime manifest。
4. 对全部15条 route 跑同一公共契约和真实机制干预；包括换动作、非法动作、终止、reset、cleanup。
5. 先完成少量端到端 Episode 的 query→初态→witness→攻击 evaluator→无泄漏发布闭环，再扩大样本。

### 执行记录
使用 PYTHONDONTWRITEBYTECODE=1；pytest 使用 -p no:cacheprovider。
/opt/anaconda3/bin/python：
- [tests/test_dynamic_mechanism_catalog.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/tests/test_dynamic_mechanism_catalog.py)、[tests/test_d2_closed_loop_protocol.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/tests/test_d2_closed_loop_protocol.py)、test_agent_interface.py 中 D0/D1离散两项：6 passed。
- [tests/test_evaluator.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/tests/test_evaluator.py)：7 passed。
- [tests/test_d3_modelica_shared_heat.py](/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler/tests/test_d3_modelica_shared_heat.py)：10 passed。
- probe_d3_citylearn_coupling.py --check：exit 0。
- probe_d3_agent_interface.py --check：exit 0。
- probe_d3_citylearn_multibuilding.py --check：exit 0。
- probe_d3_modelica_shared_heat.py --check：exit 0。
- probe_d3_ev2gym_electric_competition.py --check：exit 1。诊断为该解释器缺 gymnasium，非已证明的耦合实现错误。

专用环境 ../v10_diversity_aware_compiler/.runtime/venv/bin/python：
- probe_d3_ev2gym_electric_competition.py --check：exit 1；seed=23 没有产生两台同时在线的原生 EV。
- probe_d3_ev2gym_electric_competition.py --check --seed 3：exit 0。MPLCONFIGDIR 指向 /private/tmp/v11-audit-mpl；未安装依赖。

EV2Gym 机制证据的额外限制：probe:91–95 的 bidirectional_intervention 只比较总功率和 overload，未直接比较 B 的状态/有效动作/条件可行域。这比共享容量耦合的完整验收弱；应固定 B 请求干预 A，直接给出 B 的条件可行功率或失败结果，并做反向对照。不能只凭这个布尔值称已排除“两个独立负载相加”。

