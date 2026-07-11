---
feature_ids:
  - ai-thread-quality-gate
topics:
  - ai-thread
  - test-activity
  - truncation
  - quality-gate
doc_kind: bug-report
created: 2026-07-11
---

# AI 线程把截断测试设计标为完整产物

## 报告人

Codex 在 SPDK 发布候选真实浏览器 E2E 中发现。执行器为 DeepSeek 官方 API，工作区为 `/Volumes/Media/dpdk/spdk`。

## 复现步骤

1. 在 SPDK 工作区新建内置模型 AI 线程。
2. 真实输入 iSCSI Login 灰白盒测试设计任务，要求代码证据、流程、完整 SFMEA、八维黑盒用例及可下载 Markdown。
3. 等待运行结束并下载产物。

期望：完整产物通过质量门禁后才标记完成；截断或缺章时给出可行动错误。

实际：9 秒后显示完成，下载文件只有 41 行，在“流程步骤”第一条中间截断，却显示“下载完整产物”。

## 根因分析

- `AI_CONVERSATION_MAX_OUTPUT_TOKENS` 默认仅为 1024，完整测试活动与普通对话共用这一预算。
- LLM 适配器已记录 `finish_reason=length`，但 `run_generation()` 没有读取该状态。
- 工作流执行器已有测试活动 artifact 审计，AI 线程完成路径未接入质量审计。
- `_prepare_assistant_delivery()` 无条件物化 Markdown 并添加“下载完整产物”，导致状态与真实内容矛盾。

## 修复方案

- 明确完整、详细、可下载或交付文件的测试活动动态提升到 8192 token，上限仍受全局模型配置约束。
- 落库前检查 `finish_reason=length`；截断时写入质量审计并将运行置为失败，不生成下载交付件。
- 对 AI 线程组合 Markdown 复用测试活动契约，校验声明章节、SFMEA 字段、八类黑盒场景、用例观测字段及真实源码/测试路径。
- 用户明确只要少量用例时不强制套用完整八维门禁，保留精确范围语义。

## 验证方式

- Red：新增测试证明旧实现仍传入 1024 token，且截断、浅层内容都会被标为 completed。
- Green：门禁测试 4 条通过；AI 线程与测试活动关联回归 113 条通过。
- 实机：在隔离部署 `3133/3134` 使用同一 DeepSeek iSCSI Login 任务重跑，保存浏览器 trace、截图、事件和下载产物到发布验证证据目录。
