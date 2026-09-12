# V11 更新总览（2026-09-12）

本次更新把可信原生后端、真实 LLM 交互和 query 构造接成一条可审计链路。它不是一次正式 benchmark 数据发布；不同层级的“通过”不得互相替代。

## 1. 后端与原生机制修复

- 15 条正式 route 统一动作预检：错误类型、字符串数字和布尔值在进入模拟器前拒绝，错误动作不污染运行状态。
- 离散 workflow 将设备故障视为执行反馈；失败后模拟时间、外生事件和恢复继续发生，未来规则安装与触发时可用性分开判断。
- D0 时域与原生 12 步对齐；SustainGym 修正为 288×300 秒；终态可读取但禁止继续动作。
- Modelica 双房和共享热源增加原生散热边界、修正 FMI 变量绑定与子步通信；共享热源公开比例分配和实际供热。
- FDS 延长公开窗口并修复控制 RAMP 导致的未来动作提前影响历史前缀；仍明确是 prefix replay，不宣称在线续跑。
- EV2Gym 补充公开离站条件和实际离站电量回执；终止反馈使用实际终态。
- WNTR、CityLearn、离散故障等 adapter 补充真实公开观测、动作边界和机制/单位限制。

当前证据支持 15/15 route 的基本交互与单 seed 完整时域协议验收，不支持把所有 route 宣称为已完成任务可行性或物理机制认证。FDS 烟气可见度、CityLearn 单楼跨通道竞争、家庭水力标定等边界仍保留。

## 2. LLM 交互基础设施

- 新增统一真实会话入口和公开观测 contract；首轮发送完整状态，后续发送可无损重建的 delta。
- Agent 可自主选择等待时长或公开事件；等待期间原生 cadence 不变，所有微步和中间违约继续记录。
- 请求意图、模型回执、动作意图、原生回执和结束状态逐条 fsync；传输、格式、输出预算、动作拒绝、后端异常和本地预算分开归因。
- 不自动修复模型答案、不裁剪历史、不隐式重试模型调用；provider usage、请求/响应体积和延迟缺失均显式记录。
- 15/15 route 已有真实模型自主等待协议记录；这些记录证明接口可用和轨迹可重建，不等于 15 个责任任务全部成功。

## 3. Query construction V2

- 新增证据分层双轨：人类明确表达的需求，以及产品/领域证据推导或多来源透明组合的需求。
- 每条 item 保存来源等级、短引文、构造变化、不确定性、scenario binding、公开条件和八类 gate。
- query 与后端场景分离；阈值、时域、故障和评估服务条件必须注明来源，不能归到用户原话。
- 新增 capability screening、真实 snapshot mapping、contract 编译、原生轨迹 evaluator、冻结 release manifest 和内容寻址复核。
- V2 共记录 11 条：3 条核心接受、1 条 calibration、5 条拒收、2 条 pending。三条核心接受并非 human-explicit：两条为产品/领域推导，一条为证据组合合成。
- 冻结后的定向多房间温控迁移产生一个核心接受；直接人类表达的核心迁移探针没有找到可精确匹配来源，没有缩减实体或条件来制造成功。

## 4. Query 规模化尝试

- 冻结两个互不重叠的 development 批次，共 100 条来源（30 CrowdRE、70 HIIS），未读取或发送 CrowdRE holdout。
- `deepseek-v4-flash` 完成 78 条结构提取，得到 77 条 query 候选；1 条来源因期望方向不明确拒收，22 条因模型输出长度截断。
- 27 条候选完成同模型语义复核；全 15-route 单次目录筛查成本高且截断严重，不再推荐作为默认扩批路径。
- 对十个已有证据锚点生成 30 条同义表述，明确只有十个语义/证据簇、零新增独立需求；accepted/rejected/pending 状态只继承，不因改写升级。
- 已知 provider 用量为 972,587 tokens、268 个有 usage 回执调用；一次本地网络沙箱 `URLError` 无回执，保持 unknown，不记成零。

这次扩批证明可以机械地产生上百条候选，但没有证明可以从这两个人类语料自动得到上百条正式任务。新的 100 条来源记录中没有 item 被直接准入。下一批应先用确定性 capability card 缩小 route，再针对真实 snapshot 映射；accepted item 的增长应优先来自有来源证据且能落到现有动态场景的责任，而非继续随机抽取或堆同义句。

## 5. 精确 query 的模型—后端闭环

已接受的多房间持续保温 query 使用 `deepseek-v4-flash` 在真实 Modelica route 上运行。4,000 输出上限的首轮在 t=2100 截断并保留为预算失败；一次预声明的 8,000 上限重跑完成：

- 7 次模型决策；
- 60 个原生一分钟步，到达 t=3600；
- 自主等待期间完整状态可无损重建；
- 四个温度约束各 60 次机会，零 violation、unknown 或 pending；
- 最终轨迹判定 `task_success=true`。

这个结果只属于该精确核心 item，不能用于证明新 100 条候选已有效或已准入。

## 6. 复现与版本边界

- 后端主入口与验收：`README.md`、`TEST_COMMANDS.json`、`docs/BACKEND_COMPLETION_AUDIT.md`。
- 后端修复说明：`docs/BACKEND_TRUST_REPAIR_STATUS.md`、`environment_repairs_v1/README.md`。
- Query 流程：`query_construction/README.md`、`docs/QUERY_CONSTRUCTION_V2_DELIVERY.md`。
- 扩批结果：`docs/QUERY_SCALE_CAMPAIGN_V1.md`。
- `generated/` 包含本地运行日志和冻结证据，默认由 `.gitignore` 排除；GitHub 中提交的是实现、测试、配置和可读报告，不包含大型运行产物或模拟器安装。

提交前本地验证以 `TEST_COMMANDS.json` 为准。旧 Episode release acceptance 仍因实现与历史证据 hash 不一致而 fail-closed；不得用当前交互底座验收覆盖旧发布认证。
