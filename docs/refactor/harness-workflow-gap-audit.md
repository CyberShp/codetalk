---
feature_ids:
  - harness-workflow-refactor
topics:
  - workflow
  - harness
  - provider-adapter
  - artifact-contract
  - network-policy
  - xyflow
  - migration
doc_kind: gap-audit
created: 2026-07-27
---

# CodeTalk Harness 与工作流差距审计

## 1. 审计结论

当前代码不是缺少工作流、Harness 或画布，而是三套已经存在的能力边界发生了重叠：

1. 工作流定义声明了输入、步骤和输出，但任务准备阶段仍会无条件创建测试活动、阶段规格和固定 V3 产物契约。
2. `AgentHarnessFacade` 的公开边界基本中立，但它后面的 `AgentRunHarness` 和调用契约仍携带 SFMEA、黑盒测试和测试活动规则。
3. XYFlow 画布已经具备拖拽、连线、删除、保存等基础能力，但新建入口仍先进入六步向导，普通属性面板仍暴露内部 ID。
4. 内置模型与 CLI Agent 没有走同一 Provider Adapter；执行器能力、取消和错误归一化因此存在两套路径。
5. 内网策略当前是布尔开关加默认禁用 Agent 出站，并清除全部代理，不等同于目标要求的三档部署策略。
6. WorkflowVersion、RunSnapshot、任务和 Artifact Store 已形成可靠历史真相源，应增量迁移，不能重写或复制。

因此，本次重构必须先修复“声明输出是唯一验收权威”，再拆 Harness/治理边界。直接先改 UI 或先抽大类文件，都可能继续保留隐式治理。

## 2. 审计基线与方法

- 基线分支：`codex/v3-productization-resume`
- 基线提交：`38e5032d`
- 目标依据：`docs/refactor/harness-workflow-goal.md`
- 审计方式：静态调用链、数据模型、前后端入口、测试资产和迁移代码审计。
- 本轮范围：只产出审计与设计，不修改产品行为、数据库或历史数据。

为便于追踪，本文将目标完成标准记为 `AC-01` 至 `AC-14`，编号与目标文档“三、完成标准”的 1 至 14 一一对应。

## 3. 总体差距矩阵

| 领域 | 当前状态 | 目标状态 | 结论 | 关联验收 |
|---|---|---|---|---|
| 输出验收 | Task prepare 无条件创建 Test Activity 和 V3 Artifact Contract | 只验收显式声明输出，默认 `artifact_only` | P0 差距 | AC-04/05/06/14 |
| Harness 边界 | Facade 中立，具体 Harness/Prompt 契约含测试领域规则 | Harness 与 Runner 完全领域中立 | P0 差距 | AC-07/09 |
| Provider | CLI 走 Facade，内置模型绕过 Facade | 四类 Provider 统一薄 Adapter | P0 差距 | AC-09 |
| 网络 | 布尔内网模式，默认 Agent 网络不可用，代理被清除 | developer/intranet/strict_compliance | P0 差距 | AC-10 |
| 设计器入口 | 新建先进入六步向导，旧入口重定向 | 新建即画布，设计器直达 | P0 差距 | AC-01/03 |
| 内部 ID | Registry 和属性面板展示并要求填写 ID | 自动稳定 ID，默认隐藏 | P0 差距 | AC-02 |
| 画布能力 | XYFlow 已有拖拽、连线、删除、持久化基础 | 增加 Validator/HITL 与清晰加载错误 | 部分具备 | AC-01/03 |
| Checkpoint/HITL | 有节点复用和阶段文本 checkpoint，无通用节点 checkpoint/HITL | 节点级 checkpoint、恢复、人工审批 | P1 差距 | AC-11 |
| 状态展示 | 驾驶舱已区分执行、质量、交付 | 保留并改为通用三轴状态 | 基础具备 | AC-14 |
| 历史兼容 | 已发布版本不可变，任务绑定版本，artifact 是事实源 | 原样保留并增量迁移 | 基础具备 | AC-13 |
| 大厂 SDK | 生产依赖未发现相关 SDK | 保持无生产依赖 | 已满足，需门禁 | AC-08 |
| E2E | 已有大量真实 UI 测试，但没有本目标完整矩阵 | 通用与专业流程、内网、恢复真实 E2E | P1 差距 | AC-12 |

## 4. Workflow 与编译契约

### 4.1 已有资产

- `backend/app/services/workflow_graph.py:202` 已将 Authoring Graph 编译为 `compiled_definition` 与 `compiled_plan`。
- `backend/app/services/workflow_graph.py:233` 至 `backend/app/services/workflow_graph.py:269` 已固化输入、输出、节点、端口、Provider、MCP、Skill、超时和失败策略。
- `backend/app/services/workflow_graph.py:557` 起已有输出契约检查；Agent 的 `required_artifacts` 必须等于其连接的输出集合。
- `backend/app/services/workflow_graph.py:683` 起按目标端口解析输入绑定，并拒绝多个标量边覆盖同一端口。
- `backend/app/services/workbench_task_compile.py:180` 至 `backend/app/services/workbench_task_compile.py:193` 会根据任务实际启用的输出重算步骤 `required_artifacts`。

这些代码可以成为“声明输出唯一权威”的基础，不应另建 Artifact Contract 真相源。

### 4.2 关键差距

- `backend/app/services/workflow_graph.py:271` 生成的编译定义没有 `validation_profile`、validator 集合或运行契约版本。
- `backend/app/services/workflow_graph.py:287` 和 `backend/app/services/workflow_scheduler.py:42` 将并发固定为 1，已有 DAG 仍只能串行执行。
- `backend/app/services/workflow_graph.py:219` 至 `backend/app/services/workflow_graph.py:231` 从节点配置读取 `step_id/contract_id/output_id`，使这些字段成为用户可编辑配置。
- 当前没有显式治理节点的编译模型，运行时只能通过文件名、模板或全局默认推断治理范围。

### 4.3 必须修复

1. 新编译契约显式冻结 `validation_profile`，默认 `artifact_only`。
2. 编译器先得到 `declared_outputs`，再检查每个 Validator 的 `required_outputs` 是否为其子集。
3. 删除运行时基于文件名、工作流名、Prompt 或关键字推断 Profile 的权限。
4. 内部 ID 由创建命令生成，后续重命名只改变 label，不改变 ID。
5. 并发能力在 Checkpoint 阶段另行开启，不能与边界重构混在同一提交。

## 5. 隐式 Test Activity 与 Artifact Contract

### 5.1 真实触发链

`backend/app/services/workbench_task_run.py` 在普通任务创建时：

1. `:178` 调用 `default_test_activity_stage_specs()`；
2. `:181` 调用 `default_artifact_contract_v3()`；
3. `:354` 调用 `build_test_activity_contract()`；
4. `:512` 又为每个步骤构造 Test Activity Contract；
5. 随后将上述对象写入 task bundle 和运行产物。

该链路并不以用户添加 Validator 或选择专业 Profile 为前置条件。

`backend/app/services/workbench_workflow_runner.py` 又在完成链中多次物化 V3 产物并执行质量审计：

- `:1400`、`:1462`、`:1520`、`:1560`、`:1617` 物化固定产物；
- `:1433`、`:1470`、`:1490`、`:1531`、`:1592`、`:1636` 执行测试活动审计；
- `:10947` 的 `_workflow_declares_test_activity_deliverables()` 仍通过输出模板和文件名识别测试治理。

### 5.2 领域规则证据

- `backend/app/services/test_activity_stage_specs.py:11` 至 `:20` 固定了源码证据、流程、SFMEA、黑盒测试、独立审查和发布阶段。
- `backend/app/services/artifact_contract_v3.py:28` 至 `:57` 默认要求 `sfmea.json`、`black_box_cases.json` 等专业产物。
- `backend/app/services/test_activity_contract.py:79` 起直接写入 iSCSI Login 的文件、符号、协议事实与测试目录。
- `backend/app/services/test_activity_contract.py:1427` 起根据目标文本和输出推断领域 Profile，并创建固定质量门禁。

### 5.3 结论

这是当前“普通工作流也被阻断”“生成 report 后质量只有 40%”的架构根因。现有 `_workflow_declares_test_activity_deliverables()` 只降低了一部分误触发，但仍属于隐式推断，不满足目标。

`test_activity_contract.py`、`test_activity_stage_specs.py` 和 `artifact_contract_v3.py` 不应删除其专业能力；它们应迁入显式的 Storage Test Governance 插件边界，并只能由编译后的 Validator 节点调用。

## 6. Harness 与 Provider Adapter

### 6.1 可保留的中立边界

- `backend/app/services/harness_facade.py:29` 至 `:58` 已定义 Provider 中立的请求与结果。
- `backend/app/services/harness_facade.py:61` 至 `:75` 已有 `ProviderAdapter` 协议。
- `backend/app/services/harness_facade.py:148` 起明确 Facade 是 workflow-facing 入口，不拥有 Workflow 状态。
- `backend/app/services/harness_facade.py:238` 至 `:255` 只接受声明过且位于 Artifact 根目录下的文件。

### 6.2 边界泄漏

- `backend/app/services/agent_run_harness.py:379` 起处理 `test_activity_contract`。
- `backend/app/services/agent_run_harness.py:584` 至 `:591` 直接构造 SFMEA 与黑盒测试输出协议。
- `backend/app/services/agent_invocation_contract.py:72` 至 `:115` 将 Test Activity Contract 作为通用 Agent 调用契约的一部分。
- `backend/app/services/workbench_workflow_runner.py:2184` 的内置模型走 `_execute_builtin_llm_step()`，而 CLI Agent 在 `:2231`、`:2262` 走 `AgentHarnessFacade`。

Facade 是正确方向，但当前实现尚未成为所有 Provider 的唯一入口。领域规则位于 Facade 后面，不能据此认定 Harness 已经中立。

### 6.3 能力缺口

- `ProviderAdapter` 缺少显式 `cancel()`、capabilities 和 resume/checkpoint 接口。
- `backend/app/services/agent_run_harness.py:110` 声明 `resume_supported=False`。
- 当前 Tool Call 主要是 CLI 输出解析与展示，不是 Workflow 所有的通用调用协议。
- 当前 subagent 主要是测试阶段配置，不是可追踪的子 Harness session。
- 未发现生产可用的 Human Approval 节点与 `waiting_for_input` 状态闭环。
- `backend/app/services/workbench_task_run.py:4444` 至 `:4450` 明确 RunSnapshot 只冻结不可变输入，运行状态、重试和输出不能写回其中。
- `backend/app/services/workbench_task_run_events.py:27` 至 `:46` 追加 JSONL 事件，`:116` 至 `:173` 另行改写 `task_run.json`；当前没有跨两份文件的崩溃一致性协议，也没有通用节点 checkpoint 真相源。

## 7. 网络策略

### 7.1 当前行为

- `backend/app/config.py:87` 使用 `intranet_network_mode: bool=True`，没有三档枚举。
- `backend/app/config.py:95` 默认 `intranet_agent_egress_enforced_by_host=False`。
- `backend/app/services/network_policy.py:237` 至 `:241` 因此默认判定 CLI Agent 网络不可用。
- `backend/app/services/network_policy.py:55` 至 `:74` 在内网模式清除全部代理变量，同时关闭 telemetry/update。
- `backend/app/services/agent_cli_bridge.py:420` 起在没有受控出口时直接阻断需要联网的 Agent。

内置模型侧已经支持配置代理和企业 CA，但 CLI Agent 的环境清洗会删除代理，两条执行路径标准不一致。

### 7.2 差距

当前策略把“禁止大厂遥测/更新”和“禁止执行器访问管理员批准的模型网关”绑定在一起。目标要求只禁止未批准行为，同时允许批准的 Base URL、代理、CA 和网关。

此外，`backend/app/services/network_policy.py:117` 至 `:120` 明确将 DNS 与出口防火墙交给部署侧；仅靠 CodeTalk host allowlist 不能阻止 CLI 绕过代理直连。因此目标架构必须规定可审计的批准代理或部署 egress policy，并用未批准目的地的真实负向探测验收。

### 7.3 迁移要求

1. 引入管理员级 `developer/intranet/strict_compliance` 枚举；旧布尔值只作为迁移输入。
2. 默认 `intranet` 注入白名单代理/CA，不继承任意用户代理，也不清空批准配置。
3. telemetry、tracing、update、hosted MCP 永久禁用项独立于网络模式。
4. RunSnapshot 冻结实际网络策略 ID、代理配置 ID、允许主机和实际访问摘要，不记录凭据。

## 8. XYFlow 设计器

### 8.1 已有资产

- `frontend/src/features/workflows/designer/workflow-canvas.tsx:255` 起使用真实 ReactFlow。
- 同文件 `:136` 起处理连线，`:263` 持久化拖动位置，`:293` 绑定连接事件，并已有删除、缩放和边标签能力。
- `frontend/src/features/workflows/designer/workflow-designer.tsx:273` 起已有节点库、画布、属性面板和底部诊断区的主体布局。
- XYFlow 样式由前端包本地构建，不依赖 CDN。

### 8.2 入口与加载差距

- `frontend/src/app/workflows/new/page.tsx:2` 仍渲染 `WorkflowWizard`。
- `frontend/src/features/workflows/workflow-wizard/workflow-wizard.tsx:44` 通过无 catch 的 `Promise.all(...).then(...)` 加载三类能力；任一失败都可能永久停在 `:113` 的“正在载入节点库”。
- `frontend/src/features/workflows/workflow-wizard/workflow-wizard.tsx:105` 至 `:117` 强制六步流程，画布直到第 5 步才出现。
- `frontend/src/app/workbench/designer/page.tsx:1` 至 `:5` 将旧设计器入口重定向到列表，而不是打开独立画布。
- `frontend/src/features/workflows/designer/workflow-designer.tsx:54` 虽有 catch 和重试，但四个接口仍被合并成一个错误，无法显示失败接口与状态码。

### 8.3 内部 ID 暴露

- `backend/app/services/workflow_node_registry.py:88` 至 `:148` 将 `contract_id/output_id/step_id` 声明为必填表单字段。
- `frontend/src/features/workflows/workflow-wizard/workflow-wizard.tsx:121` 让用户填写工作流 ID，`:139` 还用输入 ID 阻断向导。
- `frontend/src/features/workflows/designer/node-inspector.tsx:65` 起在普通属性面板展示 Node ID。
- `frontend/src/lib/types/workflow.ts:36` 至 `:68` 也将内部 ID 放在通用可编辑配置中。

前端创建节点时已经自动生成初始 ID，这是可以保留的基础；需要把生成权收敛为稳定命令，并从普通表单隐藏，而不是重新发明 ID 系统。

## 9. 历史数据兼容

### 9.1 已有可靠资产

- `backend/app/services/workflow_version_store.py:72` 明确已发布 WorkflowVersion 不可变。
- 同文件 `:77` 起在迁移前创建备份，`:628`、`:662`、`:694` 拒绝修改已发布版本。
- `backend/app/services/workbench_task_store.py:47` 明确 Run Artifact 是 Attempt 的事实源。
- 同文件 `:278` 起禁止修改 task、workspace、workflow version 等不可变字段。
- `backend/app/services/workbench_task_run.py:4444` 起建立 task-owned immutable RunSnapshot。

`backend/app/services/workflow_version_store.py:201` 至 `:227` 还表明 builtin preset bootstrap 会比较 canonical definition；定义变化会归档草稿并重建发布版本。因此 Legacy 标签不能通过修改同 ID preset definition 实现，必须使用独立展示元数据。

### 9.2 预设风险

- `backend/app/services/workflow_presets.py:430` 至 `:431` 将 `basic_source_report_codex`、`basic_source_design_report_builtin` 列为活跃预设。
- `backend/app/services/workflow_presets.py:444` 的 `_BASIC_ISCSI_REPORT_GOAL` 实际固定 iSCSI。
- 同文件 `:642` 将该目标注入所谓“基础”工作流。

不能原地修改这些已被历史任务引用的 ID 和版本内容。正确策略是保留历史快照，将显示名标注为 Legacy/专业预设，并用新 ID 创建真正通用模板。

## 10. SDK 与依赖审计

`backend/requirements.txt`、`frontend/package.json` 和生产导入路径中未发现 OpenAI Agents SDK、Claude Agent SDK、Microsoft Agent Framework 或 LangGraph。相关名称只出现在安全策略或测试说明中。

当前满足 AC-08，但必须增加依赖门禁，防止后续把研究 SDK 加入生产依赖。允许离线参考或独立 POC，不允许导入生产应用启动与执行路径。

## 11. 状态与可观察性

运行驾驶舱已经分别展示执行、质量和交付状态，属于应保留资产。但 Runner 当前会把固定测试门禁的阻断映射为整个任务不可交付，普通工作流因而无法理解“执行成功但专业治理失败”。

`backend/app/services/workbench_task_run_events.py:188` 至 `:206` 当前允许调用方分别写 `quality_status` 和 `delivery_status`，没有从执行/验收/治理状态确定性派生，也无法排除执行失败但交付完成等非法组合。

目标模型必须分开：

- `execution_status`：节点是否执行成功；
- `artifact_validation_status`：声明产物是否通过基础验证；
- `governance_status`：显式专业节点是否通过或未启用；
- `delivery_status`：是否可向用户交付。

未经显式启用的治理状态必须是 `not_requested`，不能是失败或阻断。

## 12. 文件处置建议

### 保留并演进

- `workflow_version_store.py`：继续作为 WorkflowVersion 真相源。
- `workbench_task_store.py` 与 RunSnapshot：继续作为任务/运行真相源。
- `workflow_graph.py`：升级为显式 Profile 和 Validator 编译器。
- `workflow_scheduler.py`：增加 checkpoint/recovery，不另建调度器。
- `harness_facade.py`：升级统一 Adapter 接口。
- Artifact Store 与路径边界校验：继续使用现有实现。
- `workflow-canvas.tsx` 与设计器状态管理：作为 Canvas First 基础。

### 迁移到明确边界

- `test_activity_contract.py`：迁入 Storage Test Governance 插件。
- `test_activity_stage_specs.py`：成为专业模板/插件配置，不由任务准备默认创建。
- `artifact_contract_v3.py`：拆成通用 Artifact Validator 与专业渲染/治理两部分。
- `agent_run_harness.py` 的测试活动 Prompt：迁入专业节点执行器。
- Runner 末端的大段质量审计/修复链：迁入 Validator/Governance 节点。

### 删除或退役

- 强制 `WorkflowWizard` 路由与步骤状态；可保留一次性模板选择弹层，不保留重型向导。
- 基于文件名、Prompt、目标文本推断治理 Profile 的运行时逻辑。
- 内网模式无条件清理全部代理的行为。
- 普通属性面板中的内部 ID 输入框。

删除仅指新运行路径退役；历史快照解析器和只读兼容代码必须保留。

## 13. 实施前风险

| 风险 | 后果 | 控制措施 |
|---|---|---|
| 先删固定契约再补显式节点 | 专业工作流失去治理 | 先建立 declared-output/profile 编译契约，再迁移插件 |
| 原地修改旧预设 | 历史任务语义漂移 | 旧 ID/版本只读，新建通用 ID |
| Provider 统一时重写进程层 | 取消、会话恢复回归 | Adapter 包装现有 runner，逐 Provider 切换 |
| 网络模式迁移错误 | 内网 Agent 全部不可用或过度放行 | 兼容读取旧配置，默认映射 intranet，真实网关 E2E |
| Canvas First 同时改 DSL | 草稿无法打开 | 先支持 V2 只读/编辑，再新增 V3；发布前双编译对照 |
| 新 Checkpoint 另建状态库 | 出现第二真相源 | 以 Attempt Artifact 内原子 checkpoint 为唯一权威；RunSnapshot 保持输入只读，event/task_run 仅作投影 |

## 14. 审计门禁结论

当前实现尚不满足 AC-01 至 AC-07、AC-09 至 AC-12；AC-13 有可靠基础但需要迁移保护；AC-14 有 UI 基础但语义仍被隐式治理污染；AC-08 当前满足。

在目标架构获确认前，不应开始全仓重构。允许的下一步仅是评审 `harness-workflow-target-architecture.md` 和 `harness-workflow-refactor-plan.md`，确认编译契约、插件边界、网络迁移和历史兼容策略。
