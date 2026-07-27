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

## 冻结的 fixture 与契约

| 项目 | 冻结内容 | 验证方式 |
| --- | --- | --- |
| V1 已发布工作流 | `phase0_legacy_report`，版本 7 的 legacy sequential 定义 | 迁移后仍为 published，保留 legacy execution plan |
| V2 已发布工作流 | `phase0_v2_report`，directory 输入到 Agent 再到 `report.md` | 图校验、编译、发布、读取 published version |
| 历史 RunSnapshot | V3 不可变组件清单及 SHA256 | 物化 fixture 后 `validate_run_snapshot_v3()` 无错误 |
| 历史 Artifact | 声明的 `report.md` 与 delivery manifest | manifest 与交付内容逐字比对 |
| Artifact 安全边界 | 声明文件、未声明文件、相对路径越界、绝对路径越界 | Harness 只接受声明且位于 artifact root 内的 `report.md` |
| 调度与事件 | 复用节点与关键事件顺序 | 固定 `node_queued -> node_reused -> node_started -> node_completed -> run_completed` |
| 超时与取消 | 预取消 Agent 终态；既有总超时和 idle timeout 测试仍在指定回归集内 | 预取消不启动子进程并终止为 `cancelled` |
| XYFlow 浏览器路径 | 鼠标拖动、连线、删除、重连、刷新恢复；typed port 校验 | 真正 Chromium 鼠标输入，不以 API 替代画布操作 |

## 已知问题（未修改）

普通工作流即使只声明 `report.md`，`WorkbenchTaskRunPreparer` 仍会无条件生成并写入 `test_activity_contract`。Phase 0 以严格 xfail 固定该问题：

`test_phase0_ordinary_report_workflow_does_not_receive_implicit_test_activity_contract`

Phase 1 的转绿条件：仅声明 `report.md` 的普通工作流不再在 task bundle 或 artifact directory 中拥有 `test_activity_contract`；声明测试活动交付件的工作流仍保留其显式治理契约。

本阶段还观察到：真实 CLI harness 对无限输出子进程的取消回收不适合在 Phase 0 额外构造测试夹具。未修改运行时代码；当前冻结的是预取消终态，现有总超时与 idle timeout 回归保持覆盖。

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

结果：`275 passed, 1 xfailed`。

```bash
cd frontend && \
  CODETALK_REUSE_EXISTING_SERVER=1 \
  CODETALK_E2E_ALLOW_PUBLIC_DATA_MUTATION=1 \
  npx playwright test \
    e2e/workflow-v2-canvas-real.spec.ts \
    e2e/workflow-v2-input-ports-real.spec.ts
```

结果：`4 passed`。复用本机已运行的 `3003/3004` 服务是为了执行真实浏览器路径；未使用 mock 或 API 替代画布操作。

## 计数

- 后端：275 通过，1 严格 xfail，0 跳过。
- 浏览器：4 通过，0 跳过，0 xfail。
- 产品文件：0 个被触碰。

## Phase 1 前置

Phase 0 已完成，后续可在单独批准后进入 Phase 1 的 Workflow Contract 引入与隐式 Test Activity 治理移除。该阶段未开始。
