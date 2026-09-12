# V11 unified backend

统一使用及分层验收入口见[当前底座使用与验收](docs/BACKEND_DELIVERY.md)。

当前本机LLM交互底座验收：`/opt/anaconda3/bin/python tools/local_preflight.py --strict --profile autonomous-interaction`。逐项证据和已声明边界见[交付核对表](docs/BACKEND_COMPLETION_AUDIT.md)，不等同于下面的旧Episode发布认证。

当前真实模型交互入口是 `tools/run_trusted_llm_session.py`，默认支持模型自主等待。最新15路由记录、诊断与自然请求的区别见 [自主等待覆盖](docs/AUTONOMOUS_WAIT_COVERAGE_STATUS.md)。当前API已完成真实运行，不再处于旧连接阻塞状态。

离线核对当前记录：`/opt/anaconda3/bin/python tools/check_backend_delivery.py`。它重新核对动作/轨迹、抽样原生来源及D0/D1有限前缀隔离，不发模型请求、不执行模拟器，也不输出任务成功率。此检查不能代替当前代码的原生运行验收。

当前研究定位与底座验收约定见 [Benchmark 核心与可信底座](docs/BENCHMARK_FOUNDATION.md)。可信交互底座完成后，query 构造已恢复：当前证据分层流程、100 条 development 来源扩批和精确 query 原生模型运行分别见 [Query construction V2](docs/QUERY_CONSTRUCTION_V2_DELIVERY.md) 与 [Query scale campaign V1](docs/QUERY_SCALE_CAMPAIGN_V1.md)。候选提取、后端支持、原生可行性、轨迹判分和模型通过仍是独立事实。

最新进展见 [可信后端修复记录](docs/BACKEND_TRUST_REPAIR_STATUS.md)：15/15 后端基本交互和单 seed 完整时域验收已通过。任务可行性、机制 claim 和历史数据发布测试仍单独判断，不由接口通过代替。

本次集中更新的代码、证据与已知边界见 [2026-09-12 更新总览](docs/UPDATE_SUMMARY_2026-09-12.md)。

This directory is the single source tree assembled from the final locally
accepted repair package. It contains the public backend boundary, route
adapters, bounded executor, acceptance tools, tests, protocol documents, and
the Round 11 acceptance evidence under `acceptance/`.

The root keeps only the documents needed for operating and reviewing the
backend. Historical research scripts and old pilot data were removed from the
repository; the current review material is under `docs/`.

The original backend package passed a local Episode-generation acceptance for all 15
route records. The current evidence is under
`acceptance/backend_acceptance_local_final`. These historical results do not
certify the subsequently repaired simulator models or the new benchmark
interaction contract; current native repair evidence is in
`environment_repairs_v1/`. The following capability
boundaries are intentional:

- CityLearn multi-system can generate Episodes, but is not claimed as a
  verified native D3 cross-channel coupling backend.
- FDS uses full-history/prefix replay. Native online stepping is unsupported;
  requests that require it must be rejected.

Large simulator installations and runtime assets are external dependencies and
are not vendored in this repository. Use the pinned local runtimes described by
`TEST_COMMANDS.json` and the route metadata before running native campaigns.

The legacy release preflight below remains fail-closed. It currently rejects
the pre-repair acceptance package because its bound sources differ from the
current implementation. Do not relabel that package as current or interpret
the offline interaction check above as a replacement release certification.
Its dependency checks are still useful:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python tools/local_preflight.py --strict
```

The read-only acceptance check for the current fresh package is:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python \
  tools/backend_acceptance_runner.py --check \
  --output-dir acceptance/backend_acceptance_local_final
```

For LLM multi-turn calls, use `unified_compiler.llm_conversation`. It keeps
the backend's full observations as the source of truth, sends the initial
observation once, and sends lossless V10 `observation_delta` messages on later
turns. It is a provider-neutral message formatter; it does not make model API
calls.
