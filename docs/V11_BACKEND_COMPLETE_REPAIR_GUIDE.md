# V11 后端完整修复指南：Luna 实施，Astra 统一验收

任务日期：2026-09-06。授权来自用户本轮要求：将后端四类缺口整体修复，Luna 完整交付后由 Astra 检查；禁止逐文件交付、逐文件等待验收。

## 0. 交付目标与协作方式

目标是在当前 Mac 主机及其真实已安装 runtime 上，完成全部 15 条 D0–D3 路由的生产批处理准备：公共接口一致、原生过程可持续推进、生命周期和故障隔离可靠、并发有界且无串扰、证据与最终代码一致。这里的生产是本机离线批量运行后端，不是部署到真实家庭或 Linux 服务。

编码模型固定 GPT-5.6 Luna。Luna 拥有整个实施批次，可自己迭代、测试、调试、修复，不能每改一个文件请求 root 审查。Astra 只在收到完整交付包后开始一次整体审查。若审查失败，汇总一份缺陷清单整体退回，不做逐文件实时指挥。

不得把“写了测试”“已有 JSON 是 true”“启动成功”“13/15 路由通过”称作任务完成。全部 15 路由都必须有本次真实执行的逐项证据。确有无法解决的原生能力阻断时，完成所有独立可做工作，再提交 BLOCKED 和精确证据；禁止用假模拟或放宽验收阈值隐藏阻断。

原项目（最终部署位置）：
/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler

Luna 工作副本：
/private/tmp/v11-production-repair/prototypes/v11_unified_process_compiler

只编辑工作副本。它是独立的可写副本，不是原目录软链接。父目录的 sibling 链接只供读取依赖，不能改写 v5/v10 等兄弟项目。不能在其他目录安装依赖或改全局环境而绕开工具权限。

## 1. 必须先读的上下文

- GPT6_PROJECT_AUDIT_PROMPT.md
- /Users/shanyingyu/Documents/ChatGPT/[iclr] 家居/V11_PROJECT_AUDIT_2026-09-06.md
- /Users/shanyingyu/Documents/ChatGPT/[iclr] 家居/V11_FIX_ACCEPTANCE_2026-09-06.md
- README.md、DESIGN.md、CODELAB_PORTABILITY.md
- unified_compiler/agent_interface.py、unified_compiler/d2_closed_loop.py
- build_dynamic_mechanism_catalog.py、build_claim_backend_catalog.py
- probe_agent_interface_d0_d1.py、probe_d2_closed_loop.py、probe_d3_*.py 和对应 tests

上轮已修复：评分非有限数值/等长验证、D1 dt/终止保护/真实时间、公共 private_feedback 删除、EV2Gym CLI seed=3。保留这些修复和回归测试，不得回退。

冻结构造规范 DATASET_CONSTRUCTION_PIPELINE_V1.md 及其 conformance release tuple 不在本任务修改范围。Episode Query/责任 semantics、notification 指标、LLM runner 计费逻辑也不在本批改动范围。不要借本任务生成正式 Episode 或更改冻结责任定义。

## 2. 15 条路由：一个都不能省略

| 层级 | 公共 route id | 原生/运行时起点 |
|---|---|---|
| D0 | d0_exogenous_context | unified_compiler/adapters/d0_exogenous_context.py；自定义外生事件状态机，如实标注 |
| D1 | d1_sustaingym_fault | unified_compiler/adapters/d1_fault_mechanism.py |
| D1 | d1_citylearn_battery_fault | unified_compiler/adapters/citylearn_battery_fault.py |
| D1 | d1_ev2gym_fault | unified_compiler/adapters/ev2gym_fault.py |
| D1 | d1_discrete_device_fault | unified_compiler/adapters/d1_discrete_device_fault.py；Workflow 状态机 |
| D2 | energyplus_iaq | d2_humidity_air_quality_adapter.py |
| D2 | wntr_residential_water | unified_compiler/adapters/d2_wntr.py |
| D2 | fds_smoke_fire | d2_fds_adapter.py |
| D2 | modelica_buildings_aixlib | d2_modelica_buildings_aixlib_adapter.py |
| D3 | d3_citylearn_multi_system | d3_citylearn_coupling_adapter.py |
| D3 | d3_citylearn_multibuilding_competition | d3_citylearn_multibuilding_adapter.py |
| D3 | d3_wntr_water_competition | d3_wntr_water_competition_adapter.py |
| D3 | d3_modelica_shared_heat | d3_modelica_shared_heat_adapter.py |
| D3 | d3_ev2gym_electric_competition | d3_ev2gym_electric_competition_adapter.py |
| D3 | d3_energyplus_shared_ventilation | d3_energyplus_shared_ventilation_adapter.py |

现存 aliases 必须保留或有明确兼容迁移，不能删除路由来达成“全通过”。测试 inventory 从生产 registry 导出且与以上集合对比，防止漏跑。

## 3. 工作包 A：统一公共协议

建立一个明确版本的公共协议及集中 route metadata；避免继续在两套 wrapper 复制不同校验逻辑。原生 action 载荷可以各不相同，必须用 legal_actions/schema 描述；统一的是调用与回执语法，不是强制统一物理动作。

统一方法：reset(seed=0)、observe()、legal_actions()、step(action, dt_seconds=None)、close()。每条 route 公开默认 cadence、可接受 dt、原生时间单位、horizon/termination 定义和并发运行方式。省略 dt 有明确合法默认，不能某些路由 TypeError。禁止默默丢弃未知 kwargs。

公共回执固定：time_seconds、delta_t_seconds（step）、observation、action、done、terminated、truncated、info；可选诊断要在统一命名空间内。异常应统一为 action/protocol/lifecycle/runtime/timeout 类别；通过别名或继承兼容已有调用，不要靠捕获所有 Exception 假装一致。

### 时间

- 公共时间采用 Episode reset 后经过秒数，reset=0。若原生 reset 已预热/推进，则记录可审计的 native_time_origin，并用原生时间减 offset；不能忽略原生时间自己每次加固定 tick。
- time 和 delta 来自同一原生转移；delta=本次time−上次time，不能 raw delta 与 envelope time 不一致。
- 必须支持 Workflow 一次 wait 跨多 tick，回执真实反映累计经过时间。
- 对 NaN、Inf、bool、字符串、0、负数、非法 cadence 在原生动作/时间变更前拒绝。
- 到 horizon、原生终止、截断分别定义；done 必须与两者一致。终止后先拒绝再执行，不能执行后再报错。

### 生命周期与公开数据

- 未 reset/已 close 时 observe/legal_actions/step 行为统一；close 幂等；close 后必须 reset 才能继续。
- reset 失败或 step 运行时失败后不能继续使用半初始化/半推进状态。标记故障，要求新 reset；不要把错误当成功 transition。
- observation 必须等于最新后端公开状态，深拷贝，调用方改返回值不能改后端内部数据。
- info 采用公开白名单，禁止未来 schedule、完整 fault 配置、private_feedback、隐藏状态或 witness。provenance 留给私有诊断或明确授权的公开字段。
- dt/seed/action/config 验证必须在有状态原生调用之前。非法操作前后比对原生时钟、状态/可观察摘要，而非只断言抛异常。

## 4. 工作包 B：修正真实运行和并发隔离

每条 route 必须沿同一原生状态轨迹持续推进，证明不是重新初始化/重跑完整历史。不能用 object id 不变作唯一证据：wrapper id 不变不证明 solver 没重启。记录真实 session/process identity、原生时间进度、启动次数和动作切换结果。

### 已知必须处理的 FDS 问题

d2_fds_adapter.py 当前 step 每次从 t=0 重跑 accumulated schedule，且 _ensure_interactive_run 使用 interactive_{len}_{time} 固定目录并 rmtree。probe_d2_closed_loop.py 还将 full_history_real_backend_replay 记成 interactive verified。

必须调查并采用 FDS 支持的真实在线控制或原生 checkpoint/restart continuation。后者若可行，需证明读取实际 checkpoint 并仅推进新增区间，同时公开 execution mode/成本，不能把它称作同一进程持续运行。严格 in-memory online 的状态与 native-checkpoint continuation 应分开声明。若目标协议仍要求一个原生实例，checkpoint 不得冒充满足该条；给出明确未满足状态。

禁止把 Python 手写温度/烟雾更新、插值查表、预运行所有动作树、仅改标签当作修复。若官方 runtime 无合适边界，提交具体尝试和阻断，整体不得宣称15/15 online-ready。

### 文件与全局状态隔离

- 每个 Episode/session 拥有唯一临时工作目录，实例退出清理自己拥有的目录。相同 seed/动作也必须不同目录；不能互删别人目录。
- EnergyPlus working model、native output、FDS decks/checkpoint、WNTR IPC、Modelica实例名/回调、EV配置文件逐项审查。
- 不在并发 worker 中修改共享 os.environ、cwd、sys.path 或全局 RNG 后假设隔离。对不具备线程安全保证的 backend 使用进程隔离。
- 公开支持的是哪一种并发模型；可以通过集中 executor 提供 process-isolated Episodes。不要强求30个重型 solver在一个进程/线程运行，也不要把线程不安全伪装成可用。
- 有界任务队列、资源 admission、worker 数量上限，出错只影响当前 Episode。不要无限 fork 或不受控使用全机资源。
- 底层运行超时、失败、worker退出后，必须 join/reap/close 文件句柄，停止相关子进程，避免僵尸、回调悬挂与外部进程泄漏。
- 为本项目新增/明确可配置 timeout，测试采用短 timeout 注入；不能靠一次设置数小时 timeout 掩盖挂死。

### 机制证据

D0 验证中途到达/取消/冲突、Agent不能改外生schedule；D1 验证各声称 fault mode 的健康/故障对比和声称的恢复；D2 验证动作敏感的原生连续状态；D3 对至少两路通道做同prefix双向干预。

EV2Gym 不能仅用 p(A+B)>p(A) 和 overload 增大作为强耦合全部证明。固定B请求干预A，直接证明B的有效输出、状态、可行功率/约束或可达结果发生改变；反向也验证或明确原生单向机制。可以证明原生共享约束下的条件可行域，但必须用实际约束函数/数据与反事实计算，不能新增人为裁剪后称 native。

CityLearn多楼电表阈值保持 benchmark-added、native_clipping=false；真实solver和基准附加约束分开。EnergyPlus EMS添加的模型逻辑也如实溯源。

## 5. 工作包 C：全路线实际验收及稳定性

构建可重跑的 acceptance runner，逐 route 使用正确解释器/runtime，而不是统一拿系统 Python 硬跑。每个原生测试用单独子进程与明确超时，收集结构化结果、stderr、exit code、耗时。缺依赖为失败/pending，不得静默 skip 后算通过。

### 每条 route 必跑

1. 正常 reset → observe/legal_actions → 至少两次不同动作 → 终止 → close。
2. 同种子重置：初态与相同动作前缀一致，容差预先按物理变量定；公开可控随机性另记录，不要求不同seed一定不同。
3. 同prefix中途换动作的原生因果对比，不能只比较 action 字段。
4. 非法 action/config/seed/dt 参数矩阵，在原生状态推进前拒绝。
5. 终止/截断后拒绝、close两次、失败reset、close后reset、返回对象深拷贝。
6. dependency 不可用、worker退出、原生抛错、超时注入：必须 fail closed，下一次健康实例可正常运行。

### 持续与资源测试下限

- 每路由至少3个明确记录的seed/情景，以原生合法动作跑完整声明 horizon；不能为测试方便把生产 horizon 缩成2步。极短原生模型另说明生产适用时长不足，不能伪称长时验证。
- 每路由至少10次 reset→短轨迹→close 循环，并检查进程/线程/FD是否回收。
- 每路由并发至少2个独立 Episode（采用声明的安全执行方式），与串行控制轨迹逐一比较；使用不同动作并覆盖相同seed以捕捉串扰。
- 混合路由调度至少30个排队任务，按资源预算限制实际solver并发；验证结果归属、失败隔离、队列完成、无无限等待。30任务不等于声称30个重solver可同时运行。
- 全套至少一次持续运行 >=10分钟，报告真实 wall time、累计原生模拟时间、Episode数量、失败率、吞吐、最大并发、RSS/FD/子进程趋势。稳定性不是仅睡10分钟；持续实际执行工作。
- 给出测量基线和正常波动容差。warmup后残留自有子进程应为0，FD/线程不单调积累；不能任意选很大阈值掩盖增长。第三方缓存增长单列解释。

这些只是本机批处理验收下限，报告实测容量与边界，不能称无限负载保证。

## 6. 工作包 D：证据重建与可复现命令

1. 梳理 source→probe→trace/gate→catalog→test 的实际依赖图，生成可执行 rebuild 顺序。源码定稿后再跑最终证据；证据生成后又改相关源码则重新执行依赖项。
2. 所有相关失效 gate 必须由当前真实执行重建，禁止只替换旧 JSON 的 sha/verified。历史冻结规范及历史审计不重写。
3. --check 必须真只读：在临时目录运行并与已有证据比较，不能先覆盖 trace 再比较。为写入证据另设显式 build/output。文件比较前后 hash 证明被检查产物未变。
4. builder/probe 必须失败返回非零或顶层passed=false且调用runner强制非零；不能输出pending却exit0让自动流水线当成功。
5. runtime manifest 绑定实际解释器、ABI、OS/arch、库/二进制版本和hash、资产/model/source、seed/horizon、动作/初态与容差、执行mode、精确重现命令。不要只记录一个看似正确却并未使用的venv路径。
6. 区分代码可移植性与本次Mac已验证。Linux不在本批执行范围，不得写Linux-ready。
7. 并发写证据只允许主进程汇总，原子写，不能多个worker覆盖同一路径。程序输出不得含凭据。
8. 最终 catalog 与 fresh evidence 一致；旧证据缺失或hash过期须 fail closed。篡改文件/hash、删模型、改变seed输入时验收工具必须拒绝，恢复后原证据重新通过。
9. release/claim catalog 不应把 pending route 继承其他route的通过状态，也不能把部分物理probe通过升级成所有机制和生产就绪。

专用环境线索：/opt/anaconda3/bin/python 可运行部分CityLearn和Modelica；EV2Gym使用原项目父目录 v10_diversity_aware_compiler/.runtime/venv/bin/python；WNTR当前vendored扩展有Python ABI要求；EnergyPlus/FDS/Modelica要发现本地真实二进制。以实际probe为准，不能照抄这段就宣称环境验证。

## 7. 安全实施、变更管理和工作副本

- 工作副本开始前记录 source/probe/tests/docs/config/evidence 的baseline哈希与状态；大runtime/第三方模型保持只读语义，不提交缓存变动。
- 排查并避免导入模块因绝对路径反向写原项目。所有写输出必须确认落在工作副本或独有tmp。父目录sibling依赖不得修改。
- 不要删除原项目、旧证据或其他实验目录。工作副本中可清理仅本session创建的临时目录。
- 按模块整理实现，自行选择合理内部结构；目标是把四个工作包完成，不是机械只改审计列出的文件。
- 可以合理重构公共协议/测试/探针；不可用删除断言、放宽严重错误检测、skip真实测试或改模型为简化替代品来通过。
- 阶段间可以自己运行小测试，避免最后才发现问题。用户禁止的是逐文件交给Astra，不是禁止Luna自测。

## 8. 必须一次性交付的文件和机器结果

在工作副本中提交：

1. BACKEND_PRODUCTION_REPAIR_REPORT.md：最终架构/行为、根因、修改范围、兼容性、明确剩余阻断。
2. BACKEND_PUBLIC_PROTOCOL.md：稳定API/错误/时间/lifecycle/并发/资源支持合同。
3. 统一生产 route registry/metadata 与运行时解析配置；保持原有调用兼容。
4. 可重复运行的一条 acceptance 命令和一条 rebuild/check 命令；支持 --route 单项调试但最终跑all。
5. generated 下独立版本目录中的 acceptance.json、15行route matrix、每次实际run的日志/trace/provenance、stability报告和资源统计。不要仅在Markdown写PASS。
6. 当前 fresh gate/catalog，或明确列出为何不能重建的阻断；不能留下stale后宣称全完成。
7. CHANGED_FILES.json：仅交付文件的相对路径、原hash（新文件null）、新hash、用途；注明删除（原则上避免）和证据输出。不要把venv、pycache、runtime stdout垃圾列为代码交付。
8. TEST_COMMANDS.json：每条命令cwd、解释器、参数、exit code、真实耗时、输出路径；标明fake/unit/native/soak类别以及skip数。

汇总状态只允许：READY_FOR_ASTRA（15条完整实际通过且证据fresh）、BLOCKED（列硬阻断与已完成项）。不要给自己最终生产批准，批准由Astra给出。

## 9. Astra 的一次整体验收（Luna 先完成再开始）

Astra 将检查完整diff和机器结果，并独立执行：

- inventory15/15与公共factory一致；无路由被隐藏，无skip算成功；运行时环境真实。
- 一次全部路由公共接口/机制验收；代表性真实资源循环/并发及故障隔离复测；验证长时记录与原生日志真实性。
- dt/time/reset/close/error边界，尤其EnergyPlus origin、Workflow长wait、D2默认dt、FDS是否prefix replay。
- D3真实cross-intervention而非仅总量相加。
- --check前后持久文件未变；当前source hashes与gate一致；篡改/缺依赖fail closed。
- 旧评分/Workflow/CityLearn/Modelica回归不退化。

全部审查完再给一次 ACCEPT 或一份完整 REJECT 清单。通过后root批量写回原项目，核对原hash避免覆盖新改动，再在部署路径执行最终命令验证路径相关性。部署路径变化引起的证据差异必须真实重跑，不能重写历史hash骗过检查。

最终给用户的结论必须直接回答：本机哪些路由可以在什么并发/时长范围内生产运行；若仍未满足15/15，明确回答“未满足”，列剩余阻断，不用测试总数掩盖。

## 附录：FDS 官方原生外部控制线索（实施前资料，不是中途审查）

已核对 pinned FDS-6.11.1 官方文档：存在 EXTERNAL_FILENAME、FUNCTION_TYPE='EXTERNAL' 控制，及 DT_EXTERNAL_HEARTBEAT/EXTERNAL_HEARTBEAT_FILENAME 等等待外部控制文件的机制；输出刷新用 DT_FLUSH。可优先研究它是否足以实现单个FDS进程的动作/观察barrier，而不是先假定只能prefix replay。需要实测其读取时机、动作生效、输出采样与pause语义，文档存在不等于本项目已经支持。

官方来源：
- [FDS 6.11.1 User Guide 源文档（external control，约8140行）](https://raw.githubusercontent.com/firemodels/fds/FDS-6.11.1/Manuals/FDS_User_Guide/FDS_User_Guide.tex)
- [FDS 6.11.1 主循环（约607行处理外部控制）](https://raw.githubusercontent.com/firemodels/fds/FDS-6.11.1/Source/main.f90)

这一线索不授权伪造persistent通过状态；Luna仍须运行真实进程验证。
