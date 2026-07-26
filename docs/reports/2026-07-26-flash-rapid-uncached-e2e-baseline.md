---
feature_ids: [v3-productization, performance-baseline, source-analysis-observability]
topics: [e2e, deepseek-flash, spdk, workflow, performance]
doc_kind: test-report
created: 2026-07-26
---

# Flash 速度档未命中缓存基线

## 范围与方法

- 被分析仓库：`/Volumes/Media/dpdk/spdk`
- 执行档位：速度型（Rapid）
- 模型：DeepSeek `deepseek-v4-flash`，生成与独立质量审查均为 Flash
- 方式：Playwright 真实浏览器操作设置、创建工作空间、创建任务、输入分析目标、点击运行并等待终态；未 mock、未拦截请求、未用业务 API 代替界面流程。
- 样本：5 个分别聚焦 Login 状态转换、CHAP、digest、MCS、断连恢复的真实 iSCSI 任务。
- 产物根目录：`/Volumes/Media/codetalk-e2e-artifacts/v3-flash-uncached-rapid-five-post-quality-fix-20260726/`

## 结果

| 样本 | 端到端耗时 | 质量 | 交付 | 模型阶段缓存 | 模型阶段复用 |
| --- | ---: | --- | --- | --- | --- |
| 1 | 3:27 | deliverable / 0 issues | complete | 全部 miss | 否 |
| 2 | 3:22 | deliverable / 0 issues | complete | 全部 miss | 否 |
| 3 | 3:06 | deliverable / 0 issues | complete | 全部 miss | 否 |
| 4 | 3:03 | deliverable / 0 issues | complete | 全部 miss | 否 |
| 5 | 3:52 | deliverable / 0 issues | complete | 全部 miss | 否 |

- P50：3:22；P95（nearest-rank，n=5）：3:52；均值：3:22。
- 5/5 当时均显示为 `completed / passed / complete`，无质量问题项。
- 每轮均物化了 `source_analysis.md`、证据卡、流程、`sfmea.json`、黑盒用例、质量报告和测试设计辅助工件；第五轮可见 42 个顶层可交付/审计工件。

> 质量门禁后续修复：这五轮的生成和审计都配置为同一 `deepseek-v4-flash` 模型。新的
> fail-closed 门禁会把这种 `independent=false` 的核验标记为“待独立复核”，而不是最终质量
> 通过。因此这些样本仍是有效的真实性能与功能样本，但不构成模型独立的最终质量验收。

## 为什么低于 10 分钟

这不是缓存假通过：5 轮的 `source_analysis`、`business_flow`、`sfmea`、`black_box_cases` 都是 cache miss 且 `reused=false`。低于当前“速度型 8–20 分钟”目标的主要原因是当前速度档的工作划分：

1. 源码读取、SHA256 校验、证据包、入口盘点、流程证据与质量校验由本地确定性代码完成；它们不是重新让模型发现源码事实。
2. 业务流程、SFMEA、黑盒用例、模块地图和测试策略在依赖满足后并行调度；没有被单一串行循环拖慢。
3. Flash 对本轮受限的结构化输出响应较快。第五轮中业务流程约 15.7 秒、SFMEA 66.3 秒、黑盒用例 51.7 秒、模块地图 25.8 秒、测试策略 28.4 秒；这些模型阶段均留下真实输出 token、finish reason 和 stage result。
4. `source_analysis` 的模型职责仅为已验证证据的排序/缺口标记；第五轮有一次真实调用（`attempt_count=1`、provider wait 1.45 秒、76 output tokens），并非把完整源码分析跳过。

因此，这轮结果证明速度档能在 Flash 下完成受限 iSCSI 设计任务，但不代表每个范围都会耗尽完整时间窗。不应通过人为等待把数值“补慢”。产品默认档位已按当前 V3 目标调整为：速度型 `8–20` 分钟、深度型 `40–90` 分钟；驾驶舱必须持续展示真实耗时、缓存状态和已完成工作量，不能把预计区间当作质量门禁。

## 观测修复

本轮发现 `source_analysis` stage result 只写入 `duration_ms`，没有并列写入 `total_duration_ms` 或 `provider_call_count`，导致性能汇总显示源码分析总时长为 0。已修复：

- 新任务写入 `total_duration_ms`；
- 新任务写入 `provider_call_count`，包含格式修复调用；
- cache hit 明确写入 0 次 provider 调用；
- 对应单元测试已通过。

历史工件保留原始值，不回填或伪造计量。

## 仍待完成

- 速度档“复杂仓库”的上限需用更大范围样本验证；本报告只覆盖 SPDK iSCSI 定向范围。
- 深度档连续 40–90 分钟工作量、外部 Agent、断点恢复与并发大产物压力验证仍未完成。
- `source_analysis` 新观测字段需在下一次真实 E2E 中复核。
