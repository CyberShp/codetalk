---
feature_ids: [workflow-productization-v3, basic-workflow-b]
topics: [e2e, spdk, design-document, deepseek-flash, artifacts]
doc_kind: test-report
created: 2026-07-26
---

# 基础对照 B：源码与设计文档的 Flash 真实验收

## 执行方式

Playwright 在真实浏览器中完成以下用户操作：

1. 在设置页新增并探测 DeepSeek Flash 的生成与独立审查配置；
2. 创建并索引 SPDK 工作空间；
3. 选择“基础源码 + 设计文档报告（内置模型）”工作流；
4. 输入 iSCSI Login 的分析目标，上传 `v3-iscsi-login-design.md`；
5. 选择速度型并点击“保存并运行”；
6. 等待驾驶舱显示正式终态并检查交付件。

没有 mock、请求拦截或用业务 API 替代 UI 操作。运行前的内网策略只批准
`api.deepseek.com` 作为用户触发的推理主机；第一次未配置此部署批准项时，设置页探测被
`host_not_allowlisted` 正确拒绝，未创建任务。带批准配置的重跑才构成本报告证据。

## 结果

- Run：`task_run_055485884aae4a819a21c9b21ef684d2`
- Artifact root：`/Volumes/Media/codetalk-e2e-artifacts/v3-basic-builtin-flash-metric-20260726b/`
- 终态：`completed / passed / complete`
- 质量：`deliverable`，score `100`，issues `0`
- 点击运行到终态：`149697 ms`（2 分 29 秒）
- 已物化源码证据、流程、SFMEA、黑盒测试、完整报告、设计文档输入快照、质量与交付审计。

## 源码分析观测回归

这次运行使用新修复后的阶段结果，`source_analysis/stage_result.json` 记录：

```json
{
  "attempt_count": 1,
  "provider_call_count": 1,
  "total_duration_ms": 12597.4,
  "provider_wait_ms": 1526.6,
  "prompt_estimated_tokens": 4976,
  "output_tokens": 76,
  "finish_reason": "stop",
  "cache_status": "miss",
  "degraded": false
}
```

这证明性能字段不是仅靠单元测试存在；它已在真实浏览器工作流写入。该受限速度型任务快于
8 分钟窗口，属于可解释的快速完成，不被计为深度档或“伪造的慢任务”。

## 限制

这只完成内置模型基础对照 B。外部 Agent 基础对照、深度档长工作量、部署级流量捕获和大产物
驾驶舱验收仍需要独立证据。

## 深度型 Flash 收敛回归（2026-07-26）

针对同一发布的“基础源码 + 设计文档报告（内置模型）”工作流，Chromium 又完成了一次真实
深度型运行：设置页配置生成与审计模型、创建 SPDK 工作空间、上传设计文档、填写目标、选择
深度型并点击“保存并运行”，全程未以业务 API、mock 或请求拦截替代 UI。

- Run：`task_run_88c41392b2554d54952e3c2bccbe5cb7`
- Artifact root：`/Volumes/Media/codetalk-e2e-artifacts/v3-basic-builtin-flash-deep-post-dedupe-20260726/`
- Provider：生成和审计配置均为 `deepseek-v4-flash`
- 当时终态：`completed / passed / complete`，质量显示 `deliverable / 100 / 0 issues`
- 点击运行到终态：`178574 ms`（约 2 分 59 秒）
- Agent 执行时长：`168528.0 ms`；估算 Prompt `20297` tokens，输出 `5083` tokens
- 交付：源码证据、流程、SFMEA、黑盒测试、完整报告、脑图、质量审计及其支撑材料均已物化。

这证明最终报告反向修复能够在真实 Flash 结果中处理协议事实、缺失仓库路径、错误测试映射、
重复黑盒用例和 MCS 启动配置。该运行依然明显短于 V3 对深度档定义的 40--90 分钟窗口，所以
它不能用于关闭 AC-PERF-003。

> 后续门禁修复说明：该次生成器与审计器是同一个模型标识，虽然配置 ID 不同，
> `behavior_claim_validation.json` 已记录 `status=unavailable`、`independent=false`。修复
> `independent_behavior_validation_unavailable` fail-closed 门禁后，这个历史 `100` 分不再被视为
> 独立质量通过；它保留为真实功能和产物收敛证据，最终交付仍需要不同模型或独立 Agent 复核。

## 两任务并发 Flash 回归（2026-07-26）

两个独立 Chromium 浏览器会话并行执行同一发布工作流。它们首先通过设置页配置 Flash，随后
第一个会话创建 SPDK 工作空间，第二个会话在 UI 明示“代码路径已存在”后点击“打开已有工作
空间”，再分别创建任务、上传设计文档、填写分析目标、选择速度型并点击运行。这样验证的是真实
用户会遇到的“同一工作空间开多个任务”，而不是违反去重规则重复创建同一路径工作空间。

- Artifact root：`/Volumes/Media/codetalk-e2e-artifacts/v3-flash-rapid-concurrent-two-final-20260726/`
- Run：`task_run_a56cf7664baa4ee3ade038d049947636`，`completed / passed / complete`，运行 2 分 14 秒。
- Run：`task_run_8fd25842987a4d0a887c227fb8dbb10b`，`completed / passed / complete`，运行 2 分 52 秒。
- 两条运行都生成一个 Agent 执行记录及完整任务工件；没有因并发覆盖模型配置、输入或交付目录。

回归过程中先发现两个测试基础设施问题：并行 worker 的毫秒级配置名碰撞，以及第二个 worker
把“工作空间路径唯一”误当成并发失败。测试已修正为 worker/repeat 唯一命名，并在重复路径时通过
真实 UI 打开已有工作空间。证据截图与指标文件亦改为运行唯一文件名，后续并发回归不会互相覆盖。

## 同模型 Flash 质量阻断回归（2026-07-26）

Chromium 使用同一个 `deepseek-v4-flash` 作为生成和审计配置，真实完成设置、工作空间、设计文档
上传、任务向导和完整阶段执行。任务实际生成了源码证据、流程、SFMEA、黑盒用例和报告；但独立
核验记录为 `unavailable / independent=false`，因此最终 API 状态为 `quality_blocked`。

- Artifact root：`/Volumes/Media/codetalk-e2e-artifacts/v3-flash-same-model-quality-gate-display-20260726/`
- Run：`task_run_cd6db3b76c9143e38775fdfbfa0db9eb`（保留完整草稿和诊断）。
- 驾驶舱真实 UI 显示：执行状态“已阻断”、质量状态“已阻断”。

该回归同时修复了两类此前会误导测试人员的 UI 问题：文件上传尚未写入快照时不能继续下一步；
以及 `step_failed(status=quality_blocked)` 不能被前端改写成“失败”。

## 同模型 Flash 阻断原因可见性回归（2026-07-26）

在上述回归后，又发现运行接口为了缩减摘要遗漏了 `issues` 与 `recommendations`：驾驶舱虽显示
“已阻断”，但“质量阻断原因”面板为空。该问题已修复为仅公开脱敏、可行动的质量问题摘要，不公开
完整 claim 内容。

真实 Chromium 重跑证据：

- Artifact root：`/Volumes/Media/codetalk-e2e-artifacts/v3-flash-same-model-quality-gate-ui-final-20260726/`
- Run：`task_run_06831d2921b641c7bc538e52ef7c5bdc`
- 用时：`168109 ms`（2 分 48 秒；Playwright 总计 3.3 分钟，含配置、索引与上传操作）。
- 终态：`quality_blocked / blocked`；页面同时展示“已阻断”和“独立源码事实核验”阻断原因。
- 产物：`source_analysis.md`、`source_scope.json`、`evidence_cards.json`、流程、SFMEA、黑盒用例及报告
  均已物化为待修复草稿，未被错误标记为正式交付。

这不是质量通过回归，也不应被统计成通过案例。它证明了同模型审计时系统会如实保留工作结果、明确
说明不可交付的原因，并阻止“结构上看起来完整”的假绿结果。

## 同模型 Flash 真实产物与专业事实阻断回归（2026-07-26）

使用独立端口的 Chromium 又完整执行了一次“基础源码 + 设计文档报告（内置模型）”速度型流程。
用户操作仍为设置页配置 Flash、创建并索引 SPDK 工作空间、上传设计文档、填写 iSCSI Login 分析目标、
选择速度型并点击运行。该运行没有 mock、请求拦截或业务 API 替代界面操作。

- Run：`task_run_a99d941e4c1c4e01999e7af042808713`
- Artifact root：`/Volumes/Media/codetalk-e2e-v3/flash-same-model-20260726-232123/`
- 用时：`168037 ms`（2 分 48 秒；Playwright 总计约 3 分钟，包含设置、索引和上传）。
- 核心产物：`source_analysis.md`、`source_scope.json`、`evidence_cards.json`、`flow_cards.json`、
  `sfmea.json`、`black_box_cases.json` 和 `report.md` 均通过物理存在性、SHA256 与 Agent 产物登记校验。
- 源码事实账本：`24/24` 断言完成 L1 确定性验证；因此这不是“没有读源码”的伪运行。

最终仍显示 `quality_blocked / blocked`，理由有两层，均属于预期的 fail-closed 行为：

1. 生成与行为审计都是 `deepseek-v4-flash`，审计记录为 `independent=false`，不能成为独立质量结论；
2. 即使保留草稿，专业事实门禁也检测到 Flash 把 iSCSI CHAP 失败 Login Response 的 T/CSG/NSG 语义写错，
   并发现黑盒步骤使用不安全的 `/dev/sdX` 裸设备占位符。

这次还验证了部署出站策略：未给出显式 `INTRANET_ALLOWED_HOSTS=["api.deepseek.com"]` 时，设置页测试连接会
被 `host_not_allowlisted` 拒绝；只批准该推理主机后，模型请求能够执行，而 SDK 更新、遥测、追踪和任意
通用外网请求仍不在许可范围内。

## Agent Harness 启动前失败优先级回归（2026-07-26）

另一次真实 Chromium 回归选择“基础源码报告（Codex CLI）”，在无 Agent 出口网关的隔离部署中启动任务。
不发起 Codex 模型调用，预期只验证 Harness 的启动前失败体验。

- 首次结果暴露了摘要优先级错误：活动记录已说明 Agent 出口未批准，但红色主面板错误地优先显示
  “独立质量核验未就绪”，因为两项 readiness 同时失败时后者抢占了失败类型。
- 修复后再次通过真实 UI 创建工作空间、选择工作流、填写目标并点击运行；驾驶舱主面板显示
  “执行器启动前检查未通过”，第一条原因是 Agent 内网出口未批准，并提供“检查执行器设置”入口。
- 回归证据：`/Volumes/Media/codetalk-e2e-v3/codex-preflight-fixed-20260726-232928/`；Playwright `1 passed (11.7s)`。

该回归验证的是受控拒绝和可行动错误展示，不是外部 Agent 的成功交付验收。

## 同模型 Flash 的确定性事实收尾回归（2026-07-26）

同一个模型不能既生成又充当独立事实审计员，因此 `behavior_claim_validation.json` 为
`unavailable` 时，工作流必须保持“已阻断”。此前这一正确的 fail-closed 分支却意外跳过了
无模型、可由已验证源码直接完成的最终修复。修复后，确定性收尾与独立审计解耦：它可以修正
已知事实和黑盒契约，但绝不能把同模型草稿升级为正式交付。

两次真实 Chromium 浏览器回归均完成设置页配置、SPDK 工作空间、设计文档上传、任务向导和
完整运行，且仅使用 `deepseek-v4-flash`：

- `task_run_938bf3b24878463c8973bf24dc65ee7a`，证据根目录：
  `/Volumes/Media/codetalk-e2e-v3/flash-deterministic-finalization-assert-20260726-234140/`；
  用时 `147515 ms`。`deterministic_quality_finalization.json` 已物化为
  `mode=deterministic_only`，并记录了黑盒用例的确定性修复。最终仍为
  `quality_blocked`，没有假绿。
- `task_run_44c8b57a0dc9431bb6484979147ea98f`，证据根目录：
  `/Volumes/Media/codetalk-e2e-v3/flash-sfmea-fact-repair-20260726-234940/`；Playwright
  `1 passed (2.6m)`。它证明最终审计摘录中的 `SFMEA-*` 表格行号能够被回写到 canonical JSON：
  前一轮的 Login Response C-bit 与首个 Login PDU 后 timer 两条专业事实冲突均不再出现。
  Flash 仍生成了新的“未知合法 key / 多连接映射”风险，最终审计保持 `needs_rework`，得分 `85`，
  并因独立审计不可用继续阻断交付。

这条回归的原则是：确定性修复只能减少已知、可验证的错误；它不能替代独立审计，也不能把每次
Flash 新生成的风险静默删除。相关浏览器 E2E 现在显式断言同模型路径必须产生
`deterministic_quality_finalization.json`，防止该安全收尾在未来回归中被移除。

本轮完整后端回归（`test_ai_staged_execution.py`、`test_workbench_task_run.py`、
`test_agent_workbench_api.py`、`test_test_activity_contract.py`）为 `1108 passed`。
其中两条曾失败的 SFMEA normalizer 测试已改为按稳定 SFMEA ID 断言交付语义：当前实现会在发布前
去重相同的 MaxConnections 假设，并可能在 source-risk candidate 前补齐 effect chain，不能再依赖
数组位置或字段列表顺序。本轮新增及相邻的定向回归 `3 passed`，`test_workbench_task_run.py` 全量
`199 passed`。

随后补齐“未知合法 key”与 `multiconnection.sh` 映射范围的 SFMEA 确定性修复，相关完整后端回归
更新为 `1109 passed`。新的真实 Chromium/Flash 验证为
`task_run_b35846b07136400993c7b85e244451a7`，证据根目录：
`/Volumes/Media/codetalk-e2e-v3/flash-sfmea-mapping-repair-20260727-000157/`，Playwright
`1 passed (2.8m)`。这次最终审计中前述两项不再出现；仍阻断了 Flash 新生成的“性能阈值缺少同环境
基线”和一条 Login flags 事实冲突，得分 `70`。因此该回归再次证明系统在草稿质量不足时保持透明的
阻断，而不是靠消除告警伪造通过。
