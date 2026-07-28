---
feature_ids:
  - harness-workflow-refactor
  - AC-04
  - AC-05
  - AC-06
  - AC-07
  - AC-14
topics:
  - artifact-validator
  - governance-plugin
  - declared-output
  - professional-testing
  - four-axis-status
doc_kind: verification-record
created: 2026-07-28
---

# Harness 与工作流重构 Phase 5 验证记录

## 1. 范围与结论

本阶段只实施重构计划 Phase 5：把通用 Artifact 校验和专业测试治理变成
AuthoringGraph V3 中显式、可见、可编译、可执行的节点。普通工作流只验收用户
声明的输出；SFMEA、黑盒测试、存储测试设计和独立审查只有在用户选择对应
Profile 或显式放置节点时才加载。

本阶段没有开始 Phase 6 的 Checkpoint、恢复、HITL、Tool Call 或 Subagent，
也没有迁移/删除历史 V1/V2 工作流。V1/V2 专业测试兼容链继续执行历史治理；
V3 由 `compiled_contract_version=3` 单选新的显式 handler 路径。

## 2. 实现结果

- 新增领域中立 `ArtifactExistsValidator`、`JsonSchemaValidator` 和
  `SourceEvidenceValidator`，只读取声明输出子集并返回结构化结果；
- 新增惰性 `GovernancePluginRegistry`，注册 `storage_test_design`、`sfmea`、
  `black_box` 和 `independent_review`，普通 `artifact_only` 流程不会加载专业模块；
- `validator` 为只读节点，无权增加交付文件；`governance` 为生成节点，只能沿
  显式输出端口物化预先声明且具有唯一 producer 的 Artifact；
- 专业 Profile 在编译预览中展开为用户可见的 handler 节点，未注册 handler
  会阻止发布/运行；普通 V3 不按文件名、目标文本或输出内容推断 Test Activity；
- Governance 节点从稳定语义端口接收上游声明 Artifact。Runner 只传不可变引用，
  Dispatcher 在读取时重新校验根目录、普通文件、symlink、大小、UTF-8 和 JSON；
- `storage_test_design` 的输入端口为 `source_evidence`，输出端口为 `sfmea` 与
  `black_box_cases`。服务端生成稳定 ID，同时保留 `binding_key`，运行时不会把
  显示 label 或随机端口 ID 当业务语义；
- 专业生成链真实消费 Agent 产生且 SHA256 校验的源码证据。浏览器验收故意把
  物理文件名改成 `risk-register.json` 与 `test-matrix.json`，证明插件只依据冻结的
  `producer_port_key` 识别 `sfmea`/`black_box_cases` 语义，不依据节点 ID、显示名或
  文件名猜测角色；输出随后由显式专业 Validator 验收；
- 运行结果严格分为执行、Artifact 校验、治理、交付四轴。治理失败不伪装成
  Provider 失败；非阻断治理失败为 warning，阻断失败只关闭交付；
- 驾驶舱把“产物校验失败”和“专业治理失败”显示为不同状态，设计器可选择
  Validation Profile，并展示其编译展开计划；
- V3 工作流兼容列表不再把 Governance step 交给 Legacy DSL 审计；V3 创建
  Attempt 时直接冻结已发布 V3 snapshot，不再写入只接受 V1/V2 的 Legacy Store；
- 用户 Artifact 事件只包含声明交付件。`agent_invocation.json`、raw output 等内部
  诊断仍保留在技术区，但不会冒充用户下载产物。

## 3. RED 到 GREEN 根因记录

1. 初版只把上游输出端口解析为“文件存在”，Governance 实际收到的是元数据，
   没有拿到证据内容。新增 Agent evidence → Governance → SFMEA/black-box 端到端
   红测后，引入不可变 Artifact reference 和安全 hydration；坏 JSON 会以
   `governance_input_json_invalid` 阻断治理，但 Provider 执行仍保持 completed。
2. 专业节点最初只有随机物理端口 ID，编译/运行无法稳定判断哪个输入是
   `source_evidence`。Plugin descriptor 现声明语义端口，Authoring Factory 保存
   `binding_key`，Compiler 保留它，Runner 用它建立语义输入。
3. V3 列表兼容端点仍使用 Legacy 工作流审计，因此合法 Governance step 被标成
   不兼容；现在 V3 只使用 V3 validation，V1/V2 保持原审计。
4. V3 Task prepare 曾把 V3 definition 保存到 Legacy WorkflowStore，导致浏览器
   任务在一秒内失败。Preparer 新增已发布 snapshot override，V3 跳过 Legacy
   保存，V1/V2 路径不变。
5. 一条旧测试把不存在的 `test/...` 字符串当成已验证测试映射。没有放宽产品
   校验；测试改为创建真实仓库路径后再断言通过，缺失路径仍返回
   `missing_test_directory_mapping`。
6. 组合回归中两个 Legacy 内置模型用例单独通过、全量运行失败。根因是一个
   Governance 隔离测试直接从父进程 `sys.modules` 删除 Runner，API 保留旧类而
   monkeypatch 命中新模块。隔离检查现运行于子进程，完整组合回归稳定通过。
7. 旧重复黑盒用例测试的输入会先经过 V1 历史修复阶段并扩展为去重后的正式
   用例，因此最终验收不能再断言仍有重复。测试名称和断言改为准确冻结 V1
   “先修复、后验收”的兼容行为；V3 显式治理不依赖该隐式路径。

## 4. 自动化验证

### 4.1 后端契约、兼容与安全回归

```text
661 passed, 1 xfailed in 111.39s
```

覆盖 Workflow graph/version/store、Task prepare/run、Harness facade、网络策略、
声明 Artifact 权威、Validation Profile、编译展开、Plugin registry、三类通用
Validator、专业 Governance runtime、Provider Adapter 和 Agent Workbench API。
唯一 xfail 是 Phase 0 冻结的历史已知契约，不是 Phase 5 新失败。

首轮实现完成后曾运行更宽的相邻集合，结果为 `667 passed, 1 xfailed`；第二轮
审核整改后，针对改动边界重新运行上列 612 条组合回归，并补跑 93 条 Authoring、
Profile、编译、Governance 与安全聚焦测试，结果全部通过。

安全专项包含路径越界、symlink、超过 16 MiB、未声明输出、重复 producer、
Validator 越权、坏 JSON、非 UTF-8、不支持的输入 media type、治理候选输出与
声明 media type 不匹配、只读验证和治理失败四轴隔离。

### 4.2 前端静态与生产构建

```text
npm run lint: exit 0
npx tsc --noEmit: exit 0
npm run build: exit 0，19 个页面生成成功
git diff --check: exit 0
```

### 4.3 真实浏览器主流程

隔离端口 `3233/3234`、隔离 SQLite、`/Volumes/Media/codetalk-runtime-tmp` 和
`/Volumes/Media/codetalk-e2e-artifacts/phase5` 下运行：

```text
19 passed in 1.7m
```

浏览器真实执行了 XYFlow 拖入、移动、框选、鼠标拖线、删线、属性输入、保存、
刷新、Profile 选择、工作流发布、任务向导选择工作区、填写目标、启动运行、查看
四轴状态与交付件。专业图、Profile、语义端口、自定义文件名和边全部由 UI 创建；
API 仅用于建立确定性本地 Provider fixture 和核验不可见 Artifact 边界，不代替
工作流创建、配置、连线、发布或运行。

关键浏览器场景：

1. 普通 report-only 只交付 `report.md`，无 SFMEA/Test Activity 幽灵输出；
2. 专业 Profile 在 UI 展示显式展开节点和声明 Artifact；
3. Agent evidence → Storage Governance → SFMEA/black-box → Validator 完整成功；
4. JSON Schema 失败只改变 Artifact validation/delivery；
5. SFMEA 专业规则失败只改变 governance/delivery，不改 execution；
6. directory/file typed ports、占用冲突和非法拖线继续真实回归。
7. Validator 通过属性面板从 `artifact_exists` 切换为 `json_schema`，保存、刷新、
   编译和发布后仍保持所选类型；缺少 Schema 时发布前中文阻断，用户在输出属性中
   配置结构规则后才可发布；整图 PUT 仍不能篡改 handler 或语义端口。
8. 外部 Agent 产生的残缺 SFMEA/黑盒 Artifact 通过真实 Dispatcher 验证时分别
   返回 `missing_sfmea_fields` 与 `missing_black_box_dimensions`；插件按 handler
   语义懒加载 canonical 冻结模板，不依赖物理文件名，也不允许弱化模板覆盖规则。
9. `schema` Profile 自动展开与显式 `json_schema` 共用同一发布期兼容矩阵；空、
   未知类型和结构错误的 Schema 均无法编译/发布。专业角色输出必须是 JSON-compatible
   media type 和有效 JSON 数组 Schema；浏览器真实展示中文阻断，用户修正后才发布。
10. 显式 Validator 未选择任何声明交付件时，编译和发布均以稳定错误码
    `validator_required_outputs_empty` 阻断；属性面板直接提示“请至少选择一个已声明
    交付件”，用户勾选后可预览计划并发布，不存在隐式绑定。
11. 显式 `storage_test_design` Governance 的 `sfmea`、`black_box_cases` 输出通过
    冻结的 handler 与 `producer_port_key` 进入同一专业输出兼容矩阵。浏览器关闭
    Profile 快捷验收后，仍可复现 Markdown 输出被阻断，并在改为 JSON 数组后使用
    任意自定义文件名成功发布。

桌面与移动端证据：

- `/Volumes/Media/codetalk-e2e-artifacts/phase5-ui-final-green/workflow-canvas-first-desktop.png`
- `/Volumes/Media/codetalk-e2e-artifacts/phase5-ui-final-green/workflow-canvas-first-mobile.png`

E2E 日志显示本机 GitNexus `localhost:7100` 被工作区索引流程调用；这是既有本地
工具进程，不是 Hosted MCP 或公共网络调用。该观察不改变本阶段网络策略。

## 5. 安全、兼容与阶段边界

- 未连接 Redis `6399`；测试使用隔离 SQLite。
- 临时仓库、数据库、Playwright 输出和任务产物均位于 `/Volumes/Media`。
- 未加入第三方 Agent SDK、Hosted MCP、遥测、更新检查或 CDN 依赖。
- Harness、Provider Adapter 和 V3 Orchestrator 不直接导入专业 Governance 或
  历史 Test Activity 实现；通用 Dispatcher 只在显式 handler dispatch 时惰性
  加载 registry。Fresh-process 回归证明导入 Runner 及执行 `artifact_only` V3 均
  不加载 `artifact_contract_v3`、`test_activity_contract`、
  `test_activity_stage_specs`、`source_driven_test_design`。V1/V2 只通过独立惰性
  facade 调用原 canonical 实现，没有复制第二套规则。
- V1/V2 canonical definition 与历史 Artifact 未原地迁移；V3 运行不双写 Legacy
  WorkflowStore。
- 专业生成仍复用已冻结的 legacy 专业规则实现，但入口、加载和输出权威已迁入
  显式 Plugin 边界；后续只能在 Plugin 内替换实现，不能重新泄漏到 Harness。
- `formal_release` 尚未具备 Human Approval，因此不在普通用户可选 Profile 中暴露；
  Phase 6 完成审批节点后再开放。
- 未开始 Phase 6 或 Phase 7，未擅自推送远端。

## 6. 阶段停止点

首轮独立只读审核结论为 `REJECT`，指出语义端口依赖文件名、Runner 直接导入历史
专业模块、Provider 失败状态不准确、专业浏览器流程用 API 构图，以及安全输入、
输出 media type 和 `formal_release` 暴露问题。上述问题均已先增加红测再修复，并
完成完整回归。

第二轮独立只读审核结论仍为 `REJECT`，新发现 Validator 下拉选择无法持久化、
语义端口 `binding_key` 可由整图 PUT 篡改，以及 `source_evidence` Profile 会误验
普通 `report.md`。整改后，Validator 改型只能通过服务端校验注册表的草稿命令；
端口语义字段完全冻结；源码证据 Profile 只消费显式声明
`validation_roles: ["source_evidence"]` 的输出，缺少绑定会在发布前给出中文错误。
真实鼠标拖线辅助增加可验证重试后，15 条浏览器组合回归连续全绿。

第三轮独立只读审核结论仍为 `REJECT`，新发现显式 `json_schema` 可在缺少 Schema
时发布，以及专业 SFMEA/黑盒 Validator 向旧审计器传入空模板导致残缺产物假通过。
整改后，Schema 兼容性在编译/发布期 fail closed；专业插件通过惰性兼容 facade
读取 canonical `ARTIFACT_TEMPLATES`，并增加坏产物与合规自定义文件名的等价性
对照测试。独立审核员整改前宽回归为 `671 passed, 1 xfailed`；整改后专业邻接
矩阵为 `470 passed, 1 xfailed`，最终聚焦回归为 `137 passed`，15 条隔离浏览器
组合回归再次全绿。

第四轮独立只读审核结论仍为 `REJECT`，新发现 `schema` Profile 自动展开绕过
显式 Validator 的 Schema 门禁，以及专业角色可绑定 Markdown/无结构 Artifact。
整改后，显式节点和 Profile 统一调用发布期输出兼容矩阵；专业角色同时约束语义、
media type 与数组 Schema。TDD 初始 19 条失败全部转绿，后端相关回归
`184 passed`，真实浏览器增加一条“阻断 -> 属性面板修复 -> 发布”流程后为
`16 passed`。

第五轮独立只读审核结论仍为 `REJECT`，新发现显式 Validator 的
`required_outputs: []` 可发布为空转节点，以及显式 `storage_test_design` Governance
生成的专业语义端口未进入 Profile 使用的专业输出兼容矩阵。整改采用单一
`_collect_output_contract_requirements()` 收集显式 Validator、Profile 自动节点和
Governance 语义输出需求；Governance 只依据冻结的 handler 与
`producer_port_key`，不读取文件名或标签。TDD 初始结果为 `2 failed, 11 passed`，
修复后原文件 `13 passed`；API 编译/发布、属性面板中文提示和两条真实 UI
“阻断 -> 修正 -> 发布”闭环均已补齐。最终相邻后端回归 `237 passed`，宽后端回归
`641 passed, 1 xfailed`，完整隔离浏览器组合回归 `18 passed`。

第六轮独立只读审核仍为 `REJECT`，识别出必填 Governance 输入可不连线、Handler
自有端口可通过 API/整图 PUT 改写、`source_evidence` 测试误用 `source_report`、
失败策略没有真正控制 DAG、Profile 重复展开 Validator，以及可选 Governance 输出
被错误要求连线。整改后，编译器要求每个必填输入恰好绑定一次；Handler descriptor
冻结语义端口并覆盖命令与整图写入路径；Profile 节点按等价 handler 去重；只有必填
Governance 输出需要连接；`stop` 和 `continue_independent` 由统一 DAG Scheduler
执行，事件顺序固定为 `node_failed -> node_blocked -> run_completed ->
v3_status_updated`。真实鼠标删除必填证据边会阻止发布，重新拖线后才能发布。

最终全量浏览器回归又捕获一个终态归约缺陷：Agent 已完成、Governance 失败、下游
Validator 被阻断时曾形成 `completed/not_started/failed/pending`，交付会永久停在
“准备中”。新增后端红测后，只有“执行已完成且 Validator 因失败 Governance 被
阻断”的情况归约为 Artifact validation failed 和 delivery blocked；Provider 本身
失败仍保持 validation not_started，不伪装成产物质量失败。最终宽后端回归为
`658 passed, 1 xfailed`，隔离端口真实浏览器回归为 `19 passed`。

当前停止在第六轮整改完成、最终独立只读准入审核之前。只有新的审核 Agent 明确给出
`APPROVE` 且 P0/P1 为零，才允许提交建议 commit 并进入 Phase 6；否则继续留在本阶段
整改并由另一名新审核 Agent 复审。

第七轮独立只读审核为 `REJECT`，发现 V3 Runner 在识别 V3 后直接消费未冻结的
`task_run.task_bundle` 内嵌 definition/plan，且快照校验只位于 Legacy 分支；这允许
内嵌副本改写 handler、required outputs 或治理节点，绕过已冻结组件。整改后，所有
持久化 Attempt 在识别契约版本前统一验证 `run_snapshot_v3.json`；V3 contract version、
definition 和 plan 均从已通过 SHA256 校验的独立组件文件读取。内存 bundle 仅保留给
没有持久化快照的合成单元 fixture，不参与生产 Attempt 的契约选择。新增测试覆盖：
篡改 `compiled_plan.json`、篡改 `compiled_definition.json` 均返回 invalid；篡改
`task_run.json` 内嵌 bundle 不改变实际执行节点。整改聚焦回归为
`255 passed, 1 xfailed`，最终宽后端回归为 `661 passed, 1 xfailed`。

当前停止在第七轮整改完成、下一名全新独立只读审核员准入审核之前。

第八轮独立只读审核仍为 `REJECT`，复现了同时删除 snapshot、冻结 definition/plan
和 bundle marker 后，Runner 会降级回可变 bundle。整改后，持久化 V3 Attempt 只要
workflow/bundle 任一契约字段声明 V3，就必须存在并通过快照校验；冻结契约读取已
彻底删除 mutable bundle fallback。合成 V3 单测也改为物化最小冻结组件和 SHA256
快照，不再依赖产品代码中的测试绕过。新增回归同时删除三个文件和 marker，稳定
返回 `invalid/blocked`。随后进一步把真实 Preparer 留下的 `task_bundle.json` 或
`workflow_snapshot.json` 作为“必须存在快照”的持久化痕迹：即使同时清空 task
projection 中所有 V3 字段，也不能降级。V3 Runner、Governance 与 Task Run 聚焦
回归为 `275 passed, 1 xfailed`，包含 V3 Runner 的扩展宽回归为
`689 passed, 1 xfailed`。

当前停止在第八轮整改完成、第九名全新独立只读审核员准入审核之前。

第九轮全新独立只读准入审核结论为 `APPROVE`，`P0=0`、`P1=0`、`P2=0`。
Reviewer 未参与 Phase 5 实现，只基于 handoff、当前 worktree、Phase 5 diff、
源码检查和既有最终门禁证据审查；未修改文件、未提交、未推送。审核确认：

1. Governance 与 Validator 只由显式 Profile 或显式节点激活；
2. 普通 V3 `artifact_only` 冷执行只展开中立 `artifact_exists`，不会加载专业模块；
3. Validator handler 与 handler 自有端口由服务端注册表和编译期校验权威控制；
4. 专业语义路由冻结 `producer_port_key` / `binding_key`，不依赖文件名或 label；
5. 必填绑定、声明输出子集、schema/media 兼容、可选输出、失败策略与四轴状态归约均
   有编译期或运行期门禁；
6. 持久化 V3 执行必须读取并校验 `run_snapshot_v3.json`、冻结 definition 与冻结 plan；
7. 删除或篡改冻结 snapshot 组件会 fail closed；
8. traversal、symlink、oversize、非 UTF-8、不支持 media、未声明 Artifact 等边界仍被拒绝；
9. `formal_release` 在 Phase 6 显式 HITL 之前仍不对普通用户暴露。

Reviewer 证据还确认 `/Volumes/Media/codetalk-e2e-artifacts/phase5-final-review/final-gate.status`
为 `PASS`，且 `final-gate.log` 记录最终后端聚焦回归、前端 lint/type/build 均通过。
因此 Phase 5 满足“全新 reviewer 明确 APPROVE 且 P0/P1=0”的提交准入条件。
