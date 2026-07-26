---
feature_ids: [workflow-productization-v3, agent-harness-v3, quality-gates-v3]
topics: [regression, backend, verification]
doc_kind: verification-report
created: 2026-07-27
---

# V3 全量后端回归（2026-07-27）

## 命令

```bash
PYTHONPATH=backend python3.11 -m pytest -q backend/tests
```

## 结果

```text
3285 passed, 8 skipped in 1228.63s (0:20:28)
```

本次通过覆盖了 V3 的 WorkflowVersion/RunSnapshot、端口与输入绑定、Harness Facade、
测试活动、Artifact Contract、Claim L0/L1/L2、质量修复、内网策略、驾驶舱 API 和
AI 线程后端回归。

## 边界

- 这是工程后端回归，不是浏览器 E2E，也不是用户可见 AI 工作流性能数据；
- 8 项 skipped 保持为测试定义的环境性跳过，未被计入通过；
- 此结果不替代管理员持有的 Zero Public Egress 流量捕获、真实外部 Agent 验收、
  两个基础工作流浏览器交付记录或独立 reviewer。
