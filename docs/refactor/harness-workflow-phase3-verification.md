---
feature_ids:
  - harness-workflow-refactor
  - AC-01
  - AC-02
  - AC-03
topics:
  - canvas-first
  - stable-identifiers
  - optimistic-concurrency
  - workflow-compatibility
doc_kind: verification-record
created: 2026-07-28
---

# Harness 与工作流重构 Phase 3 验证记录

## 1. 范围与结论

本阶段以 Phase 2 提交 `32ea0c67` 为唯一基线，只实施重构计划中的 Phase 3：Canvas First、稳定服务端内部 ID、V3 草稿并发控制、V1/V2 兼容入口，以及声明输出的真实试运行联动。

本阶段没有开始 Harness/Provider Adapter 瘦身、Governance Plugin、Checkpoint/HITL/Subagent 或最终预设迁移；这些分别属于 Phase 4 至 Phase 7。

Phase 3 当前实现结果：

- 新建工作流直接选择“空白画布”或“自由源码分析”，创建后立即进入 XYFlow；
- 普通用户界面不要求填写 workflow/node/port/contract ID，服务端统一生成稳定 ID；
- 技术 ID 只在节点属性的“高级诊断”中只读展示，保存和刷新后保持不变；
- 节点可真实拖入、移动、连线、删线、撤销、保存并刷新恢复；
- Agent 输入端口可增加、编辑、删除，端口名称和类型直接显示在节点及连线标签中；
- 标量输入端口只允许一条数据边，占用冲突优先提示“该输入已绑定”，未占用端口立即校验类型；
- Palette 只展示后端 registry 已注册并可执行的节点，Phase 5/6 前不展示 Validator/Human Approval 占位节点；
- capability、provider、registry 三类资源独立加载、独立重试，单项失败不阻断画布；
- V3 保存、验证、编译、发布和试运行携带服务端 `expected_revision`，缺失为 422、过期为 409；
- 发布和试运行在持久化副作用前执行原子 CAS，旧草稿不能覆盖新草稿；
- V1 保持只读，V2 使用明确 Legacy 编辑入口，可复制为 V3 草稿而不修改历史 canonical definition；
- 内置 V1 的“另存为”直接创建有节点和连线的 V3 副本，不再经过已禁止的 V1→V2 隐式升级；
- V3 只声明 `report.md` 的浏览器流程实际运行离线 Agent Runtime，只交付一个报告，不产生幽灵 SFMEA、黑盒用例或 Test Activity 契约。

## 2. 目标映射

| 验收项 | 实现证据 | 验证证据 |
|---|---|---|
| AC-01 新建即画布 | `workflow_authoring_factory.py`、`canvas-entry.tsx`、V3 designer route | Canvas First Playwright 桌面/移动用例 |
| AC-02 隐藏内部 ID | 服务端 ID 工厂、属性面板高级诊断 | 浏览器断言普通 UI 无内部 ID，刷新前后 ID 相同 |
| AC-03 基础节点可建 | registry 驱动 Palette、typed ports、XYFlow mutation API | 拖入 Input/Agent/Output、移动、连线、删线、保存刷新 |
| V3 并发安全 | `expected_revision`、事务内 CAS | 缺失/过期 revision、publish/trial 竞争测试 |
| 历史兼容 | V1 只读、V2 Legacy route、copy-to-v3 | frozen V2 fixture、Legacy 浏览器兼容用例 |
| 声明输出联动 | V3 trial、task wizard、正式 Agent Runtime | `report.md` 单输出真实 E2E 与 artifact/network snapshot |

## 3. RED 到 GREEN 根因记录

1. V3 派生操作最初没有传递当前草稿 revision，连续保存与发布存在覆盖风险。增加缺失/过期 revision 红测后，将 validate、compile、publish、trial 统一为严格 CAS，并由前端按保存结果链式更新 revision。
2. 标量端口同时存在“已占用”和“类型不兼容”时，旧实现先报类型错误。需求要求绑定冲突优先；增加红测后调整连接验证顺序，同时保留独立的未占用错误类型浏览器用例。
3. V3 真运行最初在 38ms 内被网络策略阻断，不是性能快。根因是旧 custom provider 默认要求网络；E2E 改为正式离线 Agent Runtime，保留严格网络策略。
4. 正式离线 Runtime 仍被错误阻断，根因是 frozen readiness snapshot 丢失 `requires_network`。增加 store 红测后补齐冻结字段。
5. readiness 随后被 macOS 沙箱拒绝读取绝对脚本参数。根因是 probe 只批准命令路径，正式 run 却会批准配置参数路径；增加 `test_probe_allows_absolute_runtime_argument_through_same_read_sandbox` 红测后统一 probe/run 读取边界。
6. 首次独立审核发现 V3 可通过旧创建端点伪造/降级、V1 可隐式生成草稿、试运行按目标端口错误过滤输入、Agent 选择写 `provider` 而编译器读 `provider_ref`、过期试运行可能遗留上传文件，以及后端边冲突优先级和失败编译 revision 返回不完整。修复后新增 schema 注入/降级、V1 只读、provider 契约、输入筛选、上传租约、失败 revision 和边冲突专项测试。
7. 移动端真实 Agent 试运行暴露 runner 仍硬编码源端口 `value`，无法消费 V3 服务端生成的稳定端口 ID。新增红测后，runner 只信任编译器已验证且存在于不可变输入快照的 `source_input_id`，缺失依赖仍拒绝执行。
8. 全量浏览器回归发现内置 V1 的旧“另存为”仍调用 V1→V2 接口，且 V1→V3 迁移产生空画布。新增历史 V1 复制红测后，先确定性转换为 V2 图再分配全新的 V3 服务端 ID；原始 V1 快照保持逐字不变。
9. server-owned V3 report-only 回归暴露任务向导把 `output_*` 内部 ID 放进无障碍名称。输出控件现只使用用户可见标签，真实 Agent 验证长文本、MR、文件和唯一 `report.md` 交付。
10. 第二次独立审核复现节点 `step_id/contract_id/handler_id` 注入。服务端身份字段集合现覆盖 handler 和 legacy 技术身份，节点 handler 只由 registry/kind 决定，V3 PUT 逐层保护身份；V3 再走 copy-to-v3 会结构化拒绝，避免副本丢边。
11. 第二次独立审核还复现多文件上传在第二个文件 stale 时遗留首个租约。上传 CAS 冲突现统一返回 `stale_draft`，前端释放本轮所有已成功上传；未知网络结果继续保留，并由 24 小时可配置租约 TTL 在后续上传时有界、保守地清理未引用孤儿。
12. 快速连续添加节点可能在服务端响应前选中同一坐标。前端现同步预留待创建位置，成功后以服务端图为准，失败只释放本次 reservation；真实鼠标连续双击、刷新恢复回归已覆盖。
13. 第三次独立审核发现上传引用检查在 `input_snapshot.json` 读取异常时可能按“未引用”继续删除，并且旧清理上限只限制删除数量、没有限制候选与任务快照扫描工作量。引用检查现为 `referenced / unreferenced / unknown` 三态；任何文件系统、JSON 或扫描完整性不确定性均保留上传。候选使用有界轮转队列，任务快照扫描可跨轮推进，删除前仍以冻结的输入快照为唯一引用真相。
14. 第三次独立审核还发现试运行先推进草稿 revision、创建 trial 数据库和任务目录，之后才校验输入，导致 4xx 失败留下持久化副作用。试运行现按“编译 -> 无副作用输入预检 -> 单次 CAS -> 持久化”执行；缺失或无效输入不会推进 revision、创建 trial 数据或任务目录，预检后的并发修改由唯一 CAS 以 `stale_draft` 关闭。
15. 第四次独立审核以非法 UTF-8 快照复现跨轮误删：`UnicodeDecodeError` 在清理循环标记不确定前逃逸，下一轮可能跳过该快照。新增“首轮异常、次轮仍保留”的红测后，快照读取现将 `UnicodeError` 与 I/O、JSON 解析异常统一映射为 `UNKNOWN`，整个清理周期 fail closed。
16. 第五次独立审核继续以合法但过深的 JSON 复现 `RecursionError` 跨轮误删，证明枚举异常类型仍不完整。破坏性清理边界现对快照读取、解码、解析和引用遍历中的任意普通异常统一标记 `UNKNOWN`；release 返回保留提示，TTL 周期标记 uncertain 并停止删除。深层 JSON 的 release 与连续两轮 TTL 红测均已转绿。

上述修复均未关闭沙箱、未放宽内网策略、未改用 mock Provider，也未降低声明产物边界。

## 4. 测试结果

### 4.1 后端契约与兼容回归

```text
451 passed, 1 xfailed in 30.39s
```

覆盖 Canvas 创建/迁移、工作流图、版本存储、Harness façade、网络策略、任务冻结 Runtime、CLI readiness 沙箱、上传租约三态清理、试运行原子性及 Phase 0/1 相邻契约。唯一 xfail 是 Phase 0 冻结并等待后续阶段转绿的历史已知错误。

### 4.2 前端单元、静态和生产构建

```text
workflow graph + revision/provider/upload/position/error contract: 22 passed
npx tsc --noEmit: exit 0
npm run lint: exit 0
npm run build: exit 0，19 个页面生成成功
git diff --check: exit 0
```

### 4.3 真实浏览器主流程

组合回归在隔离端口 `3233/3234`、隔离 SQLite 和 `/Volumes/Media` 临时目录运行：

```text
Canvas First 5 条 + V2 兼容 4 条 + V3 声明输出 1 条
10 passed in 43.3s
```

浏览器实际执行了鼠标拖入/移动/连线/删线/框选、键盘 Delete、属性输入、文件上传、保存、刷新、试运行和产物查看。没有使用 API 替代上述主流程；API 仅用于隔离 fixture、故障注入和 Artifact 证据核验。

成功截图：

- `/Volumes/Media/codetalk-e2e-artifacts/phase3-title-red/workflow-canvas-first-desktop.png`（1440x900）；
- `/Volumes/Media/codetalk-e2e-artifacts/phase3-title-red/workflow-canvas-first-mobile.png`（390x844）；
- `/Volumes/Media/codetalk-e2e-artifacts/phase3-final/workflow-v3-declared-output-*.png`。

截图用例同时断言标题保持在可视区域且有稳定宽度、页面无横向滚动。Canvas 运行时只加载本地打包的 XYFlow 资源，没有 CDN 请求。

## 5. 真实 Runtime 证据

V3 单输出用例创建正式 Agent Runtime，并通过设置/任务共用的 readiness 路径执行最小探测，再从 stdin 接收完整 Task Bundle。运行结果验证：

- 长分析目标（含前导空格、空行和重复长文本）逐字到达；
- MR 链接逐字到达；
- 上传设计文档内容到达；
- `agent_run.json` 中 `requires_network=false`；
- `network_policy.json` 为 `allowed=true`、`reason=offline_agent_allowed`；
- deliverable 只有 `report.md`；
- 不存在 `sfmea.json`、`black_box_cases.json` 或隐式 Test Activity contract。

该真实 E2E 单项最终用时约 `7s`，组合 10 条浏览器回归用时 `43.3s`。没有通过缩短超时快速失败，也没有用 mock 或 API 调用代替执行器。

## 6. 安全、兼容与阶段边界

- 未连接 Redis `6399`；本阶段使用 SQLite 隔离数据。
- 临时仓库、数据库、上传文件、截图和运行产物都位于 `/Volumes/Media`。
- diff 未包含真实 API key。
- 没有新增第三方 Agent SDK、Hosted MCP、遥测、更新检查或 CDN 依赖。
- V1/V2 canonical definition 未被 V3 迁移覆盖；复制迁移产生新草稿和迁移预览。
- V3 optimistic concurrency 不改变 Legacy V1/V2 调用契约。
- readiness 修复只补齐正式 Runtime 已声明的离线属性和绝对参数读取路径，不授予未声明目录或网络访问。

## 7. 已知非阻断项

- Playwright 启动日志中的 Node `NO_COLOR` 警告不影响功能或构建。
- 某次组合运行中外部 GitNexus 在索引后退出；资源故障隔离用例验证画布仍可继续，后续重试可恢复。Phase 3 不改变 GitNexus 生命周期。
- Node 原生 TypeScript 测试提示 package 未声明 ESM 类型；测试本身 22/22 通过，未为消除警告修改生产 package 模式。
- 上传孤儿 TTL 采用上传触发的惰性对账；长时间完全无新上传时，过期目录会等到下一次上传再清理。对账队列为追加式，长期运行后需要安全压缩；单轮候选和快照扫描已经有界，队列/游标只负责调度，不能作为任务引用真相。
- 本地输入文件可能在无副作用预检与正式复制之间被外部进程修改；此时运行以服务端 5xx 失败并保留诊断，不会伪装成可重试的输入 4xx，也不会绕过唯一 CAS。

## 8. 阶段停止点

Phase 3 经六轮独立只读审核，最终结论为 `APPROVE`，P0/P1/P2/P3 均为零。最终审核独立验证了快照解析失败和“解析成功但引用递归遍历失败”两类跨轮场景，release 与 TTL 均持续保留上传；关键后端回归 `325 passed, 1 xfailed`，改动模块门禁 `408 passed, 1 xfailed`，V3 runner `19 passed`，前端契约 `22 passed`。

审核额外运行全仓后端时发现 3 个 black-box acceptance 失败，并在 Phase 2 基线 `32ea0c67` 逐项同样复现，确认不是 Phase 3 回归。本阶段现允许提交并进入 Phase 4；上述历史失败继续保留为后续治理和最终全量验收项。
