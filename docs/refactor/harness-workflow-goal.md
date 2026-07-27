# CodeTalk Harness 与工作流系统重构总目标

> 本文定义本次重构不可偏离的最终状态、产品边界和完成标准。
>
> `harness-workflow-refactor-plan.md` 是本文的实施规格。若两份文档冲突，以本文为准。
> 具体实现路径可以根据源码审计修订，但不得降低或改变本文定义的目标。

## 一、总目标

将 CodeTalk 从当前偏“重型测试交付审核系统”的产品形态，重构为一个：

> **画布优先、内网真正可用、由 CodeTalk 自主 Harness 驱动、专业治理按需启用的 Agent 工作流平台。**

重构应保留现有 WorkflowVersion、RunSnapshot、任务状态、DAG、AgentHarnessFacade、Provider Adapter、Artifact Store、事件、超时、取消、重试、节点复用和源码证据本地复验能力，但必须重新划清执行、验证与专业治理边界。

## 二、目标状态

### 1. CodeTalk 拥有自己的 Harness

大厂 Agent SDK 仅作为能力研究参考，不得成为生产运行时依赖，也不得维护第二套任务状态、Session、Checkpoint 或 Artifact Store。

CodeTalk 应自主实现适合内网环境的 Harness 核心能力，包括：

- Provider 统一适配；
- DAG 执行；
- 节点级 Checkpoint；
- 中断恢复；
- 超时、取消和重试；
- 标准事件；
- Tool Call；
- Human-in-the-loop；
- 子 Agent 派发；
- 结构化输出；
- Artifact 收集和验证。

保留薄 Provider Adapter，用于适配：

- 内置模型；
- Codex CLI；
- Claude CLI；
- OpenCode。

Adapter 只负责执行器差异，不拥有 CodeTalk 的工作流和任务状态。

### 2. Harness 不包含测试领域规则

Harness 和基础 Workflow Runner 不得理解：

- iSCSI；
- NVMe；
- CHAP；
- SFMEA；
- RPN；
- 黑盒测试；
- 固定用例数量；
- 特定源码目录；
- 特定函数和协议场景。

Harness 只负责启动、输入、进程、权限、事件、超时、取消、恢复和产物收集。

SFMEA、黑盒测试、源码证据、独立审查等能力必须实现为可选 Validator 或 Governance 节点。

### 3. 只验收用户显式声明的输出

CodeTalk 永远不得验收当前工作流没有声明的产物。

如果用户只声明：

```text
report.md
```

系统只能检查 `report.md`，不得额外要求：

```text
sfmea.json
black_box_cases.json
source_scope.json
flow_map.md
test_strategy.md
```

所有 Validator 要求的产物必须是工作流声明输出的子集；不满足时在工作流编译或发布阶段报错，不能在任务完成后以缺少未声明产物为由阻断。

默认验收模式为：

```text
artifact_only
```

SFMEA、黑盒测试、源码证据和独立 Reviewer 只能由用户显式选择 Profile 或连接相应验收节点后启用。

### 4. 工作流设计改为 Canvas First

新建工作流后直接进入 XYFlow 画布，不再强制经过多步向导。

页面应为：

```text
左侧节点库 + 中间画布 + 右侧属性面板
```

顶部只保留：

```text
名称、保存、试运行、发布
```

用户可以直接在画布中添加和连接：

- 文本输入；
- 源码工作区；
- 文件输入；
- Agent；
- 输出；
- Validator；
- 人工审批。

输入节点自动生成试运行表单。

普通用户不需要填写 input ID、output ID、step ID、port ID 或 contract ID。内部 ID 由系统自动生成并保持稳定，只在高级或调试模式展示。

### 5. 内网可用性优先

网络安全策略分为：

```text
developer
intranet
strict_compliance
```

默认内网部署使用 `intranet`，允许：

- 配置的模型 Base URL；
- 管理员批准的企业代理；
- 企业 CA；
- Codex、Claude 等 CLI 访问批准的模型网关。

禁止 tracing、telemetry、自动更新、在线包下载和 Hosted MCP，但不得默认通过断网和清除所有代理使 Agent 无法运行。

现有严格零出站和 OS 网络隔离能力保留为 `strict_compliance`，只能由管理员显式启用。

### 6. 修复画布真实可用性

工作流设计器必须直接可访问。

节点注册、Provider 能力或工作流能力接口失败时，页面必须显示明确错误、失败接口、状态码和重试按钮，不得永久停留在“正在载入节点库”。

XYFlow 的脚本和样式必须包含在内网前端构建中，不依赖公网资源。

### 7. 兼容现有数据

历史已发布 WorkflowVersion、RunSnapshot、任务和产物保持不变并可继续查看。

写死 SPDK/iSCSI 的现有预设应改为明确的专业预设名称，并标记为 Legacy；不得继续以“基础源码报告”等通用名称对外提供。

## 三、完成标准

只有同时满足以下条件，才能认为目标完成：

1. 新建工作流后直接出现可操作画布。
2. 用户不需要填写内部 ID。
3. 用户能在画布上创建输入、Agent、输出和可选验收节点。
4. 自由源码分析只生成并验收 `report.md`。
5. 未声明产物永远不会进入验收要求。
6. 普通工作流不要求 SFMEA、黑盒用例或独立 Reviewer。
7. Harness 核心不包含任何存储测试领域规则。
8. 大厂 Agent SDK 不属于生产依赖。
9. 内置模型、Codex CLI、Claude CLI 和 OpenCode 通过薄 Adapter 接入。
10. 默认内网模式可以通过批准的模型地址、代理和企业证书运行。
11. 任务支持取消、超时、节点级 Checkpoint 和失败恢复。
12. 简单自由分析工作流和正式存储测试工作流均完成真实浏览器及内网 E2E。
13. 历史工作流和历史任务不受破坏。
14. 用户能够区分执行失败、产物验证失败和可选专业治理失败。

## 四、实施前置要求

实施时必须先审计现有实现与该目标的差距，输出目标架构和迁移计划，再按独立阶段修改代码。

不得为了满足目标重新引入：

- 第二套运行时；
- 第二套任务状态；
- 第二套 Artifact Store；
- 隐式治理；
- 新的重型向导；
- 默认不可用的内网安全模式。
