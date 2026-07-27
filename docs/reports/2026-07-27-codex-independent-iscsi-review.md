---
feature_ids: [workflow-productization-v3, quality-gate]
topics: [spdk, iscsi, independent-review, claim-evidence, black-box]
doc_kind: independent-accuracy-review
created: 2026-07-27
---

# SPDK iSCSI Login 独立准确度复核

## 结论

不建议验收。独立只读审查以 SPDK commit 97af299 为事实源，对 CodeTalk
真实浏览器运行 task_run_213f3219eef0444c981d29592bdc3974 的交付件评分为
**35/100**。该运行在产品内显示的“facts=100、executability=100”与源码抽查
矛盾，不能作为 V3 的独立质量通过证据。

完整原始审查输出保存在：
/Volumes/Media/codetalk-e2e-artifacts/v3-codex-independent-review-20260727/review.md。
审查以只读 codex exec 在 /Volumes/Media/dpdk/spdk 中运行，未修改产品或
SPDK 源码。

## 评分

| 维度 | 得分 |
| --- | ---: |
| 证据真实性 | 11/25 |
| 流程完整性 | 7/20 |
| SFMEA | 7/20 |
| 黑盒设计 | 6/20 |
| 幻觉控制 | 2/10 |
| 可用性 | 2/5 |
| **总分** | **35/100** |

## 必须修复的 P0

1. iSCSI Login 的版本、T/CSG/NSG、SendTargets、重定向和 CHAP 方向/阶段顺序存在
   直接违背源码的断言。
2. 多个 SFMEA “未实现”假设忽略了相邻调用链，和连接析构、响应位清零、C-bit 参数重组、
   NotUnderstood、连接容量及 CHAP phase reset 的真实实现矛盾。
3. BB-25 至 BB-31 通过内部 iscsi_* 函数执行，使用返回值和内部日志作为 oracle，
   属于白盒而非黑盒。
4. 交付件把不存在的 test/iscsi_tgt/text、nop、scsi、data、reject 目录展示为可执行
   测试映射。

## P1 与整改方向

- 流程必须从 Login BHS、版本和阶段位校验开始，完整覆盖参数、SessionType/Target、
  TSIH/CID、容量、CHAP、协商、状态迁移、响应、错误和清理；不再只给 socket 调度骨架。
- raw-PDU harness 必须连接真实 SPDK target 并覆盖它声明的场景。伪 TCP 回环只能标作
  harness 自检，不能证明 SPDK 可执行性。
- 每个协议断言需要可验证 claim，绑定精确源码 quote/行号；跨调用链行为须包含完整
  supporting chain，不能用常量或日志行作为泛化行为证据。
- 黑盒用例必须使用公开 initiator、RPC、协议 PDU、配置和外部日志/状态作为操作与 oracle；
  没有真实仓库测试映射时必须明确标为待新增用例。

## 已落地的第一道防线

提交 d6090d03 已令不存在的 test/... 映射成为硬失败，并识别中文
“通过/使用内部 iscsi_*、spdk_* 函数”的白盒步骤。对同一份运行产物重审后，
系统已报告 7 条白盒越界、5 条不存在测试映射、15 条风险追溯缺失，状态为
needs_rework，不再假绿。

这只是整改起点，不能提升本次独立评分；后续重新生成并通过独立复核后，才可更新
AC-QUALITY-006。
