---
feature_ids:
  - e2e-basic-source-design-report
topics:
  - spdk
  - iscsi
  - login
  - sfmea
doc_kind: test_fixture
created: 2026-07-16
---

# SPDK iSCSI Login 设计说明（合成 E2E 夹具）

> 本文档是 CodeTalk 真实端到端测试使用的合成输入，不是 SPDK 官方设计文档。

## 范围

目标是验证 iSCSI initiator 从建立 TCP 连接到进入 Full Feature Phase 的外部行为，覆盖普通登录、CHAP、header/data digest 协商、会话恢复和失败清理。分析必须以工作空间中的 SPDK 源码和现有测试脚本为事实依据；本文档只提供期望与约束。

## 外部行为约束

1. 未启用认证时，合法 initiator 能完成 security negotiation 与 operational negotiation，并进入可提交命令的会话状态。
2. 启用单向 CHAP 时，错误用户名、错误 secret、重复 challenge 和超时响应都必须拒绝登录，不得留下可继续使用的 session。
3. 启用 mutual CHAP 时，target 与 initiator 的身份校验缺一不可；任一方向失败都必须返回可诊断但不泄露 secret 的错误。
4. HeaderDigest 或 DataDigest 协商为 CRC32C 时，协商结果应在后续 PDU 中生效；不支持的值必须被拒绝或按协议规则降级，不能静默接受不一致配置。
5. 登录期间连接断开、超时或协议字段非法时，target 应释放临时连接和认证状态；同一 initiator 随后重连不应继承失败尝试中的脏状态。
6. 已存在 session 的 reinstatement、重复 ISID/TSIH、连接数上限和多连接协商应有确定且可观测的结果。

## 可靠性与并发约束

- 多个 initiator 同时登录时，单个认证失败不得污染其他连接。
- 连续失败登录不应导致连接对象、认证缓冲区或 poller 持续增长。
- target 关闭或重启期间到达的登录请求应快速失败，并留下可关联的日志或 RPC 状态。
- 认证和 digest 处理的错误路径不得造成 reactor 长时间阻塞。

## 测试交付要求

最终报告必须包含：

- 有真实文件、符号、行号或测试目录支撑的关键流程；
- 主流程、认证失败、digest 不一致、断开重连、session reinstatement 和并发隔离；
- 带 S/O/D、RPN、评分依据、检测手段和缓解动作的 SFMEA；
- 仅使用外部输入与可观测结果的黑盒用例，每条包含前置条件、操作步骤、预期结果、观测点、失败诊断和现有 SPDK 测试目录映射；
- 源码或现有测试无法证明的部分必须明确标为证据缺口。

## 未决问题

- mutual CHAP 是否在当前构建配置与现有自动化脚本中完整覆盖？
- digest 协商失败的外部错误码、日志字段和连接清理是否一致？
- session reinstatement 与多连接会话的边界是否有可重复的黑盒夹具？
