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

## 2026-07-27 回归：假设标签绕过行为核验

### 现象

内置模型 iSCSI Login 深度流程将 Attempt 3 标记为质量 100、可交付；独立只读 Codex 逐源码评审为 38/100，发现多项 P1 协议行为错误，例如把 `Version=0x00` 当成不支持版本、把错误 CHAP 凭据映射为 Authorization Failure，以及把 `T=1,C=1` 的已拒绝路径描述成未校验。

### 根因

- 生成/确定性修复可把整行 `technical_claims` 收缩为真实的 `source_anchor`，却没有保留行字段中的行为断言。
- `risk_status=test_hypothesis` 在 L2 提示词中被当作自动支持条件，只检查锚点未伪造，不验证 failure mode、cause、effect 或 mechanism 是否与源码相反。
- 默认 `black_box_hypothesis` 被从 row-level L2 请求中排除，导致 expected result、状态码和观测项没有事实审计。

### 修复

- 所有绑定源码的 SFMEA 与黑盒行均进入 row-level L2；外部 harness 不要求由产品源码实现，但产品行为预期必须与证据一致。
- `test_hypothesis` 不再自动放行：若其中关于当前实现的陈述被源码反驳，L2 必须判 `contradicts`。
- 纯 `source_anchor` 行不再可交付；每一条用户可见的风险或黑盒用例均需至少一条显式、可独立核验的行为断言。

### 验证

- Red：`test_behavior_validation_includes_black_box_hypothesis_behavior_when_anchor_exists` 在旧逻辑下找不到 row claim；旧 Attempt 3 离线重审仍为 `deliverable/100`。
- Green：同一 Attempt 3 在新门禁下为 `needs_rework`，105 条事实中 54 条验证通过、51 条因缺少行为断言被阻断。
- 回归：`PYTHONPATH=backend /opt/homebrew/bin/python3.11 -m pytest backend/tests/test_test_activity_contract.py backend/tests/test_behavior_claim_validator.py -q`。
