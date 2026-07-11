---
feature_ids:
  - agent-output-contract
topics:
  - agent-runtime
  - markdown
  - redaction
  - quality-gate
doc_kind: bug-report
created: 2026-07-11
---

# Agent 代码块传输损坏

## 现象

Codex CLI 原始 session 中的 Python harness 可以通过静态自检，但 CodeTalk 保存的线程交付件丢失代码缩进，且函数参数 `secret=None,` 被脱敏成非法的 `secret=<redacted>`。质量门禁因此拒绝本来可解析的产物。

## 根因

1. `agent_cli_bridge._strip_progress_glyph_prefix()` 的进度前缀正则允许只匹配空白，四个及以上前导空格会被当作终端 spinner 前缀删除。
2. 终端噪声清理不识别 Markdown fenced code，包含位运算符的合法 Python 行会被符号噪声规则整行删除。
3. secret KV 脱敏把 `None,` 连同逗号一起替换，并使用不能作为 Python 表达式的裸 `<redacted>`。
4. raw-PDU 门禁仅在所有 Python 块均语法失败时拦截，存在一个可解析的次要块即可掩盖主 harness 语法错误。
5. 同一个进度前缀正则还包含 `|` 和 `-`，会把 Markdown 表 separator 的前半段当作 spinner 删除；质量门禁审计的是 Agent 原始文件，而用户下载的是清洗后的文件，因此曾出现“审计 100 分但下载表格损坏”。

## 修复

- 纯空白前缀不再按 spinner 清理。
- fenced code 内容绕过终端进度和符号噪声过滤，保留原始缩进与运算符。
- `None/null/nil/true/false` 等非 secret 哨兵保持不变；未加引号的 secret assignment 使用合法字符串字面量脱敏，并保留尾随标点。
- 任一 Python harness 代码块语法无效都会阻断交付。
- raw-PDU 门禁同时检查 BHS DataSegmentLength bytes 5-7、响应长度解析、未定义名称、成功路径无条件异常和 Mutual CHAP oracle。
- 任何首尾均为 `|` 的 Markdown 表行绕过进度前缀清理，表头、separator 和数据行按原文保留。

## 验证

- Red：代码块清洗回归用例稳定复现缩进与位运算行丢失。
- Green：`test_agent_cli_bridge.py`、`test_external_agent_discovery.py` 和 `test_test_activity_contract.py` 关联回归通过。
- 真实 Codex 原始输出经修复链路重放后，主 Python harness 可 `ast.parse`，内部质量审计达到 100 分。
- 新增 Markdown separator 单元红测和“Agent 多文件产物采用后下载”集成回归；真实浏览器重新下载后再按用户最终文件复判。
