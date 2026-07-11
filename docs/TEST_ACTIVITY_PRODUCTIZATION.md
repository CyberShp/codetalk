---
feature_ids:
  - test-activity-productization
topics:
  - agent-workbench
  - testing
  - sfmea
  - black-box
doc_kind: product-baseline
created: 2026-07-08
---

# 测试活动编排产品化基线

本文说明 CodeTalk 如何从“能运行 Agent/LLM 工作流”演进为“面向测试工程的测试活动编排系统”。

## 测试人员怎么用

1. 在 `工作空间` 创建源码工作区，等待索引完成。
2. 在 `工作流设计` 从模板库导入或编辑测试活动工作流。
3. 在 `运行驾驶舱` 选择已建工作空间和已保存工作流。
4. 填写命名好的输入，例如分析目标、需求文件、设计文件、MR 链接、覆盖率文件、测试约束。
5. 点击 `准备运行`，确认输入、执行器、MCP、skills、输出目标。
6. 点击 `执行工作流`，在右侧查看当前节点、失败原因、质量审计和交付件。
7. 下载 `sfmea.json`、`black_box_cases.json`、流程报告、质量审计报告，必要时从失败节点重跑。

普通用户不需要手写 JSON。高级输入 JSON、raw output、prompt、schema、validation、diagnostics 只作为折叠诊断和导入/导出格式存在。

## 专业性由谁界定

专业测试关注点不交给模型自由发挥，而由三层契约共同界定：

- 用户显式输入：模块、测试意图、输出文件、约束和输入材料。
- 领域测试画像：iSCSI login、NVMe-oF transport、TLS、安全、TCP、bdev IO、RPC/config、reactor/thread/poller、持久化恢复、性能回归、资源生命周期、并发竞态、可观测性诊断。
- 项目测试画像：SPDK 的 `lib/nvmf`、`lib/iscsi`、`lib/bdev`、`lib/blobstore`、`lib/thread`、`lib/event`、`lib/rpc`、`lib/jsonrpc` 与对应测试目录。

AI 可以提出额外关注点，但未被源码、测试目录、GitNexus、CGC、coverage 或历史证据验证前，必须标为 `ai_suggested_unverified`。

## TestActivityContract

`TestActivityContract` 会在 AI 线程和运行驾驶舱中生成，并进入：

- `task_bundle`
- `workflow_contract`
- `execution_contract`
- `agent_output_contract`
- Agent prompt 或内置 LLM prompt
- `test_activity_contract.json`

契约包含：

- `target`
- `domain_profiles`
- `project_profile`
- `user_requirements`
- `required_outputs`
- `focus_rationale`
- `evidence_policy`
- `black_box_boundary`
- `quality_gates`
- `executor_requirements`
- `artifact_contract`

执行器必须按照契约生成声明的交付件，不能只在终端输出一句总结，也不能自由决定交付件骨架。

## 交付件模板

当前内置模板：

- `project_structure.md`
- `source_reading_plan.md`
- `module_map.md`
- `business_flow.md`
- `tester_code_understanding.md`
- `sfmea.json`
- `black_box_cases.json`
- `black_box_cases.md`
- `test_strategy.md`
- `test_design.md`
- `coverage_gap_report.md`
- `risk_review.md`
- `execution_checklist.md`

SFMEA 必须包含 failure mode、cause、effect、detection、severity、occurrence、detection score、RPN、score explanation、mitigation、source evidence、test mapping。

黑盒用例必须包含 case id、场景名、前置条件、输入/操作、预期结果、观测点、失败诊断线索、映射测试目录、源码或测试证据。黑盒步骤不得要求调用内部函数或修改内部源码。

每个结构化黑盒交付件还必须覆盖八个基础测试维度，并通过 `test_dimension` 明确标识：

- `normal_path`
- `invalid_input`
- `resource_pressure`
- `timeout`
- `reconnect`
- `concurrency`
- `recovery`
- `performance`

领域画像在这八个基础维度上继续约束专业内容。例如 NVMe/TCP/TLS 会叠加握手、证书/PSK、降级、队列、断连和密钥轮换，iSCSI login 会叠加 CHAP、digest、session reset 和多连接；不能由模型随意省略。

## 质量审计

运行完成后会生成 `test_activity_quality_audit.json`，并在驾驶舱右侧显示：

- 可交付/需要补证据
- 分数
- 问题数
- 第一条建议
- 详情预览入口

低质量产物不会被当成完成状态。典型拦截包括：

- 缺失 SFMEA 字段。
- 黑盒用例混入内部函数调用。
- 缺少源码或测试目录证据。
- 证据路径不存在。
- 声称生成 JSON 但没有 schema 或结构不符。
- JSON 数组为空、SFMEA 分值超出 1-10 或 RPN 计算不一致。
- 黑盒用例 ID/场景重复，或缺少八个基础测试维度。
- 任一 Markdown 交付件缺少声明章节、源码证据或测试目录映射。

## 执行器一致性

Claude、OpenCode、Codex、NGA、内置模型和自定义 Agent 都应接收同一份 `execution_contract`。

失败时 CodeTalk 会把内部异常转换成中文行动建议：

- 执行器不可用：检查命令、PATH 或完整可执行文件路径。
- Agent 只回复问候语：识别为无效输出，建议重试或切换执行器。
- Agent 查源码后停止：建议从失败节点续跑并直接输出缺失交付件。
- 输出文件缺失：提示当前节点没有写入声明 artifact。
- schema/质量审计失败：提示补字段、补证据或只重跑低质量交付件。

## 内网验收清单

建议明天内网至少执行：

- DeepSeek 内置模型跑 iSCSI login 测试设计。
- Claude Agent 跑同一任务，检查是否读源码、是否生成 artifact。
- `代码分析 -> 流程 -> SFMEA -> 黑盒用例` 完整工作流。
- 故意填坏路径、坏文件、不可用执行器，检查中文错误和下一步动作。
- 连续跑 3 个任务，检查页面是否卡顿、任务列表是否固定高度、交付件是否清楚下载。

## 本轮验证记录

已完成：

- 后端测试：`PYTHONPATH=. pytest tests/test_test_activity_contract.py ...`，13 条关键回归通过。
- 前端：`npm run lint` 通过，保留既有 e2e warning；`npm run build` 通过。
- 真实浏览器 E2E：`workbench-real.spec.ts` 的 source-flow SFMEA 黑盒工作流通过。

真实 E2E 覆盖：

- 浏览器创建工作空间。
- GitNexus 索引。
- 工作流设计页从模板导入并保存。
- 运行驾驶舱选择工作空间和工作流。
- 真实填写工作流输入。
- 准备并执行工作流。
- 显示质量审计可交付。
- 预览并下载 `sfmea.json` 和 `black_box_cases.json`。
- 验证诊断默认折叠。

## 成熟度基线

当前状态：可进入内网验收的 feat 版本，不是最终成熟稳定版。

已达到：

- 测试活动契约进入工作流和 AI 线程 prompt。
- 基础领域画像、SPDK 项目画像、交付件模板和质量审计可用。
- 驾驶舱显示中文失败原因、质量审计和可下载交付件。
- source-flow 链路真实 E2E 跑通。

仍需压测：

- DeepSeek 官方 API 与至少一个外部 Agent 的同题对照。
- NGA/Claude/OpenCode 在内网编码、session、MCP、超时下的稳定性。
- AI 线程任务卡、工作流模板选择、工作流完成后一键继续追问。
- 大 Markdown、长 SFMEA 表、100+ 黑盒用例的渲染性能。
- 连续 3 个以上任务、长任务心跳、浏览器刷新和后端重启恢复。
- 工作流设计器进一步拆组件，降低 500KB 单文件维护成本。
