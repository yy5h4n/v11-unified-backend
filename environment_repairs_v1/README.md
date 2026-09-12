# 原生环境修复

## 已实现

- **Modelica 双房间**：用原生 ThermalConductor 将两房间接到外温边界；外温在一小时内为 10→0→8°C。Buildings/AixLib 继续拥有全部温度状态。没有在 Python 里生成温降。
- **Modelica 共享热源**：加入同样的散热边界；热水箱热容量改为 200 kJ/K（约48 L水），两段原生取热需求为900 W，其余240 W。共享热源为1500 W，两路独立请求上限仍为1800/1200 W，公开schema说明比例分配。参数是自编pilot场景设定，尚非真实家庭标定。
- **FMI通信**：D2从新FMU描述读取变量编号和GUID，不再依赖旧编译编号。两条route使用原生1秒FMI子步，外部决策仍为60秒，避免整步输入滞后。
- **FDS**：公开窗口由4秒改为60秒，默认10秒反馈；仍支持1秒步长。原生火源从0增长到120 kW，烟炱产率保持0.01。仍明确为prefix replay，并未宣称在线续跑。修复RAMP插值导致未来动作提前影响过去的问题：旧指令保持到决策时刻，之后1微秒内切换。
- **双车EV2Gym**：公开已连接车辆的出发期限、容量和所需电量；从原生车辆对象读取离站后的最终电量，避免漏算最后充电一步。终止回执使用实际终止状态，且不再把observation mask称为action mask。

## 验证与边界

`thermal_verification.json` 包含两条原生Modelica route各60步的idle、一次动作、固定动作、反馈控制完整轨迹。成功要求每个采样两房间18–24°C；共享热源同时要求热水≥40°C。这些是预先规定的pilot服务条件。

`ev_departures.json` 是独立EV运行环境下96步实跑，记录3次离站的实际最终电量。该测试验证回执，不等于已通过全套D3任务评估。

FDS的60秒开/关门原生对照保存在 `fds/growing/`：开门终点温度36.20°C，关门约20°C；两个分支可见度仍为30m。已证明更长窗口和热传播作用，**尚未证明当前场景形成会威胁逃生可见度的烟气暴露**。不能据此标记FDS长期安全case已准入。

这轮没有修改 WNTR、SustainGym 或 CityLearn 的原生场景；其家庭量纲、需求窗口和可行性问题仍须继续处理。所有后端仍保留在casebook覆盖表中。

## 复现

在项目目录执行：

```sh
/opt/anaconda3/bin/python -m pip install --target environment_repairs_v1/tooling cmake
/opt/anaconda3/bin/python tools/compile_repaired_modelica.py d2
/opt/anaconda3/bin/python tools/compile_repaired_modelica.py d3
/opt/anaconda3/bin/python tools/verify_repaired_thermal.py
/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider tests/test_environment_repairs.py
```

编译使用现有OpenModelica和Buildings/AixLib依赖，在临时目录生成原生C并编译；FMU保留在工作区。重新编译会将上一份active构建移到previous目录，不删除旧构建。旧共享模型源未改动。
