# Codelab portability checklist (Mac -> Linux)

审查范围：`v11_unified_process_compiler` 的 D0--D3 adapters、
`shared_runtime/`、`shared_assets/` 和 LLM closed-loop runner。本文是迁移
清单，不改变任何 adapter、catalog 或 `generated/` 证据。

## 结论

代码/数据层迁移难度中等，物理 runtime 层不能通过同步目录完成。建议把
codelab 当作一个全新的 Linux runtime target：先同步源代码、冻结数据和
manifest，再在 Linux 上按 manifest 重装/编译 runtime；安装完成后重新跑
各 backend 的 probe/replay gate，不能把 Mac 生成的 gate 当成 Linux 证据。

本机事实（2026-09-05）：

* `python3` 是 CPython 3.13.5 / macOS arm64。
* `shared_runtime/fds/bin/fds` 是官方 FDS 6.11.1 的 macOS x86_64 binary，
  当前通过 Rosetta 2 完成四臂真实 replay；它不能直接搬到 Linux。
* `shared_runtime/modelica/openmodelica/bin/omc` 是 Mach-O arm64，当前通过
  项目 adapter 注入 `OPENMODELICAHOME` 与 gettext/libiconv 动态库路径运行；
  Buildings v13.0.0、AixLib v3.0.1 和 Modelica 标准库均已固定并完成四臂
  真实 replay，但这些 Mac runtime/动态库不能直接搬到 Linux。
* `shared_assets/energyplus_v26.1.0/` 目录名和二进制是
  `Darwin-macOS13-arm64`，包含 `.dylib`、CPython 3.12 Darwin 扩展；D2
  humidity/thermal/direct/residential probes 不能在 Linux 复用该 runtime。
* `shared_runtime/wntr-site-packages/` 的扩展名为
  `*.cpython-313-darwin.so`，Pillow 等依赖还包含 arm64 `.dylib`；即使
  `wntr_runtime_requirements.lock` 已固定版本，也必须在 Linux 重建环境。
* CityLearn 的可复用源数据（`shared_assets/citylearn_v2.5.0/` 中的
  EPW/PV/battery 资产、源 CSV/PTH 若按 release 一起交付）是架构无关的，
  但当前 D3 仍依赖 v5 sibling cache 和 Linux 侧重新安装的 Python 包。

## 按 adapter 分类

| 路由 | 可直接同步 | Linux 上必须做 | 风险/备注 |
| --- | --- | --- | --- |
| D0 `unified_compiler/adapters/d0_exogenous_context.py` | Python 源码、JSON gate/fixtures | 仅标准库 Python | `Path(__file__)` 项目相对；不依赖 Mac binary。 |
| D1 `d1_discrete_device_fault.py` | Python 源码、harness JSON/fixtures | 仅标准库/项目 Python 包 | 纯确定性状态机；同步后跑单元测试即可。 |
| D2 WNTR `unified_compiler/adapters/d2_wntr.py` | adapter、网络定义逻辑、lock、`.inp` 数据 | 用 Linux Python 重新安装 WNTR/NumPy/SciPy/Pandas，并确保 EPANET Linux shared library | 当前 vendored site-packages 是 Darwin/arm64 CPython 3.13；代码已列出 `linux-x64/libepanet.so` 候选，但包里实际需在 Linux 重建并验证。 |
| D2 FDS `d2_fds_adapter.py` | adapter、`d2_fds_assets/*.fds` 模板、Mac replay schema | 安装 Linux FDS 6.11.1（CPU/MPI 版本按 codelab 选择），manifest 记录版本和 SHA-256，并重跑 gate | Mac 使用 x86_64 binary + Rosetta 2；Linux 不复用该 binary，通过 `FDS_EXECUTABLE` 或平台 runtime manifest 注入路径。 |
| D2 EnergyPlus humidity/thermal/direct/residential | IDF/EPW/JSON/CSV 等模型数据、adapter 源码 | 安装 Linux x86_64 EnergyPlus 26.1.0 和匹配 Python API；不要复用 Darwin bundle | 当前 adapter 把 `Darwin-macOS13-arm64` 写进常量，并把 `EPLUS` 插入 `sys.path`；需改为由 runtime manifest/环境变量解析。 |
| D2 Modelica `d2_modelica_buildings_aixlib_adapter.py` | `D2BuildingsAixLib.mo`、probe/adapter 源码、Buildings v13.0.0 与 AixLib v3.0.1 源码 | 安装 Linux OpenModelica，并重跑 gate；模型库源码可复用，编译产物不可复用 | Mac `omc` 是 Mach-O arm64，依赖 gettext/libiconv；Linux 必须使用自己的 ELF runtime 和动态库。 |
| D3 CityLearn `d3_citylearn_coupling_adapter.py` | CityLearn release 资产、schema/CSV/PTH（若纳入发布包）、adapter 源码 | Linux Python 环境安装 CityLearn 2.5.0 及依赖；重建 source cache 和 runtime package | `V5_ROOT = ROOT.parent / v5_scenario_compiler` 是 sibling 布局假设；`citylearn_shared.py` 默认使用 macOS `~/Library/Caches/...` 及固定 `v10.../lib/python3.13/site-packages`，需要显式配置。 |

## 已发现的平台硬编码/路径问题

1. `d2_humidity_air_quality_adapter.py`、多个 EnergyPlus probe 将
   `shared_assets/energyplus_v26.1.0/EnergyPlus-...-Darwin-macOS13-arm64`
   作为固定目录；Linux 应按 `platform/os/arch/version` 解析，不能仅改目录名。
2. `citylearn_shared.py` 的默认 cache 是 `Path.home()/Library/Caches/...`，
   默认 backend site-packages 固定在 v10 的 `lib/python3.13/site-packages`。
   Linux 上应通过 manifest 或 CLI 参数传入，不要依赖开发机 home 目录。
3. `d3_citylearn_coupling_adapter.py` 从 v11 的 sibling 位置寻找 v5
   `source_cache`，并在内存 schema 中写入 `weather.epw` 的绝对路径；应在
   runtime 前将 root 注入为 codelab workspace 路径，输出 provenance 时只保留
   project-relative ref。
4. `v5_scenario_compiler/runtime_bootstrap.py` 依赖
   `Path.home()/.cache/uv/archive-v0` 的内容缓存。该缓存不能视为可迁移资产；
   Linux 上应在独立 venv/uv 环境重新解析或把 wheel/artifact digest 纳入 runtime
   manifest。
5. `build_golden_acceptance.py` 生成的 `REPRODUCE.md` 含
   `/Users/shanyingyu/...` 绝对路径。这是文档 artifact 的可迁移性问题，不是
   物理 adapter 本身；发布前应生成相对路径命令。

## 建议的目录与 manifest

保留当前源码/证据目录不变，在部署层增加平台维度：

```text
v11_unified_process_compiler/
  unified_compiler/                 # 可移植 Python + schemas
  contracts/ conformance_v1/ harness_v2/
  generated/                        # 只读证据；按 target 重跑 gate
  shared_assets/                    # 架构无关模型、EPW、CSV、PTH
  runtime_manifests/
    linux-x86_64-cp313.json         # package + external binary digests
  runtime/
    linux-x86_64-cp313/             # 不入 git 的部署缓存，或单独 artifact
      python/wntr-site-packages/
      bin/fds
      energyplus/26.1.0/
      openmodelica/1.23.x/
      modelica/Buildings/<version>/
      modelica/AixLib/<version>/
  scripts/portability_preflight.py  # 只读检查，不生成 episode
```

`linux-x86_64-cp313.json` 至少应记录：`os`, `arch`, `python_abi`,
`runtime_name`, `version`, `source_url`, `sha256`, `entrypoint`,
`required_env`, `library_roots`（相对路径）、`dependency_probe_command` 和
`license_ref`。adapter 只消费 manifest 解析出的绝对路径；证据和
`PhysicalProcess.manifest` 只写相对 ref + digest。Mac/Linux manifest 必须
分开，不能让同一条 `runtime_sha256` 同时代表两种二进制。

## LLM runner 与物理 backend 的解耦

当前解耦边界已经足够好：`evaluate_harness_v2_v4_flash.py` 和
`evaluate_llm_closed_loop.py` 的 `ChatClient` 只向
`base_url + /chat/completions` 发送 OpenAI 风格 JSON，物理 replay 通过
policy callback 获取动作。因而本地开源模型可以部署成 vLLM、SGLang、
Ollama（OpenAI-compatible endpoint），通常只需替换 `--base-url`、
`--model` 和 API key；不需要把模型权重放进 physical runtime。

迁移时需验证以下协议兼容性：

* 返回 `choices[0].message.content` 为纯文本 JSON；当前 parser 拒绝 markdown、
  重复 JSON key、NaN/Infinity。
* 支持请求里的 `temperature=0`、`max_tokens`；若本地 server 不支持其中一项，
  在 ChatClient 适配层处理，不改 backend。
* 将 API key 通过 codelab secret/env 注入，禁止写入 report、episode 或 manifest。
* `--executor process` 会在 worker 中重新创建 ChatClient；保证 endpoint 可从
  worker 访问，并将 timeout/retry/并发按本地模型吞吐调小或限流。
* 记录 `model`, `base_url`（去除凭据）、请求/响应 digest 和 tokenizer/runtime
  版本；模型替换后重新跑 LLM evaluation，但不重写物理 backend gate。

## Codelab 执行顺序（不需要 Docker）

1. 只同步源码、conformance fixtures、contracts、`shared_assets` 和需要的
   frozen `generated` release；排除 Mac `shared_runtime` binaries、`.venv`、
   `__pycache__`、`.pytest_cache`。
2. 在 Linux codelab 创建 CPython 3.13（或统一选择的 ABI）隔离环境，安装
   `requirements-workflow-release.lock`，再按 backend 安装独立 runtime。
3. 先跑 D0/D1/conformance tests，再逐一运行 WNTR、FDS、EnergyPlus、Modelica、
   CityLearn 的 dependency probe；任一缺失都保持 `EVIDENCE_PENDING`。
4. 只有真实 Linux replay 通过确定性、终止、动作敏感性和 provenance gates 后，
   才生成新的 Linux gate/trajectory evidence；不要覆盖 Mac evidence。
5. 最后以 OpenAI-compatible local endpoint 运行 LLM smoke，再运行完整评估。

## Acceptance checklist

- [ ] `platform.machine()`、Python ABI、runtime manifest 与 codelab target 一致。
- [ ] `find runtime -type f` 不含 `darwin`/`macos`/`arm64` binary（除非 target 明确为 arm64）。
- [ ] 每个 external executable 的 `--version` 和 SHA-256 已记录。
- [ ] WNTR EPANET、FDS、EnergyPlus、OpenModelica + Buildings/AixLib 各自 probe 通过。
- [ ] CityLearn source cache、资产 manifest、相对路径和版本 digest 一致。
- [ ] D0/D1/conformance tests 通过；真实 backend gate 在 Linux 重新生成并保留平台标识。
- [ ] 本地 LLM endpoint 的 response shape、JSON action schema、worker 并发 smoke 通过。
- [ ] 报告和 manifest 不含 API key、绝对 Mac 路径或未声明的 gold action。

**总体判断：**同步“代码 + 只含文本/表格/模型数据的资产”是直接的；同步
当前 `shared_runtime` 不能工作。最大的工作量是为 Linux 重装四类物理 runtime
及其 Python ABI/本地库，而本地开源 LLM 接入属于独立的 OpenAI-compatible
HTTP 适配问题，不需要 Docker，也不应与物理 backend 安装绑在一起。
