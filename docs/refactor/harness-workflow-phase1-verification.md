---
feature_ids:
  - AC-04
  - AC-05
  - AC-06
  - AC-14
topics:
  - workflow-v3
  - declared-outputs
  - validation-profiles
  - compatibility
doc_kind: verification-record
created: 2026-07-27
---

# Harness 与工作流重构 Phase 1 验证记录

## 范围结论

Phase 1 建立了最小 `AuthoringGraphV3` / `CompiledWorkflowContractV3`，并让 V3 已声明输出和显式 Validation Profile 成为唯一验收权威。旧 V1/V2 frozen snapshot 继续走原有兼容路径；本阶段没有进入网络策略、Harness 领域规则迁移或画布信息架构重构。

## 目标映射

| 验收项 | 本阶段结果 |
| --- | --- |
| AC-04 | 仅声明 `report.md` 的 V3 工作流只生成、校验和展示 `report.md`。 |
| AC-05 | Validator 只能引用已声明输出；未声明文件不会进入 required artifacts、交付列表或驾驶舱。 |
| AC-06 | 普通 V3 工作流默认 `artifact_only`，不创建或运行 Test Activity、SFMEA、黑盒用例和独立 Reviewer 治理。 |
| AC-14 | V3 使用 execution、artifact validation、governance、delivery 四轴状态；delivery 只能由前三轴派生，非法组合拒绝写入。 |

## 主要改动

- `backend/app/services/workflow_contract_v3.py`：V3 authoring/compiled contract、Validation Profile 展开和声明输出约束。
- `backend/app/services/workflow_handler_registry.py`：通用 Agent、Artifact existence、JSON Schema handler capability registry。
- `backend/app/services/workflow_run_status.py`：四轴状态集合、纯派生规则和 legacy 兼容投影。
- `backend/app/services/workflow_graph.py`：V2/V3 编译分派、typed port 与单目标端口连线校验、输出路径安全边界。
- `backend/app/services/workbench_task_compile.py`：V3 compile/prepare 只消费 frozen compiled contract，不接受 task 级输出改名或 Schema 变更。
- `backend/app/services/workbench_task_run.py`：V3 RunSnapshot、输入逐字冻结、显式契约版本兼容分派；未知版本保持四轴并失败关闭。
- `backend/app/services/workbench_workflow_runner.py`：V3 仅执行 frozen plan 中注册的 Agent/Validator；声明输出是唯一产物权威。
- `backend/app/services/workbench_task_run_events.py`：原子写入并校验 V3 四轴状态。
- `backend/app/api/agent_workbench.py`：执行、取消、预检失败、后台异常和读取恢复统一使用 frozen contract 状态模型；V3 不进入 legacy acceptance audit。
- `frontend/src/features/tasks/task-wizard.tsx`：V3 隐藏自定义输出覆写，V2 保持兼容。
- `frontend/src/features/runs/run-cockpit-page.tsx`：V3 展示四轴状态和声明交付件，旧任务仍展示 legacy 状态。
- `frontend/src/lib/types/workflow.ts`、`frontend/src/lib/types/task.ts`：补齐 V3 contract、Validation Profile 和四轴 API 类型。

## TDD 与契约证据

新增或扩展的后端测试覆盖：

- 单一 `report.md` 从 compile、bundle、RunSnapshot、Runner 到 API 全链路无幽灵输出；
- `artifact_only` 不创建 Test Activity Contract，Phase 0 对应 strict xfail 已转为绿色目标测试；
- Validator 引用未声明输出、端口类型不匹配、单目标端口多边和 Artifact 路径越界均 fail closed；
- Profile 不按文件名、Prompt 或工作流名称推断；
- `none` 不阻断 Artifact，`schema` 只校验声明 Schema；
- 未注册的专业 handler 允许保存草稿，但发布/运行拒绝；
- 用户输入 ID 与画布节点 ID 不同时，多行、空白文本仍逐字传给 Agent；
- 真实 CLI stdin 回执逐字核对长文本、空行、首尾空格和 MR 链接，并从 task-owned `parsed_text_path` 读取上传的设计文档；
- 文件上传异步完成时基于最新表单状态合并，上传期间填写的分析目标和 MR 链接不会被旧渲染快照覆盖；
- 已完成 V3 run 再次入队时重置 Artifact/Governance 轴，不会出现“排队中但交付就绪”；
- 未知或错误类型的 contract version 不降级为 legacy，也不被强制转换为受支持 V3；
- 取消、预检失败和异常路径保持合法四轴状态；
- 历史 V1/V2 fixture、Artifact 安全边界、取消与事件顺序保持回归。

## 验证命令与结果

```bash
git diff --check

PYTHONPATH=backend python3.11 -m pytest \
  backend/tests/test_workflow_graph.py \
  backend/tests/test_workflow_validation_profiles.py \
  backend/tests/test_declared_artifact_authority.py \
  backend/tests/test_workbench_task_run.py \
  backend/tests/test_v3_workflow_runner.py \
  backend/tests/test_workflow_version_store.py \
  backend/tests/test_harness_facade.py \
  backend/tests/test_network_policy.py -q \
  --junitxml=/Volumes/Media/codetalk-runtime-tmp/phase1/backend-final.xml
```

结果：`320 passed, 1 xfailed`。唯一预期失败是 Phase 0 冻结的 hard total timeout 缺口，归属后续 Harness 阶段；普通工作流隐式 Test Activity 的 Phase 1 已知失败已经转绿。

```bash
cd frontend
npx tsc --noEmit
npx eslint \
  src/features/runs/run-cockpit-page.tsx \
  src/features/tasks/task-status.ts \
  src/features/tasks/task-wizard.tsx \
  src/features/tasks/workbench-task-detail-page.tsx \
  src/lib/api/workflows.ts \
  src/lib/types/task.ts \
  src/lib/types/workflow.ts \
  e2e/workflow-v3-declared-output-real.spec.ts
npm run build
```

结果：TypeScript、ESLint、Phase 1 选定的 37 条前端工作流/驾驶舱契约测试和 Next.js 生产构建全部通过。

```bash
CODETALK_FRONTEND_PORT=3233 \
CODETALK_BACKEND_PORT=3234 \
CODETALK_REUSE_EXISTING_SERVER=0 \
npx playwright test \
  e2e/workflow-v2-canvas-real.spec.ts \
  e2e/workflow-v2-input-ports-real.spec.ts \
  e2e/workbench-v2-task-wizard-real.spec.ts \
  e2e/workflow-v3-declared-output-real.spec.ts \
  --project=chromium
```

结果：`6 passed`。Playwright 在隔离端口启动本 worktree 前后端；业务主流程使用真实 Chromium 鼠标、键盘和产品 Harness，未以 mock、请求拦截或 API 调用替代任务创建与执行。V3 用例中的本地 CLI Provider 真实读取 stdin，并回写它收到的长文本、MR 链接和文件输入证据供逐字断言；API 仅用于隔离 fixture 的前置、清理和读取运行证据。针对上传期间继续填写其它输入的竞态，用同一真实 V3 流程额外执行 `--repeat-each=10`，结果 `10 passed`。运行时和测试产物位于 `/Volumes/Media/codetalk-runtime-tmp` 与 `/Volumes/Media/codetalk-e2e-artifacts`。

额外执行全量后端回归至 39% 后主动停止：`1424 passed, 7 skipped, 3 failed`。三条失败均位于既有 legacy 专业黑盒审计测试，且在 detached Phase 0 基线 `e9e3ebce` 上单独重跑同样稳定失败，错误均为既有 test-directory mapping/acceptance 预期不一致；本轮未改动对应质量规则或测试。它们不是 Phase 1 回归，但仍作为基线债务保留，不以修改专业规则扩大本阶段范围。

额外运行 `node --test scripts/workbench-v2-release-contract.test.mjs` 得到 `14 passed, 1 failed`。失败项仍要求已移除的 V2 前端回滚开关调用，而 `e9e3ebce` 基线中的 `workbench-v2-route-gate.tsx` 已是纯透传组件；本轮对该组件和测试均无 diff。该失败作为历史 release-contract 测试债务记录，不在 Phase 1 改写产品行为。

## 独立复审

独立 reviewer 首轮拒绝了上传期间旧状态快照覆盖新输入的竞态。修复后，reviewer 复跑契约、TypeScript、ESLint、V3 Chromium 十轮压测和完整六条浏览器套件，结论为 `APPROVE_WITH_FIXES`；唯一提交前修正是将上述既有 release-contract 失败如实写入本记录。补齐记录并统一前端严格数值 `3` 的契约版本判断后，reviewer 再次复跑相关契约、TypeScript、ESLint 和六条真实 Chromium，最终结论为 `APPROVE`。Phase 1 无剩余阻断项。

## 兼容与剩余边界

- 历史 V1/V2 workflow、RunSnapshot、Task 和 Artifact fixture 全绿，不自动升级。
- V3 API 字段向后兼容；legacy quality/delivery 投影保留，四轴数据不被它覆盖。
- `storage_test_design` 与 `formal_release` 在相应专业 handler 落地前只能作为草稿，不能发布或运行。
- Harness 内部仍可能携带 legacy Test Activity 诊断结构；V3 task contract、事件、可见产物和驾驶舱不会消费或展示它。领域规则从 Harness 核心迁出属于后续 Phase，不在本阶段扩大修改范围。
- 网络模式、Provider 出站策略和 hard total timeout 未在 Phase 1 修改。

## 阶段边界

Phase 1 到此停止。未开始 Phase 2，也未修改默认网络配置、数据库 Schema 或第三方 Agent SDK 边界。
