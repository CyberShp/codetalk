---
feature_ids: []
topics:
  - secret-redaction
  - export
  - agent-output
doc_kind: bug_report
created: 2026-07-11
---

# 密钥占位符被二次脱敏

## 现象

`token=value` 导出后变成 `token="<redacted>"`，破坏既有导出契约；Python 参数中的 `secret=None` 又可能被改成非法语法。

## 根因

第一轮 assignment 正则先生成 `token=<redacted>`，第二轮 key/value 正则把 `<redacted>` 当成新的 secret 再处理。`None/null` 的保护位于第二轮，无法阻止第一轮提前改写。

## 修复

- 第一轮改用回调，直接保留 `None/null/nil/true/false` sentinel。
- `<redacted>` 作为幂等占位符，不再重复处理。
- 普通未加引号文本保持 `token=<redacted>`；原本带引号的值保留引号。

## 验证

- Python 片段经脱敏后仍通过 `ast.parse`。
- 报告、工作空间报告、聊天导出和 provider snapshot 的既有脱敏断言全部通过。
- 流式超时回退测试改为状态轮询，并连续运行十次通过。

