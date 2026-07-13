---
feature_ids:
  - workbench-v2
topics:
  - cors
  - runtime-isolation
  - performance
doc_kind: bug-report
created: 2026-07-14
---

# Cross-runtime API fallback caused repeated CORS failures

## 1. 报告人

Codex 在 Phase 8 真实 Playwright 和后端日志验收中发现。Workbench 请求成功时，`/api/ai/conversations?limit=3` 仍周期性出现九次 400 预检。

## 2. 复现步骤

1. 在 3123/3124 启动隔离 Workbench V2。
2. 保持另一个 CodeTalk 前端运行在 3013，且其首选后端不可用。
3. 打开会渲染 AI Mini Dock 的页面。
4. 观察 3124 收到来自 3013 的三候选、三重试 OPTIONS。

期望：3013 不探测 3124；3123 的只读 AI 请求直接 GET。实际：3013 越界探测，且每个 GET 因强制 JSON Content-Type 先发预检。

## 3. 根因分析

`browserFallbackApiBases()` 无条件返回 3004 和 3124，未把 3124 限制为 3123 的配对测试端口。`request()` 又给没有 body 的 GET 添加 `Content-Type: application/json`，把简单请求升级为 CORS 预检。诊断日志确认拒绝 Origin 为 `http://localhost:3013` 和 `http://127.0.0.1:3013`；当前 3123 Origin 同时返回 200。

## 4. 修复方案

3123 只推导 3124，其他浏览器只使用配置值与公共 3004 fallback。请求层通过 `Headers` 保留调用方 header，仅在存在 body 且调用方未设置时添加 JSON Content-Type。未通过扩大 CORS 白名单掩盖跨运行时访问。

## 5. 验证方式

- 发布静态契约断言 3124 只与 3123 配对，并禁止恢复通用候选数组。
- 完整 ESLint 通过。
- 真实 Chromium 从 3123 打开首页，仅记录一次 `GET http://127.0.0.1:3124/api/ai/conversations?limit=3` 和 200，无 OPTIONS、失败或重试。
