---
feature_ids:
  - harness-workflow-refactor
topics:
  - phase-0
  - characterization-tests
  - workflow-compatibility
doc_kind: verification-record
created: 2026-07-27
---

# Harness 与工作流重构 Phase 0 验证记录

## 范围结论

本记录只冻结重构前的兼容性契约。没有修改产品运行行为、数据库 Schema、网络默认策略、工作流编译器、Harness/Runner/Adapter 或工作流设计器实现。

## 修改文件

- `backend/tests/test_workflow_version_store.py`
- `backend/tests/test_workbench_task_run.py`
- `backend/tests/test_harness_facade.py`
- `frontend/e2e/workflow-v2-canvas-real.spec.ts`
- `frontend/e2e/workflow-v2-input-ports-real.spec.ts`
- `backend/tests/fixtures/harness_workflow_refactor/v1-published-workflow.json`
- `backend/tests/fixtures/harness_workflow_refactor/v2-published-workflow.json`
- `backend/tests/fixtures/harness_workflow_refactor/historical-run-snapshot-v3.json`
- `backend/tests/fixtures/harness_workflow_refactor/historical-artifacts.json`
- `backend/tests/fixtures/harness_workflow_refactor/historical-task-attempt.json`

## 冻结的 fixture 与契约

| 项目 | 冻结内容 | 验证方式 |
| --- | --- | --- |
| V1 已发布 WorkflowVersion | `phase0_legacy_report` 的 header、version、authoring graph、compiled definition、compiled plan 和 validation | 直接灌入冻结行后读取，不调用当前编译器或发布器，读取前后 JSON 字节不变 |
| V2 已发布 WorkflowVersion | `phase0_v2_report` 第 3 版完整已发布记录 | 直接灌入冻结行后读取，不用当前编译器重新生成历史结果 |
| 历史 Task/Attempt/Event | `task-historical`、第 2 次 Attempt、父 Attempt 和三条关键事件 | 通过 `WorkbenchTaskStore`、`WorkbenchTaskRunStore`、`WorkbenchTaskRunEventStore` 与公开事件接口联合读取；原始 `task_run_events.jsonl` 完整冻结 `event_id/seq/event_type/event_kind/payload/created_at` |
| Task 详情聚合 | 历史 Task、已发布 WorkflowVersion、最后一次 Attempt 与父 Attempt 关系 | 通过真实 `/api/workbench/tasks/{task_id}` 路由读取并核对版本快照和运行摘要，不直接调用拼装辅助函数 |
| 历史 RunSnapshot | V3 不可变组件清单及 SHA256 | 在历史 Attempt 目录物化后 `validate_run_snapshot_v3()` 无错误 |
| 历史 Artifact | 声明的 `report.md` 与 delivery manifest | manifest、交付内容、SHA256 和字节数逐项比对 |
| Artifact 安全边界 | 声明文件、未声明文件、相对路径越界、绝对路径越界 | Harness 只接受声明且位于 artifact root 内的 `report.md` |
| 调度与事件 | 复用节点与关键事件顺序 | 固定 `node_queued -> node_reused -> node_started -> node_completed -> run_completed` |
| 取消 | 预取消和运行中取消 | 运行中取消真实本地 Provider 进程组，确认子进程不能逃逸，并固定终态事件 |
| 超时 | idle timeout 当前行为；持续有效输出时的 hard total timeout 缺口 | idle timeout 保持回归；hard total timeout 用严格 xfail 固定目标语义 |
| XYFlow 浏览器路径 | 节点库拖入、节点移动、连线、删除、重连、保存和刷新恢复；typed port 校验 | 真正 Chromium 鼠标输入，不以 API 替代画布主流程 |

## 已知问题（未修改）

普通工作流即使只声明 `report.md`，`WorkbenchTaskRunPreparer` 仍会无条件生成并写入 `test_activity_contract`。Phase 0 先以普通 characterization test 固定当前污染形态，再以限定 `AssertionError` 的严格 xfail 固定目标行为，避免 setup 或非预期异常被误记为已知失败：

`test_phase0_ordinary_report_workflow_does_not_receive_implicit_test_activity_contract`

Phase 1 的转绿条件：仅声明 `report.md` 的普通工作流不再在 task bundle 或 artifact directory 中拥有 `test_activity_contract`；声明测试活动交付件的工作流仍保留其显式治理契约。

当前污染形态另有一条普通 characterization test 负责固定。Phase 1 修复落地时，必须在同一次改动中将该 characterization test 反转为目标断言或删除，否则正确修复会使旧行为测试变红。

本阶段还确认了第二个已知问题：`timeout_sec` 当前没有作为持续有效输出 Provider 的严格 wall-clock 总预算；hard timeout 会扩展到至少一小时。Phase 0 不修改 Harness 行为，以严格 xfail 固定目标语义。后续 Harness 阶段转绿条件：持续输出不能绕过配置的总超时，同时 `idle_timeout_sec` 继续独立表示无有效活动预算。

## 执行命令与结果

```bash
git diff --check

PYTHONPATH=backend python3.11 -m pytest \
  backend/tests/test_workflow_graph.py \
  backend/tests/test_workflow_version_store.py \
  backend/tests/test_workbench_task_run.py \
  backend/tests/test_harness_facade.py \
  backend/tests/test_network_policy.py -q
```

结果：`278 passed, 2 xfailed`。两个预期失败分别是：

- Phase 1 移除普通工作流的隐式 Test Activity；
- 后续 Harness 阶段实现与 idle timeout 分离的 hard total timeout。

同一轮测试的 JUnit 报告保存于 `/Volumes/Media/codetalk-runtime-tmp/phase0-backend-junit.xml`，用于独立复核通过、失败、跳过和 xfail 计数；该运行证据不写入仓库。

```bash
cd frontend && \
  CODETALK_FRONTEND_PORT=3233 \
  CODETALK_BACKEND_PORT=3234 \
  NEXT_PUBLIC_API_URL=http://localhost:3234 \
  CODETALK_REUSE_EXISTING_SERVER=0 \
  CODETALK_TEMP_DIR=/Volumes/Media/codetalk-runtime-tmp \
  npx playwright test \
    e2e/workflow-v2-canvas-real.spec.ts \
    e2e/workflow-v2-input-ports-real.spec.ts \
    --project=chromium
```

结果：`4 passed`。运行时来自 worktree `/Volumes/Media/codetalk-v3-productization-resume`，产品基线 commit 为 `f783ff58`；Playwright 在隔离端口 `3233/3234` 自动启动前后端并使用独立数据目录，没有复用或修改公共 `3003/3004` 运行时。画布主流程未使用 mock 或 API 替代浏览器操作；typed-port 用例的 API 仅用于创建和归档独立 fixture。

## 计数

- 后端：278 通过，2 严格 xfail，0 跳过（当前 POSIX 验证环境）。
- 浏览器：4 通过，0 跳过，0 xfail。
- 产品文件：0 个被触碰。

## Phase 1 前置

Phase 0 的历史兼容、隐式治理、Artifact 边界、运行中取消、超时缺口和真实画布路径均已有明确测试保护。独立 reviewer 第三轮结论为 `APPROVE`，确认产品文件修改数为 0，允许进入 Phase 1。Phase 1 尚未开始。
