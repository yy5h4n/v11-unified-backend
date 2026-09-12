# Query construction：当前目标与交付门

## 当前有效目标（用户最新修订，覆盖下方历史范围）

交付一个可用、可复现的 query construction pipeline：需求依据可来自公开人类表达、产品/领域证据及其可审计组合，不再以现成公开人类语料为唯一入口。四类内容必须分开标注：人类明确表达、领域/产品推导、证据上的合成或改写、纯假设构造。将 query 与后端情景分离，在实际适配的后端上构造公开服务条件并完成原生运行、轨迹判分和反例验证。保留来源/许可、所有意义改变、操作参数依据、失败归属、用量、样本及拒收报告、运行说明。以冻结规则后才读取的来源验证流程，不依赖新增人工标注或用户逐题审核。

**不再要求覆盖全部15条后端，也不要求所有领域都找到人类来源。** 保留已有能力清单作为可选匹配范围，但覆盖率不再是完成门槛。验收仍必须有实际通过完整构造与验证链路的样本，不能以全拒收、仅单元测试或模型自评代替。

新增交付：记录语料中集中出现的需求主题及所需能力，形成需求驱动的后端候选清单。每项保留来源ID、统计范围/分母、来源偏差、现有能力缺口和最小适配建议；语料内频次不宣称代表真实用户总体。当前阶段先记录建议，不将用户“之后可以开发”当成已授权立即新增大范围后端。

下一步优先：采用 `docs/QUERY_CONSTRUCTION_V2_DELIVERY.md` 中的证据分层双轨方案，扩展冻结来源组并保持失败分母；其他主题进入候选清单，不再为了覆盖无关后端持续找语料。后文所有“必须是现成公开人类语料”“必须覆盖15后端/补齐所有领域”等表述仅保留作历史，不再执行。App goal工具仅支持完成/阻塞状态，无法改写活动正文；本段及用户最新指令是实际执行范围。

## V2 pilot outcome（2026-09-11）

证据分层双轨、独立门禁、规则冻结迁移、原生策略对照、内容寻址 release 和模型输入 packet 已实现；
主报告为 `docs/QUERY_CONSTRUCTION_V2_DELIVERY.md`。正式记录 11 条：核心接受 3、校准接受 1、拒收
5、pending 2；provider 调用为 0，精确新 query 的模型分母为 0。冻结的 5 条独立来源记录中核心接受
1、校准接受 1、拒收 2、pending 1。额外的人类直接核心探针没有找到能精确匹配当前接口的接受项，
未通过缩减条件或制造外生事件来伪造成功。下一项需方向决定的是 EV tariff 公开信息+成本 evaluator
spike，依据与边界见 `docs/QUERY_BACKEND_ADAPTATION_DECISION_V2.md`。

## 历史进展记录

开发语料继续筛查发现response列包含实际坏行为而非期望结果（CrowdRE1670鹿触发灯反复亮灭影响睡眠，另有1828等）。提取提示加入投诉/期望方向区分、不擅自发明修复；真实1670提取调用1022tokens，模型正确识别负向责任，但生成How can I咨询句，尚非直接委托。新增投诉意图复核报警与咨询句报警，并修提示；未重跑修订后提示，旧日志不重写。该需求缺鹿/室外运动后端信号，不作准入。关键词扫描后续正文仅development，未动holdout。

新增 `native_evidence.py` 与报告器 native/diagnostic 接入：按映射路径、query、公开合同与初态核对真实运行，从完整样本重新计算模型及基线成绩；错题、篡改成绩、错初态拒绝。CrowdRE179完整报告已生成，自动标为calibration_only_constant_baseline_passes，不冒称准入。4新测试，全query/D0共113项通过。本轮未调用模型、未读保留集。开发集on/off关键词扫描92条（含scare off等误召回，非语义频次），只展示前12条；57走道灯/泛光灯、100窗边灯/15分钟等进入能力缺口记录，未凭词频宣称大量可用任务。

最新：真实人类来源 CrowdRE179 已完成提取→独立复核→D0映射→真实LLM原生运行→合同判分与三策略诊断，第二次运行合同通过。仅作校准样本（常亮也通过），正式核心题准入仍0。修复D0动作包装公开缺漏、增加显式事件初态基线选项、提示区分“有人”与人数==1；109项query/D0回归通过。本轮构造与两次执行共97,156 provider tokens。证据与限制见 `docs/QUERY_ARRIVAL_CALIBRATION.md`。执行范围仍为上方最新用户修订，不要求全后端覆盖。

新增离线`tools/revalidate_query_extraction.py`，验证原请求正文与库存一致，从保存receipt重新运行当前验证器，不重新调用、不复制provider receipt避免重复计费，保留原日志与line引用。执行`crowdre_random20_revalidated_v3.jsonl`：20结构通过/0拒绝（先前6条仅空白问题均恢复），不是20语义/任务准入。来源方法核查：HomeBench arxiv2505.19628v1 §3.3从设备命令以GPT3.5合成指令，不是人类原始需求；SmartBench arxiv2603.06636v1 §3.2混合真实状态轨迹与GPT5情景序列，真实行为轨迹不等于用户长期委托文本。二者暂不作为human-query来源。仍需新来源与原生可行任务，目标未完成。

原生执行入口补初态身份失败禁止评分：记录episode_identity，0模型调用、不把不匹配初态送入合同评估；新增真实D0错初态反例通过。提取引用错误抽查ID1249发现仅换行/空格合并，新增quote_v3_whitespace_alignment：逐词/标点不变且唯一匹配时保留model_quote和原文quote/span，只允许空白差异；改词/数字/歧义仍拒绝。4新测试+全query共96项通过。旧journal不重写，历史6引用错误仍按当时版本记录；本轮未新增付费调用，尚无正式任务准入。

补交付使用说明`query_construction/README.md`：明确源/责任、后端情景、公开判分条件分离，列当前CLI实际步骤、外发行为、费用未知处理、三类失败归属与尚缺准入环节；不把组合工具冒称一键成熟pipeline。开发集条件扫描只展示前12条ID40,43,54,72,74,75,96,97,105,129,138,144，均有房间/声音/调光等额外要求，不偷换成D0整屋占用。没有读holdout。新温控研究候选MDPI buildings6020019网页429，未获取其人类原文或导入样本；BigHouse无新附件，未重复访问403入口。

新增`execution.execute_contract`与`tools/run_constructed_query.py`接构造合同到可信autonomous-wait run_session：同一公开合同随query给模型、从同条件编译native轨迹评分、原生初始化必须匹配snapshot、零延迟反应下界失败在调用前拒绝、不从执行成功推出数据准入。CLI自动选路线解释器，保留全步轨迹/事件和provider用量。2集成测试通过：真实D0后端12步+脚本测试client完成同合同评分（明确不是LLM实验或人类样本），零宽限期0调用拒绝。尚未以合格新query执行真实LLM闭环；CLI本轮仅实现，不能说新样本已跑通或全15路线都测过该新入口。

针对真实筛查把light.state当颜色/亮度，查D0源代码与snapshot确定只支持on/off。Quantity新增可选allowed_values；D0灯明确枚举，绑定器拒绝不在原生域的目标（bright white/dim/数字/布尔），不再仅以category字符串接受。其他后端未知域不猜测补全。新增反例测试；此修复防止一类无效合同，不代表语义匹配或全后端任务可行性完成。来源搜索另确认StyleKQC是人工生成并人工改写的指令语料（ACL2022.lrec-1.771），不能自动当成真实终端用户长期需求，暂不导入充数；BigHouse尚无新可用附件。

新增`tools/report_query_pipeline.py`将确切extraction/review/routing/mapping日志汇合为逐来源报告，校验输入正文/query/atoms与映射journal关联，重新执行当前表面检查但不改写历史，不把model clear当native pass。`crowdre_pipeline_report_v1.json`已生成5条全阶段可追踪记录、0准入、native未报告。Big House公开ACM网页实际HTTP403，未取得附件，非可用来源；不在该下载口重复循环。下一步优先继续找公开附件替代分发点并核实其他来源，且需实现native任务准入，报告器本身不完成goal。

扩展来源搜索找到Big House Dataset，作者页证实四种假想智能家居界面的众包问卷，研究关注界面引导对需求的影响；作者博士论文Appendix A将SQLite数据库/说明/访问脚本指向ACM DOI10.1145/3132031补充材料。已记录`query_construction/bighouse_candidate.json`，尚未取得附件或确认数据许可，不宣称已可用。作者网站页脚CC BY-NC-ND不能自动视为数据许可；TerraSwarm镜像下载为内部登录资源，不尝试绕过。另检索到SimuHome仓库CC BY-NC-ND且包含LLM生成流程、EdgeWisePersona模拟对话、enfuse合成命令，这些不作为新真实人类来源。CrowdRE原研究是否限定照明仍未通过研究方法文献确认，只能说当前随机样本照明集中。

固定随机开发抽样20条完成：run_query_extraction新增--sample-seed，先过滤development，按ID排序后seed20260910抽样，journal记录种子/库存，不能与source-id混用。`crowdre_random20_v1.jsonl`20次真实调用17149 tokens，14结构候选/6逐字引用失败，未知用量0；ID8与先前开发重叠，不宣称20全新独立。新增`tools/summarize_query_batch.py`逐请求核算状态/未完成/未知计费，报告`crowdre_random20_summary_v1.json`。14候选全部照明，随机扩大未解决领域覆盖，下一步必须查该研究采集范围、补其他真实需求来源，不能把lights重述成HVAC来凑15后端。未读holdout保持未展示，目标仍未达到。

新增全15路线语义筛查`routing.py`/`tools/screen_query_routes.py`，强制每条路线唯一、完整且附缺口理由；4测试通过。真实首5条`crowdre_routing_v1.jsonl`消耗13004 tokens：4条各给D0候选、其余14不支持；第5条candidate同时列缺口被解析门拒绝。检查理由发现模型把door.state当motion、light.state当brightness/color、占用当阅读/黑暗等，不应当作合法匹配。继续对ID10阅读白光题用真实D0 snapshot与legal_actions执行`crowdre_mapping_reading_v1.jsonl`，结果compiled=false/whole_query_not_supported，实际下游拒收已跑通。全目录screen仅召回候选，不能作为支持性证明。还需要更宽开发批次（目前5条都是照明）与可执行新样本，不能将这些全拒收充作goal完成。

CrowdRE首5条独立review已真实完成，`crowdre_review_v1.jsonl`消耗4156 tokens，模型全部clear，但人工代码代理核对发现其natural_delegation只检查atom，漏掉ID9最终query明确Set up a rule；ID8实体缩小、ID11条件证据缺漏也未识别。因此该审核在实际样本上有假阴性，不能用之前5个合成变异诊断推断可靠。新增最终query工作流措辞确定性报警及反例测试；review prompt明确同时审查final query和atoms。尚未重跑修订review，不改写v1历史。不把全部clear作为数据准入。下一步需要审核分对象结果与后端匹配的批量链路，仍无正式准入任务。

CrowdRE实际导入与首批提取完成：`query_construction/import_crowdre.py`读取UTF-8 CSV并校验完整列结构，1823条中1811保留、12隔离（9列异常、3缺身份/正文）。参与者+团队+response规范化重复传递分组，1653开发/158holdout；最大组1272，不人为拆开凑25%。`crowdre_v1/inventory.json`最初只将response作为source.text，经开发集前5条检查发现context/stimuli均含实质条件，已修为三列逐字序列化，正式后续输入使用`crowdre_v2/inventory.json`，两版partition已断言完全相同。holdout正文未展示或传模型；首5开发ID7–11已暴露。run_query_extraction默认选择修为先过滤development再取limit，防止遇到混合划分中止。

`crowdre_extraction_v1.jsonl`真实5调用完成，5条均结构引用通过，总4137 provider tokens；不是语义或任务通过。ID8模型自行将all lights缩为in that room，ID9仍出现Set up a rule，ID11证据未引用条件列。下一步需独立语义review与需求驱动能力匹配，不能据5/5宣布数据有效。全部query测试83项通过。此来源是众包研究中表述的需求，非真实家庭部署证据。当前仍无正式准入新任务，完整goal保持active。

找到更合适新来源CrowdRE（Zenodo3550721，官方API确认CC BY4.0，众包用户故事，含solo/interacting teams）。元信息记录`query_construction/crowdre_candidate.json`，已下载/private/tmp/crowdre_3550721.zip并仅列目录：all_requirements.csv与users.csv等。未读取需求正文；下一步读列schema并按参与者+团队隔离冻结，排除人口/人格信息外发，避免再把新来源全部暴露后失去holdout。不宣称原始需求已适配后端；当前仅来源元数据通过。

独立时限检查已接入run_query_mapping：`audit_reactive_contract`对已知D0门/灯逐命令下界验证，其他量保持action_lower_bound_unverified，不冒称15后端动作可达性全覆盖。4项时限测试通过；对真实mapping_atomic_v4离线审核明确reactive_deadline_incompatible（0<60s）。不修改模型的零延迟合同，也不声称整个任务不可行：预先保持灯关可满足，但不是事件后响应。未知联合动作数/动态稳定时间仍需后续原生验证。尚未新LLM调用。

映射v3提示仍漏响应时间；已在compile_mapping强制conditional invariant显式activation_grace_seconds（contract旧记录解析语义不改），7项映射测试通过。真实v4虽显式给出政策但原生三策略重跑仍idle通过/always_on失败/feedback失败，证据mapping_atomic_development_v4.jsonl、atomic_d0_policies_v2.json。不能继续把模型明确写0当可接受依据；下一步应由独立构造时间约束检查拒绝不合理零延迟，并制定公开操作条件，不仅反复加强prompt。该题仍不准入，未达到端到端目标。

新增`timing.check_response_budget`检查显式动作数下界、每原生步命令容量和期限；不取整放宽deadline，不把时间下界通过当动态可行性。3项新测试+映射共9项通过。D0一命令/60s实参检查：0s期限不兼容，60s通过必要下界；均未准入。映射提示已公开activation_grace_seconds并要求区分原文/后端/设计参数来源，禁止“supplied”笼统说明；尚未重跑新提示。需要注意该计数输入仍是显式证据，不是自动求解器推导，后续不得宣称全部动作可达性已验证。

通用条件维持合同新增显式activation_grace_seconds，默认0不改旧语义。只允许带active的invariant：每次已知激活后公开宽限期，到期及之后逐样本维持；持续active不刷新、缺观测不暂停、窗口结束前未到期保持censored。公开合同同步说明；3项新测试及既有时序/合同共23项通过。未回写旧失败题或自动给它加60s，不把宽限期修复当作持续交互必要性证明。下一步配置生成需按动作耗时及需求披露响应窗口来源，再跑原生反策略。

原生反策略执行完成：`tools/check_constructed_d0_policies.py`读取LLM生成公开合同，在未改动默认D0上跑idle/always_on/occupancy_feedback，证据atomic_d0_policies_v1.json。idle通过，always_on失败6样本；feedback在离家首次可见120s失败1样本，180s已关灯。说明默认题无行动也满足，且零延迟invariant会惩罚事件后正常反应。此为数据/合同问题，不给LLM记失败。需要公开的响应时限和合适责任/情景共同证明持续交互必要性，不能仅改判分让策略过或随意添扰动。该样本不准入claim集；未见来源仍缺，目标不完成。

已首次接通开发原子需求到轨迹判分：103照明原子，经LLM mapping_atomic_development_v2编译为无人时灯off的公开invariant；新增零人数条件测试后合同7项通过。`tools/evaluate_constructed_query.py`从公开条件重编译，关联真实snapshot/既有全程原生轨迹，输出atomic_d0_evaluation_v1：6个激活样本均通过。不是完整goal验收：默认灯初始off且该轨迹本就off，尚未证明需要持续交互；parameter_origin仅“supplied”过泛，需细化；仅开发样本、旧轨迹离线评分，不是新query驱动LLM/native闭环。下一步必须做有依据的情景机制与反例可行性检查，不能将此直接准入。

更正最近进程状态：session70939已正常完成，映射结果whole_query_not_supported（非传输失败）。下一轮检查该拒收理由与真实能力是否一致，不再poll或重发已终止调用。

来源103重试提取已真实成功：`extraction_atomic_development_v2.jsonl`，无人时关灯/关卷帘两原子，结构引用通过；新增`tools/project_query_atom.py`生成`projected_atomic_development_v1.jsonl`照明子责任，保留父需求及卷帘未选部分，query来自LLM原子责任文本。真实D0映射已启动session70939（输出mapping_atomic_development_v1.jsonl），最近仍待回执，下一轮先poll同handle，不重复调用。传输失败日志现记录cause_type/http_status且退出码1，避免假成功；旧失败原因仍不可追认。

重要范围更正：用户明确允许从人类文本提取原子责任，不能让“完整原文所有动作均需后端支持”阻止显式原子派生。新增projection.py：保留parent query/source、选中索引、未选责任、拆分理由，明确非完整原文履行；共享条件/例外/耦合仍需语义检查。完整query映射覆盖门仍保留。新增测试通过。开发来源103（无人时灯灭且卷帘关闭）已选作拆分调试，不是未见样本；提取CLI支持显式source-id，本轮真实调用尝试写于extraction_atomic_development_v1.jsonl，需按receipt/transport结果核对，不冒称生成成功。

映射v2真实调用完成，模型正确拒绝厨房占用/报警缺口，但仍返回Markdown围栏。新增parse_mapping_response只移除单层完整json围栏并记录wrapper，不修内容，重复key/额外散文/nonfinite仍拒绝；6项映射测试通过。对v2原始receipt离线重解析得whole_query_not_supported，两原子均明确拒收；原journal的旧mapping_error保留，不回写历史。此条链路现已得到真实拒收而非解析失败，尚无可执行准入样本。代码、提示与证据应冻结在报告中，不声称同一规则下历史结果自动变化。

用户已明确授权向现有服务外发模拟观测/接口/公开需求，并表示“llm request在10million以下的请求都授权”。已向用户说明暂按累计1000万输入+输出tokens理解，不按1000万次调用；仅继续小批量，需累计消耗，不代表用满预算。恢复一次D0真实映射，`mapping_development_v1.jsonl`provider1200tokens：返回Markdown围栏导致严格解析失败，同时发现模型把整屋占用当厨房占用、谓词多包一层。报警责任正确识别不支持。保留失败证据，提示增加明确裸JSON示例和实体范围限制；尚未重跑修订。授权阻塞已解除。

情景关联检查已编码：`scenario_checks.check_episode_identity`拒绝缺失/不同route、seed、初始公开状态与初始时间；snapshot工具新增显式--seed。6项情景测试通过；当前15份真实snapshot与既有delivery_native_v5记录逐一运行关联检查全部兼容。此检查只是必要条件，初态一致不证明完整配置/未来扰动相同，更不证明任务可行；完整配置出处仍待绑定。授权未收到，未外发。

EV竞争证据差异已定位：固定同伴探针用seed 3、26步，两车各11kW共同超15kW预算；默认交互seed 0多份完整回放只有一车。不是后端完全无竞争，而是任务实例没绑定已有竞争情景。详见`docs/QUERY_EV_SCENARIO_AUDIT.md`。下一步任务构造必须显式seed/config并验本情景，不能跨情景借用能力证据；未修改默认seed，未外发数据。

D3 EV既有原生记录`autonomous_wait_delivery_native_v5/d3_ev2gym_electric_competition.json`核对：只在24300s出现port1一辆车，35100s离开，离站回执38.8614kWh/要求50kWh，整个86400s无两端口同时连接。此接口回放不能证明竞争情景被激活，不等于所有其他竞争配置无效。新增`scenario_checks.ev_overlap`及3测试，区分无重叠/重叠但未证实需求冲突/无有效数据，绝不把路由D3名称当机制证据。外发仍待授权，未重试。

离线15路线快照检查`tools/check_query_backend_snapshots.py`已执行，`backend_snapshot_check_v1.json`：14路线初始字段类型可用，D3 EV两端口SOC初值None导致初始数值绑定不成立。路径存在不等于初值可判！这是动态车辆/未连接实体的合同需求，不应把None填0或删除端口。下一步需加入公开connected激活与实体生命周期关联，在未连接时允许值缺失但不能漏掉后续车；当前绑定器还不支持该完整语义。外发授权仍待用户回复，未重试被拒调用。

15条正式后端snapshot全部完成且snapshot_ok，批进程69105已正常退出。新增`tools/run_query_mapping.py`连接已有提取journal与真实snapshot、模型mapping及覆盖/合同编译。尝试外发D0观测+动作schema+公开query时被auto-review明确拒绝，理由为内部接口/状态向aigc.sankuai.com披露授权尚未明确；该调用未执行，不绕过。需用户明确授权这一类数据外发后再运行映射；源码及本地检查仍可继续。映射+合同11项测试通过。不要因一次授权门标Goal blocked；目标仍有可独立推进工作。

新增`tools/snapshot_query_backend.py`使用当前正式factory、各路线对应Python、reset及legal_actions读取真实公开接口，保证close，不执行动作或LLM。输出`backend_snapshots_v1`：D0、IAQ、SustainGym、电池已snapshot_ok且能力卡字段均解析成功。剩余路线批处理已启动（统一exec session 69105），最近仍运行，EV初始化有matplotlib只读缓存警告，不应误当失败或重复启动。下一轮先poll同一handle并检查输出，再用真实snapshot做映射，不猜动作接口。

映射LLM输入已实现`mapping.mapping_messages`：15条路线均能生成包含真实能力卡、时钟、传入公开观测和legal_actions的映射包，明确结构格式及不能删条件/偷换目标/编造服务量。5项映射测试通过；尚未实际LLM调用该阶段。查看既有`autonomous_wait_delivery_native_v5/energyplus_iaq.json`发现initial只有观测而无legal_actions，不应从这些记录猜动作接口；下一步从当前正式后端公开schema取得合法动作，再跑真实映射。覆盖packet生成不等于15条已完成源需求任务。

现有语料广扫发现第98个解析记录包含疑似下一条CSV行（另一个参与者编号/规则名），不能当单条来源。导入器新增通用嵌入记录隔离检查，保留raw_fields不猜拆分/归属；7项导入测试通过。已重新执行导入至`hiis_development_v2`：203解析记录中202保留、1隔离，旧库存与LLM证据不改写。后续使用v2库存。此异常可能解释204/203差异，但未经源文件修复确认不作定论。温控/空气需求确实存在于旧语料，不应概括为完全没有；不少附带占用/通知/窗户/健康条件，不能为适配连续后端而静默删掉。

新增`mapping.py`责任覆盖门：每个提取原子必须显式mapped且有公开条件，或unsupported且有原因；遗漏/重复原子、空映射拒绝，有任一不支持则整条query不编译，避免只保留容易实现的责任。条件以原子索引命名并送入现有合同编译器，不修改输入。4项新增测试及6项合同测试通过。此处仍是结构覆盖，不证明映射语义等价；实际LLM生成mapping与原生验证仍需接通，不能用测试fixture充当人类数据。

语义审核器基础反例测试已真实执行：`tools/check_query_review_mutations.py`，证据`review_mutations_v1.jsonl`。一个保持不变对照未报警，改时长/删条件/反转结果/长期改一次性四个合成变异均报警，provider总3499tokens。只证明这5个简单诊断上的行为，不能推断真实语料审核准确率，也不是新的人类样本。全部query组件回归58项通过。仍需真实语义映射、原生可行性及未见来源端到端交付，Goal未完成。

新增`review.py`和`tools/review_query_extraction.py`：独立调用审查7维语义（结果、条件、实体、长期范围、参数、原文覆盖、自然委托），不给生成器自我解释作为判断锚点。3项解析测试通过，已有v4前3条真实复核均返回clear，证据`review_development_v1.jsonl`。同模型独立调用仍可能相关出错；尚未用已知语义破坏反例校准审查灵敏度，clear绝不直接准入。下一步必须测试删条件/改阈值/删责任等受控变异，避免“再问模型一次”成为假验证。

新来源核对：LLM4QDARE原论文确认需求来自PURE及互联网SRS/FRS，人工共识标签不能证明终端用户来源，暂不拿其466条充当真实用户需求。SH-RDL论文确有开放问卷/访谈研究描述，但未获取原始附件和许可，不能以567样本数当可用query数。细节见`docs/QUERY_SOURCE_CANDIDATES.md`。这轮无新数据准入、无模型调用；来源覆盖仍是实际未完成项。

新增`contracts.py`将同一显式操作条件编译为公开合同及Temporal Clause，保留阈值/单位/窗口/参数来源和具体实体路径。拒绝原生时钟不对齐、超时域、未知字段及多实体触发关系未定义；不偷偷取整或只判第一个区域。6项合同测试通过；首次测试中共享通风6000秒窗口边界不对齐900秒时钟被正确拦截，测试修为6300秒。仅合同编译组件，不是完整query→合同语义映射，也未验证动作可达性；没有新模型调用。

扩大前10条开发需求真实复测：`extraction_development_v4.jsonl`，9条结构引用通过、1条非逐字引用拒绝，provider总8853tokens。前述两条one_shot误分类本轮改为respond，但仍有“Set up a rule/automation”等workflow措辞，未达到全部自然长期query质量；不可据此宣称语义9/10或任务成功率。新增`semantic_checks.py`保守标记数字变化/规则被一次化/显式假设，仅报警不认证，相关11项测试通过。该代码在v4进程启动后添加，因此v4不包含surface_review事件；不要误称本轮已运行该新检查。

quote_v2真实复测完成：`extraction_development_v3.jsonl`同3条全部原文定位结构通过，9项提取测试通过；provider总2202tokens（prompt1270/completion932）。并非语义全通过：两条把“每次触发执行一次动作”误当“整个责任只执行一次”，第二条报警原子引用未覆盖共享条件。下一版提示补collection_kind（此前只给文本，遗漏自动化规则的收集背景），澄清触发响应与一次委托区别；尚未重跑此提示修订。独立语义检查仍缺，不能以3/3格式通过替代数据合格。

真实LLM提取已执行：`tools/run_query_extraction.py`开发集前3条，模型deepseek-v4-flash-meituan，证据`generated/query_construction_v1/extraction_development_v2.jsonl`。3次正常stop，均因模型给错字符跨度被结构校验拒绝（不是后端任务失败）。provider报告prompt_tokens=1117、completion_tokens=924、total_tokens=2041；另外v1有一次transport_error且usage未知，不声称零计费。复用ChatClient新增可选max_tokens，旧默认320不变，提取1800。提取测试8项仍通过。

下一步协议修正应让模型引用原文文本、由程序唯一定位其跨度；歧义重复片段需显式定位，不应让LLM数字符成为语义提取瓶颈。保留本次原始失败证据，不回写伪造通过。另外第2条把a person不在改为无人、合并多个责任，第3条改成第一人称等也需要独立语义检查，修正跨度并不等于提取正确。尚无数据准入。

Mendeley原始访谈已本地下载并读取：5位参与者，含健康背景，不能宣称无敏感信息。主要是语音操作/健康/娱乐偏好；提及温控不自动支持长期维持委托。已读正文计exposed_source_screening，不凑未见集。详见`docs/MENDELEY_SOURCE_SCREENING.md`；未向外部LLM发送，测试后访谈未读取，可能同参与者故不可自行视为独立holdout。新来源主候选仍需更换，同时现有HIIS开发集可继续用于实际提取器调试。

新来源下载入口已打通：从Mendeley官方网页bundle找到真实公开files接口，成功取得6文件元数据，其中原始访谈与测试后访谈两份docx各约15KB。位置与版本等记录于`query_construction/mendeley_candidate.json`。尚未下载/阅读正文，仍非冻结验证集。浏览器工具因本地权限路径解析启动失败，已用经许可的公开HTTP读取替代；不是持续阻塞。下一步本地检查匿名化与访谈结构，避免将UI偏好误作长期家居责任。

新增`extraction.py`：不含后端清单的原子责任/长期改写提示协议，以及严格JSON与逐原文跨度校验。禁止静默增加日程、设备、阈值、故障或将一次请求改为长期；新增解释单列assumptions。重复JSON键、伪引用、额外认证字段均拒绝。8项新增测试通过；仅协议及解析器，尚未执行真实LLM提取。结构匹配不等于语义蕴含，自动语义检查及实际端到端验证仍待完成，不能据此准入数据。

新增能力匹配`capabilities.py`及公开观测绑定`binding.py`：清单包含全部15正式路线，区分物理分配/共享预算、SOC/能量、流量代理/服务完成。绑定保留字典原子键及列表整数索引，任一显式区域或已观测通配成员缺字段即拒绝，不静默漏判。目标当前不满足仍可绑定，不把绑定成功当作任务通过。这里只覆盖可测量性，尚不证明控制可达、语义保真或数据准入；LLM生成及未见来源端到端验证仍未完成。

最新通用组件：`query_construction/temporal.py`支持公开路径谓词、窗口内逐样本维持及上升沿触发的限时响应，按模拟秒数而不是LLM调用数。缺观测不暂停截止时间；无触发、不完整窗口、截止在采样间隔内无法确认、未知状态分别保持未确定/未评估，不冒称成功。显式条件解除在截止判断前执行并单列released，不能冒充完成响应。还未支持完整原子责任编译、单位/动作可达性绑定或所有合同类型。

该组件不复用旧`conformance_v1.TemporalEvaluator`的合成transition回放或“缺观测暂停deadline”语义；旧工具和历史认证不改写。新组件只读原生样本，不生成状态。27项来源/导入/时序测试通过。`tools/check_query_temporal_recordings.py --output NEW_PATH`已对两条后端的三份既有真实原生记录核对CO2子合同：两个失败、一个通过，全部与直接逐样本检查一致；证据`generated/query_construction_v1/temporal_recordings_v1.json`。这不是新需求端到端验收，也不是整题判分保证。

Mendeley候选的公开metadata确认CC BY 4.0，但页面/API说明的静态读取未给出文件清单；尚未下载访谈、确认匿名化或冻结验证集。下一步继续来源检查，同时补15路线的类型化绑定与公开合同编译；不要因语料下载暂缓而将纯单元测试当作整个goal完成。

最新进展：`query_construction.import_hiis`已导入固定英文CSV的203条、34个参与者，原始快照及来源库存位于`generated/query_construction_v1/hiis_development/`。README的204与文件203明确记录差异，不造第204条。编码从历史导入代码确认CP1252，新增温度/标点保真测试；14项来源/导入测试通过。原七条来源逐字段匹配通过。

发现`/private/tmp/v11-production-repair/prototypes/v11_unified_process_compiler/responsibility_ai_coding_v1/`已有大批AI编码/重述产物，历史暴露范围超出七条。因此整份HIIS旧语料保守设为development，holdout=0；不重新挑划分凑“未见”。这是对原方案的必要更正，目标仍要求另找新来源完成未见验证。

候选新来源仅检查了元信息，尚未导入正文：Open Home Foundation的Home Assistant survey（https://www.openhomefoundation.org/blog/home-assistant-survey-dataset/）发布方说明删除了自由文本评论；不能直接当作逐条自然语言责任语料，最多作为群体/需求类别背景。Mendeley的`sd47rzc4nz/1`（https://data.mendeley.com/datasets/sd47rzc4nz/1）声称有原始访谈及问卷，待许可、匿名化与可下载结构核对后再决定。未发新LLM请求，目标未完成。

以用户当前goal为准：不再逐题修特例；构建来源→原子责任→LLM长期改写→机制匹配→公开合同→原生可行性/反例判分→准入或拒收的可运行流程。全部15正式路线必须在匹配清单内；不支持的机制明确拒收，不编造需求凑覆盖。BOPTEST仍为候选。

## 不能沿用的旧假设

- `build_open_corpus_pilot_v0.py`只接workflow，不代表15路线pipeline。
- 旧七条family已经参与开发，不能重新算作未见验证集。
- `DATASET_CONSTRUCTION_PIPELINE_V1.md`中新增人工双人标注不再是用户认可的依赖；可复用来源/责任/合同分层，但自动验证不得冒称human_validated。
- 当前casebook是授权自编诊断，不是人类数据来源；reference通过不代替合同正确性。
- 原语料`andrematt/trigger_action_rules`包含不同收集群体。2026-09-10查看原始README，声明638条：TAREME 434条（含照护者、领域专家、研究者、学生）及user study 204条；不一律叫真实家庭部署。根目录未见许可证，现有复制语料的发布权限仍待证实，不能从public仓库推断可再分发。

## 工作顺序与完成要求

1. 来源卡、原文定位、收集类型/许可状态；按参与者及重复文本分组，冻结开发/未见验证划分。缺作者身份时保守按语料组隔离，不伪造参与者。
2. 结构化责任提取及LLM改写，保留逐条证据跨度和新增语义检查；分离人类需求、模拟操作条件与私人日程。确切片段匹配不证明语义蕴含。
3. 15路线能力表，需求驱动匹配而非路线硬编码故事；公开所有判分参数，拒绝不可观测/不可控/期限不可达/服务映射缺失的绑定。
4. 通用轨迹合同执行器及正负边界测试，原生可行性/机制对照独立于被测LLM；允许拒收，但不能全拒收冒充端到端成功。
5. 冻结规则后在未参与开发的人类需求上运行，报告全体样本流向、成功/拒收依据、未支持机制与来源偏差；不重选验证集凑通过。
6. 可重复CLI、样本、报告、使用说明。需要新增外发或研究取舍时才询问用户，无新增人工标注依赖。

当前新增`query_construction/sources.py`：原文跨度校验、参与者/跨语料规范化重复文本的传递分组、已见样本强制开发集。仍未执行新语料导入、冻结划分或付费LLM调用，尚不具备端到端完成证据。
