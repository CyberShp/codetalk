---
feature_ids: []
topics:
  - ai-quality-gate
  - iscsi-login
  - independent-review
doc_kind: bug_report
created: 2026-07-11
---

# iSCSI 独立复判与内部质量门禁不一致

## 现象

CodeTalk 将一份 Codex iSCSI Login 交付件判为 100 分可交付，但独立 GPT 复核为 82 分并发现两个 P1：Mutual CHAP oracle 可假通过，Operational Negotiation 最终轮可把中间成功响应误判为 Full Feature。

## 根因

原有静态门禁验证了 Python 语法、BHS DataSegmentLength、未定义名称和基础 Mutual CHAP 输入，却没有验证 oracle 的双向语义，也没有要求最终 Login 响应独立断言 `T=1`、`NSG=3`。专业事实正则还会把 SFMEA failure mode 和明确纠错文字误判为事实冲突。

## 修复

- Mutual CHAP 必须解码后按 bytes 同时验证正确 secret 匹配和错误 secret 不匹配；Authentication Failure 不能算该 oracle 通过。
- Operational 最终轮必须使用独立 Full Feature oracle 断言 `T=1`、`NSG=3`。
- SFMEA `failure_mode`/`cause` 按假设处理，并补齐明确否定表达的 correction pattern。
- 多行 `tshark` 命令可作为可执行协议观测器。

## 验证

- Red：`test_raw_pdu_static_analysis_rejects_mutual_and_full_feature_false_passes` 在旧实现下失败。
- Green：相关质量契约测试通过；旧 Codex 产物离线复跑后只保留两个真实 P1，不再出现纠错文字误报。
- 独立报告：`/tmp/codetalk-release-validation/20260711-075736/executor-comparison/codex-run-5f95-independent-rubric.md`。

