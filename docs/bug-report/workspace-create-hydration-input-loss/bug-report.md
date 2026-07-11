---
feature_ids:
  - release-candidate
topics:
  - workspace
  - hydration
  - input-integrity
  - e2e
doc_kind: bug-report
created: 2026-07-12
---

# 新建工作空间时名称被 hydration 清空

## 现象

真实浏览器在 `DOMContentLoaded` 后立即填写工作空间名称和仓库路径，点击“创建工作空间”时页面提示“请输入工作空间名称”。失败快照显示名称为空，但稍后填写的仓库路径仍在。

## 根因

新建页把两个输入框声明为受控输入，并用空字符串初始化 React 状态。服务端 HTML 已经允许输入，而客户端 hydration 尚未完成时，用户输入的名称只存在于 DOM；hydration 随后用 React 初值覆盖名称。仓库路径由于输入时机稍晚而保留，因此故障表现不稳定。

## 修复

工作空间名称和仓库路径改为非受控输入，提交时统一从 `FormData` 读取。浏览器在 hydration 前接收的文字不再被空状态覆盖，提交契约仍以真实表单内容为准。

## 验证

- Red：最终 smoke E2E 的 XML 导出、报告卡和重索引三条用例均在创建工作空间处复现名称丢失。
- Green：重跑上述三条真实浏览器用例，并回归完整 smoke E2E。
