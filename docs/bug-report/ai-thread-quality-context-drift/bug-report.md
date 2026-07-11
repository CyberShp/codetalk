---
feature_ids:
  - test-activity-productization
  - release-candidate
topics:
  - ai-thread
  - quality-gate
  - retry
  - iscsi
doc_kind: bug-report
created: 2026-07-11
---

# AI 线程重试后质量契约丢失原始任务

## 报告人

Codex 在 SPDK iSCSI Login 四执行器准确度复判中发现。独立 reviewer 将内部门禁判为 100 分的候选交付件复判为 66/100，并识别出 5 个 P1。

## 复现步骤

1. 在线程中提交完整 iSCSI Login 灰盒测试设计任务。
2. 首轮被质量门禁拒绝后，提交只描述修订项的后续消息，或点击“重试上一条”。
3. 检查本轮 `agent_invocation.json` 和最终 `test_activity_quality_audit.json`。

期望：提示词、执行清单和最终审计都继承原始 iSCSI 任务范围与交付件要求。

实际：质量契约只使用当前修订消息。当前消息未重复写出 iSCSI 主体时，领域被误识别为通用 `bdev_io`，iSCSI 专业约束没有执行。

## 根因分析

`run_generation()`、`run_agent_generation()`、`_build_prompt()`、`_build_agent_prompt()` 和 invocation manifest 分别以当前 `user_message["content"]` 构建契约。质量重试又会主动清空普通对话历史，导致 Agent 虽收到被拒草稿，却没有一份机器可识别的原始任务上下文。最终审计重复使用同一个错误输入，因此形成“提示词和审计一致，但一起偏离原任务”的闭环假象。

## 修复方案

- 新增 `_test_activity_request_context()`，从首个测试活动用户消息开始保留后续修订要求，并保持每段用户原文。
- Prompt 增加 `ORIGINAL_TEST_ACTIVITY_REQUEST_CONTEXT`，质量重试清空旧助手回答时仍保留用户原始任务。
- LLM、Agent、invocation manifest 和最终质量审计共享同一上下文，不再各自推断。
- iSCSI 主任务中仅作为安全后端提到的 Null/Malloc bdev 不再扩展为独立 bdev 测试领域。
- invocation 的输出契约以测试活动 `required_outputs` 兜底，避免用户未使用“输出文件：”固定句式时显示空输出目标。

## 验证方式

- 红测：修订消息含 Null/Malloc bdev 时，旧实现得到 `domain_profiles=[iscsi_login,bdev_io]`，且质量重试提示词不含原始任务。
- 绿测：聚焦上下文测试通过，契约只保留 `iscsi_login`，Prompt 同时包含原始任务和当前修订消息。
- 真实浏览器：从失败的 Codex iSCSI 线程点击“重试上一条”，新 invocation 显示 `domain_profiles=[iscsi_login]`，并声明 `business_flow.md`、`sfmea.json`、`black_box_cases.json`、`test_design.md`。
- 关联回归：质量契约 57 passed；AI 线程 94 passed；驾驶舱 API 106 passed。

## 剩余风险

质量门禁只能拦截已产品化的专业事实和结构要求。候选产物仍需独立 GPT 逐条核对源码与测试脚本；内部 80 分不能替代独立复判。
