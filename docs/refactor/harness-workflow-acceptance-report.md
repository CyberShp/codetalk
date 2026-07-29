---
feature_ids:
  - harness-workflow-refactor
  - AC-01
  - AC-02
  - AC-03
  - AC-04
  - AC-05
  - AC-06
  - AC-07
  - AC-08
  - AC-09
  - AC-10
  - AC-11
  - AC-12
  - AC-13
  - AC-14
topics:
  - phase-7
  - acceptance
  - migration
  - browser-e2e
  - network-egress
  - reliability
doc_kind: acceptance-report
created: 2026-07-29
---

# Harness 与工作流 Phase 7 验收报告

## 1. 当前结论

**总体状态：Pass。**

本报告是 Phase 7 的最终证据台账。当前工作树已经形成模板迁移、V1/V2 历史
兼容、Builtin/CLI 适配器/正式治理矩阵、运行失败语义、V3 画布交互/未知契约/三并发和
30 分钟工作流 soak 的隔离浏览器证据。2026-07-29 用户明确批准 AC-10 的最终责任边界，
该决定仅取代三份历史目标/架构/计划文档中的旧 AC-10 证据要求：
CodeTalk 负责无大厂 Agent SDK、自产代码无自主公网联网行为，并关闭 telemetry、自动更新
与 Hosted MCP；Agent 的强制出网隔离由部署内网安全边界负责，不再要求本项目提交管理员
PCAP/网关日志。本机 loopback model fixture 仍不表述为企业部署网关。权威计划对 AC-12
要求的是“当前可用 CLI Agent”，不是特定 Codex
二进制；机器已安装的 OpenCode `1.18.4` 已通过真实 Chrome、required sandbox 和批准的
loopback model route 完成通用 V3 流程。首位全新独立 reviewer 已给出 `REJECT`，
P0=0、P1=2、P2=0；两个 P1 已完成 Red→Green，修复后整库回归已通过，仍须由另一位
全新 reviewer 重新审核。第二位全新 reviewer 复审后仍给出 `REJECT`，P0=0、P1=1、
P2=0；它发现 generic createDraft 仍可从已发布 V2 生成可编辑 Legacy 草稿。该旁路已
完成 Red→Green 和真实 Chrome 回归，仍必须由第三位全新 reviewer 审核，不能复用前两次
结论放行。第三位全新 reviewer 随后给出 `REJECT`，P0=0、P1=2、P2=0：V3 task-agent
端点可绕过 `WorkflowDagScheduler`，且只读回滚未保护既有 V3 Attempt 的 execute、审批恢复
和 rerun。本轮已完成新的 Red→Green、startup/background/expiry 防线、真实 Chrome 和整库
回归；仍须第四位全新 reviewer 明确放行。
第四位全新 reviewer 随后给出 `REJECT`，P0=0、P1=2、P2=1：损坏/部分 V3 在特定
投影缺失组合下仍可能降级为 Legacy，且只读回滚的 cancel 与普通 task GET 仍可落盘；
同时要求把 scheduler-authority 浏览器证据补齐为 trace、原始响应、Attempt 和事件前后
对照。本轮已按 TDD 完成修复，新增冻结 descriptor/bundle 判定、cancel fail closed、GET
内存投影和完整 Chrome trace，最终整库回归通过；仍须第五位全新 reviewer 明确放行。
第五位全新 reviewer 给出 `REJECT`，P0=0、P1=1、P2=0：部分 V3 清空 mutable
projection 后，`acceptance-audit` 仍可降级执行 Legacy audit 并写入 audit/manifest。复核
同时发现同类普通 GET 在 execution contract 字段缺失时仍可回填状态。本轮已用共享 V3
Candidate 统一两个入口，保留 Legacy 审计行为并完成三组 Red→Green、相邻回归和整库
回归；仍须第六位全新 reviewer 明确放行。
第六位全新 reviewer 给出 `REJECT`，P0=0、P1=1、P2=0：若 compiled definition、agent
descriptor 和 mutable projection 都丢失，只剩无法解析的 `run_snapshot_v3.json`，Candidate
仍会降级为 Legacy。双路径 Red 测试证明完整 execute 返回 `202`、单 Agent execute 进入
Facade；修复中又用反向 Red 测试拦住了“文件存在即 V3”对合法 V1/V2 snapshot 的误伤。
最终策略对不可解析 snapshot fail closed，对可解析且 contract 为 `null` 的 Legacy 放行，
整库回归通过；仍须第七位全新 reviewer 明确放行。
第七位全新 reviewer 给出 `REJECT`，P0=0、P1=1、P2=0，主张显式 contract `1/2` 应按
Legacy 执行；该前提与权威架构“缺失/null 才进入 Legacy、V3 为整数 3、其他非空版本
unknown fail closed”及现有 preparer/runner 一致语义冲突，因此未采纳，错误试改已撤回。
但独立侧审发现更一般的真实缺口：删除 run snapshot 后，任一其他冻结 Attempt authority
文件若损坏，Candidate 仍可能降级。七文件矩阵已完成 Red→Green，整库回归通过；仍须
第八位全新 reviewer 明确放行。
第八位全新 reviewer 已明确 `APPROVE`，P0=0、P1=0、P2=0、P3=0，并独立验证 changed
area `276 passed`、前端静态 `13 passed`、TypeScript 和 `git diff --check`。reviewer 准入
门禁现已通过。此后补充的真实 OpenCode 运行关闭了 AC-12；本轮全新 reviewer 首审为
`REQUEST_CHANGES`，P0=0、P1=1，发现执行时恢复的 secret 会进入 replay plan。该问题已
完成 Red→Green，`execution_input.json` 与 `agent_replay_plan.json` 现均不持久化 secret，
同一独立 reviewer 复审后明确 `APPROVE`，P0/P1/P2/P3 均为零。随后最终只读 reviewer
又发现嵌套 secret 会导致整段冻结 OpenCode JSON 被 live 配置替换（P0=0、P1=1）；该问题
已按结构化叶路径恢复完成 Red→Green。复审又发现真实 snapshot producer 对普通格式顶层
secret 不能统一脱敏/恢复（P0=0、P1=1）；producer、持久化和 resolver 已改用精确 sentinel
契约并完成 Red→Green。复审继续发现短 JSON secret 与顶层 `AUTHORIZATION` 可绕过脱敏
（P0=0、P1=1）；按字段名递归脱敏并扩展敏感键集合后再次完成 Red→Green，聚焦、整库和
真实 Chrome/OpenCode 均通过。同一 reviewer 最终复审明确 `APPROVE`，P0/P1/P2/P3 均为
零。AC-10 按上述用户批准的责任边界验收为 `Pass`，当前没有 `Blocked` 发布条件。

本报告中的状态仅使用 `Pass`、`Known Issue` 或 `Blocked`。

## 2. 可复用证据基线

| 证据 | 已确认事实 | 限制 |
|---|---|---|
| `docs/refactor/harness-workflow-phase3-verification.md` | 真实浏览器完成 Canvas First、稳定内部 ID 隐藏、桌面/移动交互、保存/刷新/试运行；10 条组合 E2E 通过。 | 不是 Phase 7 模板与迁移全矩阵。 |
| `docs/refactor/harness-workflow-phase4-verification.md` | 四个薄 Provider Adapter、领域中立 Harness 边界和无大厂 SDK 静态门禁已验证。 | 不构成 Phase 7 的真实通用 Provider 交付。 |
| `docs/refactor/harness-workflow-phase5-verification.md` | `artifact_only`、声明输出、显式治理、四轴状态和真实浏览器专业/普通流已验证。 | 不是本轮 Legacy 分类和迁移验收。 |
| `docs/refactor/harness-workflow-phase6-verification.md` | 真实 Chrome 重启/HITL/checkpoint/reuse/recovery 证据已通过，最终 reviewer `APPROVE`，P0/P1/P2 均为零。 | 未将所有 Phase 7 timeout/compatibility 组合绑定到通用模板。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase6-checkpoint-hitl-real/restart-1785268721/` | 保存了等待、恢复、完成截图、事件、checkpoint 和任务产物。 | 仅 Phase 6 场景。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase7/migration-real-20260728-2/` | 真实 Chrome `3 passed (14.4s)`；新模板 chooser、V1/V2 preview/copy、桌面/移动与无横向溢出通过。 | Playwright 使用隔离 Next 测试服务器；生产 build 是另一项独立门禁。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase7/history-artifacts-20260729T123000+0700/` | 真实 Chrome `1 passed (11.4s)`；V1/V2 workflow、task、RunSnapshot、events、Artifact 打开/下载和迁移前后 SHA 不变。 | 只证明只读兼容和显式复制，不授权原地重写历史。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase7/phase7-provider-matrix-20260729T044023+0700/` | 真实 Chrome `3 passed`；Builtin `report.md`、loopback CLI 多行输入逐字传递、正式 flow/source-evidence/SFMEA/black-box 与显式治理通过。 | loopback adapter 不是实际安装的 Codex 模型 Provider；Builtin 使用本地批准 fixture endpoint。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase7/opencode-real-browser-final8-20260729/` | 机器已安装的 OpenCode `1.18.4` 与真实 Google Chrome，`1 passed (25.5s)`；run `task_run_34e0c184c52f475888943bc4fa86ed4a` 四轴为 completed/passed/passed/ready，只交付 `report.md`。4 个模型请求均到 `127.0.0.1:3218/v1/chat/completions`，冻结非敏感 Provider 配置、执行时秘密解析、持久化脱敏、XDG/HOME 隔离、精确 Artifact 权限和 required Seatbelt 均生效。 | final5 因验收命令把批准代理写为 Seatbelt 不接受的 `127.0.0.1:3218` 而在 probe 阶段 fail closed；纠正为权威 final4 使用的 `localhost:3218` 后 final6 通过，producer 修复后的 final7 通过，短 JSON secret/`AUTHORIZATION` 修复后的 final8 再次通过。部署内网承担 Agent 强制出网隔离。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase7/workflow-soak-20260729-040721/` | 真实 Chrome `1 passed (30.3m)`；同一 Attempt 等待 1,803,575ms，60 次交互，最大 898ms，审批后完成并保留 2 个 checkpoint。 | 使用隔离预索引 fixture；成功日志中无真实 GitNexus/7100 listener。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase7/runtime-failures-final-20260729T0620/` | 真实 Chrome `3 passed`；坏源码路径、坏文件、不可用 Provider、取消、idle timeout 与总 timeout 均有可行动终态和无幽灵交付断言。 | 坏文件由 API preflight 验证；idle/total timeout 由 API 故障注入后再由浏览器验证产品终态，不宣称全部由 UI 发起。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase7/v3-ui-concurrency-final-20260729T1000/` | 真实 Chrome `3 passed (35.5s)`；桌面/移动新增、平移、拖拽、连线、键盘删边、保存/刷新/试运行、未知冻结契约提示及三个并发 V3 task 隔离均通过。 | 最终运行前后确认 `7100` 无 listener；证据 JSON 保存该断言，不代表每个较早历史运行都单独归档了端口扫描文件。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase7/legacy-read-only-gate-ux-final-20260729T0645/` | 两轮 reviewer P1 修复后使用机器已安装的真实 Google Chrome，`3 passed (18.8s)`；桌面、390x844、V1/V2 从库页、版本页、设计器和 V2 Legacy 向导均以正确文案先进入预览，预览前零写入，再携带 token 显式确认复制。 | 首轮因缺少 Playwright bundled headless shell 在启动浏览器前失败；未下载浏览器，改用已安装 Chrome。最终 `.last-run.json` 为 `passed`。 |
| 2026-07-29 reviewer 前本地工程门禁 | 后端整库 `4001 passed, 8 skipped, 0 failed`（1,273.57s）；前端 lint、`tsc --noEmit`、生产 build 均 exit 0；Phase 7 静态脚本当时 `9/9`；`git diff --check` exit 0。 | 后端使用 `GITNEXUS_BIN=/usr/bin/false`、`GITNEXUS_PORT=7101` 与 `GITNEXUS_BASE_URL=http://127.0.0.1:7101`；这是两个 P1 修复前的基线，不替代修复后整库回归。 |
| 2026-07-29 reviewer P1 Red→Green | 多模板 scheduler 测试先 `1 failed, 4 passed`，后 `5 passed`；迁移确认后端/前端测试先红，`requires_confirmation` 契约一致性测试也先红；后端整合 `49 passed`、最终整库 `4007 passed, 8 skipped, 0 failed`（1,256.29s），Phase 7 静态 `10/10`；前端 lint、`tsc --noEmit`、生产 build 均 exit 0。 | 后端使用 GitNexus guard，`7100` 前/中/后无监听；全新 reviewer 仍是准入门禁。 |
| 2026-07-29 第二轮 reviewer P1 Red→Green | V1/V2 generic draft 和旧 `/copy` API 门禁测试先 `3 failed, 1 passed`，后 `4 passed`；聚合迁移后端 `53 passed`；前端入口静态先 `8/10`，后最终 Phase 7 静态 `12/12`；真实 Chrome 最终 `3 passed (18.8s)`。 | 修复后整库 `4011 passed, 8 skipped, 0 failed`；第三位全新 reviewer 仍是准入门禁。 |
| 第二轮 P1 后整库尝试与根因闭环 | 首次整库为 `4010 passed, 1 failed, 8 skipped`；唯一失败是旧 V2 API 生命周期测试仍期待发布后直接创建 Legacy 草稿。保留 V2 创建/编辑/发布兼容性，将该旧断言改为结构化 `409`、无新 draft 和发布投影不变后，focused 合并回归 `54 passed`，最终整库 `4011 passed, 8 skipped, 0 failed`（1,239.85s）。 | 该失败未隐藏，也未通过放宽 V1/V2 只读门禁修复；`7100` 在最终整库前/中/后无监听。 |
| 2026-07-29 第三轮 reviewer P1 Red→Green | reviewer 为 `REJECT`，P0=0、P1=2、P2=0。专项测试先为 `8 failed, 1 passed`，扩展只读写面后为 `6 failed, 9 passed`，最终 `15 passed`；Phase 6/7 recovery/migration 聚焦 `69 passed`，Legacy Agent/HITL/rerun 聚焦 `31 passed`，整库 `4026 passed, 8 skipped, 0 failed`（1,268.08s）。 | V3 单 Agent execute/validate/materialize 现在统一结构化 fail closed；既有 Attempt 的 execute、approval、rerun、startup recovery、后台开关竞争和 expiry monitor 均受共享冻结标记策略保护。Legacy 孤立 Agent 调试仍兼容。第四位全新 reviewer 仍是准入门禁。 |
| 2026-07-29 第四轮 reviewer P1/P2 Red→Green | reviewer 为 `REJECT`，P0=0、P1=2、P2=1。新增/收紧专项测试先为 `3 failed, 14 passed`，最终 `17 passed`；startup/cancel/GET/Phase 7 聚焦 `56 passed, 158 deselected`；最终整库 `4028 passed, 8 skipped, 0 failed`（1,274.21s）。 | 损坏 snapshot、缺失 compiled definition、清空 mutable projections 时仍由冻结 `agent_execution_descriptors.json`/bundle 判定为 V3；只读 cancel 零写入 fail closed，普通 GET 只做内存四轴投影。第五位全新 reviewer 仍是准入门禁。 |
| 2026-07-29 第五轮 reviewer P1 Red→Green | reviewer 为 `REJECT`，P0=0、P1=1、P2=0。rollback acceptance-audit 先 `1 failed, 17 passed`；部分 V3 GET 零写入测试先红；启用写入时防止降级 Legacy audit 的测试也先红。最终 authority `20 passed`，authority 加 Legacy 审计 `23 passed`，Phase 6/7/迁移聚焦 `165 passed`，整库 `4031 passed, 8 skipped, 0 failed`（1,275.29s）。 | acceptance-audit 与普通 GET 现在都使用冻结 Candidate；回滚返回结构化 `409`，启用写入的部分 V3 返回 no-op，GET 只做内存投影；Legacy ready/incomplete/corrupt 审计仍兼容。第六位全新 reviewer 仍是准入门禁。 |
| 2026-07-29 第六轮 reviewer P1 Red→Green | reviewer 为 `REJECT`，P0=0、P1=1、P2=0。snapshot-only 单 Agent/完整 execute 先 `2 failed`；“文件存在即 V3”修复随后使合法 Legacy snapshot + descriptor 测试先红。最终 authority `21 passed`，V2/Legacy/Phase 6/7 相邻矩阵 `171 passed`，整库 `4032 passed, 8 skipped, 0 failed`（1,282.13s）。 | 不可解析 snapshot 现在 fail closed；可解析的非空 contract 或 V3 runtime component 走 V3；可解析且 contract 为 `null` 的 Legacy snapshot 继续兼容，descriptor 不单独决定身份。第七位全新 reviewer 仍是准入门禁。 |
| 2026-07-29 第七轮 reviewer 分歧与侧审 Red→Green | reviewer 为 `REJECT`，P0=0、P1=1、P2=0；其“contract 1/2 是 task-run Legacy”结论与权威文档及 preparer/runner 冲突，未采纳，试验性改动已撤回。独立侧审的七类 corrupt authority 矩阵先 `6 failed, 1 passed`，最终 authority `28 passed`，Phase 6/7/V2/Legacy 相邻矩阵 `179 passed`，整库 `4039 passed, 8 skipped, 0 failed`（1,264.47s）。 | task/workflow/bundle/compiled definition/compiled plan/descriptor/run snapshot 任一存在但不是 JSON object 均 fail closed；有效 Legacy snapshot 仍按缺失/null contract 兼容，任意其他非空 contract 作为 V3/unknown 不得降级。第八位全新 reviewer 仍是准入门禁。 |
| 2026-07-29 第八轮独立准入审核 | `APPROVE`，P0=0、P1=0、P2=0、P3=0；reviewer 独立验证 changed-area `276 passed`、前端静态 `13 passed`、TypeScript 和 `git diff --check`。 | reviewer 门禁通过；该审核发生在真实 OpenCode 补证之前，当时按报告中的旧 AC-12 解释记录为 `Blocked`。 |
| 2026-07-29 OpenCode 增量门禁 | replay plan secret P1 已完成 Red→Green并获首位 reviewer 复审 `APPROVE`。最终 reviewer 先后三次以 `REQUEST_CHANGES` 指出嵌套 secret 导致冻结 JSON 漂移、普通格式顶层 secret 泄露/不可恢复、短 JSON secret 与顶层 `AUTHORIZATION` 绕过脱敏（每轮 P0=0、P1=1）；三项均完成 Red→Green。最终聚焦后端 `361 passed`，整库 `4047 passed, 8 skipped, 0 failed`（1,257.64s），真实 Chrome/OpenCode final8 `1 passed (25.5s)`；前端 lint、`tsc --noEmit`、生产 build 和 `git diff --check` 均通过。 | 固定日期测试缺少 `received_at` 的首次全量失败也已按确定性时间输入修正；生产审批逻辑未改。同一 reviewer 最终复审明确 `APPROVE`，P0=0、P1=0、P2=0、P3=0。 |
| `/Volumes/Media/codetalk-e2e-artifacts/phase7/v3-scheduler-authority-final-20260729T0900/` | 机器已安装的真实 Google Chrome，`1 passed (9.7s)`；真实 V3 workflow/task/attempt 的 task-agent direct execute 返回原始结构化 `409 workflow_v3_scheduler_authority`，产品运行页无 Execute/Validate/Materialize 单节点动作。保存 trace、截图、完整请求/响应、Attempt SHA 和事件前后对照。 | 使用隔离 3233/3234，GitNexus 指向 7101 的 `/usr/bin/false`；运行前后 7100 无 listener。Attempt SHA 与事件集合前后完全一致。 |
| `docs/security/zero-public-egress-verification.md` | 自动策略、负向保护和管理员抓包程序已记录；责任边界专项 `30 passed`。 | 管理员 PCAP/网关日志归部署内网安全验收，不再是 CodeTalk 发布阻塞项。 |

上表只记录已实际运行并保存在 `/Volumes/Media` 的证据。第八位独立 reviewer 准入已通过；
OpenCode 补证关闭 AC-12；AC-10 按用户批准的应用/部署责任边界关闭。

## 3. AC-01 至 AC-14

| AC | 状态 | 现有证据 | 结论与仍需动作 |
|---|---|---|---|
| AC-01 新建即画布 | Pass | Phase 3 Canvas First 真实 Playwright；Phase 7 migration-real 在 1440x900 与 390x844 复验新模板 chooser。 | 新通用模板路径和既有画布主流程均有浏览器证据。 |
| AC-02 用户不填内部 ID | Pass | Phase 3 的 Canvas/表单 E2E；Phase 5 的设计器验证。 | 现有证据显示稳定 ID 由服务端生成并默认隐藏。 |
| AC-03 可建基础与可选验收节点 | Pass | Phase 3 Canvas 交互、Phase 5 显式 Validator/Governance、Phase 6 HITL 和 Phase 7 六模板 catalog/migration E2E。 | 基础、治理和审批节点均有跨阶段浏览器证据。 |
| AC-04 自由分析只生成并验收 `report.md` | Pass | Phase 3/5 report-only 证据；Phase 7 provider matrix 的 Builtin 与 loopback CLI 均只交付 `report.md`。 | manifest 与浏览器交付列表均无 SFMEA、black-box 或幽灵输出。 |
| AC-05 未声明产物不进入验收 | Pass | Phase 5 声明输出、Validator 子集和安全/编译负例回归。 | 已有后端和浏览器证据。 |
| AC-06 普通流不要求专业治理 | Pass | Phase 5 `artifact_only` 冷路径、事件/产物 absence 与浏览器流程。 | 已有证据证明普通流未隐式加载专业治理。 |
| AC-07 Harness 无存储测试领域规则 | Pass | Phase 4 领域中立 Harness/Adapter 静态门禁和 Phase 5 惰性 Governance Plugin 边界。 | 已有 Phase 4/5 门禁证据。 |
| AC-08 无大厂 Agent SDK 生产依赖 | Pass | Phase 4 requirements/lockfile/vendor/import/startup 静态门禁。 | 已有 Phase 4 验证记录。 |
| AC-09 四个薄 Adapter | Pass | Phase 4 对 Builtin、Codex CLI、Claude CLI、OpenCode 的共同契约与 registry 回归。 | 已有统一 Facade/Adapter 证据。 |
| AC-10 默认内网可用 | Pass | 无大厂 Agent SDK 静态门禁；CodeTalk 自产代码只调用显式配置的运行端点，关闭 telemetry、自动更新和 Hosted MCP；网络责任边界专项 `30 passed`。 | 用户批准 Agent 强制出网隔离由部署内网安全边界负责；管理员 PCAP/网关日志不属于 CodeTalk 发布门禁。 |
| AC-11 取消、超时、checkpoint 与恢复 | Pass | Phase 6 真实浏览器后端重启恢复；Phase 7 30.3 分钟 HITL soak 保持同一 Attempt、2 个 checkpoint，并在批准后完成；runtime-failures-final 复验取消、idle timeout 和总 timeout 终态。 | timeout 使用 API 故障注入建立隔离条件，再由浏览器核验产品终态；后端重启权威证据来自 Phase 6。 |
| AC-12 通用与正式存储测试真实 E2E | Pass | 正式存储设计显式治理已有真实 Chrome 证据；通用流程新增机器已安装 OpenCode `1.18.4` 的真实 Chrome 运行，逐字输入经 4 个本地模型请求到达，只交付 `report.md`，四轴全部通过。 | 权威计划要求“当前可用 CLI Agent”，OpenCode 满足该项。 |
| AC-13 历史不破坏 | Pass | history-artifacts 真实 Chrome 打开 V1/V2 workflow、task、RunSnapshot、events 和 Artifact；101 项预览有界，下载字节 SHA 与源文件一致，preview/copy 前后冻结 JSON/hash 不变。 | 显式 copy 只创建新 V3 草稿，未原地修改历史。 |
| AC-14 四轴状态可区分 | Pass | Phase 5 真实浏览器将 execution、artifact validation、governance、delivery 分开显示；Phase 7 v3-ui-concurrency-final 复验未知冻结契约 `999` 的中文兼容提示。 | 四轴失败分离和未知契约 fail-closed 均已有浏览器证据。 |

## 4. Phase 7 真实验收矩阵

| 场景 | 状态 | 需要保存的证据 |
|---|---|---|
| Builtin：源码工作区加目标，通用 `artifact_only`，只交付 `report.md` | Pass | provider matrix 的 trace、冻结请求、manifest 与 `report.md` SHA；使用本地批准 fixture endpoint。 |
| Loopback Codex-compatible CLI adapter：同一通用流程，用户多行输入逐字传递 | Pass | `received-input.json` 保留首尾空格、空行和 URL；只交付 `report.md`，有 trace 与 SHA。 |
| 实际安装的 OpenCode：批准 loopback model route 运行同一流程 | Pass | `/opt/homebrew/bin/opencode 1.18.4`、真实 Chrome、required Seatbelt、隔离 HOME、冻结网络/Provider 配置、trace、截图、请求记录、manifest 和 `report.md` SHA 均已保存。 |
| 正式存储测试：源码加设计文档，显式治理 | Pass | trace、编译计划、`flow.md`、`source-evidence.json`、`sfmea.json`、`black-box-cases.json`、治理终态与 SHA。 |
| 坏路径、坏文件、不可用 Provider | Pass | runtime-failures-final 保存中文可行动错误、HTTP/status、工作区前后不变与无 Artifact 断言；坏文件使用 API preflight，坏路径和 Provider 终态由浏览器核验。 |
| 取消、idle timeout、总 timeout、刷新、后端重启 | Pass | runtime-failures-final 复验取消和两类 timeout；soak 完成 60 次刷新/交互；后端重启恢复沿用 Phase 6 真实浏览器权威证据。故障注入 API 只用于建立 timeout 条件，产品终态由浏览器核验。 |
| V1/V2 workflow/task/RunSnapshot/artifact 打开与下载 | Pass | history-artifacts 证据含迁移前后 JSON/hash、浏览器路径、下载字节 SHA 和 preview/copy。 |
| 1440x900 与 390x844 | Pass | Phase 3 画布交互证据加 migration-real 新 chooser 桌面/移动截图与无横向溢出断言。 |
| V3 桌面/移动画布、未知契约与三并发 task | Pass | v3-ui-concurrency-final 保存三条最终 trace、截图和 JSON；三个 task/run ID、事件复合键和 agent-run Artifact manifest 均隔离。 |
| 新 reviewer 只读准入审核 | Pass | 前七轮 `REJECT` 历史与所有 Red→Green 均保留；第八位全新 reviewer 明确 `APPROVE`，P0/P1/P2/P3 均为零，并独立验证 changed-area、前端静态、TypeScript 和 diff check。 |

所有真实浏览器运行必须使用前端 `3233`、后端 `3234` 和 `/Volumes/Media` 隔离数据根；
生产 Next build 是同轮独立门禁，不能与 Playwright 的隔离测试服务器混称。运行结束后
必须停止后端、前端、GitNexus `7100` 等本轮监听服务。

## 5. 性能与可靠性矩阵

| 项目 | 状态 | 现状与缺口 |
|---|---|---|
| 普通自由源码分析的执行档位与 Provider 活动 | Known Issue | Phase 3 有约 7 秒的离线真实 runtime 证据，但 Phase 7 尚无真实批准 Provider 的源码读取、活动和 Artifact 证据。不得以“小于 8 分钟”自动判错或自动通过。 |
| 长任务至少 30 分钟，UI 不冻结、任务列表不拉长页面 | Pass | workflow-soak `1 passed (30.3m)`；1,803,575ms、60 次交互、最大 898ms，任务列表 shell 高度保持 238px，最终完成。 |
| 三个并发 V3 task 隔离 | Pass | `backend/tests/test_phase7_workflow_reliability.py` 验证后端隔离；v3-ui-concurrency-final 再由真实 Chrome 验证三个 task/run ID、按 `(run_id,event_id)` 的事件隔离和每个 agent-run manifest。 |
| 101+ 结果的大报告预览有界、下载字节完整 | Pass | history-artifacts 真实 Chrome 复验 101 项预览截断/可滚动，下载字节 SHA 精确一致。 |
| Checkpoint/recovery 与 HITL | Pass | Phase 6 真实重启/HITL 证据确认 checkpoint 是恢复权威，且 approve/reject 后可继续；Phase 7 只需回归验证不破坏该能力。 |
| CodeTalk 网络策略与无自主公网行为 | Pass | 无 SDK/遥测/更新依赖静态门禁、显式运行端点策略和环境清理专项共 `30 passed`；Agent 强制出口由部署内网安全边界负责。 |

## 6. 关键证据 SHA-256

| 证据 | SHA-256 |
|---|---|
| migration desktop trace | `f265573d63903d66b7937c0f4605dd4d2aba3a25451f9f6f17fd1d08d037c6f7` |
| migration mobile trace | `6233de252548b474bfb587243ed409fe099579f4cdb5b8bf8eb5736a9dd18bf1` |
| migration V1/V2 copy trace | `e421be9d990d304b8f01a921a1d387654a081db9b9a5c824b3150c7b2e23286b` |
| history trace | `8db33b413da9583872d3f0c0de0885155345e9903911e6177c8d941f25ccbefe` |
| history source/download bytes | `a8501be9aa6ad1cbbea6fbe40197ba3df5ecb7cb7018f301fad52415038cfa84` |
| provider formal/Builtin/loopback traces | `cf42a1c49e57b4772f4ec93ca291e3246df719caa5bbd120b34284b13d79b90a` / `82111c42f24097905d19c03c0d4d4fd67eb133a09f4608e8dde681417f78a988` / `de40cb6bd94af57874eef5af0cde7ee77d8dfe605acc81eabb1761c1df6db974` |
| provider Builtin/loopback reports | `f965c177a421983dce6c3605021da33dad55a5ea7ce6ddc6b5b77ef98e1d6c86` / `462d8471567bc33412862c233646dc03bdeabb55846b6e255d30e6fe406233b2` |
| provider formal flow/source-evidence/SFMEA/black-box | `cd5890cf12561dd48b910385cb5ec49a629463d07cfda4678e4a69698a9128d8` / `8306565a3937b5e8f08cf16935e37db60a263abfba3f8c46e77e02ffe25c1537` / `9765beb6c66c36a55422a5efa11d59f64777d6014dbe8371072c149d0ef74e2c` / `aa7d5d8172da270e2231523801a42e63c2d3524d37c75f995fc496331ff070ba` |
| 30-minute soak trace/metrics | `f66c8c8f47be5b715d3b96164f94669df821c790ebc978532d106c681cc7abad` / `6316e593522305f8342503e7c0249b6873ba619cbeef5de7c4100ed6bfe317d9` |
| post-review V1/V2 all-entry no-write preview / desktop / mobile traces | `522ac9db0b98c7955943eb573e92fa8898739f2292a81898a2e3fa3f79540500` / `73cf0a204cabb97ec68ea57341d8201546723db68cf996f587fac158d4756558` / `950545fa98cdb9f347d7b8900b3a7698be4f5f0e9b46375c1e7d24abcf8d79b7` |
| runtime bad path / bad file+provider / cancel+timeouts traces | `015fe8ff0905e94130b92cf92fd406095d591d2a7f9193abce5f4d96c898cab8` / `61008b83f01c2395502402b02ab395b1607198652a4570f056d96ef9e259d168` / `98076a6203d847b46446d9b930216452c3c3d8a5b2a73b227aa2876ec14c46c0` |
| runtime bad path / bad file+provider / cancel+timeouts JSON | `ccdd7dc5af8b9cdc75917dddf25d6250d2ee6e790bff021b60252d691b117f1d` / `7668348f5b07772ff868ec624042f8b6df472188b050eb41ad9a250c5a170100` / `5a8cff29875b30605cf06bad498d4e2b850c5b8e8d2c218dc1967a152d521b3b` |
| V3 concurrency / unknown contract / interaction traces | `66b01d01cf92ddf8b14137e1e6f7f5cadd3fbb7b791d9799806579362de7d3a7` / `96118715cee6ae59bc152d29e9da042aaeb08658983afaffaff5ca07270e16e5` / `cd456a8f6a9c5e4922c1f1d88f59d3b60b6d8aa01e569cb001f328e99c8a6e10` |
| V3 concurrency / unknown contract / interaction JSON | `a114a67ad22259a91af5b2e73fcc2eda07ced950a444759d9a299662bcb26295` / `7076b7307c3f052b71e7907613a47dcacb737a3fc91c3e5cd24c63ad5f7a3f1d` / `4a6b71c61cf3406d33e425ab1f66404d6975c3d96bf0d40206b36a960500d656` |
| V3 scheduler authority final last-run / trace | `91d1c43004802cd49950d78eb11c8fa7d05da8ffffe219a8b13b2f561bc00903` / `7b728bfef7868581b2b9c8ae86979c81d427918d0f0358663102f0124549b459` |
| V3 scheduler authority final screenshot / request-response JSON | `86ad75e2402ae89b6358524597e97eabce5c8ab138e292ecdaa520a5a0cadeac` / `dc46c50763c241d22d0c19df30e628d0e75526bf86c9d22eb8c347bf8190df41` |
| installed OpenCode trace / result JSON / screenshot | `6642fe69f758f3dc46a2da2c38a706e5cb29c0436ad0e4827fe0b2d5afb92c9d` / `c92368da80337e3b3a188155953afaf1011240e490508767e81f2242b92b56d9` / `6ff6d49be4fa005e9ae5d67eee51516f4536e8f562398048c7cf7d5f46f8247c` |
| installed OpenCode `report.md` / task artifact manifest | `f24da733d7aaf9e5e95eb01e29c988e05e48845b550b05f9bdbf520f675c272e` / `5e5afb91791de28b585983058ecf220975af743b77d964c6d3785d7fefee34b5` |

Provider 日志中的 `started 'gitnexus'` 对应防护配置的 `/usr/bin/false` 进程；它立即退出，
没有真实 GitNexus 服务或 `7100` listener。最终 V3 UI/concurrency 运行前后复验 `7100`
无监听，本轮服务收口检查也确认 `3233/3234/7100` 均无监听；不把它表述成每个较早
历史运行都单独保存了端口扫描文件。

## 7. 发布结论与 reviewer 门禁

当前没有 `Blocked` 发布条件。2026-07-29 用户确认 AC-10 只验收 CodeTalk 无大厂 SDK、
自产代码无自主公网联网行为；Agent 出网隔离由部署内网安全边界承担。对应专项测试
`30 passed`。最终只读 reviewer 已对短 JSON secret/`AUTHORIZATION` 递归脱敏修复给出
`APPROVE`，P0/P1/P2/P3 均为零。

首位 reviewer 的两个 P1 已完成 Red→Green：所有可执行服务端模板现在都通过实际
`WorkflowDagScheduler`，多 Agent 模板按当前串行调度能力运行两个独立 Agent；copy-to-V3
现在要求显式确认、当前迁移契约版本和与只读预览内容绑定的 token，直接 copy 调用点
已收敛到可见预览页。第二位 reviewer 随后发现库页、版本页和设计器仍可经 generic
createDraft 从已发布 V2 生成 Legacy 草稿；该旁路随后完成 Red→Green 和真实 Chrome 回归。

第二轮 P1 已完成 Red→Green：generic createDraft 对 V1/V2 base 和旧 `/copy` 写路径均
结构化 fail closed 且状态前后不变，V3 published base 仍可创建 V3 draft；库页、版本页、
设计器和 Legacy 向导均由真实 Chrome 证明先进入预览。首次修复后整库暴露一个过时 V2
测试断言，根因修正后 focused `54 passed`、最终整库 `4011 passed, 8 skipped, 0 failed`；
第三位全新 reviewer 复审后继续发现两个 P1：V3 task-agent direct endpoint 绕过 scheduler，
以及回滚模式未保护既有 Attempt 的执行恢复入口。本轮用冻结 `run_snapshot_v3`/组件标记
形成共享 V3 Candidate 判定，损坏或部分 V3 也不得降级为 Legacy；V3 task-agent 的
execute/validate/materialize 均只能经完整 scheduler，回滚模式同时保护显式 execute、
approval、rerun、startup recovery、后台开关竞争、approval expiry、output materialize 和
semantic import。rerun GET 在回滚模式仍可读，但不再偷偷落盘。第三轮修复后真实 Chrome
`1 passed` 且整库 `4026 passed, 8 skipped, 0 failed`。

第四位 reviewer 又发现部分损坏 V3 的 Legacy 降级、只读 cancel/GET 写入和浏览器证据
不完整。专项测试先 `3 failed, 14 passed` 后 `17 passed`；最终共享 Candidate 策略读取冻结
descriptor/bundle，cancel 在状态读取前 fail closed，普通 GET 仅返回内存四轴投影。最终
真实 Chrome `1 passed (9.7s)`，保存 trace、原始 `409`、Attempt SHA 和 events 前后对照；
整库 `4028 passed, 8 skipped, 0 failed`。

第五位 reviewer 复现部分 V3 在回滚时调用 Legacy acceptance audit，并写入 audit/manifest。
专项红灯为 `1 failed, 17 passed`；侧审又定位到缺失 execution contract 字段时普通 GET 的
Legacy `mark_outcomes` 回填，并补充“写入启用时部分 V3 也不得执行 Legacy audit”的红灯。
最终 acceptance-audit 在回滚时结构化 `409`、启用写入时 no-op，普通 GET 仅内存投影；
Legacy 审计回归仍通过。authority `20 passed`、相邻聚焦 `165 passed`，整库最终为
`4031 passed, 8 skipped, 0 failed`（1,275.29s）。

第六位 reviewer 继续发现 snapshot-only 损坏 V3 可降级，双路径专项先 `2 failed`；直接把
snapshot 文件存在当作 V3 又会误伤合法 Legacy，因此反向 Legacy snapshot + descriptor
测试也先红。最终以“不可解析 snapshot fail closed、可解析 snapshot 按非空 contract/V3
component 判定、合法 `null` contract 保持 Legacy”收敛。authority `21 passed`、相邻
V2/Legacy/Phase 6/7 `171 passed`，整库 `4032 passed, 8 skipped, 0 failed`（1,282.13s）。

第七位 reviewer 将显式 contract `1/2` 视为 task-run Legacy，但权威文档只允许缺失/null
进入 Legacy，preparer 和 runner 也拒绝其他非空未知版本；该意见被技术性驳回，临时试改
未保留。独立侧审随后发现 `task_run`、workflow snapshot、bundle、compiled definition、
compiled plan、descriptor 或 run snapshot 单独损坏时仍可能降级，矩阵先 `6 failed, 1
passed`；统一不可解析 authority fail-closed 后 authority `28 passed`、相邻 `179 passed`、
整库 `4039 passed, 8 skipped, 0 failed`（1,264.47s）。

第八位 reviewer verdict 为 `APPROVE`，P0/P1/P2/P3 均为零；OpenCode 增量的独立 reviewer
在 replay plan secret 问题完成 Red→Green 后也给出 `APPROVE`，P0/P1/P2/P3 均为零。
最终只读 reviewer 又发现嵌套 secret 会替换整段冻结 JSON（P0=0、P1=1）；结构化叶恢复
完成 Red→Green 后，复审继续发现顶层普通格式 secret 的 snapshot 泄露/不可恢复
（P0=0、P1=1）；统一 sentinel 修复后，第三次复审又发现短 JSON secret 与顶层
`AUTHORIZATION` 可绕过脱敏（P0=0、P1=1）。按字段名递归脱敏修复已完成
producer→persistence→resolver Red→Green、`361 passed` 聚焦、整库和 final8 真实浏览器
验证；同一 reviewer 最终复审为 `APPROVE`，P0/P1/P2/P3 均为零。AC-12 已由权威
计划所称“当前可用 CLI Agent”的真实 OpenCode 运行关闭；AC-10 按用户批准的责任边界
关闭。Phase 7 发布验收完成；本地批准 fixture 仍不表述为企业部署网关的 PCAP/日志证据。
