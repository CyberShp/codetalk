---
feature_ids:
  - module_analysis
topics:
  - workbench
  - agent-runtime
  - input-contract
doc_kind: bug-report
created: 2026-07-11
---

# 模块分析静默退化为静态扫描

## 报告人

用户在 SPDK 工作区真实运行 `module_analysis` 时发现任务秒级完成；Codex 验证通过浏览器复现。

## 复现步骤

1. 在运行驾驶舱选择 SPDK 工作区和“模块分析工作流”。
2. 将执行器覆盖为 Codex。
3. 输入完整的 iSCSI Login 源码分析目标并准备、执行工作流。
4. 观察冻结快照、节点事件和最终交付件。

期望：Codex 读取源码和测试目录，生成证据充分的模块分析报告。

实际：冻结快照显示 `Agent: 0`，三个节点均为 `local_static`，约 1 秒完成；报告只有步骤和文件清单。未填写的示例 MR 地址也进入了 Query。

## 根因分析

- `module_analysis` 预设只声明 `local_scope_discover`、`evidence_validate` 和 `report_render`，没有 `agent_task`。
- `provider_override` 只在创建 Agent run 时使用；零 Agent 节点会让覆盖值被静默忽略。
- 驾驶舱以全局演示 `DEFAULT_INPUTS` 初始化，其中包含硬编码 MR 地址；输入摄取会保留未声明字段，因此示例值污染任务快照和报告。

## 修复方案

- 保留本地静态证据收集，在其后增加真实 Agent 模块分析步骤，并把 `module_analysis.md` 设为必需交付件。
- 对零 Agent 节点禁用执行器覆盖，后端同时拒绝此类无效覆盖。
- 删除全局演示输入，运行表单只由当前工作流的命名输入生成。

## 验证方式

- 单元测试验证预设包含 Agent 节点、覆盖值能冻结到 Agent run、零 Agent 覆盖被拒绝。
- 前端测试验证静态工作流禁用执行器覆盖，初始输入不含伪 MR。
- 独立实例中通过真实鼠标、键盘运行 SPDK 模块分析，确认 Codex 被拉起、完整输入进入执行契约、报告引用真实源码和测试证据。
