# 首条真实人类来源闭环校准：到家照明

来源：CrowdRE `crowdre:179`，CC BY 4.0，开发集。原始三个字段：

> Context: The house realizes someone is inside.
> Stimuli: A person walks into the home.
> Response: It turns on the lights.

模型提取 query：When a person walks into the home, turn on the lights.

## 实际结果

`generated/query_construction_v1/crowdre_arrival_native_v2/result.json`：真实模型 deepseek-v4-flash-meituan，D0 原生 720 秒/12 步完成，合同判分通过，1 个实际到达机会，无违约。12 次模型调用，输入 45,683、输出 376、合计 46,059 tokens。自主等待可用，模型本轮选择每次等 60 秒；不是框架强制 12 次决策。

构造输入/复核/映射日志为同目录 `crowdre_arrival_extraction_v1.jsonl`、`crowdre_arrival_review_v1.jsonl`、`crowdre_arrival_mapping_v5.jsonl`；快照 `d0_schema_v3_snapshot.json`。首次失败运行保留在 `crowdre_arrival_native_v1/`，41,255 tokens。此次所有构造调用 9,842 tokens，两次执行加构造共 97,156 provider tokens，不是整个项目累计。

这是**端到端校准样本，不是正式核心挑战题**：原生 `crowdre_arrival_policies_v5.json` 中 idle 失败，always_on 和 occupancy_feedback 都通过。模型也在一开始开灯并保持，因此不能从成功推出其具有必要的持续适应能力。仅检验空屋到有人的到达；不涵盖已有其他人在家的新到达，不代表所有原文情景。

## 沉淀成通用修复的问题

- 映射曾把“a person”错写成 occupancy==1，而原生回家人数为2，三种策略均得到未触发而非通过。映射提示已区分自然语言单数和实际人数阈值；仍不能以提示替代语义验证。
- D0 动作清单缺完整 act/command 包装，补充公开 action_schema，并测试照该结构确实能执行。本地原生回放重新验证。首次模型始终等待；修后能发开灯命令，但多处一起变化，不能证明单一因果。
- 原先 response 初始 true 也触发，却未明确告知。新增显式 trigger_at_window_entry=false，仅将初态作基线，随后真实边沿才触发。旧默认保持；公开合同和映射提示同步。未知、无事件、超时依旧不能判成功。
- 109 项 query/D0 回归通过。不能外推为所有后端和数据已经认证。

下一步：沿此可运行链处理更多开发需求，明确分出校准题、满足项目 claim 的核心题和后端需求候选。冻结构造规则后才评估保留集；本轮没有展示或发送保留集正文。
