"""Per-case data review, distinct from successful backend interaction evidence."""
from unified_compiler.route_registry import route_metadata

# These are authoring decisions, not invented evaluator thresholds or gold scores.
REVIEWS = {
 'd0_exogenous_context': ('自编合同已校验，待人类来源', '离家后门关、灯灭，并应对再次开门。', '离家后120秒内完成两项；维持期间外生开门后60秒内恢复。所有原生样本都检查，回来不抹去此前违规。', '已验证原生可行策略及漏做/迟做/做错反例。仍需回接开源人类需求与多场景；不是正式数据准入或LLM成绩。'),
 'd1_sustaingym_fault': ('场景不匹配', '在用房间持续凉爽，故障及恢复后也不能过冷。', '公开在用区域和舒适带下的逐区温度轨迹。', '当前制冷场景出现13.86°C；需先修原生天气/初始条件并验证制冷可行性，不能把舒适下限调低来迁就轨迹。'),
 'd1_citylearn_battery_fault': ('补储备合同', '指定晚间服务窗口持续有足够备用电，允许电网补电。', '窗口内每步可用储备量，不是终点SOC。', '8小时片段尚未绑定晚间窗口、储备目标与有效容量；旧终点35%不是这条长期需求的判分。'),
 'd1_ev2gym_fault': ('补离站判分', '车每次出发时电量足够，有余裕再省钱。', '逐次实际离站交付及窗口内家庭功率约束。', '不能只取最后一次departure_soc；每个目标需与对应车辆/离站事件匹配，未离站窗口应标删失而非成功。'),
 'd1_discrete_device_fault': ('补恢复合同', '停车后不让车库门长时间敞开。', '实际门状态与可操作时间、恢复时限。', '尝试操作故障设备是执行失败，不是答案格式错；停车触发依据与不可操作期间如何判分必须公开。'),
 'energyplus_iaq': ('补公开阈值', '持续保持室内空气清新，兼顾通风消耗。', '全部原生样本CO2与暴露积分；省风仅为次要指标。', '改为全时段责任，避免依赖未绑定的占用窗口；1200ppm是待公开确认的模拟任务阈值，不是人类来源原话。'),
 'wntr_residential_water': ('尺度与可行性阻塞', '需要时有水，漏水时减少损失。', '需求窗口每支路服务与漏水累计，不能看最高一次水压。', '家庭尺度/需求量未校准；阀门止漏是否必然断供尚须验证。暂不生成家庭服务成功标签。'),
 'fds_smoke_fire': ('机制不匹配', '烟气发展期间保持逃生路径可用。', '整个撤离窗口的可见度与温度，而非仅一次关门。', '原生门动作已影响温度，但烟气可见度挑战未证明；保留原需求，暂停该case准入，不偷换成温度控制题。'),
 'modelica_buildings_aixlib': ('补舒适合同', '两个房间持续暖和。', '两个房间每步温度、越界度时及最长越界。', '原生有散热/供热对照；当前只跑1小时，不能当作整夜成功。需公开舒适带、初始宽限和片段覆盖范围。'),
 'd3_citylearn_multi_system': ('限制claim归属', '保持舒适，同时用电池减少购电。', '舒适硬条件与完整区间购电分别统计。', '可作为连续多设备责任，但没有跨通道资源竞争证据；不能因route名含D3就当作强D3。'),
 'd3_citylearn_multibuilding_competition': ('补共享预算合同', '两户持续舒适，同时遵守共用电表预算。', '每楼舒适与每原生区间电表净能量预算同时满足。', '预算单位是kWh/区间，不是原生供电功率裁剪；预充电可行性、公开舒适目标与预算处理仍需验证。'),
 'd3_wntr_water_competition': ('尺度与服务合同阻塞', '早上洗澡和洗衣两项服务都有足量供水。', '逐窗口供水量、压力与未满足需求，不是两路累计流量大于0。', '已观察到延迟水箱耦合；但原生流量代理未校准为洗澡/洗衣交付，需求量和可延迟窗口未定义。'),
 'd3_modelica_shared_heat': ('补双服务合同', '房间暖和、热水够用，同时维持两项服务。', '房温与热水服务结果，不要求同时开两个执行器。', '原生有限热量分配有效；需公开温度/取水需求并验证整个窗口。固定分配可过，不足以声称必须动态规划。'),
 'd3_ev2gym_electric_competition': ('补逐车事件合同', '两辆车按时有电，且不使共享变压器超载。', '每车每次离站的最终交付与所有区间变压器约束。', '必须消费真实离站回执，包括最后一次充电后立即离站；共享约束不是后端自动替策略限流。'),
 'd3_energyplus_shared_ventilation': ('补恢复与维持合同', '先改善两房间的闷空气，然后持续保持清新。', '公开恢复截止后两房间所有原生样本达标，中途违规不能被终点恢复抹掉。', '90分钟/1200ppm需要作为公开任务条件验证；按实际模拟时间判，不按模型调用序号；共享风量是真实分配。'),
 'boptest': ('无可执行后端', '有人使用的房间保持舒适并节能。', '候选需求，不能提供运行成绩。', '尚无正式执行route，不能混入15条正式后端的成功率。'),
}


def review_for(route):
    status, need, judge, issue = REVIEWS[route]
    return {'status': status, 'user_need': need, 'judge': judge, 'unresolved': issue,
            'admission_ready': False, 'task_success': None,
            'provenance': 'user_authorized_synthetic_case_not_human_grounded',
            'runtime': route_metadata(route) if route != 'boptest' else None,
            'evaluator_status': ('authored_contract_native_and_adversarial_checks' if route == 'd0_exogenous_context' else 'contract_and_adversarial_tests_pending'),
            'explanation': '需求设计核对，不是数据、评测或任务通过证明；后端交互通过不能解除这些缺口。'}
