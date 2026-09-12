# Query construction V2：证据分层、原生闭环与冻结迁移

## 结论

推荐采用**证据分层的双轨构造流程**，而不是寻找单一“完美语料”：

1. 直接轨保留人类明确表达的责任，允许不改变含义的委托式改写；
2. 推导轨从产品、领域及真实使用问题中构造需求，但永远标为推导或证据组合，不冒充人类原话；
3. 两轨共同经过独立的来源、改写、后端、情景、evaluation、机制、原生执行和模型执行门；
4. 纯假设题只可用于诊断，不进入真实需求统计。

本轮没有理由开发新后端。复用 `d1_discrete_device_fault` 和
`modelica_buildings_aixlib` 已能得到核心闭环；`d3_modelica_shared_heat` 的资源耦合真实，
但热水温度是否足以代表“有热水可用”仍未解决；EV 的最低价格目标和 IAQ 的反馈/节流目标缺
合格 evaluator。此时新增后端会绕开更直接的数据和判分问题。

## 路线比较

| 路线 | 真实需求依据 | 覆盖与多样性 | 自动扩展 | 主要风险 | 本轮决定 |
| --- | --- | --- | --- | --- | --- |
| 单一公开人类语料 | 单条来源最直接 | CrowdRE 开发样本严重偏照明；与现有 route 错配 | 中 | 为适配后端而削弱实体/条件；许可与开发污染 | 保留为直接轨，不再作为唯一入口 |
| 产品/手册驱动 | 能证明功能和领域情景真实存在 | 温控、车库、EV、IAQ 较广 | 高 | “产品能做”不等于“用户明确想要” | 只进入推导轨，强制降低证据等级 |
| 论坛需求/投诉 | 条件、例外、失败模式具体 | 长尾强、适合发现 evaluator 缺口 | 中 | 采样偏差、许可、坏行为极性、上下文不完整 | 短引文+URL；完整语义未核对则 pending/reject |
| 任意 LLM 合成 | 可快速填 route | 表面覆盖最高 | 高 | 无法证明真实需求；生成器与 reviewer 可共错 | 仅允许纯假设诊断，不作需求证据 |
| 证据组合构造 | 可把真实问题与已部署能力连接 | 能命中现有强场景并保留缺口 | 高 | 组合可能改变原意 | **推荐**；逐项登记改变和不确定性，不标 human-explicit |

停止开放式探索的条件是：至少两类来源进入同一 schema；开发批次含核心接受、拒收和 pending；
冻结规则后的独立样本产生至少一个完整原生闭环，并报告所有失败。当前已经满足，后续应扩数据，
不继续无边界搜来源或堆检查项。

## 可运行流程

### 1. 来源与构造记录

`query_construction/evidence_batch.py` 实现 `evidence-query-batch.v1`：

- 来源只可标 `human_explicit` 或 `domain_or_product_inferred`；合成等级属于 item，不能伪装成 source；
- 每个引用必须是来源卡 `source_text` 中唯一的精确短跨度；精确匹配不宣称语义蕴含；
- `human_explicit` 只接受原意不变的委托化或忠实改写；任何意义改变必须降为
  `evidence_grounded_synthetic`；
- 组合构造至少引用两个来源，并分别说明贡献；
- 验证集只能引用标为 `validation_frozen_before_read` 的来源；
- 八个门必须全部存在，模型通过不能覆盖来源、后端或 evaluator 失败；
- 只有 `model_execution` 可以把失败归到模型。

### 2. 场景和判分

现有 `query_construction` 的 capability card、binding、contract compiler 和原生时序 evaluator 继续复用。
新证据层不从自然语言猜阈值，也不替模型选择动作。公开条件必须区分：来源给出的条件、产品能力、
以及 benchmark 自行给出的时域、阈值和服务范围。

核心题需要同一情景下至少满足：idle 失败；一个与变化无关的固定/一次动作失败；可行反馈策略通过；
轨迹完整；对应机制确实在该 episode 中发生。校准题显式标 `accepted_calibration`，不要求机制证明，
也不进入核心成功率。

### 3. 执行命令

从仓库根目录运行：

```sh
python tools/audit_evidence_query_batch.py \
  --batch generated/query_construction_v2/development_batch_v1.json \
  --output generated/query_construction_v2/NEW_DEVELOPMENT_AUDIT.json

python tools/run_evidence_query_diagnostics.py \
  --output generated/query_construction_v2/NEW_GARAGE_DIAGNOSTIC.json

python tools/run_thermal_query_diagnostics.py \
  --output generated/query_construction_v2/NEW_THERMAL_DIAGNOSTIC.json

python tools/prepare_evidence_query_agent_packet.py \
  --batch generated/query_construction_v2/core_transfer_batch_v1.json \
  --item-id core-transfer-multiroom-comfort-01 \
  --diagnostic generated/query_construction_v2/core_transfer_thermal_native_v1.json \
  --output generated/query_construction_v2/NEW_AGENT_PACKET.json

python tools/summarize_evidence_query_release.py \
  --batch generated/query_construction_v2/development_batch_v1.json \
  --batch generated/query_construction_v2/validation_batch_v1.json \
  --batch generated/query_construction_v2/core_transfer_batch_v1.json \
  --output generated/query_construction_v2/NEW_RELEASE_SUMMARY.json

python tools/freeze_evidence_query_release.py \
  --config generated/query_construction_v2/release_config_v1.json \
  --output generated/query_construction_v2/release_manifest_v1.json \
  --check

python -m pytest tests/test_query_*.py -q
```

工具不调用外部模型。批次中的 evidence hash 会在审计时重算；轨迹或脚本变化后不能静默沿用旧证据。
新增批次应使用新文件名，不覆盖本轮冻结文件。
发布文件 `release_manifest_v1.json` 对 22 个输入、代码、冻结记录、轨迹和报告做内容寻址，并内嵌
重新计算的批次汇总。`--check` 会在任一文件或统计改变时失败。

## 实际样本与证据

### 开发批次

`generated/query_construction_v2/development_batch_v1.json` 有 6 条完整流转：

- 2/6 `accepted_core`（33.3%）：证据组合的车库持续关闭；产品证据推导的双房持续保温；
- 1/6 pending（16.7%）：共享供热下房温+热水；
- 3/6 rejected（50.0%）：EV 最低价格目标缺 evaluator；无人+车库门缺精确联合 route；
  照明投诉的默认情景没有“一人离开、另一人仍在”的机会。

来源等级分母为 6：3 条人类明确表达、1 条产品/领域推导、2 条证据组合合成。接受不等于
human-explicit：两个核心接受中一个是产品推导，一个是明确标注意义变化的组合合成。

车库核心样本复用完整 10 步原生 witness/idle 轨迹；故障改变实际可用性，恢复及后续受阻要求重新观察，
不能把 close 指令被接受当成功。双房核心样本检查两个房间每个 60 秒样本；idle、一次动作、固定 0.3
阀位失败，反馈策略通过。

### 冻结后的独立验证

广域验证规则在 `validation_freeze_v1.json` 中先冻结，再按固定的四领域顺序取首个合格来源。
`validation_batch_v1.json` 的 4 条结果为：

- 1/4 `accepted_calibration`（25%）：SmartThings 用户明确提出固定时刻检查并关闭车库门；
- 2/4 rejected（50%）：openHAB 三区域供热不能缩成现有两房单执行器；EV 产品的最低价目标仍无 evaluator；
- 1/4 pending（25%）：Hubitat 用户的 CO2 反馈通风有真实需求和后端动态，但固定最大通风也能通过旧阈值。

因为广域验证没有核心接受，又在 `core_transfer_freeze_v1.json` 事先限定了独立厂商、多房间定时舒适、
首个合格结果和四策略原生对照。搜索后首个合格来源是 Honeywell Home/Resideo。新 seed 23 的四条
60 步原生运行结果：idle、一次 0.5、固定 0.3 均失败；反馈通过，双房范围 19.209–20.389°C。
`core_transfer_batch_v1.json` 因此得到 1/1 核心接受。它验证来源到既有场景的规则迁移，不是总体成功率，
也没有证明跨后端泛化。

合并描述性统计（不可当概率样本）：独立样本共 5 条，核心接受 1、校准接受 1、拒收 2、pending 1。
核心接受率 1/5=20%，所有接受率 2/5=40%，拒收率 2/5=40%。广域批次与定向核心探针的抽样目的不同，
正式论文不得把 5 条合并成无条件成功率。

`release_summary_v1.json` 机器复算了全部 11 条记录：核心接受 3、校准接受 1、拒收 5、pending 2；
所有接受率 4/11=36.4%，核心接受率 3/11=27.3%，拒收率 5/11=45.5%。这里混合了开发、广域验证
与定向探针，只是发布账目；不能替代上面的独立样本分母，更不能推断用户总体或模型能力。

为检查核心接受是否只发生在推导轨，`human_core_transfer_freeze_v1.json` 又预先冻结了六步搜索。
`human_core_transfer_result_v1.json` 的结果是 0 条加入、`no_qualifying_source`：最接近的直接请求包括
四个具名房间+在家条件（现有 route 只有两个泛化房间、无 occupancy），以及“车库每次重新打开都重置
60 分钟计时”（当前 D1 没有独立于 agent 的对应手动关闭/重开事件）。没有通过缩减实体或让诊断策略
自己制造外生事件来取得成功。因此本轮**仍未得到 human-explicit 的核心接受样本**；三条核心接受分别
是两条产品/领域推导和一条证据组合合成。

## 反例与失败归因

新增测试覆盖：

- 将意义改变的合成 query 伪标为人类原话会拒绝；
- 验证样本使用未冻结来源会拒绝；
- 伪造通过的模型结果不能覆盖后端/evaluator 失败；
- 非模型门不得归因给模型；
- evidence 文件改变或 hash 过期会拒绝；
- 冻结验证不能只凭文件 hash：当前代码会从全部原生样本重算结果、核对时钟、动作回执及四策略对照；
- 缺失原生样本或错误时钟不能通过固定时刻 evaluator；
- 固定时刻校准明确不惩罚合同之外的后续事件；
- 热力对照的 idle、一次、固定和反馈策略结构不同，且数值参数在运行前固定。

冻结后发现校准准入的通用逻辑存在互斥条件，修复记录在
`validation_freeze_amendment_v1.json`：原实现同时要求机制门 passed 和 not_applicable。
修复只允许校准题的机制门为 not_applicable，其余客观门仍须全部 passed；开发和验证批次均重跑。

发布核查又发现“hash 相同”只能证明文件没变，不能证明批次里写的结论正确。通用加固记录在
`validation_freeze_amendment_v2.json`：冻结接受必须用当前代码重新评分，旧开发证据只保留为
`digest_only_legacy`。从空临时目录重跑车库和热力工具分别得到与冻结文件完全相同的 hash；没有因
这个加固规则替换、增删或重排任何来源。

当前失败归因：

- 数据/构造：EV 价格目标没有完整判分定义；投诉上下文/实体范围不完整；
- 交互/后端：三独立供热区域缺精确接口；
- 模型：本轮没有对精确新 query 进行付费或外部模型运行，因此模型分母为 0，不能报告模型成功率；
- 无法归因/待定：DHW 温度与可用热水服务的对应、IAQ 反馈必要性。

## 消耗

- 开发批次记录 12 条复用的既有原生轨迹；
- 冻结验证新跑 2 条车库轨迹；
- 定向核心迁移新跑 4 条热力轨迹；
- 本轮批次统计共引用/执行 18 条原生 policy runs，其中新执行 6 条；
- provider/API 调用 0，provider tokens 0，未知 provider 用量事件 0；
- web 研究和本地开发推理不混入 provider 任务 token 统计。

精确核心 query 的首轮模型可见内容已冻结为 `core_transfer_agent_packet_v1.json`：它由当前 route reset
重新构造并核对诊断初态，包含同一 query、公开合同、合法动作、初始观测、输出格式、等待语义和 30 次
调用上限。状态明确为 `not_authorized_not_run`，provider calls 为 0；准备 packet 不是模型实验结果。

## 验证状态

`python -m pytest tests/test_query_*.py -q` 为 137/137 通过。全仓测试不能宣称全绿：默认收集会因
两份已不存在的旧顶层脚本中止；排除它们后为 623 passed、8 failed、32 setup errors。集中错误来自
缺失的历史 generated artifacts、缺失 `scenario_catalog.json`、既有 D0 wait 边界不一致，以及
`agent_interface.py` 既有修改使旧证据 hash 失效。这些路径不属于本轮 V2 修改；本轮没有重建或
覆盖用户的历史产物。它们是当前工作区基线风险，而不是模型或新样本的失败。

## 已知局限与下一步扩展

尚未证明：这些小批次代表智能家居用户总体；论坛内容可整体再分发；双房温控阈值代表个体偏好；
核心 query 的被测 LLM 成功率；新来源/新后端的规模化通过率；跨多个 episode 的长期泛化。
也尚未证明直接人类表达轨可以在当前精确接口上形成核心接受；本轮冻结探针为 0/1 搜索目标成功，
不是通过率估计。

下一批建议保持规则不变，按来源组冻结 12–20 条：至少 1/3 直接人类表达、1/3 产品/领域推导、
其余为证据组合；按参与者/线程/近重复分组。先做来源与结构审计，再匹配 route；不得按能否通过后端
替换样本。每个候选最多允许一次通用映射，不支持则进入 backlog。只有当同一高频需求反复因同一
缺口拒收、且有限 task-layer adaptation 仍无法解决时，才提出后端开发决策，附预计新增实体、动力学、
evaluator、测试成本和可新增的有效样本数。
