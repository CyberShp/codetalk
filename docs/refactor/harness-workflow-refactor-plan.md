---
feature_ids:
  - harness-workflow-refactor
topics:
  - implementation-plan
  - workflow
  - harness
  - network-policy
  - canvas-first
  - migration
doc_kind: implementation-plan
created: 2026-07-27
---

# CodeTalk Harness 与工作流系统重构 Implementation Plan

**Feature:** Harness 与工作流边界重构  
**Goal:** 将 CodeTalk 重构为画布优先、内网可用、自主 Harness 驱动、专业治理按需启用的 Agent 工作流平台。  
**Acceptance Criteria:** 以 `harness-workflow-goal.md` 的 14 项完成标准为唯一终点。  
**Architecture:** 新 AuthoringGraphV3 编译为显式 CompiledWorkflowContractV3；现有 Orchestrator 与 Task/Attempt 体系保持运行真相源，RunSnapshot 只冻结输入，Attempt Artifact Store 中的原子 node checkpoint 负责恢复，Event/task_run 是可重建投影；所有 Provider 通过 CodeTalk Facade/Adapter；专业规则只存在于显式 Governance 节点。  
**Tech Stack:** FastAPI、Python 3.11、SQLite、Next.js、React、TypeScript、XYFlow、Playwright、pytest。  
**前端验证:** Yes；必须使用真实浏览器 hover、拖拽、连线、输入、保存、刷新、试运行和下载，不以 API 调用替代主流程。

---

## 0. 修订说明

本计划根据 `harness-workflow-gap-audit.md` 修订附件中的原始实施路径。目标没有降低，主要修正如下：

1. 原计划把“恢复基本可用性”混合了输出契约、网络和模板，无法独立回滚；现拆成声明输出、网络、Canvas、Harness、Governance、Checkpoint、兼容七个实施阶段，另设一个只读 Phase 0 前置门禁。
2. 不能先“瘦身 Harness”再定义编译契约，否则专业工作流会失去验收语义；必须先让 `declared_outputs` 和显式 Profile 成为运行权威。
3. 现有 `AgentHarnessFacade` 仅对 CLI 生效；内置模型统一 Adapter 是独立迁移，不应重写 Provider 实现。
4. 现有 XYFlow 已经可用，不重写画布；只替换新建入口、能力加载、内部 ID 暴露和节点类型。
5. 历史数据不能批量转换。新旧运行路径按冻结的 compiled contract 版本选择，不在运行时升级。
6. `test_activity_contract.py` 等专业能力不直接删除，先迁到插件边界，再移除核心导入。
7. Checkpoint/HITL/Tool/Subagent 依赖统一 Orchestrator/Harness 接口，不能与 Provider 迁移并行硬塞。

## 1. 完成边界

### 本计划必须交付

- 普通 `report.md` 工作流只验收 `report.md`。
- 默认 `artifact_only`，所有专业治理显式可见。
- Canvas First，普通用户不输入任何内部 ID。
- Builtin/Codex/Claude/OpenCode 四种 Provider 走统一薄 Adapter。
- 三档网络模式，默认 intranet 可使用批准网关、代理和 CA。
- Harness/Runner 核心无存储测试领域规则。
- 节点 Checkpoint、取消、超时、恢复、Tool Call、HITL、Subagent 可用。
- 历史发布版本、任务和 Artifact 原样可查看。
- 通用与正式存储测试工作流完成真实浏览器和内网 E2E。

### 本计划明确不做

- 引入任何大厂 Agent SDK 为生产依赖；
- 新建第二套 Session、Task、Checkpoint 或 Artifact Store；
- 云端 tracing、Hosted MCP、在线 Studio、自动下载或更新；
- 重写整个 XYFlow 设计器；
- 批量改写历史 WorkflowVersion/RunSnapshot；
- 用文件名、Prompt、关键词或工作流名称推断治理。

## 2. 实施规则

1. 每个阶段独立分支/提交，上一阶段验收通过后再开始下一阶段。
2. 每个 bug 或契约变化先写失败测试，再做最小实现。
3. 不删除旧路径，直到新路径真实 E2E 通过且历史兼容测试覆盖。
4. 数据迁移必须先备份，迁移函数幂等，并提供只读回滚路径。
5. 所有新运行状态留在现有 Task/Attempt/Artifact 体系；RunSnapshot 保持输入快照不可变，Event 和 `task_run.json` 是由权威 checkpoint 派生的投影。
6. 每个实现 PR 必须映射到 `AC-01` 至 `AC-14` 至少一项。
7. 同一实现者不自审；合入前执行独立 review 与 merge gate。

## 3. Phase 0：特征测试与契约冻结

**目标：** 在改变行为前，用测试固定现有历史读取、任务状态、Artifact 边界和画布交互。

### 改动边界

- 只增加 characterization tests、fixtures 和迁移快照。
- 不改变运行逻辑、UI、数据库 schema 或默认配置。

### 重点文件

- `backend/tests/test_workflow_graph.py`
- `backend/tests/test_workflow_version_store.py`
- `backend/tests/test_workbench_task_run.py`
- `backend/tests/test_harness_facade.py`
- `backend/tests/test_network_policy.py`
- `frontend/e2e/workflow-v2-canvas-real.spec.ts`
- `frontend/e2e/workflow-v2-input-ports-real.spec.ts`
- 新增 `backend/tests/fixtures/harness_workflow_refactor/`

### 测试先行

1. 固定一个 V1、V2 已发布工作流和历史 RunSnapshot fixture。
2. 固定 `report.md` 单输出工作流当前被附加 Test Activity 的错误行为；用 `xfail(strict=True)` 标记并写明 Phase 1 转绿条件，防止静默 XPASS。
3. 固定声明 Artifact 路径越界会被拒绝。
4. 固定取消、idle timeout、节点复用和事件序列的当前行为。
5. 固定真实画布拖入、连接、删除、保存、刷新恢复。

### 验证命令

```bash
PYTHONPATH=backend python3.11 -m pytest \
  backend/tests/test_workflow_graph.py \
  backend/tests/test_workflow_version_store.py \
  backend/tests/test_workbench_task_run.py \
  backend/tests/test_harness_facade.py \
  backend/tests/test_network_policy.py -q

cd frontend && npx playwright test \
  e2e/workflow-v2-canvas-real.spec.ts \
  e2e/workflow-v2-input-ports-real.spec.ts
```

### 迁移风险

无生产迁移。风险是 fixture 捕获了偶然实现细节；只固定外部契约、冻结数据与用户可见行为。

### 回滚

删除本阶段新增测试/fixture 即可，不涉及数据。

### 验收

- 历史 V1/V2 与 RunSnapshot fixture 可读取；
- 现有核心能力测试全绿；
- 隐式治理的预期失败用例稳定复现；
- 未修改任何产品文件。

### 建议提交

`test(refactor): freeze workflow harness compatibility contracts`

## 4. Phase 1：声明输出与显式 Validation Profile

**目标：** 让当前工作流声明的输出成为唯一验收权威，普通工作流默认 `artifact_only`。

**映射：** AC-04、AC-05、AC-06、AC-14。

### 改动边界

- 新增 AuthoringGraphV3/CompiledWorkflowContractV3 的最小后端模型。
- 编译器支持 `validation_profile` 与 Validator 输出子集校验。
- 新 V3 task prepare 不再默认创建 Test Activity/V3 固定产物契约。
- 保留 legacy compiled snapshot 的旧路径。
- 暂不移动专业规则文件，不改 Canvas 页面结构。

### 重点文件

- `backend/app/services/workflow_graph.py`
- 新增 `backend/app/services/workflow_contract_v3.py`
- `backend/app/services/workbench_task_compile.py`
- `backend/app/services/workbench_task_run.py`
- `backend/app/services/workbench_workflow_runner.py`
- `frontend/src/lib/types/workflow.ts`
- `backend/tests/test_workflow_graph.py`
- 新增 `backend/tests/test_workflow_validation_profiles.py`
- 新增 `backend/tests/test_declared_artifact_authority.py`

### TDD 用例

1. `report.md` 是唯一输出时，task bundle/RunSnapshot/required artifacts 只包含 `report.md`。
2. `artifact_only` 不创建 Test Activity Contract，不运行 SFMEA/黑盒/独立 Reviewer。
3. Validator 要求未声明 `sfmea.json` 时，编译失败并定位 Validator。
4. 不允许通过文件名、Prompt、工作流名称推断 Profile。
5. `none` 不做 Artifact 阻断；`schema` 只校验声明 Schema。
6. 旧 frozen snapshot 仍按 legacy 行为读取，新 V3 任务不受影响。
7. 四轴状态中未启用治理为 `not_requested`。
8. Profile 展开出的 handler 未注册时只能保存草稿，试运行和发布明确拒绝；Phase 5 前不得发布 `storage_test_design/formal_release`。
9. `delivery_status` 只能按 execution/artifact/governance 三轴派生，非法组合写入失败。
10. 编译契约冻结 goal、Prompt 模板/版本、skill instructions 和输入渲染规则；多行、空白及长用户输入进入 RunSnapshot 后逐字传到 Adapter。
11. execution 完成但 artifact/governance 仍运行时 delivery=pending；仅 non-blocking governance 失败时 governance=warning、delivery=ready。

### 实现步骤

1. 定义 V3 schema 与编译输出，不接运行器。
2. 增加 V2/V3 编译分派，发布 V3 时冻结显式 Profile。
3. 让 task compile 从 `declared_outputs` 唯一构造 required artifacts。
4. 让 V3 task prepare 跳过默认 Test Activity/V3 专业契约。
5. 在 V3 runner 只执行编译计划中的 Validator。
6. 保留 legacy adapter，明确写入事件 `legacy_runtime_contract`。
7. 增加 handler capability registry；编译可以保留不可执行草稿，发布与试运行必须对未知 handler fail closed。

### 验证命令

```bash
PYTHONPATH=backend python3.11 -m pytest \
  backend/tests/test_workflow_graph.py \
  backend/tests/test_workflow_validation_profiles.py \
  backend/tests/test_declared_artifact_authority.py \
  backend/tests/test_workbench_task_run.py -q
```

### 迁移风险

- 专业工作流若被误识别为 V3，可能失去治理。
- 状态字段新增可能影响驾驶舱解析。

控制：按 `compiled_contract_version` 分派；旧快照永不自动升级；API 新字段全部向后兼容。

### 回滚

关闭 V3 workflow creation feature flag；现有新 V3 草稿保留但禁止发布，历史路径不变。

### 验收

- 单输出工作流的 bundle、运行事件、Artifact 和驾驶舱中均不存在幽灵输出；
- 专业治理只能来自显式 Profile/节点；
- 历史 fixture 全绿。

### 建议提交

`refactor(workflow): make declared outputs the validation authority`

## 5. Phase 2：三档网络与批准代理

**目标：** 默认 intranet 能运行批准的模型和 CLI Agent，同时继续禁止遥测、更新与 Hosted MCP。

**映射：** AC-10。

### 改动边界

- 配置模型由布尔值改为枚举，保留旧配置读取。
- 只注入管理员批准的 proxy/no_proxy/CA。
- 为网络型 CLI Agent 增加 `approved_proxy_gateway` 与 `deployment_egress_policy` 两种管理员强制边界；环境变量本身不算出站控制。
- Provider readiness probe 和真实运行使用同一网络决策。
- 不改 Harness 领域边界，不改工作流 DSL。

### 重点文件

- `backend/app/config.py`
- `backend/app/services/network_policy.py`
- `backend/app/services/agent_sandbox.py`
- `backend/app/services/agent_cli_bridge.py`
- `backend/app/services/agent_run_harness.py`
- `backend/app/llm/factory.py`
- 设置 API 与设置页对应文件
- `backend/tests/test_network_policy.py`
- 新增 `backend/tests/test_network_mode_migration.py`
- `frontend/e2e/v3-intranet-model-policy-real.spec.ts`

### TDD 用例

1. 默认 intranet 允许已配置 Base URL。
2. 已批准代理/CA 注入 Agent；未知环境代理不继承。
3. telemetry/update/Hosted MCP 环境永久关闭。
4. strict_compliance 默认断网并要求 OS 隔离。
5. developer 不强制 namespace，但仍不启用遥测。
6. readiness probe 与 run 对同一配置做出一致结论。
7. 日志和网络快照只记录配置 ID，不泄露凭据。
8. CLI 尝试访问未批准的本地测试目的地时，被批准代理或部署 egress policy 真实拒绝；不能只断言环境变量。
9. 未配置强制边界时，只阻断需要联网的 CLI Adapter，并给出配置建议；内置模型和离线 Agent 仍可运行。

### 迁移风险

- 旧 `intranet_network_mode` 映射错误；
- 企业代理认证值泄露；
- readiness 与实际运行环境不一致。

### 回滚

保留旧变量读取；feature flag 切回 legacy network decision。不得通过回滚恢复“清除全部代理后宣称内网可用”。

### 验收

- DeepSeek/内置模型通过批准 Base URL 运行；
- Codex CLI 通过批准网关或代理运行；
- 负向探测证明 CLI 不能绕过批准边界访问未批准测试目的地；运行诊断确认无 telemetry/update/Hosted MCP 目标；
- 403、CA 和代理错误均给出具体中文修复建议。

### 建议提交

`feat(network): add usable intranet and strict compliance modes`

## 6. Phase 3：Canvas First 与稳定内部 ID

**目标：** 用户新建后直接操作画布，不经过重型向导，不填写内部 ID。

**映射：** AC-01、AC-02；AC-03 的基础节点在本阶段完成，Validator/HITL 分别由 Phase 5/6 完成。

### 改动边界

- 复用现有 XYFlow 组件和 reducer，不重写画布。
- 新建 API 生成 workflow/node/port/contract/output IDs。
- 轻量模板弹层取代六步向导。
- 能力接口独立加载、独立报错和重试。
- Palette 只展示当前后端声明为可执行的节点；Validator 与 Human Approval 分别在 Phase 5/6 handler 可用时上线，不允许出现“可发布但运行时 skipped”的占位节点。

### 重点文件

- `frontend/src/app/workflows/new/page.tsx`
- `frontend/src/app/workbench/designer/page.tsx`
- `frontend/src/features/workflows/designer/workflow-designer.tsx`
- `frontend/src/features/workflows/designer/workflow-canvas.tsx`
- `frontend/src/features/workflows/designer/node-inspector.tsx`
- `frontend/src/features/workflows/workflow-graph.ts`
- 退役 `frontend/src/features/workflows/workflow-wizard/workflow-wizard.tsx`
- `frontend/src/lib/api/workflows.ts`
- `backend/app/services/workflow_node_registry.py`
- Workflow API route/schema
- `frontend/e2e/workflow-v2-canvas-real.spec.ts`
- `frontend/e2e/workflow-v2-input-ports-real.spec.ts`
- 新增 `frontend/e2e/workflow-canvas-first-real.spec.ts`

### TDD 用例

1. 点击新建后一次导航进入可操作画布。
2. 空白模板与自由源码分析模板均不出现向导。
3. 用户只填写显示名称；内部 ID 自动生成、保存刷新后稳定。
4. 属性面板默认不展示内部 ID，高级诊断只读展示。
5. 添加 directory/file 输入并连接不同类型端口，错误连接立即拒绝。
6. 试运行表单按输入节点 label/type/required 生成。
7. capability/provider/registry 任一失败时显示 endpoint、status、commit SHA、重试。
8. 画布拖拽、平移、连线、删线、删节点、撤销、保存、刷新恢复真实执行。

### 迁移风险

- V2 草稿属性面板仍需要旧 ID 字段；
- 路由切换影响旧书签；
- 自动 ID 在复制/撤销时冲突。

控制：V2 旧草稿进入兼容编辑模式；新 V3 使用稳定 ID 命令；旧路由 302 到具体 designer 而非列表。

### 回滚

保留旧向导组件一个版本但从路由下线；feature flag 可恢复入口。已保存 V3 草稿不删除。

### 验收

- 1440x900 和 390x844 真机浏览器均完成新建、拖拽、连线、保存、刷新、试运行；
- 用户全程不输入 JSON 或内部 ID；
- 前端生产包网络摘要中无 XYFlow CDN 请求。

### 建议提交

`feat(workflows): make the XYFlow canvas the authoring entry`

## 7. Phase 4：Harness 瘦身与统一 Provider Adapter

**目标：** 所有 Agent/模型执行通过同一个领域中立 Harness 边界。

**映射：** AC-07、AC-08、AC-09。

### 改动边界

- 扩展 Facade/Adapter 接口：capabilities、resume、cancel、artifact candidates。
- 包装现有内置模型路径为 `BuiltinModelAdapter`。
- 为 Codex/Claude/OpenCode 建立明确 Adapter 类型，内部先复用现有 CLI bridge。
- 从通用 Harness request/prompt contract 移除 Test Activity 字段。
- 不在本阶段迁移专业 Validator 实现，只保证旧专业流程走 legacy adapter。

### 重点文件

- `backend/app/services/harness_facade.py`
- 新增 `backend/app/services/provider_adapters/`
- `backend/app/services/agent_run_harness.py`
- `backend/app/services/agent_invocation_contract.py`
- `backend/app/services/workbench_workflow_runner.py`
- `backend/app/services/agent_cli_bridge.py`
- `backend/app/llm/factory.py`
- `backend/tests/test_harness_facade.py`
- 新增 `backend/tests/test_provider_adapter_contract.py`
- 新增 `backend/tests/test_harness_domain_neutrality.py`

### TDD 用例

1. 四个 Adapter 通过同一契约测试套件。
2. 不支持的 capabilities 在发布/preflight 明确失败，不静默忽略。
3. Builtin 与 CLI 产生同一类 run_started/activity/artifact/completed 事件。
4. 取消调用 Adapter cancel，且只由 Orchestrator更新 Task 状态。
5. Harness 只收集声明 Artifact，拒绝越界和未声明文件。
6. 静态架构测试禁止 Harness/Adapter 导入专业治理模块或包含领域关键常量。
7. `requirements.txt`、lockfile、可选依赖、生产 import 和 vendor 目录均不含大厂 Agent SDK；研究 POC 必须位于非生产隔离目录且不被应用导入。
8. `session_resume=true` 走 Adapter resume；不支持 resume 时返回结构化 unsupported，并由 Orchestrator按 checkpoint 安全重启节点。
9. Adapter 接收到的用户多行输入与 RunSnapshot 原始值逐字一致，不在首个换行截断。

### 迁移风险

- CLI 事件清洗、session resume、Windows `.cmd` 解析回归；
- 内置模型流式输出语义变化；
- 双路径重复写事件。

控制：Adapter 首先包装旧实现；按 Provider feature flag 单独切换；一个 Attempt 只能选一个执行路径。

### 回滚

按 Provider 关闭新 Adapter；保留 legacy adapter 一个迁移周期。新 Facade 契约向后兼容旧调用参数。

### 验收

- Builtin/Codex/Claude/OpenCode 契约测试全绿；
- 至少 Builtin 与当前环境可用的一个 CLI Agent 完成真实源码分析；
- Harness 核心领域中立静态门禁全绿；
- 无第二套任务、session 或 Artifact 真相源。

### 建议提交

`refactor(harness): unify builtin and cli provider adapters`

## 8. Phase 5：Artifact Validator 与 Governance Plugin

**目标：** 将专业测试治理从 Runner/Harness 迁成显式插件节点，同时保留正式存储测试能力。

**映射：** AC-04、AC-05、AC-06、AC-07、AC-14。

### 改动边界

- 提取通用 Artifact Exists、JSON Schema、Source Evidence Validator。
- 建立 Governance Plugin registry。
- 将只读 `validator` 与生成型 `governance` 分成两类节点：前者只返回 ValidationResult，后者必须通过输出端口产生预先声明的 Artifact。
- 将 SFMEA、黑盒、Storage Test Design、Independent Reviewer 迁入插件包。
- Profile 在编译时展开为可见节点。
- 删除 V3 Runner 对 Test Activity 默认构造和文件名推断。

### 重点文件

- 新增 `backend/app/services/validators/`
- 新增 `backend/app/services/governance_plugins/`
- 迁移 `test_activity_contract.py`
- 迁移 `test_activity_stage_specs.py`
- 拆分 `artifact_contract_v3.py`
- `backend/app/services/workbench_workflow_runner.py`
- `backend/app/services/workflow_graph.py`
- `backend/app/services/workflow_node_registry.py`
- 新增 `backend/tests/test_governance_plugin_registry.py`
- 新增 `backend/tests/test_validator_declared_output_subset.py`
- 迁移现有专业审计测试

### TDD 用例

1. `artifact_only` 只验证声明文件存在/非空/路径安全。
2. `source_evidence` 只验证连接到该节点的声明 Artifact。
3. `storage_test_design` 才加载 SFMEA/黑盒规则。
4. Validator 所需 Artifact 非声明子集时发布失败。
5. 普通 Runner/Harness 不导入 Governance 包。
6. 专业工作流继续输出并验收其显式声明的完整文件集。
7. 插件失败只改变 governance/delivery，不伪装成 Provider 执行失败。
8. 生成型 Governance 的每个 Artifact 都有声明 output、连接边和唯一 producer；只读 Validator 无权增加用户交付文件。

### 迁移风险

- 迁移期间同一专业规则被 legacy 与 plugin 重复执行；
- 固定报告物化逻辑被错误放入通用 Artifact Validator。

控制：按 compiled contract version 单选实现；插件输出也必须先在工作流声明；运行事件记录 handler ID。

### 回滚

V3 专业模板暂停发布，legacy 专业工作流继续只读/运行；普通 V3 artifact_only 不回退。

### 验收

- 通用报告与正式存储测试两条真实流程都通过；
- 通用流程运行目录中没有 Test Activity/SFMEA 幽灵契约；
- 专业插件规则与原能力对照测试无丢失；
- UI 清楚区分产物验证失败和治理失败。

### 建议提交

`refactor(governance): make professional validation explicit nodes`

## 9. Phase 6：Checkpoint、恢复、Tool Call、HITL 与 Subagent

**目标：** 补齐 CodeTalk 自主 Harness 的通用长期运行能力，不引入第二套状态。

**映射：** AC-11。

### 改动边界

- 在现有 Attempt Artifact 目录增加原子 `checkpoints/<node_id>.json`；RunSnapshot 继续保持 immutable input snapshot。
- Orchestrator 从未完成节点恢复，并按 hash 复用成功节点。
- 增加受控 Tool Call 协议、Human Approval 节点和 child session。
- 不引入外部 Agent SDK 或新数据库。

### 重点文件

- `backend/app/services/workflow_scheduler.py`
- `backend/app/services/workbench_task_store.py`
- `backend/app/services/workbench_task_run.py`
- `backend/app/services/harness_facade.py`
- 新增 `backend/app/services/node_checkpoint.py`
- 新增 `backend/app/services/tool_dispatch.py`
- 前端运行驾驶舱状态与审批组件
- 新增 `backend/tests/test_node_checkpoint_recovery.py`
- 新增 `backend/tests/test_harness_tool_call.py`
- 新增 `backend/tests/test_human_approval_node.py`
- 新增 `backend/tests/test_subagent_sessions.py`
- 新增真实浏览器恢复 E2E

### TDD 用例

1. 浏览器中断/后端重启后，从首个未完成节点继续。
2. 节点定义或输入 hash 变化时不得错误复用。
3. cancel 终止活动 Provider task 并写 terminal event。
4. total timeout 与 idle timeout 分别触发，活动输出会刷新 idle 计时。
5. HITL 进入 `waiting_for_input`，批准后续跑，拒绝按失败策略结束。
6. Tool Call 必须经过工具 registry、Schema 和权限校验。
7. child session 的事件和 Artifact 可追踪，但不创建新 Task 真相源。
8. 任务恢复不重复交付旧 Artifact。
9. checkpoint 提交后、事件追加前模拟崩溃，重启后从 checkpoint 重建事件和 `task_run.json` 投影，不重复执行节点。
10. checkpoint 提交前模拟崩溃，临时 Artifact 被忽略并按 idempotency key 安全重试。
11. 旧 revision 不能覆盖新 revision，相同 idempotency key 的重复提交不产生第二份交付。

### 迁移风险

- checkpoint 原子性不足导致重复执行；
- Provider session 可恢复性被误当成节点可恢复性；
- HITL 等待被总超时错误取消。

控制：节点 Artifact 原子 rename 后，以 `fsync + atomic replace` 提交 checkpoint 作为唯一 commit point；事件和 `task_run.json` 是可重建投影。无法 resume 的 Provider 从该节点按 idempotency key 安全重试；审批等待采用独立期限。

### 回滚

关闭 checkpoint reuse/HITL/tool/subagent feature flags，新数据仍作为诊断 Artifact 可读；基础串行执行继续工作。

### 验收

- 取消、总超时、idle timeout、后端重启恢复均完成真实运行；
- 节点复用与重跑事件可解释；
- 无新增任务/Artifact 状态库；RunSnapshot 未被写入可变运行状态。

### 建议提交

`feat(harness): add node checkpoints and interactive execution`

## 10. Phase 7：历史兼容、预设重分类与完整验收

**目标：** 新架构成为默认路径，历史数据保持可追溯，完成产品级真实验收。

**映射：** AC-12、AC-13，并回归 AC-01 至 AC-14。

### 改动边界

- 新增通用 V3 模板；旧硬编码预设通过独立 presentation metadata 标记 Legacy/专业范围，不修改 canonical definition。
- 设置迁移、草稿迁移预览、前后端版本提示。
- 完整真实浏览器、内网、恢复、兼容和性能验收。
- 删除已经无调用的新运行路径兼容胶水，但保留历史只读解析。

### 重点文件

- `backend/app/services/workflow_presets.py`
- `backend/app/services/workflow_version_store.py`
- `backend/app/services/workbench_task_store.py`
- 工作流列表/模板 UI
- 运行驾驶舱与历史详情 UI
- E2E 与部署脚本
- `docs/refactor/harness-workflow-migration-runbook.md`
- `docs/refactor/harness-workflow-acceptance-report.md`

### 预设策略

新通用模板使用新 ID：

- 空白画布；
- 自由源码分析；
- 源码 + 可选设计文档；
- 变更影响分析；
- 多 Agent 分析；
- 正式存储测试设计。

旧 `basic_source_report_codex`、`basic_source_design_report_builtin` 不改历史内容。独立 presentation metadata 将其显示为 Legacy SPDK/iSCSI 专业预设，不触发 builtin preset bootstrap 重建版本，也不再作为默认模板。缺少 `compiled_contract_version` 的冻结定义确定性进入 legacy runner；未知非空版本拒绝运行。

### 真实验收矩阵

1. Builtin：源码工作区 + 分析目标 -> `report.md`，只做 artifact_only。
2. 当前可用 CLI Agent：同一通用流程，输入逐字到达并生成 `report.md`。
3. 正式存储测试：源码 + 设计文档 -> 流程/SFMEA/黑盒 Artifact -> 显式治理。
4. 错误输入：坏路径、坏文件、不可用 Provider，错误中文可行动。
5. 网络：approved Base URL/proxy/CA，确认无遥测、更新、Hosted MCP。
6. 取消、idle timeout、总超时、浏览器刷新、后端重启恢复。
7. 历史 V1/V2 工作流、任务、RunSnapshot 和 Artifact 打开/下载。
8. 1440x900 与 390x844 UI；拖拽、连线、删线、保存、刷新、试运行。

### 性能与可靠性

- 普通自由源码分析采用执行档位标注，不把小于 8 分钟一律判错；以真实 Provider 活动、源码读取和 Artifact 为完成证据。
- 长任务至少运行 30 分钟，页面不冻结，任务列表不拉长主页面。
- 三个并发任务状态与 Artifact 不串线。
- 大报告和上百条结果采用虚拟化/边界滚动，Artifact 下载内容完整。

### 迁移风险

- Legacy 标签被误认为删除；
- 新模板与历史 ID 冲突；
- 兼容胶水过早删除。

### 回滚

发生回滚时暂停新建/发布 V3，历史数据和 Legacy 列表仍可只读访问，V3 workflow 也只读保留；不得把写死的 Legacy 预设重新设为通用默认。数据库迁移使用既有备份机制恢复。

### 验收

- `harness-workflow-goal.md` 14 项全部有测试证据；
- 状态只能为 Pass、Known Issue 或 Blocked，不留空；
- 无 P0/P1；
- 独立 reviewer 通过；
- 输出接受报告、截图、trace、事件、Artifact、网络摘要和迁移回归证据。

### 建议提交

`feat(workflows): complete v3 migration and product acceptance`

## 11. 跨阶段测试门禁

每阶段至少执行：

```bash
git diff --check
PYTHONPATH=backend python3.11 -m pytest <本阶段后端测试> -q
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run build
cd frontend && npx playwright test <本阶段真实 E2E>
```

架构边界增加静态门禁：

```text
Harness/Adapter/Orchestrator 不得导入 governance_plugins
生产 manifest、可选依赖、import、vendor 与启动路径不得包含大厂 Agent SDK
V3 Runner 不得调用文件名/Prompt/目标文本 Profile 推断
Validator.required_outputs 必须是 declared_outputs 子集
未注册 node handler 的工作流不得试运行或发布
delivery_status 必须由其他三轴确定性派生
```

## 12. 回归失败处理

每个失败走固定闭环：

```text
复现 -> 保留 trace/事件/Artifact/日志 -> 定位调用链 -> 红测试
-> 最小修复 -> 同用例重跑 -> 相邻契约回归
```

禁止用以下方式“修复”：

- 删除证据或降低声明输出真实性；
- 将端口类型改为 `any`；
- 增加新的隐式默认产物；
- 把治理失败改名为执行失败；
- 通过缩短超时让任务更快失败；
- 让前端隐藏后端错误；
- 只用 API 代替真实浏览器验收。

## 13. 阶段依赖与可并行性

- Phase 0 完成后才能改代码。
- Phase 1 是 Phase 3、4、5 的契约前置。
- Phase 2 可与 Phase 1 独立开发，但必须在 Provider E2E 前完成。
- Phase 3 与 Phase 4 在 Phase 1 后可并行开发，合并时只通过编译契约联动。
- Phase 5 依赖 Phase 1 和 Phase 4。
- Phase 6 依赖 Phase 4，不依赖具体专业插件。
- Phase 7 等待 Phase 3、5、6 完成。

## 14. 目标追踪矩阵

| 验收项 | 主阶段 | 证据类型 |
|---|---|---|
| AC-01 新建即画布 | Phase 3 | Playwright 视频/截图 |
| AC-02 隐藏内部 ID | Phase 3 | UI E2E + schema test |
| AC-03 可建基础/验收节点 | Phase 3/5/6 | 画布 E2E |
| AC-04 只生成/验收 report | Phase 1 | bundle + Artifact + UI E2E |
| AC-05 未声明永不验收 | Phase 1/5 | 编译负例 + runtime test |
| AC-06 普通流无专业治理 | Phase 1/5 | event/contract absence |
| AC-07 Harness 无领域规则 | Phase 4/5 | import/static gate |
| AC-08 无大厂 SDK | Phase 4 | dependency gate |
| AC-09 四 Adapter | Phase 4 | shared contract suite |
| AC-10 默认内网可用 | Phase 2/7 | 真实网关 E2E/网络摘要 |
| AC-11 checkpoint/恢复 | Phase 6 | 中断/重启 E2E |
| AC-12 两类真实 E2E | Phase 7 | trace/artifacts |
| AC-13 历史不破坏 | Phase 0/7 | frozen fixture/migration test |
| AC-14 状态可区分 | Phase 1/5/7 | API + cockpit E2E |

## 15. 启动条件

本计划目前处于“待架构确认”。在用户确认以下内容前，不开始 Phase 0 之后的产品实现：

1. AuthoringGraphV3 与 legacy 只读兼容策略；
2. declared_outputs 唯一权威；
3. Validation Profile 显式展开为可见节点；
4. 专业规则迁入插件；
5. 内置模型也走统一 Adapter；
6. 三档网络与默认 intranet；
7. Checkpoint 使用 Attempt Artifact 目录中的原子权威记录；RunSnapshot 只冻结输入，Event/task_run 仅作可重建投影。

确认后从 Phase 0 开始，按阶段逐个提交、验证和评审，不启动全仓一次性重构。
