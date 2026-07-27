---
feature_ids:
  - harness-workflow-refactor
  - AC-10
topics:
  - network-policy
  - provider-readiness
  - cli-agent
  - intranet
  - strict-compliance
doc_kind: verification-record
created: 2026-07-28
---

# Harness 与工作流重构 Phase 2 验证记录

## 1. 范围与结论

本阶段以 `3e57f64b` 为基线，只实施重构计划中的 Phase 2：三档网络策略、部署批准的模型地址/代理/CA、CLI 强制出站边界、readiness 与真实运行一致性、永久关闭遥测/更新/Hosted MCP，以及旧配置兼容。

本阶段没有修改工作流 DSL、声明产物契约或专业治理边界，也没有开始 Phase 3 的 Canvas First 改造。

Phase 2 验收结论：

- `developer`、`intranet`、`strict_compliance` 三档模式已具备部署级配置和测试覆盖；
- 默认有效模式为 `intranet`，旧 `intranet_network_mode` 仍可读取；
- 内置模型只允许访问管理员批准的精确模型 API 路径；
- 网络型 CLI Agent 必须使用 `approved_proxy_gateway` 或 `deployment_egress_policy`，否则在启动前失败；
- 离线 Agent 不会因未配置出站边界被误阻断，也不会继承批准代理；
- Provider readiness probe 与真实 CLI 运行共享同一网络决策、清洗环境和沙箱包装；
- 外部 Agent discovery 的启动探测和真实运行也复用同一决策，不能再绕过 Harness 网络边界；
- 设置页只读展示部署策略 ID 和状态，不展示代理地址、认证信息、CA 路径或 `NO_PROXY` 内容；
- 遥测、远程追踪、更新检查、在线包源和 Hosted MCP 在三档模式中均保持关闭；
- 403、CA、代理和策略拒绝均转换为可行动的中文提示。

## 2. 目标映射

| Phase 2 要求 | 实现证据 | 验证证据 |
|---|---|---|
| 三档网络模式与旧配置读取 | `backend/app/config.py`、`backend/app/services/network_policy.py` | `test_network_mode_migration.py` |
| 仅注入管理员批准的 proxy/no_proxy/CA | `resolve_agent_network_context()` | 未批准环境值剥离、批准配置注入及快照脱敏测试 |
| CLI 强制出站边界 | `backend/app/services/agent_sandbox.py` | macOS 两端口真实子进程：批准端口可达，未批准端口被 Seatbelt 拒绝 |
| probe 与 run 同一决策 | `agent_cli_bridge.py`、`agent_run_harness.py`、`harness_facade.py` | probe/stream/Harness 一致性测试及真实 Codex 浏览器探测 |
| discovery 无旁路 | `external_agent_discovery.py` | discovery probe/run 环境清洗契约及批准/未批准双端口真实子进程测试 |
| 内置模型精确端点批准 | `llm/factory.py`、`openai_compat.py`、`anthropic.py` | 未批准地址浏览器负例、真实 DeepSeek 正例 |
| 设置页只读部署策略 | `/api/settings/network-policy`、设置页策略面板 | 保存、刷新、错误展示和离线 Agent 真实浏览器用例 |
| 不泄露凭据 | `AgentNetworkContext.snapshot()`、设置 API 脱敏 | proxy credential、API key、CA 路径负向断言及工作树扫描 |
| 永久禁用非必要联网 | 环境清洗与模型路径限制 | telemetry/update/package index/Hosted MCP 契约测试 |

## 3. 兼容与安全边界

- 新配置未显式提供 `network_mode` 时，继续按旧布尔配置映射：`true -> intranet`，`false -> developer`。
- `network_policy_v2_enabled=false` 保留旧决策路径，便于部署回滚；V2 默认开启。
- 历史 Agent Runtime 没有 `requires_network` 字段时按 `true` 迁移，避免旧网络型 Agent 被错误当作离线执行器。
- 普通用户保存的通用代理和 CA 设置不具备提升部署出站权限的能力。
- 网络快照只保存策略模式、边界类型和配置 ID；不保存代理 URL、主机、认证值、CA 路径或用户密钥。
- 继承环境中的 `SSL_CERT_DIR`、`GIT_SSL_CAINFO`、`NPM_CONFIG_CAFILE`、`NODE_OPTIONS` 等代理/CA 绕过通道会被删除，只允许注入部署批准的代理和 CA。
- `approved_proxy_gateway` 在 macOS 由 Seatbelt 限定为批准代理端点；无法提供等价进程级控制的平台会失败关闭，并要求管理员使用部署级 egress policy。
- macOS Seatbelt profile 写入权限为 `0600` 的运行时临时目录，不进入任务 artifact；用户可下载的只有脱敏策略摘要。
- `strict_compliance` 要求显式 OS 网络隔离；未启用时不启动 Agent。

## 4. 测试结果

### 4.1 后端契约与回归

在隔离临时目录 `/Volumes/Media/codetalk-runtime-tmp/phase2/final5`、Redis 开发端口 `6398` 上运行 Phase 0/1 兼容测试和 Phase 2 网络、LLM、CLI、Harness、设置 API、Agent discovery 测试：

```text
950 passed, 1 skipped, 1 xfailed in 270.09s
```

JUnit：`/Volumes/Media/codetalk-runtime-tmp/phase2/final5/backend-phase2.xml`

唯一 `xfail` 是 Phase 0 已冻结的历史 hard total-timeout 契约缺口；本阶段未修改或掩盖该行为。唯一 skip 是环境条件用例，不是 Phase 2 回归失败。

外部 Agent discovery、Windows CLI 解析、fallback、取消/超时和沙箱兼容套件另行完整运行：

```text
425 passed, 1 skipped in 219.58s
```

### 4.2 前端静态门禁与生产构建

```text
network-policy-ui-contract.test.mjs: 5 passed
npx tsc --noEmit: exit 0
npm run lint: exit 0
npm run build: exit 0，18 个页面生成成功
```

仓库内没有与本阶段匹配的 `.pen` 设计稿，因此无设计稿对照项。设置页已通过真实浏览器交互验证。

### 4.3 浏览器主流程

修复独立审核问题后，在当前 worktree 的隔离服务 `3220/3221` 上，Playwright 真实输入、点击、保存、刷新并执行：

```text
7 passed, 2 skipped in 22.4s
```

两个 skip 是需要显式真实凭据/本地登录态的可选 DeepSeek 与 Codex 正例；同一轮中的未批准模型地址、只读策略、离线 Agent 保存、XYFlow 画布和输入端口用例全部通过。

该轮中未批准模型地址、只读策略刷新恢复、离线 Agent 保存、XYFlow 拖拽/连线/删除/保存/刷新及 typed input port 校验均通过；两个真实 Provider 正例沿用下述已留存的实网证据，未重复暴露凭据。

证据目录：`/Volumes/Media/codetalk-e2e-artifacts/phase2/full-final2/`

### 4.4 真实 Provider 正例

- DeepSeek：通过设置页真实填写并点击测试，后端实际向管理员批准的 `/v1/chat/completions` 发起请求并收到 HTTP 200；`1 passed`。证据：`/Volumes/Media/codetalk-e2e-artifacts/phase2/deepseek-real/`。
- Codex CLI：通过设置页真实选择预设、保存 Runtime 并点击测试，探针实际执行最小模型请求；`1 passed (24.6s)`。证据：`/Volumes/Media/codetalk-e2e-artifacts/phase2/codex-final/`。

真实凭据只通过测试进程环境提供，未写入仓库、验证文档、截图或网络快照。

## 5. 质量门禁

- `git diff --check`：通过。
- Python compileall：通过。
- 根目录媒体/设计工件扫描：无命中。
- 工作树密钥扫描：未发现真实 API key；命中的 `sk-*` 均为既有测试假数据。
- `scripts/check-fallback-layers.mjs`：仓库不存在该脚本，已记录而未伪造结果。
- 本阶段未连接 Redis `6399`。
- 本阶段产物和运行临时文件均位于 `/Volumes/Media`。

## 6. 独立审核修复

首次独立审核结论为 `REJECT`，指出三项边界问题：external discovery 未接入统一策略、继承 CA 环境变量清洗不完整、Seatbelt profile 位于可下载 artifact 目录。三项均先增加失败测试，再完成最小修复并转绿；同时补充真实双端口 discovery 子进程负向用例，以及设置 API 对进程级 Agent 配置的测试隔离。

最终独立复审结论为 `APPROVE`：原始 P0/P1 全部关闭，未发现新的 P0/P1，范围严格停在 Phase 2。Reviewer 独立运行网络边界重点集 `37 passed`，并运行真实 external discovery/macOS sandbox 集 `9 passed`；前端 UI contract、TypeScript、lint、production build 与 `git diff --check` 也由 reviewer 再次确认。非阻断残余风险是 Seatbelt profile 依赖运行时临时目录生命周期清理；文件已位于 artifact 外且权限为 `0600`，后续可补充定期清理策略。

## 7. 修改文件范围

产品代码只涉及：

- 网络配置与策略；
- Agent Runtime 网络属性、probe、沙箱和 Harness 传递；
- 内置模型 transport 与设置 API；
- 设置页只读策略和 Agent 在线/离线选择。

测试代码新增或更新：

- 三档模式和旧配置迁移；
- 网络快照脱敏；
- macOS 真实强制代理边界；
- OpenAI-compatible/Anthropic 错误归一化；
- 设置 API 和设置页；
- 真实 DeepSeek、Codex CLI 与浏览器正反向路径。

## 8. 阶段停止点

Phase 2 完成后停止。Canvas First、稳定内部 ID、统一四类 Provider Adapter、Validator/HITL 和完整端到端迁移分别属于后续阶段，本提交不提前实现。
