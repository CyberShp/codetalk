---
feature_ids:
  - F002
  - test-activity-productization
topics:
  - ai-thread
  - agent-runtime
  - encoding
  - invocation-contract
doc_kind: bug-report
created: 2026-07-11
---

# AI 线程执行契约漂移与实时中文乱码

## 现象

- 测试活动的完整用户任务在同一 Agent prompt 中重复四次。
- 实际执行器 provider 没进入 `capability_manifest.json`，运行后能力快照显示为空。
- 大段源码之后的中文摘要在实时 SSE 中出现乱码，但持久化消息恢复正常。
- 新的质量修复门禁使若干旧协议探针被当作低质量测试报告重试并失败。

## 根因

1. `TestActivityContract` 同时把完整文本写入 `target`、`user_requirements` 和用户显式 focus，再在 prompt 尾部重复原始任务。
2. `_agent_thread_invocation_manifest()` 构造 runtime 快照时漏写 `provider`。
3. plain stdout 使用 `_decode_strict_if_complete()` 探测编码；一个被截断的 UTF-8 字符序列可能恰好是合法 GB18030，于是分块被提前按错误编码解码。
4. 旧测试夹具输出的两条黑盒用例本身不满足当前质量门禁，失败属于契约变化而非产品回归。

## 修复

- Prompt 中的结构化契约以 `<CURRENT_USER_MESSAGE>` 引用最终用户块，确保每个用户字符只出现一次。
- AgentInvocation runtime 快照保留 provider。
- UTF-8 尾部为 `unexpected end of data` 时继续缓冲，完整后再做多编码判断。
- 把状态、JSON 状态和源码折叠测试声明为显式协议探针；真实测试活动仍走严格质量审计。

## 红绿证据

- 修复前核心组合：13 failed，355 passed。
- 实时流红测试可稳定复现 `源码全文已读取` 变成乱码。
- 聚焦修复：4 passed。
- AI 线程与 runtime：190 passed。
- 关联核心组合：368 passed。

## 风险与后续

本轮修复覆盖 UTF-8 分块和现有 GB18030/GBK 回退，但仍需用 Codex、Claude、OpenCode/NGA 的真实 CLI 输出做同题 E2E，验证 ANSI repaint、NDJSON、超长单行、Windows code page 和 session 恢复。

