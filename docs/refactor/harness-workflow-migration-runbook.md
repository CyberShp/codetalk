---
feature_ids:
  - harness-workflow-refactor
  - AC-10
  - AC-12
  - AC-13
topics:
  - phase-7
  - workflow-migration
  - rollback
  - legacy-compatibility
  - intranet-operations
doc_kind: migration-runbook
created: 2026-07-29
---

# Harness 与工作流 Phase 7 迁移运行手册

## 1. 目的与权威边界

本手册只覆盖 Phase 7 的增量 V3 默认化、历史兼容、回滚和验收操作。它以
`harness-workflow-goal.md`、`harness-workflow-target-architecture.md` 和
`harness-workflow-refactor-plan.md` 为准；发生冲突时以目标文档为准。

迁移的基本规则是：**历史已发布版本、RunSnapshot、任务、事件和 Artifact 不做
批量写入或原地升级。** V3 是新建草稿和新发布版本的路径，不是历史数据的重解释或
替换。任何迁移动作都必须留下可比对的备份、预览、操作者和证据位置。

本手册不授权删除历史兼容代码、清理 Legacy 预设、推送远端、连接 Redis `6399`，也
不授权把未完成的 Phase 7 验收标为完成。

## 2. 运行前检查

1. 在维护窗口确认工作目录为 `/Volumes/Media/codetalk-harness-phase2`，不使用
   `reset`、`clean`、`checkout` 覆盖工作树，也不重建 worktree。
2. 记录 `git rev-parse HEAD`、`git status --short` 和本次环境变量。未提交变更必须
   与实施记录一起保存，不能以清理工作树替代归档。
3. 新建本次证据根目录，例如
   `/Volumes/Media/codetalk-e2e-artifacts/phase7/<run-id>/`；临时数据库、上传、
   Playwright 数据和截图也必须留在 `/Volumes/Media`。
4. 仅为 Phase 7 浏览器验收启动隔离端口：前端 `3233`、后端 `3234`。
   `3003/3004` 是公共本地默认端口，不得用于隔离 E2E。
5. 不连接 Redis `6399`。本阶段工作流验收使用隔离 SQLite；如确有开发或测试 Redis
   需求，只能使用 `6398` 并在证据中记录。
6. 网络模式、批准的 Base URL、企业代理、`NO_PROXY` 和 CA bundle 必须由部署管理员
   提供。不得添加 Hosted MCP、遥测、自动更新、在线包下载或公共出口作为迁移捷径。

建议记录的最小清单：

```text
git rev-parse HEAD
git status --short
env | grep '^CODETALK_'
lsof -nP -iTCP:3233 -sTCP:LISTEN
lsof -nP -iTCP:3234 -sTCP:LISTEN
```

任何本机 GitNexus 服务仅可使用 loopback `127.0.0.1:7100`；E2E 结束后应同后端一并
停止。它不是 Hosted MCP，也不能作为公共网络例外。

## 3. 数据备份与可验证恢复

### 3.1 备份顺序

在停止写入后，为生产 Workbench SQLite 数据库做一致性备份。不要在运行中的 WAL
数据库上直接复制单个 `.db` 文件。使用 SQLite 的一致性 `.backup`/`VACUUM INTO` 机制，
或先停止后端并同时保存 `.db`、`-wal`、`-shm`。备份根目录应位于：

```text
/Volumes/Media/codetalk-e2e-artifacts/phase7/<run-id>/backup/
```

至少保存并计算 SHA-256：

- Workbench workflow database 及其 WAL/SHM（如存在）；
- 主任务数据库及其 WAL/SHM（如存在）；
- 需要验证的历史 Attempt Artifact 目录、RunSnapshot 和事件文件；
- 导出的 `workflow_headers`、`workflow_versions`、历史 Task/Attempt 的只读 JSON
  清单；
- 备份命令、操作者、UTC 时间和运行版本。

`WorkflowVersionStore.initialize_and_migrate()` 已调用既有
`ensure_workbench_migration_backup()`。这是启动期保护，不替代维护窗口的显式、可验
证备份。迁移前后都必须对抽样的历史行和 Artifact 下载字节做 SHA-256 比较。

### 3.2 恢复原则

1. 首选先进入 V3 只读回滚模式，而不是立即恢复数据库。
2. 只有出现确认的数据损坏、错误写入或无法通过只读兼容性读取时，才停止服务并从
   同一次一致性备份恢复数据库和匹配的 WAL/SHM。
3. 恢复后先在隔离副本验证历史 V1/V2 workflow、task、RunSnapshot、event 与
   Artifact 的读取和下载，再恢复服务写入。
4. 恢复不能把 Legacy 预设重新设为通用默认，也不能将历史对象改写为 V3。

## 4. 无写历史迁移流程

### 4.1 启动与读取

启动时允许创建尚不存在的 schema/meta 和安全备份；不得对既有已发布 V1/V2 行批量
回填、改写 JSON、改 `updated_at`、变更 published version ID 或重建历史 Artifact。

对于缺少 `compiled_contract_version` 的冻结定义，读取时确定性走 legacy compatibility
runner。`WorkflowVersionStore._version_from_row()` 可以在内存中为旧 V1 定义构造 legacy
plan，以维持读取/执行兼容；该读取适配不得写回 `compiled_plan_json`。未知的非空冻结
版本必须 fail closed，不能猜测升级为 V3。

启动后执行下列抽样检查，并把请求/响应和 hash 保存到证据目录：

1. 读取每个代表性历史 header 和 published version；
2. 打开关联历史 task、RunSnapshot、事件和 Artifact 清单；
3. 下载至少一个历史 Artifact，比较迁移前后 SHA-256；
4. 比较 `workflow_versions` 中历史行的 JSON、timestamps 和 version ID。

### 4.2 预设重分类

`basic_source_report_codex` 与 `basic_source_design_report_builtin` 保留其 canonical ID、
历史定义和已发布版本。列表显示由独立 presentation metadata 提供，例如
`Legacy` 与 `SPDK/iSCSI 专业` 标签；presentation 不能修改 canonical definition，
也不能触发 builtin preset bootstrap 重建或产生新的历史发布版本。

新的默认模板必须使用独立 ID：空白画布、自由源码分析、源码加可选设计文档、变更影响
分析、多 Agent 分析、正式存储测试设计。默认通用模板是 V3 的
`artifact_only`，只在显式声明时启用专业治理。

### 4.3 显式预览和复制

历史 V1/V2 已发布版本只能执行以下两个独立动作：

1. `GET /api/workbench/workflows/{workflow_id}/versions/{version_id}/migration-preview`
   返回只读预览。它必须报告源/目标 schema、保留事实、启用的专业规则、输出/Profile
   差异和 rollback 影响，且不创建 draft、不更新 header 或历史 version。
2. 用户审阅预览后，才可调用
   `POST /api/workbench/workflows/{workflow_id}/versions/{version_id}/copy-to-v3`。
   POST body 必须显式携带 GET 预览返回的 `migration_contract_version`、
   `confirmation_token` 和 `preview_confirmed: true`；缺失、拒绝、未知版本或 token 不匹配
   均 fail closed，不能把确认藏在客户端默认值中。通过后它创建一个新的 V3 draft 和新的
   workflow/version identity；源版本继续只读不变。

通用 `POST /api/workbench/workflows/{workflow_id}/versions` 只允许从 V3 发布版本创建新的
V3 draft；若 base 是 V1/V2，必须返回结构化 `409 legacy_workflow_read_only` 和预览/复制
路径且不写入任何 header/version。旧 `POST /api/workbench/workflows/{workflow_id}/copy`
仅保留 fail-closed 兼容响应，不得再生成 V2 草稿。

同样，旧 `intranet_network_mode` 的设置迁移只能先显示只读预览。旧 host-enforced
配置可能映射到 `strict_compliance`，其他旧 intranet 配置映射为 `intranet`，但管理员
确认前不得静默改变实际运行能力。前后端都必须校验 migration contract version；未知
版本保持只读并给出可行动中文提示。

## 5. V3 写入回滚

### 5.1 启用只读回滚

设置以下部署环境变量并重启后端：

```bash
CODETALK_WORKFLOW_V3_WRITES_ENABLED=false
```

该开关由 `workflow_migration_policy.py` 统一解释。关闭后，V3 新建、草稿变更、发布、
复制到 V3、试运行和新运行必须返回 HTTP `409` 及
`workflow_v3_read_only` 的中文说明。既有 V3 Attempt 的显式 execute、rerun、人工审批
恢复、cancel、acceptance audit、output materialize 和 semantic import 也必须返回同一
`409`，且不得先写事件、审批 receipt、rerun plan、audit/manifest 或后台任务。V3 Candidate
必须由冻结 snapshot/compiled definition/bundle 中的非空 contract 识别，不能因 mutable
task projection 丢失而降级为 Legacy；若 `run_snapshot_v3.json` 存在但无法解析，身份不可
证明，必须 fail closed。合法可解析、contract 为 `null` 的 V1/V2 snapshot 仍是 Legacy，
`agent_execution_descriptors.json` 的存在本身不能改写该结论。后端重启时，V3 startup
recovery 和 approval expiry monitor 必须保持静默只读，并把 V3 Attempt 排除在 Legacy
interruption reconciler 之外。
它不是关闭 Workbench 的总开关。

### 5.2 回滚后必须保留的读取能力

关闭 V3 写入后，以下请求仍必须可用：

- 历史和 V3 workflow/version/header 的 GET；
- 历史 task、attempt、RunSnapshot、事件和状态的 GET；
- 完整或部分损坏 V3 Attempt 的普通 GET；状态只能在内存中投影，不得回填 task/event；
- Artifact list、preview 和 download；
- rerun plan/validation 的只读计算；若计划尚未落盘，GET 只能内存计算，不得为了读取写入；
- Legacy workflow 的 presentation/list 与冻结 legacy runner 的历史读取。

回滚验证至少包含一条 V3 写入 `409` 和一组 V1/V2/V3 查看、Artifact 下载 `200`。若
读取能力不满足，不得把问题归类为正常回滚，应保持服务隔离并执行数据恢复调查。

### 5.3 恢复写入

只有完成根因记录、备份完整性检查、相关回归和独立审核后，才可改回：

```bash
CODETALK_WORKFLOW_V3_WRITES_ENABLED=true
```

恢复写入后，先创建新的 V3 draft 验证，再验证历史对象 hash 未变；不得通过重新启用
旧硬编码预设或删除版本检测来“恢复”服务。

## 6. Legacy runner 与双路径约束

一个 Attempt 只能根据其冻结 contract 选择一条路径：

```text
missing compiled_contract_version -> legacy compatibility runner
compiled_contract_version = 3 -> V3 orchestrator
unknown non-empty version -> reject/fail closed
```

不得在运行中将 legacy snapshot 升级为 V3，不得让同一 Attempt 同时进入两条 runner，
也不得删除缺少版本时的 legacy runner。Phase 7 可删除的仅是确认没有调用者的“新运行
兼容胶水”；历史 parser、历史查看和下载路径必须保留。

V3 task 下的 Agent envelope 只供 `WorkflowDagScheduler` 执行。task-scoped 单 Agent
execute、validate 或 materialize 请求必须返回 `workflow_v3_scheduler_authority`，不能
因快照损坏、projection 丢失或直接知道 `step_id` 而降级到 `AgentHarnessFacade.execute()`。
明确没有任何 V3 冻结标记的 Legacy Agent 调试运行继续兼容。

## 7. 真实验收运行约束

浏览器验收使用真实 Chrome/Playwright、隔离的 Next 测试服务器和隔离数据根；生产
Next 构建作为独立门禁运行，不能用开发服务器通过替代，也不能把独立 build 记录误写
成 Playwright 运行了生产服务器。典型浏览器命令形态为：

```bash
cd /Volumes/Media/codetalk-harness-phase2/frontend
env CODETALK_FRONTEND_PORT=3233 CODETALK_BACKEND_PORT=3234 \
  CODETALK_REUSE_EXISTING_SERVER=0 \
  CODETALK_TEMP_DIR=/Volumes/Media/codetalk-runtime-tmp/phase7 \
  CODETALK_PLAYWRIGHT_DATA_DIR=/Volumes/Media/codetalk-e2e-artifacts/phase7/<run-id> \
  CODETALK_E2E_ARTIFACT_DIR=/Volumes/Media/codetalk-e2e-artifacts/phase7/<run-id> \
  CODETALK_PLAYWRIGHT_KEEP_DATA=1 \
  npx playwright test <phase7-specs>
```

不得用 API 调用替代画布新建、拖拽、连线、删除、保存、刷新、试运行或下载主流程。API
仅可用于隔离 fixture、故障注入和不可见状态核验。所有监听端口和本地 GitNexus 都要在
运行结束后关闭。

网络验收必须在批准的 Base URL/proxy/CA 上进行，并证明无 telemetry、updater、Hosted
MCP 或未批准出口。管理员拥有的 PCAP、网关日志和拒绝公共目的地的负向证据是发布门禁；
开发账户不能伪造或代替这项证据。采集脚本为
`scripts/capture-intranet-egress.sh`，输出必须位于 `/Volumes/Media/codetalk-e2e-artifacts/`。

## 8. 完成记录

2026-07-29 的本地隔离验收已生成八组主证据：

- `phase7/migration-real-20260728-2`：模板 chooser、V1/V2 preview/copy、桌面/移动；
- `phase7/history-artifacts-20260729T123000+0700`：历史 task/RunSnapshot/events/Artifact 与下载 SHA；
- `phase7/phase7-provider-matrix-20260729T044023+0700`：Builtin fixture、loopback
  Codex-compatible CLI adapter 和正式治理；
- `phase7/workflow-soak-20260729-040721`：30.3 分钟同一 Attempt 的 HITL soak。
- `phase7/runtime-failures-final-20260729T0620`：坏路径、坏文件、不可用 Provider、取消、
  idle timeout 与总 timeout 的失败语义；其中坏文件由 API preflight 验证，timeout 由
  API 故障注入后再由浏览器核验产品终态；
- `phase7/v3-ui-concurrency-final-20260729T1000`：桌面/移动 V3 画布操作、未知冻结契约
  提示和三个并发 V3 task/run/event/Artifact 隔离。
- `phase7/legacy-read-only-gate-ux-final-20260729T0645`：两轮 reviewer P1 修复后，真实 Chrome
  复验 V1/V2 从库页、版本页、设计器和 V2 Legacy 向导先进入预览页、预览前零写入、
  正确迁移文案、版本化确认 token、显式确认复制及桌面/移动模板路径，`3 passed (18.8s)`。
- `phase7/v3-scheduler-authority-final-20260729T0900`：第四轮 reviewer P1/P2 修复后，
  真实 Chrome 创建 V3 task/attempt，证明直接 task-agent execute 返回原始结构化 `409`，
  产品运行页没有 Execute/Validate/Materialize 单节点动作，`1 passed (9.7s)`；目录保存
  Playwright trace、截图、完整请求/响应、Attempt 前后 SHA 和 events 前后对照，且执行前后
  Attempt 与事件均未变化。

这些目录都位于 `/Volumes/Media/codetalk-e2e-artifacts/`。它们不包含实际安装 Codex 经
企业网关的运行，也不包含管理员 PCAP/网关日志，不能单独解除发布阻塞。

Phase 7 只有在以下材料齐全后才允许结束：14 项 AC 状态全部填写为 `Pass`、
`Known Issue` 或 `Blocked`，且最终没有 Blocked 的发布条件；真实浏览器 trace、截图、
事件、RunSnapshot、Artifact hash/download、网络摘要、历史 hash 对照、性能/可靠性
结果、管理员 PCAP/网关证据和一位全新只读 reviewer 的 `APPROVE`（P0=0、P1=0）。

当前完成与缺口以 `harness-workflow-acceptance-report.md` 为准。没有这些证据时，本手册
只定义操作流程，不构成发布批准。

2026-07-29 第八位全新只读 reviewer 已给出 `APPROVE`，P0/P1/P2/P3 均为零；该批准只
关闭代码准入门禁，不替代仍缺失的管理员 PCAP/网关日志和实际安装 Codex 经批准内网网关
运行证据。只要 AC-10/AC-12 仍为 `Blocked`，Phase 7 就不能标记为发布验收完成。
