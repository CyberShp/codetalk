---
feature_ids:
  - release-candidate-productization
  - test-activity-productization
  - F002
topics:
  - release-candidate
  - agent-runtime
  - test-workflow
  - e2e
doc_kind: implementation-plan
created: 2026-07-11
status: completed
completed: 2026-07-12
---

# CodeTalk 发布候选版产品化计划

## 目标

从当前 `feat` 内网 Beta 基线推进到可发布候选版。完成标准不是“链路能跑”，而是测试人员能够稳定地设计、运行、诊断和复用全部测试活动；不同执行器接收同一输入契约，交付件有证据和质量门禁，失败后有明确恢复动作。

## 不可妥协的验收条件

- A-L 全部真实浏览器用例必须标记为 Pass、Known Issue 或 Blocked，不能留空。
- 无 P0/P1 未关闭缺陷；已知问题必须有影响范围、绕过方式和修复计划。
- iSCSI login 同题对照覆盖 Codex、Claude、OpenCode/NGA 可用者和 DeepSeek 内置模型。
- GPT rubric 平均分不低于 80；不得存在 P0/P1 级幻觉或不存在的证据引用。
- 所有业务主流程使用真实鼠标、hover、点击和键盘输入，不以 API 调用代替。
- 前后端测试、lint、build、密钥扫描、跨执行器回归和独立 review 全部通过。
- 最终修复合并并推送远端 `feat`，清理临时分支/worktree，重启公开服务后再次冒烟。

## 实施阶段

1. 冻结基线：记录分支、版本、服务、A-L 矩阵、执行器能力和已知阻塞。
2. 统一测试活动契约：项目理解、代码分析、流程、测试策略、测试设计、SFMEA、黑盒、覆盖率、风险和执行清单均有结构化模板、证据约束与质量门禁。
3. 统一执行器协议：完整输入只传一次，工作区源码优先，MCP/skills/输入文件/MR/输出目标均进入冻结快照；输出分为用户答案、折叠过程、产物和诊断。
4. 打通线程与工作流：线程可套用工作流，运行结果可继续追问，线程产物可固化；两侧状态、证据、文件和错误表达一致。
5. 部署与兼容性：验证 macOS/Linux/Windows `.cmd` 解析、服务重启、端口冲突、DeepWiki 清除、密钥脱敏和部署脚本。
6. 性能与可靠性：长列表、长对话、30 分钟任务、三个并发 Agent、大 Markdown、100+ 用例和恢复场景。
7. SPDK 真实 E2E：按 A-L 用例库执行，保留 screenshot、trace、console、后端日志、网络摘要和 AI artifacts。
8. 准确度与发布门禁：同题对照、GPT 复判、缺陷闭环、全量质量门禁、独立 review、合并和公开服务冒烟。

## A-L 验收矩阵

| 类别 | 范围 | 当前状态 | 最终证据 |
| --- | --- | --- | --- |
| A | 环境、设置、provider/tool probe、端口和 Redis | Pass | native/isolated startup、probe tests、secret scan |
| B | SPDK workspace、索引、恢复和搜索 | Pass | source slices、GitNexus capacity/backoff、browser evidence |
| C | AI 线程、上下文、并发、恢复和导出 | Pass | same-task staged run、events、manifest/ZIP |
| D | 智能体编排、preset、审计、artifact 和 rerun | Pass | 17 Playwright cases、workflow snapshots |
| E | 代码分析到流程、SFMEA、黑盒四件套 | Pass | accepted independent artifacts |
| F | SFMEA 字段、评分、证据、mitigation 和复判 | Pass | 15 rows、GPT rubric 88/100 |
| G | 黑盒边界、场景覆盖、可执行性、映射和去重 | Pass | 10 cases、8 dimensions、real mappings |
| H | coverage、entry、readiness、格式错误和一致性 | Pass | backend/frontend contract regression |
| I | semantic case、memory evidence 和 source slice | Pass | Workbench/AI source evidence regression |
| J | Markdown/JSON/表格/诊断包导出和脱敏 | Pass | browser individual/ZIP download、manifest、scan |
| K | desktop/mobile、hover、字体、状态和键盘 | Pass | real browser interaction and UI suite |
| L | 长任务、并发、恢复、大结果和性能门槛 | Pass | activity timeout、bounded lists/queue、full regression |

最终矩阵没有空项或 Pending。Windows 实机回归按本轮目标明确排除，不改变 A-L 分类结论；已有 Windows 契约测试仍是门禁的一部分。

## 缺陷闭环

每个缺陷按“真实复现 -> 日志/trace/产物 -> 调用链 -> 红测试 -> 最小修复 -> 绿测试 -> 关联回归 -> 浏览器复验”执行。证据保存到 `/tmp/codetalk-release-validation/20260711-075736/`，最终汇总到 E2E 总报告和修复记录。

## 历史起点

- worktree：`/Volumes/Media/codetalk-release-candidate`
- 分支：`codex/release-candidate-productization`
- 起点：`feat@621b7e5022712cc5c1c077689ee6b408703a267b`
- 部署测试：167 passed，1 skipped。
- 前端 Node 契约：32 passed。
- 后端核心组合初始：13 failed，355 passed。
- 第一轮修复后：368 passed。

## 完成状态

最终实现和证据见 `PLAN_V2.md`、`docs/reports/2026-07-12-release-debt-zero.md` 与 `docs/features/F002-clowder-agent-parity.md`。本计划已完成，不再作为开放 backlog 使用。
