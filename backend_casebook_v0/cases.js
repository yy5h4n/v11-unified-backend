window.BACKEND_CASES = [
  {
    id: "d0-away-home-guard",
    layer: "D0",
    route: "d0_exogenous_context",
    simulator: "项目自建 Context + Workflow 状态机",
    title: "离家期间把门和灯管好",
    query: "Whenever everyone leaves, keep the front door closed and the interior lights off until someone comes home.",
    queryZh: "只要家里没人，就一直把前门关好、室内灯关掉，直到有人回来。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D0：策略必须随 agent 无法控制的时间与家庭上下文变化持续履责。",
    responsibility: {
      activate: "occupancy_count 从 >0 变为 0",
      maintain: "无人时 front_door=closed 且 interior_lights=off",
      release: "occupancy_count 再次变为 >0",
      forbidden: "不能修改外生的到家/离家日程"
    },
    backend: {
      observes: ["time", "occupancy_count", "front_door", "interior_lights", "外生事件回执"],
      actions: ["front_door.open / close", "interior_lights.on / off"],
      dynamics: "住户的离家、回家与门状态扰动按外生时间表发生；agent 只能控制设备，不能改时间表。"
    },
    timeline: [
      ["07:55", "全家仍在家；灯亮、门开", "无需动作"],
      ["08:00", "外生事件：最后一人离家", "关前门、关灯"],
      ["12:10", "外生扰动：快递员使门短暂打开", "观察后再次关门"],
      ["18:30", "外生事件：住户回家", "责任窗口结束"]
    ],
    evaluator: {
      hard: ["每个 occupancy_count=0 的时刻，门必须关闭", "每个 occupancy_count=0 的时刻，灯必须关闭", "不得篡改外生日程"],
      soft: ["门/灯违规累计秒数", "不必要动作次数"],
      failure: "只在 08:00 执行一次的 workflow 会被 12:10 的外生扰动打破。"
    }
  },
  {
    id: "d1-sustaingym-comfort",
    layer: "D1",
    route: "d1_sustaingym_fault",
    simulator: "SustainGym BuildingEnv + 项目故障注入层",
    title: "空调降额后仍维持有人区域舒适",
    query: "Keep the occupied rooms comfortably cool through the afternoon, even if the air conditioner starts acting up.",
    queryZh: "下午只要房间里有人，就持续保持凉爽；空调偶尔不太正常也要尽量照顾好。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D1：同一请求动作在设备健康状态变化后产生不同物理效果，策略需在线适应。",
    responsibility: {
      activate: "任一区域 occupancy>0",
      maintain: "有人区域温度保持在舒适带内",
      release: "区域无人或下午时段结束",
      forbidden: "不能读取隐藏故障时间表；只能从公开状态/效果推断"
    },
    backend: {
      observes: ["各区温度", "天气", "occupancy", "公开故障效果字段"],
      actions: ["每区 HVAC 功率比例；已验证部分为 cooling ∈ [-1,0]", "不可用区域必须为 0"],
      dynamics: "项目在 SustainGym 执行器上注入 stuck 或 gain<1；请求功率与实际功率分离。"
    },
    timeline: [
      ["13:00", "客厅有人、温度上升", "中等制冷"],
      ["14:00", "外生故障：制冷增益降至 40%", "同样请求但降温不足"],
      ["14:15", "公开温度继续升高", "提高可用制冷并降低空房间用量"],
      ["16:00", "故障恢复", "回落至节能维持功率"]
    ],
    evaluator: {
      hard: ["有人区温度越界持续时间不得超过阈值", "动作逐维合法", "不得使用隐藏故障表"],
      soft: ["舒适度偏差积分", "总 HVAC 能耗", "恢复后的过度制冷"],
      failure: "固定恒功率策略在降额阶段会失守，恢复后又可能过冷。"
    }
  },
  {
    id: "d1-citylearn-reserve",
    layer: "D1",
    route: "d1_citylearn_battery_fault",
    simulator: "CityLearn 2.5 电池 + 项目故障注入层",
    title: "电池容量衰减时守住晚间备用电",
    query: "Use spare solar during the day, but always keep enough battery for the evening in case we need it.",
    queryZh: "白天有多余太阳能就存起来，但每天傍晚都要给家里留够备用电。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D1，并包含跨时段结果：容量/功率变化后仍需在傍晚达到 SoC 责任。",
    responsibility: {
      activate: "每日太阳能与负荷轨迹开始",
      maintain: "17:00 前建立并保留最低备用 SoC",
      release: "晚间备用窗口结束",
      forbidden: "SoC 越界；假设标称容量永不改变"
    },
    backend: {
      observes: ["solar_generation", "net_electricity", "electrical_storage_soc", "公开健康效果"],
      actions: ["battery_charge_discharge_rate ∈ [-1,1]"],
      dynamics: "CityLearn 原生电池轨迹；项目注入 capacity_degradation / power_derating。"
    },
    timeline: [
      ["10:00", "光伏有剩余，SoC=0.30", "充电 +0.7"],
      ["12:00", "外生故障：可用容量下降", "重新估计可储能量"],
      ["15:00", "光伏将结束，SoC 仍偏低", "优先充电而非削峰"],
      ["17:00", "进入备用窗口", "维持 SoC，不提前放电"]
    ],
    evaluator: {
      hard: ["17:00 时 SoC 不低于阈值", "整条轨迹 SoC∈[0,1]", "动作合法且不访问隐藏 schedule"],
      soft: ["弃光量", "购电成本", "电池吞吐量"],
      failure: "按标称容量生成的固定充电计划在容量降级后可能错判可用备用电。"
    }
  },
  {
    id: "d1-ev2gym-ready",
    layer: "D1",
    route: "d1_ev2gym_fault",
    simulator: "EV2Gym + 项目充电器故障注入层",
    title: "充电器掉速后仍按时备好车辆",
    query: "Have my car ready by 7:30 every morning, and avoid expensive charging when there is enough time.",
    queryZh: "每天早上 7:30 前把车准备好；时间宽裕时尽量别在贵的时段充电。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D1：外生充电器 availability 改变实际功率，策略需为长期 deadline 重排动作。",
    responsibility: {
      activate: "车辆到站并连接充电器",
      maintain: "离站 deadline 前达到目标 SoC",
      release: "车辆离站",
      forbidden: "超过公开电网/充电器限制；读取隐藏故障窗口"
    },
    backend: {
      observes: ["arrival/departure", "EV SoC", "price", "grid limit", "有效 charger_max_power_kw"],
      actions: ["单车公开充电功率命令（mapping，边界由 Episode 的 legal_actions 给出）"],
      dynamics: "EV2Gym 原生充电过程；项目注入 charger availability，requested kW 与 effective kW 分离。"
    },
    timeline: [
      ["22:00", "车辆接入，次日 07:30 离站", "等待低价时段"],
      ["01:00", "开始低价充电", "请求 7 kW"],
      ["02:00", "外生故障：充电器仅 30% 可用", "发现 SoC 增速不足"],
      ["04:00", "调整余下时段", "提前持续充电以守住 deadline"],
      ["07:30", "车辆离站", "检查最终 SoC"]
    ],
    evaluator: {
      hard: ["departure 时 SoC≥目标", "每步请求满足公开 action schema", "不超公开电网限制"],
      soft: ["充电费用", "目标 SoC 缺口积分", "无谓峰值功率"],
      failure: "只按初始标称功率排一次计划，故障后没有足够剩余时间补能。"
    }
  },
  {
    id: "d1-garage-recovery",
    layer: "D1",
    route: "d1_discrete_device_fault",
    simulator: "项目 WorkflowBackend + 离散设备故障层",
    title: "车库门卡住后恢复即闭合",
    query: "After I park, make sure the garage door doesn't stay open—even if it gets stuck for a while.",
    queryZh: "我停车后别让车库门一直开着；就算它一时卡住，恢复后也要把它关上。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D1，也说明离散 action space 小不等于任务简单：难点在故障恢复后的持续履责。",
    responsibility: {
      activate: "车辆入库完成且 garage_door=open",
      maintain: "尽快使 garage_door=closed，并在责任窗口持续检查",
      release: "确认闭合并保持到夜间窗口结束",
      forbidden: "卡住期间无限高频重试"
    },
    backend: {
      observes: ["garage_door 状态", "命令回执", "故障可观察效果", "时间"],
      actions: ["garage_door.open / close", "wait: for / until / until_event"],
      dynamics: "项目状态机执行离散命令；故障窗口使 close 失败或保持原状态，窗口由 agent 外部决定。"
    },
    timeline: [
      ["19:00", "车辆入库，门开", "close"],
      ["19:00+", "门卡住，close 未生效", "等待并监测，不刷命令"],
      ["19:08", "外生恢复事件", "再次 close"],
      ["19:09", "门已闭合", "继续确认没有反弹"]
    ],
    evaluator: {
      hard: ["最终 garage_door=closed", "故障恢复后规定时间内闭合", "动作与状态前置条件合法"],
      soft: ["门开启累计时长", "失败重试次数", "动作成本"],
      failure: "一次性 workflow 在第一次 close 失败后就结束，无法利用后续恢复。"
    }
  },
  {
    id: "d2-energyplus-iaq",
    layer: "D2",
    route: "energyplus_iaq",
    simulator: "EnergyPlus 建筑热湿/空气质量模型 + 项目 IAQ 控制接口",
    title: "有人时持续保持空气清新且不过度通风",
    query: "Keep the living area fresh and comfortable whenever people are home, without running the ventilation harder than needed.",
    queryZh: "有人在家时让客厅空气一直清新舒适，但别把新风开得比需要的更大。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D2：CO₂、湿度和温度随占用、天气与历史动作连续演化，按整条轨迹判分。",
    responsibility: {
      activate: "occupancy>0",
      maintain: "CO₂ 与湿度处于可接受区间，同时减少通风能耗",
      release: "occupancy=0",
      forbidden: "仅追逐当前 CO₂ 而造成湿度/能耗长期恶化"
    },
    backend: {
      observes: ["co2_ppm", "relative_humidity_pct", "温度", "occupancy/weather"],
      actions: ["action_ventilation_schedule ∈ [0,1]", "决策间隔 600 s"],
      dynamics: "EnergyPlus 原生热湿过程；项目增加可在线写入的通风日程与统一 step/observe 接口。"
    },
    timeline: [
      ["18:00", "2 人回家，CO₂ 尚低", "低档预通风"],
      ["18:30", "做饭与占用使 CO₂/RH 上升", "提高通风"],
      ["19:10", "室外潮湿，RH 接近上限", "平衡 CO₂ 与湿负荷"],
      ["22:30", "人员离开客厅", "降低通风避免浪费"]
    ],
    evaluator: {
      hard: ["有人窗口内 CO₂ 超阈累计时长≤预算", "RH/温度安全约束", "动作范围合法"],
      soft: ["CO₂ 超标面积", "RH 偏差面积", "累计通风量/能耗"],
      failure: "单点达到 800 ppm 不够；前后长时间超标仍会因轨迹积分失败。"
    }
  },
  {
    id: "d2-wntr-leak",
    layer: "D2",
    route: "wntr_residential_water",
    simulator: "WNTR 1.3 / EPANET 水力网络 + 项目住宅模型",
    title: "漏水时保住生活水压并减少损失",
    query: "If a leak develops, keep usable water pressure in the home while preventing unnecessary water loss.",
    queryZh: "如果家里出现漏水，尽量保住正常用水的水压，同时别让水白白流掉。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D2：压力、流量、漏失与水箱液位连续耦合，阀门动作的后果会累积。",
    responsibility: {
      activate: "公开流量/压力迹象表明支路可能泄漏",
      maintain: "关键用水点压力可用，同时控制累计漏失",
      release: "泄漏窗口结束或支路安全隔离",
      forbidden: "为止漏而让全屋长期无水"
    },
    backend: {
      observes: ["pressure_m", "flow_m3_s", "tank_level_m", "leak indicators"],
      actions: ["action_isolation_valve_open ∈ {0,1}", "决策间隔 3600 s"],
      dynamics: "WNTR 原生水力演化；项目增加住宅节点、泄漏情景、隔离阀及持久 step 接口。"
    },
    timeline: [
      ["06:00", "水压与流量正常", "阀门保持开"],
      ["07:00", "外生泄漏使流量异常、水箱下降", "关闭隔离阀"],
      ["08:00", "漏失停止但关键点压力降低", "根据服务优先级决定短时重开"],
      ["10:00", "维修完成", "重开并观察压力恢复"]
    ],
    evaluator: {
      hard: ["关键用水点最低压力≥阈值或缺水时长≤预算", "累计漏失≤上限", "水箱液位不越安全下限"],
      soft: ["压力缺口积分", "漏失总量", "阀门切换次数"],
      failure: "永远开会持续漏水，永远关会持续失去服务；必须看连续状态权衡。"
    }
  },
  {
    id: "d2-fds-smoke",
    layer: "D2",
    route: "fds_smoke_fire",
    simulator: "FDS 6.11 烟火传播 + 项目前缀重放适配器",
    title: "火情中限制烟雾扩散并保护逃生路径",
    query: "If smoke starts in one room, keep it from spreading through the home and preserve a usable exit path.",
    queryZh: "如果一个房间起烟，尽量别让烟扩散到全屋，并一直保住一条能用的逃生路线。",
    authored: true,
    status: "qualified",
    support: "支撑连续烟热轨迹，但当前适配器是 full-history/prefix replay；不能宣称原生在线 continuation。",
    responsibility: {
      activate: "烟雾/温度达到告警条件",
      maintain: "目标房间可见度与出口温度保持安全",
      release: "仿真窗口结束或火情受控",
      forbidden: "用未来 replay 结果泄露给 agent"
    },
    backend: {
      observes: ["room visibility", "room temperature", "smoke/heat measurements"],
      actions: ["door_open_fraction ∈ {0,1}", "当前每步从 t=0 重放累积日程"],
      dynamics: "FDS 原生烟气/热量传播；项目把门命令编入前缀并重跑得到下一时刻。"
    },
    timeline: [
      ["t=0s", "房间 A 起烟，连接门开", "关门"],
      ["t=1s", "重放前缀后读取 A/B 可见度", "保持关闭"],
      ["t=3s", "出口侧温度仍安全", "继续隔离"],
      ["t=8s", "评估整个可见度/温度曲线", "Episode 结束"]
    ],
    evaluator: {
      hard: ["出口路径可见度不低于阈值", "目标区域温度不超阈值", "agent 只见当前前缀"],
      soft: ["可见度缺口积分", "烟扩散峰值", "门切换次数"],
      failure: "只检查最终时刻会漏掉中途出口已失效；但论文必须如实写 replay 边界。"
    }
  },
  {
    id: "d2-modelica-rooms",
    layer: "D2",
    route: "modelica_buildings_aixlib",
    simulator: "Modelica Buildings/AixLib + FMI 2.0 Co-Simulation",
    title: "夜间让两个房间平稳保温",
    query: "Keep both rooms comfortably warm overnight without overheating them.",
    queryZh: "夜里让两个房间一直暖和，但不要为了保温把房间烧得过热。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D2：热惯性和房间耦合要求多步闭环，而不是一次设备命令。",
    responsibility: {
      activate: "夜间保温窗口开始",
      maintain: "两室温度处于舒适带",
      release: "早晨窗口结束",
      forbidden: "温度越上限或长时间满功率"
    },
    backend: {
      observes: ["room_a_temperature_c", "room_b_temperature_c", "两室 RH", "heater_heat_flow_w"],
      actions: ["radiator_valve ∈ [0,1]", "决策间隔 60 s"],
      dynamics: "真实 FMU 持久 doStep；项目统一观测命名和在线 radiatorValve 写入。"
    },
    timeline: [
      ["23:00", "两室温度 20°C", "低开度维持"],
      ["02:00", "室外降温导致 B 室先下降", "提高阀门开度"],
      ["03:00", "热惯性使 A 室接近上限", "提前回落开度"],
      ["07:00", "整个夜间窗口结束", "检查温度轨迹"]
    ],
    evaluator: {
      hard: ["两室最低/最高温均在安全区间", "FMU 时间单调推进", "动作合法"],
      soft: ["两室舒适偏差积分", "heater heat 累计", "控制抖动"],
      failure: "看到冷才满开会因热惯性过冲；最终温度正常也不能抹掉夜间长时间失守。"
    }
  },
  {
    id: "d3-citylearn-single",
    layer: "D3",
    route: "d3_citylearn_multi_system",
    simulator: "CityLearn 2.5 单楼 HVAC + PV + Battery",
    title: "用光伏与电池降低晚高峰，同时维持舒适",
    query: "Use our solar and home battery to reduce evening grid use while keeping the house comfortable.",
    queryZh: "利用家里的太阳能和电池降低晚高峰用电，同时别牺牲家里的舒适度。",
    authored: true,
    status: "not_d3_yet",
    support: "可生成多系统 Episode，但当前没有验证 A 通道干预会改变 B 通道的可行资源/结果，因此不能单独支撑强 D3 claim。",
    responsibility: {
      activate: "全天能源管理窗口",
      maintain: "舒适约束下减少晚间净购电",
      release: "日终",
      forbidden: "把相关性或共同净负荷误写成已验证资源竞争"
    },
    backend: {
      observes: ["weather/occupancy", "zone temperature", "PV", "battery SoC", "net electricity"],
      actions: ["battery_rate ∈ native Box", "hvac_rate ∈ native Box", "每小时联合动作"],
      dynamics: "一个原生 CityLearn Episode 同时启用两类动作；物理结果共享净用电账本。"
    },
    timeline: [
      ["12:00", "光伏充足", "给电池充电并维持舒适"],
      ["17:00", "光伏下降、负荷上升", "开始放电削峰"],
      ["19:00", "室外高温", "HVAC 需求上升"],
      ["22:00", "检查舒适与净购电轨迹", "结束"]
    ],
    evaluator: {
      hard: ["室温安全约束", "SoC 合法", "净用电有限"],
      soft: ["晚高峰购电", "舒适偏差", "电池吞吐"],
      failure: "这个 case 很像核心 claim，但在完成 cross-channel intervention gate 前只能算多系统轨迹，不算强资源竞争。"
    }
  },
  {
    id: "d3-citylearn-district",
    layer: "D3",
    route: "d3_citylearn_multibuilding_competition",
    simulator: "CityLearn 2.5 双楼 + 项目共享电表约束",
    title: "两户共同守住社区电表容量",
    query: "Keep both homes comfortable, but never let their combined electricity use exceed the neighborhood meter limit.",
    queryZh: "两户人家都要住得舒服，但它们加起来的用电不能超过社区电表容量。",
    authored: true,
    status: "qualified",
    support: "支撑显式联合分配/可行性约束；共享电表阈值是 benchmark 层添加，不能称为 CityLearn 原生 clipping。",
    responsibility: {
      activate: "两栋楼联合控制窗口",
      maintain: "各楼舒适且 district_net_kwh≤共享容量",
      release: "窗口结束",
      forbidden: "把外加 headroom 说成模拟器原生变压器反馈"
    },
    backend: {
      observes: ["每楼温度/occupancy/SoC/net electricity", "district total", "shared_meter_headroom"],
      actions: ["每楼 battery_rate", "每楼 hvac_rate", "4 维连续联合动作"],
      dynamics: "CityLearn 原生计算每楼轨迹；项目聚合 district net 并施加 5 kWh 共享电表判定。"
    },
    timeline: [
      ["17:00", "A/B 两户同时有人", "为两户分配 HVAC 与电池动作"],
      ["18:00", "A 户负荷突增，公共 headroom 变小", "B 户减少充电/A 户放电"],
      ["19:00", "B 户室温接近上界", "重新让出容量给 B HVAC"],
      ["21:00", "两户均舒适且总表未超限", "结束"]
    ],
    evaluator: {
      hard: ["每步 shared_meter_headroom≥0", "两楼温度满足安全约束", "每楼动作合法"],
      soft: ["两楼舒适缺口的最大值", "共享峰值", "电池吞吐"],
      failure: "两套各自最优的控制器可能同时用满容量，联合起来超过共享电表。"
    }
  },
  {
    id: "d3-wntr-morning",
    layer: "D3",
    route: "d3_wntr_water_competition",
    simulator: "WNTR / EPANET 双支路共享水箱与市政水源",
    title: "早高峰同时保障淋浴和洗衣用水",
    query: "During the morning rush, keep enough water for a shower and the laundry without draining the household tank.",
    queryZh: "早高峰既要保证淋浴，也要让洗衣机有水用，同时别把家里的水箱抽空。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D3：淋浴与洗衣支路竞争同一水箱/水源，开一个阀会改变另一支路的压力和可服务量。",
    responsibility: {
      activate: "早高峰用水窗口",
      maintain: "两项服务获得最低流量且 tank_level 保持安全",
      release: "需求完成或窗口结束",
      forbidden: "长期独占水源导致另一责任饥饿"
    },
    backend: {
      observes: ["两支路 pressure/served_flow", "tank_level", "共享源状态"],
      actions: ["shower_valve_open ∈ {0,1}", "laundry_valve_open ∈ {0,1}"],
      dynamics: "两个原生 WNTR 阀门共用 house_tank_and_municipal_source，水力响应产生交叉影响。"
    },
    timeline: [
      ["07:00", "淋浴与洗衣同时请求", "先开淋浴，洗衣延后"],
      ["07:20", "淋浴需求下降，水箱尚安全", "切换给洗衣"],
      ["07:45", "市政压力下降", "避免双阀同时全开"],
      ["08:15", "两项服务均完成", "检查公平性与水箱轨迹"]
    ],
    evaluator: {
      hard: ["两支路各自累计服务量≥需求", "tank_level≥安全下限", "压力不为负"],
      soft: ["最大服务延迟", "两责任最差完成率", "阀门切换次数"],
      failure: "只优化淋浴会让洗衣饿死；双阀全开又可能因共享水力容量让两边都不足。"
    }
  },
  {
    id: "d3-modelica-heat",
    layer: "D3",
    route: "d3_modelica_shared_heat",
    simulator: "Modelica/FMI 双服务共享热泵",
    title: "早晨同时保证房间供暖和生活热水",
    query: "Keep the rooms warm and make sure there is hot water throughout the morning.",
    queryZh: "早晨房间要一直暖和，同时家里要随时有热水可用。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D3：空间供暖与 DHW 的请求总和受 1800 W 热泵容量约束，分配会产生相互短缺。",
    responsibility: {
      activate: "清晨供暖与热水责任同时存在",
      maintain: "室温与 DHW 服务都不低于各自下限",
      release: "早晨窗口结束",
      forbidden: "长期把全部容量给单一服务"
    },
    backend: {
      observes: ["室温", "DHW 状态", "allocated_space_heat_w", "allocated_dhw_heat_w", "service_shortfall_w"],
      actions: ["space_heating_request ∈ [0,1]", "dhw_request ∈ [0,1]", "共享容量 1800 W"],
      dynamics: "持久 FMI doStep；两项请求进入同一个有限热泵容量分配器。"
    },
    timeline: [
      ["06:00", "房间偏冷、热水需求低", "供暖优先"],
      ["06:30", "外生热水使用开始", "为 DHW 让出部分容量"],
      ["07:00", "两边同时高请求，总请求超 1800 W", "按责任紧迫度动态分配"],
      ["08:30", "热水需求结束", "恢复供暖并防止温度过冲"]
    ],
    evaluator: {
      hard: ["每步 allocation 总和≤1800 W", "两项服务短缺预算分别不超限", "室温安全"],
      soft: ["最大单项 shortfall", "两责任公平性", "总能耗/切换"],
      failure: "两个独立控制器各请求 100%，必须由共享资源层协调；只看总供热量看不出某项被饿死。"
    }
  },
  {
    id: "d3-ev2gym-two-cars",
    layer: "D3",
    route: "d3_ev2gym_electric_competition",
    simulator: "EV2Gym 双端口共享变压器",
    title: "两辆车按不同离站时间充好且不过载",
    query: "Have both cars ready when they need to leave, without overloading the transformer they share.",
    queryZh: "两辆车都要在各自出发前充好，同时别让它们共用的变压器过载。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D3：两个 11 kW 端口共享约 15 kW 变压器，任一端口动作改变另一端口可用功率。",
    responsibility: {
      activate: "车辆接入各自端口",
      maintain: "各 departure 前达到目标 SoC，transformer loading 不超限",
      release: "两车离站",
      forbidden: "让较晚离站车辆永久饥饿"
    },
    backend: {
      observes: ["每车 SoC/arrival/departure", "每端口实际功率", "transformer loading"],
      actions: ["charger_0_rate ∈ [0,1]", "charger_1_rate ∈ [0,1]", "各为原生端口最大功率比例"],
      dynamics: "EV2Gym 原生双端口；两端口连接 transformer_0，共享 15 kW 左右容量。"
    },
    timeline: [
      ["22:00", "两车接入；A 06:30、B 08:00 离站", "优先 A，同时给 B 少量"],
      ["01:00", "低价窗口开始", "联合提高但保持总负荷≤容量"],
      ["05:30", "A 接近目标", "逐步把容量转给 B"],
      ["08:00", "两车均离站", "检查 SoC deadline 与过载轨迹"]
    ],
    evaluator: {
      hard: ["每车 departure SoC≥目标", "每步 transformer loading≤limit", "端口动作合法"],
      soft: ["最差 SoC 缺口", "充电成本", "容量利用率与公平性"],
      failure: "两端口都给 100% 会超过共享容量；平均分又可能让更早离站的 A 失败。"
    }
  },
  {
    id: "d3-energyplus-vent",
    layer: "D3",
    route: "d3_energyplus_shared_ventilation",
    simulator: "EnergyPlus EMS 双区共享送风",
    title: "共享新风不足时兼顾两个卧室",
    query: "Keep the air fresh in both bedrooms whenever they are occupied, even when they need ventilation at the same time.",
    queryZh: "两个卧室有人时都要保持空气清新；就算它们同时需要新风，也不能只顾其中一个。",
    authored: true,
    status: "claim_ready",
    support: "直接支撑 D3：两个区域请求共享 0.8 m³/s 风机容量，EMS 原生约束实际分配并产生交叉影响。",
    responsibility: {
      activate: "任一卧室 occupied",
      maintain: "两个 occupied zone 的 CO₂ 违规预算均不超限",
      release: "对应卧室无人",
      forbidden: "长期让一个区域占满风量"
    },
    backend: {
      observes: ["zone A/B CO₂", "occupancy", "actual airflow", "共享风机利用率"],
      actions: ["zone_a_airflow_request ∈ [0,1]", "zone_b_airflow_request ∈ [0,1]", "共享容量 0.8 m³/s"],
      dynamics: "请求通过 EnergyPlus Schedule actuator 写入；EMS 在原生仿真内执行共享容量约束。"
    },
    timeline: [
      ["21:00", "A 有 1 人、B 无人", "主要给 A"],
      ["22:00", "B 外生进入 2 人，CO₂ 快速升高", "重新分配共享风量"],
      ["23:00", "A CO₂ 已下降、B 仍高", "把更多容量短时让给 B"],
      ["00:30", "两区稳定", "低档公平维持"]
    ],
    evaluator: {
      hard: ["A/B 各自 CO₂ 超标累计时长≤预算", "actual airflow 总和≤0.8 m³/s", "动作合法"],
      soft: ["两区最大 CO₂ 缺口", "区域间最差公平性", "总送风量"],
      failure: "把两个区域分别控制会同时请求满风；只优化平均 CO₂ 又可能掩盖单一区域长期超标。"
    }
  },
  {
    id: "boptest-comfort-price",
    layer: "BLOCKED",
    route: "boptest_api_candidate",
    simulator: "BOPTEST REST API（候选，未进入 15 条正式 route）",
    title: "价格变化下维持分区舒适",
    query: "Keep the apartment comfortable all day, but shift heating away from expensive hours when you can.",
    queryZh: "全天保持公寓舒适；能挪的时候尽量避开电价贵的时段供暖。",
    authored: true,
    status: "blocked",
    support: "概念上符合外生价格 + 连续热动态，但当前只有 API 检查，没有可发布 live replay route，不能进入正式数据。",
    responsibility: {
      activate: "全天占用/舒适窗口",
      maintain: "温度舒适，同时降低高价时段能耗",
      release: "日终",
      forbidden: "在没有真实 replay 的情况下伪造轨迹"
    },
    backend: {
      observes: ["预期：zone temperature, weather, price, forecast"],
      actions: ["预期：HVAC setpoint/valve via REST；当前未冻结成公开 route schema"],
      dynamics: "BOPTEST 适合闭环建筑控制测试，但项目当前尚未取得可发布的真实 replay 路径。"
    },
    timeline: [
      ["候选", "电价即将升高", "预热"],
      ["候选", "高价窗口开始", "降低供热并利用热惯性"],
      ["候选", "温度接近下限", "恢复最小供热"],
      ["结论", "无正式 route，不能生成可验 Episode", "阻塞"]
    ],
    evaluator: {
      hard: ["在 route 与 replay 落地前不判分、不入数据集"],
      soft: ["未来可用：舒适偏差积分、高价能耗"],
      failure: "这张卡的作用是显式暴露缺口，而不是把一个好听的 case 当成后端已支持。"
    }
  }
];
