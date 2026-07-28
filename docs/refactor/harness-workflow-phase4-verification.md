---
feature_ids:
  - harness-workflow-refactor
  - AC-07
  - AC-08
  - AC-09
topics:
  - harness
  - provider-adapter
  - builtin-model
  - cli-agent
  - capability-preflight
doc_kind: verification-record
created: 2026-07-28
---

# Harness 与工作流重构 Phase 4 验证记录

## 1. 范围与结论

本阶段只实施重构计划 Phase 4：将内置模型、Codex CLI、Claude Code 和
OpenCode 收敛到同一个领域中立 `AgentHarnessFacade` 与薄 Provider Adapter
边界。工作流 Orchestrator 继续是 Task、Attempt、RunSnapshot、节点状态和
Artifact 的唯一真相源；Adapter 只负责执行器能力、启动、输入传输、事件、
取消、续接和候选产物。

本阶段没有开始 Phase 5 的 Governance Plugin、Phase 6 的 checkpoint/HITL/
subagent，也没有迁移或删除历史工作流。专业测试旧路径由显式 legacy
compatibility 模块隔离；V3 普通 Harness 契约不再携带 SFMEA、黑盒测试、
Test Activity 或其他领域字段。

## 2. 实现结果

- 新增稳定的 `ProviderCapabilities`、`ProviderSession`、
  `ProviderResumeToken`、`ArtifactCandidate`、`CancelResult` 与
  `ProviderUnsupported` 公共契约；
- 新增 `BuiltinModelAdapter`、`CodexCliAdapter`、`ClaudeCliAdapter` 和
  `OpenCodeAdapter`，CLI Adapter 复用现有 `agent_cli_bridge`；
- 新增唯一 Provider registry，编译/发布能力快照和 Runner Adapter 选择共享
  同一能力真相；
- V3 的 Builtin 与 CLI Agent 节点均经 `AgentHarnessFacade.prepare/execute`，
  不再由 Runner 绕过 Facade；
- Facade 统一发出 `run_started` 与 terminal 事件，并只接受 Adapter 报告、
  工作流声明且位于 Attempt Artifact 根目录内的候选文件；
- CLI Adapter 在启动前冻结 Artifact 指纹，只报告本次调用新增或修改且执行后
  未被替换的普通文件；任务输入仅把 RunSnapshot 已复制到 `inputs/` 下的解析
  文本、原件副本、分块和元数据加入 sandbox 只读边界；
- 发布和试运行在持久化副作用前拒绝执行器不支持的能力，并返回中文、可行动
  的结构化错误；未知 custom/legacy provider 不通过命令名猜测能力；
- 支持 resume 的 Adapter 调用 Adapter resume；不支持时返回结构化
  `unsupported_capability`；取消只调用 Adapter，Task 状态仍由 Orchestrator
  投影；
- 同步 Builtin callable 不能终止底层调用，因此明确声明
  `cancellation=false`；超时只负责拒绝迟到结果，不伪装成 Provider 已取消；
- V3 AgentInvocation 只包含 `rendered_input`、`declared_outputs` 和
  `provider_config`。多行输入、MR、文件绑定和端口值在 `rendered_input` 内逐字
  保留；旧专业字段仅在 legacy compatibility 路径存在；
- requirements、lockfile、vendor 和生产 import 静态门禁禁止引入大厂 Agent
  SDK；Harness/Adapter 静态门禁禁止领域关键词和专业治理 import；
- Builtin 兼容路径保留原有生命周期与 staged execution，避免双写 CLI turn
  快照或产生第二套任务状态。

## 3. RED 到 GREEN 根因记录

1. Facade 最初只读取 Adapter 结果的 `run_id`，而公共结果契约使用
   `session_id`。新增结果契约测试后改为优先读取 `session_id`，只把 `run_id`
   作为 legacy alias。
2. V3 Runner 初次统一 Facade 后仍可能把 Artifact 目录中上次遗留的声明文件
   当成当前 Provider 产物。新增 stale Artifact 红测后，当前 Adapter 没有报告
   的文件会以 `provider_did_not_report_artifact` 拒绝。
3. Builtin 首次统一后被外层 Runner 写入 CLI-only turn snapshot，旧正式内置
   模型验收因此要求不存在的 `execution_input/replay_plan`。Runner 现复用内置
   staged lifecycle，不创建第二份 CLI 生命周期。
4. 发布端和运行端曾各自维护 capability 列表。新增 registry 测试后，API
   snapshot、preflight 和 Adapter 构造统一读取 Provider registry；内置模型
   能力由 `BuiltinModelAdapter` 常量提供。
5. 真实浏览器第一次运行时，完整多行目标已存在于 `input_snapshot.json` 和
   Agent `task_bundle.json`，但 Phase 3 的离线 E2E Provider 仍按旧
   `task_bundle.resolved_inputs` 解析 stdin，导致测试观测为空。Phase 4 公共
   契约明确只暴露 `rendered_input/declared_outputs/provider_config`；新增后端
   逐字还原测试，并让真实 E2E Provider 解析 `rendered_input`，没有把领域
   Task Bundle 重新泄漏到 Harness。
6. 首轮独立审核发现四项 P1：CLI 误收遗留文件、Builtin 虚报取消、resume
   绕过 Facade 生命周期、V3 未知 Provider 回落到 legacy Runner。四项均先补
   红测，再分别通过执行前后强指纹、诚实 capability、execute/resume 共用
   `_run_adapter_operation`、未知 Provider 发布/运行双重拒绝修复。
7. 已知 Codex Adapter 浏览器链路先后暴露两处旧路径未覆盖的问题：真实探测
   丢失 wrapper 只读路径，以及已复制输入材料未进入正式执行 sandbox。两者均
   以最小只读集合修复，没有开放整个 Task Run 或原始上传目录。
8. 连续浏览器压力复跑稳定复现 macOS 进程组退出竞态：Provider 已输出并退出，
   `_terminate_process()` 的迟到 `SIGTERM` 偶发返回 `EPERM`，把成功任务翻成
   失败。新增精确单测后，桥接层在该窗口转为单进程收尾；修复后同一主流程
   连续 10 次通过。
9. 第二轮独立审核发现 Facade 把合法 `ProviderUnsupported` 当普通结果读取，
   并可能先发出虚假 `run_started`。新增四个真实 Adapter 的 unsupported 契约
   测试后，Facade 仅在实际活动或运行结果出现后开始生命周期；unsupported
   保持结构化返回且不创建事件或产物。
10. 第二轮独立审核用真实生产 callable 复现 Builtin 超时后后台线程仍可写入
    最终 Artifact 目录。Builtin 现在只在独立 epoch staging 中执行，Facade
    验收后才提升声明产物和受控诊断文件；超时、取消或旧 epoch 的迟到结果
    无权修改 Orchestrator 真相源。
11. Builtin 只调用同步 `complete()`，原 capability 却声明 `streaming=true`。
    现在诚实声明 `streaming=false`，要求 streaming 的试运行和发布在持久化前
    返回能力不满足错误。
12. 后台异常原先在脱敏前通过 `logger.exception` 写入完整 traceback。新增
    Bearer 注入红测后，日志只记录脱敏后的 traceback；任务诊断仍保留中文、
    可行动错误，但 token、代理凭据和 Provider URL 凭据不会写入服务日志。
13. 活动进程在进程组与单进程终止均返回 `EPERM` 时曾被静默遗留。取消和
    timeout 红测现在固定强制 kill、等待确认和无法清理时的结构化失败；已退出
    的 macOS 竞态仍保持成功，不会被误判为任务失败。
14. 第三轮独立审核把取消注入 Adapter 返回与 Artifact 提交之间，复现了迟到
    交付。Facade 现在在候选收集、提升和终态事件前均执行 commit fence；取消时
    回滚本次已提升文件，返回结构化 `cancelled`，不发送
    `artifact_created/completed`。execute 与 resume 共用该边界。
15. 第三轮独立审核确认 V3 Runner 没有消费 Facade 的
    `ProviderUnsupported` union。Runner 现在在任何 `.artifacts` 访问前转换为
    中文、可行动的结构化失败，首轮和 source-slice 续轮均不会交付或误报完成。
16. 进程清理最终统一为有界协议：进程组和非进程组路径都处理
    `terminate/kill/SIGKILL` 的 PermissionError、ProcessLookup 和超时；无法
    确认退出时抛出结构化 `AgentRuntimeError`，不会吞错后无限等待。该路径覆盖
    macOS/Linux 进程组和 Windows 单进程语义。
17. 第四轮独立审核把取消精确注入 `collect_artifacts()` 返回之后，发现 Adapter
    已不可逆提升文件并删除备份。Facade 现在拥有可回滚 Artifact transaction：
    调用前快照声明输出，候选收集和临时提升后仍保持 rollback 能力；唯一 commit
    point 完成最后取消检查后才清理备份、finalize Provider 和发送终态。取消可
    删除本轮新文件、恢复旧文件，并通过指纹避免覆盖较新 epoch 更新。
18. `artifact_created` 回调后也会重新检查取消。回调触发取消时事务回滚，只发送
    `cancelled`，不会再发送 `completed`。execute/resume、Builtin staging 和
    CLI direct candidate 共用相同原子提交语义。
19. 第五轮独立审核在 `artifact_created` 回调中用较新 epoch 替换目标，发现旧
    transaction 未执行所有权 CAS。commit point 现在逐项重新验证 owner
    fingerprint、普通文件类型、目标及父目录无 symlink、实时路径仍在冻结的
    Artifact root 内。所有权漂移返回结构化 `artifact_commit_rejected`，不声明
    产物、不发送 `completed`，且不会用旧备份覆盖更新 epoch。
20. commit-time CAS 的 staged/direct、execute/resume、新/旧文件组合均有确定性
    测试；覆盖普通内容替换、目标 symlink 和父目录 symlink。它不依赖后续
    Artifact Validator 兜底，Facade 自身只为本次 Adapter 仍拥有的字节发出成功
    生命周期。
21. 第六轮独立审核将已验收候选连同父目录搬出 root，再在原位置建立 symlink，
    复现 rollback 沿链接删除或恢复外部同 inode 文件。rollback 现在在每次
    unlink/replace 前重新执行冻结 root、目标和父目录链安全检查；路径漂移时不
    触碰候选路径，也不恢复 baseline，只清理事务私有 backup/state。
22. staged 旧文件 backup 已迁入 UUID 私有事务目录。无法安全恢复时保留结构化
    `artifact_commit_rejected`，不会为了回滚完整性越界写入。八种
    staged/direct × execute/resume × new/old 组合固定外部文件保持原样且无事务
    私有残留。

## 4. 自动化验证

### 4.1 Phase 4 与相邻后端回归

```text
474 passed, 1 xfailed in 28.94s
```

覆盖 Harness facade、四 Adapter 公共契约、registry、Builtin/CLI、V3 Runner、
Task Run、Scheduler、WorkflowVersion、发布/试运行 capability preflight、工作流
图和领域中立/无 SDK/状态所有权静态门禁。唯一 xfail 是 Phase 0 冻结的已知
隐式治理行为，按计划等待 Phase 5 转绿。

额外运行 CLI bridge、Agent runtime/sandbox、Provider 设置、网络策略、
任务网络快照、设置 API 和 LLM factory 组合回归：

```text
268 passed in 26.64s
```

V3 领域中立输入专项回归：

```text
4 passed in 0.15s
```

### 4.2 前端静态与生产构建

```text
npx tsc --noEmit: exit 0
npm run lint: exit 0
npm run build: exit 0，19 个页面生成成功
git diff --check: exit 0
```

Phase 4 没有修改产品 UI。真实浏览器用隔离端口 `3233/3234`、隔离 SQLite 和
`/Volumes/Media` 临时目录运行任务向导：鼠标选择已发布工作流，填写并确认
含前导空格和空行的长目标，上传设计文档，填写 MR，启动正式 Agent Runtime，
等待四轴状态并查看产物。首次红测稳定复现旧 E2E Provider 的契约解析错误；
修复后先连续 3 次通过，进程组 `EPERM` 红绿修复后再连续压力复跑：

```text
10 passed in 39.0s
```

第二轮 P1 修复后，在隔离端口 `3233/3234`、隔离 SQLite、
`/Volumes/Media/codetalk-runtime-tmp` 和独立证据目录再次运行同一浏览器主流程：

```text
5 passed in 29.5s
```

第三轮 commit fence、unsupported 调用链和 Windows/POSIX 清理修复后，使用新
run id 和独立证据目录再次运行同一主流程：

```text
5 passed in 24.4s
```

第四轮原子 Artifact transaction 修复后，使用新 run id 和独立证据目录再次
运行同一浏览器主流程：

```text
5 passed in 27.6s
```

第五轮 commit-time ownership CAS 修复后，使用新 run id 和独立证据目录再次
运行同一浏览器主流程：

```text
5 passed in 25.1s
```

第六轮对称 safe rollback 修复后，使用新 run id 和独立证据目录再次运行同一
浏览器主流程：

```text
5 passed in 24.4s
```

API 只用于创建隔离 fixture 和核验 Artifact，未代替浏览器主流程。

## 5. 真实 Provider 证据

### 5.1 Codex CLI

- 可执行文件：本机 `/opt/homebrew/bin/codex`，版本 `0.145.0`；
- 分析仓库：`/Volumes/Media/dpdk/spdk`，commit `97af299e3`；
- 真实读取：`lib/iscsi/iscsi.c`、`lib/iscsi/iscsi.h`；
- 运行时间：`171338 ms`；
- 事件包含：`run_started`、`session_created`、持续 `activity`、
  `artifact_created`、`completed`；
- 结果：`completed`，Facade 只接受 `report.md`；
- 证据目录：
  `/Volumes/Media/codetalk-e2e-artifacts/phase4/codex-adapter-lifecycle-20260728-070823/`。

第一次运行被 `intranet_egress_boundary_required` 正确拒绝；配置部署批准的
egress boundary 后同一 Adapter 成功。当前代码的第二次真实运行在持续输出时
超过两分钟而没有误触 idle timeout，并在结束时由 Facade 统一发出
`artifact_created`。没有关闭网络策略或将公网 IP 作为信任依据。

### 5.2 DeepSeek Flash 内置模型

- 模型：`deepseek-v4-flash`，通过 `BuiltinModelAdapter`；
- 输入：本地读取并附 SHA256 的同一组 SPDK 源码片段；
- 运行时间：`5893 ms`；
- Token：prompt `1402`、completion `709`、total `2111`；
- finish reason：`stop`；
- 事件：`run_started → activity → artifact_created → completed`；
- 结果：`completed`，Facade 只接受 `report.md`；
- 证据目录：
  `/Volumes/Media/codetalk-e2e-artifacts/phase4/builtin-deepseek-flash-facade-20260728-071155/`。

验证目录、日志、Harness contract 和报告均未写入 API key、Authorization 或
Bearer 值。

## 6. 安全与阶段边界

- 未连接 Redis `6399`；自动化使用 SQLite 和隔离目录；
- 所有本轮临时文件、截图、Provider 结果和数据库均位于 `/Volumes/Media`；
- 未新增第三方 Agent SDK、Hosted MCP、遥测、更新检查、CDN 或第二个 Store；
- 未放宽 Artifact 路径、声明集合、网络 egress、Provider capability 或发布
  CAS 门禁；
- legacy compatibility 仅包裹旧专业输入/输出字段，不进入 V3 普通 Harness
  请求；
- Phase 5、6、7 的产品行为尚未开始，本记录不提前宣称对应能力完成。

## 7. 审核准入

本阶段必须由未参与实现的独立只读 reviewer 对 Phase 4 diff、上述测试和真实
Provider 证据进行准入审核。只有结论为 `APPROVE` 且无 P0/P1，才允许提交并
进入 Phase 5；任何 `REJECT` 必须在 Phase 4 内按 red-to-green 修复并复审。

前六轮结论均为 `REJECT`，发现的 P1 已按第 3 节逐项完成 red-to-green 修复。
第七轮独立 reviewer 最终结论为 `APPROVE`：P0/P1 均为 0，未发现阻断性架构
偏移，确认 Phase 4 满足准入条件并可安全进入 Phase 5。Reviewer 独立复跑
Phase 主集合得到 `474 passed, 1 xfailed`，runtime/network/bridge 得到
`268 passed`，并复验 staged/direct × execute/resume × new/old 八种目录漂移、
正常成功、取消回滚、newer epoch、目标及父目录 symlink，均符合事务与边界契约。

## 8. 可复现命令

Phase 4 与相邻后端回归使用本记录对应的明确文件集合：

```bash
PYTHONPATH=backend python3.11 -m pytest \
  backend/tests/test_harness_facade.py \
  backend/tests/test_provider_adapter_contract.py \
  backend/tests/test_provider_adapter_registry.py \
  backend/tests/test_builtin_model_adapter.py \
  backend/tests/test_cli_provider_adapters.py \
  backend/tests/test_harness_domain_neutrality.py \
  backend/tests/test_no_agent_sdk_dependency.py \
  backend/tests/test_provider_state_ownership.py \
  backend/tests/test_agent_invocation_contract.py \
  backend/tests/test_v3_workflow_runner.py \
  backend/tests/test_workflow_validation_profiles.py \
  backend/tests/test_workbench_task_run.py \
  backend/tests/test_workbench_task_store.py \
  backend/tests/test_workflow_version_store.py \
  backend/tests/test_workflow_graph.py \
  backend/tests/test_workflow_scheduler.py \
  backend/tests/test_llm_factory.py -q
```

Bridge、设置、网络和 runtime/sandbox 组合回归：

```bash
PYTHONPATH=backend python3.11 -m pytest \
  backend/tests/test_agent_cli_bridge.py \
  backend/tests/test_agent_provider_settings.py \
  backend/tests/test_agent_runtimes.py \
  backend/tests/test_agent_sandbox.py \
  backend/tests/test_analysis_pipeline_fallback.py \
  backend/tests/test_network_policy.py \
  backend/tests/test_network_policy_settings_api.py \
  backend/tests/test_settings_api.py \
  backend/tests/test_settings_routes.py \
  backend/tests/test_task_engine_network_policy.py \
  backend/tests/test_llm_factory.py -q
```

前端与隔离浏览器主流程：

```bash
cd frontend
npx tsc --noEmit
npm run lint
npm run build
env CODETALK_FRONTEND_PORT=3233 \
  CODETALK_BACKEND_PORT=3234 \
  CODETALK_TEMP_DIR=/Volumes/Media/codetalk-runtime-tmp \
  CODETALK_PLAYWRIGHT_RUN_ID=phase4-safe-rollback-review \
  CODETALK_E2E_ARTIFACT_DIR=/Volumes/Media/codetalk-e2e-artifacts/phase4-safe-rollback \
  npx playwright test e2e/workflow-v3-declared-output-real.spec.ts --repeat-each=5
```
